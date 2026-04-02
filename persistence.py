"""Save and load world state + chat history to disk."""

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from world.state import WorldState

import config

import llm_agents

logger = logging.getLogger("roma.persistence")

SAVE_FILE = Path(__file__).parent / "roma_save.json"
SAVE_BACKUP = Path(__file__).parent / "roma_save.json.backup"
DISTRICTS_CACHE = Path(__file__).parent / "roma_districts_cache.json"
SURVEYS_CACHE = Path(__file__).parent / "roma_surveys_cache.json"
LLM_SETTINGS_FILE = Path(__file__).parent / "roma_llm_settings.json"


def _atomic_write(path: Path, text: str, backup: Path | None = None) -> None:
    """Write *text* to *path* atomically via a temp file + rename.

    If *backup* is given and *path* already exists, the current file is
    copied to *backup* before the rename so the previous version is preserved.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if backup is not None and path.exists():
        shutil.copy2(path, backup)
    os.replace(str(tmp), str(path))


def save_state(world: WorldState, chat_history: list[dict], district_index: int, districts: list[dict] = None):
    scenario: dict[str, Any] | None = getattr(config, "SCENARIO", None)
    run_started_at_s: float | None = None
    if scenario and isinstance(scenario, dict):
        if scenario.get("started_at_s") is not None:
            try:
                run_started_at_s = float(scenario["started_at_s"])
            except (TypeError, ValueError):
                run_started_at_s = None
        if run_started_at_s is None and SAVE_FILE.exists():
            try:
                old = json.loads(SAVE_FILE.read_text())
                prev = old.get("run_started_at_s")
                if isinstance(prev, (int, float)):
                    run_started_at_s = float(prev)
            except Exception:
                pass
        if run_started_at_s is None:
            run_started_at_s = time.time()
            merged = dict(scenario)
            merged["started_at_s"] = run_started_at_s
            config.SCENARIO = merged
            scenario = merged

    data = {
        "district_index": district_index,
        "districts": districts or [],
        "turn": world.turn,
        "current_period": world.current_period,
        "current_year": world.current_year,
        "chat_history": chat_history,
        "run_started_at_s": run_started_at_s,
        "scenario": scenario,
        "tiles": world.occupied_tile_dicts(),
    }

    _atomic_write(SAVE_FILE, json.dumps(data, indent=2), backup=SAVE_BACKUP)
    logger.info(f"Saved: {len(data['tiles'])} tiles, district #{district_index}, {len(districts or [])} districts")


def save_districts_cache(districts: list[dict], map_description: str = ""):
    data = {"districts": districts, "map_description": map_description}
    _atomic_write(DISTRICTS_CACHE, json.dumps(data, indent=2))
    logger.info(f"Cached {len(districts)} districts")


def load_districts_cache() -> tuple[list[dict], str] | None:
    if not DISTRICTS_CACHE.exists():
        return None
    try:
        data = json.loads(DISTRICTS_CACHE.read_text())
        districts = data.get("districts", [])
        map_desc = data.get("map_description", "")
        if districts:
            # Validate that every district has at least 'name' and 'region'.
            for d in districts:
                if not isinstance(d, dict) or "name" not in d or "region" not in d:
                    logger.warning("Malformed district entry in cache — discarding cache")
                    try:
                        DISTRICTS_CACHE.unlink()
                    except OSError:
                        pass
                    return None
            logger.info(f"Loaded {len(districts)} cached districts")
            return districts, map_desc
    except Exception as e:
        logger.error(f"Failed to load districts cache: {e}")
        try:
            DISTRICTS_CACHE.unlink()
        except OSError:
            pass
    return None


def save_surveys_cache(surveys: dict):
    _atomic_write(SURVEYS_CACHE, json.dumps(surveys, indent=2))


def load_surveys_cache() -> dict:
    if SURVEYS_CACHE.exists():
        try:
            data = json.loads(SURVEYS_CACHE.read_text())
            if not isinstance(data, dict):
                logger.warning("Surveys cache is not a dict — discarding")
                SURVEYS_CACHE.unlink(missing_ok=True)
                return {}
            # Each entry must be a list (master_plan)
            for k, v in data.items():
                if not isinstance(v, list):
                    logger.warning("Survey cache entry %s is not a list — discarding cache", k)
                    SURVEYS_CACHE.unlink(missing_ok=True)
                    return {}
            return data
        except Exception as e:
            logger.warning("Failed to load surveys cache: %s — discarding", e)
            SURVEYS_CACHE.unlink(missing_ok=True)
    return {}


def load_state(world: WorldState) -> tuple[list[dict], int, list[dict]] | None:
    """Returns (chat_history, district_index, districts) or None."""
    if not SAVE_FILE.exists() and not SAVE_BACKUP.exists():
        return None

    data = None

    # Try primary save file first.
    if SAVE_FILE.exists():
        try:
            data = json.loads(SAVE_FILE.read_text())
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Primary save file is corrupt: %s — trying backup", e)

    # Fall back to backup if primary failed or was missing.
    if data is None and SAVE_BACKUP.exists():
        try:
            data = json.loads(SAVE_BACKUP.read_text())
            logger.warning("Recovered state from backup %s", SAVE_BACKUP.name)
        except (json.JSONDecodeError, Exception) as e:
            logger.error("Backup save file is also corrupt: %s", e)
            return None

    if data is None:
        return None

    try:
        world.turn = data.get("turn", 0)
        world.current_period = data.get("current_period", "")
        world.current_year = data.get("current_year", -44)

        for tile_data in data.get("tiles", []):
            x, y = tile_data.get("x", 0), tile_data.get("y", 0)
            world.place_tile(x, y, tile_data)

        # Clear build_log entries generated by the loading loop above;
        # only NEW placements during this run should be logged.
        world.build_log.clear()

        chat_history = data.get("chat_history", [])
        district_index = data.get("district_index", 0)
        districts = data.get("districts", [])
        scen = data.get("scenario")
        run_started_at_s = data.get("run_started_at_s")
        if isinstance(run_started_at_s, (int, float)):
            run_started_at_s = float(run_started_at_s)
        else:
            run_started_at_s = None
        if isinstance(scen, dict) and scen:
            if scen.get("started_at_s") is None and run_started_at_s is not None:
                scen = {**scen, "started_at_s": run_started_at_s}
            elif scen.get("started_at_s") is None:
                scen = {**scen, "started_at_s": time.time()}
            config.SCENARIO = scen
            if not (world.current_period or "").strip() and scen.get("period"):
                world.current_period = str(scen["period"])
            fy = scen.get("focus_year")
            if fy is not None:
                try:
                    world.current_year = int(fy)
                except (TypeError, ValueError):
                    pass

        logger.info(f"Loaded: {len(data['tiles'])} tiles, district #{district_index}, {len(districts)} districts")
        return chat_history, district_index, districts

    except Exception as e:
        logger.error(f"Failed to load save: {e}")
        return None


def merge_llm_overrides_from_save(
    current: dict[str, dict[str, Any]], incoming: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Apply UI save per agent; blank API key field keeps the previously saved key (OpenAI only)."""
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
        prov = str(merged.get("provider", "")).lower()
        if prov in ("openai_compatible", "openai", "chatgpt"):
            if not merged.get("openai_api_key") and prev.get("openai_api_key"):
                merged["openai_api_key"] = prev["openai_api_key"]
        if prov in ("claude", "claude_cli"):
            merged.pop("openai_base_url", None)
            merged.pop("openai_api_key", None)
        out[agent_key] = merged
    return out


