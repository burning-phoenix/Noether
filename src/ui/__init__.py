"""
Textual TUI for multi-agent orchestration.

Provides a two-tab interface:
- Tab 1 (Coder View): Real-time code output with line numbers
- Tab 2 (Task Manager): Chat with Planner + task queue
"""

from .app import MultiAgentApp
from .messages import (
    CoderRequest,
    CoderResponse,
    PlannerRequest,
    PlannerResponse,
    DeepSeekExploreRequest,
    DeepSeekExploreResponse,
    TaskQueueUpdated,
)

__all__ = [
    "MultiAgentApp",
    "CoderRequest",
    "CoderResponse",
    "PlannerRequest",
    "PlannerResponse",
    "DeepSeekExploreRequest",
    "DeepSeekExploreResponse",
    "TaskQueueUpdated",
]
