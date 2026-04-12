"""Canonical tile and building models for the world grid.

**Contract**

- ``Tile`` holds terrain, elevation, and a typed ``TerrainAnalysis`` snapshot
  (slope, aspect, stability, classified ``terrain_type`` string, soil, moisture,
  temperature). Procedural environment code fills that model; tile properties
  expose the same scalar names as before.
- ``Building`` is immutable (``frozen=True``) with an explicit integer footprint
  (relative offsets from the anchor tile, always including ``(0, 0)``).
- Serialization (``to_dict``) remains **flat** for WebSocket clients: building
  fields are merged at the top level when a ``building`` is present.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any

_logger = logging.getLogger("eternal.tile")

_ALWAYS_SERIALIZE = ("x", "y", "terrain", "elevation", "color", "icon", "turn")

_WATER_TERRAIN_LABELS = frozenset({"water", "marsh", "swamp"})

if TYPE_CHECKING:
    from core.config import Config
    from world.state import WorldState


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
class TerrainAnalysis:
    """Per-tile procedural terrain snapshot (strict fields; None means unset)."""

    terrain_type: str | None = None
    slope: float | None = None
    aspect: float | None = None
    roughness: float | None = None
    stability: float | None = None
    soil_type: str | None = None
    moisture: float | None = None
    temperature: float | None = None

    def merge_from_flat_mapping(self, raw: dict[str, Any]) -> None:
        """Update known keys from a mapping (e.g. persistence or partial payloads)."""
        mapping = {
            "terrain_type": ("terrain_type", str),
            "slope": ("slope", float),
            "aspect": ("aspect", float),
            "roughness": ("roughness", float),
            "stability": ("stability", float),
            "soil_type": ("soil_type", str),
            "moisture": ("moisture", float),
            "temperature": ("temperature", float),
        }
        for json_key, (attr_name, caster) in mapping.items():
            if json_key not in raw:
                continue
            val = raw[json_key]
            if val is None:
                setattr(self, attr_name, None)
                continue
            try:
                setattr(self, attr_name, caster(val))
            except (TypeError, ValueError):
                _logger.debug("Ignored invalid terrain_analysis %s=%r", json_key, val)

    def as_flat_non_none_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for attr_name in (
            "terrain_type",
            "slope",
            "aspect",
            "roughness",
            "stability",
            "soil_type",
            "moisture",
            "temperature",
        ):
            val = getattr(self, attr_name)
            if val is not None:
                out[attr_name] = val
        return out


@dataclass(frozen=True)
class Building:
    """One built structure; footprint is relative tile offsets from placement anchor."""

    name: str
    building_type: str
    period: str
    spec: dict[str, Any] = field(default_factory=dict)
    stability_score: float = 1.0
    placed_turn: int = 0
    footprint_relative_tiles: tuple[tuple[int, int], ...] = ((0, 0),)

    def __post_init__(self) -> None:
        if not self.footprint_relative_tiles:
            raise ValueError("footprint_relative_tiles must be non-empty")
        seen: set[tuple[int, int]] = set()
        for cell in self.footprint_relative_tiles:
            if len(cell) != 2:
                raise ValueError(f"invalid footprint offset {cell!r}")
            dx, dy = int(cell[0]), int(cell[1])
            pair = (dx, dy)
            if pair in seen:
                raise ValueError(f"duplicate footprint cell {pair!r}")
            seen.add(pair)
        if (0, 0) not in seen:
            raise ValueError("footprint_relative_tiles must include anchor (0, 0)")

    def validate_placement(
        self,
        world: WorldState,
        anchor_world_x: int,
        anchor_world_y: int,
        *,
        system_configuration: Config,
        water_channel_tile_xy_set: set[tuple[int, int]] | None = None,
    ) -> None:
        """Raise ``ValueError`` when footprint violates stability, slope, water, or roads."""
        from core.config import Config as ConfigClass  # local for clarity

        if not isinstance(system_configuration, ConfigClass):
            raise TypeError("system_configuration must be a Config instance")

        min_stability = float(system_configuration.terrain.min_buildable_cell_stability_value)
        max_slope = float(system_configuration.terrain.placement_max_abs_slope_value)
        require_road = int(system_configuration.terrain.placement_require_cardinal_road_adjacency_flag) == 1
        btype_lower = str(self.building_type).strip().lower()

        for dx, dy in self.footprint_relative_tiles:
            wx, wy = anchor_world_x + int(dx), anchor_world_y + int(dy)
            tile = world.get_tile(wx, wy)
            if tile is None:
                raise ValueError(f"placement footprint missing tile at ({wx},{wy})")

            if water_channel_tile_xy_set is not None and (wx, wy) in water_channel_tile_xy_set:
                raise ValueError(f"footprint cell ({wx},{wy}) lies in a water channel")

            terrain_l = str(tile.terrain).strip().lower()
            if terrain_l in _WATER_TERRAIN_LABELS and btype_lower != "road":
                raise ValueError(f"footprint cell ({wx},{wy}) has water-class terrain {tile.terrain!r}")

            if btype_lower != "road" and terrain_l == "road":
                raise ValueError(f"footprint cell ({wx},{wy}) overlaps an existing road")

            stability_val = tile.stability
            if stability_val is not None and float(stability_val) < min_stability:
                raise ValueError(
                    f"footprint cell ({wx},{wy}) stability {stability_val} below minimum {min_stability}"
                )

            slope_val = tile.slope
            if slope_val is not None and float(slope_val) > max_slope:
                raise ValueError(
                    f"footprint cell ({wx},{wy}) slope {slope_val} exceeds maximum {max_slope}"
                )

        if require_road and btype_lower != "road":
            has_cardinal_road = False
            for rdx, rdy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                neighbor = world.get_tile(anchor_world_x + rdx, anchor_world_y + rdy)
                if neighbor is not None and str(neighbor.terrain).strip().lower() == "road":
                    has_cardinal_road = True
                    break
            if not has_cardinal_road:
                raise ValueError(
                    f"anchor ({anchor_world_x},{anchor_world_y}) has no cardinally adjacent road "
                    "(placement_require_cardinal_road_adjacency is enabled)"
                )


@dataclass(slots=True)
class Tile:
    """One grid cell. ``building`` is optional (open terrain / roads have none)."""

    x: int
    y: int
    terrain: str = "empty"
    elevation: float = 0.0
    terrain_analysis: TerrainAnalysis = field(default_factory=TerrainAnalysis)
    building: Building | None = None
    color: str = "#c2b280"
    icon: str = ""
    turn: int = 0
    description: str | None = None
    historical_note: str | None = None
    scene: str | None = None
    placed_by: str | None = None

    @property
    def terrain_type(self) -> str | None:
        return self.terrain_analysis.terrain_type

    @terrain_type.setter
    def terrain_type(self, value: str | None) -> None:
        self.terrain_analysis.terrain_type = None if value is None else str(value)

    @property
    def slope(self) -> float | None:
        return self.terrain_analysis.slope

    @slope.setter
    def slope(self, value: float | None) -> None:
        self.terrain_analysis.slope = None if value is None else float(value)

    @property
    def aspect(self) -> float | None:
        return self.terrain_analysis.aspect

    @aspect.setter
    def aspect(self, value: float | None) -> None:
        self.terrain_analysis.aspect = None if value is None else float(value)

    @property
    def roughness(self) -> float | None:
        return self.terrain_analysis.roughness

    @roughness.setter
    def roughness(self, value: float | None) -> None:
        self.terrain_analysis.roughness = None if value is None else float(value)

    @property
    def stability(self) -> float | None:
        return self.terrain_analysis.stability

    @stability.setter
    def stability(self, value: float | None) -> None:
        self.terrain_analysis.stability = None if value is None else float(value)

    @property
    def soil_type(self) -> str | None:
        return self.terrain_analysis.soil_type

    @soil_type.setter
    def soil_type(self, value: str | None) -> None:
        self.terrain_analysis.soil_type = None if value is None else str(value)

    @property
    def moisture(self) -> float | None:
        return self.terrain_analysis.moisture

    @moisture.setter
    def moisture(self, value: float | None) -> None:
        self.terrain_analysis.moisture = None if value is None else float(value)

    @property
    def temperature(self) -> float | None:
        return self.terrain_analysis.temperature

    @temperature.setter
    def temperature(self, value: float | None) -> None:
        self.terrain_analysis.temperature = None if value is None else float(value)

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
            self.building = replace(self.building, name=str(value or ""))

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
            self.building = replace(self.building, building_type=str(value or "building"))

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
            self.building = replace(self.building, period=str(value or ""))

    @property
    def spec(self) -> dict[str, Any] | None:
        return self.building.spec if self.building else None

    @spec.setter
    def spec(self, value: dict[str, Any] | None) -> None:
        if value is None and self.building is None:
            return
        spec_dict = dict(value or {})
        if self.building is None:
            self.building = Building(name="", building_type="building", period="", spec=spec_dict)
        else:
            self.building = replace(self.building, spec=spec_dict)

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
                    self.building = replace(self.building, stability_score=float(raw))
                elif key == "placed_turn":
                    if self.building is None:
                        self.building = Building(name="", building_type="building", period="", spec={})
                    self.building = replace(self.building, placed_turn=int(raw))
                continue
            if key in tile_scalar:
                setattr(self, key, raw)
                continue
            if key in analysis_keys:
                patch = {str(key): raw}
                self.terrain_analysis.merge_from_flat_mapping(patch)
                continue
            if key == "terrain_analysis" and isinstance(raw, dict):
                self.terrain_analysis.merge_from_flat_mapping(raw)
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
        out.update(self.terrain_analysis.as_flat_non_none_dict())
        if self.building:
            out["building_name"] = self.building.name
            out["building_type"] = self.building.building_type
            out["period"] = self.building.period
            out["spec"] = self.building.spec
            out["stability_score"] = self.building.stability_score
            out["placed_turn"] = self.building.placed_turn
        return out
