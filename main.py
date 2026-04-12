"""Eternal Cities — Entry point.

In-app **RESET** (web UI): full wipe — world, save, district + survey caches (Cartographus planning).

**Reload code** (browser button): hard refresh; does not restart Python or delete saves.

**Restart server** (POST /api/restart-server): saves state then touches `reload_trigger.txt` so uvicorn
`--reload` restarts the process — keeps `roma_save.json`, `roma_districts_cache.json`, and
`roma_surveys_cache.json`. Requires:

    ETERNAL_CITIES_RELOAD=1 python main.py

Static assets are cache-busted on each server restart (`?v=…`).
"""

import asyncio
import functools
import os
import sys
import time
import logging
from typing import Any
from contextlib import asynccontextmanager
import uvicorn
from pathlib import Path

from core import config
from server.state import AppState
from server.broadcast import broadcast as _broadcast_impl
from server.app import build_app
from agents import llm_routing as llm_agents
from orchestration.engine import BuildEngine
from agents import base as agents_base
from core.errors import SaveIndexError
from core.persistence import (
    clear_saves,
    load_llm_settings,
    load_blueprint,
    load_state,
    merge_llm_overrides_from_save,
    save_llm_settings,
    save_state,
    validate_blueprint_tile_invariants,
)
from core.config import CHAT_PERSIST_DEBOUNCE_S, HEARTBEAT_INTERVAL_S, LOG_LEVEL, create_scenario

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
    force=True,
)

from core.run_log import init_run_log
from core.heartbeat import start_heartbeat, stop_heartbeat

init_run_log()
logging.info(
    "Eternal Cities startup | ETERNAL_LOG_LEVEL=%s effective=%s argv=%s",
    os.environ.get("ETERNAL_LOG_LEVEL", "INFO"),
    logging.getLevelName(LOG_LEVEL),
    " ".join(sys.argv[:6]),
)

# Touched by POST /api/restart-server when ETERNAL_CITIES_RELOAD=1 so uvicorn --reload picks up a restart request.
RELOAD_SENTINEL = Path(__file__).resolve().parent / "reload_trigger.txt"

# pkill -f regex: only child processes from agents/base.py (claude --print --system-prompt ...).
# Broader patterns like "claude.*--print" can match unrelated Claude CLI usage.
CLAUDE_AGENT_PKILL_PATTERN = r"claude.*--print.*--system-prompt"

# On startup (including uvicorn auto-reload), kill orphaned Claude CLI processes
# from the previous server instance to prevent token waste.
import subprocess as _startup_sp
_startup_sp.run(["pkill", "-f", CLAUDE_AGENT_PKILL_PATTERN], capture_output=True)
logging.info("Startup: killed any orphaned Claude CLI processes")

BANNER = """
╔══════════════════════════════════════════════════╗
║                                                  ║
║          E T E R N A L   C I T I E S             ║
║                                                  ║
║     Waiting for city selection...                ║
║                                                  ║
║          Open: http://localhost:8000              ║
║                                                  ║
╚══════════════════════════════════════════════════╝
"""

load_llm_settings()

# Shared application state
state = AppState()

# Bind broadcast to the shared state so callers use broadcast(message) without needing to pass state.
broadcast = functools.partial(_broadcast_impl, state)

# Make the bound broadcast available for late imports (e.g. agents/base.py token_usage push).
import server.state as _server_state_mod
_server_state_mod.broadcast_fn = broadcast
agents_base.set_ui_broadcast(broadcast)

engine = BuildEngine(state.world, state.bus, broadcast, state.chat_history, run_session=state.run_session)

_heartbeat_thread = None


