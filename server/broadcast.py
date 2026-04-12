"""broadcast() — push a message to all connected WebSocket clients."""

import json
import logging

if __name__ != "__main__":
    from server.state import AppState

logger = logging.getLogger("eternal.broadcast")


def _engine_ui_fields(state: "AppState") -> dict:
    """Snapshot for client header (build running / scenario loaded)."""
    engine_running = False
    if state.engine_is_running is not None:
        try:
            engine_running = bool(state.engine_is_running())
        except Exception:
            logger.warning(
                "engine_is_running callback failed; treating engine as not running",
                exc_info=True,
            )
            engine_running = False
    return {
        "engine_running": engine_running,
        "scenario_active": bool(state.run_session.scenario),
    }


def attach_engine_ui_to_message(state: "AppState", message: dict) -> dict:
    """Attach ``engine_running`` / ``scenario_active`` to every outbound WebSocket payload.

    The client header shows "build ?" until it has seen these fields; attaching them
    to all typed messages (not only world_state/scenario/token_usage) keeps the
    strip accurate after reconnect, long LLM waits (last: agent_prompt), and replay.
    """
    if not isinstance(message, dict):
        return message
    mt = message.get("type")
    if mt is None:
        return message
    out = dict(message)
    fields = _engine_ui_fields(state)
    out.update(fields)
    logger.debug(
        "attach_engine_ui type=%s engine_running=%s scenario_active=%s",
        mt,
        fields["engine_running"],
        fields["scenario_active"],
    )
    return out


async def broadcast(state: "AppState", message: dict):
    """Cache status messages, append chat-like messages to history, and fan out to all WebSockets."""
    outgoing = attach_engine_ui_to_message(state, message)
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
        max_chat_history = state.system_configuration.chat_history_max_messages
        if len(state.chat_history) > max_chat_history:
            del state.chat_history[: len(state.chat_history) - max_chat_history]
        schedule_fn = getattr(state, "schedule_debounced_persist_after_chat", None)
        if callable(schedule_fn):
            schedule_fn()

    if not state.ws_connections:
        logger.debug("broadcast type=%s (no WebSocket clients — cached only)", message.get("type"))
        return

    n_clients = len(state.ws_connections)
    try:
        approx_bytes = len(json.dumps(outgoing, default=str))
    except Exception:
        approx_bytes = -1
    logger.debug(
        "broadcast fan-out type=%s clients=%s approx_json_bytes=%s",
        message.get("type"),
        n_clients,
        approx_bytes,
    )

    # Sequential sends with per-connection error handling (avoids killing slow connections)
    dead = []
    for ws in state.ws_connections:
        try:
            await ws.send_json(outgoing)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in state.ws_connections:
            state.ws_connections.remove(ws)
    if dead:
        logger.warning(
            "broadcast dropped %s dead WebSocket(s); active_clients=%s",
            len(dead),
            len(state.ws_connections),
        )
