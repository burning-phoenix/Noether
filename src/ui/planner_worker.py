"""
Planner worker — extracted from app.py.

Handles Planner inference, command parsing, task decomposition,
and command execution/analysis.
"""

import re
import logging
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger("noether.planner")


class PlannerWorker:
    """Manages Planner inference and command parsing.

    Pure-logic class — no Textual imports, no @work decorators.
    app.py creates thin wrappers that delegate here.
    """

    def __init__(
        self,
        planner_agent,
        orchestrator,
        edit_handler=None,
    ):
        self.planner_agent = planner_agent
        self.orchestrator = orchestrator
        self.edit_handler = edit_handler

    def stream_chat(
        self,
        prompt: str,
        request_type: str,
        on_chunk: Optional[Callable[[str], None]] = None,
        coder_context: str = "",
        sandbox=None,
    ) -> str:
        """Stream Planner inference and return full response.

        Args:
            prompt: User prompt (already enriched with file context)
            request_type: 'chat', 'decompose', or 'edit'
            on_chunk: Callback for each output chunk
            coder_context: Current Coder output for context
            sandbox: FileSystemSandbox for file reading

        Returns:
            Full response text
        """
        # Update Planner with current Coder output context
        if self.planner_agent:
            self.planner_agent.set_coder_output_context(coder_context)

        full_response = ""
        self.last_tool_calls = []

        if self.planner_agent and request_type == "decompose":
            for chunk in self.planner_agent.decompose_task(prompt):
                full_response += chunk
                if on_chunk:
                    on_chunk(chunk)
        elif self.planner_agent and request_type == "edit":
            target_file = self._extract_file_path(prompt)
            file_reader = sandbox.safe_read if sandbox else None

            for chunk in self.planner_agent.request_edit(
                instruction=prompt,
                target_file=target_file,
                file_reader=file_reader,
            ):
                full_response += chunk
                if on_chunk:
                    on_chunk(chunk)
        else:
            # Regular chat
            if self.planner_agent:
                for chunk in self.planner_agent.chat(prompt):
                    if isinstance(chunk, str):
                        full_response += chunk
                        if on_chunk:
                            on_chunk(chunk)
                    elif isinstance(chunk, dict) and chunk.get("type") == "tool_call":
                        self.last_tool_calls.append(chunk["tool_call"])

        return full_response

    def process_tool_calls(self, pipeline=None) -> list:
        """Process any tool calls from the last chat response through the pipeline.

        Args:
            pipeline: OperationPipeline to route operations through

        Returns:
            List of OperationResult objects
        """
        if not hasattr(self, "last_tool_calls") or not self.last_tool_calls:
            return []

        import json
        from ..orchestration.pipeline import (
            OperationRequest,
            OperationType,
            ApprovalPolicy,
        )

        results = []
        for tc in self.last_tool_calls:
            fn_name = tc.get("function", {}).get("name")
            args_str = tc.get("function", {}).get("arguments", "{}")
            try:
                args = json.loads(args_str) if args_str else {}
            except Exception:
                args = {}

            if fn_name == "execute_bash" and pipeline:
                raw_cmd = args.get("command", "")
                cmd_args = args.get("args", [])
                op_result = pipeline.execute(OperationRequest(
                    op_type=OperationType.BASH_WRITE,
                    source="chat",
                    command=raw_cmd,
                    args=cmd_args,
                    cwd=args.get("cwd"),
                    approval_policy=ApprovalPolicy.ALWAYS,
                    record_undo=False,
                ))
                results.append(op_result)

            elif fn_name == "edit_file" and pipeline:
                target = args.get("target_file", "")
                chunks = args.get("search_replace_chunks", [])
                for chunk in chunks:
                    search = chunk.get("search", "")
                    replace = chunk.get("replace", "")
                    op_result = pipeline.execute(OperationRequest(
                        op_type=OperationType.FILE_EDIT,
                        source="chat",
                        target_file=target,
                        search_content=search,
                        replace_content=replace,
                        reason=f"Edit {target}",
                        approval_policy=ApprovalPolicy.ALWAYS,
                    ))
                    results.append(op_result)

            elif fn_name == "semantic_search":
                # Handle directly — no pipeline needed
                query = args.get("query", "")
                if query:
                    try:
                        from ..sandbox.embeddings import CodebaseRAG
                        rag = CodebaseRAG(".")
                        search_results = rag.search(query)
                        import json
                        results.append({
                            "type": "semantic_search", 
                            "query": query, 
                            "success": True,
                            "stdout": json.dumps(search_results, indent=2)
                        })
                    except Exception as e:
                        results.append({
                            "type": "semantic_search",
                            "query": query,
                            "success": False,
                            "stderr": str(e)
                        })

        self.last_tool_calls = []
        return results

    def parse_decomposition(self, response: str, original_request: str) -> tuple:
        """Parse a task decomposition response from Planner.

        Returns:
            (decomposition, errors) tuple — errors may be empty list
        """
        from ..orchestration import TaskDecomposition

        decomposition = TaskDecomposition.from_planner_response(
            response, original_request
        )
        errors = self.orchestrator.add_decomposition(decomposition)
        return decomposition, errors

    def parse_edit_operations(self, response: str) -> list:
        """Parse Search/Replace operations from Planner's edit response."""
        if not self.planner_agent:
            return []
        return self.planner_agent.parse_search_replace_operations(response)

    def check_for_commands(self, response: str) -> list[dict]:
        """Check text for user-facing chat commands (no /exec text parsing).

        Tool calls (execute_bash, edit_file) are now handled by
        process_tool_calls() through the pipeline, not here.

        Returns:
            List of command dicts with keys: type, command, args
        """
        commands = []

        # Line-by-line scan for chat commands only
        for line in response.splitlines():
            line = line.strip()

            if line.startswith("/explore-add"):
                query = line[len("/explore-add"):].strip()
                if query:
                    commands.append({
                        "type": "explore-add",
                        "query": query,
                    })
            elif line.startswith("/run"):
                commands.append({"type": "run"})

        if commands:
            logger.info("Detected %d command(s) in Planner response", len(commands))

        return commands

    @staticmethod
    def _parse_explore_args(args: list[str]) -> tuple[str, list[str]]:
        """Parse /explore arguments into (explore_type, files)."""
        from ..agents.explore_agent import ExploreAgent

        files = []
        explore_type = "architecture"
        for arg in args:
            if arg in ExploreAgent.EXPLORE_TYPES:
                explore_type = arg
            else:
                files.append(arg)
        return explore_type, files

    @staticmethod
    def _extract_file_path(text: str) -> Optional[str]:
        """Extract a file path from user text."""
        import re

        # Look for common file path patterns
        patterns = [
            r'`([^`]+\.\w+)`',           # backtick-quoted path
            r'"([^"]+\.\w+)"',            # double-quoted path
            r"'([^']+\.\w+)'",            # single-quoted path
            r'\b(\S+\.\w{1,5})\b',        # bare filename with extension
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                candidate = match.group(1)
                # Filter out URLs and other non-paths
                if not candidate.startswith(('http://', 'https://', '//')):
                    return candidate

        return None
