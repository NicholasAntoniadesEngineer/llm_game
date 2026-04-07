"""broadcast() — push a message to all connected WebSocket clients."""

import logging

from core.config import CHAT_HISTORY_MAX_MESSAGES

if __name__ != "__main__":
    from server.state import AppState

logger = logging.getLogger("eternal.server")


async def broadcast(state: "AppState", message: dict):
    """Cache status messages, append chat-like messages to history, and fan out to all WebSockets."""
    if message.get("type") == "agent_status":
        agent = message.get("agent")
        status = message.get("status")
        if isinstance(agent, str) and isinstance(status, str):
            state.agent_status_by_agent[agent] = dict(message)

    if message.get("type") in (
        "chat",
        "phase",
        "timeline",
        "master_plan",
        "map_description",
        "map_image",
        "placement_warnings",
        "complete",
        "paused",
    ):
        state.chat_history.append(message)
        if len(state.chat_history) > CHAT_HISTORY_MAX_MESSAGES:
            del state.chat_history[: len(state.chat_history) - CHAT_HISTORY_MAX_MESSAGES]

    if not state.ws_connections:
        return

    # Sequential sends with per-connection error handling (avoids killing slow connections)
    dead = []
    for ws in state.ws_connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in state.ws_connections:
            state.ws_connections.remove(ws)
