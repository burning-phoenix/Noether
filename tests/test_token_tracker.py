"""Tests for token usage tracker."""

import pytest
import threading
from src.backends.token_tracker import TokenTracker, TokenUsage


class TestTokenTracker:
    """Test TokenTracker."""

    def test_empty_tracker(self):
        tracker = TokenTracker()
        assert tracker.get_last() is None
        assert tracker.get_formatted_last() == ""

    def test_record_and_get_last(self):
        tracker = TokenTracker()
        tracker.record(100, 200, "test-model")
        last = tracker.get_last()
        assert last is not None
        assert last.prompt_tokens == 100
        assert last.completion_tokens == 200
        assert last.total_tokens == 300
        assert last.model == "test-model"

    def test_get_last_returns_most_recent(self):
        tracker = TokenTracker()
        tracker.record(10, 20, "model-a")
        tracker.record(30, 40, "model-b")
        last = tracker.get_last()
        assert last.prompt_tokens == 30
        assert last.model == "model-b"

    def test_session_totals(self):
        tracker = TokenTracker()
        tracker.record(100, 200, "m")
        tracker.record(150, 250, "m")
        totals = tracker.get_session_totals()
        assert totals["prompt_tokens"] == 250
        assert totals["completion_tokens"] == 450
        assert totals["total_tokens"] == 700
        assert totals["call_count"] == 2

    def test_session_totals_empty(self):
        tracker = TokenTracker()
        totals = tracker.get_session_totals()
        assert totals["prompt_tokens"] == 0
        assert totals["completion_tokens"] == 0
        assert totals["total_tokens"] == 0
        assert totals["call_count"] == 0

    def test_formatted_last(self):
        tracker = TokenTracker()
        tracker.record(125, 340, "model")
        assert tracker.get_formatted_last() == "125 in / 340 out (465 total)"

    def test_formatted_session(self):
        tracker = TokenTracker()
        tracker.record(1000, 500, "m")
        tracker.record(500, 450, "m")
        formatted = tracker.get_formatted_session()
        assert "2,450 tokens" in formatted
        assert "2 calls" in formatted

    def test_formatted_session_empty(self):
        tracker = TokenTracker()
        assert tracker.get_formatted_session() == "Session: 0 tokens"

    def test_thread_safety(self):
        tracker = TokenTracker()
        errors = []

        def record_batch(start):
            try:
                for i in range(100):
                    tracker.record(start + i, start + i + 1, "model")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_batch, args=(i * 100,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        totals = tracker.get_session_totals()
        assert totals["call_count"] == 500


class TestTokenUsage:
    """Test TokenUsage dataclass."""

    def test_creation(self):
        usage = TokenUsage(
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            model="test",
        )
        assert usage.prompt_tokens == 10
        assert usage.completion_tokens == 20
        assert usage.total_tokens == 30
        assert usage.model == "test"
        assert usage.timestamp > 0
