"""
Main Multi-Agent TUI Application.

Provides a two-tab interface for orchestrating:
- Coder3-Coder (local, code generation)
- Planner-K2 (API, planning and editing)
- DeepSeek V3 (API, exploration)
"""

import logging
import os
from pathlib import Path
from threading import Event, Lock
from typing import Optional

from .screens.settings_trace import SettingsTracePane
# ... other imports remain exactly the same ...
# but we need to inject the setup_global_logging call. I'll do it safely since 
# the rest of the file has many imports.

logger = logging.getLogger("noether.app")

from ..observability.log_handler import setup_global_logging
setup_global_logging()

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, TabbedContent, TabPane, Static, ProgressBar
from textual.containers import Container, Center, Middle, Horizontal
from textual.binding import Binding
from textual import work, on

from .screens.coder_view import CoderViewPane
from .screens.task_manager import TaskManagerPane
from .screens.settings_trace import SettingsTracePane
from .widgets.sidebar import RightSidebar, SidebarResizer
from .modals.approval_modal import CommandApprovalModal, FileWriteApprovalModal, BatchFileApprovalModal, ExploreCommandApprovalModal
from .modals.undo_modal import UndoConfirmationModal
from .messages import (
    CoderRequest,
    CoderResponse,
    PlannerRequest,
    PlannerResponse,
    TaskQueueUpdated,
    EditRequest,
    SearchReplaceRequest,
    EditFeedbackMessage,
    ModeSwitch,
    ConfirmScope,
    ExecuteTasks,
    ExploreAndAddContext,
    StartAutonomousLoop,
    AutonomousLoopUpdate,
    PlannerModeSwitch,
    TokenUsageUpdate,
    UndoRequest,
    UndoComplete,
    ProviderChanged,
    PendingFileOperations,
    ProcessDecomposition,
    ScheduleCommandApproval,
    ClearContext,
)

# Import backends (will be initialized lazily)
from ..backends.openai_backend import OpenAICompatibleBackend
from ..backends.base import LLMBackend
from ..orchestration import TaskOrchestrator, CodeEditor, FileOperationParser, FileOperationExecutor, SearchReplaceOperation, UnifiedUndoStack, SnapshotUndoStack
from ..orchestration.pipeline import OperationPipeline
from ..sandbox import SandboxCommandExecutor, FileSystemSandbox
from ..agents import PlannerAgent, CoderAgent, ExploreAgent
from ..modes.planner_modes import PlannerMode, PlanSubMode, PlannerModeState
from ..config.settings import NoetherSettings


