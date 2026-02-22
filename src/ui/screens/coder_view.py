"""
Coder View pane - displays real-time code output with line numbers.
"""

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import TextArea, Static, Input
from textual import on

from ..messages import CoderRequest, CoderResponse


class CoderViewPane(Container):
    """Main pane for viewing Coder's real-time code generation."""

    DEFAULT_CSS = """
    CoderViewPane {
        height: 1fr;
        width: 100%;
    }

    #coder-output {
        height: 1fr;
        border: none;
    }

    #coder-status {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        padding: 0 1;
        color: $text-muted;
    }

    #coder-input {
        dock: bottom;
        height: 3;
        margin: 0;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._full_content = "# Coder Code Output\n# Type a prompt below to generate code\n"
        self._status = "Idle"
        self._task_id = ""
        self._local_model_available = True

    def compose(self) -> ComposeResult:
        yield TextArea(
            self._full_content,
            language="python",
            theme="monokai",
            show_line_numbers=True,
            read_only=True,
            id="coder-output",
        )
        yield Static(f"Status: {self._status}", id="coder-status")
        yield Input(placeholder="Send prompt to Coder...", id="coder-input")

    def set_local_model_unavailable(self) -> None:
        """Disable direct Coder input when no local model is available."""
        self._local_model_available = False
        self._full_content = (
            "# Local Model Not Available\n"
            "#\n"
            "# Add the path to the local model to use this.\n"
            "#   noether --model /path/to/model.gguf\n"
            "#\n"
            "# Code generation is available via Chat (Ctrl+2)\n"
            "# using the API-based coder in Fast Mode.\n"
        )
        self.get_output_area().load_text(self._full_content)
        coder_input = self.query_one("#coder-input", Input)
        coder_input.placeholder = "Local model required — use Chat tab for code generation"
        coder_input.disabled = True
        self.set_status("No local model")

    def set_local_model_available(self) -> None:
        """Re-enable direct Coder input when local model becomes available."""
        self._local_model_available = True
        coder_input = self.query_one("#coder-input", Input)
        coder_input.placeholder = "Send prompt to Coder..."
        coder_input.disabled = False
        self.set_status("Idle")

    def get_output_area(self) -> TextArea:
        """Get the output TextArea."""
        return self.query_one("#coder-output", TextArea)

    @on(Input.Submitted, "#coder-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle direct input to Coder."""
        prompt = event.value.strip()
        if not prompt:
            return

        event.input.clear()
        self.clear_output()
        self.set_status("Processing...")
        self.post_message(CoderRequest(prompt))

    @on(CoderResponse)
    def on_coder_response(self, message: CoderResponse) -> None:
        """Handle coder response updates from the dispatcher."""
        if getattr(message, "status", None):
            self.set_status(message.status)

        if message.is_complete:
            self.set_status("Done")
            if hasattr(self, "on_streaming_complete"):
                self.on_streaming_complete()
        elif message.output:
            self.append_output(message.output)

    def update_output(self, content: str) -> None:
        """Replace the output content."""
        self._full_content = content
        self.get_output_area().load_text(content)

    def append_output(self, chunk: str) -> None:
        """Append streaming chunk to output."""
        self._full_content += chunk
        output = self.get_output_area()
        output.load_text(self._full_content)
        output.scroll_end()

    def clear_output(self) -> None:
        """Clear the output."""
        self._full_content = ""
        self.get_output_area().load_text("")

    def set_status(self, status: str) -> None:
        """Update the status bar."""
        self._status = status
        self.query_one("#coder-status", Static).update(f"Status: {status}")

    def set_task_id(self, task_id: str) -> None:
        """Set the current task ID in status."""
        self._task_id = task_id
        status_text = f"Status: {self._status}"
        if task_id:
            status_text += f" | Task: {task_id}"
        self.query_one("#coder-status", Static).update(status_text)

    def get_line_content(self, line_number: int) -> str:
        """Get content of a specific line for Planner editing."""
        lines = self._full_content.split("\n")
        if 0 < line_number <= len(lines):
            return lines[line_number - 1]
        return ""

    def edit_lines(self, start: int, end: int, new_content: str) -> bool:
        """Edit lines in the output (called by Planner)."""
        lines = self._full_content.split("\n")
        if 0 < start <= end <= len(lines):
            new_lines = new_content.split("\n")
            lines[start - 1 : end] = new_lines
            self._full_content = "\n".join(lines)
            self.get_output_area().load_text(self._full_content)
            return True
        return False
