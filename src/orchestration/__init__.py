"""
Task orchestration system for multi-agent coordination.

Manages task decomposition, queuing, and execution flow between
Planner (planning) and Coder (implementation).
"""

from .task import Task, TaskStatus, TaskPriority, TaskDecomposition
from .orchestrator import TaskOrchestrator
from .prompts import PLANNER_DECOMPOSITION_SYSTEM, PLANNER_EDIT_SYSTEM, DEEPSEEK_EXPLORE_SYSTEM
from .editor import (
    CodeEditor,
    EditResult,
    SearchReplaceOperation,
    MatchResult,
    ContentMatcher,
    EditError,
    add_line_numbers,
    extract_lines,
    parse_search_replace_blocks,
    apply_with_indent_preservation,
    get_context_lines,
)
from .file_operations import (
    FileOperation,
    FileOpType,
    FileOpResult,
    FileOperationParser,
    FileOperationExecutor,
)
from .undo import UnifiedUndoStack, UndoEntry, SnapshotUndoStack, SnapshotEntry
from .pipeline import OperationPipeline, OperationRequest, OperationResult, OperationType, ApprovalPolicy

__all__ = [
    "Task",
    "TaskStatus",
    "TaskPriority",
    "TaskDecomposition",
    "TaskOrchestrator",
    "CodeEditor",
    "EditResult",
    "SearchReplaceOperation",
    "MatchResult",
    "ContentMatcher",
    "EditError",
    "add_line_numbers",
    "extract_lines",
    "parse_search_replace_blocks",
    "apply_with_indent_preservation",
    "get_context_lines",
    "PLANNER_DECOMPOSITION_SYSTEM",
    "PLANNER_EDIT_SYSTEM",
    "DEEPSEEK_EXPLORE_SYSTEM",
    "FileOperation",
    "FileOpType",
    "FileOpResult",
    "FileOperationParser",
    "FileOperationExecutor",
    "UnifiedUndoStack",
    "UndoEntry",
    "SnapshotUndoStack",
    "SnapshotEntry",
    "OperationPipeline",
    "OperationRequest",
    "OperationResult",
    "OperationType",
    "ApprovalPolicy",
]
