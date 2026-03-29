"""FastAPI application — serves frontend and WebSocket."""

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from world.state import WorldState
from orchestration.bus import MessageBus
from config import GRID_WIDTH, GRID_HEIGHT

logger = logging.getLogger("roma.server")

# Shared state
world = WorldState(GRID_WIDTH, GRID_HEIGHT)
bus = MessageBus()
ws_connections: list[WebSocket] = []
chat_history: list[dict] = []

# Reset callback — set by main.py after engine is created
reset_callback = None

app = FastAPI(title="Roma Aeterna")

static_dir = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


ASSET_VERSION = str(int(time.time()))  # cache-bust on every server restart

@app.get("/")
async def index():
    html = (static_dir / "index.html").read_text()
    html = html.replace("__ASSET_VERSION__", ASSET_VERSION)
    return HTMLResponse(html)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_connections.append(websocket)
    logger.info(f"Client connected ({len(ws_connections)} total)")

    try:
        await websocket.send_json(world.to_dict())
        # Send city/period info for UI
        from config import SCENARIO
        await websocket.send_json({
            "type": "scenario",
            "city": SCENARIO["location"],
            "period": SCENARIO["period"],
            "description": SCENARIO.get("description", ""),
        })
        for msg in chat_history:
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
            elif data.get("type") == "reset":
                logger.info("Reset requested by client")
                if reset_callback:
                    await reset_callback()
    except WebSocketDisconnect:
        if websocket in ws_connections:
            ws_connections.remove(websocket)
        logger.info(f"Client disconnected ({len(ws_connections)} total)")
    except Exception:
        if websocket in ws_connections:
            ws_connections.remove(websocket)


async def broadcast(message: dict):
    if message.get("type") in ("chat", "phase", "timeline", "master_plan", "map_description", "map_image"):
        chat_history.append(message)

    dead = []
    for ws in ws_connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_connections.remove(ws)
