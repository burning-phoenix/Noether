"""
Approval modal for sandbox operations.

Displays a modal dialog when an agent requests to perform
a potentially risky operation (command execution, file write, etc.).
"""

from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static
from textual.binding import Binding


class ApprovalModal(ModalScreen[bool]):
    """
    Modal dialog for approving sandbox operations.

    Displays the operation details and allows the user to
    approve or deny the request.
    """

    BINDINGS = [
        Binding("y", "approve", "Approve", show=True),
        Binding("n", "deny", "Deny", show=True),
        Binding("escape", "deny", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    ApprovalModal {
        align: center middle;
        background: $background 50%;
    }

    #approval-dialog {
        width: 60;
        height: auto;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }

    #approval-title {
        text-style: bold;
        color: $text;
        background: $warning;
        width: 100%;
        text-align: center;
        padding: 1;
        margin-bottom: 1;
    }

    #approval-description {
        margin: 1 0;
        color: $text-muted;
        text-align: center;
    }

    #approval-command {
        background: $surface-darken-1;
        padding: 1;
        margin: 1 0;
        border: solid $primary;
        color: $text;
        height: auto;
        max-height: 10;
    }

    #approval-risk {
        margin: 1 0;
        text-align: center;
        text-style: bold;
    }

    #approval-risk.safe { color: $success; }
    #approval-risk.review { color: $warning; }
    #approval-risk.approval { color: $error; }

    #approval-buttons {
        layout: horizontal;
        height: 3;
        margin-top: 2;
        align: center middle;
    }

    #approval-buttons Button {
        margin: 0 2;
        min-width: 15;
    }

    #approval-hint {
        text-align: center;
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        operation: str,
        target: str,
        description: str,
        risk_level: str = "approval",
    ) -> None:
        """
        Initialize the approval modal.

        Args:
            operation: Type of operation (command, write, delete)
            target: The command or path
            description: Human-readable description
            risk_level: Risk level (safe, review, approval)
        """
        super().__init__()
        self.operation = operation
        self.target = target
        self.description = description
        self.risk_level = risk_level

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-dialog"):
            yield Label(
                f"Approval Required ({self.risk_level.upper()})",
                id="approval-title"
            )

            yield Static(
                f"Operation: {self.operation}",
                id="approval-description"
            )

            yield Static(
                self.target,
                id="approval-command"
            )

            risk_label = Static(
                f"Risk Level: {self.risk_level}",
                id="approval-risk"
            )
            risk_label.add_class(self.risk_level)
            yield risk_label

            if self.description:
                yield Static(
                    self.description,
                    id="approval-reason"
                )

            with Grid(id="approval-buttons"):
                yield Button(
                    "Approve (Y)",
                    variant="success",
                    id="approve"
                )
                yield Button(
                    "Deny (N)",
                    variant="error",
                    id="deny"
                )

            yield Static(
                "Press Y to approve, N to deny",
                id="approval-hint"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        if event.button.id == "approve":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_approve(self) -> None:
        """Approve the operation."""
        self.dismiss(True)

    def action_deny(self) -> None:
        """Deny the operation."""
        self.dismiss(False)


class CommandApprovalModal(ApprovalModal):
    """Specialized modal for command execution approval."""

    def __init__(
        self,
        command: str,
        description: str,
        risk_level: str = "approval",
    ) -> None:
        super().__init__(
            operation="Execute Command",
            target=command,
            description=description,
            risk_level=risk_level,
        )


class FileWriteApprovalModal(ApprovalModal):
    """Specialized modal for file write approval."""

    def __init__(
        self,
        path: str,
        content_size: int,
        description: str = "",
    ) -> None:
        super().__init__(
            operation="Write File",
            target=path,
            description=description or f"File: {path}\nSize: {content_size:,} characters",
            risk_level="approval",
        )


class FileDeleteApprovalModal(ApprovalModal):
    """Specialized modal for file deletion approval."""

    def __init__(
        self,
        path: str,
        description: str = "",
    ) -> None:
        super().__init__(
            operation="Delete File",
            target=path,
            description=description or "This action cannot be undone",
            risk_level="approval",
        )


class ExploreCommandApprovalModal(ModalScreen[bool]):
    """
    Modal for batch approval of exploration commands.

    Shows all commands that will be executed and allows
    approving or denying all at once.
    """

    BINDINGS = [
        Binding("y", "approve", "Approve All", show=True),
        Binding("n", "deny", "Deny All", show=True),
        Binding("escape", "deny", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    ExploreCommandApprovalModal {
        align: center middle;
        background: $background 50%;
    }

    #explore-cmd-dialog {
        width: 75;
        height: auto;
        max-height: 80%;
        border: thick $success;
        background: $surface;
        padding: 1 2;
    }

    #explore-cmd-title {
        text-style: bold;
        color: $text;
        background: $success;
        width: 100%;
        text-align: center;
        padding: 1;
        margin-bottom: 1;
    }

    #explore-cmd-description {
        margin: 1 0;
        color: $text-muted;
        text-align: center;
    }

    #explore-cmd-list {
        background: $surface-darken-1;
        padding: 1;
        margin: 1 0;
        border: solid $primary;
        height: auto;
        max-height: 15;
        overflow-y: auto;
    }

    .cmd-item {
        padding: 0 1;
        margin-bottom: 1;
    }

    .cmd-command {
        color: $text;
        text-style: bold;
    }

    .cmd-desc {
        color: $text-muted;
    }

    #explore-cmd-buttons {
        layout: horizontal;
        height: 3;
        margin-top: 2;
        align: center middle;
    }

    #explore-cmd-buttons Button {
        margin: 0 2;
        min-width: 15;
    }

    #explore-cmd-hint {
        text-align: center;
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, commands: list[tuple[str, str]]) -> None:
        """
        Initialize explore command approval modal.

        Args:
            commands: List of (command, description) tuples
        """
        super().__init__()
        self.commands = commands

    def compose(self) -> ComposeResult:
        with Vertical(id="explore-cmd-dialog"):
            yield Label(
                f"Run {len(self.commands)} exploration command(s)?",
                id="explore-cmd-title"
            )

            yield Static(
                "The following commands will run in the project directory:",
                id="explore-cmd-description"
            )

            with Vertical(id="explore-cmd-list"):
                for cmd, desc in self.commands:
                    yield Static(
                        f"$ {cmd}\n  {desc}",
                        classes="cmd-item"
                    )

            with Grid(id="explore-cmd-buttons"):
                yield Button(
                    "Approve All (Y)",
                    variant="success",
                    id="approve"
                )
                yield Button(
                    "Deny All (N)",
                    variant="error",
                    id="deny"
                )

            yield Static(
                "Press Y to run all commands, N to skip",
                id="explore-cmd-hint"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        if event.button.id == "approve":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_approve(self) -> None:
        """Approve all commands."""
        self.dismiss(True)

    def action_deny(self) -> None:
        """Deny all commands."""
        self.dismiss(False)


class BatchFileApprovalModal(ModalScreen[bool]):
    """
    Modal for batch approval of multiple file operations.

    Shows all files to be created/modified and allows
    approving or denying all at once.
    """

    BINDINGS = [
        Binding("y", "approve", "Approve All", show=True),
        Binding("n", "deny", "Deny All", show=True),
        Binding("escape", "deny", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    BatchFileApprovalModal {
        align: center middle;
        background: $background 50%;
    }

    #batch-dialog {
        width: 70;
        height: auto;
        max-height: 80%;
        border: thick $success;
        background: $surface;
        padding: 1 2;
    }

    #batch-title {
        text-style: bold;
        color: $text;
        background: $success;
        width: 100%;
        text-align: center;
        padding: 1;
        margin-bottom: 1;
    }

    #batch-description {
        margin: 1 0;
        color: $text-muted;
        text-align: center;
    }

    #batch-file-list {
        background: $surface-darken-1;
        padding: 1;
        margin: 1 0;
        border: solid $primary;
        height: auto;
        max-height: 15;
        overflow-y: auto;
    }

    .file-item {
        padding: 0 1;
    }

    .file-create { color: $success; }
    .file-write { color: $warning; }
    .file-delete { color: $error; }

    #batch-buttons {
        layout: horizontal;
        height: 3;
        margin-top: 2;
        align: center middle;
    }

    #batch-buttons Button {
        margin: 0 2;
        min-width: 15;
    }

    #batch-hint {
        text-align: center;
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, file_operations: list) -> None:
        """
        Initialize batch approval modal.

        Args:
            file_operations: List of FileOperation objects
        """
        super().__init__()
        self.file_operations = file_operations

    def compose(self) -> ComposeResult:
        with Vertical(id="batch-dialog"):
            yield Label(
                f"Save {len(self.file_operations)} File(s)?",
                id="batch-title"
            )

            yield Static(
                "The following files will be created in the project directory:",
                id="batch-description"
            )

            with Vertical(id="batch-file-list"):
                for op in self.file_operations:
                    op_type = op.op_type.value.upper()
                    size = len(op.content) if op.content else 0

                    item = Static(
                        f"[{op_type}] {op.path} ({size} chars)",
                        classes=f"file-item file-{op.op_type.value}"
                    )
                    yield item

            with Grid(id="batch-buttons"):
                yield Button(
                    "Save All (Y)",
                    variant="success",
                    id="approve"
                )
                yield Button(
                    "Cancel (N)",
                    variant="error",
                    id="deny"
                )

            yield Static(
                "Press Y to save all files, N to cancel",
                id="batch-hint"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        if event.button.id == "approve":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_approve(self) -> None:
        """Approve all operations."""
        self.dismiss(True)

    def action_deny(self) -> None:
        """Deny all operations."""
        self.dismiss(False)
