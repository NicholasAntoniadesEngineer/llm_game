"""FastAPI application — serves frontend and WebSocket."""

import asyncio
import logging
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from core.config import Config
from server.state import AppState
from server.broadcast import attach_engine_ui_to_message, broadcast

logger = logging.getLogger("eternal.server")


def _ws_label(websocket: WebSocket) -> str:
    try:
        c = websocket.client
        if c:
            return f"{c.host}:{c.port}"
    except Exception:
        pass
    return "unknown-client"


def build_app(state: AppState, system_configuration: "Config", lifespan=None):
    """Construct the FastAPI app. Optional ``lifespan`` is set from main.py (banner, auto-resume). Config injected for all parameters."""
    static_dir = Path("static")  # relative path only
    app = FastAPI(title="Roma Aeterna", lifespan=lifespan)
    app.state.config = system_configuration

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

    @app.get("/api/health")
    async def api_health():
        """Health check endpoint -- returns current server status, tile/district counts, and token usage."""
        from core.token_usage import get_token_summary

        tile_count = len(state.world.tiles)
        district_count = 0
        is_running = False
        if state.engine_is_running is not None:
            is_running = state.engine_is_running()
        # Extract district count from the most recent phase message in chat history
        for msg in reversed(state.chat_history):
            if msg.get("type") == "phase" and msg.get("total_districts"):
                district_count = msg["total_districts"]
                break

        token_summary = get_token_summary(system_configuration=system_configuration)
        total_tokens = token_summary.get("total_tokens", 0)

        return {
            "status": "ok",
            "tiles": tile_count,
            "districts": district_count,
            "running": is_running,
            "tokens": total_tokens,
        }

    @app.get("/api/cities")
    async def get_cities():
        """Uses config for cities list (from CSV-driven loader)."""
        return [
            {
                "name": c["name"],
                "year_min": c["year_min"],
                "year_max": c["year_max"],
                "description": c["description"],
            }
            for c in system_configuration.get_cities_list()
        ]

    @app.get("/api/session")
    async def api_session():
        """Whether a saved scenario exists (for Continue vs new city on reload)."""
        scen = state.run_session.scenario
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
        return _build_llm_settings_payload(system_configuration)

    @app.post("/api/llm-settings")
    async def api_post_llm_settings(request: Request):
        """Save AI routing from the Configure AI panel."""
        try:
            body = await request.json()
        except Exception as json_error:
            raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from json_error
        overrides = body.get("overrides", {})
        logger.info(
            "POST /api/llm-settings overrides_keys=%s",
            list(overrides.keys()) if isinstance(overrides, dict) else "invalid",
        )
        if not isinstance(overrides, dict):
            raise HTTPException(status_code=400, detail="Field 'overrides' must be a JSON object.")
        if not state.llm_settings_callback:
            raise HTTPException(
                status_code=503,
                detail="LLM settings persistence is not configured for this process.",
            )
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
            logger.info(f"[ws#{conn_id}] send world_state")
            _world_payload = attach_engine_ui_to_message(state, state.world.to_dict())
            await websocket.send_json(_world_payload)
            # Send scenario if already started (reconnect case)
            if state.run_session.scenario:
                _sc = state.run_session.scenario
                logger.info(f"[ws#{conn_id}] send scenario city={_sc.get('location')} period={_sc.get('period')}")
                _scenario_payload = {
                    "type": "scenario",
                    "city": _sc["location"],
                    "period": _sc["period"],
                    "year": _sc.get("focus_year"),
                    "description": _sc.get("description", ""),
                    "started_at_s": _sc.get("started_at_s"),
                }
                await websocket.send_json(attach_engine_ui_to_message(state, _scenario_payload))
            # Do not replay old "paused" messages: they re-open the error overlay on every refresh
            # even when the build has moved on. If the engine is actually stopped on pause, we send
            # the latest paused payload once below.
            replay = [
                m
                for m in state.chat_history[-state.system_configuration.chat_replay_max_messages:]
                if m.get("type") != "paused"
            ]
            if replay:
                logger.info(f"[ws#{conn_id}] replay messages count={len(replay)} (paused excluded)")
            for msg in replay:
                if isinstance(msg, dict):
                    await websocket.send_json(attach_engine_ui_to_message(state, dict(msg)))
                else:
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

            from core.token_usage import aggregate_for_ui as token_aggregate_for_ui
            from core.token_usage import get_token_usage_store

            tu = token_aggregate_for_ui()
            if any((v.get("total_tokens") or 0) > 0 for v in tu.values()):
                _tu_payload = {
                    "type": "token_usage",
                    "by_ui_agent": tu,
                    "by_llm_key": get_token_usage_store().to_payload(),
                }
                await websocket.send_json(attach_engine_ui_to_message(state, _tu_payload))

            # Only reopen the pause overlay if the *latest* persisted event is still "paused".
            # Scanning the whole history for any old paused message made every refresh show a stale
            # API error after the build had already moved on (phase/chat/complete).
            if (
                state.engine_is_running is not None
                and not state.engine_is_running()
                and state.run_session.scenario
                and state.chat_history
                and state.chat_history[-1].get("type") == "paused"
            ):
                logger.info(f"[ws#{conn_id}] send active paused state (engine not running, last event=paused)")
                paused_payload = dict(state.chat_history[-1])
                paused_payload["suggest_auto_resume"] = True
                await websocket.send_json(paused_payload)

            _engine_snap = False
            if state.engine_is_running is not None:
                try:
                    _engine_snap = bool(state.engine_is_running())
                except Exception:
                    _engine_snap = False
            _token_sent = any((v.get("total_tokens") or 0) > 0 for v in tu.values())
            logger.info(
                "[ws#%s] initial burst summary: world_tiles=%s scenario=%s replay_msgs=%s "
                "agent_status_cached=%s token_usage_sent=%s engine_running=%s chat_history_len=%s",
                conn_id,
                len(state.world.tiles),
                bool(state.run_session.scenario),
                len(replay),
                len(state.agent_status_by_agent),
                _token_sent,
                _engine_snap,
                len(state.chat_history),
            )
        except Exception:
            dur = round(time.time() - conn_start, 1)
            logger.exception(f"[ws#{conn_id}] DISCONNECTED (initial send error) after {dur}s total={len(state.ws_connections)}")
            return

        # Only register for broadcast() AFTER the replay is complete, so in-flight
        # broadcasts cannot interleave with the ordered initial-state messages.
        state.ws_connections.append(websocket)
        logger.info(f"[ws#{conn_id}] replay done, registered for broadcast total={len(state.ws_connections)}")

        async def _ws_handle_tile_info(payload: dict) -> None:
            tile = state.world.get_tile(payload.get("x", 0), payload.get("y", 0))
            if tile:
                await websocket.send_json({"type": "tile_detail", "tile": tile.to_dict()})

        async def _ws_handle_start(payload: dict) -> None:
            city_name = payload.get("city", "Rome")
            year = payload.get("year", 0)
            logger.info(f"[ws#{conn_id}] start requested city={city_name} year={year}")
            if state.start_callback:
                await state.start_callback(city_name, year)

        async def _ws_handle_reset(_payload: dict) -> None:
            logger.info(f"[ws#{conn_id}] reset requested")
            if state.reset_callback:
                await state.reset_callback()

        async def _ws_handle_resume(_payload: dict) -> None:
            logger.info(f"[ws#{conn_id}] resume requested")
            if state.resume_callback:
                await state.resume_callback()

        async def _ws_handle_pause(_payload: dict) -> None:
            logger.info(f"[ws#{conn_id}] pause requested")
            if state.pause_callback:
                await state.pause_callback()

        async def _ws_handle_get_llm_settings(_payload: dict) -> None:
            logger.info(f"[ws#{conn_id}] send llm_settings")
            await websocket.send_json(_build_llm_settings_payload(system_configuration))

        async def _ws_handle_save_llm_settings(payload: dict) -> None:
            overrides = payload.get("overrides")
            logger.info(
                f"[ws#{conn_id}] save_llm_settings overrides_keys="
                f"{list(overrides.keys()) if isinstance(overrides, dict) else 'invalid'}"
            )
            if isinstance(overrides, dict) and state.llm_settings_callback:
                await state.llm_settings_callback(overrides)
                await websocket.send_json({"type": "llm_settings_saved", "ok": True})

        async def _ws_handle_restart_server(_payload: dict) -> None:
            logger.info(f"[ws#{conn_id}] restart_server (same as POST /api/restart-server)")
            if state.restart_server_callback:
                try:
                    result = await state.restart_server_callback()
                    await websocket.send_json({"type": "restart_server_result", **result})
                except Exception as restart_err:
                    logger.exception(f"[ws#{conn_id}] restart_server failed")
                    await websocket.send_json(
                        {"type": "restart_server_result", "ok": False, "error": str(restart_err)}
                    )
            else:
                await websocket.send_json(
                    {"type": "restart_server_result", "ok": False, "error": "not configured"}
                )

        async def _ws_handle_reset_timeline(_payload: dict) -> None:
            logger.info(f"[ws#{conn_id}] reset_timeline (same as POST /api/reset-timeline)")
            if state.reset_timeline_callback:
                try:
                    result = await state.reset_timeline_callback()
                    await websocket.send_json({"type": "reset_timeline_result", **result})
                except Exception as timeline_err:
                    logger.exception(f"[ws#{conn_id}] reset_timeline failed")
                    await websocket.send_json(
                        {"type": "reset_timeline_result", "ok": False, "error": str(timeline_err)}
                    )
            else:
                await websocket.send_json(
                    {"type": "reset_timeline_result", "ok": False, "error": "not configured"}
                )

        async def _ws_handle_ping(_payload: dict) -> None:
            engine_running = False
            if state.engine_is_running is not None:
                try:
                    engine_running = bool(state.engine_is_running())
                except Exception:
                    engine_running = False
            await websocket.send_json(
                {
                    "type": "pong",
                    "server_time": time.time(),
                    "engine_running": engine_running,
                    "scenario_active": bool(state.run_session.scenario),
                }
            )

        _ws_incoming_handlers = {
            "tile_info": _ws_handle_tile_info,
            "start": _ws_handle_start,
            "reset": _ws_handle_reset,
            "resume": _ws_handle_resume,
            "pause": _ws_handle_pause,
            "get_llm_settings": _ws_handle_get_llm_settings,
            "save_llm_settings": _ws_handle_save_llm_settings,
            "restart_server": _ws_handle_restart_server,
            "reset_timeline": _ws_handle_reset_timeline,
            "ping": _ws_handle_ping,
        }

        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type")
                if msg_type != "ping":
                    logger.info(f"[ws#{conn_id}] recv type={msg_type}")
                handler = _ws_incoming_handlers.get(msg_type) if isinstance(msg_type, str) else None
                if handler is not None:
                    await handler(data if isinstance(data, dict) else {})
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


