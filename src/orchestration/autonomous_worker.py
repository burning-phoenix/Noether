"""
Autonomous Worker

Handles the execution of Planner's Reason-Act-Observe loops.
"""

import asyncio
import traceback
import logging
from typing import Optional, Any
from textual import work, on
from textual.widget import Widget

from ..ui.messages import StartAutonomousLoop, ExploreAndAddContext
from ..ui.screens.task_manager import TaskManagerPane
from ..observability.tracer import tracer

logger = logging.getLogger("noether.autonomous_worker")

class AutonomousWorker(Widget):
    """
    Invisible Textual widget that listens to StartAutonomousLoop messages
    and manages the blocking agent loop within a background thread.
    """

    DEFAULT_CSS = """
    AutonomousWorker {
        display: none;
    }
    """

    def __init__(
        self,
        planner_agent: Optional[Any] = None,
        schedule_system_message_fn: Optional[Any] = None,
        pipeline: Optional[Any] = None,
    ):
        super().__init__()
        self.planner_agent = planner_agent
        self.schedule_system_message_fn = schedule_system_message_fn
        self.pipeline = pipeline

    @on(StartAutonomousLoop)
    async def on_start_autonomous_loop(self, message: StartAutonomousLoop) -> None:
        """Handle request to start Planner's autonomous Reason-Act-Observe loop."""
        if not self.planner_agent:
            self.app.notify("Planner agent not available — check API key in Settings (Ctrl+S)", severity="error")
            if self.schedule_system_message_fn:
                self.app.call_from_thread(
                    self.schedule_system_message_fn,
                    "⚠️ No Planner agent available. Set your API key in Settings (Ctrl+S).",
                    "red"
                )
            return

        self.app.notify(f"Starting autonomous loop: {message.task[:50]}...")
        self._run_autonomous_loop(message.task, message.context)

    @work(thread=True)
    def _run_autonomous_loop(self, task: str, context: Optional[str]) -> None:
        """Run the autonomous loop in a background thread."""
        task_manager = self.app.call_from_thread(self.app.query_one, TaskManagerPane)

        # Show start message
        if self.schedule_system_message_fn:
            self.app.call_from_thread(
                self.schedule_system_message_fn,
                f"[bold magenta]Starting Autonomous Mode[/]\nTask: {task}",
                "magenta"
            )

        try:
            # Create event loop for async operations
            loop = asyncio.new_event_loop()

            try:
                # Run the agentic loop with tracer span
                with tracer.span("autonomous.loop", task=task[:100]) as span:
                    result = loop.run_until_complete(
                        self.planner_agent.run_autonomous_loop(task)
                    )
                    span.set_result(status=result.get("status", "unknown"))
            finally:
                loop.close()

            # Process result
            status = result.get("status", "unknown")
            if status == "success":
                summary = result.get("result", "Task completed")
                iterations = result.get("iterations", 0)
                if self.schedule_system_message_fn:
                    self.app.call_from_thread(
                        self.schedule_system_message_fn,
                        f"[bold green]Autonomous Loop Complete[/]\n"
                        f"Iterations: {iterations}\n"
                        f"Result: {summary}",
                        "green"
                    )
                # Record in memory
                if self.planner_agent:
                    self.planner_agent.memory.record_system_event(
                        f"Autonomous loop completed: {summary}"
                    )

            elif status == "needs_input":
                question = result.get("question", "Clarification needed")
                if self.schedule_system_message_fn:
                    self.app.call_from_thread(
                        self.schedule_system_message_fn,
                        f"[bold yellow]Autonomous Loop Paused[/]\n"
                        f"Planner needs input: {question}",
                        "yellow"
                    )

            elif status == "max_iterations":
                if self.schedule_system_message_fn:
                    self.app.call_from_thread(
                        self.schedule_system_message_fn,
                        f"[bold red]Autonomous Loop: Max iterations reached[/]\n"
                        f"{result.get('message', 'Stopped after max iterations')}",
                        "red"
                    )

            else:
                if self.schedule_system_message_fn:
                    self.app.call_from_thread(
                        self.schedule_system_message_fn,
                        f"[bold red]Autonomous Loop: Unknown status[/]\n{result}",
                        "red"
                    )

        except Exception as e:
            self.app.call_from_thread(
                self.app.notify,
                f"Autonomous loop error: {e}",
                severity="error"
            )
            if self.schedule_system_message_fn:
                self.app.call_from_thread(
                    self.schedule_system_message_fn,
                    f"[bold red]Autonomous Loop Error[/]\n{str(e)}\n{traceback.format_exc()[:500]}",
                    "red"
                )

    @on(ExploreAndAddContext)
    async def on_explore_add(self, message: ExploreAndAddContext) -> None:
        """Handle /explore-add by running agentic loop for targeted exploration."""
        if not self.planner_agent:
            self.app.notify("Planner agent not available — check API key in Settings (Ctrl+S)", severity="error")
            if self.schedule_system_message_fn:
                self.app.call_from_thread(
                    self.schedule_system_message_fn,
                    "⚠️ No Planner agent available. Set your API key in Settings (Ctrl+S).",
                    "red"
                )
            return

        self.app.notify(f"Exploring: {message.query[:50]}...")
        self._run_explore_add(message.query)

    @work(thread=True)
    def _run_explore_add(self, query: str) -> None:
        """Run agentic exploration loop, compress results, add to Planner context."""
        try:
            # Log activity
            if self.planner_agent and self.planner_agent.on_log_activity:
                self.planner_agent.on_log_activity(f"[bold cyan]EXPLORE[/] Starting: {query}")

            # Create a focused exploration prompt
            explore_prompt = (
                f"Explore the codebase to understand: {query}\n"
                "Use execute_bash to examine file structure, read files, "
                "and search for relevant code. Use semantic_search to find "
                "related code by concept. Build a thorough understanding."
            )

            # Run the agentic loop with tracer span
            with tracer.span("explore.add", query=query) as span:
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(
                        self.planner_agent.agentic_loop.reason_act_observe(
                            user_request=explore_prompt,
                            context=self.planner_agent._project_context,
                        )
                    )
                finally:
                    loop.close()
                span.set_result(status="complete")

            # Compress the loop's context into a detailed report
            raw_context = self.planner_agent.agentic_loop._format_context_memory()

            # Use the Planner backend to compress (one-shot, no tools)
            compress_prompt = (
                f"The following is the raw output from an autonomous exploration of: {query}\n\n"
                f"{raw_context}\n\n"
                "Synthesize this into a detailed, actionable report. Include:\n"
                "- Key files and their roles\n"
                "- Important functions/classes found\n"
                "- Code patterns and architecture\n"
                "- Any issues or concerns discovered\n"
                "Keep code snippets where they add clarity. Be thorough but organized."
            )

            report = ""
            for chunk in self.planner_agent.backend.stream(compress_prompt, max_tokens=2048):
                report += chunk

            # Add to Planner's project context
            self.planner_agent.set_project_context(
                self.planner_agent._project_context + f"\n\n[Exploration: {query}]\n{report}"
            )

            # Show in chat
            if self.schedule_system_message_fn:
                self.app.call_from_thread(
                    self.schedule_system_message_fn,
                    f"[bold magenta]Exploration Complete: {query}[/]\n{report}",
                    "magenta"
                )

            # Record in memory
            self.planner_agent.memory.record_system_event(
                f"Explored: {query} — {report[:200]}"
            )

        except Exception as e:
            self.app.call_from_thread(
                self.app.notify,
                f"Explore-add error: {e}",
                severity="error"
            )
            if self.schedule_system_message_fn:
                self.app.call_from_thread(
                    self.schedule_system_message_fn,
                    f"[bold red]Exploration Error[/]\n{str(e)}",
                    "red"
                )
