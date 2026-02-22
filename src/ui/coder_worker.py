"""
Coder worker — extracted from app.py.

Handles Coder code generation, file operation parsing, and
synchronous file writes after batch approval.
"""

import logging
from pathlib import Path
from threading import Lock
from typing import Optional, Callable, Iterator

from ..orchestration.file_operations import FileOperationParser, FileOpType
from ..orchestration.orchestrator import TaskOrchestrator
from ..orchestration.undo import UnifiedUndoStack
from ..observability.tracer import tracer

logger = logging.getLogger("noether.coder")


class CoderWorker:
    """Manages Coder code generation and file operations.

    This is a pure-logic class — no Textual imports, no @work decorators.
    app.py creates thin @work(thread=True) wrappers that delegate here.

    Dependencies are injected so the class is testable without a TUI.
    """

    def __init__(
        self,
        orchestrator: TaskOrchestrator,
        undo_stack: UnifiedUndoStack,
        file_op_parser: Optional[FileOperationParser] = None,
    ):
        self.orchestrator = orchestrator
        self.undo_stack = undo_stack
        self.file_op_parser = file_op_parser
        self._streaming_lock = Lock()
        self._is_streaming = False

    @property
    def is_streaming(self) -> bool:
        with self._streaming_lock:
            return self._is_streaming

    def set_streaming(self, value: bool) -> None:
        with self._streaming_lock:
            self._is_streaming = value

    def stream_code(
        self,
        backend,
        prompt: str,
        task_id: Optional[str] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Stream code generation from a backend.

        Args:
            backend: LLM backend to stream from
            prompt: The prompt to send
            task_id: Optional task ID for status display
            on_chunk: Callback for each output chunk
            on_status: Callback for status updates

        Returns:
            Full response text
        """
        full_response = ""

        # Get task description for status
        task_desc = ""
        if task_id:
            task_obj = self.orchestrator.get_task(task_id)
            if task_obj:
                task_desc = task_obj.description[:50]

        if on_status:
            on_status(f"Generating: {task_desc}..." if task_desc else "Generating code...")

        with tracer.span("coder.stream", task_id=task_id or "", task_desc=task_desc) as span:

            for chunk in backend.stream(prompt, include_reasoning=False, max_tokens=16384):
                full_response += chunk
                if on_chunk:
                    on_chunk(chunk)
                # Periodic status update
                if on_status and len(full_response) % 500 < len(chunk):
                    on_status(f"Generating... ({len(full_response)} chars)")

            span.set_result(response_length=len(full_response))

        return full_response

    def parse_file_ops(
        self,
        response: str,
        task_id: Optional[str] = None,
    ) -> list:
        """Parse file operations from LLM output.

        Args:
            response: Full LLM response text
            task_id: Optional task ID for filename hints

        Returns:
            List of FileOperation objects (may be empty)
        """
        if not self.file_op_parser:
            return []

        hint = None
        if task_id:
            task_obj = self.orchestrator.get_task(task_id)
            if task_obj and task_obj.expected_output:
                hint = task_obj.expected_output

        file_ops = self.file_op_parser.parse_with_hint(response, hint)

        if not file_ops:
            logger.warning(
                "No file ops found in LLM output (%d chars). "
                "Hint was: %s",
                len(response),
                hint,
            )

        return file_ops

    def execute_file_ops_sync(
        self,
        file_ops: list,
        sandbox,
    ) -> tuple[int, int]:
        """Execute file operations synchronously (approval already granted).

        Args:
            file_ops: List of FileOperation objects
            sandbox: FileSystemSandbox for path validation

        Returns:
            (success_count, fail_count)
        """
        success_count = 0
        fail_count = 0

        with tracer.span("coder.file_ops", op_count=len(file_ops)) as span:

            for op in file_ops:
                try:
                    # Read previous content for undo (before writing)
                    prev_content = None
                    if sandbox and op.path:
                        prev_content = sandbox.safe_read(op.path)

                    # Execute the write synchronously
                    if op.op_type in (FileOpType.CREATE, FileOpType.WRITE):
                        success = sandbox.safe_write_sync(
                            op.path,
                            op.content or "",
                        )
                    elif op.op_type == FileOpType.MKDIR:
                        resolved = sandbox.resolve_path(op.path)
                        if resolved:
                            resolved.mkdir(parents=True, exist_ok=True)
                            success = True
                        else:
                            success = False
                    elif op.op_type == FileOpType.DELETE:
                        resolved = sandbox.resolve_path(op.path)
                        if resolved and resolved.exists():
                            resolved.unlink()
                            success = True
                        else:
                            success = resolved is not None
                    else:
                        success = False

                    if success:
                        success_count += 1
                        # Record to undo stack
                        if op.op_type in (FileOpType.CREATE, FileOpType.WRITE):
                            if prev_content is not None:
                                self.undo_stack.push_file_write(
                                    path=op.path,
                                    previous_content=prev_content,
                                    new_content=op.content or "",
                                )
                            else:
                                self.undo_stack.push_file_create(
                                    path=op.path,
                                    content=op.content or "",
                                )
                        logger.info("File op success: %s %s", op.op_type.value, op.path)
                    else:
                        fail_count += 1
                        logger.warning("File op failed: %s %s", op.op_type.value, op.path)

                except Exception as e:
                    fail_count += 1
                    logger.error("File op error for %s: %s", op.path, e)

            span.set_result(success=success_count, failed=fail_count)

        return success_count, fail_count

    def complete_task(self, task_id: str, result: str) -> None:
        """Mark current task as completed."""
        self.orchestrator.complete_current_task(result)

    def fail_task(self, task_id: str, error: str) -> None:
        """Mark current task as failed."""
        self.orchestrator.fail_current_task(error)
