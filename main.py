"""Eternal Cities — Entry point.

In-app **RESET** (web UI): full wipe — world, save, district + survey caches (Cartographus planning).

**Reload code** (browser button): hard refresh; does not restart Python or delete saves.

**Restart server** (POST /api/restart-server): saves state then touches `reload_trigger.txt` so uvicorn
`--reload` restarts the process — keeps `roma_save.json`, `roma_districts_cache.json`, and
`roma_surveys_cache.json`. Requires:

    ETERNAL_CITIES_RELOAD=1 python main.py

**Listen port (``python main.py`` only):** ``http_server_listen_port`` comes from ``data/system_config.csv``.
If that port is already taken, set an override for this process only (does not change the CSV):

    ETERNAL_CITIES_HTTP_PORT_OVERRIDE=8017 python main.py

Static assets are cache-busted on each server restart (`?v=…`).
"""

import asyncio
import errno
import functools
import os
import socket
import sys
import time
import logging
from typing import Any
from contextlib import asynccontextmanager
import uvicorn
from pathlib import Path

from server.state import AppState
from server.broadcast import broadcast as _broadcast_impl
from server.app import build_app
from agents import llm_routing as llm_agents
from orchestration.engine import BuildEngine
from core.errors import SaveIndexError
from core.persistence import (
    clear_saves,
    load_blueprint,
    load_llm_settings,
    load_state,
    merge_llm_overrides_from_save,
    save_llm_settings,
    save_state,
    validate_blueprint_tile_invariants,
)
from core.application_services import configure_application_services, set_broadcast_async
from core.config import load_config
from core.bootstrap import apply_llm_routing_from_config
from core.token_usage import TokenUsageStore

from core.run_log import init_run_log
from core.heartbeat import start_heartbeat, stop_heartbeat

# Load the single source of truth configuration early (strict CSV validation, fails hard if invalid)
system_configuration = load_config()
application_services = configure_application_services(
    system_configuration=system_configuration,
    token_usage_store=TokenUsageStore(),
)
apply_llm_routing_from_config(system_configuration, application_services)
load_llm_settings(
    system_configuration=system_configuration,
    application_services=application_services,
)

init_run_log(
    run_log_buffer_max_lines=system_configuration.run_log_buffer_max_lines,
    log_level_string=system_configuration.ui.log_level_string,
)

