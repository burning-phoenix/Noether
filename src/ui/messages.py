"""
Custom Textual messages for inter-component communication.

These messages allow different parts of the UI to communicate
without tight coupling.
"""

from textual.message import Message
from typing import Optional


class CoderRequest(Message):
    """Request to send a prompt to Coder."""

    def __init__(self, prompt: str, task_id: Optional[str] = None) -> None:
        self.prompt = prompt
        self.task_id = task_id
        super().__init__()


class CoderResponse(Message):
    """Streaming response chunk from Coder."""

    def __init__(self, chunk: str, is_complete: bool = False, is_start: bool = False, status: Optional[str] = None, task_id: Optional[str] = None) -> None:
        self.chunk = chunk
        self.is_complete = is_complete
        self.is_start = is_start
        self.status = status
        self.task_id = task_id
        super().__init__()


class PlannerRequest(Message):
    """Request to send a prompt to Planner."""

    def __init__(self, prompt: str, request_type: str = "chat") -> None:
        self.prompt = prompt
        self.request_type = request_type  # "chat", "decompose", "edit"
        super().__init__()


class PlannerResponse(Message):
    """Streaming response chunk from Planner."""

    def __init__(self, chunk: str, is_complete: bool = False, is_start: bool = False, response_type: str = "chat") -> None:
        self.chunk = chunk
        self.is_complete = is_complete
        self.is_start = is_start
        self.response_type = response_type
        super().__init__()

class PendingFileOperations(Message):
    """Event emitting file operations that require user approval."""
    def __init__(self, file_ops: list, task_id: Optional[str] = None) -> None:
        self.file_ops = file_ops
        self.task_id = task_id
        super().__init__()

class ProcessDecomposition(Message):
    """Event to trigger app to process task decomposition."""
    def __init__(self, response: str, prompt: str) -> None:
        self.response = response
        self.prompt = prompt
        super().__init__()

class ScheduleCommandApproval(Message):
    """Event to trigger app to schedule a command approval modal."""
    def __init__(self, command: str) -> None:
        self.command = command
        super().__init__()


class DeepSeekExploreRequest(Message):
    """Request to run DeepSeek explore."""

    def __init__(self, explore_type: str = "architecture", query: Optional[str] = None, files: Optional[list[str]] = None) -> None:
        self.explore_type = explore_type
        self.query = query
        self.files = files or []
        super().__init__()


class DeepSeekExploreResponse(Message):
    """Response from DeepSeek explore."""

    def __init__(self, chunk: str, is_complete: bool = False) -> None:
        self.chunk = chunk
        self.is_complete = is_complete
        super().__init__()


class TaskQueueUpdated(Message):
    """Notification that the task queue has changed."""

    def __init__(self, queue_size: int, current_task_id: Optional[str] = None) -> None:
        self.queue_size = queue_size
        self.current_task_id = current_task_id
        super().__init__()


class TaskCompleted(Message):
    """Notification that a task has completed."""

    def __init__(self, task_id: str, result: str) -> None:
        self.task_id = task_id
        self.result = result
        super().__init__()


class TaskFailed(Message):
    """Notification that a task has failed."""

    def __init__(self, task_id: str, error: str) -> None:
        self.task_id = task_id
        self.error = error
        super().__init__()


class EditRequest(Message):
    """Request to edit code at specific lines (legacy format)."""

    def __init__(
        self,
        target: str,  # "coder_output" or file path
        line_start: int,
        line_end: int,
        old_content: str,
        new_content: str,
        reason: str,
    ) -> None:
        self.target = target
        self.line_start = line_start
        self.line_end = line_end
        self.old_content = old_content
        self.new_content = new_content
        self.reason = reason
        super().__init__()


class SearchReplaceRequest(Message):
    """Request to edit code using content-based search/replace."""

    def __init__(
        self,
        target: str,  # "coder_output" or file path
        search_content: str,
        replace_content: str,
        reason: str,
    ) -> None:
        self.target = target
        self.search_content = search_content
        self.replace_content = replace_content
        self.reason = reason
        super().__init__()


