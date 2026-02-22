import asyncio
import json
from enum import Enum
from typing import Dict, List, Optional, Callable, Awaitable
from dataclasses import dataclass

from src.sandbox.command_executor import SandboxCommandExecutor
from src.backends.openai_backend import FireworksBackend
from src.sandbox.tools import get_readonly_tools
from src.orchestration.pipeline import (
    OperationPipeline,
    OperationRequest,
    OperationType,
    ApprovalPolicy,
)

@dataclass
class AgentAction:
    tool_name: str
    arguments: Dict


class PlannerAgenticLoop:
    """
    Enhanced React-style loop for Planner with sandboxed execution.
    Uses read-only tools only — no edit_file in the auto loop.
    Routes bash through OperationPipeline.
    """

    def __init__(
        self,
        planner_backend: FireworksBackend,
        sandbox_executor: SandboxCommandExecutor,
        pipeline: Optional[OperationPipeline] = None,
        max_iterations: int = 15,
        on_log_activity: Optional[Callable[[str], None]] = None,
        on_action_start: Optional[Callable[[int, str, str], None]] = None,
        on_action_complete: Optional[Callable[[int, str, bool, str], None]] = None,
    ):
        self.planner = planner_backend
        self.cmd_executor = sandbox_executor
        self.pipeline = pipeline
        self.max_iterations = max_iterations
        self.iteration_count = 0
        self.context_memory: List[Dict] = []
        self.log = on_log_activity or (lambda msg: None)
        self.on_action_start = on_action_start
        self.on_action_complete = on_action_complete

    async def reason_act_observe(
        self,
        user_request: str,
        context: Optional[str] = None,
    ) -> Dict:
        """Core React loop: Reason -> Act -> Observe -> Repeat."""

        self.iteration_count = 0
        self.context_memory = [
            {
                "role": "user",
                "content": f"Task: {user_request}\nContext: {context or 'No additional context'}",
            }
        ]

        self.log(f"[bold]Starting autonomous loop[/] for: {user_request[:100]}")

        while self.iteration_count < self.max_iterations:
            self.iteration_count += 1
            self.log(f"[cyan]Iteration {self.iteration_count}/{self.max_iterations}[/]")

            # REASON: Planner decides next actions
            self.log("[dim]Reasoning...[/]")
            actions, text_response = await self._reason_phase()

            if text_response:
                self.log(f"[dim]{text_response[:100]}...[/]")

            if not actions:
                self.log("[bold yellow]No tools called. Finishing Task.[/]")
                return {
                    "status": "success",
                    "result": text_response or "Task complete.",
                    "iterations": self.iteration_count,
                }

            action = actions[0]

            self.log(
                f"[yellow]Action:[/] {action.tool_name} | "
                f"[dim]{json.dumps(action.arguments)[:50]}...[/]"
            )

            if self.on_action_start:
                self.on_action_start(
                    self.iteration_count,
                    action.tool_name,
                    json.dumps(action.arguments)[:100],
                )

            if action.tool_name == "ask_user":
                question = action.arguments.get("question", "Verification needed")
                self.log(f"[bold yellow]ASK_USER:[/] {question}")
                if self.on_action_complete:
                    self.on_action_complete(
                        self.iteration_count, "ask_user", True, question
                    )
                return {
                    "status": "needs_input",
                    "question": question,
                    "context": self._summarize_context(),
                }

            # ACT: Execute the action
            self.log(f"[blue]Executing:[/] {action.tool_name}")
            result = await self._act_phase(action)

            success = result.get("success", False)
            if success:
                self.log(f"[green]Success[/]")
            else:
                self.log(f"[red]Failed:[/] {result.get('stderr', result.get('error', 'Unknown error'))[:100]}")

            if self.on_action_complete:
                self.on_action_complete(
                    self.iteration_count,
                    action.tool_name,
                    success,
                    result.get("stdout", "")[:200] if success else result.get("stderr", "")[:200],
                )

            # OBSERVE: Process result
            result_output = result.get("stdout", "")
            result_error = result.get("stderr", "")

            observe_content = f"Action: {action.tool_name}\n"
            observe_content += f"Arguments: {json.dumps(action.arguments)}\n"
            observe_content += f"Success: {result.get('success', False)}\n"

            if result_output:
                if len(result_output) > 1500:
                    observe_content += f"Output (truncated):\n{result_output[:1500]}\n...[truncated]"
                else:
                    observe_content += f"Output:\n{result_output}"

            if result_error:
                observe_content += f"\nErrors:\n{result_error[:500]}"

            self.context_memory.append(
                {
                    "role": "assistant",
                    "content": observe_content,
                }
            )

            if result.get("success") is False:
                error_msg = result.get("stderr") or result.get("error") or "Unknown error"
                self.context_memory.append(
                    {
                        "role": "system",
                        "content": f"Previous action failed. Error: {error_msg}. Please adjust approach.",
                    }
                )

        self.log(f"[bold red]Max iterations reached[/] ({self.max_iterations})")
        return {
            "status": "max_iterations",
            "message": f"Reached max iterations ({self.max_iterations})",
            "context": self._summarize_context(),
        }

    async def _reason_phase(self) -> tuple[List[AgentAction], str]:
        """Planner analyzes situation and plans next action using read-only tools."""

        context_str = self._format_context_memory()
        if len(context_str) > 4000:
            context_str = context_str[-4000:]

        from src.prompts import AUTONOMOUS_LOOP_SYSTEM_PROMPT
        prompt = AUTONOMOUS_LOOP_SYSTEM_PROMPT.format(context_str=context_str)

        tool_calls = []
        text_response = ""

        async for chunk in self.planner.astream(prompt, tools=get_readonly_tools()):
            if isinstance(chunk, str):
                text_response += chunk
            elif isinstance(chunk, dict) and chunk.get("type") == "tool_call":
                tool_calls.append(chunk["tool_call"])

        actions = []
        for tc in tool_calls:
            try:
                args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"] else {}
                actions.append(AgentAction(
                    tool_name=tc["function"]["name"],
                    arguments=args
                ))
            except Exception as e:
                self.log(f"[red]Failed to parse tool arguments:[/] {e}")

        return actions, text_response

    async def _act_phase(self, action: AgentAction) -> Dict:
        """Execute the planned action. Routes through pipeline when available."""

        if action.tool_name == "execute_bash":
            cmd = action.arguments.get("command", "")
            if not cmd:
                return {"success": False, "error": "No command specified"}

            args = action.arguments.get("args", [])

            # Route through pipeline if available (pipeline is fully sync — no event loop needed)
            if self.pipeline:
                op_result = self.pipeline.execute(OperationRequest(
                    op_type=OperationType.BASH_READ,
                    source="auto",
                    command=cmd,
                    args=args,
                    cwd=action.arguments.get("cwd"),
                    approval_policy=ApprovalPolicy.NEVER,
                    record_undo=False,
                ))
                return {
                    "success": op_result.success,
                    "stdout": op_result.stdout,
                    "stderr": op_result.stderr,
                    "returncode": op_result.returncode,
                }

            # Fallback: direct executor (legacy path)
            full_cmd = f"{cmd} {' '.join(args)}".strip()
            self.log(f"[bold magenta]EXEC[/] {full_cmd}")
            result = await self.cmd_executor.execute(full_cmd, cwd=action.arguments.get("cwd"))
            status_color = "green" if result.get("success", False) else "red"
            self.log(f"[{status_color}]Return code: {result.get('returncode', -1)}[/]")
            return result

        elif action.tool_name == "semantic_search":
            query = action.arguments.get("query", "")
            self.log(f"[bold cyan]SEARCH[/] {query}")
            try:
                from src.sandbox.embeddings import CodebaseRAG
                rag = CodebaseRAG(".")
                results = rag.search(query)
                return {
                    "success": True,
                    "stdout": json.dumps(results, indent=2)
                }
            except Exception as e:
                return {"success": False, "error": f"Search failed: {e}"}

        # edit_file is NOT available in readonly tools — should never reach here
        return {"success": False, "error": f"Unknown tool: {action.tool_name}"}


    def _format_context_memory(self) -> str:
        """Format context memory with clear sections."""
        parts = []
        for entry in self.context_memory:
            role = entry['role'].upper()
            content = entry['content']

            if role == "USER":
                parts.append(f"=== USER REQUEST ===\n{content}")
            elif role == "ASSISTANT":
                if "Result:" in content:
                    parts.append(f"=== ACTION & RESULT ===\n{content}")
                else:
                    parts.append(f"=== YOUR PREVIOUS ACTION ===\n{content}")
            elif role == "SYSTEM":
                parts.append(f"=== SYSTEM NOTE ===\n{content}")

        return "\n\n".join(parts)

    def _summarize_context(self) -> str:
        return "Task in progress..."
