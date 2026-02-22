"""
Modal dialog for codebase indexing approval.

Shows file count, estimated time, and explanation before
starting the embedding/indexing process.
"""

from textual.app import ComposeResult
from textual.containers import Vertical, Grid
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static
from textual.binding import Binding


class IndexApprovalModal(ModalScreen[bool]):
    """Approval modal shown before codebase indexing begins."""

    BINDINGS = [
        Binding("y", "approve", "Approve", show=True),
        Binding("n", "deny", "Deny", show=True),
        Binding("escape", "deny", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    IndexApprovalModal {
        align: center middle;
        background: $background 50%;
    }

    #index-dialog {
        width: 64;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #index-title {
        text-style: bold;
        color: $text;
        background: $primary;
        width: 100%;
        text-align: center;
        padding: 1;
        margin-bottom: 1;
    }

    #index-info {
        margin: 1 0;
        color: $text-muted;
    }

    #index-stats {
        background: $surface-darken-1;
        padding: 1;
        margin: 1 0;
        border: solid $primary;
        color: $text;
        height: auto;
    }

    #index-buttons {
        layout: horizontal;
        height: 3;
        margin-top: 2;
        align: center middle;
    }

    #index-buttons Button {
        margin: 0 2;
        min-width: 15;
    }

    #index-hint {
        text-align: center;
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, file_count: int, file_types: list[str]) -> None:
        super().__init__()
        self.file_count = file_count
        self.file_types = file_types

    @property
    def estimated_seconds(self) -> float:
        return (self.file_count * 0.05) + 4.0

    def compose(self) -> ComposeResult:
        est = self.estimated_seconds
        if est < 60:
            time_str = f"{est:.0f}s"
        else:
            time_str = f"{est / 60:.1f}m"

        types_str = ", ".join(self.file_types)

        with Vertical(id="index-dialog"):
            yield Label("Index Codebase", id="index-title")

            yield Static(
                "This will scan your project files, generate embeddings "
                "using a local ONNX model, and store them in a vector "
                "database for semantic code search.",
                id="index-info",
            )

            yield Static(
                f"Files to index:  {self.file_count}\n"
                f"File types:      {types_str}\n"
                f"Estimated time:  ~{time_str}",
                id="index-stats",
            )

            with Grid(id="index-buttons"):
                yield Button("Index (Y)", variant="success", id="approve")
                yield Button("Cancel (N)", variant="error", id="deny")

            yield Static(
                "Press Y to start indexing, N to cancel",
                id="index-hint",
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
