"""Eternal Cities — Configuration."""

import json
import os
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_LLM_DEFAULTS_PATH = Path(
    os.environ.get("ETERNAL_LLM_DEFAULTS_PATH", "")
).expanduser() if os.environ.get("ETERNAL_LLM_DEFAULTS_PATH") else _DATA_DIR / "llm_defaults.json"

_LLM_AGENT_KEY_ORDER = (
    "cartographus_skeleton",
    "cartographus_refine",
    "cartographus_survey",
    "urbanista",
)
_LLM_REQUIRED_AGENT_KEYS = frozenset(_LLM_AGENT_KEY_ORDER)


def _load_llm_defaults(path: Path) -> dict[str, Any]:
    """Load LLM routing defaults from JSON (xAI defaults, per-agent routing, optional OpenAI-compatible defaults)."""
    if not path.is_file():
        raise FileNotFoundError(
            f"LLM defaults file not found: {path}. "
            "Set ETERNAL_LLM_DEFAULTS_PATH or add data/llm_defaults.json."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"LLM defaults must be a JSON object: {path}")

    for section in (
        "xai",
        "openai_compatible",
        "agents",
        "agent_labels",
    ):
        if section not in raw:
            raise ValueError(f"LLM defaults missing required key {section!r}: {path}")

    agents = raw["agents"]
    if not isinstance(agents, dict):
        raise ValueError(f"LLM defaults 'agents' must be an object: {path}")
    if frozenset(agents.keys()) != _LLM_REQUIRED_AGENT_KEYS:
        raise ValueError(
            f"LLM defaults 'agents' must have exactly keys {sorted(_LLM_REQUIRED_AGENT_KEYS)!r}, "
            f"got {sorted(agents.keys())!r}: {path}"
        )
    for agent_key, spec in agents.items():
        if not isinstance(spec, dict):
            raise ValueError(f"LLM defaults agents[{agent_key!r}] must be an object: {path}")
        prov = spec.get("provider")
        mod = spec.get("model")
        if not isinstance(prov, str) or not prov.strip():
            raise ValueError(
                f"LLM defaults agents[{agent_key!r}].provider must be a non-empty string: {path}"
            )
        if not isinstance(mod, str) or not mod.strip():
            raise ValueError(
                f"LLM defaults agents[{agent_key!r}].model must be a non-empty string: {path}"
            )

    labels = raw["agent_labels"]
    if not isinstance(labels, dict):
        raise ValueError(f"LLM defaults 'agent_labels' must be an object: {path}")
    if frozenset(labels.keys()) != _LLM_REQUIRED_AGENT_KEYS:
        raise ValueError(
            f"LLM defaults 'agent_labels' must have exactly keys {sorted(_LLM_REQUIRED_AGENT_KEYS)!r}, "
            f"got {sorted(labels.keys())!r}: {path}"
        )
    for agent_key, label in labels.items():
        if not isinstance(label, str) or not label.strip():
            raise ValueError(
                f"LLM defaults agent_labels[{agent_key!r}] must be a non-empty string: {path}"
            )

    xai = raw["xai"]
    if not isinstance(xai, dict):
        raise ValueError(f"LLM defaults 'xai' must be an object: {path}")
    for k in ("base_url", "default_model"):
        if not isinstance(xai.get(k), str) or not xai[k].strip():
            raise ValueError(f"LLM defaults xai.{k} must be a non-empty string: {path}")
    sug = xai.get("model_suggestions")
    if not isinstance(sug, list) or not sug or not all(isinstance(x, str) and x.strip() for x in sug):
        raise ValueError(
            f"LLM defaults xai.model_suggestions must be a non-empty array of strings: {path}"
        )

    oi = raw["openai_compatible"]
    if not isinstance(oi, dict):
        raise ValueError(f"LLM defaults 'openai_compatible' must be an object: {path}")
    if "base_url" not in oi or "default_model" not in oi:
        raise ValueError(
            f"LLM defaults openai_compatible must include base_url and default_model strings: {path}"
        )
    if not isinstance(oi["base_url"], str) or not isinstance(oi["default_model"], str):
        raise ValueError(
            f"LLM defaults openai_compatible base_url and default_model must be strings: {path}"
        )
    oi_sug = oi.get("model_suggestions")
    if oi_sug is not None:
        if not isinstance(oi_sug, list) or not all(isinstance(x, str) for x in oi_sug):
            raise ValueError(
                f"LLM defaults openai_compatible.model_suggestions must be an array of strings: {path}"
            )

    return raw


_LLM_RAW = _load_llm_defaults(_LLM_DEFAULTS_PATH)

# Per-agent defaults (merged with data/llm_settings.json and UI at runtime)
LLM_AGENT_DEFAULTS: dict[str, dict[str, str]] = {
    k: {
        "provider": str(_LLM_RAW["agents"][k]["provider"]).strip(),
        "model": str(_LLM_RAW["agents"][k]["model"]).strip(),
    }
    for k in _LLM_AGENT_KEY_ORDER
}

LLM_AGENT_LABELS: dict[str, str] = {
    k: str(_LLM_RAW["agent_labels"][k]).strip() for k in _LLM_AGENT_KEY_ORDER
}

XAI_MODEL_SUGGESTIONS: tuple[str, ...] = tuple(_LLM_RAW["xai"]["model_suggestions"])

_oi_sug = _LLM_RAW["openai_compatible"].get("model_suggestions")
OPENAI_COMPATIBLE_MODEL_SUGGESTIONS: tuple[str, ...] = tuple(
    x.strip() for x in (_oi_sug or []) if isinstance(x, str) and x.strip()
)

# Default Claude CLI binary when provider is explicitly claude_cli (e.g. old saved settings).
CLAUDE_CLI_BINARY = "claude"

# Grid settings (each tile ≈ 10 m in agent prompts — total city footprint scales with size)
# 4× world size (linear dimensions): 80×80 → 320×320 tiles
GRID_WIDTH = 320
GRID_HEIGHT = 320

# Allow environment variable overrides for grid dimensions and district cap
GRID_WIDTH = int(os.environ.get("ETERNAL_GRID_WIDTH", str(GRID_WIDTH)))
GRID_HEIGHT = int(os.environ.get("ETERNAL_GRID_HEIGHT", str(GRID_HEIGHT)))
MAX_DISTRICTS = int(os.environ.get("ETERNAL_MAX_DISTRICTS", "12"))

STEP_DELAY = float(os.environ.get("ETERNAL_STEP_DELAY", "0.3"))

# Global OpenAI-compatible defaults (secrets only via env or UI — never committed).
OPENAI_COMPATIBLE_BASE_URL = _LLM_RAW["openai_compatible"]["base_url"]
OPENAI_COMPATIBLE_API_KEY = ""
OPENAI_COMPATIBLE_MODEL = _LLM_RAW["openai_compatible"]["default_model"]

# xAI / Grok (API key: XAI_API_KEY env or Configure AI only)
XAI_BASE_URL = _LLM_RAW["xai"]["base_url"]
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
XAI_DEFAULT_MODEL = _LLM_RAW["xai"]["default_model"]

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
