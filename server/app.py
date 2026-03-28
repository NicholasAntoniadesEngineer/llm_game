"""FastAPI application — serves frontend and WebSocket."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from world.state import WorldState
from orchestration.bus import MessageBus
from config import GRID_WIDTH, GRID_HEIGHT

logger = logging.getLogger("roma.server")

# Shared state
world = WorldState(GRID_WIDTH, GRID_HEIGHT)
bus = MessageBus()
ws_connections: list[WebSocket] = []

app = FastAPI(title="Roma Aeterna")

static_dir = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(static_dir / "index.html"))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_connections.append(websocket)
    logger.info(f"Client connected ({len(ws_connections)} total)")

    # Send full world state on connect
    try:
        await websocket.send_json(world.to_dict())
    except Exception:
        ws_connections.remove(websocket)
        return

    # Listen for client messages (tile info requests)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "tile_info":
                tile = world.get_tile(data.get("x", 0), data.get("y", 0))
                if tile:
                    await websocket.send_json({
                        "type": "tile_detail",
                        "tile": tile.to_dict(),
                    })
    except WebSocketDisconnect:
        ws_connections.remove(websocket)
        logger.info(f"Client disconnected ({len(ws_connections)} total)")
    except Exception:
        if websocket in ws_connections:
            ws_connections.remove(websocket)


async def broadcast(message: dict):
    """Send a message to all connected WebSocket clients."""
    dead = []
    for ws in ws_connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_connections.remove(ws)