def load_llm_settings() -> dict[str, dict[str, Any]]:
    """Load roma_llm_settings.json and apply to llm_agents. Returns applied overrides."""
    if not LLM_SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(LLM_SETTINGS_FILE.read_text(encoding="utf-8"))
        overrides = data.get("overrides", data)
        if not isinstance(overrides, dict):
            logger.warning("LLM settings file: invalid format, ignoring")
            return {}
        # Only known agent keys
        cleaned: dict[str, dict[str, Any]] = {}
        for k, v in overrides.items():
            if k in llm_agents.AGENT_LLM and isinstance(v, dict):
                cleaned[k] = {a: b for a, b in v.items() if b is not None}
        llm_agents.set_runtime_overrides(cleaned)
        logger.info("Loaded LLM settings for %d agent(s) from %s", len(cleaned), LLM_SETTINGS_FILE.name)
        return cleaned
    except Exception as e:
        logger.error("Failed to load LLM settings: %s", e)
        return {}


def save_llm_settings(overrides: dict[str, dict[str, Any]]) -> None:
    """Persist runtime LLM overrides (may contain API keys — file should stay private)."""
    payload = {"overrides": overrides}
    _atomic_write(LLM_SETTINGS_FILE, json.dumps(payload, indent=2))
    logger.info("Saved LLM settings to %s", LLM_SETTINGS_FILE.name)
