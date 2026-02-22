"""
Planner Agent for planning, task decomposition, and code editing.

Planner-K2-Thinking has a large context window (128K) and excels at:
- Understanding complex requirements
- Breaking down tasks into atomic units
- Maintaining project context
- Precise code editing via line references
"""

from pathlib import Path
from typing import Iterator, Optional, Callable, Union
import json
import re
from ..observability.tracer import tracer

from ..backends import FireworksBackend
from ..orchestration import Task, TaskDecomposition, TaskOrchestrator, SearchReplaceOperation
from ..orchestration.editor import parse_search_replace_blocks
from ..orchestration.agentic_loop import PlannerAgenticLoop
from ..orchestration.prompts import PLANNER_CHAT_SYSTEM, PLANNER_DECOMPOSITION_SYSTEM, PLANNER_EDIT_SYSTEM
from ..prompts import PLANNER_GO_MODE_SYSTEM, PLANNER_PLAN_MAINTAINABLE_SYSTEM, PLANNER_PLAN_DISCOVERY_SYSTEM
from ..modes.planner_modes import PlannerModeState, PlannerMode, PlanSubMode
from ..sandbox.command_executor import SandboxCommandExecutor
from ..memory.planner_memory import PlannerMemoryManager


class PlannerAgent:
    """
    Agent for task planning and code editing using Planner-K2.

    Responsibilities:
    - Decompose complex tasks into atomic units for Coder
    - Maintain conversation history for context
    - Parse and execute code edit operations
    - Coordinate with the task orchestrator
    """

    def __init__(
        self,
        backend: FireworksBackend,
        orchestrator: Optional[TaskOrchestrator] = None,
        on_edit_request: Optional[Callable[[SearchReplaceOperation], Union[bool]]] = None,
        on_log_activity: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize the Planner agent.

        Args:
            backend: Fireworks backend configured for Planner
            orchestrator: Task orchestrator for adding decomposed tasks
            on_edit_request: Callback for edit operations
            on_log_activity: Callback for logging activities
        """
        self._backend = backend
        self.orchestrator = orchestrator
        self.on_edit_request = on_edit_request
        self.on_log_activity = on_log_activity or (lambda msg: None)

        # Initialize Sandbox Executor
        self.sandbox_executor = SandboxCommandExecutor()

        # Pipeline is set later by app.py after init
        self._pipeline = None

        # Initialize Autonomous Loop
        self.agentic_loop = PlannerAgenticLoop(
            planner_backend=self._backend,
            sandbox_executor=self.sandbox_executor,
            pipeline=None,  # Set later via set_pipeline()
            on_log_activity=self.on_log_activity
        )

        # Mode state
        self.mode_state = PlannerModeState()

        # Context management
        self.memory = PlannerMemoryManager()
        self._project_context: str = ""
        self._coder_output_context: str = ""
        
    @property
    def backend(self) -> FireworksBackend:
        return self._backend
        
    @backend.setter
    def backend(self, new_backend: FireworksBackend) -> None:
        self._backend = new_backend
        if hasattr(self, 'agentic_loop') and self.agentic_loop:
            self.agentic_loop.planner = new_backend

    def set_pipeline(self, pipeline) -> None:
        """Set the OperationPipeline for routing operations."""
        self._pipeline = pipeline
        self.agentic_loop.pipeline = pipeline

    def switch_mode(self, new_mode: PlannerMode, sub_mode: Optional[PlanSubMode] = None) -> None:
        """Switch planner mode and clear backend history to prevent cross-mode pollution."""
        self.mode_state.mode = new_mode
        if sub_mode is not None:
            self.mode_state.plan_sub_mode = sub_mode
        # Prevent stale history from a different mode influencing the new one
        self.backend.clear_history()
        self.memory.record_system_event(f"Mode switched to {self.mode_state.get_display_string()}")

    def _get_system_prompt_for_mode(self) -> str:
        """Return the appropriate system prompt based on current mode."""
        if self.mode_state.is_go_mode():
            return PLANNER_GO_MODE_SYSTEM
        if self.mode_state.is_discovery():
            return PLANNER_PLAN_DISCOVERY_SYSTEM
        if self.mode_state.is_maintainable():
            return PLANNER_PLAN_MAINTAINABLE_SYSTEM
        # Fallback
        return PLANNER_CHAT_SYSTEM

    def set_project_context(self, context: str) -> None:
        """
        Set the project context (file structure, key files, etc.).

        Args:
            context: Project context string
        """
        self._project_context = context

    def set_coder_output_context(self, output: str) -> None:
        """
        Set the current Coder output for reference in editing.

        Args:
            output: Current code output from Coder
        """
        self._coder_output_context = output

    def chat(
        self,
        message: str,
        include_context: bool = True,
    ) -> Iterator[Union[str, dict]]:
        """
        Chat with Planner using Socratic method for scope refinement.

        Args:
            message: User message
            include_context: Whether to include project/output context

        Yields:
            str chunks for text, dict chunks for tool_call events
        """
        prompt = message

        # Auto-enrich with semantic search (RAG) if index coverage is sufficient
        try:
            from ..sandbox.embeddings import CodebaseRAG, HAS_EMBEDDINGS
            if HAS_EMBEDDINGS:
                rag = CodebaseRAG(project_root=str(Path.cwd()))
                try:
                    indexed, total = rag.get_index_coverage()
                    if total > 0 and (indexed / total) >= 0.50:
                        results = rag.search(message, limit=3)
                        if results:
                            rag_context = "\n".join(
                                f"- {r['path']}: {r['preview'][:200]}"
                                for r in results
                            )
                            prompt = f"[Relevant codebase context from semantic search]\n{rag_context}\n\n{message}"
                finally:
                    rag.close()
        except Exception:
            pass  # Gracefully skip if not available

        if include_context and self._project_context:
            prompt = f"Project Context:\n{self._project_context}\n\n{prompt}"

        # Build memory-aware prompt
        memory_context = self.memory.get_full_context_for_llm()
        chat_prompt = f"""
{memory_context}

NEW REQUEST:
{prompt}
"""
        # Debug logging
        if self.on_log_activity:
             self.on_log_activity(f"DEBUG: Chat Context Length: {len(chat_prompt)}")

        # Use mode-appropriate system prompt
        system_prompt = self._get_system_prompt_for_mode()

        # In Go mode, append action reminder
        if self.mode_state.is_go_mode():
            chat_prompt += "\n\nRemember: Act first, ask later. Use Search/Replace blocks directly. For bash or exploration, suggest /auto or /explore-add."

        full_response = ""
        from ..sandbox.tools import get_chat_tools
        with tracer.span("planner.chat", prompt_length=len(chat_prompt)) as span:
            for chunk in self.backend.stream(chat_prompt, system=system_prompt, tools=get_chat_tools()):
                if isinstance(chunk, str):
                    full_response += chunk
                yield chunk  # Yields both str and dict (tool_call) chunks
            span.set_result(response_length=len(full_response))

        # Update history
        self.memory.record_interaction(message, full_response)

    def decompose_task(
        self,
        task_description: str,
        additional_context: str = "",
    ) -> Iterator[str]:
        """
        Decompose a complex task into atomic subtasks.

        Args:
            task_description: The task to decompose
            additional_context: Additional context to include

        Yields:
            Response chunks (JSON decomposition)
        """
        prompt = f"""Decompose this task into atomic subtasks:

{task_description}

"""
        if additional_context:
            prompt += f"""Additional Context:
{additional_context}

"""

        # Auto-enrich with semantic search for decomposition
        try:
            from ..sandbox.embeddings import CodebaseRAG, HAS_EMBEDDINGS
            if HAS_EMBEDDINGS:
                rag = CodebaseRAG(project_root=str(Path.cwd()))
                try:
                    indexed, total = rag.get_index_coverage()
                    if total > 0 and (indexed / total) >= 0.50:
                        results = rag.search(task_description, limit=3)
                        if results:
                            rag_context = "\n".join(
                                f"- {r['path']}: {r['preview'][:200]}"
                                for r in results
                            )
                            prompt += f"""Relevant Codebase Context (from semantic search):
{rag_context}

"""
                finally:
                    rag.close()
        except Exception:
            pass

        if self._project_context:
            prompt += f"""Project Context:
{self._project_context}

"""

        # Add mode-aware decomposition guidance
        if self.mode_state.is_go_mode():
            prompt += "Prefer fewer, larger tasks. Minimize overhead. Get it done.\n\n"
        elif self.mode_state.is_discovery():
            prompt += "Experimental/spike tasks are OK. Prioritize the riskiest piece first. Skip tests for throwaway prototypes.\n\n"
        elif self.mode_state.is_maintainable():
            prompt += "Include test tasks alongside implementation. Add documentation tasks for public APIs. Include error handling as explicit subtasks.\n\n"

        prompt += "Provide the decomposition in JSON format as specified."

        # Use decomposition system prompt with higher max_tokens for complex decompositions
        full_response = ""
        for chunk in self.backend.stream(prompt, system=PLANNER_DECOMPOSITION_SYSTEM, max_tokens=4096):
            full_response += chunk
            yield chunk

        # Update memory with task decomposition (orchestrator integration handled by app.py)
        try:
            decomposition = TaskDecomposition.from_planner_response(
                full_response, task_description
            )
            # Update Task State in memory for context awareness
            self.memory.task_state.set_task(
                task_description,
                decomposition.subtasks if hasattr(decomposition, 'subtasks') else []
            )
        except Exception as e:
            if self.on_log_activity:
                self.on_log_activity(f"Memory update failed: {e}")

    def request_edit(
        self,
        instruction: str,
        target_code: Optional[str] = None,
        target_file: Optional[str] = None,
        error_feedback: Optional[str] = None,
        file_reader: Optional[Callable[[str], Optional[str]]] = None,
    ) -> Iterator[str]:
        """
        Request code edits from Planner using Search/Replace blocks.

        Args:
            instruction: Edit instruction (e.g., "fix the bug in calculate_total")
            target_code: Code to edit (uses coder_output_context if not provided)
            target_file: Optional file path to edit (reads from sandbox)
            error_feedback: Optional feedback from a failed edit attempt
            file_reader: Optional function to read files (for sandbox access)

        Yields:
            Response chunks (includes Search/Replace blocks)
        """
        # Determine target and code to show
        if target_file and file_reader:
            code = file_reader(target_file)
            if code is None:
                yield f"Cannot read file: {target_file}"
                return
            target_label = target_file
        elif target_code:
            code = target_code
            target_label = "provided code"
        else:
            code = self._coder_output_context
            target_label = "coder_output"

        if not code:
            yield "No code available to edit. Generate some code first or specify a file."
            return

        # Show raw code (no line numbers) - content matching doesn't need them
        prompt = f"""File to edit: {target_label}

```
{code}
```

Edit Request: {instruction}

Provide your edits using Search/Replace blocks. Use "{target_label}" as the filename in your edit block.
Copy EXACT content for the SEARCH section."""

        # If there was a previous failure, include the feedback
        if error_feedback:
            prompt += f"""

PREVIOUS ATTEMPT FAILED:
{error_feedback}

Please correct your SEARCH content using the feedback above and try again."""

        for chunk in self.backend.stream(prompt, system=PLANNER_EDIT_SYSTEM):
            yield chunk

    def parse_search_replace_operations(self, response: str) -> list[SearchReplaceOperation]:
        """
        Parse Search/Replace operations from Planner's response.

        Supports both new Search/Replace format and legacy JSON format.

        Args:
            response: Planner's response text

        Returns:
            List of SearchReplaceOperation objects
        """
        # Try new Search/Replace format first
        operations = parse_search_replace_blocks(response)

        if operations:
            return operations

        # Fallback to legacy JSON format
        json_pattern = re.compile(
            r'\{[^{}]*"action"\s*:\s*"edit"[^{}]*\}',
            re.DOTALL
        )

        for match in json_pattern.finditer(response):
            try:
                data = json.loads(match.group())
                if data.get("action") == "edit":
                    op = SearchReplaceOperation(
                        target=data.get("file", "coder_output"),
                        search_content=data.get("old_content", ""),
                        replace_content=data.get("new_content", ""),
                        reason=data.get("reason", ""),
                    )
                    operations.append(op)
            except (json.JSONDecodeError, ValueError, KeyError):
                continue

        return operations

    def apply_edits(
        self,
        operations: list[SearchReplaceOperation],
    ) -> list[tuple[SearchReplaceOperation, bool, str]]:
        """
        Apply search/replace edit operations (with approval).

        Sync method — on_edit_request callback must be sync (see _execute_edit).

        Args:
            operations: List of SearchReplaceOperation to apply

        Returns:
            List of (operation, success, message) tuples
        """
        results = []

        for op in operations:
            if self.on_edit_request:
                success = self.on_edit_request(op)
                message = "Applied" if success else "Rejected or failed"
            else:
                success = False
                message = "No edit handler configured"

            results.append((op, success, message))

        return results

    def _add_line_numbers(self, code: str) -> str:
        """Add line numbers to code for reference."""
        lines = code.split("\n")
        max_width = len(str(len(lines)))
        numbered = []
        for i, line in enumerate(lines, 1):
            numbered.append(f"{i:>{max_width}}: {line}")
        return "\n".join(numbered)

    def get_context_summary(self) -> dict:
        """Get a summary of current context state."""
        return {
            "has_project_context": bool(self._project_context),
            "project_context_length": len(self._project_context),
            "has_coder_output": bool(self._coder_output_context),
            "coder_output_lines": len(self._coder_output_context.split("\n")) if self._coder_output_context else 0,
            "backend_history_messages": len(self.backend.get_history()),
        }

    def clear_context(self) -> None:
        """Clear all context and history."""
        self._project_context = ""
        self._coder_output_context = ""
        self.backend.clear_history()

    async def run_autonomous_loop(self, user_request: str) -> dict:
        """
        Run the autonomous Reason-Act-Observe loop for a request.
        
        Args:
            user_request: The user's high-level request
            
        Returns:
            Dictionary with result status and messages
        """
        return await self.agentic_loop.reason_act_observe(
            user_request=user_request,
            context=self._project_context
        )