class EditFeedbackMessage(Message):
    """Feedback message for failed edit operations."""

    def __init__(
        self,
        target: str,
        error_type: str,  # "no_match", "ambiguous_match", "apply_failed"
        feedback: str,  # Formatted feedback for LLM
        original_search: str,
        closest_match: Optional[str] = None,
        similarity: float = 0.0,
    ) -> None:
        self.target = target
        self.error_type = error_type
        self.feedback = feedback
        self.original_search = original_search
        self.closest_match = closest_match
        self.similarity = similarity
        super().__init__()


class EditResult(Message):
    """Result of an edit operation."""

    def __init__(self, success: bool, message: str, target: str) -> None:
        self.success = success
        self.message = message
        self.target = target
        super().__init__()


class SandboxApprovalRequest(Message):
    """Request user approval for a sandboxed operation."""

    def __init__(self, command: str, description: str, risk_level: str) -> None:
        self.command = command
        self.description = description
        self.risk_level = risk_level
        super().__init__()


class SandboxApprovalResponse(Message):
    """User's response to sandbox approval request."""

    def __init__(self, approved: bool, command: str) -> None:
        self.approved = approved
        self.command = command
        super().__init__()


class ModeSwitch(Message):
    """Request to switch between fast and local mode."""

    def __init__(self, fast_mode: bool) -> None:
        self.fast_mode = fast_mode
        super().__init__()


class ConfirmScope(Message):
    """User confirms the discussed scope, triggering decomposition."""

    def __init__(self, scope_summary: str = "") -> None:
        self.scope_summary = scope_summary
        super().__init__()


class ExecuteTasks(Message):
    """Request to execute all pending tasks."""

    def __init__(self, auto_run: bool = True) -> None:
        self.auto_run = auto_run
        super().__init__()


class ExploreAndAddContext(Message):
    """Request to explore codebase via agentic loop and add result to Planner context."""

    def __init__(self, query: str = "architecture") -> None:
        self.query = query  # Natural language description of what to explore
        super().__init__()


class PlannerExploreRequest(Message):
    """Request Planner to explore/analyze files directly (without DeepSeek).

    This is different from ExploreAndAddContext which uses DeepSeek.
    Planner explores files directly to save context and be explicit about the analysis.
    """

    def __init__(self, files: list[str], query: Optional[str] = None) -> None:
        self.files = files
        self.query = query
        super().__init__()


class StartAutonomousLoop(Message):
    """Request to start Planner's autonomous Reason-Act-Observe loop."""

    def __init__(self, task: str, context: Optional[str] = None) -> None:
        self.task = task
        self.context = context
        super().__init__()


class AutonomousLoopUpdate(Message):
    """Update from the autonomous loop (iteration progress, actions, etc.)."""

    def __init__(
        self,
        iteration: int,
        action_type: str,
        action_detail: str,
        is_complete: bool = False,
        result: Optional[str] = None,
    ) -> None:
        self.iteration = iteration
        self.action_type = action_type
        self.action_detail = action_detail
        self.is_complete = is_complete
        self.result = result
        super().__init__()


class PlannerModeSwitch(Message):
    """Request to switch Planner operational mode."""

    def __init__(self, mode: str, sub_mode: Optional[str] = None) -> None:
        self.mode = mode  # "go" or "plan"
        self.sub_mode = sub_mode  # "maintainable" or "discovery" (for plan mode)
        super().__init__()


class TokenUsageUpdate(Message):
    """Token usage information from a backend call."""

    def __init__(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        model: str,
    ) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.model = model
        super().__init__()


class UndoRequest(Message):
    """Request to undo the last operation."""
    pass


class UndoComplete(Message):
    """Result of an undo operation."""

    def __init__(self, success: bool, description: str) -> None:
        self.success = success
        self.description = description
        super().__init__()


class ClearContext(Message):
    """User wants to reset all agent state."""
    pass


class ProviderChanged(Message):
    """Fired when the user saves settings with a new provider, API key, or models."""
    def __init__(self, provider: str, fireworks_key: str, openrouter_key: str, chat_model: str, coder_model: str, explorer_model: str) -> None:
        self.provider = provider
        self.fireworks_key = fireworks_key
        self.openrouter_key = openrouter_key
        self.chat_model = chat_model
        self.coder_model = coder_model
        self.explorer_model = explorer_model
        super().__init__()

