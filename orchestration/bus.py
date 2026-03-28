"""In-memory message bus for agent communication."""

import asyncio
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class BusMessage:
    sender: str
    msg_type: str  # directive, proposal, fact_check, placement, flavor, phase
    content: str
    tiles: Optional[list[dict]] = None
    target: Optional[str] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: float = field(default_factory=time.time)
    turn: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class MessageBus:
    def __init__(self):
        self._messages: list[BusMessage] = []
        self._subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def publish(self, message: BusMessage):
        self._messages.append(message)
        for q in self._subscribers:
            await q.put(message)

    def history(self, last_n: int = 10) -> list[BusMessage]:
        return self._messages[-last_n:]

    def history_text(self, last_n: int = 10) -> str:
        """Formatted history for agent context injection."""
        msgs = self.history(last_n)
        if not msgs:
            return "(No previous messages)"
        lines = []
        for m in msgs:
            lines.append(f"[{m.sender}] ({m.msg_type}): {m.content}")
        return "\n".join(lines)
