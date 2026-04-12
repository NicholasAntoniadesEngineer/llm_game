"""Run log — captures all server logs and key events into a downloadable text file.

Every log message from every logger (agents, engine, server, persistence, etc.)
is captured, plus structured events (agent calls, token usage, decisions).

Set ETERNAL_LOG_LEVEL=DEBUG for verbose diagnostics (also raises root logger level).
"""

import logging
import os
import time
from collections import deque
from typing import Any

_LOG_BUFFER: deque[str] | None = None
_RUN_START: float | None = None
_emit_fallback_logger = logging.getLogger("eternal.run_log_emit")


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class RunLogHandler(logging.Handler):
    """Logging handler that appends every log record to the in-memory buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if _LOG_BUFFER is not None:
                _LOG_BUFFER.append(msg)
        except Exception:
            _emit_fallback_logger.exception("RunLogHandler.emit failed; record not appended to buffer")


# Singleton handler — attached once in init_run_log().
_handler: RunLogHandler | None = None


def init_run_log(*, run_log_buffer_max_lines: int, log_level_string: str) -> None:
    """Attach the RunLogHandler to the root logger. Call once at startup."""
    global _handler, _RUN_START, _LOG_BUFFER
    if _handler is not None:
        return
    _RUN_START = time.time()
    _LOG_BUFFER = deque(maxlen=run_log_buffer_max_lines)
    _handler = RunLogHandler()
    _handler.setLevel(logging.DEBUG)
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    ))
    root = logging.getLogger()
    root.addHandler(_handler)
    try:
        resolved_level = getattr(logging, log_level_string.upper(), logging.INFO)
        eff = logging.getLevelName(root.level or logging.WARNING)
        _LOG_BUFFER.append(f"{'='*72}")
        _LOG_BUFFER.append("  ETERNAL CITIES — Run Log")
        _LOG_BUFFER.append(f"  Started: {_ts()}")
        _LOG_BUFFER.append(
            f"  ETERNAL_LOG_LEVEL={os.environ.get('ETERNAL_LOG_LEVEL', 'INFO')} "
            f"config.log_level_string={logging.getLevelName(resolved_level)} root_effective≈{eff}"
        )
        _LOG_BUFFER.append(f"{'='*72}")
        _LOG_BUFFER.append("")
    except Exception:
        _LOG_BUFFER.append(f"{'='*72}")
        _LOG_BUFFER.append("  ETERNAL CITIES — Run Log")
        _LOG_BUFFER.append(f"  Started: {_ts()}")
        _LOG_BUFFER.append(f"{'='*72}")
        _LOG_BUFFER.append("")


def log_event(category: str, message: str, **kwargs: Any) -> None:
    """Log a structured event (shows up in the run log with a clear prefix)."""
    parts = [f"[{category.upper()}] {message}"]
    for k, v in kwargs.items():
        parts.append(f"  {k}: {v}")
    entry = "\n".join(parts)
    if _LOG_BUFFER is not None:
        _LOG_BUFFER.append(f"{_ts()}  {entry}")


_TRACE_LOGGER = logging.getLogger("eternal.trace")


def _trace_kwarg_preview(value: Any, max_len: int = 800) -> str:
    try:
        s = repr(value)
    except Exception:
        s = "<unreprable>"
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def trace_event(category: str, message: str, **kwargs: Any) -> None:
    """Structured trace: same buffer as log_event plus stderr via ``eternal.trace`` for hang diagnosis."""
    log_event(category, message, **kwargs)
    safe = {k: _trace_kwarg_preview(v) for k, v in kwargs.items()}
    _TRACE_LOGGER.info("[%s] %s | %s", category.upper(), message, safe)


def get_log_text() -> str:
    """Return the full log as a single string for download."""
    buf = _LOG_BUFFER if _LOG_BUFFER is not None else deque()
    header_lines = [
        f"{'='*72}",
        f"  ETERNAL CITIES — Run Log Export",
        f"  Exported: {_ts()}",
        f"  Lines captured: {len(buf)}",
    ]
    if _RUN_START:
        elapsed = time.time() - _RUN_START
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        header_lines.append(f"  Server uptime: {h}h {m}m {s}s")
    header_lines.append(f"{'='*72}")
    header_lines.append("")

    return "\n".join(header_lines) + "\n".join(buf) + "\n"


def clear_log() -> None:
    """Clear the log buffer (e.g. on reset)."""
    if _LOG_BUFFER is not None:
        _LOG_BUFFER.clear()
        _LOG_BUFFER.append(f"{_ts()}  [LOG] Buffer cleared")