def _make_heartbeat_snapshot() -> dict[str, Any]:
    """Read-only snapshot for the background heartbeat (must not touch the asyncio loop)."""
    scen = state.run_session.scenario
    loc = ""
    if isinstance(scen, dict):
        loc = str(scen.get("location") or "")
    agents: dict[str, str] = {}
    try:
        for agent_key, msg in (state.agent_status_by_agent or {}).items():
            if isinstance(msg, dict):
                st = msg.get("status", "?")
                det = msg.get("detail")
                if det:
                    agents[str(agent_key)] = f"{st}: {str(det)[:60]}"
                else:
                    agents[str(agent_key)] = str(st)
    except Exception:
        logging.getLogger("eternal.main").exception("heartbeat snapshot: failed to read agent status")
    trace_snap = getattr(engine, "_trace_snapshot", None)
    if not isinstance(trace_snap, dict):
        trace_snap = {}
    return {
        "running": engine.running,
        "generation": engine.generation,
        "district_index": engine.district_index,
        "districts_count": len(engine.districts),
        "tiles_count": len(engine.world.tiles),
        "turn": engine.world.turn,
        "scenario_location": loc,
        "chat_messages": len(state.chat_history),
        "ws_clients": len(state.ws_connections),
        "trace": dict(trace_snap),
        "agents": agents,
    }


# Load saved state if available (tiles, chat, districts, scenario — survives server restarts)
try:
    saved = load_state(state.world)
except SaveIndexError:
    logging.exception("Save index is corrupt or unreadable — starting without disk restore")
    saved = None
if saved:
    loaded_chat, district_index, districts, loaded_generation, loaded_scenario = saved
    state.chat_history.extend(loaded_chat)
    engine.district_index = district_index
    engine.districts = districts
    engine.generation = loaded_generation
    state.run_session.scenario = loaded_scenario
    for msg in validate_blueprint_tile_invariants(state.world, load_blueprint()):
        logging.warning("Blueprint invariant: %s", msg)
    logging.info(
        "Restored from disk: district #%s, %s districts, %s chat messages — world + scenario kept for restart",
        district_index,
        len(districts),
        len(loaded_chat),
    )
else:
    logging.info("Starting fresh — awaiting user selection")


_engine_action_lock = asyncio.Lock()

# After chat/phase messages, persist index + chat to disk (debounced) so long LLM waits
# do not leave only stale snapshots if the process dies before the next tile save.
_persist_chat_generation = 0


async def _debounced_persist_after_chat(gen: int) -> None:
    await asyncio.sleep(CHAT_PERSIST_DEBOUNCE_S)
    if gen != _persist_chat_generation:
        return
    async with _engine_action_lock:
        if not state.run_session.scenario:
            return
        await asyncio.to_thread(
            save_state,
            state.world,
            state.chat_history,
            engine.district_index,
            engine.districts,
            engine.generation,
            scenario=state.run_session.scenario,
            flush_mode="incremental",
        )


def schedule_debounced_persist_after_chat() -> None:
    global _persist_chat_generation
    _persist_chat_generation += 1
    gen = _persist_chat_generation
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_debounced_persist_after_chat(gen))


async def handle_start(city_name, year):
    """User selected a city and year — create scenario and start engine."""
    async with _engine_action_lock:
        await _handle_start_inner(city_name, year)


async def _handle_start_inner(city_name, year):
    was_running = engine.running
    if was_running:
        engine.running = False
    await engine.cancel_run_task_join()
    if was_running:
        import subprocess

        await asyncio.to_thread(subprocess.run, ["pkill", "-f", CLAUDE_AGENT_PKILL_PATTERN], capture_output=True)

    await engine.abort_pipeline_tasks()
    engine.reset_pipeline_for_new_run()
    state.agent_status_by_agent.clear()
    await engine.broadcast_all_agents_idle()

    # Create and set the scenario
    new_scenario = create_scenario(city_name, year)
    state.run_session.scenario = new_scenario
    logging.info(f"Starting: {new_scenario['location']}, {new_scenario['period']}")

    # Reset world state for fresh build
    state.world.clear()
    state.world.current_period = new_scenario["period"]
    state.world.current_year = new_scenario["focus_year"]

    state.chat_history.clear()
    engine.districts = []
    engine.district_index = 0

    # Delete old caches
    clear_saves()

    # Broadcast scenario to all clients (engine UI fields added in server.broadcast.attach_engine_ui_to_message)
    await broadcast(state.world.to_dict())
    # Include climate data for atmosphere system
    climate = new_scenario.get("climate")
    await broadcast({
        "type": "scenario",
        "city": new_scenario["location"],
        "period": new_scenario["period"],
        "year": new_scenario.get("focus_year"),
        "description": new_scenario.get("description", ""),
        "started_at_s": new_scenario.get("started_at_s"),
        "climate": climate,
    })

    # Start the engine
    await engine.schedule_run()


