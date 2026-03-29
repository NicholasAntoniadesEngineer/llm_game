"""FastAPI application — serves frontend and WebSocket."""

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
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

# Callbacks — set by main.py after engine is created
reset_callback = None
start_callback = None  # Called with (city_name, year) when user clicks Start
resume_callback = None  # Resume build after API/network pause
llm_settings_callback = None  # async (overrides: dict) — persist + apply LLM routing from UI

app = FastAPI(title="Roma Aeterna")

static_dir = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


ASSET_VERSION = str(int(time.time()))  # cache-bust on every server restart

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
    }


@app.get("/api/llm-settings")
async def api_get_llm_settings():
    """Load AI routing for the Configure AI panel (HTTP so it always works without WebSocket timing)."""
    return _build_llm_settings_payload()


@app.post("/api/llm-settings")
async def api_post_llm_settings(request: Request):
    """Save AI routing from the Configure AI panel."""
    body = await request.json()
    overrides = body.get("overrides", {})
    if isinstance(overrides, dict) and llm_settings_callback:
        await llm_settings_callback(overrides)
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_connections.append(websocket)
    logger.info(f"Client connected ({len(ws_connections)} total)")

    try:
        await websocket.send_json(world.to_dict())
        # Send scenario if already started (reconnect case)
        import config
        if config.SCENARIO:
            await websocket.send_json({
                "type": "scenario",
                "city": config.SCENARIO["location"],
                "period": config.SCENARIO["period"],
                "description": config.SCENARIO.get("description", ""),
            })
        replay = chat_history[-CHAT_REPLAY_MAX_MESSAGES:]
        for msg in replay:
            await websocket.send_json(msg)
    except Exception:
        if websocket in ws_connections:
            ws_connections.remove(websocket)
        return

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "tile_info":
                tile = world.get_tile(data.get("x", 0), data.get("y", 0))
                if tile:
                    await websocket.send_json({"type": "tile_detail", "tile": tile.to_dict()})
            elif data.get("type") == "start":
                city_name = data.get("city", "Rome")
                year = data.get("year", 0)
                logger.info(f"Start requested: {city_name}, year {year}")
                if start_callback:
                    await start_callback(city_name, year)
            elif data.get("type") == "reset":
                logger.info("Reset requested by client")
                if reset_callback:
                    await reset_callback()
            elif data.get("type") == "resume":
                logger.info("Resume requested by client after pause")
                if resume_callback:
                    await resume_callback()
            elif data.get("type") == "get_llm_settings":
                await websocket.send_json(_build_llm_settings_payload())
            elif data.get("type") == "save_llm_settings":
                overrides = data.get("overrides")
                if isinstance(overrides, dict) and llm_settings_callback:
                    await llm_settings_callback(overrides)
                    await websocket.send_json({"type": "llm_settings_saved", "ok": True})
    except WebSocketDisconnect:
        if websocket in ws_connections:
            ws_connections.remove(websocket)
        logger.info(f"Client disconnected ({len(ws_connections)} total)")
    except Exception:
        if websocket in ws_connections:
            ws_connections.remove(websocket)


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
