"""
Custom tracer for Noether.

Provides structured tracing without external dependencies (no LangSmith/LangFuse).
Trace events are:
1. Written as JSON lines to ~/.noether/logs/session_<timestamp>.jsonl
2. Streamed to registered listeners (e.g. the Settings/Trace screen)

Usage:
    from src.observability.tracer import tracer, trace_span

    # Decorate a function
    @trace_span("coder.stream")
    def stream_code(...):
        ...

    # Manual spans
    with tracer.span("file_op", path="hello.py") as span:
        ...
        span.set_result(success=True)

    # Register a listener for live UI streaming
    tracer.add_listener(my_callback)
"""

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from threading import Lock
from typing import Optional, Callable, Any
from uuid import uuid4

logger = logging.getLogger("noether.tracer")


@dataclass
class TraceEvent:
    """A single trace event."""
    event_id: str
    span_id: str
    event_type: str           # "span_start", "span_end", "log", "error", "metric"
    name: str                 # e.g. "coder.stream", "file_op.write"
    timestamp: str            # ISO 8601
    duration_ms: Optional[float] = None
    metadata: dict = field(default_factory=dict)
    parent_span_id: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # Remove None values for compact JSON
        return {k: v for k, v in d.items() if v is not None}


class SpanContext:
    """A trace span that measures duration and records results."""

    def __init__(self, tracer: "NoetherTracer", name: str, span_id: str, parent_id: Optional[str] = None, **metadata):
        self.tracer = tracer
        self.name = name
        self.span_id = span_id
        self.parent_id = parent_id
        self.metadata = metadata
        self._start_time = time.monotonic()
        self._result_metadata: dict = {}

    def set_result(self, **kwargs) -> None:
        """Set result metadata for this span."""
        self._result_metadata.update(kwargs)

    def set_error(self, error: str) -> None:
        """Mark this span as failed."""
        self._result_metadata["error"] = error

    def add_metadata(self, **kwargs) -> None:
        """Add additional metadata to this span."""
        self.metadata.update(kwargs)

    def _end(self) -> None:
        """End the span and emit the event."""
        duration_ms = (time.monotonic() - self._start_time) * 1000
        merged = {**self.metadata, **self._result_metadata}
        error = merged.pop("error", None)

        self.tracer._emit(TraceEvent(
            event_id=str(uuid4())[:8],
            span_id=self.span_id,
            event_type="span_end",
            name=self.name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration_ms=round(duration_ms, 1),
            metadata=merged if merged else {},
            parent_span_id=self.parent_id,
            error=error,
        ))


class NoetherTracer:
    """Lightweight tracer with JSON log output and live streaming."""

    def __init__(self):
        self._listeners: list[Callable[[TraceEvent], None]] = []
        self._lock = Lock()
        self._log_dir: Optional[Path] = None
        self._log_file = None
        self._session_id = str(uuid4())[:8]
        self._active_spans: dict[str, SpanContext] = {}
        self._enabled = True

        # Initialize log directory
        self._init_log_dir()

    def _init_log_dir(self) -> None:
        """Set up the log directory."""
        try:
            self._log_dir = Path.home() / ".noether" / "logs"
            self._log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = self._log_dir / f"session_{timestamp}_{self._session_id}.jsonl"
            self._log_file = open(log_path, "a", encoding="utf-8")
            logger.info("Tracer logging to %s", log_path)
        except Exception as e:
            logger.warning("Could not initialize trace log: %s", e)
            self._log_file = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def add_listener(self, callback: Callable[[TraceEvent], None]) -> None:
        """Register a listener for live trace events."""
        with self._lock:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[TraceEvent], None]) -> None:
        """Remove a registered listener."""
        with self._lock:
            self._listeners = [l for l in self._listeners if l is not callback]

    @contextmanager
    def span(self, name: str, parent_id: Optional[str] = None, **metadata):
        """Context manager for a traced span."""
        if not self._enabled:
            yield SpanContext(self, name, "disabled")
            return

        span_id = str(uuid4())[:8]
        ctx = SpanContext(self, name, span_id, parent_id, **metadata)

        # Emit start event
        self._emit(TraceEvent(
            event_id=str(uuid4())[:8],
            span_id=span_id,
            event_type="span_start",
            name=name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metadata=metadata if metadata else {},
            parent_span_id=parent_id,
        ))

        with self._lock:
            self._active_spans[span_id] = ctx

        try:
            yield ctx
        except Exception as e:
            ctx.set_error(str(e))
            raise
        finally:
            with self._lock:
                self._active_spans.pop(span_id, None)
            ctx._end()

    def log_event(self, name: str, **metadata) -> None:
        """Log a standalone event (not a span)."""
        if not self._enabled:
            return
        self._emit(TraceEvent(
            event_id=str(uuid4())[:8],
            span_id="",
            event_type="log",
            name=name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metadata=metadata,
        ))

    def log_error(self, name: str, error: str, **metadata) -> None:
        """Log an error event."""
        if not self._enabled:
            return
        self._emit(TraceEvent(
            event_id=str(uuid4())[:8],
            span_id="",
            event_type="error",
            name=name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metadata=metadata,
            error=error,
        ))

    def log_metric(self, name: str, value: float, unit: str = "", **metadata) -> None:
        """Log a numeric metric."""
        if not self._enabled:
            return
        self._emit(TraceEvent(
            event_id=str(uuid4())[:8],
            span_id="",
            event_type="metric",
            name=name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metadata={"value": value, "unit": unit, **metadata},
        ))

    def _emit(self, event: TraceEvent) -> None:
        """Write event to log file and notify listeners."""
        event_dict = event.to_dict()

        # Write to JSONL file
        if self._log_file:
            try:
                self._log_file.write(json.dumps(event_dict) + "\n")
                self._log_file.flush()
            except Exception as e:
                logger.warning("Could not write trace event: %s", e)

        # Notify listeners
        with self._lock:
            listeners = list(self._listeners)

        for listener in listeners:
            try:
                listener(event)
            except Exception as e:
                logger.warning("Trace listener error: %s", e)

    def close(self) -> None:
        """Close the tracer and flush logs."""
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None


# ── Global singleton ──────────────────────────────────────────────────

tracer = NoetherTracer()


def trace_span(name: str):
    """Decorator that wraps a function in a trace span.

    Usage:
        @trace_span("coder.stream")
        def stream_code(self, prompt, ...):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with tracer.span(name) as span:
                result = func(*args, **kwargs)
                return result
        return wrapper
    return decorator
