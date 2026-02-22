"""
Orchestration integration tests.

Tests the critical flows that WERE NOT covered before:
1. Task chaining (complete → next task starts)
2. Queue recovery (fail → next task starts, not halt)
3. CoderWorker file ops (sync writes, undo recording)
4. PlannerWorker command parsing
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from src.orchestration.orchestrator import TaskOrchestrator
from src.orchestration.task import Task, TaskStatus, TaskPriority
from src.orchestration.undo import UnifiedUndoStack
from src.orchestration.file_operations import FileOperation, FileOpType, FileOperationParser
from src.sandbox.filesystem_sandbox import FileSystemSandbox
from src.ui.coder_worker import CoderWorker
from src.ui.planner_worker import PlannerWorker


# ── Orchestrator: task chaining ──────────────────────────────────────

class TestTaskChaining:
    """Test that the orchestrator correctly chains tasks."""

    def test_complete_chains_to_next(self):
        """After completing a task, the next task should auto-start."""
        started_tasks = []

        def on_started(task):
            started_tasks.append(task.id)

        orch = TaskOrchestrator(
            on_task_started=on_started,
            on_task_completed=lambda t: None,
            on_task_failed=lambda t: None,
        )

        # Add two tasks
        t1 = Task(description="Task 1", priority=TaskPriority.HIGH)
        t2 = Task(description="Task 2", priority=TaskPriority.MEDIUM)
        orch.add_tasks([t1, t2])

        # Get first task (auto-starts it)
        task = orch.get_next_task()
        assert task.id == t1.id
        assert t1.id in started_tasks

        # Complete first task
        orch.complete_current_task("done")
        assert orch.completed_count == 1
        assert orch.current_task is None

        # Get next task
        task2 = orch.get_next_task()
        assert task2.id == t2.id
        assert t2.id in started_tasks

    def test_fail_does_not_lose_task(self):
        """After a task fails, the queue should continue."""
        failed_tasks = []

        orch = TaskOrchestrator(
            on_task_started=lambda t: None,
            on_task_completed=lambda t: None,
            on_task_failed=lambda t: failed_tasks.append(t.id),
        )

        # Add two tasks
        t1 = Task(description="Task 1", priority=TaskPriority.HIGH, max_attempts=1)
        t2 = Task(description="Task 2", priority=TaskPriority.MEDIUM)
        orch.add_tasks([t1, t2])

        # Start first task
        orch.get_next_task()

        # Fail it (max_attempts=1 so it goes to failed_tasks)
        orch.fail_current_task("API error")
        assert t1.id in failed_tasks
        assert orch.failed_count == 1
        assert orch.current_task is None

        # Queue should still have the second task
        assert orch.queue_size == 1
        task2 = orch.get_next_task()
        assert task2.id == t2.id

    def test_fail_with_retries_requeues(self):
        """Tasks with remaining attempts should be re-queued."""
        orch = TaskOrchestrator()

        t1 = Task(description="Flaky task", max_attempts=3)
        orch.add_task(t1)

        # Start and fail
        orch.get_next_task()
        orch.fail_current_task("timeout")

        # Should be back in queue for retry
        assert orch.queue_size == 1
        assert orch.current_task is None

        # Get it again
        task = orch.get_next_task()
        assert task.id == t1.id
        assert task.attempts == 2

    def test_empty_queue_returns_none(self):
        """Getting next task from empty queue returns None."""
        orch = TaskOrchestrator()
        assert orch.get_next_task() is None

    def test_complete_with_no_current_returns_none(self):
        """Completing with no current task is a no-op."""
        orch = TaskOrchestrator()
        assert orch.complete_current_task("result") is None


# ── CoderWorker: file ops ─────────────────────────────────────────

class TestCoderWorkerFileOps:
    """Test CoderWorker.execute_file_ops_sync."""

    @pytest.fixture
    def sandbox(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        approval = AsyncMock(return_value=True)
        return FileSystemSandbox(project_root=str(project), approval_callback=approval)

    @pytest.fixture
    def coder(self):
        orch = TaskOrchestrator()
        undo = UnifiedUndoStack()
        parser = FileOperationParser(sandbox_root=Path("."))
        return CoderWorker(orchestrator=orch, undo_stack=undo, file_op_parser=parser)

    def test_create_files(self, coder, sandbox):
        ops = [
            FileOperation(
                path="hello.py",
                content="print('hello')",
                op_type=FileOpType.CREATE,
                reason="test",
            ),
            FileOperation(
                path="world.py",
                content="print('world')",
                op_type=FileOpType.CREATE,
                reason="test",
            ),
        ]

        success, fail = coder.execute_file_ops_sync(ops, sandbox)
        assert success == 2
        assert fail == 0

        # Verify files written
        assert (Path(sandbox.project_root) / "hello.py").read_text() == "print('hello')"
        assert (Path(sandbox.project_root) / "world.py").read_text() == "print('world')"

    def test_undo_recorded_for_new_file(self, coder, sandbox):
        ops = [
            FileOperation(
                path="new.py",
                content="new content",
                op_type=FileOpType.CREATE,
                reason="test",
            ),
        ]

        coder.execute_file_ops_sync(ops, sandbox)
        assert len(coder.undo_stack._legacy_stack) == 1

    def test_undo_recorded_for_overwrite(self, coder, sandbox):
        # Create existing file
        (Path(sandbox.project_root) / "existing.py").write_text("old content")

        ops = [
            FileOperation(
                path="existing.py",
                content="new content",
                op_type=FileOpType.WRITE,
                reason="test",
            ),
        ]

        coder.execute_file_ops_sync(ops, sandbox)
        assert len(coder.undo_stack._legacy_stack) == 1

    def test_blocked_path_fails(self, coder, sandbox):
        ops = [
            FileOperation(
                path=".env",
                content="SECRET=123",
                op_type=FileOpType.CREATE,
                reason="test",
            ),
        ]

        success, fail = coder.execute_file_ops_sync(ops, sandbox)
        assert success == 0
        assert fail == 1

    def test_mkdir_operation(self, coder, sandbox):
        ops = [
            FileOperation(
                path="src/models",
                content="",
                op_type=FileOpType.MKDIR,
                reason="test",
            ),
        ]

        success, fail = coder.execute_file_ops_sync(ops, sandbox)
        assert success == 1
        assert (Path(sandbox.project_root) / "src" / "models").is_dir()

    def test_mixed_success_and_failure(self, coder, sandbox):
        ops = [
            FileOperation(
                path="good.py",
                content="# good",
                op_type=FileOpType.CREATE,
                reason="test",
            ),
            FileOperation(
                path=".env",
                content="BAD",
                op_type=FileOpType.CREATE,
                reason="test",
            ),
            FileOperation(
                path="also_good.py",
                content="# also good",
                op_type=FileOpType.CREATE,
                reason="test",
            ),
        ]

        success, fail = coder.execute_file_ops_sync(ops, sandbox)
        assert success == 2
        assert fail == 1


# ── CoderWorker: streaming ──────────────────────────────────────────

class TestCoderWorkerStreaming:
    """Test CoderWorker.stream_code."""

    def test_stream_collects_full_response(self):
        orch = TaskOrchestrator()
        coder = CoderWorker(orchestrator=orch, undo_stack=UnifiedUndoStack())

        backend = MagicMock()
        backend.stream.return_value = iter(["chunk1", "chunk2", "chunk3"])

        result = coder.stream_code(backend, "test prompt")
        assert result == "chunk1chunk2chunk3"

    def test_stream_calls_on_chunk(self):
        orch = TaskOrchestrator()
        coder = CoderWorker(orchestrator=orch, undo_stack=UnifiedUndoStack())

        backend = MagicMock()
        backend.stream.return_value = iter(["a", "b"])

        chunks = []
        coder.stream_code(backend, "prompt", on_chunk=lambda c: chunks.append(c))
        assert chunks == ["a", "b"]

    def test_streaming_lock(self):
        orch = TaskOrchestrator()
        coder = CoderWorker(orchestrator=orch, undo_stack=UnifiedUndoStack())

        assert coder.is_streaming is False
        coder.set_streaming(True)
        assert coder.is_streaming is True
        coder.set_streaming(False)
        assert coder.is_streaming is False


# ── PlannerWorker: command parsing ──────────────────────────────────────

class TestPlannerWorkerCommands:
    """Test PlannerWorker.check_for_commands."""

    @pytest.fixture
    def planner(self):
        return PlannerWorker(planner_agent=None, orchestrator=TaskOrchestrator())

    def test_legacy_exec_no_longer_parsed(self, planner):
        """Legacy /exec text commands are no longer parsed (use native tools)."""
        response = 'Let me run this: /exec "ls -la"'
        commands = planner.check_for_commands(response)
        assert len(commands) == 0

    def test_parse_explore_add_command(self, planner):
        response = "/explore-add look at the settings screen"
        commands = planner.check_for_commands(response)
        assert len(commands) == 1
        assert commands[0]["type"] == "explore-add"
        assert commands[0]["query"] == "look at the settings screen"

    def test_parse_run_command(self, planner):
        response = "/run"
        commands = planner.check_for_commands(response)
        assert len(commands) == 1
        assert commands[0]["type"] == "run"

    def test_no_commands_returns_empty(self, planner):
        response = "Just a normal chat response with no commands."
        commands = planner.check_for_commands(response)
        assert len(commands) == 0

    def test_native_tool_calls_parsed(self, planner):
        """Native tool calls (execute_bash) are parsed via process_tool_calls."""
        planner.last_tool_calls = [
            {"function": {"name": "execute_bash", "arguments": '{"command": "ls", "args": ["-la"]}'}}
        ]
        # process_tool_calls routes through pipeline; without pipeline, returns empty
        # but clears last_tool_calls
        results = planner.process_tool_calls(pipeline=None)
        # Without pipeline, bash calls are skipped but tool_calls are still consumed
        assert len(planner.last_tool_calls) == 0


# ── CoderWorker: file op parsing ─────────────────────────────────────

class TestCoderWorkerParsing:
    """Test CoderWorker.parse_file_ops."""

    def test_parse_with_no_parser_returns_empty(self):
        orch = TaskOrchestrator()
        coder = CoderWorker(orchestrator=orch, undo_stack=UnifiedUndoStack())
        # No parser set
        result = coder.parse_file_ops("some response")
        assert result == []

    def test_parse_delegates_to_parser(self, tmp_path):
        orch = TaskOrchestrator()
        parser = FileOperationParser(sandbox_root=tmp_path)
        coder = CoderWorker(
            orchestrator=orch,
            undo_stack=UnifiedUndoStack(),
            file_op_parser=parser,
        )

        # Test with response containing a code block with filename
        response = '```python filename="hello.py"\nprint("hello")\n```'
        result = coder.parse_file_ops(response)
        assert len(result) >= 1
        assert result[0].path == "hello.py"
