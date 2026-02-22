"""
Undo Manager

Handles undo requests, displays confirmation modals, and executes the reverse
operations. Supports both snapshot-based undo (new) and legacy S/R undo.
"""

import logging
from typing import Optional, Any
from textual import work, on
from textual.widget import Widget

from ..ui.messages import UndoRequest, UndoComplete
from ..ui.modals.undo_modal import UndoConfirmationModal

logger = logging.getLogger("noether.undo_manager")

class UndoManager(Widget):
    """
    Invisible Textual widget that listens to UndoRequest messages
    and manages the undo stack execution cycle.
    """

    DEFAULT_CSS = """
    UndoManager {
        display: none;
    }
    """

    def __init__(
        self,
        undo_stack: Any,
        code_editor: Optional[Any] = None,
        filesystem_sandbox: Optional[Any] = None,
        execute_edit_fn: Optional[Any] = None,
    ):
        super().__init__()
        self.undo_stack = undo_stack
        self.code_editor = code_editor
        self.filesystem_sandbox = filesystem_sandbox
        self.execute_edit_fn = execute_edit_fn

    @on(UndoRequest)
    async def on_undo_request(self, message: UndoRequest) -> None:
        """Handle undo request."""
        if self.undo_stack.is_empty():
            self.app.notify("Nothing to undo")
            return

        entry = self.undo_stack.peek()
        if not entry:
            return

        self.app.push_screen(
            UndoConfirmationModal(entry=entry),
            callback=self._on_undo_modal_dismissed,
        )

    def _on_undo_modal_dismissed(self, approved: bool) -> None:
        """Callback when undo confirmation modal is dismissed."""
        if not approved:
            self.app.notify("Undo cancelled")
            return

        entry = self.undo_stack.pop()
        if not entry:
            return

        self._execute_undo(entry)

    @work(thread=True)
    def _execute_undo(self, entry) -> None:
        """Execute the undo operation in background thread.

        Handles both:
        - SnapshotEntry (new): restore content_before or delete
        - UndoEntry (legacy): swap S/R and re-apply, or restore file
        """
        import asyncio
        from ..orchestration.undo import SnapshotEntry

        try:
            # New snapshot-based undo
            if isinstance(entry, SnapshotEntry):
                self._execute_snapshot_undo(entry)
                return

            # Legacy UndoEntry handling
            if entry.action_type == "edit":
                from ..orchestration.editor import SearchReplaceOperation
                reverse_op = SearchReplaceOperation(
                    target=entry.edit_target or "coder_output",
                    search_content=entry.edit_replace_content or "",
                    replace_content=entry.edit_search_content or "",
                    reason=f"Undo: {entry.description}",
                )
                if self.code_editor and self.execute_edit_fn:
                    success = self.execute_edit_fn(reverse_op, push_undo=False)
                    if success:
                        self.app.call_from_thread(self.app.notify, f"Undo successful: {entry.description}")
                    else:
                        self.app.call_from_thread(self.app.notify, f"Undo failed", severity="error")
                else:
                    self.app.call_from_thread(self.app.notify, "Code editor not available", severity="error")

            elif entry.action_type == "file_create":
                if self.filesystem_sandbox:
                    loop = asyncio.new_event_loop()
                    try:
                        success = loop.run_until_complete(
                            self.filesystem_sandbox.safe_delete(
                                entry.file_path or "",
                                description=f"Undo file creation: {entry.file_path}",
                            )
                        )
                    finally:
                        loop.close()
                    if success:
                        self.app.call_from_thread(self.app.notify, f"Undo: Deleted {entry.file_path}")
                    else:
                        self.app.call_from_thread(self.app.notify, f"Undo failed: Could not delete {entry.file_path}", severity="error")

            elif entry.action_type == "file_write":
                if self.filesystem_sandbox and entry.file_previous_content is not None:
                    success = self.filesystem_sandbox.safe_write_sync(
                        entry.file_path or "",
                        entry.file_previous_content,
                    )
                    if success:
                        self.app.call_from_thread(self.app.notify, f"Undo: Reverted {entry.file_path}")
                    else:
                        self.app.call_from_thread(self.app.notify, f"Undo failed: Could not revert {entry.file_path}", severity="error")

            # Show result in chat
            self.app.call_from_thread(
                self.app._schedule_system_message,
                f"Undo completed: {entry.description}",
                "yellow"
            )
            self.app.call_from_thread(
                self.post_message,
                UndoComplete(success=True, description=entry.description)
            )

        except Exception as e:
            self.app.call_from_thread(self.app.notify, f"Undo error: {e}", severity="error")
            self.app.call_from_thread(
                self.post_message,
                UndoComplete(success=False, description=str(e))
            )

    def _execute_snapshot_undo(self, entry) -> None:
        """Execute snapshot-based undo: restore content_before or delete file."""
        if entry.content_before is None:
            # File didn't exist before — delete it
            if self.filesystem_sandbox:
                import asyncio
                loop = asyncio.new_event_loop()
                try:
                    success = loop.run_until_complete(
                        self.filesystem_sandbox.safe_delete(
                            entry.path,
                            description=f"Undo: delete {entry.path}",
                        )
                    )
                finally:
                    loop.close()
                if success:
                    self.app.call_from_thread(self.app.notify, f"Undo: Deleted {entry.path}")
                else:
                    self.app.call_from_thread(self.app.notify, f"Undo failed: Could not delete {entry.path}", severity="error")
        else:
            # Restore previous content
            if self.filesystem_sandbox:
                success = self.filesystem_sandbox.safe_write_sync(
                    entry.path,
                    entry.content_before,
                )
                if success:
                    self.app.call_from_thread(self.app.notify, f"Undo: Reverted {entry.path}")
                else:
                    self.app.call_from_thread(self.app.notify, f"Undo failed: Could not revert {entry.path}", severity="error")

        self.app.call_from_thread(
            self.app._schedule_system_message,
            f"Undo completed: {entry.description}",
            "yellow"
        )
        self.app.call_from_thread(
            self.post_message,
            UndoComplete(success=True, description=entry.description)
        )
