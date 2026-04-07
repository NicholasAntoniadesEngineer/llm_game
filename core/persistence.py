"""Chunked persistence — saves world state in per-chunk tile files + metadata index."""

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from world.state import WorldState
from core import config
from agents import llm_routing as llm_agents

logger = logging.getLogger("eternal.persistence")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAVES_DIR = _PROJECT_ROOT / "data" / "saves"
CHUNKS_DIR = SAVES_DIR / "chunks"
INDEX_FILE = SAVES_DIR / "index.json"
DISTRICTS_CACHE = SAVES_DIR / "districts_cache.json"
SURVEYS_CACHE = SAVES_DIR / "surveys_cache.json"
BLUEPRINT_FILE = SAVES_DIR / "blueprint.json"
LLM_SETTINGS_FILE = _PROJECT_ROOT / "data" / "llm_settings.json"


def _ensure_dirs():
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, text: str) -> None:
    """Write text to path atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _chunk_key(x: int, y: int) -> tuple[int, int]:
    """Return chunk coordinate for a tile at (x, y)."""
    return (x // config.CHUNK_SIZE, y // config.CHUNK_SIZE)


def _chunk_filename(cx: int, cy: int) -> str:
    return f"chunk_{cx}_{cy}.json"


# ─── Save ───────────────────────────────────────────────────────────

def save_state(world: WorldState, chat_history: list[dict],
               district_index: int, districts: list[dict] = None,
               generation: int = 0):
    """Save world state: index.json + dirty chunk files."""
    _ensure_dirs()

    scenario: dict[str, Any] | None = getattr(config, "SCENARIO", None)
    run_started_at_s: float | None = None
    if scenario and isinstance(scenario, dict):
        run_started_at_s = scenario.get("started_at_s")
        if run_started_at_s is not None:
            run_started_at_s = float(run_started_at_s)
        if run_started_at_s is None:
            run_started_at_s = time.time()
            config.SCENARIO = {**scenario, "started_at_s": run_started_at_s}
            scenario = config.SCENARIO

    # Write dirty chunks
    chunks_written = 0
    chunk_tiles: dict[tuple[int, int], list[dict]] = {}
    for (x, y), tile in world.tiles.items():
        if tile.terrain == "empty":
            continue
        ck = _chunk_key(x, y)
        if ck not in chunk_tiles:
            chunk_tiles[ck] = []
        chunk_tiles[ck].append(tile.to_dict())

    # Write ALL chunks that have tiles (not just dirty — clean save)
    for ck, tiles in chunk_tiles.items():
        path = CHUNKS_DIR / _chunk_filename(ck[0], ck[1])
        _atomic_write(path, json.dumps(tiles))
        chunks_written += 1

    world._dirty_chunks.clear()

    # Write index
    index = {
        "district_index": district_index,
        "districts": districts or [],
        "generation": generation,
        "turn": world.turn,
        "current_period": world.current_period,
        "current_year": world.current_year,
        "min_x": world.min_x,
        "max_x": world.max_x,
        "min_y": world.min_y,
        "max_y": world.max_y,
        "chat_history": chat_history,
        "run_started_at_s": run_started_at_s,
        "scenario": scenario,
        "chunk_size": config.CHUNK_SIZE,
    }
    _atomic_write(INDEX_FILE, json.dumps(index, indent=2))

    total_tiles = sum(len(t) for t in chunk_tiles.values())
    logger.info(f"Saved: {total_tiles} tiles in {chunks_written} chunks, "
                f"district #{district_index}, generation {generation}")


# ─── Load ───────────────────────────────────────────────────────────

def load_state(world: WorldState) -> tuple[list[dict], int, list[dict]] | None:
    """Load from chunked format. Returns (chat_history, district_index, districts) or None."""
    if not INDEX_FILE.exists():
        return None

    index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))

    world.turn = index["turn"]
    world.current_period = index["current_period"]
    world.current_year = index["current_year"]

    # Load all chunk files
    tile_count = 0
    if CHUNKS_DIR.exists():
        for chunk_file in CHUNKS_DIR.glob("chunk_*.json"):
            tiles = json.loads(chunk_file.read_text(encoding="utf-8"))
            for tile_data in tiles:
                x, y = tile_data["x"], tile_data["y"]
                world.place_tile(x, y, tile_data)
                tile_count += 1

    world.build_log.clear()

    chat_history = index["chat_history"]
    district_index = index["district_index"]
    districts = index["districts"]

    scen = index.get("scenario")
    if isinstance(scen, dict) and scen:
        run_started_at_s = index.get("run_started_at_s")
        if isinstance(run_started_at_s, (int, float)):
            run_started_at_s = float(run_started_at_s)
        else:
            run_started_at_s = None
        if scen.get("started_at_s") is None:
            if run_started_at_s is not None:
                scen = {**scen, "started_at_s": run_started_at_s}
            else:
                scen = {**scen, "started_at_s": time.time()}
        config.SCENARIO = scen
        if not (world.current_period or "").strip() and scen.get("period"):
            world.current_period = str(scen["period"])
        fy = scen.get("focus_year")
        if fy is not None:
            world.current_year = int(fy)

    logger.info(f"Loaded: {tile_count} tiles, district #{district_index}, {len(districts)} districts")
    return chat_history, district_index, districts


def clear_saves():
    """Delete all save data (full reset)."""
    if SAVES_DIR.exists():
        shutil.rmtree(SAVES_DIR)
    logger.info("All save data cleared")


# ─── District + Survey Caches ───────────────────────────────────────

def save_districts_cache(districts: list[dict], map_description: str = ""):
    _ensure_dirs()
    data = {"districts": districts, "map_description": map_description}
    _atomic_write(DISTRICTS_CACHE, json.dumps(data, indent=2))
    logger.info(f"Cached {len(districts)} districts")


def load_districts_cache() -> tuple[list[dict], str] | None:
    if not DISTRICTS_CACHE.exists():
        return None
    data = json.loads(DISTRICTS_CACHE.read_text(encoding="utf-8"))
    districts = data["districts"]
    map_desc = data.get("map_description", "")
    for d in districts:
        if not isinstance(d, dict) or "name" not in d or "region" not in d:
            raise ValueError(f"Malformed district entry in cache: {d}")
    logger.info(f"Loaded {len(districts)} cached districts")
    return districts, map_desc


def save_surveys_cache(surveys: dict):
    _ensure_dirs()
    _atomic_write(SURVEYS_CACHE, json.dumps(surveys, indent=2))


def load_surveys_cache() -> dict:
    if not SURVEYS_CACHE.exists():
        return {}
    data = json.loads(SURVEYS_CACHE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Surveys cache is not a dict")
    for k, v in data.items():
        if not isinstance(v, list):
            raise ValueError(f"Survey cache entry {k} is not a list")
    return data


# ─── Blueprint ─────────────────────────────────────────────────────

def save_blueprint(blueprint_dict: dict):
    """Save city blueprint data to disk."""
    _ensure_dirs()
    _atomic_write(BLUEPRINT_FILE, json.dumps(blueprint_dict, indent=2))
    logger.info("Blueprint saved")


def load_blueprint() -> dict | None:
    """Load city blueprint data from disk. Returns dict or None."""
    if not BLUEPRINT_FILE.exists():
        return None
    try:
        data = json.loads(BLUEPRINT_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            logger.info("Blueprint loaded from disk")
            return data
    except Exception as exc:
        logger.warning("Failed to load blueprint: %s", exc)
    return None


# ─── LLM Settings ──────────────────────────────────────────────────

def save_llm_settings(overrides: dict):
    _atomic_write(LLM_SETTINGS_FILE, json.dumps(overrides, indent=2))


def load_llm_settings():
    if not LLM_SETTINGS_FILE.exists():
        return
    data = json.loads(LLM_SETTINGS_FILE.read_text(encoding="utf-8"))
    if isinstance(data, dict) and data:
        llm_agents.set_runtime_overrides(data)
        logger.info("Loaded LLM settings (%s agents)", len(data))


def merge_llm_overrides_from_save(
    current: dict[str, dict[str, Any]], incoming: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Apply UI save per agent; blank API key keeps previously saved key."""
    out: dict[str, dict[str, Any]] = {k: dict(v) for k, v in current.items()}
    for agent_key, patch in incoming.items():
        if agent_key not in llm_agents.AGENT_LLM or not isinstance(patch, dict):
            continue
        prev = out.get(agent_key, {})
        merged: dict[str, Any] = {}
        for k, v in patch.items():
            if v is None:
                continue
            if k == "openai_api_key" and isinstance(v, str) and not v.strip():
                continue
            merged[k] = v
        out[agent_key] = {**prev, **merged}
    return out