logging.basicConfig(
    level=getattr(logging, system_configuration.ui.log_level_string.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
    force=True,
)


def _assert_runtime_dependencies_present() -> None:
    """Fail fast with install instructions if the venv was not populated from requirements.txt."""
    try:
        import jsonschema  # noqa: F401 — required by core.society_validator (building prompts)
    except ModuleNotFoundError as import_err:
        raise SystemExit(
            "Missing required package 'jsonschema' (used by society / building prompts).\n"
            "Use a project virtualenv (macOS system Python blocks PEP 668 `pip install`):\n"
            "  python3.11 -m venv .venv   # or python3.12 — avoid 3.14 with pinned pydantic in requirements.txt\n"
            "  .venv/bin/pip install -r requirements.txt\n"
            "  .venv/bin/python main.py\n"
            "Or: source .venv/bin/activate && pip install -r requirements.txt && python main.py\n"
            f"Original error: {import_err}"
        ) from import_err


_assert_runtime_dependencies_present()

logging.info(
    "Eternal Cities startup | log_level=%s effective=%s argv=%s",
    system_configuration.ui.log_level_string,
    logging.getLevelName(getattr(logging, system_configuration.ui.log_level_string.upper(), logging.INFO)),
    " ".join(sys.argv[:6]),
)

# Touched by POST /api/restart-server when ETERNAL_CITIES_RELOAD=1 so uvicorn --reload picks up a restart request.
RELOAD_SENTINEL = Path("reload_trigger.txt")  # relative path only

# pkill -f regex: only child processes from agents/base.py (claude --print --system-prompt ...).
# Broader patterns like "claude.*--print" can match unrelated Claude CLI usage.
CLAUDE_AGENT_PKILL_PATTERN = r"claude.*--print.*--system-prompt"

# On startup (including uvicorn auto-reload), kill orphaned Claude CLI processes
# from the previous server instance to prevent token waste.
import subprocess as startup_subprocess
startup_subprocess.run(["pkill", "-f", CLAUDE_AGENT_PKILL_PATTERN], capture_output=True)
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

# Shared application state with injected config and unified application context
state = AppState(
    system_configuration=system_configuration,
    application_services=application_services,
)

# Bind broadcast to the shared state so callers use broadcast(message) without needing to pass state.
broadcast = functools.partial(_broadcast_impl, state)

set_broadcast_async(application_services, broadcast)

engine = BuildEngine(
    state.world,
    state.bus,
    broadcast,
    state.chat_history,
    run_session=state.run_session,
    system_configuration=system_configuration,
    application_services=application_services,
)


def _terrain_data_for_ws_replay() -> dict | None:
    """Rebuild terrain_data for WebSocket refresh (rivers/hills use blueprint, not tile list)."""
    if not isinstance(state.run_session.scenario, dict):
        return None
    bp_obj = getattr(engine, "blueprint", None)
    if bp_obj is not None:
        try:
            hills = bp_obj.hills or []
            water = bp_obj.water or []
            roads = bp_obj.roads or []
        except Exception:
            return None
        if not hills and not water and not roads:
            return None
        return {
            "type": "terrain_data",
            "hills": hills,
            "water": water,
            "roads": roads,
            "max_gradient": system_configuration.terrain.maximum_gradient_value,
            "gradient_iterations": system_configuration.terrain.gradient_iterations_count,
        }
    raw_bp = load_blueprint(system_configuration=system_configuration)
    if not isinstance(raw_bp, dict):
        return None
    hills = raw_bp.get("hills") or []
    water = raw_bp.get("water") or []
    roads = raw_bp.get("roads") or []
    if not hills and not water and not roads:
        return None
    return {
        "type": "terrain_data",
        "hills": hills,
        "water": water,
        "roads": roads,
        "max_gradient": system_configuration.terrain.maximum_gradient_value,
        "gradient_iterations": system_configuration.terrain.gradient_iterations_count,
    }


def _agent_activity_one_liner() -> str:
    """First non-idle agent status (detail) for live build_status ticks."""
    try:
        for _agent_key, msg in (state.agent_status_by_agent or {}).items():
            if not isinstance(msg, dict):
                continue
            st = str(msg.get("status", "")).strip().lower()
            if st in ("", "idle"):
                continue
            agent = str(msg.get("agent", _agent_key))
            det = msg.get("detail")
            if det and str(det).strip():
                return f"{agent}: {str(det).strip()[:80]}"
            return f"{agent}: {st}"
    except Exception:
        logging.getLogger("eternal.main").exception("agent activity one-liner failed")
    return ""


_RUN_PHASE_HEADER_LABELS: dict[str, str] = {
    "idle": "not running the build loop",
    "discovering": "discovering / planning the map",
    "building": "building districts and structures",
    "paused_api": "paused — API, model, or network issue",
    "shutting_down": "shutting down the build loop",
}


def _human_activity_line_for_header(
    *,
    running: bool,
    snap: dict[str, Any],
    agent_activity: str,
    phase_age_s: int | None,
    generation: int,
    build_wave_phase: str,
) -> str:
    """One explicit sentence for the UI: what the server is doing right now."""
    if not running:
        return "Idle — no live build (Continue session or Start a city)."
    parts: list[str] = []
    aa = (agent_activity or "").strip()
    if aa:
        parts.append(aa)
    run_phase_raw = str(snap.get("run_phase") or "").strip().lower()
    if run_phase_raw:
        parts.append(_RUN_PHASE_HEADER_LABELS.get(run_phase_raw, f"orchestrator: {run_phase_raw}"))
    phase = snap.get("phase")
    phase_s = str(phase).strip() if phase else ""
    if phase_s:
        parts.append(f"trace step: {phase_s}")
    prep_total = snap.get("prep_total")
    prep_index = snap.get("prep_index")
    prep_structure = snap.get("prep_structure")
    prep_btype = snap.get("prep_building_type")
    if isinstance(prep_total, int) and prep_total > 0 and prep_structure:
        pi = int(prep_index) + 1 if isinstance(prep_index, int) else 0
        bt = str(prep_btype).strip() if prep_btype else "?"
        parts.append(f"Urbanista prompt prep {pi}/{prep_total}: {str(prep_structure)[:44]} ({bt})")
    district = snap.get("district")
    if district and str(district).strip():
        parts.append(f"district {str(district).strip()}")
    wave = snap.get("wave")
    if wave and str(wave).strip():
        parts.append(str(wave).strip()[:56])
    parts.append(f"gen {int(generation)} · save-wave {str(build_wave_phase)}")
    if phase_age_s is not None and phase_age_s >= 15:
        if phase_s == "district_prep_prompts":
            parts.append(
                f"prep step ~{int(phase_age_s)}s on this building (local CPU / world context, not the model yet)"
            )
        else:
            parts.append(f"same trace step ~{int(phase_age_s)}s (usually waiting on the model API)")
    return " — ".join(parts)[:240]


def _build_status_for_ws_replay() -> dict | None:
    """Short engine snapshot so the header can show what the server is doing after a reconnect."""
    scen = state.run_session.scenario
    if not isinstance(scen, dict):
        return None
    snap = getattr(engine, "_trace_snapshot", None)
    if not isinstance(snap, dict):
        snap = {}
    mono = snap.get("monotonic_s")
    phase_age_s: int | None = None
    if isinstance(mono, (int, float)):
        phase_age_s = max(0, int(time.monotonic() - float(mono)))

    agent_activity = _agent_activity_one_liner()
    generation_i = int(getattr(engine, "generation", 0))
    build_wave_phase_s = str(getattr(engine, "build_wave_phase", "landmark"))

    common = {
        "type": "build_status",
        "generation": generation_i,
        "district_index": int(getattr(engine, "district_index", 0)),
        "district_build_cursor": int(getattr(engine, "district_build_cursor", 0)),
        "build_wave_phase": build_wave_phase_s,
        "districts_total": len(getattr(engine, "districts", []) or []),
        "district": snap.get("district"),
        "wave": snap.get("wave"),
        "agent_activity": agent_activity,
        "phase_age_s": phase_age_s,
    }

    if not bool(getattr(engine, "running", False)):
        idle_line = "Idle — no live build (Continue session or Start a city)."
        return {
            **common,
            "session_mode": "idle",
            "phase": "waiting_for_resume_or_paused",
            "run_phase": "idle",
            "hint": "No live build — open Continue current session or Start a city.",
            "activity_line": idle_line,
        }

    activity_line = _human_activity_line_for_header(
        running=True,
        snap=snap,
        agent_activity=agent_activity,
        phase_age_s=phase_age_s,
        generation=generation_i,
        build_wave_phase=build_wave_phase_s,
    )
    return {
        **common,
        "session_mode": "building",
        "phase": snap.get("phase"),
        "run_phase": snap.get("run_phase"),
        "activity_line": activity_line,
    }


state.terrain_data_for_replay = _terrain_data_for_ws_replay
state.build_status_for_replay = _build_status_for_ws_replay

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
    saved = load_state(state.world, system_configuration=system_configuration)
except SaveIndexError:
    logging.exception("Save index is corrupt or unreadable — starting without disk restore")
    saved = None
if saved:
    (
        loaded_chat,
        district_index,
        districts,
        loaded_generation,
        loaded_scenario,
        loaded_build_wave_phase,
        loaded_district_build_cursor,
    ) = saved
    state.chat_history.extend(loaded_chat)
    engine.district_index = district_index
    engine.districts = districts
    engine.generation = loaded_generation
    engine.build_wave_phase = loaded_build_wave_phase
    engine.district_build_cursor = loaded_district_build_cursor
    state.run_session.scenario = loaded_scenario
    for msg in validate_blueprint_tile_invariants(
        state.world, load_blueprint(system_configuration=system_configuration)
    ):
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
    await asyncio.sleep(system_configuration.timing.chat_persist_debounce_seconds)
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
            system_configuration=system_configuration,
            flush_mode="incremental",
            build_wave_phase=engine.build_wave_phase,
            district_build_cursor=engine.district_build_cursor,
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
    new_scenario = system_configuration.create_scenario(city_name, year)
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
    clear_saves(system_configuration=system_configuration)

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
    state.world.current_year = system_configuration.world_reset_default_year_int

    state.chat_history.clear()
    engine.districts = []
    engine.district_index = 0
    state.agent_status_by_agent.clear()
    await engine.broadcast_all_agents_idle()

    clear_saves(system_configuration=system_configuration)

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
        system_configuration=system_configuration,
        flush_mode="full",
        build_wave_phase=engine.build_wave_phase,
        district_build_cursor=engine.district_build_cursor,
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
            snap = getattr(engine, "_trace_snapshot", None)
            if not isinstance(snap, dict):
                snap = {}
            logging.info(
                "Resume skipped — build loop already running (trace phase=%s district=%s)",
                snap.get("phase"),
                snap.get("district"),
            )
            return
        await engine.schedule_run()


async def handle_llm_settings_save(overrides: dict):
    """Persist per-agent LLM routing from UI; merges empty API key fields with existing."""
    if not isinstance(overrides, dict):
        return
    current = llm_agents.get_runtime_overrides(application_services=state.application_services)
    merged = merge_llm_overrides_from_save(
        current,
        overrides,
        application_services=state.application_services,
    )
    llm_agents.set_runtime_overrides(merged, application_services=state.application_services)
    await asyncio.to_thread(
        save_llm_settings,
        merged,
        system_configuration=system_configuration,
    )


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
            system_configuration=system_configuration,
            flush_mode="full",
            build_wave_phase=engine.build_wave_phase,
            district_build_cursor=engine.district_build_cursor,
        )

    async def _touch_reload_sentinel_after_response() -> None:
        await asyncio.sleep(system_configuration.server_reload_sentinel_pre_touch_sleep_seconds)
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
            system_configuration=system_configuration,
            flush_mode="incremental",
            build_wave_phase=engine.build_wave_phase,
            district_build_cursor=engine.district_build_cursor,
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
    _heartbeat_thread = start_heartbeat(
        _make_heartbeat_snapshot,
        system_configuration.timing.heartbeat_interval_seconds,
    )

    hb = float(system_configuration.timing.heartbeat_interval_seconds)
    tick_building = max(4.0, min(hb, 15.0))
    tick_idle_scenario = max(12.0, min(hb * 1.5, 45.0))

    async def _broadcast_build_status_tick() -> None:
        await asyncio.sleep(2.0)
        while True:
            sleep_next = 10.0
            try:
                if not isinstance(state.run_session.scenario, dict):
                    sleep_next = 15.0
                else:
                    fn = getattr(state, "build_status_for_replay", None)
                    if callable(fn):
                        msg = fn()
                        if isinstance(msg, dict) and msg.get("type") == "build_status":
                            await broadcast(msg)
                    is_run = (
                        state.engine_is_running is not None and state.engine_is_running()
                    )
                    sleep_next = tick_building if is_run else tick_idle_scenario
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger("eternal.main").exception("build_status tick broadcast failed")
            await asyncio.sleep(sleep_next)

    _build_status_tick_task = asyncio.create_task(
        _broadcast_build_status_tick(),
        name="build_status_ws_tick",
    )
    try:
        yield
    finally:
        _build_status_tick_task.cancel()
        try:
            await _build_status_tick_task
        except asyncio.CancelledError:
            pass
        stop_heartbeat(_heartbeat_thread)
        await engine.graceful_shutdown()
        logging.info("Build engine stopped (server shutdown or reload)")


