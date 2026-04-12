"""Canonical tile and building models for the world grid.

**Contract (post-refactor)**

- ``Tile`` holds terrain, elevation, and a single ``terrain_analysis`` bag for
  slope, aspect, stability, classified ``terrain_type`` string, soil, moisture,
  and temperature. Procedural environment code writes that bag; readers may
  use the properties below for backward-compatible attribute access.
- ``Building`` holds everything that used to live on ``Tile`` for constructed
  structures (name, type, period, procedural ``spec`` JSON, stability score).
  Flavor text and LLM-only fields stay in ``spec``; geometry lives only in
  procedural ``spec`` components validated outside LLM paths.
- Serialization (``to_dict``) remains **flat** for WebSocket clients: building
  fields are merged at the top level when a ``building`` is present.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_logger = logging.getLogger("eternal.tile")

_ALWAYS_SERIALIZE = ("x", "y", "terrain", "elevation", "color", "icon", "turn")


class TerrainType(Enum):
    """Terrain classification values (thresholds still from ``system_config``)."""

    EMPTY = "empty"
    FLAT = "flat"
    GENTLE_SLOPE = "gentle_slope"
    STEEP_SLOPE = "steep_slope"
    CLIFF = "cliff"
    VALLEY = "valley"
    RIDGE = "ridge"
    PLATEAU = "plateau"
    DEPRESSION = "depression"
    WATER = "water"
    MARSH = "marsh"
    SAND = "sand"
    ROCK = "rock"
    FOREST = "forest"
    URBAN = "urban"


@dataclass(slots=True)
class Building:
    """One built structure occupying one or more tiles (anchor carries footprint)."""

    name: str
    building_type: str
    period: str
    spec: dict[str, Any]
    stability_score: float = 1.0
    placed_turn: int = 0


def _terrain_analysis_float(ta: dict[str, Any], key: str) -> float | None:
    raw = ta.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _terrain_analysis_str(ta: dict[str, Any], key: str) -> str | None:
    raw = ta.get(key)
    if raw is None:
        return None
    return str(raw)


@dataclass(slots=True)
class Tile:
    """One grid cell. ``building`` is optional (open terrain / roads have none)."""

    x: int
    y: int
    terrain: str = "empty"
    elevation: float = 0.0
    terrain_analysis: dict[str, Any] = field(default_factory=dict)
    building: Building | None = None
    color: str = "#c2b280"
    icon: str = ""
    turn: int = 0
    description: str | None = None
    historical_note: str | None = None
    scene: str | None = None
    placed_by: str | None = None

    # --- Back-compat: analysis scalars map into ``terrain_analysis`` ------------
    @property
    def terrain_type(self) -> str | None:
        return _terrain_analysis_str(self.terrain_analysis, "terrain_type")

    @terrain_type.setter
    def terrain_type(self, value: str | None) -> None:
        if value is None:
            self.terrain_analysis.pop("terrain_type", None)
        else:
            self.terrain_analysis["terrain_type"] = str(value)

    @property
    def slope(self) -> float | None:
        return _terrain_analysis_float(self.terrain_analysis, "slope")

    @slope.setter
    def slope(self, value: float | None) -> None:
        if value is None:
            self.terrain_analysis.pop("slope", None)
        else:
            self.terrain_analysis["slope"] = float(value)

    @property
    def aspect(self) -> float | None:
        return _terrain_analysis_float(self.terrain_analysis, "aspect")

    @aspect.setter
    def aspect(self, value: float | None) -> None:
        if value is None:
            self.terrain_analysis.pop("aspect", None)
        else:
            self.terrain_analysis["aspect"] = float(value)

    @property
    def roughness(self) -> float | None:
        return _terrain_analysis_float(self.terrain_analysis, "roughness")

    @roughness.setter
    def roughness(self, value: float | None) -> None:
        if value is None:
            self.terrain_analysis.pop("roughness", None)
        else:
            self.terrain_analysis["roughness"] = float(value)

    @property
    def stability(self) -> float | None:
        return _terrain_analysis_float(self.terrain_analysis, "stability")

    @stability.setter
    def stability(self, value: float | None) -> None:
        if value is None:
            self.terrain_analysis.pop("stability", None)
        else:
            self.terrain_analysis["stability"] = float(value)

    @property
    def soil_type(self) -> str | None:
        return _terrain_analysis_str(self.terrain_analysis, "soil_type")

    @soil_type.setter
    def soil_type(self, value: str | None) -> None:
        if value is None:
            self.terrain_analysis.pop("soil_type", None)
        else:
            self.terrain_analysis["soil_type"] = str(value)

    @property
    def moisture(self) -> float | None:
        return _terrain_analysis_float(self.terrain_analysis, "moisture")

    @moisture.setter
    def moisture(self, value: float | None) -> None:
        if value is None:
            self.terrain_analysis.pop("moisture", None)
        else:
            self.terrain_analysis["moisture"] = float(value)

    @property
    def temperature(self) -> float | None:
        return _terrain_analysis_float(self.terrain_analysis, "temperature")

    @temperature.setter
    def temperature(self, value: float | None) -> None:
        if value is None:
            self.terrain_analysis.pop("temperature", None)
        else:
            self.terrain_analysis["temperature"] = float(value)

    # --- Building fields delegated to ``Building`` ------------------------------
    @property
    def building_name(self) -> str | None:
        return self.building.name if self.building else None

    @building_name.setter
    def building_name(self, value: str | None) -> None:
        if value is None and self.building is None:
            return
        if self.building is None:
            self.building = Building(
                name=str(value or ""),
                building_type="building",
                period="",
                spec={},
            )
        else:
            self.building.name = str(value or "")

    @property
    def building_type(self) -> str | None:
        return self.building.building_type if self.building else None

    @building_type.setter
    def building_type(self, value: str | None) -> None:
        if value is None and self.building is None:
            return
        if self.building is None:
            self.building = Building(
                name="",
                building_type=str(value or "building"),
                period="",
                spec={},
            )
        else:
            self.building.building_type = str(value or "building")

    @property
    def period(self) -> str | None:
        return self.building.period if self.building else None

    @period.setter
    def period(self, value: str | None) -> None:
        if value is None and self.building is None:
            return
        if self.building is None:
            self.building = Building(name="", building_type="building", period=str(value or ""), spec={})
        else:
            self.building.period = str(value or "")

    @property
    def spec(self) -> dict[str, Any] | None:
        return self.building.spec if self.building else None

    @spec.setter
    def spec(self, value: dict[str, Any] | None) -> None:
        if value is None and self.building is None:
            return
        if self.building is None:
            self.building = Building(name="", building_type="building", period="", spec=dict(value or {}))
        else:
            self.building.spec = dict(value or {})

    def apply_placement_payload(self, data: dict[str, Any]) -> None:
        """Apply normalized placement dict; unknown keys raise."""
        structural = {"building_name", "building_type", "period", "spec", "stability_score", "placed_turn"}
        tile_scalar = {
            "terrain",
            "elevation",
            "color",
            "icon",
            "turn",
            "description",
            "historical_note",
            "scene",
            "placed_by",
        }
        analysis_keys = {
            "terrain_type",
            "slope",
            "aspect",
            "roughness",
            "stability",
            "soil_type",
            "moisture",
            "temperature",
        }
        for key, raw in data.items():
            if key in ("x", "y"):
                continue
            if raw is None:
                continue
            if key in structural:
                if key == "building_name":
                    self.building_name = str(raw)
                elif key == "building_type":
                    self.building_type = str(raw)
                elif key == "period":
                    self.period = str(raw)
                elif key == "spec":
                    self.spec = raw if isinstance(raw, dict) else {"value": raw}
                elif key == "stability_score":
                    if self.building is None:
                        self.building = Building(name="", building_type="building", period="", spec={})
                    self.building.stability_score = float(raw)
                elif key == "placed_turn":
                    if self.building is None:
                        self.building = Building(name="", building_type="building", period="", spec={})
                    self.building.placed_turn = int(raw)
                continue
            if key in tile_scalar:
                setattr(self, key, raw)
                continue
            if key in analysis_keys:
                self.terrain_analysis[str(key)] = raw
                continue
            if key == "terrain_analysis" and isinstance(raw, dict):
                self.terrain_analysis.update(raw)
                continue
            _logger.debug("Ignoring unmodeled tile payload key %r", key)

    def to_dict(self) -> dict[str, Any]:
        """Sparse JSON for APIs; building nested fields flattened."""
        out: dict[str, Any] = {}
        for k in ("x", "y", "terrain", "elevation", "color", "icon", "turn"):
            v = getattr(self, k)
            if v is not None or k in _ALWAYS_SERIALIZE:
                out[k] = v
        for k in ("description", "historical_note", "scene", "placed_by"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        if self.terrain_analysis:
            out.update({k: v for k, v in self.terrain_analysis.items() if v is not None})
        if self.building:
            out["building_name"] = self.building.name
            out["building_type"] = self.building.building_type
            out["period"] = self.building.period
            out["spec"] = self.building.spec
            out["stability_score"] = self.building.stability_score
            out["placed_turn"] = self.building.placed_turn
        return out
