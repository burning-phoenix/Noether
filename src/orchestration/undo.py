"""
Snapshot-based undo stack for file operations.

Stores full file content snapshots before each operation,
enabling reliable undo by restoring the exact previous state.

Thread-safe: All operations are protected by a lock since the stack
is accessed from both worker threads (via @work decorators) and the
main event loop.
"""

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional


@dataclass
class SnapshotEntry:
    """A single undoable operation backed by a full file snapshot."""
    path: str
    content_before: Optional[str]  # None = file didn't exist (undo = delete)
    timestamp: float = field(default_factory=time.time)
    description: str = ""


class SnapshotUndoStack:
    """
    Thread-safe LIFO stack of snapshot-based undo entries.

    Single push_snapshot() replaces the old push_edit/push_file_create/push_file_write.
    Undo = restore content_before (or delete if None).
    """

    MAX_SIZE = 50

    def __init__(self):
        self._stack: list[SnapshotEntry] = []
        self._lock = Lock()

    def push_snapshot(
        self,
        path: str,
        content_before: Optional[str],
        description: str = "",
    ) -> None:
        """Record a file snapshot for potential undo.

        Args:
            path: File path that was modified
            content_before: Full file content before the operation.
                            None means the file didn't exist (undo = delete).
            description: Human-readable description
        """
        entry = SnapshotEntry(
            path=path,
            content_before=content_before,
            description=description or f"Modify {path}",
        )
        with self._lock:
            self._push_unlocked(entry)

    def peek(self) -> Optional[SnapshotEntry]:
        """View the top entry without removing it."""
        with self._lock:
            return self._stack[-1] if self._stack else None

    def pop(self) -> Optional[SnapshotEntry]:
        """Remove and return the top entry."""
        with self._lock:
            return self._stack.pop() if self._stack else None

    def get_preview_text(self) -> str:
        """Get a human-readable description of the next undo."""
        with self._lock:
            entry = self._stack[-1] if self._stack else None

        if not entry:
            return "Nothing to undo"

        if entry.content_before is None:
            return f"Delete created file: {entry.path}"
        return f"Revert {entry.path} to previous content"

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._stack) == 0

    def size(self) -> int:
        with self._lock:
            return len(self._stack)

    def _push_unlocked(self, entry: SnapshotEntry) -> None:
        """Push an entry, dropping oldest if at capacity. Must hold lock."""
        if len(self._stack) >= self.MAX_SIZE:
            self._stack.pop(0)
        self._stack.append(entry)


# ---------------------------------------------------------------------------
# Backward-compatible aliases
# ---------------------------------------------------------------------------
# These allow existing code (tests, imports) to keep working during migration.


@dataclass
class UndoEntry:
    """Legacy undo entry — wraps SnapshotEntry interface for compatibility."""
    action_type: str  # "edit", "file_create", "file_write"
    timestamp: float = field(default_factory=time.time)
    description: str = ""

    # Legacy edit fields
    edit_target: Optional[str] = None
    edit_search_content: Optional[str] = None
    edit_replace_content: Optional[str] = None

    # Legacy file fields
    file_path: Optional[str] = None
    file_previous_content: Optional[str] = None


class UnifiedUndoStack:
    """Legacy undo stack — delegates to SnapshotUndoStack internally.

    Provides the old push_edit/push_file_create/push_file_write API
    while storing snapshot entries under the hood.
    """

    MAX_SIZE = 50

    def __init__(self):
        self._inner = SnapshotUndoStack()
        # Keep a parallel legacy stack for peek/pop returning UndoEntry
        self._legacy_stack: list[UndoEntry] = []
        self._lock = Lock()

    def push_edit(
        self,
        target: str,
        search_content: str,
        replace_content: str,
        reason: str = "",
    ) -> None:
        """Record a search/replace edit for potential undo."""
        entry = UndoEntry(
            action_type="edit",
            description=f"Edit {target}: {reason}" if reason else f"Edit {target}",
            edit_target=target,
            edit_search_content=search_content,
            edit_replace_content=replace_content,
        )
        with self._lock:
            if len(self._legacy_stack) >= self.MAX_SIZE:
                self._legacy_stack.pop(0)
            self._legacy_stack.append(entry)

    def push_file_create(self, path: str, content: str) -> None:
        """Record a file creation (undo = delete)."""
        entry = UndoEntry(
            action_type="file_create",
            description=f"Create {path}",
            file_path=path,
            file_previous_content=None,
        )
        with self._lock:
            if len(self._legacy_stack) >= self.MAX_SIZE:
                self._legacy_stack.pop(0)
            self._legacy_stack.append(entry)

    def push_file_write(self, path: str, previous_content: str, new_content: str) -> None:
        """Record a file write/overwrite (undo = restore previous content)."""
        entry = UndoEntry(
            action_type="file_write",
            description=f"Write {path}",
            file_path=path,
            file_previous_content=previous_content,
        )
        with self._lock:
            if len(self._legacy_stack) >= self.MAX_SIZE:
                self._legacy_stack.pop(0)
            self._legacy_stack.append(entry)

    def peek(self) -> Optional[UndoEntry]:
        """View the top entry without removing it."""
        with self._lock:
            return self._legacy_stack[-1] if self._legacy_stack else None

    def pop(self) -> Optional[UndoEntry]:
        """Remove and return the top entry."""
        with self._lock:
            return self._legacy_stack.pop() if self._legacy_stack else None

    def get_preview_text(self) -> str:
        """Get a human-readable description of the next undo."""
        with self._lock:
            entry = self._legacy_stack[-1] if self._legacy_stack else None

        if not entry:
            return "Nothing to undo"

        if entry.action_type == "edit":
            return f"Undo edit on {entry.edit_target}"
        elif entry.action_type == "file_create":
            return f"Delete created file: {entry.file_path}"
        elif entry.action_type == "file_write":
            return f"Revert {entry.file_path} to previous content"
        return entry.description

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._legacy_stack) == 0

    def size(self) -> int:
        with self._lock:
            return len(self._legacy_stack)