class MultiAgentApp(App):
    """
    Multi-agent orchestration TUI with two main views.

    Tab 1 (Coder View): Real-time code output with line numbers
    Tab 2 (Task Manager): Chat with Planner + task queue
    """

    TITLE = "Noether"
    SUB_TITLE = "Coder + Planner + DeepSeek"

    BINDINGS = [
        Binding("ctrl+1", "show_coder", "Code"),
        Binding("ctrl+2", "show_tasks", "Chat"),
        Binding("ctrl+e", "explore", "Explore"),
        Binding("ctrl+b", "toggle_sidebar", "Panel"),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    CSS = """
    TabbedContent {
        height: 1fr;
    }

    TabPane {
        padding: 0;
        height: 1fr;
    }

    ContentSwitcher {
        height: 1fr;
    }

    #loading-screen {
        width: 100%;
        height: 100%;
        align: center middle;
        background: $surface;
    }

    #loading-screen.hidden {
        display: none;
    }

    #loading-content {
        width: 50;
        height: auto;
        padding: 1 3;
        border: round $primary;
        background: $panel;
    }

    #loading-title {
        text-align: center;
        text-style: bold;
        color: $text;
    }

    #loading-separator {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }

    #loading-progress {
        margin: 1 0;
    }

    #loading-status {
        text-align: center;
        color: $text-muted;
    }

    #loading-hint {
        text-align: center;
        color: $text-disabled;
        margin-top: 1;
    }

    #body-container.hidden {
        display: none;
    }
    """

    def __init__(
        self,
        coder_model_path: Optional[str] = None,
        settings: Optional[NoetherSettings] = None,
        fast_mode: bool = False,
    ):
        """
        Initialize the multi-agent app.

        Args:
            coder_model_path: Path to Coder GGUF model (for local mode)
            settings: Configuration including provider, API keys, and models
            fast_mode: If True, use fast coder backend instead of local Coder
        """
        super().__init__()

        # Mode configuration
        self.fast_mode = fast_mode

        # Model paths and keys
        self.coder_model_path = coder_model_path or os.environ.get(
            "CODER_MODEL_PATH",
            "./models/Coder3-Coder-30B-A3B-Instruct-Q4_K_M.gguf"
        )
        self.settings = settings or NoetherSettings()

        # Backends (initialized lazily)
        self._coder_backend: Optional[LLMBackend] = None
        self._planner_backend: Optional[OpenAICompatibleBackend] = None
        self._deepseek_backend: Optional[OpenAICompatibleBackend] = None
        self._fast_coder_backend: Optional[OpenAICompatibleBackend] = None  # For fast mode

        # Task orchestrator (callbacks wired during AgentDispatcher mount)
        self._orchestrator = TaskOrchestrator()

        # Sandbox (initialized in on_mount)

        self._filesystem_sandbox: Optional[FileSystemSandbox] = None

        # Agents (initialized after backends)
        self._planner_agent: Optional[PlannerAgent] = None
        self._coder_agent: Optional[CoderAgent] = None
        self._explore_agent: Optional[ExploreAgent] = None

        # Code editor (initialized in on_mount)
        self._code_editor: Optional[CodeEditor] = None

        # Event loop reference (set in on_mount for worker thread access)
        self._loop = None

        # File operations (initialized in on_mount)
        self._file_op_parser: Optional[FileOperationParser] = None
        self._file_op_executor: Optional[FileOperationExecutor] = None

        # State
        # Thread-safe cache of Coder output content
        # This is the source of truth for content - view mirrors it
        self._coder_content = "# Coder Code Output\n# Type a prompt below to generate code\n"

        # Undo management extracted to UndoManager
        self._undo_stack = UnifiedUndoStack()
        self._snapshot_undo_stack = SnapshotUndoStack()

        # Pipeline (initialized after sandbox in on_mount)
        self._pipeline: Optional[OperationPipeline] = None

        # Pending file operations (for batch approval)
        self._pending_file_ops: list = []
        self._pending_task_id: Optional[str] = None

        # Pending explore commands (for explore approval)
        self._pending_explore_commands: list[tuple[str, str]] = []
        self._pending_explore_type: str = ""
        self._pending_explore_query: Optional[str] = None
        self._pending_explore_add: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        # Loading screen
        with Center(id="loading-screen"):
            with Middle():
                with Container(id="loading-content"):
                    yield Static("Noether v0.1.0", id="loading-title")
                    yield Static("─" * 44, id="loading-separator")
                    yield ProgressBar(total=100, show_eta=False, id="loading-progress")
                    yield Static("Initializing...", id="loading-status")
                    yield Static("Press Ctrl+Q to quit", id="loading-hint")
        # Main content (hidden initially)
        initial_tab = "task-manager" if self.fast_mode else "coder-view"
        with Horizontal(id="body-container", classes="hidden"):
            with Container(id="main-content"):
                with TabbedContent(initial=initial_tab):
                    with TabPane("Coder View", id="coder-view"):
                        yield CoderViewPane()
                    with TabPane("Task Manager", id="task-manager"):
                        yield TaskManagerPane()
                    with TabPane("Settings & Trace", id="settings-trace"):
                        yield SettingsTracePane(self.settings)
            
            # Sidebar and drag resizer
            yield SidebarResizer(id="sidebar-resizer")
            yield RightSidebar(id="right-sidebar")
        
        yield Footer()

    def _update_loading_status(self, message: str, progress: int = 0) -> None:
        """Update the loading screen status message and progress bar."""
        try:
            status = self.query_one("#loading-status", Static)
            status.update(message)
            if progress > 0:
                bar = self.query_one("#loading-progress", ProgressBar)
                bar.progress = progress
        except Exception as e:
            self.log.warning(f"Could not update loading status: {e}")

    def _finish_loading(self) -> None:
        """Hide loading screen and show main content."""
        try:
            self.query_one("#loading-screen").add_class("hidden")
            self.query_one("#body-container").remove_class("hidden")
        except Exception as e:
            self.log.warning(f"Could not finish loading transition: {e}")

    async def on_mount(self) -> None:
        """Initialize backends when app mounts."""
        import asyncio
        # Store reference to Textual's event loop for use in worker threads
        self._loop = asyncio.get_running_loop()

        self._update_loading_status("Setting up project...", 10)

        # Initialize sandbox — cwd IS the project root
        sandbox_root = Path.cwd()

        self._filesystem_sandbox = FileSystemSandbox(
            project_root=str(sandbox_root),
            approval_callback=self._request_file_approval,
        )

        self._update_loading_status("Initializing code editor...", 30)

        # Initialize code editor
        # NOTE: We use thread-safe wrappers for get/set because apply_edit runs
        # in a worker thread with its own event loop, and direct widget access
        # from non-main threads causes crashes.
        self._code_editor = CodeEditor(
            get_coder_output=self._get_coder_output_safe,
            set_coder_output=self._set_coder_output_safe,
            file_read=self._filesystem_sandbox.safe_read,
            file_write=self._filesystem_sandbox.safe_write,
            approval_callback=self._request_edit_approval,
        )
        
        # Initialize file operation parser and executor
        self._file_op_parser = FileOperationParser(sandbox_root=sandbox_root)
        self._file_op_executor = FileOperationExecutor(
            sandbox=self._filesystem_sandbox,
            auto_approve=True,
        )

        # Initialize extracted worker modules
        from .coder_worker import CoderWorker
        from .edit_handler import EditHandler

        self._coder_worker = CoderWorker(
            orchestrator=self._orchestrator,
            undo_stack=self._undo_stack,
            file_op_parser=self._file_op_parser,
        )
        self._edit_handler = EditHandler(
            code_editor=self._code_editor,
            undo_stack=self._undo_stack,
            sandbox=self._filesystem_sandbox,
        )

        # Initialize local Coder only if not in fast mode
        if not self.fast_mode:
            self._update_loading_status("Loading Coder model...", 50)
            self._init_coder()
        else:
            self._update_loading_status("Fast mode — skipping local model...", 50)
            # Disable Coder view input (no local model)
            coder_view = self.query_one(CoderViewPane)
            coder_view.set_local_model_unavailable()

        # Initialize API backends if key is available
        if self.settings.active_api_key:
            self._update_loading_status(f"Connecting to {self.settings.provider.title()}...", 70)
            try:
                self._planner_backend = OpenAICompatibleBackend.create(
                    provider=self.settings.provider,
                    role="chat",
                    api_key=self.settings.active_api_key,
                    model_id=self.settings.get_model("chat"),
                )
                self._deepseek_backend = OpenAICompatibleBackend.create(
                    provider=self.settings.provider,
                    role="explorer",
                    api_key=self.settings.active_api_key,
                    model_id=self.settings.get_model("explorer"),
                )

                # Initialize fast coder if in fast mode
                if self.fast_mode:
                    self._update_loading_status("Initializing fast coder...", 80)
                    self._fast_coder_backend = OpenAICompatibleBackend.create(
                        provider=self.settings.provider,
                        role="coder",
                        api_key=self.settings.active_api_key,
                        model_id=self.settings.get_model("coder"),
                    )

                # Initialize agents
                self._planner_agent = PlannerAgent(
                    backend=self._planner_backend,
                    orchestrator=self._orchestrator,
                    on_edit_request=self._handle_edit_request,
                    on_log_activity=self._handle_log_activity,
                )
                self._explore_agent = ExploreAgent(
                    backend=self._deepseek_backend,
                    sandbox=self._filesystem_sandbox,
                    project_root=str(Path.cwd()),
                )

                # Wire extracted modules with agents
                from .planner_worker import PlannerWorker
                self._planner_worker = PlannerWorker(
                    planner_agent=self._planner_agent,
                    orchestrator=self._orchestrator,
                    edit_handler=self._edit_handler,
                )
                self._edit_handler.planner_agent = self._planner_agent
                self._edit_handler.set_ui_callbacks(
                    push_screen_fn=lambda modal, cb: self.call_from_thread(self.push_screen, modal, cb),
                    notify_fn=lambda msg, severity=None: self._safe_call(
                        self.notify, msg, severity=severity
                    ) if severity else self._safe_call(self.notify, msg),
                    post_message_fn=lambda msg: self._safe_call(self.post_message, msg),
                )

                mode_str = "Fast mode" if self.fast_mode else "Local mode"
                self._update_loading_status(f"Ready! ({mode_str})", 100)
            except Exception as e:
                self._update_loading_status(f"API error: {e}")
        else:
            self._update_loading_status("No API key - Planner/DeepSeek unavailable")
            # Schedule persistent message so user knows why LLMs won't respond
            self.call_later(
                self._schedule_system_message,
                "⚠️ No API key configured. Use Settings (Ctrl+S) to add your Fireworks or OpenRouter API key.",
                "red"
            )

        # Initialize the unified operation pipeline
        from ..sandbox.command_executor import SandboxCommandExecutor
        _sandbox_executor = SandboxCommandExecutor(project_root=Path.cwd())
        self._pipeline = OperationPipeline(
            sandbox_executor=_sandbox_executor,
            filesystem_sandbox=self._filesystem_sandbox,
            undo_stack=self._snapshot_undo_stack,
            push_screen_fn=lambda modal, cb: self.call_from_thread(self.push_screen, modal, cb),
            notify_fn=lambda msg, severity=None: self._safe_call(
                self.notify, msg, severity=severity
            ) if severity else self._safe_call(self.notify, msg),
            on_context_update=self._on_pipeline_context_update,
        )

        # Wire pipeline into planner agent
        if self._planner_agent:
            self._planner_agent.set_pipeline(self._pipeline)

        # Mount the agent dispatcher to handle threaded orchestration independently
        from ..orchestration.dispatcher import AgentDispatcher
        self.dispatcher = AgentDispatcher(
            coder_worker=self._coder_worker,
            planner_worker=getattr(self, '_planner_worker', None),
            fast_mode=self.fast_mode,
            coder_backend=getattr(self, '_coder_backend', None),
            fast_coder_backend=getattr(self, '_fast_coder_backend', None),
            planner_backend=getattr(self, '_planner_backend', None),
            edit_handler=getattr(self, '_edit_handler', None),
            sandbox=self._filesystem_sandbox,
            coder_content=self._coder_content,
            explore_agent=getattr(self, '_explore_agent', None),
            deepseek_backend=getattr(self, '_deepseek_backend', None),
            orchestrator=getattr(self, '_orchestrator', None),
            pipeline=self._pipeline,
        )
        self.mount(self.dispatcher)

        # Mount Undo Manager
        from ..orchestration.undo_manager import UndoManager
        self.undo_manager = UndoManager(
            undo_stack=self._undo_stack,
            code_editor=getattr(self, '_code_editor', None),
            filesystem_sandbox=self._filesystem_sandbox,
            execute_edit_fn=getattr(self, '_execute_edit', None)
        )
        self.mount(self.undo_manager)

        # Mount Autonomous Worker
        from ..orchestration.autonomous_worker import AutonomousWorker
        self.autonomous_worker = AutonomousWorker(
            planner_agent=getattr(self, '_planner_agent', None),
            schedule_system_message_fn=getattr(self, '_schedule_system_message', None),
            pipeline=self._pipeline,
        )
        self.mount(self.autonomous_worker)

        # Show main content
        self._finish_loading()

        # Detect new project (empty directory)
        cwd = Path.cwd()
        is_new_project = not any(
            f for f in cwd.iterdir()
            if not f.name.startswith(".")
        )
        if is_new_project:
            # Switch to Task Manager tab and set Plan Mode
            self.query_one(TabbedContent).active = "task-manager"
            task_manager = self.query_one(TaskManagerPane)
            task_manager.set_welcome_mode(is_new_project=True)
            if self._planner_agent:
                self._planner_agent.switch_mode(PlannerMode.PLAN, sub_mode=PlanSubMode.MAINTAINABLE)

        # Check SRT sandbox availability
        if self._planner_agent and not self._planner_agent.sandbox_executor.is_available:
            self.notify(
                "Sandbox not configured. Run 'noether setup-sandbox' to enable command isolation.",
                severity="warning"
            )

        # Safety: warn user that this tool operates on real files
        cwd = Path.cwd()
        if (cwd / ".git").is_dir():
            self.notify(f"Project: {cwd.name} (git repo detected)")
        else:
            self.notify(
                f"Operating on {cwd}. No git repo — consider backups.",
                severity="warning",
            )

        # Show status notifications
        if self.settings.active_api_key and self._planner_backend:
            self.notify(f"{self.settings.provider.title()} backends ready")
        else:
            self.notify(
                "No API key — Planner/DeepSeek unavailable",
                severity="warning"
            )

        # Compute index coverage for status bar
        self._update_index_coverage()

    @work(thread=True)
    def _init_coder(self) -> None:
        """Initialize Coder backend in background thread."""
        from ..backends import LocalBackend
        if LocalBackend is None:
            self.call_from_thread(
                self.notify,
                "Local mode requires llama-cpp-python: pip install noether[local]",
                severity="error",
            )
            return
        try:
            self._coder_backend = LocalBackend(
                model_path=self.coder_model_path
            )
            # Update dispatcher ref
            if hasattr(self, 'dispatcher'):
                self.dispatcher.coder_backend = self._coder_backend
                
            self.call_from_thread(
                self.notify, "Coder loaded successfully"
            )
        except FileNotFoundError as e:
            self.call_from_thread(
                self.notify, f"Coder model not found: {e}", severity="error"
            )
        except Exception as e:
            self.call_from_thread(
                self.notify, f"Coder init failed: {e}", severity="error"
            )

    # --- Actions ---

    def action_show_coder(self) -> None:
        """Switch to Coder view tab."""
        self.query_one(TabbedContent).active = "coder-view"

    def action_show_tasks(self) -> None:
        """Switch to Task Manager tab."""
        self.query_one(TabbedContent).active = "task-manager"

    def action_explore(self) -> None:
        """Trigger DeepSeek explore (architecture by default)."""
        self.post_message(DeepSeekExploreRequest(explore_type="architecture"))

    def action_toggle_sidebar(self) -> None:
        """Toggle right sidebar."""
        try:
            sidebar = self.query_one(RightSidebar)
            sidebar.toggle()
        except Exception as e:
            self.log.warning(f"Could not toggle sidebar: {e}")

    def action_clear(self) -> None:
        """Clear current view."""
        active = self.query_one(TabbedContent).active
        if active == "coder-view":
            self._clear_coder_output()
        # Task manager clear is handled via /clear command

    # --- Message Handlers ---

    # --- Message Routing ---
    # Textual messages bubble UP only (child → parent), never laterally.
    # AgentDispatcher, AutonomousWorker, and UndoManager are App-level siblings
    # of the TabPanes containing TaskManagerPane and CoderViewPane.
    # The App must intercept bubbled messages and forward them to the correct target.

    @on(PlannerRequest)
    def _route_planner_request(self, message: PlannerRequest) -> None:
        """Forward PlannerRequest from TaskManagerPane → AgentDispatcher."""
        if message.is_forwarded:
            return
        message._set_forwarded()
        if hasattr(self, 'dispatcher'):
            self.dispatcher.post_message(message)

    @on(CoderRequest)
    def _route_coder_request(self, message: CoderRequest) -> None:
        """Forward CoderRequest from CoderViewPane → AgentDispatcher."""
        if message.is_forwarded:
            return
        message._set_forwarded()
        if hasattr(self, 'dispatcher'):
            self.dispatcher.post_message(message)

    @on(PlannerResponse)
    def _route_planner_response(self, message: PlannerResponse) -> None:
        """Forward PlannerResponse from AgentDispatcher → TaskManagerPane."""
        if message.is_forwarded:
            return  # Already forwarded, don't re-route
        message._set_forwarded()
        try:
            task_mgr = self.query_one(TaskManagerPane)
            task_mgr.post_message(message)
        except Exception:
            pass

    @on(CoderResponse)
    def _route_coder_response(self, message: CoderResponse) -> None:
        """Forward CoderResponse from AgentDispatcher → CoderViewPane + TaskManagerPane."""
        if message.is_forwarded:
            return  # Already forwarded, don't re-route
        message._set_forwarded()
        try:
            coder_view = self.query_one(CoderViewPane)
            coder_view.post_message(message)
        except Exception:
            pass
        try:
            task_mgr = self.query_one(TaskManagerPane)
            task_mgr.post_message(message)
        except Exception:
            pass

    @on(StartAutonomousLoop)
    def _route_start_autonomous(self, message: StartAutonomousLoop) -> None:
        """Forward StartAutonomousLoop from TaskManagerPane → AutonomousWorker."""
        if message.is_forwarded:
            return
        message._set_forwarded()
        if hasattr(self, 'autonomous_worker'):
            self.autonomous_worker.post_message(message)

    @on(ExploreAndAddContext)
    def _route_explore_add(self, message: ExploreAndAddContext) -> None:
        """Forward ExploreAndAddContext from TaskManagerPane → AutonomousWorker."""
        if message.is_forwarded:
            return
        message._set_forwarded()
        if hasattr(self, 'autonomous_worker'):
            self.autonomous_worker.post_message(message)

    @on(UndoRequest)
    def _route_undo_request(self, message: UndoRequest) -> None:
        """Forward UndoRequest from TaskManagerPane → UndoManager."""
        if message.is_forwarded:
            return
        message._set_forwarded()
        if hasattr(self, 'undo_manager'):
            self.undo_manager.post_message(message)

    # --- Pipeline Context Update ---

    def _on_pipeline_context_update(self, request) -> None:
        """Called by OperationPipeline after successful file operations.

        Swaps full file content to skim in Planner's context.
        """
        if request.target_file and self._edit_handler:
            self._edit_handler._swap_to_skim_context(request.target_file)

    # --- Delegate Edits to EditHandler ---

    @on(PendingFileOperations)
    def on_pending_file_ops(self, message: PendingFileOperations) -> None:
        self._pending_file_ops = message.file_ops
        self._pending_task_id = message.task_id
        self._schedule_file_approval(message.file_ops)

    @on(ProcessDecomposition)
    def on_process_decomposition(self, message: ProcessDecomposition) -> None:
        self._process_decomposition(message.response, message.prompt)

    @on(ScheduleCommandApproval)
    def on_schedule_command_approval(self, message: ScheduleCommandApproval) -> None:
        self._schedule_command_approval(message.command)

    def _execute_edit(self, operation: SearchReplaceOperation, push_undo: bool = True) -> bool:
        """Delegate to EditHandler. See edit_handler.py for full logic."""
        return self._edit_handler.execute_edit(operation, push_undo)

    @staticmethod
    def _format_match_info(result) -> str:
        """Delegate to EditHandler."""
        from .edit_handler import EditHandler
        return EditHandler.format_match_info(result)

    def _swap_to_skim_context(self, target: str) -> None:
        """Delegate to EditHandler."""
        self._edit_handler._swap_to_skim_context(target)

    def _apply_edit_blocks(self, response: str) -> None:
        """Delegate to EditHandler."""
        self._edit_handler.apply_edit_blocks(response)

    @work(thread=True)
    def _execute_edit_from_message(self, operation: SearchReplaceOperation) -> None:
        """Thin wrapper for message handlers that need to trigger an edit via a new worker."""
        self._edit_handler.execute_edit(operation)

    def _schedule_command_approval(self, command: str) -> None:
        """Schedule command approval modal on main thread."""
        self._pending_exec_command = command
        safe_cmd = command.replace("[", "\\[").replace("]", "\\]")
        self.notify(f"Approval needed: {safe_cmd[:50]}")

        # Use push_screen with callback (not await) since we're on main thread
        self.push_screen(
            CommandApprovalModal(
                command=command,
                description="Planner wants to execute this command.",
                risk_level="review" if command.startswith(("ls", "cat", "grep", "find")) else "approval"
            ),
            callback=self._on_command_modal_dismissed,
        )

    def _on_command_modal_dismissed(self, approved: bool) -> None:
        """Callback when command approval modal is dismissed."""
        command = getattr(self, "_pending_exec_command", None)
        if not command:
            return

        # Escape brackets for safe display
        safe_cmd = command.replace("[", "\\[").replace("]", "\\]")

        if approved:
            self.notify(f"Executing: {safe_cmd[:50]}")
            # Show in chat and run command
            self._schedule_system_message(f"Executing: {safe_cmd}", "yellow")
            self._run_approved_command(command)
        else:
            self.notify("Command execution denied", severity="warning")
            self._schedule_system_message(f"User denied command: {safe_cmd}", "red")
            # Feed denial back to Planner's memory
            if self._planner_agent:
                self._planner_agent.memory.record_system_event(
                    f"Command denied by user: {command}"
                )

    def _schedule_file_approval(self, file_ops: list) -> None:
        """Schedule batch file approval modal on main thread."""
        self.notify(f"Approval needed for {len(file_ops)} file(s)")

        self.push_screen(
            BatchFileApprovalModal(file_operations=file_ops),
            callback=self._on_file_approval_dismissed,
        )

    def _on_file_approval_dismissed(self, approved: bool) -> None:
        """Callback when file approval modal is dismissed."""
        file_ops = self._pending_file_ops
        task_id = self._pending_task_id

        if not file_ops:
            return

        if approved:
            self.notify(f"Saving {len(file_ops)} file(s)...")
            self._run_approved_file_ops(file_ops, task_id)
        else:
            self.notify("File save cancelled", severity="warning")
            # Clear pending and fail the task
            self._pending_file_ops = []
            self._pending_task_id = None
            # Clear coder status
            try:
                task_mgr = self.query_one(TaskManagerPane)
                task_mgr.update_coder_status("")
            except Exception:
                pass
            if task_id:
                self._orchestrator.fail_current_task("User cancelled file save")

    @work(thread=True)
    def _run_approved_file_ops(self, file_ops: list, task_id: Optional[str]) -> None:
        """Execute approved file operations in background thread.

        Delegates to CoderWorker for the actual file writes and undo recording.
        Keeps UI coordination and task chaining here.
        """
        try:
            # Delegate file writes to CoderWorker
            success_count, fail_count = self._coder_worker.execute_file_ops_sync(
                file_ops, self._filesystem_sandbox
            )

            # Report results
            if success_count > 0:
                self._safe_call(
                    self.notify,
                    f"Created {success_count} file(s) in project directory"
                )
            if fail_count > 0:
                self._safe_call(
                    self.notify,
                    f"Failed to create {fail_count} file(s)",
                    severity="warning"
                )

            # Update UI status
            try:
                coder_view = self.call_from_thread(self.query_one, CoderViewPane)
                self.call_from_thread(coder_view.set_status, "Done - files saved")
            except Exception as e:
                self.log.warning(f"Could not update coder view status: {e}")

            # Complete task if this was from the queue and chain to next
            if task_id:
                result_summary = f"Created {success_count} file(s)"
                self._orchestrator.complete_current_task(result_summary)
                self._safe_call(self._start_next_task)

            # Clear coder status
            try:
                task_mgr = self.call_from_thread(self.query_one, TaskManagerPane)
                self.call_from_thread(task_mgr.update_coder_status, "")
            except Exception:
                pass

            # Clear pending
            self._pending_file_ops = []
            self._pending_task_id = None

        except Exception as e:
            self._safe_call(
                self.notify,
                f"File operation error: {e}",
                severity="error"
            )
            if task_id:
                self._orchestrator.fail_current_task(str(e))

    @work(thread=True)
    def _run_approved_command(self, command: str) -> None:
        """Run an approved command via Planner's executor, then ask Planner to analyze results."""
        import asyncio
        try:
            if not self._planner_agent:
                self.call_from_thread(
                    self.notify, "Planner agent not available", severity="error"
                )
                return

            # Run sync executor (no event loop needed)
            result = self._planner_agent.sandbox_executor.execute_sync(command)

            # Format output
            output = f"$ {command}\n"
            stdout = result.get('stdout', '')
            stderr = result.get('stderr', '')
            if stdout:
                output += stdout
            if stderr:
                output += f"\n[stderr]\n{stderr}"

            # Determine success
            success = result.get('success', False)
            returncode = result.get('returncode', -1)

            # Show command output in chat
            style = "green" if success else "red"
            self.call_from_thread(
                self._schedule_system_message,
                output,
                style
            )

            # Feed result back to Planner's memory
            memory_entry = f"Command executed: {command}\nReturn code: {returncode}\nOutput:\n{stdout[:1500]}"
            self._planner_agent.memory.record_system_event(memory_entry)

            # Now ask Planner to analyze the results and continue the conversation
            if success and stdout:
                self._ask_planner_to_analyze_results_sync(command, stdout)

        except Exception as e:
            self.call_from_thread(
                self.notify, f"Execution failed: {e}", severity="error"
            )
            # Persist error in chat
            self.call_from_thread(
                self._schedule_system_message,
                f"⚠️ Command execution failed: {e}",
                "red"
            )
            # Also log to memory
            if self._planner_agent:
                self._planner_agent.memory.record_system_event(
                    f"Command failed: {command}\nError: {str(e)}"
                )

    def _ask_planner_to_analyze_results_sync(self, command: str, output: str) -> None:
        """Ask Planner to analyze command results (sync version for thread workers)."""
        task_manager = self.call_from_thread(self.query_one, TaskManagerPane)

        # Truncate output if too long
        if len(output) > 2000:
            output = output[:2000] + "\n...[truncated]"

        # Create a reflection prompt that encourages follow-through
        reflection_prompt = f"""I just ran this command for you:
$ {command}

Output:
{output}

Continue with your plan. If you were going to make code changes based on this output, produce your Search/Replace blocks NOW. If you need to run another command, output it. If done, provide a brief summary."""

        # Signal start of Planner's analysis
        self.call_from_thread(
            task_manager.add_planner_response_chunk, "", is_start=True
        )

        try:
            full_response = ""
            # Use Planner to analyze (this uses the chat method which includes memory context)
            for chunk in self._planner_agent.chat(reflection_prompt, include_context=True):
                if isinstance(chunk, str):
                    full_response += chunk
                    self.call_from_thread(
                        task_manager.add_planner_response_chunk, chunk
                    )

            self.call_from_thread(task_manager.finish_planner_response)

            # Check if Planner wants to run more commands
            self._check_for_commands(full_response)

            # Check if Planner produced Search/Replace blocks in the follow-up
            self._apply_edit_blocks(full_response)

        except Exception as e:
            self.call_from_thread(
                self.notify, f"Analysis failed: {e}", severity="error"
            )

    def _schedule_system_message(self, message: str, style: str) -> None:
        """Schedule async system message display."""
        self.call_later(self._async_show_system_message, message, style)

    async def _async_show_system_message(self, message: str, style: str) -> None:
        """Async handler to show system message in task manager."""
        try:
            task_manager = self.query_one(TaskManagerPane)
            await task_manager.show_system_message(message, style)
        except Exception as e:
            self.notify(f"Failed to show message: {e}", severity="warning")

    def _schedule_explore_result(self, content: str) -> None:
        """Schedule async explore result display."""
        self.call_later(self._async_show_explore_result, content)

    async def _async_show_explore_result(self, content: str) -> None:
        """Async handler to show explore result in task manager."""
        try:
            task_manager = self.query_one(TaskManagerPane)
            await task_manager.show_explore_result(content)
        except Exception as e:
            self.notify(f"Failed to show explore result: {e}", severity="warning")

    def _schedule_explore_and_add_result(self, content: str) -> None:
        """Schedule async explore-and-add result display."""
        self.call_later(self._async_show_explore_and_add_result, content)

    async def _async_show_explore_and_add_result(self, content: str) -> None:
        """Async handler to show explore-and-add result in task manager."""
        try:
            task_manager = self.query_one(TaskManagerPane)
            await task_manager.show_explore_and_add_result(content)
        except Exception as e:
            self.notify(f"Failed to show explore-and-add result: {e}", severity="warning")

    def _process_decomposition(self, response: str, original_request: str) -> None:
        """Process a task decomposition response from Planner."""
        from ..orchestration import TaskDecomposition

        try:
            decomposition = TaskDecomposition.from_planner_response(
                response, original_request
            )
            errors = self._orchestrator.add_decomposition(decomposition)

            if errors:
                self.notify(
                    f"Decomposition issues: {', '.join(errors)}",
                    severity="warning"
                )
            else:
                self.notify(
                    f"Added {decomposition.task_count} tasks - starting execution..."
                )
                
                # Auto-start first task
                self._start_next_task()

        except Exception as e:
            self.notify(
                f"Failed to parse decomposition: {e}",
                severity="error"
            )
    
    def _start_next_task(self) -> None:
        """Start the next task from the queue."""
        if self._orchestrator.queue_size > 0:
            # get_next_task will trigger _on_task_started callback
            task = self._orchestrator.get_next_task()
            if task:
                self._update_queue_display()

    @on(EditRequest)
    def on_edit_request(self, message: EditRequest) -> None:
        """Handle a legacy code edit request (converts to search/replace)."""
        if not self._code_editor:
            self.notify("Code editor not initialized", severity="error")
            return

        self._execute_edit_from_message(SearchReplaceOperation(
            target=message.target,
            search_content=message.old_content,
            replace_content=message.new_content,
            reason=message.reason,
        ))

    @on(SearchReplaceRequest)
    def on_search_replace_request(self, message: SearchReplaceRequest) -> None:
        """Handle a search/replace edit request (external triggers)."""
        if not self._code_editor:
            self.notify("Code editor not initialized", severity="error")
            return

        self._execute_edit_from_message(SearchReplaceOperation(
            target=message.target,
            search_content=message.search_content,
            replace_content=message.replace_content,
            reason=message.reason,
        ))

    @on(EditFeedbackMessage)
    async def on_edit_feedback(self, message: EditFeedbackMessage) -> None:
        """Handle edit feedback - can be used to feed back to Planner for retry."""
        # Log the feedback for debugging
        if self._planner_agent:
            self._planner_agent.memory.record_system_event(
                f"Edit failed on {message.target}: {message.error_type}\n"
                f"Similarity to closest match: {message.similarity:.0%}"
            )

        # Show abbreviated feedback in notification
        if message.similarity > 0:
            self.notify(
                f"Edit feedback: {message.similarity:.0%} similar match found",
                severity="warning"
            )

    @on(ModeSwitch)
    async def on_mode_switch(self, message: ModeSwitch) -> None:
        """Handle mode switch request."""
        self.fast_mode = message.fast_mode
        coder_view = self.query_one(CoderViewPane)

        if message.fast_mode:
            # Initialize fast coder if needed
            if not self._fast_coder_backend and self.settings.active_api_key:
                self._fast_coder_backend = OpenAICompatibleBackend.create(
                    provider=self.settings.provider,
                    role="coder",
                    api_key=self.settings.active_api_key,
                    model_id=self.settings.get_model("coder"),
                )
            coder_view.set_local_model_unavailable()
            self.notify("Switched to Fast Mode (API-based)")
        else:
            # Initialize local Coder if needed
            if not self._coder_backend:
                self._init_coder()
            coder_view.set_local_model_available()
            self.notify("Switched to Local Mode (Coder)")

    @on(PlannerModeSwitch)
    async def on_planner_mode_switch(self, message: PlannerModeSwitch) -> None:
        """Handle Planner mode switch request."""
        if not self._planner_agent:
            return

        if message.mode == "go":
            self._planner_agent.switch_mode(PlannerMode.GO)
        elif message.mode == "plan":
            sub = PlanSubMode.DISCOVERY if message.sub_mode == "discovery" else PlanSubMode.MAINTAINABLE
            self._planner_agent.switch_mode(PlannerMode.PLAN, sub_mode=sub)

        # Store mode preference in session memory
        self._planner_agent.memory.session.store(
            "planner_mode", self._planner_agent.mode_state.get_display_string()
        )

        display = self._planner_agent.mode_state.get_display_string()
        self.notify(f"Planner mode: {display}")

    @on(ConfirmScope)
    async def on_confirm_scope(self, message: ConfirmScope) -> None:
        """Handle scope confirmation - trigger decomposition."""
        if not self._planner_backend:
            self.notify("Planner not available", severity="error")
            return
        
        # Build decomposition prompt from accumulated scope
        decompose_prompt = f"""The user has confirmed this project scope. Please decompose it into atomic, implementable subtasks.

Scope Discussion:
{message.scope_summary if message.scope_summary else 'Create the discussed project'}

Each subtask must have a "type" field:
- "create": Generate a new file from scratch (has expected_output filename)
- "edit": Modify an existing file (has target_file and search_hint)

Return JSON with reasoning and subtasks array. Decompose into small, focused tasks."""

        # Send as decomposition request
        self.post_message(PlannerRequest(decompose_prompt, request_type="decompose"))

    @on(ClearContext)
    async def on_clear_context(self, message: ClearContext) -> None:
        """Handle full context reset from /clear."""
        if self._planner_agent:
            self._planner_agent.clear_context()
            self._planner_agent.memory.clear()
        self.notify("Agent state cleared")

    @on(ExecuteTasks)
    async def on_execute_tasks(self, message: ExecuteTasks) -> None:
        """Handle request to execute pending tasks."""
        if self._orchestrator.queue_size == 0:
            self.notify("No tasks in queue", severity="warning")
            return

        self._start_next_task()

    def _update_token_display(self, backend) -> None:
        """Update token usage displays from a backend's tracker."""
        if not hasattr(backend, 'token_tracker'):
            return
        last = backend.token_tracker.get_last()
        totals = backend.token_tracker.get_session_totals()

        def _do_update():
            try:
                if last:
                    task_manager = self.query_one(TaskManagerPane)
                    task_manager.show_token_usage(
                        last.prompt_tokens, last.completion_tokens, last.total_tokens
                    )
                sidebar = self.query_one(RightSidebar)
                sidebar.update_token_usage(totals)
            except Exception as e:
                self.log.warning(f"Could not update token display: {e}")

        self._safe_call(_do_update)

    def _is_main_thread(self) -> bool:
        import threading
        return threading.current_thread() is threading.main_thread()

    def _safe_call(self, func, *args, **kwargs):
        """Call a function safely from any thread context."""
        if self._is_main_thread():
            func(*args, **kwargs)
        else:
            self.call_from_thread(func, *args, **kwargs)


    def _update_index_coverage(self) -> None:
        """Compute embedding index coverage and push to status bar."""
        try:
            from ..sandbox.embeddings import CodebaseRAG, HAS_EMBEDDINGS
            if not HAS_EMBEDDINGS:
                task_manager = self.query_one(TaskManagerPane)
                task_manager.update_index_status(-1, 0)  # Signal deps missing
                return
            rag = CodebaseRAG(project_root=str(Path.cwd()))
            indexed, total = rag.get_index_coverage()
            task_manager = self.query_one(TaskManagerPane)
            task_manager.update_index_status(indexed, total)
        except Exception as e:
            logger.warning(f"Could not update index coverage: {e}")

    def _handle_log_activity(self, message: str) -> None:
        """Callback to log activity to the sidebar (may be from any thread)."""
        def _do_log():
            try:
                sidebar = self.query_one(RightSidebar)
                sidebar.log_activity(message)
            except Exception as e:
                self.log.warning(f"Could not log activity: {e}")

        self._safe_call(_do_log)

    # --- Thread-Safe Coder Output Access ---

    def _get_coder_output_safe(self) -> str:
        """Thread-safe getter for Coder output content.

        Returns the cached content - safe to call from any thread.
        No DOM access required.
        """
        return self._coder_content

    def _set_coder_output_safe(self, content: str) -> None:
        """Thread-safe setter for Coder output content.

        Updates the cache immediately (for thread-safe reads), then schedules
        UI update on main thread if needed.
        """
        import threading

        # Update cache first - this makes the content immediately available
        # for reads from any thread
        self._coder_content = content

        if threading.current_thread() is threading.main_thread():
            # On main thread, update UI directly
            try:
                coder_view = self.query_one(CoderViewPane)
                coder_view.update_output(content)
            except Exception as e:
                self.log.error(f"Failed to update Coder output: {e}")
        else:
            # On worker thread, schedule UI update on main thread
            self.call_from_thread(self._do_set_coder_output, content)

    def _do_set_coder_output(self, content: str) -> None:
        """Actually update Coder output UI (runs on main thread)."""
        # Also update cache in case this is called directly
        self._coder_content = content
        try:
            coder_view = self.query_one(CoderViewPane)
            coder_view.update_output(content)
        except Exception as e:
            self.log.error(f"Failed to update Coder output: {e}")

    def _clear_coder_output(self) -> None:
        """Clear Coder output (call from main thread only)."""
        self._coder_content = ""
        try:
            coder_view = self.query_one(CoderViewPane)
            coder_view.clear_output()
        except Exception as e:
            self.log.error(f"Failed to clear Coder output: {e}")

    def _append_coder_output(self, chunk: str) -> None:
        """Append to Coder output (call from main thread only)."""
        self._coder_content += chunk
        try:
            coder_view = self.query_one(CoderViewPane)
            coder_view.append_output(chunk)
        except Exception as e:
            self.log.error(f"Failed to append Coder output: {e}")

    # --- Approval Callbacks ---

    async def _request_file_approval(
        self,
        operation: str,
        path: str,
        description: str,
    ) -> bool:
        """
        Request user approval for a file operation.

        WARNING: Uses push_screen_wait which is BROKEN from worker threads.
        This method is only safe from the main async event loop.
        For worker-thread approval, use the push_screen(modal, callback) +
        threading.Event pattern (see _execute_edit for the correct approach).
        """
        modal = FileWriteApprovalModal(path, len(description), description)
        return await self.push_screen_wait(modal)

    async def _request_edit_approval(self, operation: SearchReplaceOperation) -> bool:
        """
        Request user approval for a code edit.

        WARNING: Uses push_screen_wait which is BROKEN from worker threads.
        This is a legacy method — prefer _execute_edit() which uses the
        push_screen(modal, callback) + threading.Event pattern instead.
        """
        from .modals.approval_modal import ApprovalModal

        # Show match info if available
        match_info = ""
        if operation.match_type:
            match_info = f" ({operation.match_type} match"
            if operation.match_confidence:
                match_info += f", {operation.match_confidence:.0%} confidence"
            match_info += ")"

        modal = ApprovalModal(
            operation="Code Edit",
            target=f"{operation.target}{match_info}",
            description=f"{operation.reason}\n\nSearch:\n{operation.search_content[:200]}...\n\nReplace:\n{operation.replace_content[:200]}...",
            risk_level="review",
        )

        return await self.push_screen_wait(modal)

    def _handle_edit_request(self, operation: SearchReplaceOperation) -> bool:
        """Handle an edit request from Planner agent.

        Delegates to _execute_edit (sync, callback-based approval).
        Must be called from a worker thread.
        """
        return self._execute_edit(operation)

    @on(ProviderChanged)
    def on_provider_changed(self, message: ProviderChanged) -> None:
        """Handle provider, API key, or model changes from the settings screen."""
        self.settings.provider = message.provider
        self.settings.fireworks_api_key = message.fireworks_key
        self.settings.openrouter_api_key = message.openrouter_key
        self.settings.chat_model = message.chat_model
        self.settings.coder_model = message.coder_model
        self.settings.explorer_model = message.explorer_model
        
        if not self.settings.active_api_key:
            self.notify(f"{message.provider.title()} API key missing. Backends disabled.", severity="warning")
            return
            
        self.notify(f"Reloading backends with {message.provider.title()}...")
        
        try:
            self._planner_backend = OpenAICompatibleBackend.create(
                provider=self.settings.provider,
                role="chat",
                api_key=self.settings.active_api_key,
                model_id=self.settings.get_model("chat"),
            )
            self._deepseek_backend = OpenAICompatibleBackend.create(
                provider=self.settings.provider,
                role="explorer",
                api_key=self.settings.active_api_key,
                model_id=self.settings.get_model("explorer"),
            )

            if self.fast_mode:
                self._fast_coder_backend = OpenAICompatibleBackend.create(
                    provider=self.settings.provider,
                    role="coder",
                    api_key=self.settings.active_api_key,
                    model_id=self.settings.get_model("coder"),
                )

            # Update agents with new backends
            if self._planner_agent:
                self._planner_agent.backend = self._planner_backend
            if self._explore_agent:
                self._explore_agent.backend = self._deepseek_backend

            self.notify("Backends successfully reloaded!", severity="information")
            
        except Exception as e:
            logger.error("Failed to reload backends: %s", e, exc_info=True)
            self.notify(f"Failed to reload backends: {e}", severity="error")


def run_app(
    coder_model_path: Optional[str] = None,
    settings: Optional[NoetherSettings] = None,
    fast_mode: bool = False,
) -> None:
    """
    Run the multi-agent TUI application.

    Args:
        coder_model_path: Path to Coder GGUF model (for local mode)
        settings: NoetherSettings application settings
        fast_mode: If True, use API-based coder instead of local Coder
    """
    app = MultiAgentApp(
        coder_model_path=coder_model_path,
        settings=settings,
        fast_mode=fast_mode,
    )
    app.run()
