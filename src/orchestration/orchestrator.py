"""
Task orchestrator for managing the execution queue.

Handles task scheduling, dependency resolution, and execution flow
between Planner (planning) and Coder (implementation).
"""

from collections import deque
from typing import Optional, Callable, Awaitable
from datetime import datetime

from .task import Task, TaskStatus, TaskPriority, TaskDecomposition


class TaskOrchestrator:
    """
    Manages the task queue and execution flow.

    Responsibilities:
    - Maintain task queue with priority ordering
    - Resolve dependencies before execution
    - Track completed and failed tasks
    - Provide callbacks for UI updates
    """

    MAX_CODER_CONTEXT = 4096  # Coder's effective context limit in tokens

    def __init__(
        self,
        on_task_started: Optional[Callable[[Task], None]] = None,
        on_task_completed: Optional[Callable[[Task], None]] = None,
        on_task_failed: Optional[Callable[[Task], None]] = None,
        on_queue_changed: Optional[Callable[[], None]] = None,
    ):
        """
        Initialize the orchestrator.

        Args:
            on_task_started: Callback when a task starts
            on_task_completed: Callback when a task completes
            on_task_failed: Callback when a task fails
            on_queue_changed: Callback when queue changes
        """
        self._task_queue: deque[Task] = deque()
        self._completed_tasks: dict[str, Task] = {}
        self._failed_tasks: dict[str, Task] = {}
        self._current_task: Optional[Task] = None

        # Callbacks
        self._on_task_started = on_task_started
        self._on_task_completed = on_task_completed
        self._on_task_failed = on_task_failed
        self._on_queue_changed = on_queue_changed

    @property
    def current_task(self) -> Optional[Task]:
        """Get the currently executing task."""
        return self._current_task

    @property
    def queue_size(self) -> int:
        """Get the number of tasks in the queue."""
        return len(self._task_queue)

    @property
    def completed_count(self) -> int:
        """Get the number of completed tasks."""
        return len(self._completed_tasks)

    @property
    def failed_count(self) -> int:
        """Get the number of failed tasks."""
        return len(self._failed_tasks)

    def add_task(self, task: Task) -> None:
        """
        Add a single task to the queue.

        Args:
            task: The task to add

        Raises:
            ValueError: If task exceeds context limit
        """
        if task.estimate_context_size() > self.MAX_CODER_CONTEXT:
            raise ValueError(
                f"Task '{task.id}' exceeds context limit: "
                f"{task.estimate_context_size()} > {self.MAX_CODER_CONTEXT} tokens. "
                "Task needs further decomposition."
            )

        self._task_queue.append(task)
        self._sort_queue()
        self._notify_queue_changed()

    def add_tasks(self, tasks: list[Task]) -> list[str]:
        """
        Add multiple tasks to the queue.

        Args:
            tasks: List of tasks to add

        Returns:
            List of task IDs that were rejected (too large)
        """
        rejected = []

        for task in tasks:
            try:
                self.add_task(task)
            except ValueError:
                rejected.append(task.id)

        return rejected

    def add_decomposition(self, decomposition: TaskDecomposition) -> list[str]:
        """
        Add all tasks from a decomposition result.

        Args:
            decomposition: The task decomposition from Planner

        Returns:
            List of validation errors (empty if all valid)
        """
        errors = decomposition.validate(self.MAX_CODER_CONTEXT)
        if errors:
            return errors

        rejected = self.add_tasks(decomposition.subtasks)
        return [f"Task {tid} exceeds context limit" for tid in rejected]

    def _sort_queue(self) -> None:
        """Sort queue by priority and dependency order."""
        tasks = list(self._task_queue)
        completed_ids = set(self._completed_tasks.keys())

        # Topological sort with priority consideration
        sorted_tasks = []
        remaining = tasks.copy()

        while remaining:
            # Find tasks with satisfied dependencies
            ready = [t for t in remaining if t.can_execute(completed_ids)]

            if not ready:
                # No ready tasks - add remaining (blocked) tasks
                sorted_tasks.extend(remaining)
                break

            # Sort ready tasks by priority (highest first)
            ready.sort(key=lambda t: t.priority.value, reverse=True)

            for task in ready:
                sorted_tasks.append(task)
                completed_ids.add(task.id)  # Assume will complete for sorting
                remaining.remove(task)

        self._task_queue = deque(sorted_tasks)

    def get_next_task(self) -> Optional[Task]:
        """
        Get the next executable task from the queue.

        Returns:
            The next task, or None if queue is empty or all blocked
        """
        if self._current_task:
            return None  # Already executing a task

        completed_ids = set(self._completed_tasks.keys())

        for task in self._task_queue:
            if task.can_execute(completed_ids):
                self._task_queue.remove(task)
                self._current_task = task
                task.start()

                if self._on_task_started:
                    self._on_task_started(task)

                self._notify_queue_changed()
                return task

        return None

    def complete_current_task(self, result: str) -> Optional[Task]:
        """
        Mark the current task as completed.

        Args:
            result: The task result/output

        Returns:
            The completed task, or None if no current task
        """
        if not self._current_task:
            return None

        task = self._current_task
        task.complete(result)
        self._completed_tasks[task.id] = task
        self._current_task = None

        if self._on_task_completed:
            self._on_task_completed(task)

        self._notify_queue_changed()
        return task

    def fail_current_task(self, error: str) -> Optional[Task]:
        """
        Mark the current task as failed.

        Args:
            error: The error message

        Returns:
            The failed task, or None if no current task
        """
        if not self._current_task:
            return None

        task = self._current_task
        task.fail(error)

        if task.status == TaskStatus.FAILED:
            # Max attempts reached
            self._failed_tasks[task.id] = task
            self._current_task = None

            if self._on_task_failed:
                self._on_task_failed(task)
        else:
            # Can retry - put back in queue
            self._task_queue.appendleft(task)
            self._current_task = None

        self._notify_queue_changed()
        return task

    def cancel_current_task(self) -> Optional[Task]:
        """
        Cancel the current task without marking as failed.

        Returns:
            The cancelled task, or None if no current task
        """
        if not self._current_task:
            return None

        task = self._current_task
        task.status = TaskStatus.PENDING
        self._task_queue.appendleft(task)
        self._current_task = None

        self._notify_queue_changed()
        return task

    def remove_task(self, task_id: str) -> bool:
        """
        Remove a task from the queue.

        Args:
            task_id: The ID of the task to remove

        Returns:
            True if task was found and removed
        """
        for task in self._task_queue:
            if task.id == task_id:
                self._task_queue.remove(task)
                self._notify_queue_changed()
                return True
        return False

    def clear_queue(self) -> int:
        """
        Clear all pending tasks from the queue.

        Returns:
            Number of tasks cleared
        """
        count = len(self._task_queue)
        self._task_queue.clear()
        self._notify_queue_changed()
        return count

    def get_task(self, task_id: str) -> Optional[Task]:
        """
        Get a task by ID (from queue, completed, or failed).

        Args:
            task_id: The task ID

        Returns:
            The task, or None if not found
        """
        # Check current task
        if self._current_task and self._current_task.id == task_id:
            return self._current_task

        # Check queue
        for task in self._task_queue:
            if task.id == task_id:
                return task

        # Check completed
        if task_id in self._completed_tasks:
            return self._completed_tasks[task_id]

        # Check failed
        if task_id in self._failed_tasks:
            return self._failed_tasks[task_id]

        return None

    def get_queue_snapshot(self) -> list[dict]:
        """
        Get current queue state for UI display.
        
        Returns:
            List of task dictionaries with display info
        """
        return self.get_full_task_snapshot()

    def get_full_task_snapshot(self) -> list[dict]:
        """
        Get snapshot of ALL tasks (history + queue) for UI display.
        Sorted by priority then creation time.
        """
        tasks = []

        # Helper to format task
        def format_task(t: Task, is_current: bool = False):
            return {
                "id": t.id,
                "status": t.status.value,
                "description": self._truncate(t.description, 50),
                "priority": t.priority.value,
                "context_size": t.estimate_context_size(),
                "is_current": is_current,
                "created_at": t.created_at.timestamp() if t.created_at else 0
            }

        # 1. Completed
        for t in self._completed_tasks.values():
            tasks.append(format_task(t))
            
        # 2. Failed
        for t in self._failed_tasks.values():
            tasks.append(format_task(t))
            
        # 3. Current
        if self._current_task:
            tasks.append(format_task(self._current_task, is_current=True))
            
        # 4. Queued
        for t in self._task_queue:
            tasks.append(format_task(t))
            
        # Sort by status priority (Running > Pending > Failed > Done) is hard to generic sort
        # So we just rely on the list order or sort by creation if needed.
        # Let's sort by creation time to show history logically
        # But maybe we want the "Active" stuff at top?
        # For a checklist, chronological usually makes sense, or reverse chronological.
        # Let's do reverse chronological (newest first) for now.
        tasks.sort(key=lambda x: x["created_at"], reverse=True)
        
        return tasks

    def get_statistics(self) -> dict:
        """
        Get orchestrator statistics.

        Returns:
            Dictionary with queue statistics
        """
        return {
            "queue_size": len(self._task_queue),
            "completed_count": len(self._completed_tasks),
            "failed_count": len(self._failed_tasks),
            "has_current": self._current_task is not None,
            "total_queued_tokens": sum(
                t.estimate_context_size() for t in self._task_queue
            ),
        }

    def _truncate(self, text: str, max_length: int) -> str:
        """Truncate text with ellipsis."""
        if len(text) <= max_length:
            return text
        return text[: max_length - 3] + "..."

    def _notify_queue_changed(self) -> None:
        """Notify listeners that the queue changed."""
        if self._on_queue_changed:
            self._on_queue_changed()
