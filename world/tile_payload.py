"""Shared tile payload normalization for all writers (orchestration, roads, persistence)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import Config


def normalize_tile_dict_for_world(tile_data: dict, *, system_configuration: "Config") -> dict:
    """Shallow copy with numeric ``elevation`` clamped to configured world bounds."""
    out = dict(tile_data)
    elev_min = system_configuration.world_place_tile_min_elevation
    elev_max = system_configuration.grid.maximum_elevation_value
    elev = out.get("elevation")
    if isinstance(elev, (int, float)):
        out["elevation"] = max(float(elev_min), min(float(elev), float(elev_max)))
    return out
