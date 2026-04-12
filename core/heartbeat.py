"""Periodic heartbeat in a separate daemon thread (survives asyncio stalls for diagnosis)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("eternal.heartbeat")


class HeartbeatThread(threading.Thread):
    def __init__(self, interval_s: float, snapshot_fn: Callable[[], dict[str, Any]]):
        super().__init__(daemon=True, name="EternalHeartbeat")
        self._interval_s = max(1.0, float(interval_s))
        self._snapshot_fn = snapshot_fn
        # Do not use ``_stop``: ``threading.Thread`` reserves that name for join/shutdown internals.
        self._shutdown_requested_event = threading.Event()

    def run(self) -> None:
        logger.info("Heartbeat thread started interval_s=%s", self._interval_s)
        while not self._shutdown_requested_event.wait(self._interval_s):
            try:
                snap = self._snapshot_fn()
                from core.run_log import log_event

                compact = {k: (str(v)[:500] + "…" if len(str(v)) > 500 else str(v)) for k, v in snap.items()}
                log_event("heartbeat", "tick", **compact)
                logger.info("HEARTBEAT %s", snap)
            except Exception:
                logger.exception("HEARTBEAT snapshot failed (heartbeat thread still alive)")
        logger.info("Heartbeat thread stopped")

    def stop(self) -> None:
        self._shutdown_requested_event.set()


def start_heartbeat(snapshot_fn: Callable[[], dict[str, Any]], interval_s: float) -> HeartbeatThread:
    thread = HeartbeatThread(interval_s, snapshot_fn)
    thread.start()
    return thread


def stop_heartbeat(thread: HeartbeatThread | None, timeout_s: float = 3.0) -> None:
    if thread is None:
        return
    thread.stop()
    thread.join(timeout=timeout_s)
    if thread.is_alive():
        logger.warning("Heartbeat thread did not join within %ss", timeout_s)