app = build_app(state, system_configuration=system_configuration, lifespan=_app_lifespan)


def _bind_error_is_address_already_in_use(bind_error: OSError) -> bool:
    if bind_error.errno == errno.EADDRINUSE:
        return True
    if getattr(bind_error, "winerror", None) == 10048:
        return True
    return False


def _log_tcp_listen_port_busy_and_exit(
    *,
    listen_host_string: str,
    listen_port_int: int,
    bind_error: OSError,
) -> None:
    root_log = logging.getLogger("eternal.main")
    root_log.error(
        "HTTP listen port unavailable host=%r port=%s (%s). "
        "Stop the other process or change http_server_listen_port in data/system_config.csv. "
        "Hint (macOS/Linux): lsof -nP -iTCP:%s -sTCP:LISTEN",
        listen_host_string,
        listen_port_int,
        bind_error,
        listen_port_int,
    )
    try:
        snapshot = startup_subprocess.run(
            ["lsof", "-nP", f"-iTCP:{listen_port_int}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        if snapshot.stdout.strip():
            root_log.error("Port listener snapshot:\n%s", snapshot.stdout.strip())
    except Exception as snapshot_err:
        root_log.debug("listen_port_snapshot_failed: %s", snapshot_err, exc_info=True)
    raise SystemExit(3) from bind_error


def _assert_tcp_listen_port_available_before_uvicorn(
    *,
    listen_host_string: str,
    listen_port_int: int,
) -> None:
    """Probe-bind so we fail before uvicorn lifespan (avoids banner/heartbeat then bind error)."""
    if listen_port_int == 0:
        return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
            probe_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe_socket.bind((listen_host_string, int(listen_port_int)))
    except OSError as bind_err:
        if _bind_error_is_address_already_in_use(bind_err):
            _log_tcp_listen_port_busy_and_exit(
                listen_host_string=listen_host_string,
                listen_port_int=int(listen_port_int),
                bind_error=bind_err,
            )
        raise


if __name__ == "__main__":
    _reload = os.environ.get("ETERNAL_CITIES_RELOAD", "").strip().lower() in ("1", "true", "yes")
    if _reload:
        logging.info(
            "ETERNAL_CITIES_RELOAD: watching files — code changes restart the process. "
            "UI RESET does not reload code."
        )
    _uvicorn_kwargs = dict(
        host=system_configuration.http_server_listen_host_string,
        port=system_configuration.http_server_listen_port_int,
        log_level=system_configuration.uvicorn_log_level_string,
        reload=_reload,
        reload_includes=["*.py", "reload_trigger.txt"] if _reload else None,
    )
    _http_port_override_raw = os.environ.get("ETERNAL_CITIES_HTTP_PORT_OVERRIDE", "").strip()
    if _http_port_override_raw:
        _main_log = logging.getLogger("eternal.main")
        try:
            _parsed_port = int(_http_port_override_raw)
        except ValueError as port_override_err:
            _main_log.error(
                "ETERNAL_CITIES_HTTP_PORT_OVERRIDE invalid %r: %s",
                _http_port_override_raw,
                port_override_err,
            )
            raise SystemExit(2) from port_override_err
        if _parsed_port < 1 or _parsed_port > 65535:
            _main_log.error(
                "ETERNAL_CITIES_HTTP_PORT_OVERRIDE out of range 1..65535: %s",
                _parsed_port,
            )
            raise SystemExit(2)
        _uvicorn_kwargs["port"] = _parsed_port
        _main_log.info(
            "Using ETERNAL_CITIES_HTTP_PORT_OVERRIDE=%s (CSV http_server_listen_port unchanged)",
            _parsed_port,
        )
    # Debounce bursts of watcher events so the dev server does not restart repeatedly on startup.
    if _reload:
        _uvicorn_kwargs["reload_delay"] = system_configuration.uvicorn_reload_delay_seconds
        _uvicorn_kwargs["reload_excludes"] = [
            "**/__pycache__/**",
            "**/.git/**",
            "**/venv/**",
            "**/.venv/**",
        ]
    _assert_tcp_listen_port_available_before_uvicorn(
        listen_host_string=str(_uvicorn_kwargs["host"]),
        listen_port_int=int(_uvicorn_kwargs["port"]),
    )
    if _reload:
        uvicorn.run("main:app", **_uvicorn_kwargs)
    else:
        uvicorn.run(app, **_uvicorn_kwargs)
