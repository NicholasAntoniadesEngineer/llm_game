"""Curated architectural reference data — measurements and proportion hints for agents."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.config import Config

logger = logging.getLogger("eternal.reference_db")

_CACHE: list[dict[str, Any]] | None = None
_LOOKUP_CACHE: dict[tuple[str, str, int], dict[str, Any] | None] = {}
_CACHE_CONFIG_ID: str | None = None


def _data_path(*, system_configuration: Config) -> Path:
    rel = str(system_configuration.architectural_reference_file_relative).strip()
    root = Path(__file__).resolve().parent.parent
    return root / rel


def load_architectural_entries(*, system_configuration: Config) -> list[dict[str, Any]]:
    global _CACHE, _LOOKUP_CACHE, _CACHE_CONFIG_ID
    config_id = str(system_configuration.architectural_reference_file_relative).strip()
    if _CACHE is not None and _CACHE_CONFIG_ID == config_id:
        return _CACHE
    _CACHE = None
    _CACHE_CONFIG_ID = config_id
    _LOOKUP_CACHE.clear()
    path = _data_path(system_configuration=system_configuration)
    if not path.is_file():
        logger.warning("No architectural_reference.json at %s", path)
        _CACHE = []
        return _CACHE
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("entries")
    if not isinstance(entries, list):
        logger.warning("architectural_reference.json missing entries array")
        _CACHE = []
        return _CACHE
    _CACHE = entries
    return _CACHE


def lookup_architectural_reference(
    building_type: str,
    city: str,
    year: int,
    *,
    system_configuration: Config,
) -> dict[str, Any] | None:
    """
    Return the best-matching reference entry for Urbanista/Historicus prompts.
    More specific matches (city + year window) beat generic entries.
    """
    btype = (building_type or "").strip().lower()
    city_l = (city or "").strip().lower()
    try:
        yi = int(year)
    except (TypeError, ValueError):
        yi = 0

    cache_key = (btype, city_l, yi)
    if cache_key in _LOOKUP_CACHE:
        return _LOOKUP_CACHE[cache_key]

    best: dict[str, Any] | None = None
    best_score = -1

    for e in load_architectural_entries(system_configuration=system_configuration):
        if not isinstance(e, dict):
            continue
        m = e.get("match")
        if not isinstance(m, dict):
            m = {}

        btypes = [str(x).lower() for x in m.get("building_types", [])]
        if btypes and btype not in btypes:
            continue

        cities = [str(c).strip().lower() for c in m.get("cities", [])]
        if cities and city_l not in cities:
            continue

        ymin = m.get("year_min")
        ymax = m.get("year_max")
        if ymin is not None:
            try:
                if yi < int(ymin):
                    continue
            except (TypeError, ValueError):
                pass
        if ymax is not None:
            try:
                if yi > int(ymax):
                    continue
            except (TypeError, ValueError):
                pass

        score = 0
        if btypes:
            score += 3
        if cities:
            score += 4
        if ymin is not None or ymax is not None:
            score += 2
        if score > best_score:
            best_score = score
            best = e

    _LOOKUP_CACHE[cache_key] = best
    return best


def format_reference_for_prompt(entry: dict[str, Any] | None) -> str:
    """Compact text block for LLM injection (not full raw JSON)."""
    if not entry:
        return ""
    lines: list[str] = []
    if entry.get("id"):
        lines.append(f"id: {entry['id']}")
    if entry.get("title"):
        lines.append(str(entry["title"]))
    if entry.get("notes"):
        lines.append(str(entry["notes"]))
    if entry.get("proportion_rules_hints"):
        lines.append("proportion_rules_hints (merge into spec.proportion_rules when they fit the brief):")
        lines.append(json.dumps(entry["proportion_rules_hints"], indent=2))
    if entry.get("measurements_meters_typical"):
        lines.append("typical_meter_ranges (sanity-check against Historian):")
        lines.append(json.dumps(entry["measurements_meters_typical"], indent=2))
    if entry.get("material_hints"):
        lines.append("material_hints (#RRGGBB):")
        lines.append(json.dumps(entry["material_hints"], indent=2))
    if entry.get("citation"):
        lines.append(f"citation: {entry['citation']}")
    return "\n".join(lines).strip()