async def handle_reset():
    """Reset world, clear save, restart engine."""
    async with _engine_action_lock:
        await _handle_reset_inner()


async def _handle_reset_inner():
    engine.running = False
    await engine.cancel_run_task_join()
    await engine.abort_pipeline_tasks()
    engine.reset_pipeline_for_new_run()

    import subprocess
    await asyncio.to_thread(subprocess.run, ["pkill", "-f", CLAUDE_AGENT_PKILL_PATTERN], capture_output=True)

    state.world.clear()
    state.world.current_period = ""
    state.world.current_year = -44

    state.chat_history.clear()
    engine.districts = []
    engine.district_index = 0
    state.agent_status_by_agent.clear()
    await engine.broadcast_all_agents_idle()

    clear_saves()

    # Back to selection screen
    state.run_session.scenario = None
    await broadcast(state.world.to_dict())


async def handle_pause():
    """User-initiated pause: stop the engine, kill all agent subprocesses, save state."""
    async with _engine_action_lock:
        await _handle_pause_inner()


async def _handle_pause_inner():
    if not state.run_session.scenario:
        logging.warning("Pause ignored: no active scenario")
        return
    if not engine.running:
        logging.warning("Pause ignored: engine not running")
        return
    engine.running = False
    # Cancel the main run task and all pipeline sub-tasks
    await engine.cancel_run_task_join()
    await engine.abort_pipeline_tasks()
    # Kill any Claude CLI subprocesses still running
    import subprocess
    await asyncio.to_thread(subprocess.run, ["pkill", "-f", CLAUDE_AGENT_PKILL_PATTERN], capture_output=True)
    await engine.broadcast_all_agents_idle()
    await asyncio.to_thread(
        save_state,
        state.world,
        state.chat_history,
        engine.district_index,
        engine.districts,
        engine.generation,
        scenario=state.run_session.scenario,
        flush_mode="full",
    )
    await broadcast({
        "type": "paused",
        "reason": "user_pause",
        "summary": "Build paused by user. All agent queries stopped.",
    })
    logging.info("Build paused by user — engine + CLI agents killed")


async def handle_resume():
    """Continue build after API rate limit / error / network pause."""
    async with _engine_action_lock:
        if not state.run_session.scenario:
            logging.warning("Resume ignored: no active scenario")
            return
        if engine.running:
            logging.warning("Resume ignored: engine already running")
            return
        await engine.schedule_run()


async def handle_llm_settings_save(overrides: dict):
    """Persist per-agent LLM routing from UI; merges empty API key fields with existing."""
    if not isinstance(overrides, dict):
        return
    current = llm_agents.get_runtime_overrides()
    merged = merge_llm_overrides_from_save(current, overrides)
    llm_agents.set_runtime_overrides(merged)
    await asyncio.to_thread(save_llm_settings, merged)


def _reload_env_enabled() -> bool:
    return os.environ.get("ETERNAL_CITIES_RELOAD", "").strip().lower() in ("1", "true", "yes")


