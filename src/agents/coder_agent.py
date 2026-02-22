"""
Coder Agent for code generation and implementation.

Coder3-Coder-30B runs locally and excels at:
- Writing clean, efficient code
- Implementing specific tasks with provided context
- Following patterns from context examples
"""

from typing import Iterator, Optional, Callable
from dataclasses import dataclass

from ..backends.base import LLMBackend
from ..orchestration import Task, TaskOrchestrator


@dataclass
class GenerationResult:
    """Result of a code generation."""
    content: str
    task_id: Optional[str]
    tokens_generated: int
    success: bool
    error: Optional[str] = None


class CoderAgent:
    """
    Agent for code generation using local Coder3-Coder-30B.

    Responsibilities:
    - Execute atomic tasks from the queue
    - Stream code generation to the UI
    - Track generation state and results
    """

    def __init__(
        self,
        backend: LLMBackend,
        orchestrator: Optional[TaskOrchestrator] = None,
        on_generation_start: Optional[Callable[[str], None]] = None,
        on_generation_chunk: Optional[Callable[[str], None]] = None,
        on_generation_complete: Optional[Callable[[GenerationResult], None]] = None,
    ):
        """
        Initialize the Coder agent.

        Args:
            backend: LLMBackend for inference (local or API)
            orchestrator: Task orchestrator for queue management
            on_generation_start: Callback when generation starts
            on_generation_chunk: Callback for each generated chunk
            on_generation_complete: Callback when generation completes
        """
        self.backend = backend
        self.orchestrator = orchestrator
        self.on_generation_start = on_generation_start
        self.on_generation_chunk = on_generation_chunk
        self.on_generation_complete = on_generation_complete

        # State
        self._is_generating = False
        self._current_task: Optional[Task] = None
        self._last_result: Optional[GenerationResult] = None

    @property
    def is_generating(self) -> bool:
        """Check if currently generating."""
        return self._is_generating

    @property
    def current_task(self) -> Optional[Task]:
        """Get the current task being processed."""
        return self._current_task

    @property
    def last_result(self) -> Optional[GenerationResult]:
        """Get the last generation result."""
        return self._last_result

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Iterator[str]:
        """
        Generate code from a prompt.

        Args:
            prompt: The prompt to generate from
            system: Optional system prompt override
            task_id: Optional task ID for tracking

        Yields:
            Generated text chunks
        """
        if self._is_generating:
            raise RuntimeError("Already generating. Cancel or wait for completion.")

        self._is_generating = True
        self._current_task = None

        if self.on_generation_start:
            self.on_generation_start(task_id or "direct")

        content = ""
        tokens = 0
        error = None

        try:
            for chunk in self.backend.stream(prompt, system=system):
                content += chunk
                tokens += 1  # Rough estimate
                if self.on_generation_chunk:
                    self.on_generation_chunk(chunk)
                yield chunk

        except Exception as e:
            error = str(e)
            raise

        finally:
            self._is_generating = False
            result = GenerationResult(
                content=content,
                task_id=task_id,
                tokens_generated=tokens,
                success=error is None,
                error=error,
            )
            self._last_result = result

            if self.on_generation_complete:
                self.on_generation_complete(result)

    def execute_task(self, task: Task) -> Iterator[str]:
        """
        Execute a task from the queue.

        Args:
            task: The task to execute

        Yields:
            Generated text chunks
        """
        if self._is_generating:
            raise RuntimeError("Already generating. Cancel or wait for completion.")

        self._current_task = task
        task.start()

        try:
            prompt = task.to_prompt()

            for chunk in self.generate(prompt, task_id=task.id):
                yield chunk

            # Mark task complete
            if self.orchestrator and self._last_result:
                self.orchestrator.complete_current_task(self._last_result.content)

        except Exception as e:
            # Mark task failed
            if self.orchestrator:
                self.orchestrator.fail_current_task(str(e))
            raise

        finally:
            self._current_task = None

    def process_queue(self) -> Iterator[tuple[Task, Iterator[str]]]:
        """
        Process tasks from the queue.

        Yields:
            (task, chunk_iterator) tuples for each task
        """
        if not self.orchestrator:
            return

        while True:
            task = self.orchestrator.get_next_task()
            if not task:
                break

            yield task, self.execute_task(task)

    def cancel_generation(self) -> bool:
        """
        Cancel the current generation.

        Returns:
            True if generation was cancelled
        """
        if not self._is_generating:
            return False

        self._is_generating = False

        if self._current_task and self.orchestrator:
            self.orchestrator.cancel_current_task()

        self._current_task = None
        return True

    def get_status(self) -> dict:
        """Get current agent status."""
        return {
            "is_generating": self._is_generating,
            "current_task_id": self._current_task.id if self._current_task else None,
            "has_last_result": self._last_result is not None,
            "last_result_success": self._last_result.success if self._last_result else None,
            "backend_context": self.backend.get_context_info(),
        }
