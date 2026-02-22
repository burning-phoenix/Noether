"""
Token usage tracking for LLM backend calls.

Thread-safe tracker that records token usage from API responses
and provides formatted summaries.
"""

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional


@dataclass
class TokenUsage:
    """Single token usage record."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str
    timestamp: float = field(default_factory=time.time)


class TokenTracker:
    """Thread-safe token usage tracker."""

    def __init__(self):
        self._history: list[TokenUsage] = []
        self._lock = Lock()

    def record(self, prompt_tokens: int, completion_tokens: int, model: str) -> None:
        """Record a token usage entry."""
        total = prompt_tokens + completion_tokens
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            model=model,
        )
        with self._lock:
            self._history.append(usage)

    def get_last(self) -> Optional[TokenUsage]:
        """Get the most recent usage record."""
        with self._lock:
            return self._history[-1] if self._history else None

    def get_session_totals(self) -> dict:
        """Get cumulative session totals."""
        with self._lock:
            prompt = sum(u.prompt_tokens for u in self._history)
            completion = sum(u.completion_tokens for u in self._history)
            return {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "call_count": len(self._history),
            }

    def get_formatted_last(self) -> str:
        """Format the last usage for display."""
        last = self.get_last()
        if not last:
            return ""
        return f"{last.prompt_tokens} in / {last.completion_tokens} out ({last.total_tokens} total)"

    def get_formatted_session(self) -> str:
        """Format session totals for display."""
        totals = self.get_session_totals()
        total = totals["total_tokens"]
        if total == 0:
            return "Session: 0 tokens"
        return f"Session: {total:,} tokens ({totals['call_count']} calls)"
