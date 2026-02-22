"""
Task data model for multi-agent orchestration.

Tasks are atomic units of work that can be executed by Coder
within its limited context window (4K tokens).
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


class TaskStatus(Enum):
    """Status of a task in the queue."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"  # Waiting on dependencies


class TaskPriority(Enum):
    """Priority levels for task scheduling."""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def from_value(cls, value: int) -> "TaskPriority":
        """Convert an int to TaskPriority, clamping to valid range."""
        clamped = max(1, min(4, int(value)))
        return cls(clamped)


@dataclass
class Task:
    """
    Atomic task unit for Coder execution.

    Each task is designed to be self-contained with all necessary
    context to fit within Coder's 4K token window.
    """

    # Core identification
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str = ""

    # Task type: "create" (new file via Coder) or "edit" (modify existing via Planner S/R)
    task_type: str = "create"
    target_file: str = ""       # For edit tasks: which file to modify
    search_hint: str = ""       # For edit tasks: what code to find

    # Context for execution (must fit in 4K tokens)
    context: str = ""
    expected_output: str = ""

    # Task metadata
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM

    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Results
    result: Optional[str] = None
    error: Optional[str] = None

    # Dependency management
    parent_task_id: Optional[str] = None  # For hierarchical decomposition
    dependencies: list[str] = field(default_factory=list)  # Task IDs that must complete first

    # Execution metadata
    attempts: int = 0
    max_attempts: int = 3

    def estimate_context_size(self) -> int:
        """
        Estimate tokens needed for this task.

        Uses rough approximation of 4 characters per token.
        """
        total_chars = len(self.description) + len(self.context) + len(self.expected_output)
        return total_chars // 4

    def can_execute(self, completed_task_ids: set[str]) -> bool:
        """
        Check if all dependencies are satisfied.

        Args:
            completed_task_ids: Set of task IDs that have completed

        Returns:
            True if all dependencies are met
        """
        return all(dep_id in completed_task_ids for dep_id in self.dependencies)

    def start(self) -> None:
        """Mark task as in progress."""
        self.status = TaskStatus.IN_PROGRESS
        self.started_at = datetime.now()
        self.attempts += 1

    def complete(self, result: str) -> None:
        """Mark task as completed with result."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now()
        self.result = result

    def fail(self, error: str) -> None:
        """Mark task as failed with error."""
        if self.attempts >= self.max_attempts:
            self.status = TaskStatus.FAILED
        else:
            self.status = TaskStatus.PENDING  # Allow retry
        self.error = error

    def block(self, reason: str) -> None:
        """Mark task as blocked."""
        self.status = TaskStatus.BLOCKED
        self.error = reason

    def to_prompt(self) -> str:
        """
        Convert task to a prompt for the appropriate agent.

        For "create" tasks: prompt for Coder (generate new files).
        For "edit" tasks: prompt for Planner (S/R blocks on existing files).

        Returns:
            Formatted prompt string
        """
        if self.task_type == "edit":
            parts = [f"## Edit Task: {self.description}"]
            if self.target_file:
                parts.append(f"\n### Target File\n{self.target_file}")
            if self.search_hint:
                parts.append(f"\n### Code to Find\n{self.search_hint}")
            if self.context:
                parts.append(f"\n### Context\n{self.context}")
            parts.append("\n### Instructions\nProvide Search/Replace blocks to make the required changes. Match existing code exactly.")
            return "\n".join(parts)

        # Default: create task for Coder
        parts = [f"## Task: {self.description}"]

        if self.context:
            parts.append(f"\n### Context\n{self.context}")

        if self.expected_output:
            parts.append(f"\n### Expected Output\n{self.expected_output}")

        parts.append("\n### Instructions\nProvide a complete implementation. Include all necessary code and brief explanations.")

        return "\n".join(parts)

    def to_dict(self) -> dict:
        """Convert task to dictionary for serialization."""
        d = {
            "id": self.id,
            "description": self.description,
            "task_type": self.task_type,
            "context": self.context,
            "expected_output": self.expected_output,
            "status": self.status.value,
            "priority": self.priority.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "result": self.result,
            "error": self.error,
            "parent_task_id": self.parent_task_id,
            "dependencies": self.dependencies,
            "attempts": self.attempts,
            "estimated_tokens": self.estimate_context_size(),
        }
        if self.target_file:
            d["target_file"] = self.target_file
        if self.search_hint:
            d["search_hint"] = self.search_hint
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """Create task from dictionary."""
        task = cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            description=data.get("description", ""),
            task_type=data.get("task_type", data.get("type", "create")),
            target_file=data.get("target_file", ""),
            search_hint=data.get("search_hint", ""),
            context=data.get("context", ""),
            expected_output=data.get("expected_output", ""),
            priority=TaskPriority.from_value(data.get("priority", 2)),
            parent_task_id=data.get("parent_task_id"),
            dependencies=data.get("dependencies", []),
        )

        if data.get("status"):
            task.status = TaskStatus(data["status"])

        return task


@dataclass
class TaskDecomposition:
    """
    Result of Planner's task decomposition.

    Contains the original request, reasoning, and generated subtasks.
    """

    original_request: str
    reasoning: str
    subtasks: list[Task]

    @property
    def estimated_total_tokens(self) -> int:
        """Total estimated tokens across all subtasks."""
        return sum(task.estimate_context_size() for task in self.subtasks)

    @property
    def task_count(self) -> int:
        """Number of subtasks."""
        return len(self.subtasks)

    def validate(self, max_tokens_per_task: int = 4096) -> list[str]:
        """
        Validate the decomposition.

        Args:
            max_tokens_per_task: Maximum tokens allowed per task

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        for task in self.subtasks:
            tokens = task.estimate_context_size()
            if tokens > max_tokens_per_task:
                errors.append(
                    f"Task '{task.id}' exceeds token limit: {tokens} > {max_tokens_per_task}"
                )

            # Check for circular dependencies
            if task.id in task.dependencies:
                errors.append(f"Task '{task.id}' depends on itself")

        # Check for missing dependencies
        task_ids = {t.id for t in self.subtasks}
        for task in self.subtasks:
            for dep_id in task.dependencies:
                if dep_id not in task_ids:
                    errors.append(
                        f"Task '{task.id}' depends on unknown task '{dep_id}'"
                    )

        return errors

    @staticmethod
    def _repair_truncated_json(json_str: str) -> str:
        """
        Attempt to repair truncated JSON from LLM output.

        Common truncation patterns:
        - Missing closing brackets/braces
        - Incomplete last object in array
        - Trailing comma before missing element
        """
        import re

        s = json_str.strip()
        if not s:
            return s

        # Count brackets to detect truncation
        open_braces = s.count('{') - s.count('}')
        open_brackets = s.count('[') - s.count(']')

        # If balanced, no repair needed
        if open_braces == 0 and open_brackets == 0:
            return s

        # Remove incomplete last object/element if array is truncated mid-object
        # Pattern: {..., { incomplete  -> remove the incomplete object
        if open_braces > 0:
            # Find the last complete object by finding last '}' and truncating incomplete part
            last_complete = s.rfind('}')
            if last_complete > 0:
                # Check if there's an incomplete object after
                after_last = s[last_complete+1:].strip()
                if after_last.startswith(','):
                    s = s[:last_complete+1]
                    open_braces = s.count('{') - s.count('}')
                    open_brackets = s.count('[') - s.count(']')

        # Remove trailing commas
        s = re.sub(r',\s*$', '', s)
        s = re.sub(r',\s*(\]|\})', r'\1', s)

        # Add missing closing brackets/braces
        s += ']' * open_brackets
        s += '}' * open_braces

        return s

    @classmethod
    def from_planner_response(cls, response: str, original_request: str) -> "TaskDecomposition":
        """
        Parse Planner's JSON response into a TaskDecomposition.

        Args:
            response: Planner's JSON response
            original_request: The original user request

        Returns:
            TaskDecomposition object
        """
        import json
        import re

        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", response)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to find raw JSON (look for object start)
            brace_match = re.search(r'\{[\s\S]*', response)
            json_str = brace_match.group(0) if brace_match else response

        # First attempt: parse as-is
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # Second attempt: repair truncated JSON
            repaired = cls._repair_truncated_json(json_str)
            try:
                data = json.loads(repaired)
            except json.JSONDecodeError as e:
                # Final fallback: return a single task
                return cls(
                    original_request=original_request,
                    reasoning=f"Failed to parse decomposition: {e}",
                    subtasks=[
                        Task(
                            description=original_request,
                            context="Original request could not be decomposed.",
                            priority=TaskPriority.MEDIUM,
                        )
                    ],
                )

        reasoning = data.get("reasoning", "No reasoning provided")
        subtasks = []

        for i, subtask_data in enumerate(data.get("subtasks", [])):
            task = Task(
                description=subtask_data.get("description", f"Subtask {i+1}"),
                task_type=subtask_data.get("type", subtask_data.get("task_type", "create")),
                target_file=subtask_data.get("target_file", ""),
                search_hint=subtask_data.get("search_hint", ""),
                context=subtask_data.get("context", ""),
                expected_output=subtask_data.get("expected_output", ""),
                priority=TaskPriority.from_value(subtask_data.get("priority", 2)),
                dependencies=subtask_data.get("dependencies", []),
            )
            subtasks.append(task)

        return cls(
            original_request=original_request,
            reasoning=reasoning,
            subtasks=subtasks,
        )
