"""Tests for Planner memory system."""

import pytest
from src.memory.planner_memory import (
    SimpleConversationMemory,
    TaskState,
    SessionMemory,
    PlannerMemoryManager,
)


class TestSimpleConversationMemory:
    """Test sliding window conversation memory."""

    def test_add_and_get_context(self):
        mem = SimpleConversationMemory(max_exchanges=5)
        mem.add("user", "hello")
        mem.add("assistant", "hi there")
        context = mem.get_context()
        assert "user: hello" in context
        assert "assistant: hi there" in context

    def test_sliding_window(self):
        mem = SimpleConversationMemory(max_exchanges=2)
        # Add 3 exchanges (6 messages), window is 2 exchanges (4 messages)
        mem.add("user", "msg1")
        mem.add("assistant", "resp1")
        mem.add("user", "msg2")
        mem.add("assistant", "resp2")
        mem.add("user", "msg3")
        mem.add("assistant", "resp3")

        # Should have only last 4 messages
        assert len(mem.buffer) == 4
        context = mem.get_context()
        assert "msg1" not in context
        assert "msg2" in context
        assert "msg3" in context

    def test_empty_context(self):
        mem = SimpleConversationMemory()
        assert mem.get_context() == ""

    def test_push_beyond_limit(self):
        mem = SimpleConversationMemory(max_exchanges=1)
        mem.add("user", "first")
        mem.add("assistant", "first-resp")
        mem.add("user", "second")
        mem.add("assistant", "second-resp")
        assert len(mem.buffer) == 2
        context = mem.get_context()
        assert "first" not in context
        assert "second" in context


class TestTaskState:
    """Test task state tracking."""

    def test_initial_state(self):
        state = TaskState()
        assert state.current_task == ""
        assert len(state.subtasks) == 0
        assert len(state.completed_subtasks) == 0

    def test_set_task(self):
        state = TaskState()
        state.set_task("Build API", ["task1", "task2"])
        assert state.current_task == "Build API"
        assert len(state.subtasks) == 2

    def test_mark_complete(self):
        state = TaskState()
        state.set_task("Build API", ["task1", "task2"])
        state.mark_complete("task1", ["app.py"])
        assert "task1" in state.completed_subtasks
        assert "app.py" in state.created_files

    def test_mark_complete_idempotent(self):
        state = TaskState()
        state.set_task("Build API", ["task1"])
        state.mark_complete("task1", [])
        state.mark_complete("task1", [])
        assert state.completed_subtasks.count("task1") == 1

    def test_progress_context(self):
        state = TaskState()
        state.set_task("Build API", ["task1", "task2"])
        state.mark_complete("task1", ["app.py"])
        context = state.get_progress_context()
        assert "Build API" in context
        assert "Total subtasks: 2" in context
        assert "Completed: 1" in context

    def test_progress_no_task(self):
        state = TaskState()
        assert "No active task" in state.get_progress_context()

    def test_set_task_resets_completed(self):
        state = TaskState()
        state.set_task("First", ["a"])
        state.mark_complete("a", [])
        state.set_task("Second", ["b", "c"])
        assert len(state.completed_subtasks) == 0


class TestSessionMemory:
    """Test session key-value store."""

    def test_store_and_recall(self):
        mem = SessionMemory()
        mem.store("framework", "FastAPI")
        assert mem.recall("framework") == "FastAPI"

    def test_recall_missing(self):
        mem = SessionMemory()
        assert mem.recall("nonexistent") == ""

    def test_overwrite(self):
        mem = SessionMemory()
        mem.store("key", "val1")
        mem.store("key", "val2")
        assert mem.recall("key") == "val2"

    def test_get_all_facts(self):
        mem = SessionMemory()
        mem.store("framework", "Flask")
        mem.store("db", "SQLite")
        facts = mem.get_all_facts()
        assert "framework: Flask" in facts
        assert "db: SQLite" in facts

    def test_get_all_facts_empty(self):
        mem = SessionMemory()
        assert "No stored preferences" in mem.get_all_facts()


class TestPlannerMemoryManager:
    """Test the full memory manager."""

    def test_get_full_context(self):
        mgr = PlannerMemoryManager()
        mgr.record_interaction("hello", "hi")
        mgr.session.store("lang", "python")
        context = mgr.get_full_context_for_llm()
        assert "SESSION CONTEXT" in context
        assert "RECENT CONVERSATION" in context
        assert "python" in context

    def test_record_system_event(self):
        mgr = PlannerMemoryManager()
        mgr.record_system_event("Command executed: ls")
        context = mgr.get_full_context_for_llm()
        assert "Command executed: ls" in context

    def test_system_event_truncation(self):
        mgr = PlannerMemoryManager()
        long_event = "x" * 1000
        mgr.record_system_event(long_event)
        assert len(mgr.system_events[0]) < 600  # Truncated at 500 + suffix

    def test_system_event_max_kept(self):
        mgr = PlannerMemoryManager(max_system_events=3)
        for i in range(5):
            mgr.record_system_event(f"event_{i}")
        assert len(mgr.system_events) == 3
        assert mgr.system_events[0] == "event_2"

    def test_extract_and_store_preference(self):
        mgr = PlannerMemoryManager()
        mgr.extract_and_store_preference("framework", "Django")
        assert mgr.session.recall("framework") == "Django"

    def test_update_task_progress(self):
        mgr = PlannerMemoryManager()
        mgr.task_state.set_task("build", ["t1", "t2"])
        mgr.update_task_progress("t1", ["app.py"])
        assert "t1" in mgr.task_state.completed_subtasks
