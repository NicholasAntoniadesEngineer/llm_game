"""Injected UI notification — schedules async broadcast from sync agent code without module globals."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

logger = logging.getLogger("eternal.agents")


@runtime_checkable
class UiNotifier(Protocol):
    """Push WebSocket payloads: fire-and-forget or awaited send."""

    def schedule_message(self, message: dict[str, Any]) -> None:
        """Schedule ``broadcast_async(message)`` on the running loop if possible."""

    async def send_message(self, message: dict[str, Any]) -> None:
        """Await the async broadcast (e.g. token usage after LLM call)."""


class NoOpUiNotifier:
    """Discards messages (tests, offline tools)."""

    def schedule_message(self, message: dict[str, Any]) -> None:
        _ = message

    async def send_message(self, message: dict[str, Any]) -> None:
        _ = message


class AsyncBroadcastNotifier:
    """Wraps the same async callable as ``server.broadcast`` / ``BuildEngine.broadcast``."""

    def __init__(self, broadcast_async: Callable[..., Awaitable[Any]]) -> None:
        self._broadcast_async = broadcast_async

    def schedule_message(self, message: dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast_async(message))
        except RuntimeError as no_loop:
            logger.error(
                "UI broadcast schedule failed: no running event loop",
                exc_info=no_loop,
            )
            raise
        except Exception as schedule_error:
            logger.exception(
                "UI broadcast schedule failed",
                exc_info=schedule_error,
            )
            raise

    async def send_message(self, message: dict[str, Any]) -> None:
        await self._broadcast_async(message)
