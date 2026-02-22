"""
Undo confirmation modal dialog.

Displays details about the operation to be undone and
allows the user to confirm or cancel.
"""

from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static
from textual.binding import Binding

from ...orchestration.undo import UndoEntry, SnapshotEntry


class UndoConfirmationModal(ModalScreen[bool]):
    """Modal dialog for confirming an undo operation."""

    BINDINGS = [
        Binding("y", "approve", "Undo", show=True),
        Binding("n", "deny", "Cancel", show=True),
        Binding("escape", "deny", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    UndoConfirmationModal {
        align: center middle;
        background: $background 50%;
    }

    #undo-dialog {
        width: 60;
        height: auto;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }

    #undo-title {
        text-style: bold;
        color: $text;
        background: $warning;
        width: 100%;
        text-align: center;
        padding: 1;
        margin-bottom: 1;
    }

    #undo-action-type {
        margin: 1 0;
        color: $text-muted;
        text-align: center;
    }

    #undo-detail {
        background: $surface-darken-1;
        padding: 1;
        margin: 1 0;
        border: solid $primary;
        color: $text;
        height: auto;
        max-height: 10;
    }

    #undo-buttons {
        layout: horizontal;
        height: 3;
        margin-top: 2;
        align: center middle;
    }

    #undo-buttons Button {
        margin: 0 2;
        min-width: 15;
    }

    #undo-hint {
        text-align: center;
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, entry) -> None:
        """Accept UndoEntry or SnapshotEntry."""
        super().__init__()
        self.entry = entry

    def compose(self) -> ComposeResult:
        with Vertical(id="undo-dialog"):
            yield Label("Undo Operation", id="undo-title")

            # Handle both SnapshotEntry and legacy UndoEntry
            if isinstance(self.entry, SnapshotEntry):
                action_label = "SNAPSHOT RESTORE"
                if self.entry.content_before is None:
                    detail = f"This will DELETE: {self.entry.path}"
                else:
                    prev_size = len(self.entry.content_before)
                    detail = f"This will REVERT: {self.entry.path}\nRestore to previous content ({prev_size} chars)"
            elif hasattr(self.entry, 'action_type'):
                action_label = self.entry.action_type.upper()
                if self.entry.action_type == "edit":
                    detail = (
                        f"Target: {self.entry.edit_target}\n"
                        f"This will reverse the edit:\n"
                        f"  Replace back: {(self.entry.edit_replace_content or '')[:100]}...\n"
                        f"  With original: {(self.entry.edit_search_content or '')[:100]}..."
                    )
                elif self.entry.action_type == "file_create":
                    detail = f"This will DELETE: {self.entry.file_path}"
                elif self.entry.action_type == "file_write":
                    prev_size = len(self.entry.file_previous_content or "")
                    detail = f"This will REVERT: {self.entry.file_path}\nRestore to previous content ({prev_size} chars)"
                else:
                    detail = self.entry.description
            else:
                action_label = "UNDO"
                detail = str(self.entry)

            yield Static(
                f"Action: {action_label}",
                id="undo-action-type"
            )

            yield Static(detail, id="undo-detail")

            with Grid(id="undo-buttons"):
                yield Button(
                    "Undo (Y)",
                    variant="warning",
                    id="approve"
                )
                yield Button(
                    "Cancel (N)",
                    variant="error",
                    id="deny"
                )

            yield Static(
                "Press Y to undo, N to cancel",
                id="undo-hint"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approve":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)
