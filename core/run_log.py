"""Run log — captures all server logs and key events into a downloadable text file.

Every log message from every logger (agents, engine, server, persistence, etc.)
is captured, plus structured events (agent calls, token usage, decisions).
"""

import logging
import time
from collections import deque
from typing import Any

# Max lines kept in memory (oldest dropped when exceeded).
MAX_LOG_LINES = 10_000

_LOG_BUFFER: deque[str] = deque(maxlen=MAX_LOG_LINES)
_RUN_START: float | None = None


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class RunLogHandler(logging.Handler):
    """Logging handler that appends every log record to the in-memory buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            _LOG_BUFFER.append(msg)
        except Exception:
            pass


# Singleton handler — attached once in init_run_log().
_handler: RunLogHandler | None = None


def init_run_log() -> None:
    """Attach the RunLogHandler to the root logger. Call once at startup."""
    global _handler, _RUN_START
    if _handler is not None:
        return
    _RUN_START = time.time()
    _handler = RunLogHandler()
    _handler.setLevel(logging.DEBUG)
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    ))
    logging.getLogger().addHandler(_handler)
    _LOG_BUFFER.append(f"{'='*72}")
    _LOG_BUFFER.append(f"  ETERNAL CITIES — Run Log")
    _LOG_BUFFER.append(f"  Started: {_ts()}")
    _LOG_BUFFER.append(f"{'='*72}")
    _LOG_BUFFER.append("")


def log_event(category: str, message: str, **kwargs: Any) -> None:
    """Log a structured event (shows up in the run log with a clear prefix)."""
    parts = [f"[{category.upper()}] {message}"]
    for k, v in kwargs.items():
        parts.append(f"  {k}: {v}")
    entry = "\n".join(parts)
    _LOG_BUFFER.append(f"{_ts()}  {entry}")


def get_log_text() -> str:
    """Return the full log as a single string for download."""
    header_lines = [
        f"{'='*72}",
        f"  ETERNAL CITIES — Run Log Export",
        f"  Exported: {_ts()}",
        f"  Lines captured: {len(_LOG_BUFFER)}",
    ]
    if _RUN_START:
        elapsed = time.time() - _RUN_START
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        header_lines.append(f"  Server uptime: {h}h {m}m {s}s")
    header_lines.append(f"{'='*72}")
    header_lines.append("")

    return "\n".join(header_lines) + "\n".join(_LOG_BUFFER) + "\n"


def clear_log() -> None:
    """Clear the log buffer (e.g. on reset)."""
    _LOG_BUFFER.clear()
    _LOG_BUFFER.append(f"{_ts()}  [LOG] Buffer cleared")
