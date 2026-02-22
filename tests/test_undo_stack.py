"""Tests for unified undo stack."""

import pytest
from src.orchestration.undo import UnifiedUndoStack, UndoEntry


class TestUnifiedUndoStack:
    """Test UnifiedUndoStack."""

    def test_empty_stack(self):
        stack = UnifiedUndoStack()
        assert stack.is_empty() is True
        assert stack.size() == 0
        assert stack.peek() is None
        assert stack.pop() is None

    def test_push_edit(self):
        stack = UnifiedUndoStack()
        stack.push_edit(
            target="app.py",
            search_content="old code",
            replace_content="new code",
            reason="fix bug",
        )
        assert stack.size() == 1
        assert stack.is_empty() is False

    def test_push_edit_stores_content(self):
        stack = UnifiedUndoStack()
        stack.push_edit("app.py", "search", "replace", "reason")
        entry = stack.peek()
        assert entry is not None
        assert entry.action_type == "edit"
        assert entry.edit_target == "app.py"
        assert entry.edit_search_content == "search"
        assert entry.edit_replace_content == "replace"

    def test_push_file_create(self):
        stack = UnifiedUndoStack()
        stack.push_file_create("new_file.py", "content")
        entry = stack.peek()
        assert entry is not None
        assert entry.action_type == "file_create"
        assert entry.file_path == "new_file.py"
        assert entry.file_previous_content is None  # No previous content for creates

    def test_push_file_write(self):
        stack = UnifiedUndoStack()
        stack.push_file_write("existing.py", "old content", "new content")
        entry = stack.peek()
        assert entry is not None
        assert entry.action_type == "file_write"
        assert entry.file_path == "existing.py"
        assert entry.file_previous_content == "old content"

    def test_lifo_ordering(self):
        stack = UnifiedUndoStack()
        stack.push_edit("a.py", "s1", "r1", "first")
        stack.push_edit("b.py", "s2", "r2", "second")
        stack.push_edit("c.py", "s3", "r3", "third")

        entry = stack.pop()
        assert entry.edit_target == "c.py"
        entry = stack.pop()
        assert entry.edit_target == "b.py"
        entry = stack.pop()
        assert entry.edit_target == "a.py"
        assert stack.is_empty()

    def test_peek_does_not_remove(self):
        stack = UnifiedUndoStack()
        stack.push_edit("a.py", "s", "r", "reason")
        assert stack.size() == 1
        entry = stack.peek()
        assert entry is not None
        assert stack.size() == 1  # Still there

    def test_pop_on_empty_returns_none(self):
        stack = UnifiedUndoStack()
        assert stack.pop() is None

    def test_max_entries_cap(self):
        stack = UnifiedUndoStack()
        for i in range(60):
            stack.push_edit(f"file_{i}.py", "s", "r", f"edit {i}")
        assert stack.size() == 50
        # Most recent should still be the last pushed
        entry = stack.peek()
        assert "file_59" in entry.edit_target

    def test_max_entries_drops_oldest(self):
        stack = UnifiedUndoStack()
        for i in range(55):
            stack.push_edit(f"file_{i}.py", "s", "r", f"edit {i}")

        # Pop all and collect targets
        targets = []
        while not stack.is_empty():
            entry = stack.pop()
            targets.append(entry.edit_target)

        # Should have 50 entries, starting from the most recent
        assert len(targets) == 50
        # First popped should be file_54 (most recent)
        assert targets[0] == "file_54.py"
        # Last popped should be file_5 (oldest remaining after 5 were dropped)
        assert targets[-1] == "file_5.py"

    def test_preview_text_edit(self):
        stack = UnifiedUndoStack()
        stack.push_edit("app.py", "s", "r", "fix")
        assert "app.py" in stack.get_preview_text()
        assert "edit" in stack.get_preview_text().lower()

    def test_preview_text_file_create(self):
        stack = UnifiedUndoStack()
        stack.push_file_create("new.py", "content")
        preview = stack.get_preview_text()
        assert "new.py" in preview
        assert "Delete" in preview or "delete" in preview.lower()

    def test_preview_text_file_write(self):
        stack = UnifiedUndoStack()
        stack.push_file_write("old.py", "prev", "new")
        preview = stack.get_preview_text()
        assert "old.py" in preview
        assert "Revert" in preview or "revert" in preview.lower()

    def test_preview_text_empty(self):
        stack = UnifiedUndoStack()
        assert stack.get_preview_text() == "Nothing to undo"

    def test_mixed_operations(self):
        stack = UnifiedUndoStack()
        stack.push_edit("a.py", "s", "r", "edit")
        stack.push_file_create("b.py", "content")
        stack.push_file_write("c.py", "old", "new")

        assert stack.size() == 3
        assert stack.pop().action_type == "file_write"
        assert stack.pop().action_type == "file_create"
        assert stack.pop().action_type == "edit"


class TestUndoEntry:
    """Test UndoEntry dataclass."""

    def test_timestamp_set(self):
        entry = UndoEntry(action_type="edit", description="test")
        assert entry.timestamp > 0

    def test_default_nones(self):
        entry = UndoEntry(action_type="edit")
        assert entry.edit_target is None
        assert entry.edit_search_content is None
        assert entry.edit_replace_content is None
        assert entry.file_path is None
        assert entry.file_previous_content is None
