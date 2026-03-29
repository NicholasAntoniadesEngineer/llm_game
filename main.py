"""Eternal Cities — Entry point."""

import asyncio
import logging
import uvicorn
from pathlib import Path

import config
import server.app as server_module
from server.app import app, world, bus, broadcast, chat_history
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
)
from config import GRID_WIDTH, GRID_HEIGHT, create_scenario

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

# pkill -f regex: only child processes from agents/base.py (claude --print --system-prompt ...).
# Broader patterns like "claude.*--print" can match unrelated Claude CLI usage.
CLAUDE_AGENT_PKILL_PATTERN = r"claude.*--print.*--system-prompt"

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

# Load saved state if available
saved = load_state(world)
if saved:
    loaded_chat, district_index, districts = saved
    chat_history.extend(loaded_chat)
    engine.district_index = district_index
    engine.districts = districts
    logging.info(f"Resumed: district #{district_index}, {len(districts)} districts, {len(loaded_chat)} messages")
else:
    logging.info("Starting fresh — awaiting user selection")


async def handle_start(city_name, year):
    """User selected a city and year — create scenario and start engine."""
    # Stop if already running
    if engine.running:
        engine.running = False
        await asyncio.sleep(0.5)
        import subprocess
        subprocess.run(["pkill", "-f", CLAUDE_AGENT_PKILL_PATTERN], capture_output=True)

    await engine.abort_pipeline_tasks()
    engine.reset_pipeline_for_new_run()

    # Create and set the scenario
    config.SCENARIO = create_scenario(city_name, year)
    logging.info(f"Starting: {config.SCENARIO['location']}, {config.SCENARIO['period']}")

    # Reset world state for fresh build
    from world.state import WorldState
    new_world = WorldState(GRID_WIDTH, GRID_HEIGHT)
    world.grid = new_world.grid
    world.turn = 0
    world.current_period = ""
    world.current_year = year
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
        "description": config.SCENARIO.get("description", ""),
    })

    # Start the engine
    asyncio.create_task(engine.run())


async def handle_reset():
    """Reset world, clear save, restart engine."""
    engine.running = False
    await asyncio.sleep(0.5)
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

    if SAVE_FILE.exists():
        SAVE_FILE.unlink()
    if DISTRICTS_CACHE.exists():
        DISTRICTS_CACHE.unlink()
    if SURVEYS_CACHE.exists():
        SURVEYS_CACHE.unlink()

    # Back to selection screen
    config.SCENARIO = None
    await broadcast(world.to_dict())


async def handle_resume():
    """Continue build after API rate limit / error / network pause."""
    if not config.SCENARIO:
        logging.warning("Resume ignored: no active scenario")
        return
    if engine.running:
        logging.warning("Resume ignored: engine already running")
        return
    asyncio.create_task(engine.run())


async def handle_llm_settings_save(overrides: dict):
    """Persist per-agent LLM routing from UI; merges empty API key fields with existing."""
    if not isinstance(overrides, dict):
        return
    current = llm_agents.get_runtime_overrides()
    merged = merge_llm_overrides_from_save(current, overrides)
    llm_agents.set_runtime_overrides(merged)
    await asyncio.to_thread(save_llm_settings, merged)


server_module.reset_callback = handle_reset
server_module.start_callback = handle_start
server_module.resume_callback = handle_resume
server_module.llm_settings_callback = handle_llm_settings_save
server_module.engine_is_running = lambda: engine.running


@app.on_event("startup")
async def startup():
    print(BANNER)
    # Don't auto-start — wait for user to select city and click Start


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
