"""FastAPI application — serves frontend and WebSocket."""

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from world.state import WorldState
from orchestration.bus import MessageBus
from config import (
    GRID_WIDTH,
    GRID_HEIGHT,
    CITIES,
    create_scenario,
    format_year,
    CHAT_HISTORY_MAX_MESSAGES,
    CHAT_REPLAY_MAX_MESSAGES,
)

logger = logging.getLogger("roma.server")

# Shared state
world = WorldState(GRID_WIDTH, GRID_HEIGHT)
bus = MessageBus()
ws_connections: list[WebSocket] = []
chat_history: list[dict] = []
ws_connection_sequence = 0


def _ws_label(websocket: WebSocket) -> str:
    try:
        c = websocket.client
        if c:
            return f"{c.host}:{c.port}"
    except Exception:
        pass
    return "unknown-client"

# Callbacks — set by main.py after engine is created
reset_callback = None
start_callback = None  # Called with (city_name, year) when user clicks Start
resume_callback = None  # Resume build after API/network pause
llm_settings_callback = None  # async (overrides: dict) — persist + apply LLM routing from UI
# Callable returning whether BuildEngine.run() is active — used to avoid replaying stale "paused" on refresh.
engine_is_running = None

app = FastAPI(title="Roma Aeterna")

static_dir = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


ASSET_VERSION = str(int(time.time()))  # cache-bust on every server restart


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    """Browsers request /favicon.ico by default; serve the SVG icon without a 404."""
    return RedirectResponse(url=f"/static/favicon.svg?v={ASSET_VERSION}", status_code=302)


@app.get("/")
async def index():
    html = (static_dir / "index.html").read_text()
    html = html.replace("__ASSET_VERSION__", ASSET_VERSION)
    return HTMLResponse(html)


@app.get("/api/cities")
async def get_cities():
    return [
        {
            "name": c["name"],
            "year_min": c["year_min"],
            "year_max": c["year_max"],
            "description": c["description"],
        }
        for c in CITIES
    ]


def _build_llm_settings_payload() -> dict:
    """Same shape as WebSocket message type llm_settings (for UI)."""
    import llm_agents
    from token_usage import STORE as TOKEN_USAGE_STORE

    agents_payload = {}
    for key in llm_agents.AGENT_LLM:
        spec = llm_agents.get_agent_llm_spec(key)
        row = {}
        for k, v in spec.items():
            if k == "openai_api_key":
                row["has_openai_api_key"] = bool(v)
            else:
                row[k] = v
        agents_payload[key] = row
    return {
        "type": "llm_settings",
        "agents": agents_payload,
        "labels": llm_agents.AGENT_LLM_LABELS,
        "token_usage": TOKEN_USAGE_STORE.to_payload(),
    }


@app.get("/api/llm-settings")
async def api_get_llm_settings():
    """Load AI routing for the Configure AI panel (HTTP so it always works without WebSocket timing)."""
    logger.info("GET /api/llm-settings")
    return _build_llm_settings_payload()


@app.post("/api/llm-settings")
async def api_post_llm_settings(request: Request):
    """Save AI routing from the Configure AI panel."""
    body = await request.json()
    overrides = body.get("overrides", {})
    logger.info(f"POST /api/llm-settings overrides_keys={list(overrides.keys()) if isinstance(overrides, dict) else 'invalid'}")
    if isinstance(overrides, dict) and llm_settings_callback:
        await llm_settings_callback(overrides)
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_connections.append(websocket)
    global ws_connection_sequence
    ws_connection_sequence += 1
    conn_id = ws_connection_sequence
    logger.info(f"[ws#{conn_id}] connected client={_ws_label(websocket)} total={len(ws_connections)}")

    try:
        import config as config_module

        logger.info(f"[ws#{conn_id}] send world_state")
        await websocket.send_json(world.to_dict())
        # Send scenario if already started (reconnect case)
        if config_module.SCENARIO:
            logger.info(f"[ws#{conn_id}] send scenario city={config_module.SCENARIO.get('location')} period={config_module.SCENARIO.get('period')}")
            await websocket.send_json({
                "type": "scenario",
                "city": config_module.SCENARIO["location"],
                "period": config_module.SCENARIO["period"],
                "description": config_module.SCENARIO.get("description", ""),
            })
        # Do not replay old "paused" messages: they re-open the error overlay on every refresh
        # even when the build has moved on. If the engine is actually stopped on pause, we send
        # the latest paused payload once below.
        replay = [
            m
            for m in chat_history[-CHAT_REPLAY_MAX_MESSAGES:]
            if m.get("type") != "paused"
        ]
        if replay:
            logger.info(f"[ws#{conn_id}] replay messages count={len(replay)} (paused excluded)")
        for msg in replay:
            await websocket.send_json(msg)

        if (
            engine_is_running is not None
            and not engine_is_running()
            and getattr(config_module, "SCENARIO", None)
        ):
            last_paused = next(
                (m for m in reversed(chat_history) if m.get("type") == "paused"),
                None,
            )
            if last_paused:
                logger.info(f"[ws#{conn_id}] send active paused state (engine not running)")
                await websocket.send_json(last_paused)
    except Exception:
        if websocket in ws_connections:
            ws_connections.remove(websocket)
        logger.exception(f"[ws#{conn_id}] error during initial send; disconnected total={len(ws_connections)}")
        return

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            logger.info(f"[ws#{conn_id}] recv type={msg_type}")
            if msg_type == "tile_info":
                tile = world.get_tile(data.get("x", 0), data.get("y", 0))
                if tile:
                    await websocket.send_json({"type": "tile_detail", "tile": tile.to_dict()})
            elif msg_type == "start":
                city_name = data.get("city", "Rome")
                year = data.get("year", 0)
                logger.info(f"[ws#{conn_id}] start requested city={city_name} year={year}")
                if start_callback:
                    await start_callback(city_name, year)
            elif msg_type == "reset":
                logger.info(f"[ws#{conn_id}] reset requested")
                if reset_callback:
                    await reset_callback()
            elif msg_type == "resume":
                logger.info(f"[ws#{conn_id}] resume requested")
                if resume_callback:
                    await resume_callback()
            elif msg_type == "get_llm_settings":
                logger.info(f"[ws#{conn_id}] send llm_settings")
                await websocket.send_json(_build_llm_settings_payload())
            elif msg_type == "save_llm_settings":
                overrides = data.get("overrides")
                logger.info(f"[ws#{conn_id}] save_llm_settings overrides_keys={list(overrides.keys()) if isinstance(overrides, dict) else 'invalid'}")
                if isinstance(overrides, dict) and llm_settings_callback:
                    await llm_settings_callback(overrides)
                    await websocket.send_json({"type": "llm_settings_saved", "ok": True})
            else:
                logger.info(f"[ws#{conn_id}] ignored unknown message type={msg_type}")
    except WebSocketDisconnect:
        if websocket in ws_connections:
            ws_connections.remove(websocket)
        logger.info(f"[ws#{conn_id}] disconnected client={_ws_label(websocket)} total={len(ws_connections)}")
    except Exception:
        if websocket in ws_connections:
            ws_connections.remove(websocket)
        logger.exception(f"[ws#{conn_id}] unexpected error; disconnected total={len(ws_connections)}")


async def broadcast(message: dict):
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
        chat_history.append(message)
        if len(chat_history) > CHAT_HISTORY_MAX_MESSAGES:
            del chat_history[: len(chat_history) - CHAT_HISTORY_MAX_MESSAGES]

    dead = []
    for ws in ws_connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_connections.remove(ws)
