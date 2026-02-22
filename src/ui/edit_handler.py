"""
Edit handler — extracted from app.py.

Handles Search/Replace edit execution: match → approve → write → undo.
"""

import logging
from pathlib import Path
from threading import Event
from typing import Optional

from ..orchestration.editor import (
    CodeEditor,
    SearchReplaceOperation,
    parse_search_replace_blocks,
)
from ..orchestration.undo import UnifiedUndoStack

logger = logging.getLogger("noether.edit")


class EditHandler:
    """Handles search/replace edit execution with modal approval.

    This class needs a few Textual primitives (push_screen, call_from_thread)
    passed in as callbacks — it doesn't import Textual directly, keeping it
    testable with mock callbacks.
    """

    def __init__(
        self,
        code_editor: CodeEditor,
        undo_stack: UnifiedUndoStack,
        sandbox=None,
        planner_agent=None,
    ):
        self.code_editor = code_editor
        self.undo_stack = undo_stack
        self.sandbox = sandbox
        self.planner_agent = planner_agent

        # These are set by app.py after init — they bridge to Textual
        self._push_screen: Optional[callable] = None  # call_from_thread(push_screen, modal, cb)
        self._notify: Optional[callable] = None        # _safe_call(notify, ...)
        self._post_message: Optional[callable] = None   # _safe_call(post_message, ...)

    def set_ui_callbacks(
        self,
        push_screen_fn,
        notify_fn,
        post_message_fn,
    ):
        """Set UI callbacks for modal approval and notifications.

        Args:
            push_screen_fn: callable(modal, on_dismissed_callback)
            notify_fn: callable(message, severity=None)
            post_message_fn: callable(message)
        """
        self._push_screen = push_screen_fn
        self._notify = notify_fn
        self._post_message = post_message_fn

    @staticmethod
    def format_match_info(result) -> str:
        """Format match info string from an EditResult."""
        if result.match_result is not None and result.match_result.match_type:
            info = f" ({result.match_result.match_type} match"
            if result.match_result.confidence:
                info += f", {result.match_result.confidence:.0%} confidence"
            return info + ")"
        return ""

    def execute_edit(
        self,
        operation: SearchReplaceOperation,
        push_undo: bool = True,
    ) -> bool:
        """Execute a single edit: match → approve → write → undo.

        Sync method — safe to call from @work(thread=True) workers.
        Uses push_screen with callback + threading.Event for modal approval.

        Returns:
            True on success, False on failure
        """
        # 1. Match
        try:
            result = self.code_editor.apply_edit_sync(operation)
        except Exception as e:
            logger.error("Edit exception: %s: %s", type(e).__name__, e)
            error_msg = str(e)[:100]
            if self._notify:
                self._notify(f"Edit failed with error: {error_msg}", severity="error")
            if self.planner_agent:
                self.planner_agent.memory.record_system_event(
                    f"Edit operation failed: {type(e).__name__}: {str(e)[:500]}"
                )
            return False

        match_info = self.format_match_info(result)

        # 2. File edits with pending write — need approval
        if result.pending_file_write is not None:
            return self._handle_file_edit(
                operation, result, match_info, push_undo
            )

        # 3. coder_output edits (already applied by apply_edit_sync)
        if result.success:
            if self._notify:
                self._notify(f"Edit applied{match_info}: {operation.reason}")
            if push_undo:
                self.undo_stack.push_edit(
                    target=operation.target,
                    search_content=operation.search_content,
                    replace_content=operation.replace_content,
                    reason=operation.reason,
                )
            return True

        # 4. Failure — notify + feedback for LLM retry
        if self._notify:
            self._notify(f"Edit failed: {operation.reason}", severity="error")
        if result.match_result and result.match_result.closest_match:
            self._send_edit_feedback(operation, result)
        if self.planner_agent:
            self.planner_agent.memory.record_system_event(
                f"Edit failed on {operation.target}: {result.message}"
            )
        return False

    def _handle_file_edit(
        self,
        operation: SearchReplaceOperation,
        result,
        match_info: str,
        push_undo: bool,
    ) -> bool:
        """Handle a file edit that requires approval and disk write."""
        from .modals.approval_modal import ApprovalModal

        # Load full file content into Planner memory before edit
        if self.planner_agent:
            try:
                target_path = Path(operation.target)
                if not target_path.is_absolute() and self.sandbox:
                    target_path = self.sandbox.resolve_path(operation.target) or target_path
                if target_path.exists():
                    full_content = target_path.read_text(encoding="utf-8")
                    self.planner_agent.memory.record_system_event(
                        f"Loading full content of {operation.target} for edit ({len(full_content)} chars)"
                    )
                    self.planner_agent.set_project_context(full_content[:50000])
            except Exception:
                pass

        # Show approval modal
        if self._push_screen:
            modal = ApprovalModal(
                operation="File Edit",
                target=f"{operation.target}{match_info}",
                description=(
                    f"{operation.reason}\n\n"
                    f"Search:\n{operation.search_content[:200]}...\n\n"
                    f"Replace:\n{operation.replace_content[:200]}..."
                ),
                risk_level="review",
            )

            approval_result: list[bool] = []
            event = Event()

            def _on_dismissed(approved: bool) -> None:
                approval_result.append(approved)
                event.set()

            self._push_screen(modal, _on_dismissed)
            event.wait(timeout=60)

            if not approval_result or not approval_result[0]:
                if self._notify:
                    self._notify(f"Edit rejected: {operation.reason}", severity="warning")
                self._swap_to_skim_context(operation.target)
                return False

        # Write via sandbox
        try:
            if self.sandbox:
                file_path = self.sandbox.resolve_path(operation.target)
                if not file_path:
                    if self._notify:
                        self._notify(f"Path not allowed: {operation.target}", severity="error")
                    return False
            else:
                file_path = Path(operation.target)
                if not file_path.is_absolute():
                    if self._notify:
                        self._notify(f"No sandbox and path is relative: {operation.target}", severity="error")
                    return False

            if not file_path.exists():
                if self._notify:
                    self._notify(f"File not found: {file_path}", severity="error")
                return False

            file_path.write_text(result.pending_file_write, encoding="utf-8")
            if self._notify:
                self._notify(f"Edit applied{match_info}: {operation.reason}")
            if push_undo:
                self.undo_stack.push_edit(
                    target=operation.target,
                    search_content=operation.search_content,
                    replace_content=operation.replace_content,
                    reason=operation.reason,
                )
            self._swap_to_skim_context(operation.target)
            return True

        except Exception as e:
            logger.error("File write error: %s", e)
            if self._notify:
                self._notify(f"File write failed: {e}", severity="error")
            self._swap_to_skim_context(operation.target)
            return False

    def _swap_to_skim_context(self, target: str) -> None:
        """Replace full file content in Planner's context with a skim."""
        if not self.planner_agent:
            return
        try:
            from ..agents.explore_agent import skim_file, find_file_in_project

            resolved = find_file_in_project(target, Path.cwd())
            if not resolved or not resolved.exists():
                return

            content = resolved.read_text(encoding="utf-8")
            skimmed = skim_file(content, target)

            self.planner_agent.set_project_context(skimmed)
            self.planner_agent.memory.record_system_event(
                f"File context for {target} swapped to skim ({len(skimmed)} chars)"
            )
        except Exception:
            pass

    def _send_edit_feedback(self, operation: SearchReplaceOperation, result) -> None:
        """Send edit failure feedback for LLM self-correction."""
        if not self._post_message:
            return
        try:
            # Import lazily to avoid circular imports
            from .messages import EditFeedbackMessage
            self._post_message(
                EditFeedbackMessage(
                    target=operation.target,
                    error_type="no_match",
                    feedback=result.message,
                    original_search=operation.search_content,
                    closest_match=result.match_result.closest_match,
                    similarity=result.match_result.closest_similarity,
                ),
            )
        except Exception as e:
            logger.warning("Could not send edit feedback: %s", e)

    def apply_edit_blocks(self, response: str) -> int:
        """Parse and apply any S/R blocks found in a response.

        Returns:
            Number of edits applied
        """
        edits = parse_search_replace_blocks(response)
        if edits:
            if self._notify:
                self._notify(f"Found {len(edits)} edit block(s)")
            for edit in edits:
                self.execute_edit(edit)
        return len(edits)
