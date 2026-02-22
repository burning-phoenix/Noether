from textual.widget import Widget
from textual.widgets import Label, DataTable, RichLog, Static
from textual.containers import Vertical, Container
from textual.reactive import reactive
from textual.events import MouseDown, MouseMove, MouseUp
from textual.geometry import Offset

class SidebarResizer(Widget):
    """Draggable handle to resize the sidebar."""

    DEFAULT_CSS = """
    SidebarResizer {
        width: 1;
        height: 100%;
        background: $primary;
        opacity: 0.5;
    }

    SidebarResizer:hover {
        opacity: 1.0;
        background: $accent;
    }

    SidebarResizer.drag {
        opacity: 1.0;
        background: $accent;
    }
    
    SidebarResizer.hidden {
        display: none;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.dragging = False
        self.mouse_origin: Offset | None = None
        self.sidebar_origin_width: int = 0

    async def on_mouse_down(self, event: MouseDown) -> None:
        self.capture_mouse()
        self.dragging = True
        self.add_class("drag")
        self.mouse_origin = event.screen_offset
        try:
            sidebar = self.app.query_one(RightSidebar)
            self.sidebar_origin_width = sidebar.styles.width.value if sidebar.styles.width else sidebar.size.width
        except Exception:
            self.sidebar_origin_width = 35

    async def on_mouse_move(self, event: MouseMove) -> None:
        if self.dragging and self.mouse_origin is not None:
            delta_x = self.mouse_origin.x - event.screen_x
            new_width = self.sidebar_origin_width + delta_x
            if new_width < 15:
                new_width = 15
            elif new_width > 120:
                new_width = 120
            
            try:
                sidebar = self.app.query_one(RightSidebar)
                sidebar.styles.width = new_width
            except Exception:
                pass

    async def on_mouse_up(self, event: MouseUp) -> None:
        self.release_mouse()
        self.dragging = False
        self.remove_class("drag")
        self.mouse_origin = None


class RightSidebar(Widget):
    """
    Collapsible right sidebar containing:
    1. Session Tasks (Checklist)
    2. Activity Log (Real-time agent actions)
    """

    DEFAULT_CSS = """
    RightSidebar {
        width: 35;
        background: $surface;
        border-left: vkey $primary;
        height: 100%;
    }

    RightSidebar.hidden {
        display: none;
    }

    .section-title {
        background: $primary;
        color: $text;
        text-style: bold;
        padding: 0 1;
        width: 100%;
        margin-top: 1;
    }

    #session-tasks-table {
        height: 1fr;
        border-bottom: solid $secondary;
        scrollbar-gutter: stable;
    }

    #token-usage-display {
        padding: 0 1;
        color: $text-muted;
        height: auto;
    }

    #activity-log {
        height: 1fr;
        min-height: 10;
        background: $surface-darken-1;
        border-top: solid $secondary;
        scrollbar-gutter: stable;
    }
    
    #title-container {
        dock: top;
        height: auto;
        background: $accent;
        color: $text;
        text-align: center;
        text-style: bold;
        padding: 0 1;
    }
    """

    def compose(self):
        with Vertical():
            yield Static("SESSION OVERVIEW", id="title-container")

            yield Label("TASKS", classes="section-title")
            yield DataTable(id="session-tasks-table")

            yield Label("TOKENS", classes="section-title")
            yield Static("Session: 0 tokens", id="token-usage-display")

            yield Label("ACTIVITY LOG", classes="section-title")
            yield RichLog(id="activity-log", markup=True, wrap=True)

    def on_mount(self):
        table = self.query_one("#session-tasks-table", DataTable)
        table.add_columns("Stat", "Task")
        table.cursor_type = "row"
        table.zebra_stripes = True

    def toggle(self):
        """Toggle visibility."""
        if self.has_class("hidden"):
            self.remove_class("hidden")
            try:
                self.app.query_one(SidebarResizer).remove_class("hidden")
            except Exception:
                pass
        else:
            self.add_class("hidden")
            try:
                self.app.query_one(SidebarResizer).add_class("hidden")
            except Exception:
                pass

    def log_activity(self, message: str):
        """Log a rich text message to the activity log."""
        log = self.query_one("#activity-log", RichLog)
        log.write(message)

    def update_token_usage(self, totals: dict) -> None:
        """Update the token usage display."""
        display = self.query_one("#token-usage-display", Static)
        total = totals.get("total_tokens", 0)
        calls = totals.get("call_count", 0)
        if total == 0:
            display.update("Session: 0 tokens")
        else:
            display.update(f"Session: {total:,} tokens ({calls} calls)")

    def update_tasks(self, tasks: list[dict]):
        """Update the tasks table."""
        table = self.query_one("#session-tasks-table", DataTable)
        table.clear()
        
        # Sort so current/pending are usually interesting, but we want full history.
        # Incoming list is already reverse chronological from orchestrator.
        
        for t in tasks:
            # Map status to icon
            status = t["status"]
            icon = "❓"
            if status == "pending": icon = "⏳"
            elif status == "in_progress": icon = "🔄"
            elif status == "completed": icon = "✅"
            elif status == "failed": icon = "❌"
            
            # format description — wider now without ID column
            desc = t["description"]
            if len(desc) > 28:
                desc = desc[:25] + "..."

            table.add_row(icon, desc)