def _build_llm_settings_payload(system_configuration: Config) -> dict:
    """Same shape as WebSocket message type llm_settings (for UI)."""
    from agents import llm_routing as llm_agents
    from core.token_usage import get_token_usage_store

    agents_payload = {}
    for key in llm_agents.iter_registered_agent_llm_keys():
        spec = llm_agents.get_agent_llm_spec(key)
        row = {}
        for k, v in spec.items():
            if k == "openai_api_key":
                row["has_openai_api_key"] = bool(v)
            else:
                row[k] = v
        agents_payload[key] = row

    llm_defaults_raw = system_configuration.load_llm_defaults()
    xai_model_suggestions = list(llm_defaults_raw["xai"]["model_suggestions"])
    openai_model_suggestions = list(llm_defaults_raw["openai_compatible"]["model_suggestions"])
    combined_suggestions = list(dict.fromkeys(xai_model_suggestions + openai_model_suggestions))

    return {
        "type": "llm_settings",
        "agents": agents_payload,
        "labels": llm_agents.get_agent_llm_labels_dictionary(),
        "token_usage": get_token_usage_store().to_payload(),
        "xai_model_suggestions": xai_model_suggestions,
        "openai_compatible_model_suggestions": openai_model_suggestions,
        "model_id_suggestions": combined_suggestions,
    }
