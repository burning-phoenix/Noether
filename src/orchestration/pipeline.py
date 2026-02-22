"""
Unified Operation Pipeline.

Single orchestration point for ALL file and bash operations.
Step sequence: VALIDATE -> PREPARE -> APPROVE -> EXECUTE -> UNDO RECORD -> CONTEXT UPDATE

Thread-safe: designed to be called from @work(thread=True) workers.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Any, Callable, Dict, List, Optional

from .editor import ContentMatcher, apply_with_indent_preservation
from .undo import SnapshotUndoStack

logger = logging.getLogger("noether.pipeline")

# Output truncation limit for bash commands (12.6)
MAX_OUTPUT_CHARS = 4000


class OperationType(Enum):
    BASH_READ = "bash_read"
    BASH_WRITE = "bash_write"
    FILE_EDIT = "file_edit"
    FILE_CREATE = "file_create"
    FILE_WRITE = "file_write"
    FILE_DELETE = "file_delete"


class ApprovalPolicy(Enum):
    ALWAYS = "always"
    NEVER = "never"
    BATCH = "batch"


@dataclass
class OperationRequest:
    op_type: OperationType
    source: str  # "chat", "auto", "task_queue", "explore"
    command: Optional[str] = None
    args: Optional[List[str]] = None
    cwd: Optional[str] = None
    target_file: Optional[str] = None
    search_content: Optional[str] = None
    replace_content: Optional[str] = None
    file_content: Optional[str] = None
    reason: str = ""
    approval_policy: ApprovalPolicy = ApprovalPolicy.ALWAYS
    record_undo: bool = True


@dataclass
class OperationResult:
    success: bool
    op_type: OperationType
    message: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    before_content: str = ""
    after_content: str = ""
    match_type: Optional[str] = None
    match_confidence: float = 0.0
    approved: bool = True
    undo_recorded: bool = False


# Git subcommands allowed in the auto loop (read-only)
GIT_READONLY_SUBCOMMANDS = {"status", "log", "diff", "show", "branch", "rev-parse", "stash"}


class OperationPipeline:
    """Single orchestration point for all file and bash operations."""

    def __init__(
        self,
        sandbox_executor,
        filesystem_sandbox,
        undo_stack: SnapshotUndoStack,
        push_screen_fn: Optional[Callable] = None,
        notify_fn: Optional[Callable] = None,
        on_context_update: Optional[Callable] = None,
        audit_log_path: Optional[Path] = None,
    ):
        self.sandbox_executor = sandbox_executor
        self.filesystem_sandbox = filesystem_sandbox
        self.undo_stack = undo_stack
        self._push_screen = push_screen_fn
        self._notify = notify_fn
        self._on_context_update = on_context_update
        self._audit_log_path = audit_log_path or (Path.cwd() / ".noether" / "audit.jsonl")

    def execute(self, request: OperationRequest) -> OperationResult:
        """Single sync entry point. Safe from worker threads."""
        start_time = time.time()

        # VALIDATE
        error = self._validate(request)
        if error:
            self._audit_log(request, error, start_time)
            return error

        # PREPARE
        prepared = self._prepare(request)
        if not prepared.success:
            self._audit_log(request, prepared, start_time)
            return prepared

        # APPROVE
        if not self._approve(request, prepared):
            result = OperationResult(
                success=False,
                op_type=request.op_type,
                message="Operation rejected by user",
                approved=False,
            )
            self._audit_log(request, result, start_time)
            return result

        # EXECUTE
        result = self._execute(request, prepared)

        # UNDO RECORD
        if result.success and request.record_undo:
            self._record_undo(request, prepared)
            result.undo_recorded = True

        # CONTEXT UPDATE
        if result.success and self._on_context_update:
            try:
                self._on_context_update(request)
            except Exception as e:
                logger.warning("Context update failed: %s", e)

        self._audit_log(request, result, start_time)
        return result

    def _validate(self, request: OperationRequest) -> Optional[OperationResult]:
        """Validate the request. Returns error result or None if valid."""
        from ..sandbox.schemas import ReadOnlyShellCommand, FullShellCommand

        if request.op_type in (OperationType.BASH_READ, OperationType.BASH_WRITE):
            if not request.command:
                return OperationResult(
                    success=False, op_type=request.op_type,
                    message="No command specified",
                )

            # Validate through Pydantic schema
            schema_cls = ReadOnlyShellCommand if request.op_type == OperationType.BASH_READ else FullShellCommand
            try:
                schema_cls(
                    command=request.command,
                    args=request.args or [],
                    cwd=request.cwd,
                )
            except Exception as e:
                return OperationResult(
                    success=False, op_type=request.op_type,
                    message=f"Command validation failed: {e}",
                )

            # Check SRT availability for bash
            if not self.sandbox_executor.is_available:
                return OperationResult(
                    success=False, op_type=request.op_type,
                    message="SRT not installed. Run `noether setup-sandbox` to install it.",
                )

        elif request.op_type == OperationType.FILE_EDIT:
            if not request.target_file:
                return OperationResult(
                    success=False, op_type=request.op_type,
                    message="No target file specified for edit",
                )
            if not request.search_content:
                return OperationResult(
                    success=False, op_type=request.op_type,
                    message="No search content specified for edit",
                )
            # Validate path through sandbox
            if self.filesystem_sandbox:
                resolved = self.filesystem_sandbox.resolve_path(request.target_file)
                if not resolved:
                    return OperationResult(
                        success=False, op_type=request.op_type,
                        message=f"Path not allowed: {request.target_file}",
                    )
                if not resolved.exists():
                    return OperationResult(
                        success=False, op_type=request.op_type,
                        message=f"File not found: {request.target_file}",
                    )

        elif request.op_type in (OperationType.FILE_CREATE, OperationType.FILE_WRITE):
            if not request.target_file:
                return OperationResult(
                    success=False, op_type=request.op_type,
                    message="No target file specified",
                )
            if self.filesystem_sandbox:
                resolved = self.filesystem_sandbox.resolve_path(request.target_file)
                if not resolved:
                    return OperationResult(
                        success=False, op_type=request.op_type,
                        message=f"Path not allowed: {request.target_file}",
                    )
                if not self.filesystem_sandbox.is_path_allowed(resolved, for_write=True):
                    return OperationResult(
                        success=False, op_type=request.op_type,
                        message=f"Write not allowed: {request.target_file}",
                    )

        elif request.op_type == OperationType.FILE_DELETE:
            if not request.target_file:
                return OperationResult(
                    success=False, op_type=request.op_type,
                    message="No target file specified for delete",
                )

        return None

    def _prepare(self, request: OperationRequest) -> OperationResult:
        """Prepare the operation. For edits, run ContentMatcher."""
        if request.op_type == OperationType.FILE_EDIT:
            return self._prepare_edit(request)

        elif request.op_type in (OperationType.FILE_CREATE, OperationType.FILE_WRITE):
            # Read previous content for undo snapshot
            before_content = ""
            if self.filesystem_sandbox and request.target_file:
                existing = self.filesystem_sandbox.safe_read(request.target_file)
                if existing is not None:
                    before_content = existing

            return OperationResult(
                success=True, op_type=request.op_type,
                message="Ready",
                before_content=before_content,
            )

        # Bash and delete — no prep needed
        return OperationResult(
            success=True, op_type=request.op_type,
            message="Ready",
        )

    def _prepare_edit(self, request: OperationRequest) -> OperationResult:
        """Run 4-layer ContentMatcher for file edits."""
        # Read the file
        content = None
        if self.filesystem_sandbox:
            content = self.filesystem_sandbox.safe_read(request.target_file)
        else:
            try:
                content = Path(request.target_file).read_text(encoding="utf-8")
            except Exception:
                pass

        if content is None:
            return OperationResult(
                success=False, op_type=request.op_type,
                message=f"Cannot read file: {request.target_file}",
            )

        # Run ContentMatcher
        matcher = ContentMatcher(content)
        match_result = matcher.find_match(request.search_content)

        if not match_result.found:
            # Build error feedback
            from .editor import EditError, get_context_lines
            error = EditError(
                error_type="no_match",
                target=request.target_file,
                search_content=request.search_content,
            )
            if match_result.closest_match:
                error.closest_match = match_result.closest_match
                error.closest_similarity = match_result.closest_similarity
                error.line_range = (match_result.closest_line_start, match_result.closest_line_end)
                if match_result.closest_line_start > 0:
                    error.closest_context = get_context_lines(
                        content, match_result.closest_line_start,
                        match_result.closest_line_end, context=3,
                    )
                error.suggestion = "Copy the EXACT content from the closest match above into your SEARCH block."
            else:
                error.suggestion = "The search content was not found. Check the file and try again with exact content."

            return OperationResult(
                success=False, op_type=request.op_type,
                message=error.format_feedback(),
                match_type=None,
            )

        # Compute new content
        new_content = apply_with_indent_preservation(
            content, match_result, request.replace_content,
        )

        return OperationResult(
            success=True, op_type=request.op_type,
            message=f"Edit ready ({match_result.match_type} match, {match_result.confidence:.0%} confidence)",
            before_content=content,
            after_content=new_content,
            match_type=match_result.match_type,
            match_confidence=match_result.confidence,
        )

    def _approve(self, request: OperationRequest, prepared: OperationResult) -> bool:
        """Request user approval if policy requires it."""
        if request.approval_policy == ApprovalPolicy.NEVER:
            return True
        if request.approval_policy == ApprovalPolicy.BATCH:
            return True  # Caller handles batch approval externally

        if not self._push_screen:
            return True  # No UI — auto-approve

        # Build modal for the operation
        from ..ui.modals.approval_modal import ApprovalModal

        if request.op_type in (OperationType.BASH_READ, OperationType.BASH_WRITE):
            full_cmd = request.command or ""
            if request.args:
                full_cmd += " " + " ".join(request.args)
            modal = ApprovalModal(
                operation="Execute Command",
                target=full_cmd,
                description=request.reason or f"Run: {full_cmd}",
                risk_level="review" if request.op_type == OperationType.BASH_WRITE else "low",
            )
        elif request.op_type == OperationType.FILE_EDIT:
            modal = ApprovalModal(
                operation="File Edit",
                target=f"{request.target_file} ({prepared.match_type} match, {prepared.match_confidence:.0%})",
                description=(
                    f"{request.reason}\n\n"
                    f"Search:\n{(request.search_content or '')[:200]}...\n\n"
                    f"Replace:\n{(request.replace_content or '')[:200]}..."
                ),
                risk_level="review",
            )
        elif request.op_type in (OperationType.FILE_CREATE, OperationType.FILE_WRITE):
            modal = ApprovalModal(
                operation="File Write",
                target=request.target_file or "",
                description=request.reason or f"Write to {request.target_file}",
                risk_level="review",
            )
        elif request.op_type == OperationType.FILE_DELETE:
            modal = ApprovalModal(
                operation="File Delete",
                target=request.target_file or "",
                description=request.reason or f"Delete {request.target_file}",
                risk_level="high",
            )
        else:
            return True

        # Show modal and wait
        approval_result: list[bool] = []
        event = Event()

        def _on_dismissed(approved: bool) -> None:
            approval_result.append(approved)
            event.set()

        self._push_screen(modal, _on_dismissed)
        event.wait(timeout=60)

        return bool(approval_result and approval_result[0])

    def _execute(self, request: OperationRequest, prepared: OperationResult) -> OperationResult:
        """Execute the operation."""
        if request.op_type in (OperationType.BASH_READ, OperationType.BASH_WRITE):
            return self._execute_bash(request)
        elif request.op_type == OperationType.FILE_EDIT:
            return self._execute_file_edit(request, prepared)
        elif request.op_type in (OperationType.FILE_CREATE, OperationType.FILE_WRITE):
            return self._execute_file_write(request, prepared)
        elif request.op_type == OperationType.FILE_DELETE:
            return self._execute_file_delete(request)

        return OperationResult(
            success=False, op_type=request.op_type,
            message=f"Unknown operation type: {request.op_type}",
        )

    def _execute_bash(self, request: OperationRequest) -> OperationResult:
        """Execute a bash command through SRT (sync, no event loop needed)."""
        full_cmd = request.command or ""
        if request.args:
            full_cmd += " " + " ".join(request.args)

        result = self.sandbox_executor.execute_sync(full_cmd, cwd=request.cwd)

        # Truncate output (executor already truncates, but guard against
        # future callers that bypass the executor)
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        if len(stdout) > MAX_OUTPUT_CHARS:
            stdout = stdout[:MAX_OUTPUT_CHARS] + "\n...[truncated]"
        if len(stderr) > MAX_OUTPUT_CHARS:
            stderr = stderr[:MAX_OUTPUT_CHARS] + "\n...[truncated]"

        return OperationResult(
            success=result.get("success", False),
            op_type=request.op_type,
            message=f"Command: {full_cmd}",
            stdout=stdout,
            stderr=stderr,
            returncode=result.get("returncode", -1),
        )

    def _execute_file_edit(self, request: OperationRequest, prepared: OperationResult) -> OperationResult:
        """Write the prepared edit to disk."""
        if not prepared.after_content:
            return OperationResult(
                success=False, op_type=request.op_type,
                message="No content to write (prepare step may have failed)",
            )

        if self.filesystem_sandbox:
            success = self.filesystem_sandbox.safe_write_sync(
                request.target_file, prepared.after_content,
            )
        else:
            try:
                Path(request.target_file).write_text(prepared.after_content, encoding="utf-8")
                success = True
            except Exception as e:
                logger.error("File write error: %s", e)
                success = False

        if success:
            return OperationResult(
                success=True, op_type=request.op_type,
                message=prepared.message,
                before_content=prepared.before_content,
                after_content=prepared.after_content,
                match_type=prepared.match_type,
                match_confidence=prepared.match_confidence,
            )
        return OperationResult(
            success=False, op_type=request.op_type,
            message=f"Failed to write {request.target_file}",
        )

    def _execute_file_write(self, request: OperationRequest, prepared: OperationResult) -> OperationResult:
        """Write/create a file."""
        content = request.file_content or ""

        if self.filesystem_sandbox:
            success = self.filesystem_sandbox.safe_write_sync(
                request.target_file, content,
            )
        else:
            try:
                p = Path(request.target_file)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                success = True
            except Exception as e:
                logger.error("File write error: %s", e)
                success = False

        if success:
            return OperationResult(
                success=True, op_type=request.op_type,
                message=f"Written: {request.target_file}",
                before_content=prepared.before_content,
                after_content=content,
            )
        return OperationResult(
            success=False, op_type=request.op_type,
            message=f"Failed to write {request.target_file}",
        )

    def _execute_file_delete(self, request: OperationRequest) -> OperationResult:
        """Delete a file."""
        if self.filesystem_sandbox:
            resolved = self.filesystem_sandbox.resolve_path(request.target_file)
            if resolved and resolved.exists():
                try:
                    resolved.unlink()
                    return OperationResult(
                        success=True, op_type=request.op_type,
                        message=f"Deleted: {request.target_file}",
                    )
                except Exception as e:
                    return OperationResult(
                        success=False, op_type=request.op_type,
                        message=f"Delete failed: {e}",
                    )
            return OperationResult(
                success=False, op_type=request.op_type,
                message=f"File not found: {request.target_file}",
            )

        try:
            Path(request.target_file).unlink()
            return OperationResult(
                success=True, op_type=request.op_type,
                message=f"Deleted: {request.target_file}",
            )
        except Exception as e:
            return OperationResult(
                success=False, op_type=request.op_type,
                message=f"Delete failed: {e}",
            )

    def _record_undo(self, request: OperationRequest, prepared: OperationResult) -> None:
        """Record a snapshot for undo."""
        if request.op_type == OperationType.FILE_EDIT:
            self.undo_stack.push_snapshot(
                path=request.target_file,
                content_before=prepared.before_content,
                description=request.reason or f"Edit {request.target_file}",
            )
        elif request.op_type in (OperationType.FILE_CREATE, OperationType.FILE_WRITE):
            self.undo_stack.push_snapshot(
                path=request.target_file,
                content_before=prepared.before_content if prepared.before_content else None,
                description=request.reason or f"Write {request.target_file}",
            )
        elif request.op_type == OperationType.FILE_DELETE:
            # For delete, we'd need the content before deletion
            # This is handled by the prepare step if needed
            pass
        # Bash ops don't record undo

    def _audit_log(self, request: OperationRequest, result: OperationResult, start_time: float = 0) -> None:
        """Append to .noether/audit.jsonl."""
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": time.time(),
                "duration": time.time() - start_time if start_time else 0,
                "op_type": request.op_type.value,
                "source": request.source,
                "target": request.target_file or request.command or "",
                "success": result.success,
                "message": result.message[:200],
            }
            with open(self._audit_log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.debug("Audit log write failed: %s", e)
