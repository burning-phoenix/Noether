"""
Task Manager pane - chat with Planner and manage task queue.

Provides:
- Planner chat for scope refinement
- /confirm command to approve scope and start decomposition
- /mode fast|local to switch execution mode
- /run to execute pending tasks
"""

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import DataTable, Input, Static, Label, Button, Collapsible
from textual import on

from ..messages import PlannerRequest, ModeSwitch, ConfirmScope, ExecuteTasks, ExploreAndAddContext, StartAutonomousLoop, PlannerModeSwitch, UndoRequest, PlannerResponse, CoderResponse, ClearContext
from ...modes.planner_modes import PlannerModeState, PlannerMode, PlanSubMode


class TaskManagerPane(Container):
    """Task management pane with Planner chat and task queue."""

    DEFAULT_CSS = """
    TaskManagerPane {
        height: 1fr;
        width: 100%;
    }

    #status-bar {
        dock: top;
        height: 1;
        background: $surface-darken-1;
        padding: 0 1;
    }

    #mode-indicator {
        width: auto;
        padding: 0 1;
    }

    .mode-fast {
        color: $success;
    }

    .mode-local {
        color: $warning;
    }

    #planner-mode-indicator {
        width: auto;
        padding: 0 1;
    }

    .planner-go {
        color: $success;
    }

    .planner-plan {
        color: #5599ff;
    }

    .planner-discovery {
        color: $warning;
    }

    #status-info {
        width: 1fr;
        text-align: right;
        padding: 0 2;
    }

    #chat-container {
        height: 1fr;
        border-top: hkey $surface-darken-2;
        border-bottom: hkey $surface-darken-2;
        padding: 0 1;
        overflow-y: auto;
    }

    #planner-input {
        dock: bottom;
        height: 3;
        margin: 1 0;
    }

    #task-queue {
        dock: bottom;
        height: auto;
        min-height: 5;
        max-height: 10;
        border: solid $accent;
        margin-bottom: 1;
    }

    Collapsible {
        width: 100%;
        height: auto;
        background: $surface;
        margin: 0;
        padding: 0 0 1 0;
    }

    CollapsibleTitle {
        padding: 0 1;
        color: $text-muted;
    }

    CollapsibleTitle:hover {
        color: $text;
        background: $surface-lighten-1;
    }

    Collapsible.-collapsed {
        padding-bottom: 0;
    }

    #coder-status {
        dock: bottom;
        height: 1;
        background: $surface-darken-2;
        padding: 0 1;
        color: $text-muted;
    }

    .coder-active {
        color: $success;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._messages: list[tuple[str, str]] = []  # (role, content)
        self._current_response_text = ""  # Track streaming response
        self._scope_context = ""  # Accumulated scope discussion
        self._explore_context = ""  # DeepSeek explore output for Planner
        self._fast_mode = False  # Current mode
        self._task_count = 0  # Current queue size
        self._planner_mode_state = PlannerModeState()  # Planner mode tracking
        self._index_status = ""  # Indexing coverage label

    def compose(self) -> ComposeResult:
        # Status bar with mode indicator
        with Horizontal(id="status-bar"):
            yield Static("⚡ Fast Mode", id="mode-indicator", classes="mode-fast")
            yield Static("Go Mode", id="planner-mode-indicator", classes="planner-go")
            yield Static(f"Tasks: 0 | Project: {Path.cwd().name}", id="status-info")
        
        with VerticalScroll(id="chat-container"):
            yield Static(
                "[bold yellow]Welcome![/]\n"
                "Chat with Planner to refine your project scope.\n\n"
                "[bold]Commands:[/]\n"
                "• /confirm - Approve scope, start implementation\n"
                "• /mode fast|local - Switch execution mode\n"
                "• /planner [go|plan [maintainable|discovery]] - Switch Planner mode\n"
                "• /run - Execute pending tasks\n"
                "• /auto <task> - Run autonomous Reason-Act-Observe loop\n"
                "• /explore-add <description> - Explore codebase and add to context\n"
                "• /undo - Undo last edit or file operation\n"
                "• /clear - Clear chat and context",
                id="welcome-msg"
            )
        yield Static("", id="coder-status")
        yield DataTable(id="task-queue")
        yield Input(placeholder="Describe your project to Planner, then /confirm when ready...", id="planner-input")

    def on_mount(self) -> None:
        """Set up the task queue table."""
        table = self.query_one("#task-queue", DataTable)
        table.add_columns("ID", "Status", "Description", "Priority")
        table.cursor_type = "row"
        table.zebra_stripes = True
    
    def set_welcome_mode(self, is_new_project: bool) -> None:
        """Switch between new-project and existing-project welcome messages."""
        welcome = self.query_one("#welcome-msg", Static)
        if is_new_project:
            welcome.update(
                "[bold yellow]Welcome![/] This directory is empty — let's build something.\n\n"
                "Describe what you want to create, and Planner will help you plan it.\n"
                "Planner is in [bold]Plan Mode (Maintainable)[/] by default for new projects.\n\n"
                "[bold]Commands:[/]\n"
                "  /planner plan discovery — for rapid prototyping\n"
                "  /planner go — to skip planning and start coding"
            )
        # For existing projects, the default welcome is already shown in compose()

    def set_mode(self, fast_mode: bool) -> None:
        """Update the mode indicator."""
        self._fast_mode = fast_mode
        indicator = self.query_one("#mode-indicator", Static)
        if fast_mode:
            indicator.update("⚡ Fast Mode")
            indicator.remove_class("mode-local")
            indicator.add_class("mode-fast")
        else:
            indicator.update("🏠 Local Mode")
            indicator.remove_class("mode-fast")
            indicator.add_class("mode-local")
    
    def update_status(self, task_count: int = None) -> None:
        """Update the status info."""
        if task_count is not None:
            self._task_count = task_count
        status = self.query_one("#status-info", Static)
        parts = [f"Tasks: {self._task_count}", f"Project: {Path.cwd().name}"]
        if self._index_status:
            parts.append(self._index_status)
        status.update(" | ".join(parts))

    def update_index_status(self, indexed: int, total: int) -> None:
        """Update the indexing coverage indicator."""
        if indexed < 0:
            self._index_status = "INDEX: N/A (install fastembed, qdrant-client)"
        elif total == 0:
            self._index_status = ""
        else:
            pct = int((indexed / total) * 100)
            self._index_status = f"INDEX: {indexed}/{total} ({pct}%)"
        self.update_status()

    def update_coder_status(self, text: str) -> None:
        """Update the coder status line.

        Pass an empty string to clear (idle state).
        """
        status = self.query_one("#coder-status", Static)
        status.update(text)
        if text:
            status.add_class("coder-active")
        else:
            status.remove_class("coder-active")

    @on(Input.Submitted, "#planner-input")
    async def on_planner_input(self, event: Input.Submitted) -> None:
        """Handle input to Planner."""
        prompt = event.value.strip()
        if not prompt:
            return

        event.input.clear()
        chat = self.query_one("#chat-container", VerticalScroll)

        # Handle /confirm command
        if prompt.startswith("/confirm"):
            await chat.mount(Static("[bold green]✓ Scope confirmed! Starting decomposition...[/]"))
            chat.scroll_end()
            self.post_message(ConfirmScope(scope_summary=self._scope_context))
            return
        
        # Handle /mode command
        if prompt.startswith("/mode"):
            parts = prompt.split(maxsplit=1)
            if len(parts) > 1:
                mode = parts[1].lower().strip()
                if mode == "fast":
                    self.set_mode(True)
                    await chat.mount(Static("[yellow]Switched to Fast Mode (API-based)[/]"))
                    self.post_message(ModeSwitch(fast_mode=True))
                elif mode == "local":
                    self.set_mode(False)
                    await chat.mount(Static("[yellow]Switched to Local Mode (Coder)[/]"))
                    self.post_message(ModeSwitch(fast_mode=False))
                else:
                    await chat.mount(Static("[red]Usage: /mode fast|local[/]"))
            else:
                mode_str = "Fast" if self._fast_mode else "Local"
                await chat.mount(Static(f"[yellow]Current mode: {mode_str}[/]"))
            chat.scroll_end()
            return
        
        # Handle /run command
        if prompt.startswith("/run"):
            await chat.mount(Static("[yellow]Executing pending tasks...[/]"))
            chat.scroll_end()
            self.post_message(ExecuteTasks(auto_run=True))
            return

        # Handle /auto command - autonomous Reason-Act-Observe loop
        if prompt.startswith("/auto"):
            parts = prompt.split(maxsplit=1)
            if len(parts) > 1:
                task_description = parts[1]
                await chat.mount(Static(
                    f"[bold magenta]Starting Autonomous Mode[/]\n"
                    f"Task: {task_description}\n"
                    f"[dim]Planner will reason, act, and observe until complete...[/]"
                ))
                chat.scroll_end()
                self.post_message(StartAutonomousLoop(
                    task=task_description,
                    context=self._scope_context if self._scope_context else None
                ))
            else:
                await chat.mount(Static(
                    "[red]Usage: /auto <task description>[/]\n"
                    "Example: /auto Create a simple Flask API with user authentication"
                ))
                chat.scroll_end()
            return

        # Handle /explore-add command — autonomous targeted exploration
        if prompt.startswith("/explore-add"):
            query = prompt[len("/explore-add"):].strip()
            if not query:
                await chat.mount(Static("[red]Usage: /explore-add <what to explore>[/]"))
                chat.scroll_end()
                return
            await chat.mount(Static(f"[yellow]Exploring: {query}...[/]"))
            chat.scroll_end()
            self.post_message(ExploreAndAddContext(query=query))
            return

        # Handle /planner command
        if prompt.startswith("/planner"):
            parts = prompt.split()
            if len(parts) == 1:
                # Show current mode
                display = self._planner_mode_state.get_display_string()
                await chat.mount(Static(f"[yellow]Current Planner mode: {display}[/]"))
            elif parts[1].lower() == "go":
                self._planner_mode_state.mode = PlannerMode.GO
                self.set_planner_mode(PlannerMode.GO)
                await chat.mount(Static("[green]Switched to Go Mode (action-first)[/]"))
                self.post_message(PlannerModeSwitch(mode="go"))
            elif parts[1].lower() == "plan":
                sub = parts[2].lower() if len(parts) > 2 else "maintainable"
                if sub == "discovery":
                    self._planner_mode_state.mode = PlannerMode.PLAN
                    self._planner_mode_state.plan_sub_mode = PlanSubMode.DISCOVERY
                    self.set_planner_mode(PlannerMode.PLAN, PlanSubMode.DISCOVERY)
                    await chat.mount(Static("[yellow]Switched to Plan Mode (Discovery) - ship fast, learn faster[/]"))
                    self.post_message(PlannerModeSwitch(mode="plan", sub_mode="discovery"))
                else:
                    self._planner_mode_state.mode = PlannerMode.PLAN
                    self._planner_mode_state.plan_sub_mode = PlanSubMode.MAINTAINABLE
                    self.set_planner_mode(PlannerMode.PLAN, PlanSubMode.MAINTAINABLE)
                    await chat.mount(Static("[#5599ff]Switched to Plan Mode (Maintainable) - build to last[/]"))
                    self.post_message(PlannerModeSwitch(mode="plan", sub_mode="maintainable"))
            else:
                await chat.mount(Static("[red]Usage: /planner [go|plan [maintainable|discovery]][/]"))
            chat.scroll_end()
            return

        # Handle /undo command
        if prompt.startswith("/undo"):
            await chat.mount(Static("[yellow]Checking undo stack...[/]"))
            chat.scroll_end()
            self.post_message(UndoRequest())
            return

        # Handle /clear command
        if prompt.startswith("/clear"):
            await chat.remove_children()
            self._scope_context = ""
            self._explore_context = ""
            self.post_message(ClearContext())  # Tell app to clear agent state
            await chat.mount(Static("[yellow]Chat and context cleared. Start fresh![/]"))
            return

        # Handle /queue command
        if prompt.startswith("/queue"):
            table = self.query_one("#task-queue", DataTable)
            count = table.row_count
            await chat.mount(Static(f"[yellow]Task queue: {count} tasks[/]"))
            chat.scroll_end()
            return

        # Regular message to Planner - accumulate for scope context
        await chat.mount(Static(f"[bold blue]You:[/] {prompt}"))
        chat.scroll_end()
        
        # Add to scope context
        self._scope_context += f"\nUser: {prompt}"

        # Always chat — decomposition only happens via /confirm
        request_type = "chat"

        self.post_message(PlannerRequest(prompt, request_type=request_type))

    def update_task_queue(self, tasks: list[dict]) -> None:
        """Update the task queue display."""
        table = self.query_one("#task-queue", DataTable)
        table.clear()
        for task in tasks:
            status_emoji = {"pending": "⏳", "in_progress": "🔄", "completed": "✅", "failed": "❌"}.get(task["status"], "❓")
            table.add_row(
                task["id"],
                f"{status_emoji} {task['status']}",
                task["description"][:40],
                str(task["priority"]),
            )
        self.update_status(task_count=len(tasks))

    @on(PlannerResponse)
    def on_planner_response(self, message: PlannerResponse) -> None:
        """Handle streaming chunks from Planner without tight coupling."""
        import asyncio
        if message.is_start:
            asyncio.create_task(self.add_planner_response_chunk("", is_start=True))
        elif message.is_complete:
            self.finish_planner_response()
        else:
            if message.chunk:
                asyncio.create_task(self.add_planner_response_chunk(message.chunk))

    @on(CoderResponse)
    def on_coder_response(self, message: CoderResponse) -> None:
        """Sync Coder status text to Task Manager seamlessly."""
        if getattr(message, "status", None):
            self.update_coder_status(message.status)

    async def add_planner_response_chunk(self, chunk: str, is_start: bool = False) -> None:
        """Add a streaming chunk from Planner."""
        chat = self.query_one("#chat-container", VerticalScroll)

        if is_start:
            # Remove any existing current-response widget first
            try:
                old = self.query_one("#current-response", Static)
                await old.remove()
            except Exception:
                pass
            # Create new response widget
            self._current_response_text = "[bold green]Planner:[/] "
            await chat.mount(Static(self._current_response_text, id="current-response"))
        else:
            # Append to existing response
            self._current_response_text = getattr(self, '_current_response_text', '') + chunk
            try:
                current = self.query_one("#current-response", Static)
                current.update(self._current_response_text)
            except Exception:
                pass

        chat.scroll_end()

    def finish_planner_response(self, response_text: str = "") -> None:
        """Finalize Planner's response."""
        # Add to scope context
        if response_text:
            self._scope_context += f"\nPlanner: {response_text[:500]}"
        
        # Clear the tracking variable
        self._current_response_text = ""
        # Remove the ID so we can create a new one next time
        try:
            current = self.query_one("#current-response", Static)
            current.id = None
        except Exception:
            pass

    async def show_explore_result(self, content: str) -> None:
        """Display DeepSeek explore result in a collapsible section."""
        chat = self.query_one("#chat-container", VerticalScroll)

        line_count = content.count('\n') + 1
        title = f"DeepSeek Report ({line_count} lines)"

        collapsible = Collapsible(
            Static(f"[bold magenta]DeepSeek Report:[/]\n{content}"),
            title=title,
            collapsed=True,
        )
        await chat.mount(collapsible)
        chat.scroll_end()
    
    async def show_system_message(self, message: str, style: str = "yellow") -> None:
        """Display a system message in the chat.

        Short messages (<5 lines) are shown inline.
        Longer messages are wrapped in a Collapsible widget (collapsed by default).
        """
        chat = self.query_one("#chat-container", VerticalScroll)

        line_count = message.count('\n') + 1
        if line_count < 5:
            # Short messages show inline
            await chat.mount(Static(f"[{style}]{message}[/]"))
        else:
            # Long messages wrapped in collapsible
            first_line = message.split('\n')[0]
            # Truncate title if too long
            title = first_line[:60] + "..." if len(first_line) > 60 else first_line
            title = f"{title} ({line_count} lines)"

            collapsible = Collapsible(
                Static(f"[{style}]{message}[/]"),
                title=title,
                collapsed=True,
            )
            await chat.mount(collapsible)

        chat.scroll_end()
    
    def set_explore_context(self, content: str) -> None:
        """Store explore result for Planner context."""
        self._explore_context = content
        self._scope_context += f"\n\n[Codebase Analysis]\n{content[:2000]}"
    
    def get_explore_context(self) -> str:
        """Get the stored explore context."""
        return self._explore_context
    
    def set_planner_mode(self, mode: PlannerMode, sub_mode: PlanSubMode = PlanSubMode.MAINTAINABLE) -> None:
        """Update the Planner mode indicator."""
        indicator = self.query_one("#planner-mode-indicator", Static)
        if mode == PlannerMode.GO:
            indicator.update("Go Mode")
            indicator.remove_class("planner-plan", "planner-discovery")
            indicator.add_class("planner-go")
        elif sub_mode == PlanSubMode.DISCOVERY:
            indicator.update("Plan (Discovery)")
            indicator.remove_class("planner-go", "planner-plan")
            indicator.add_class("planner-discovery")
        else:
            indicator.update("Plan (Maintainable)")
            indicator.remove_class("planner-go", "planner-discovery")
            indicator.add_class("planner-plan")

    async def show_token_usage(self, prompt_tokens: int, completion_tokens: int, total_tokens: int) -> None:
        """Show token usage below the last response."""
        chat = self.query_one("#chat-container", VerticalScroll)
        await chat.mount(Static(
            f"[dim]tokens: {prompt_tokens} in / {completion_tokens} out ({total_tokens} total)[/dim]"
        ))
        chat.scroll_end()

    async def show_explore_and_add_result(self, content: str) -> None:
        """Display explore result AND add it to Planner context."""
        # Store for Planner
        self.set_explore_context(content)
        # Display in chat with collapsible
        chat = self.query_one("#chat-container", VerticalScroll)

        line_count = content.count('\n') + 1
        title = f"DeepSeek Report - added to Planner context ({line_count} lines)"

        collapsible = Collapsible(
            Static(f"[bold magenta]DeepSeek Report:[/]\n{content}"),
            title=title,
            collapsed=True,
        )
        await chat.mount(collapsible)
        await chat.mount(Static("[dim]Planner now knows the current codebase structure.[/]"))
        chat.scroll_end()