async def handle_restart_server() -> dict:
    """
    Persist current world + chat + districts, then touch reload_trigger.txt so uvicorn reload
    restarts the process. Does NOT delete SAVE_FILE, DISTRICTS_CACHE, or SURVEYS_CACHE.
    Requires ETERNAL_CITIES_RELOAD=1 (watch mode).

    Touch is delayed so HTTP or WebSocket can return JSON before the process reloads.
    """
    if not _reload_env_enabled():
        return {
            "ok": False,
            "error": "ETERNAL_CITIES_RELOAD is not enabled",
            "hint": "Start with: ETERNAL_CITIES_RELOAD=1 python main.py",
        }
    # Fully stop the engine (cancel run task + pipeline) to prevent orphaned coroutines
    # and token spend after the process restarts.
    async with _engine_action_lock:
        was_running = engine.running
        if was_running:
            engine.running = False
        await engine.cancel_run_task_join()
        await engine.abort_pipeline_tasks()
    import subprocess as _sp
    await asyncio.to_thread(_sp.run, ["pkill", "-f", CLAUDE_AGENT_PKILL_PATTERN], capture_output=True)
    if state.run_session.scenario:
        await asyncio.to_thread(
            save_state,
            state.world,
            state.chat_history,
            engine.district_index,
            engine.districts,
            engine.generation,
            scenario=state.run_session.scenario,
            flush_mode="full",
        )

    async def _touch_reload_sentinel_after_response() -> None:
        await asyncio.sleep(0.45)
        try:
            await asyncio.to_thread(RELOAD_SENTINEL.touch)
            logging.info("reload_trigger.txt touched — uvicorn should restart (saves and caches kept)")
        except Exception:
            logging.exception("reload_trigger.txt touch failed")

    asyncio.create_task(_touch_reload_sentinel_after_response())
    return {"ok": True}


async def handle_reset_timeline() -> dict:
    """New run clock (started_at_s) only; keeps tiles, chat, districts, and caches on disk."""
    async with _engine_action_lock:
        scen = state.run_session.scenario
        if not scen or not isinstance(scen, dict):
            return {"ok": False, "error": "No active scenario"}

        t = time.time()
        merged = dict(scen)
        merged["started_at_s"] = t
        state.run_session.scenario = merged
        await asyncio.to_thread(
            save_state,
            state.world,
            state.chat_history,
            engine.district_index,
            engine.districts,
            engine.generation,
            scenario=merged,
            flush_mode="incremental",
        )
    await broadcast(
        {
            "type": "scenario",
            "city": merged["location"],
            "period": merged["period"],
            "year": merged.get("focus_year"),
            "description": merged.get("description", ""),
            "started_at_s": t,
        }
    )
    logging.info("Timeline reset: new started_at_s=%s (world and caches unchanged)", t)
    return {"ok": True, "started_at_s": t}


state.reset_callback = handle_reset
state.start_callback = handle_start
state.resume_callback = handle_resume
state.pause_callback = handle_pause
state.llm_settings_callback = handle_llm_settings_save
state.restart_server_callback = handle_restart_server
state.reset_timeline_callback = handle_reset_timeline
state.engine_is_running = lambda: engine.running
state.schedule_debounced_persist_after_chat = schedule_debounced_persist_after_chat


@asynccontextmanager
async def _app_lifespan(_app):
    print(BANNER)
    global _heartbeat_thread
    # Restored world/scenario may exist on disk, but the build does not start until the user clicks
    # "Continue current session" in the UI (WebSocket `resume`) or starts a new city.
    _heartbeat_thread = start_heartbeat(_make_heartbeat_snapshot, HEARTBEAT_INTERVAL_S)
    yield
    stop_heartbeat(_heartbeat_thread)
    await engine.graceful_shutdown()
    logging.info("Build engine stopped (server shutdown or reload)")


app = build_app(state, lifespan=_app_lifespan)


if __name__ == "__main__":
    _reload = os.environ.get("ETERNAL_CITIES_RELOAD", "").strip().lower() in ("1", "true", "yes")
    if _reload:
        logging.info(
            "ETERNAL_CITIES_RELOAD: watching files — code changes restart the process. "
            "UI RESET does not reload code."
        )
    _uvicorn_kwargs = dict(
        host="0.0.0.0",
        port=8000,
        log_level="info",
        reload=_reload,
        reload_includes=["*.py", "reload_trigger.txt"] if _reload else None,
    )
    # Debounce bursts of watcher events so the dev server does not restart repeatedly on startup.
    if _reload:
        _uvicorn_kwargs["reload_delay"] = 1.0
        _uvicorn_kwargs["reload_excludes"] = [
            "**/__pycache__/**",
            "**/.git/**",
            "**/venv/**",
            "**/.venv/**",
        ]
    uvicorn.run("main:app", **_uvicorn_kwargs)
