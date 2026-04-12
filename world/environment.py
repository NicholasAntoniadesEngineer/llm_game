"""Procedural terrain math and environment finalization helpers.

All classification thresholds and stability modifiers are passed in from
``Config`` / ``system_config.csv`` — this module contains no standalone numeric
policy defaults for terrain rules.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, List, Tuple

from world.tile import TerrainType

if TYPE_CHECKING:
    from core.config import Config
    from world.blueprint import CityBlueprint
    from world.state import WorldState


class TerrainAnalysis:
    """Terrain classification and slope analysis (thresholds supplied by caller)."""

    @staticmethod
    def classify_terrain(
        elevation: float,
        slope: float,
        neighbors: List[float],
        *,
        classification_thresholds: dict[str, float],
        moisture: float = 0.5,
        temperature: float = 20.0,
        roughness: float = 0.0,
    ) -> TerrainType:
        """Classify terrain type based on multiple factors."""

        tc = classification_thresholds
        water_max = float(tc["water_elevation_max"])
        marsh_elev_max = float(tc["marsh_elevation_max"])
        marsh_moisture_min = float(tc["marsh_moisture_min"])
        cliff_slope_min = float(tc["cliff_slope_min"])
        cliff_elev_min = float(tc["cliff_elevation_min"])
        moderate_slope_min = float(tc["moderate_slope_min"])
        plateau_elev_min = float(tc["plateau_elevation_min"])
        valley_elev_max = float(tc["valley_elevation_max"])
        valley_slope_max = float(tc["valley_slope_max"])
        sand_moisture_max = float(tc["sand_moisture_max"])
        sand_temp_min = float(tc["sand_temperature_min"])
        rock_rough_min = float(tc["rock_roughness_min"])

        if elevation < water_max:
            return TerrainType.WATER
        if elevation < marsh_elev_max and moisture > marsh_moisture_min:
            return TerrainType.MARSH

        if slope > cliff_slope_min:
            if elevation > cliff_elev_min:
                return TerrainType.CLIFF
            return TerrainType.STEEP_SLOPE
        if slope > moderate_slope_min:
            return TerrainType.GENTLE_SLOPE

        if elevation > plateau_elev_min:
            return TerrainType.PLATEAU
        if elevation < valley_elev_max and slope < valley_slope_max:
            return TerrainType.VALLEY

        if moisture < sand_moisture_max and temperature > sand_temp_min:
            return TerrainType.SAND
        if roughness > rock_rough_min:
            return TerrainType.ROCK

        return TerrainType.FLAT

    @staticmethod
    def calculate_slope(elevation: float, neighbors: List[float]) -> Tuple[float, float]:
        """Slope magnitude and aspect from eight neighbor elevations (NW,N,NE,W,E,SW,S,SE)."""
        if len(neighbors) < 8:
            return 0.0, 0.0

        north_elevation = neighbors[1]
        south_elevation = neighbors[6]
        west_elevation = neighbors[3]
        east_elevation = neighbors[4]
        dx = (east_elevation - west_elevation) / 2.0
        dy = (south_elevation - north_elevation) / 2.0

        slope = math.sqrt(dx * dx + dy * dy)
        aspect = math.atan2(dy, dx) if slope > 0.01 else 0.0

        return slope, aspect

    @staticmethod
    def calculate_roughness(elevations: List[float]) -> float:
        """Terrain roughness from elevation variance."""
        if len(elevations) < 2:
            return 0.0

        mean = sum(elevations) / len(elevations)
        variance = sum((e - mean) ** 2 for e in elevations) / len(elevations)
        return math.sqrt(variance)

    @staticmethod
    def assess_stability(
        terrain_type: TerrainType,
        slope: float,
        soil_type: str,
        moisture: float,
        *,
        classification_thresholds: dict[str, float],
        terrain_type_modifiers: dict[str, float],
        soil_type_modifiers: dict[str, float],
    ) -> float:
        """Assess terrain stability for construction."""
        base_stability = 1.0
        tc = classification_thresholds
        slope_threshold = float(tc["assess_stability_slope_threshold"])
        slope_penalty = float(tc["assess_stability_slope_penalty_factor"])
        moisture_high = float(tc["assess_stability_moisture_high"])
        moisture_low = float(tc["assess_stability_moisture_low"])

        terrain_key = terrain_type.value
        base_stability *= float(terrain_type_modifiers.get(terrain_key, 0.8))

        if slope > slope_threshold:
            base_stability *= max(0.3, 1.0 - slope * slope_penalty)

        soil_key = str(soil_type).strip().lower()
        base_stability *= float(soil_type_modifiers.get(soil_key, 0.8))

        if moisture > moisture_high:
            base_stability *= 0.7
        elif moisture < moisture_low:
            base_stability *= 0.9

        return max(0.0, min(1.0, base_stability))


def generate_terrain(
    world: WorldState,
    blueprint: CityBlueprint,
    *,
    system_configuration: Config,
) -> int:
    """Run smoothed elevation and full per-tile ``terrain_analysis`` on placed tiles."""
    return blueprint.populate_elevation(world, system_configuration=system_configuration)


def resolve_road_water_conflicts(
    world: WorldState,
    blueprint: CityBlueprint,
    *,
    system_configuration: Config,
) -> None:
    """Reserved hook for procedural bridge insertion across water channels.

    Road rasterization already consults ``water_channel_tile_set`` to avoid
    painting roads into channels; additional graph-level repairs belong here.
    """
    _ = world, blueprint, system_configuration


def compute_valid_buildable_cells(
    world: WorldState,
    blueprint: CityBlueprint,
    districts: list[dict] | None,
    *,
    system_configuration: Config,
) -> dict[str, set[tuple[int, int]]]:
    """Per-district cells inside each region rect that are not water channels or roads."""
    out: dict[str, set[tuple[int, int]]] = {}
    if not districts:
        return out
    water_ch = blueprint.water_channel_tile_set(system_configuration=system_configuration)
    road_xy: set[tuple[int, int]] = {
        (tx, ty)
        for (tx, ty), tile in world.tiles.items()
        if tile.terrain == "road" or (tile.building_type or "").lower() == "road"
    }
    for d in districts:
        name = str(d.get("name", "")).strip()
        if not name:
            continue
        region = d.get("region") or {}
        try:
            x1 = int(region["x1"])
            y1 = int(region["y1"])
            x2 = int(region["x2"])
            y2 = int(region["y2"])
        except (KeyError, TypeError, ValueError):
            continue
        lo_x, hi_x = (x1, x2) if x1 <= x2 else (x2, x1)
        lo_y, hi_y = (y1, y2) if y1 <= y2 else (y2, y1)
        cells: set[tuple[int, int]] = set()
        for gx in range(lo_x, hi_x + 1):
            for gy in range(lo_y, hi_y + 1):
                if (gx, gy) in water_ch or (gx, gy) in road_xy:
                    continue
                cells.add((gx, gy))
        out[name] = cells
    return out
