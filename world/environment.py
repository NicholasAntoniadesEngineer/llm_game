"""Procedural terrain math and environment finalization helpers.

All classification thresholds and stability modifiers are passed in from
``Config`` / ``system_config.csv`` — this module contains no standalone numeric
policy defaults for terrain rules.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING, List, Tuple

from orchestration.world_commit import apply_tile_placements
from world.roads import bresenham_line, road_dict_allows_water_crossing
from world.tile import TerrainType

if TYPE_CHECKING:
    from core.config import Config
    from world.blueprint import CityBlueprint
    from world.state import WorldState

logger = logging.getLogger("eternal.environment")


class TerrainFieldEvaluator:
    """Pure terrain classification and slope math (thresholds supplied by caller)."""

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


def _ordered_centerline_cells_for_road_dict(road_dict: dict) -> list[tuple[int, int]]:
    """All grid cells along a road polyline in traversal order (dedupe consecutive)."""
    raw_points = road_dict.get("points", [])
    waypoints: list[tuple[int, int]] = []
    for point in raw_points:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            waypoints.append((int(point[0]), int(point[1])))
        elif isinstance(point, dict):
            waypoints.append((int(point.get("x", 0)), int(point.get("y", 0))))
    if len(waypoints) < 2:
        return []
    ordered_cells: list[tuple[int, int]] = []
    for segment_index in range(len(waypoints) - 1):
        x0, y0 = waypoints[segment_index]
        x1, y1 = waypoints[segment_index + 1]
        for cell in bresenham_line(x0, y0, x1, y1):
            if not ordered_cells or ordered_cells[-1] != cell:
                ordered_cells.append(cell)
    return ordered_cells


def _split_road_dict_at_water_channel(
    road_dict: dict,
    water_channel_tile_xy_set: set[tuple[int, int]],
) -> list[dict]:
    """Deterministically split a polyline road into dry segments plus explicit water crossings."""
    if road_dict_allows_water_crossing(road_dict):
        return [road_dict]
    centerline = _ordered_centerline_cells_for_road_dict(road_dict)
    if len(centerline) < 2:
        return [road_dict]

    runs: list[tuple[bool, list[tuple[int, int]]]] = []
    run_cells: list[tuple[int, int]] = []
    run_is_water = centerline[0] in water_channel_tile_xy_set
    for cell in centerline:
        in_water = cell in water_channel_tile_xy_set
        if in_water != run_is_water and run_cells:
            runs.append((run_is_water, list(run_cells)))
            run_cells = [cell]
            run_is_water = in_water
        else:
            run_cells.append(cell)
    if run_cells:
        runs.append((run_is_water, list(run_cells)))

    base_name = str(road_dict.get("name", "road"))
    road_type = road_dict.get("type", "vicus")
    width = road_dict.get("width", 1)
    split_roads: list[dict] = []
    for run_index, (is_water_run, cells) in enumerate(runs):
        if len(cells) < 2:
            continue
        point_list = [[int(cx), int(cy)] for cx, cy in cells]
        sub_name = base_name if len(runs) == 1 else f"{base_name}__seg{run_index + 1}"
        sub: dict = {
            "name": sub_name,
            "type": road_type,
            "points": point_list,
            "width": width,
        }
        if is_water_run:
            sub["crosses_water"] = True
        split_roads.append(sub)

    return split_roads if split_roads else [road_dict]


def resolve_road_water_conflicts(
    world: WorldState,
    blueprint: CityBlueprint,
    *,
    system_configuration: Config,
) -> int:
    """Split non-bridge roads that cross rasterized water channels into bridge + dry segments.

    Mutates only ``blueprint.roads`` ordering and segmentation; ``world`` is unused but kept
    for API symmetry with other environment passes.
    """
    _ = world
    water_channel_tile_xy_set = blueprint.water_channel_tile_set(system_configuration=system_configuration)
    if not water_channel_tile_xy_set:
        return 0
    original_roads = list(blueprint.roads)
    rebuilt: list[dict] = []
    split_count = 0
    for road in original_roads:
        split_list = _split_road_dict_at_water_channel(road, water_channel_tile_xy_set)
        if len(split_list) > 1:
            split_count += len(split_list) - 1
        rebuilt.extend(split_list)
    if rebuilt != original_roads:
        blueprint.roads = rebuilt
        blueprint.reset_water_adjacency_cache()
        logger.info(
            "resolve_road_water_conflicts: rewrote %d blueprint roads into %d segments (extra=%d)",
            len(original_roads),
            len(rebuilt),
            split_count,
        )
    return split_count


def generate_terrain(
    world: WorldState,
    blueprint: CityBlueprint,
    *,
    system_configuration: Config,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> int:
    """Run smoothed elevation and full per-tile analysis; idempotent, chunk-batched apply.

    Recomputes the authoritative elevation payload, skips writes where tiles already match,
    and invokes ``progress_callback(done_chunks, total_chunks, label)`` after each chunk.
    """
    tile_updates = blueprint.compute_elevation_tile_field_updates(
        world, system_configuration=system_configuration
    )
    if not tile_updates:
        return 0

    chunk_size = world.chunk_size_tiles
    updates_by_chunk: dict[tuple[int, int], dict[tuple[int, int], dict]] = defaultdict(dict)
    for (tx, ty), payload in tile_updates.items():
        updates_by_chunk[(tx // chunk_size, ty // chunk_size)][(tx, ty)] = payload

    ordered_chunk_keys = sorted(updates_by_chunk.keys())
    total_chunks = len(ordered_chunk_keys)
    updated_tiles = 0
    for done_index, ck in enumerate(ordered_chunk_keys, start=1):
        chunk_batch: list[tuple[int, int, dict]] = []
        for (tx, ty) in sorted(updates_by_chunk[ck].keys()):
            payload = updates_by_chunk[ck][(tx, ty)]
            tile = world.get_tile(tx, ty)
            if not tile:
                continue
            if blueprint.tile_payload_matches_current_tile(tile, payload):
                continue
            chunk_batch.append((tx, ty, payload))
        if chunk_batch:
            batch_result = apply_tile_placements(
                world,
                chunk_batch,
                system_configuration=system_configuration,
            )
            if batch_result.place_tile_rejections_count:
                logger.warning(
                    "generate_terrain chunk %s: place_tile rejected %s updates (coordinate guard)",
                    ck,
                    batch_result.place_tile_rejections_count,
                )
            updated_tiles += len(batch_result.placed_tile_dicts)
        if progress_callback is not None:
            progress_callback(done_index, total_chunks, f"elevation_chunk_{ck[0]}_{ck[1]}")
    logger.info(
        "generate_terrain: chunks=%d tile_writes=%d (idempotent compare)",
        total_chunks,
        updated_tiles,
    )
    return updated_tiles


def compute_valid_buildable_cells(
    world: WorldState,
    blueprint: CityBlueprint,
    districts: list[dict] | None,
    *,
    system_configuration: Config,
) -> dict[str, set[tuple[int, int]]]:
    """Per-district cells inside each region rect that are not water channels, roads, or unstable."""
    out: dict[str, set[tuple[int, int]]] = {}
    if not districts:
        return out
    min_stability_required = float(system_configuration.terrain.min_buildable_cell_stability_value)
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
                probe_tile = world.get_tile(gx, gy)
                if probe_tile is not None:
                    stability_value = probe_tile.stability
                    if stability_value is not None and float(stability_value) < min_stability_required:
                        continue
                cells.add((gx, gy))
        out[name] = cells
    return out
