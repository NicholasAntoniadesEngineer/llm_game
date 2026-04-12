"""Stable fingerprints for save format, caches, and district identity."""

from __future__ import annotations

import hashlib
import json
from typing import Any

SAVE_FORMAT_VERSION = 2
CACHE_WRAP_VERSION = 1


def _canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_run_fingerprint(
    scenario: dict[str, Any] | None,
    chunk_size: int,
    grid_width: int,
    grid_height: int,
) -> str:
    """Fingerprint for run + grid + chunk size (invalidates district/survey caches on change)."""
    scen_subset: dict[str, Any] = {}
    if isinstance(scenario, dict):
        for key in ("location", "period", "focus_year", "year_start", "year_end", "ruler"):
            if key in scenario:
                scen_subset[key] = scenario[key]
    payload = {
        "scenario": scen_subset,
        "chunk_size": int(chunk_size),
        "grid": [int(grid_width), int(grid_height)],
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()[:32]


def compute_districts_layout_fingerprint(districts: list[dict]) -> str:
    """Stable hash of district list order, ids, names, and regions."""
    normalized: list[dict[str, Any]] = []
    for idx, district in enumerate(districts):
        if not isinstance(district, dict):
            continue
        normalized.append(
            {
                "i": idx,
                "name": district.get("name"),
                "region": district.get("region"),
                "district_id": district.get("district_id"),
            }
        )
    return hashlib.sha256(_canonical_json_bytes(normalized)).hexdigest()[:24]


def ensure_district_ids(districts: list[dict]) -> None:
    """Assign stable ``district_id`` to each district dict (in-place)."""
    for idx, district in enumerate(districts):
        if not isinstance(district, dict):
            continue
        existing = district.get("district_id")
        if isinstance(existing, str) and existing.strip():
            continue
        key_material = {"index": idx, "name": district.get("name"), "region": district.get("region")}
        district["district_id"] = hashlib.sha256(_canonical_json_bytes(key_material)).hexdigest()[:16]


def district_survey_key(district: dict) -> str:
    """Cache key for survey results (stable across renames if ``district_id`` preserved)."""
    if not isinstance(district, dict):
        return "unknown"
    did = district.get("district_id")
    if isinstance(did, str) and did.strip():
        return did.strip()
    ensure_district_ids([district])
    return str(district["district_id"])
