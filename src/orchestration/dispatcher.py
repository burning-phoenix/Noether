"""
Agent Dispatcher

Handles routing of requests to agents, manages worker threads,
and posts messages back to the UI, decoupling app.py from direct UI mutations.
"""

from typing import Optional, Any
from textual.widget import Widget
from textual import on, work

from ..ui.messages import (
    CoderRequest,
    CoderResponse,
    PlannerRequest,
    PlannerResponse,
    ExecuteTasks,
    ExploreAndAddContext,
)

class AgentDispatcher(Widget):
    """
    Hidden widget that intercepts agent requests, coordinates worker threads,
    and dispatches reactive Textual messages instead of using threading locks.
    """
    
    DEFAULT_CSS = """
    AgentDispatcher {
        display: none;
    }
    """

    def __init__(
        self,
        coder_worker: Any,
        planner_worker: Any,
        fast_mode: bool,
        coder_backend: Optional[Any] = None,
        fast_coder_backend: Optional[Any] = None,
        planner_backend: Optional[Any] = None,
        edit_handler: Optional[Any] = None,
        sandbox: Optional[Any] = None,
        coder_content: str = "",
        explore_agent: Optional[Any] = None,
        deepseek_backend: Optional[Any] = None,
        orchestrator: Optional[Any] = None,
        pipeline: Optional[Any] = None,
    ) -> None:
        super().__init__()
        self.coder_worker = coder_worker
        self.planner_worker = planner_worker
        self.fast_mode = fast_mode
        self.coder_backend = coder_backend
        self.fast_coder_backend = fast_coder_backend
        self.planner_backend = planner_backend
        self.edit_handler = edit_handler
        self.sandbox = sandbox
        self.pipeline = pipeline

        self._coder_streaming = False
        self._planner_streaming = False

        self.coder_content = coder_content
        self.explore_agent = explore_agent
        self.deepseek_backend = deepseek_backend
        self.orchestrator = orchestrator
        
    def on_mount(self) -> None:
        """Bind up orchestrator events when mounted into the UI."""
        if self.orchestrator:
            self.orchestrator._on_task_started = self._on_task_started
            self.orchestrator._on_task_completed = self._on_task_completed
            self.orchestrator._on_task_failed = self._on_task_failed
            self.orchestrator._on_queue_changed = self._on_queue_changed
            
    def _execute_edit_from_message(self, operation: Any) -> None:
        if self.edit_handler:
            self.edit_handler.execute_edit(operation)
    
    def _handle_log_activity(self, message: str) -> None:
        # We can just delegate to app's logger or notify
        self.app.notify(message)

    @on(CoderRequest)
    def handle_coder_request(self, message: CoderRequest) -> None:
        # In fast mode, use the API-based fast coder
        if self.fast_mode:
            if not self.fast_coder_backend:
                self.notify("Fast coder not initialized", severity="warning")
                return
        else:
            if not self.coder_backend:
                self.notify("Coder not loaded yet", severity="warning")
                return

        if self._coder_streaming:
            self.notify("Coder is busy", severity="warning")
            return

        self._coder_streaming = True
        self._run_coder(message.prompt, message.task_id)

    @work(thread=True)
    def _run_coder(self, prompt: str, task_id: Optional[str] = None) -> None:
        self.coder_worker.set_streaming(True)
        mode_label = "Fast" if self.fast_mode else "Local"
        
        # Send streaming start
        self.post_message(CoderResponse("", is_start=True, status=f"Generating ({mode_label})...", task_id=task_id))

        def _on_chunk(chunk: str):
            self.post_message(CoderResponse(chunk=chunk, task_id=task_id))

        def _on_status(text: str) -> None:
            self.post_message(CoderResponse("", status=text, task_id=task_id))

        try:
            backend = self.fast_coder_backend if self.fast_mode else self.coder_backend

            full_response = self.coder_worker.stream_code(
                backend=backend,
                prompt=prompt,
                task_id=task_id,
                on_chunk=_on_chunk,
                on_status=_on_status,
            )

            _on_status("Parsing file operations...")

            file_ops = self.coder_worker.parse_file_ops(full_response, task_id)

            if file_ops:
                _on_status(f"Found {len(file_ops)} file(s) - awaiting approval...")
                self.app.call_from_thread(self.app.notify, f"Found {len(file_ops)} file(s) to create")
                
                # Send custom message to App to trigger approval modal
                # (App handles modals best)
                from ..ui.messages import PendingFileOperations
                self.post_message(PendingFileOperations(file_ops=file_ops, task_id=task_id))
                
            else:
                _on_status("No files found in output")
                self.app.call_from_thread(self.app.notify, "No file operations found in output", severity="warning")

            if task_id:
                self.coder_worker.complete_task(task_id, full_response)
                # Chain to next task if any
                self.post_message(ExecuteTasks(auto_run=True))

            _on_status("Done")

        except Exception as e:
            self.app.call_from_thread(self.app.notify, f"Coder error: {e}", severity="error")
            # Persist error in chat so user doesn't miss it
            self.app.call_from_thread(
                self.app._schedule_system_message,
                f"⚠️ Coder API Error: {e}",
                "red"
            )
            _on_status(f"Error: {e}")
            if task_id:
                self.coder_worker.fail_task(task_id, str(e))

        finally:
            self.coder_worker.set_streaming(False)
            self.post_message(CoderResponse("", is_complete=True, task_id=task_id))
            self.app.call_from_thread(setattr, self, '_coder_streaming', False)

    @on(PlannerRequest)
    def handle_planner_request(self, message: PlannerRequest) -> None:
        if not self.planner_backend:
            self.notify("Planner not available (check API key)", severity="warning")
            return

        if self._planner_streaming:
            self.notify("Planner is busy", severity="warning")
            return

        self._planner_streaming = True
        self._run_planner(message.prompt, message.request_type)

    @work(thread=True)
    def _run_planner(self, prompt: str, request_type: str) -> None:
        # Send streaming start
        self.post_message(PlannerResponse("", is_start=True, response_type=request_type))

        try:
            def _on_chunk(chunk: str):
                self.post_message(PlannerResponse(chunk=chunk, response_type=request_type))

            iteration = 0
            max_iterations = 5 if request_type == "chat" else 1
            current_prompt = prompt

            while iteration < max_iterations:
                iteration += 1

                if self.planner_worker:
                    full_response = self.planner_worker.stream_chat(
                        prompt=current_prompt,
                        request_type=request_type,
                        on_chunk=_on_chunk,
                        coder_context=self.coder_content,
                        sandbox=self.sandbox,
                    )
                else:
                    full_response = ""
                    if self.planner_backend:
                        for chunk in self.planner_backend.stream(current_prompt):
                            full_response += chunk
                            _on_chunk(chunk)

                if request_type == "decompose" and self.planner_worker:
                    from ..ui.messages import ProcessDecomposition
                    self.post_message(ProcessDecomposition(response=full_response, prompt=prompt))

                # Process tool calls through pipeline (handles bash + edit_file)
                results = []
                if self.planner_worker and self.pipeline:
                    results = self.planner_worker.process_tool_calls(self.pipeline)

                # Legacy path: parse S/R blocks from text for edit requests
                if request_type == "edit" and self.planner_worker:
                    edits = self.planner_worker.parse_edit_operations(full_response)
                    if edits and self.edit_handler:
                        for edit in edits:
                            self.edit_handler.execute_edit(edit)

                # Check for user-facing chat commands (/explore-add, /run)
                if self.planner_worker:
                    commands = self.planner_worker.check_for_commands(full_response)
                    self._dispatch_commands(commands)

                # Also check for text-based S/R blocks (non-edit requests)
                if request_type != "edit" and self.edit_handler:
                    self.edit_handler.apply_edit_blocks(full_response)

                if not results:
                    break  # No tools called, loop complete

                if iteration >= max_iterations:
                    break

                # Format results for next iteration
                feedback_parts = ["SYSTEM TOOL EXECUTION RESULTS:\n"]
                for res in results:
                    if isinstance(res, dict) and res.get("type") == "semantic_search":
                        status = "Success" if res.get("success") else "Failed"
                        feedback_parts.append(f"[semantic_search] {status}")
                        if res.get("stdout"):
                            feedback_parts.append(f"Output:\n{res['stdout']}")
                        if res.get("stderr"):
                            feedback_parts.append(f"Error:\n{res['stderr']}")
                    elif hasattr(res, "success"):
                        status = "Success" if res.success else "Failed"
                        op_name = res.op_type.value if hasattr(res, "op_type") else "Tool"
                        feedback_parts.append(f"[{op_name}] {status}")
                        if hasattr(res, "stdout") and res.stdout:
                            feedback_parts.append(f"Output:\n{res.stdout[:2000]}")
                        if hasattr(res, "stderr") and res.stderr:
                            feedback_parts.append(f"Errors:\n{res.stderr[:1000]}")
                        if hasattr(res, "message") and res.message:
                            feedback_parts.append(f"Message: {res.message}")
                        # Richer feedback for file edits: show line diff or warn if unchanged
                        if res.success and getattr(res, "op_type", None) and res.op_type.value == "file_edit":
                            before = getattr(res, "before_content", "")
                            after = getattr(res, "after_content", "")
                            if before and after:
                                if before == after:
                                    feedback_parts.append("WARNING: File content UNCHANGED after edit. The search content may have matched but the replacement was identical.")
                                else:
                                    before_lines = before.count("\n") + 1
                                    after_lines = after.count("\n") + 1
                                    feedback_parts.append(f"Changed: {before_lines} -> {after_lines} lines")

                current_prompt = "\n".join(feedback_parts)
                if iteration >= max_iterations - 1:
                    current_prompt += "\n\nIMPORTANT: This is your final iteration. Synthesize ALL findings into a complete, actionable response. Do NOT call any more tools."
                else:
                    current_prompt += "\n\nAnalyze these results and continue your response. If the task is complete, provide a final synthesis without calling tools."

                # Notify user of loop progress
                self.app.call_from_thread(
                    self.app.notify,
                    f"Tool execution complete. Planner reacting (Iteration {iteration+1}/{max_iterations})...",
                    severity="information"
                )

                # Add a separator to the UI streaming response
                _on_chunk("\n\n*Observing tool output and reacting...*\n\n")

        except Exception as e:
            self.app.call_from_thread(self.app.notify, f"Planner error: {e}", severity="error")
            # Persist error in chat so user doesn't miss it
            self.app.call_from_thread(
                self.app._schedule_system_message,
                f"⚠️ Planner API Error: {e}",
                "red"
            )

        finally:
            self.post_message(PlannerResponse("", is_complete=True, response_type=request_type))
            self.app.call_from_thread(setattr, self, '_planner_streaming', False)

    def _dispatch_commands(self, commands: list[dict]) -> None:
        from ..ui.messages import ScheduleCommandApproval
        for cmd in commands:
            cmd_type = cmd.get("type")

            if cmd_type == "exec":
                display = cmd.get("display", cmd["command"])
                self.app.call_from_thread(self.app.notify, f"Detected command: {display[:60]}")
                self.post_message(ScheduleCommandApproval(command=cmd["command"]))
                
            elif cmd_type == "explore-add":
                self.post_message(ExploreAndAddContext(
                    query=cmd.get("query", "architecture"),
                ))
            elif cmd_type == "run":
                self.post_message(ExecuteTasks(auto_run=True))

        if commands:
            self.app.call_from_thread(self._handle_log_activity, f"Commands detected: {len(commands)}")


    # ExploreAndAddContext is now handled by AutonomousWorker (agentic loop)


    # --- Orchestrator Callbacks Extracted from app.py ---
    # --- Orchestrator Callbacks ---

    def _is_main_thread(self) -> bool:
        """Check if we're on the main thread."""
        import threading
        return threading.current_thread() is threading.main_thread()

    def _safe_call(self, func, *args, **kwargs):
        """Call a function safely from any thread context."""
        if self._is_main_thread():
            func(*args, **kwargs)
        else:
            self.app.call_from_thread(func, *args, **kwargs)

    def _on_task_started(self, task) -> None:
        """Called when a task starts execution (may be from any thread)."""
        self._safe_call(self.app.notify, f"Starting task: {task.id}")
        # Route by task type: edit → Planner S/R, create → Coder
        if getattr(task, 'task_type', 'create') == "edit":
            self._safe_call(self.app.post_message, PlannerRequest(task.to_prompt(), request_type="edit"))
        else:
            self._safe_call(self.app.post_message, CoderRequest(task.to_prompt(), task_id=task.id))

    def _on_task_completed(self, task) -> None:
        """Called when a task completes (may be from any thread)."""
        self._safe_call(self.app.notify, f"Task completed: {task.id}")
        self._safe_call(self._update_queue_display)

        # Auto-start next task — must run on main thread so that
        # the CoderRequest post_message is dispatched correctly.
        self.app.call_from_thread(self.app._start_next_task)

    def _on_task_failed(self, task) -> None:
        """Called when a task fails permanently (may be from any thread)."""
        self._safe_call(self.app.notify, f"Task failed: {task.id} - {task.error}", severity="error")
        self._safe_call(self._update_queue_display)

        # Continue to next task — don't halt the entire queue
        self.app.call_from_thread(self.app._start_next_task)

    def _on_queue_changed(self) -> None:
        """Called when the queue changes (may be from any thread)."""
        self._safe_call(self._update_queue_display)

    def _update_queue_display(self) -> None:
        """Update the task queue in the UI (Task Manager and Sidebar)."""
        try:
            tasks = self.orchestrator.get_full_task_snapshot()

            # Use _safe_call for both query and update to ensure thread safety
            def _do_update():
                try:
                    task_manager = self.app.query_one(TaskManagerPane)
                    task_manager.update_task_queue(tasks)
                    sidebar = self.app.query_one(RightSidebar)
                    sidebar.update_tasks(tasks)
                except Exception as e:
                    self.log.warning(f"Could not update queue display widgets: {e}")

            self._safe_call(_do_update)

        except Exception as e:
            self.log.warning(f"Could not update queue display: {e}")

