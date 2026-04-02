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
import os
import time
import logging
from contextlib import asynccontextmanager
import uvicorn
from pathlib import Path

import config
import server.app as server_module
from server.app import build_app, world, bus, broadcast, chat_history
import llm_agents
from orchestration.engine import BuildEngine
from persistence import (
    DISTRICTS_CACHE,
    SAVE_FILE,
    SURVEYS_CACHE,
    load_llm_settings,
    load_state,
    merge_llm_overrides_from_save,
    save_llm_settings,
    save_state,
)
from config import GRID_WIDTH, GRID_HEIGHT, create_scenario

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

from run_log import init_run_log
init_run_log()

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

engine = BuildEngine(world, bus, broadcast, chat_history)

# Load saved state if available (tiles, chat, districts, scenario — survives server restarts)
saved = load_state(world)
if saved:
    loaded_chat, district_index, districts = saved
    chat_history.extend(loaded_chat)
    engine.district_index = district_index
    engine.districts = districts
    logging.info(
        "Restored from disk: district #%s, %s districts, %s chat messages — world + scenario kept for restart",
        district_index,
        len(districts),
        len(loaded_chat),
    )
else:
    logging.info("Starting fresh — awaiting user selection")


_engine_action_lock = asyncio.Lock()


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

        subprocess.run(["pkill", "-f", CLAUDE_AGENT_PKILL_PATTERN], capture_output=True)

    await engine.abort_pipeline_tasks()
    engine.reset_pipeline_for_new_run()
    server_module.agent_status_by_agent.clear()
    await engine.broadcast_all_agents_idle()

    # Create and set the scenario
    config.SCENARIO = create_scenario(city_name, year)
    logging.info(f"Starting: {config.SCENARIO['location']}, {config.SCENARIO['period']}")

    # Reset world state for fresh build
    from world.state import WorldState
    new_world = WorldState(GRID_WIDTH, GRID_HEIGHT)
    world.grid = new_world.grid
    world.turn = 0
    world.current_period = config.SCENARIO["period"]
    world.current_year = config.SCENARIO["focus_year"]
    world.build_log = []

    chat_history.clear()
    engine.districts = []
    engine.district_index = 0

    # Delete old caches
    if SAVE_FILE.exists():
        SAVE_FILE.unlink()
    if DISTRICTS_CACHE.exists():
        DISTRICTS_CACHE.unlink()
    if SURVEYS_CACHE.exists():
        SURVEYS_CACHE.unlink()

    # Broadcast scenario to all clients
    await broadcast(world.to_dict())
    await broadcast({
        "type": "scenario",
        "city": config.SCENARIO["location"],
        "period": config.SCENARIO["period"],
        "year": config.SCENARIO.get("focus_year"),
        "description": config.SCENARIO.get("description", ""),
        "started_at_s": config.SCENARIO.get("started_at_s"),
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
    subprocess.run(["pkill", "-f", CLAUDE_AGENT_PKILL_PATTERN], capture_output=True)

    from world.state import WorldState
    new_world = WorldState(GRID_WIDTH, GRID_HEIGHT)
    world.grid = new_world.grid
    world.turn = 0
    world.current_period = ""
    world.current_year = -44
    world.build_log = []

    chat_history.clear()
    engine.districts = []
    engine.district_index = 0
    server_module.agent_status_by_agent.clear()
    await engine.broadcast_all_agents_idle()

    if SAVE_FILE.exists():
        SAVE_FILE.unlink()
    if DISTRICTS_CACHE.exists():
        DISTRICTS_CACHE.unlink()
    if SURVEYS_CACHE.exists():
        SURVEYS_CACHE.unlink()

    # Back to selection screen
    config.SCENARIO = None
    await broadcast(world.to_dict())


async def handle_pause():
    """User-initiated pause: stop the engine, kill all agent subprocesses, save state."""
    async with _engine_action_lock:
        await _handle_pause_inner()


async def _handle_pause_inner():
    if not config.SCENARIO:
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
    subprocess.run(["pkill", "-f", CLAUDE_AGENT_PKILL_PATTERN], capture_output=True)
    await engine.broadcast_all_agents_idle()
    await asyncio.to_thread(
        save_state,
        world,
        chat_history,
        engine.district_index,
        engine.districts,
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
        if not config.SCENARIO:
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
    _sp.run(["pkill", "-f", CLAUDE_AGENT_PKILL_PATTERN], capture_output=True)
    if getattr(config, "SCENARIO", None):
        await asyncio.to_thread(
            save_state,
            world,
            chat_history,
            engine.district_index,
            engine.districts,
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
        scen = getattr(config, "SCENARIO", None)
        if not scen or not isinstance(scen, dict):
            return {"ok": False, "error": "No active scenario"}

        t = time.time()
        merged = dict(scen)
        merged["started_at_s"] = t
        config.SCENARIO = merged
        await asyncio.to_thread(
            save_state,
            world,
            chat_history,
            engine.district_index,
            engine.districts,
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


server_module.reset_callback = handle_reset
server_module.start_callback = handle_start
server_module.resume_callback = handle_resume
server_module.pause_callback = handle_pause
server_module.llm_settings_callback = handle_llm_settings_save
server_module.restart_server_callback = handle_restart_server
server_module.reset_timeline_callback = handle_reset_timeline
server_module.engine_is_running = lambda: engine.running


@asynccontextmanager
async def _app_lifespan(_app):
    print(BANNER)
    # Restored world/scenario may exist on disk, but the build does not start until the user clicks
    # "Continue current session" in the UI (WebSocket `resume`) or starts a new city.
    yield
    await engine.graceful_shutdown()
    logging.info("Build engine stopped (server shutdown or reload)")


app = build_app(lifespan=_app_lifespan)
server_module.app = app


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
