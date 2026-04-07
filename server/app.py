"""FastAPI application — serves frontend and WebSocket."""

import asyncio
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from core.config import (
    CITIES,
    CHAT_HISTORY_MAX_MESSAGES,
    CHAT_REPLAY_MAX_MESSAGES,
)
from server.state import AppState
from server.broadcast import broadcast

logger = logging.getLogger("eternal.server")


def _ws_label(websocket: WebSocket) -> str:
    try:
        c = websocket.client
        if c:
            return f"{c.host}:{c.port}"
    except Exception:
        pass
    return "unknown-client"


def build_app(state: AppState, lifespan=None):
    """Construct the FastAPI app. Optional ``lifespan`` is set from main.py (banner, auto-resume)."""
    static_dir = Path(__file__).parent.parent / "static"
    app = FastAPI(title="Roma Aeterna", lifespan=lifespan)

    class _RedirectStaticIndexMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.url.path == "/static/index.html":
                return RedirectResponse(url="/", status_code=302)
            return await call_next(request)

    app.add_middleware(_RedirectStaticIndexMiddleware)

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon_ico():
        """Browsers request /favicon.ico by default; serve the SVG icon without a 404."""
        return RedirectResponse(url=f"/static/favicon.svg?v={state.asset_version}", status_code=302)

    @app.get("/")
    async def index():
        html = (static_dir / "index.html").read_text()
        html = html.replace("__ASSET_VERSION__", state.asset_version)
        return HTMLResponse(
            content=html,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )

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

    @app.get("/api/session")
    async def api_session():
        """Whether a saved scenario exists (for Continue vs new city on reload)."""
        from core import config as config_module

        scen = getattr(config_module, "SCENARIO", None)
        if not scen or not isinstance(scen, dict):
            return {"has_active_scenario": False}
        return {
            "has_active_scenario": True,
            "city": scen.get("location"),
            "period": scen.get("period"),
            "year": scen.get("focus_year"),
            "started_at_s": scen.get("started_at_s"),
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
        if isinstance(overrides, dict) and state.llm_settings_callback:
            await state.llm_settings_callback(overrides)
        return {"ok": True}

    @app.get("/api/logs")
    async def api_get_logs():
        """Download the full run log as plain text."""
        from core.run_log import get_log_text
        from fastapi.responses import Response

        logger.info("GET /api/logs")
        text = get_log_text()
        return Response(
            content=text,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="eternal_cities_run.log"',
                "Cache-Control": "no-store",
            },
        )

    @app.post("/api/restart-server")
    async def api_restart_server():
        """Persist save, then touch reload_trigger.txt so uvicorn --reload restarts (keeps caches)."""
        logger.info("POST /api/restart-server")
        if state.restart_server_callback:
            return await state.restart_server_callback()
        return {"ok": False, "error": "not configured"}

    @app.post("/api/reset-timeline")
    async def api_reset_timeline():
        """New started_at_s only; does not delete districts/survey caches or roma_save.json."""
        logger.info("POST /api/reset-timeline")
        if state.reset_timeline_callback:
            return await state.reset_timeline_callback()
        return {"ok": False, "error": "not configured"}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        state.ws_connection_sequence += 1
        conn_id = state.ws_connection_sequence
        conn_start = time.time()
        logger.info(f"[ws#{conn_id}] CONNECTED client={_ws_label(websocket)} (replay pending)")

        try:
            from core import config as config_module

            logger.info(f"[ws#{conn_id}] send world_state")
            await websocket.send_json(state.world.to_dict())
            # Send scenario if already started (reconnect case)
            if config_module.SCENARIO:
                logger.info(f"[ws#{conn_id}] send scenario city={config_module.SCENARIO.get('location')} period={config_module.SCENARIO.get('period')}")
                await websocket.send_json({
                    "type": "scenario",
                    "city": config_module.SCENARIO["location"],
                    "period": config_module.SCENARIO["period"],
                    "year": config_module.SCENARIO.get("focus_year"),
                    "description": config_module.SCENARIO.get("description", ""),
                    "started_at_s": config_module.SCENARIO.get("started_at_s"),
                })
            # Do not replay old "paused" messages: they re-open the error overlay on every refresh
            # even when the build has moved on. If the engine is actually stopped on pause, we send
            # the latest paused payload once below.
            replay = [
                m
                for m in state.chat_history[-CHAT_REPLAY_MAX_MESSAGES:]
                if m.get("type") != "paused"
            ]
            if replay:
                logger.info(f"[ws#{conn_id}] replay messages count={len(replay)} (paused excluded)")
            for msg in replay:
                await websocket.send_json(msg)

            if state.agent_status_by_agent:
                if state.engine_is_running is not None and not state.engine_is_running():
                    for agent_key in list(state.agent_status_by_agent.keys()):
                        cached = state.agent_status_by_agent[agent_key]
                        if cached.get("status") == "thinking":
                            agent_id = cached.get("agent", agent_key)
                            if isinstance(agent_id, str):
                                fixed = {"type": "agent_status", "agent": agent_id, "status": "idle"}
                                state.agent_status_by_agent[agent_key] = fixed
                                logger.info(
                                    "[ws#%s] repaired stale cached thinking -> idle (engine not running) agent=%s",
                                    conn_id,
                                    agent_id,
                                )
                logger.info(f"[ws#{conn_id}] send cached agent_status count={len(state.agent_status_by_agent)}")
                for cached in state.agent_status_by_agent.values():
                    await websocket.send_json(cached)

            from core.token_usage import STORE as token_usage_store
            from core.token_usage import aggregate_for_ui as token_aggregate_for_ui

            tu = token_aggregate_for_ui()
            if any((v.get("total_tokens") or 0) > 0 for v in tu.values()):
                await websocket.send_json(
                    {
                        "type": "token_usage",
                        "by_ui_agent": tu,
                        "by_llm_key": token_usage_store.to_payload(),
                    }
                )

            # Only reopen the pause overlay if the *latest* persisted event is still "paused".
            # Scanning the whole history for any old paused message made every refresh show a stale
            # API error after the build had already moved on (phase/chat/complete).
            if (
                state.engine_is_running is not None
                and not state.engine_is_running()
                and getattr(config_module, "SCENARIO", None)
                and state.chat_history
                and state.chat_history[-1].get("type") == "paused"
            ):
                logger.info(f"[ws#{conn_id}] send active paused state (engine not running, last event=paused)")
                paused_payload = dict(state.chat_history[-1])
                paused_payload["suggest_auto_resume"] = True
                await websocket.send_json(paused_payload)
        except Exception:
            dur = round(time.time() - conn_start, 1)
            logger.exception(f"[ws#{conn_id}] DISCONNECTED (initial send error) after {dur}s total={len(state.ws_connections)}")
            return

        # Only register for broadcast() AFTER the replay is complete, so in-flight
        # broadcasts cannot interleave with the ordered initial-state messages.
        state.ws_connections.append(websocket)
        logger.info(f"[ws#{conn_id}] replay done, registered for broadcast total={len(state.ws_connections)}")

        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type")
                logger.info(f"[ws#{conn_id}] recv type={msg_type}")
                if msg_type == "tile_info":
                    tile = state.world.get_tile(data.get("x", 0), data.get("y", 0))
                    if tile:
                        await websocket.send_json({"type": "tile_detail", "tile": tile.to_dict()})
                elif msg_type == "start":
                    city_name = data.get("city", "Rome")
                    year = data.get("year", 0)
                    logger.info(f"[ws#{conn_id}] start requested city={city_name} year={year}")
                    if state.start_callback:
                        await state.start_callback(city_name, year)
                elif msg_type == "reset":
                    logger.info(f"[ws#{conn_id}] reset requested")
                    if state.reset_callback:
                        await state.reset_callback()
                elif msg_type == "resume":
                    logger.info(f"[ws#{conn_id}] resume requested")
                    if state.resume_callback:
                        await state.resume_callback()
                elif msg_type == "pause":
                    logger.info(f"[ws#{conn_id}] pause requested")
                    if state.pause_callback:
                        await state.pause_callback()
                elif msg_type == "get_llm_settings":
                    logger.info(f"[ws#{conn_id}] send llm_settings")
                    await websocket.send_json(_build_llm_settings_payload())
                elif msg_type == "save_llm_settings":
                    overrides = data.get("overrides")
                    logger.info(f"[ws#{conn_id}] save_llm_settings overrides_keys={list(overrides.keys()) if isinstance(overrides, dict) else 'invalid'}")
                    if isinstance(overrides, dict) and state.llm_settings_callback:
                        await state.llm_settings_callback(overrides)
                        await websocket.send_json({"type": "llm_settings_saved", "ok": True})
                elif msg_type == "restart_server":
                    logger.info(f"[ws#{conn_id}] restart_server (same as POST /api/restart-server)")
                    if state.restart_server_callback:
                        try:
                            result = await state.restart_server_callback()
                            await websocket.send_json({"type": "restart_server_result", **result})
                        except Exception as e:
                            logger.exception(f"[ws#{conn_id}] restart_server failed")
                            await websocket.send_json(
                                {"type": "restart_server_result", "ok": False, "error": str(e)}
                            )
                    else:
                        await websocket.send_json(
                            {"type": "restart_server_result", "ok": False, "error": "not configured"}
                        )
                elif msg_type == "reset_timeline":
                    logger.info(f"[ws#{conn_id}] reset_timeline (same as POST /api/reset-timeline)")
                    if state.reset_timeline_callback:
                        try:
                            result = await state.reset_timeline_callback()
                            await websocket.send_json({"type": "reset_timeline_result", **result})
                        except Exception as e:
                            logger.exception(f"[ws#{conn_id}] reset_timeline failed")
                            await websocket.send_json(
                                {"type": "reset_timeline_result", "ok": False, "error": str(e)}
                            )
                    else:
                        await websocket.send_json(
                            {"type": "reset_timeline_result", "ok": False, "error": "not configured"}
                        )
                elif msg_type == "ping":
                    pass  # Keepalive — no response needed
                else:
                    logger.info(f"[ws#{conn_id}] ignored unknown message type={msg_type}")
        except WebSocketDisconnect:
            dur = round(time.time() - conn_start, 1)
            if websocket in state.ws_connections:
                state.ws_connections.remove(websocket)
            logger.info(f"[ws#{conn_id}] DISCONNECTED (clean) after {dur}s client={_ws_label(websocket)} total={len(state.ws_connections)}")
        except Exception:
            dur = round(time.time() - conn_start, 1)
            if websocket in state.ws_connections:
                state.ws_connections.remove(websocket)
            logger.exception(f"[ws#{conn_id}] DISCONNECTED (error) after {dur}s total={len(state.ws_connections)}")

    return app


def _build_llm_settings_payload() -> dict:
    """Same shape as WebSocket message type llm_settings (for UI)."""
    from agents import llm_routing as llm_agents
    from core.token_usage import STORE as TOKEN_USAGE_STORE

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
        "claude_cli_models": list(llm_agents.CLAUDE_CLI_MODEL_CHOICES),
    }
