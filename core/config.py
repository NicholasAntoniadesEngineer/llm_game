"""Eternal Cities — Configuration."""

import json
import os
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Grid settings (each tile ≈ 10 m in agent prompts — total city footprint scales with size)
# 4× world size (linear dimensions): 80×80 → 320×320 tiles
GRID_WIDTH = 320
GRID_HEIGHT = 320

# Allow environment variable overrides for grid dimensions and district cap
GRID_WIDTH = int(os.environ.get("ETERNAL_GRID_WIDTH", str(GRID_WIDTH)))
GRID_HEIGHT = int(os.environ.get("ETERNAL_GRID_HEIGHT", str(GRID_HEIGHT)))
MAX_DISTRICTS = int(os.environ.get("ETERNAL_MAX_DISTRICTS", "12"))

# Legacy names — prefer per-agent settings in llm_agents.py at repo root.
CLAUDE_MODEL = "haiku"
CLAUDE_MODEL_FAST = "haiku"
STEP_DELAY = float(os.environ.get("ETERNAL_STEP_DELAY", "0.3"))

# Defaults for claude_cli and openai_compatible when llm_agents.py does not set per-agent overrides.
CLAUDE_CLI_BINARY = "claude"
OPENAI_COMPATIBLE_BASE_URL = ""
OPENAI_COMPATIBLE_API_KEY = ""
# Optional global override for openai_compatible model (prefer setting model in llm_agents.py per agent).
OPENAI_COMPATIBLE_MODEL = ""

# Max concurrent Urbanista CLI calls (design pass; placement streams as each completes).
# Higher = faster builds but more parallel API calls. 5 is safe for Claude Max plans.
URBANISTA_MAX_CONCURRENT = int(os.environ.get("ETERNAL_URBANISTA_MAX_CONCURRENT", "5"))

# Max concurrent surveyor CLI calls across parallel district surveys.
SURVEY_MAX_CONCURRENT = int(os.environ.get("ETERNAL_SURVEY_MAX_CONCURRENT", "3"))

# Surveyor: when a district lists more than this many named buildings, run multiple
# smaller survey passes and merge (fewer tokens per call, clearer placement).
SURVEY_BUILDINGS_PER_CHUNK = int(os.environ.get("ETERNAL_SURVEY_CHUNK", "18"))

# Max buildings Cartographus should list per district. Lower = faster builds.
# The skeleton planner prompt references this. Set via ETERNAL_MAX_BUILDINGS_PER_DISTRICT env var.
MAX_BUILDINGS_PER_DISTRICT = int(os.environ.get("ETERNAL_MAX_BUILDINGS_PER_DISTRICT", "8"))

# Persist world to disk every N structures placed. With 7+ minute Urbanista calls,
# each structure is precious — save after every one to prevent data loss on crash.
SAVE_STATE_EVERY_N_STRUCTURES = int(os.environ.get("ETERNAL_SAVE_EVERY_N", "1"))

# Chunk size for sparse world persistence (tiles per chunk side)
CHUNK_SIZE = int(os.environ.get("ETERNAL_CHUNK_SIZE", "64"))

# Continuous generation: max expansion generations (0 = infinite)
MAX_GENERATIONS = int(os.environ.get("ETERNAL_MAX_GENERATIONS", "0"))

# Seconds to wait between expansion passes when no new districts found
EXPANSION_COOLDOWN = float(os.environ.get("ETERNAL_EXPANSION_COOLDOWN", "10"))

# Cap chat messages stored for replay (oldest dropped).
CHAT_HISTORY_MAX_MESSAGES = int(os.environ.get("ETERNAL_CHAT_MAX", "500"))

# Max chat messages sent to a client on WebSocket connect (most recent).
CHAT_REPLAY_MAX_MESSAGES = int(os.environ.get("ETERNAL_CHAT_REPLAY_MAX", "200"))

# Agent display info
AGENTS = {
    "cartographus": {"name": "Cartographer",  "purpose": "Surveyor & Mapmaker", "color": "#e67e22"},
    "urbanista":    {"name": "Architect",      "purpose": "Master Architect",    "color": "#4a9eff"},
}

# ═══════════════════════════════════════════════════
# CITIES — loaded from data/cities.json
# ═══════════════════════════════════════════════════

CITIES = json.loads((_DATA_DIR / "cities.json").read_text())

WINDOW = 50

def format_year(y):
    if y < 0:
        return f"{abs(y)} BC"
    return str(y)

def get_city(name):
    """Look up a city by name."""
    for c in CITIES:
        if c["name"].lower() == name.lower():
            return c
    return None

def create_scenario(city_name, year):
    """Create a SCENARIO dict from user-selected city and year."""
    import time
    city = get_city(city_name)
    if not city:
        city = CITIES[0]
    year = max(city["year_min"], min(year, city["year_max"]))
    return {
        "location": city["name"],
        "description": city["description"],
        "features": city["features"],
        "grid_note": city["grid_note"],
        "period": f"around {format_year(year)}",
        "focus_year": year,
        "started_at_s": time.time(),
        "year_start": year - WINDOW // 2,
        "year_end": year + WINDOW // 2,
        "ruler": "Research who ruled and what the city looked like at this exact time",
        "climate": city.get("climate"),
    }

# Default scenario (set by user selection via /api/start)
SCENARIO = None
