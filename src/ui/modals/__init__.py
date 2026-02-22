"""
Modal dialogs for the multi-agent TUI.
"""

from .approval_modal import (
    ApprovalModal,
    CommandApprovalModal,
    FileWriteApprovalModal,
    FileDeleteApprovalModal,
    BatchFileApprovalModal,
)
from .undo_modal import UndoConfirmationModal
from .indexing_modal import IndexApprovalModal

__all__ = [
    "ApprovalModal",
    "CommandApprovalModal",
    "FileWriteApprovalModal",
    "FileDeleteApprovalModal",
    "BatchFileApprovalModal",
    "UndoConfirmationModal",
    "IndexApprovalModal",
]
