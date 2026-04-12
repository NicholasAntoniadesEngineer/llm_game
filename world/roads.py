"""Road rasterization and elevation computation for city blueprints."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from world.blueprint import CityBlueprint
    from world.state import WorldState

logger = logging.getLogger("eternal.roads")


def bresenham_line(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    """Integer line drawing algorithm (Bresenham).

    Returns all grid cells along the line from (x0,y0) to (x1,y1).
    """
    points = []
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy

    while True:
        points.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy

    return points


def _widen_line(
    points: list[tuple[int, int]],
    width: int,
    *,
    forbidden_tile_xy_set: set[tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    """Expand a polyline to an integer tile corridor by offsetting perpendicular to each segment.

    width=1: centerline only. width>1: for each consecutive pair, step ``half=floor(width/2)``
    tiles along the unit perpendicular on both sides (cardinal-rounded offsets).
    When ``forbidden_tile_xy_set`` is set, widened cells that fall in the set are omitted
    (centerline cells are never removed by this filter).
    """
    if width <= 1:
        return points

    expanded = set(points)
    half = width // 2
    if half < 1:
        return list(expanded)

    def _offsets_for_tangent(tdx: int, tdy: int) -> list[tuple[int, int]]:
        length = math.hypot(float(tdx), float(tdy))
        if length < 1e-9:
            return []
        px = -tdy / length
        py = tdx / length
        out: list[tuple[int, int]] = []
        for k in range(1, half + 1):
            for sign in (-1, 1):
                out.append((int(round(sign * k * px)), int(round(sign * k * py))))
        return out

    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        tdx, tdy = x1 - x0, y1 - y0
        for ox, oy in _offsets_for_tangent(tdx, tdy):
            for (sx, sy) in bresenham_line(x0, y0, x1, y1):
                cell = (sx + ox, sy + oy)
                if forbidden_tile_xy_set is not None and cell in forbidden_tile_xy_set:
                    continue
                expanded.add(cell)

    return list(expanded)


def water_features_channel_tiles(
    water_features: list[dict],
    *,
    default_channel_width_tiles: int,
) -> set[tuple[int, int]]:
    """Rasterize blueprint water polylines to tile occupancy (centerline plus corridor width).

    Used so ordinary roads do not overwrite the river/lake channel. Per-feature keys
    ``channel_width_tiles`` or ``width`` override the default width when present and valid.
    """
    if default_channel_width_tiles < 1:
        raise ValueError("default_channel_width_tiles must be >= 1")
    occupied_channel_tiles: set[tuple[int, int]] = set()
    for water_body in water_features or []:
        raw_points = water_body.get("points", [])
        waypoints: list[tuple[int, int]] = []
        for point in raw_points:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                waypoints.append((int(point[0]), int(point[1])))
            elif isinstance(point, dict):
                waypoints.append((int(point.get("x", 0)), int(point.get("y", 0))))
        if len(waypoints) < 2:
            continue
        raw_width = water_body.get("channel_width_tiles", water_body.get("width"))
        if raw_width is None:
            channel_width_tiles = default_channel_width_tiles
        else:
            try:
                channel_width_tiles = int(raw_width)
            except (TypeError, ValueError):
                channel_width_tiles = default_channel_width_tiles
        channel_width_tiles = max(1, channel_width_tiles)
        segment_centerline_points: list[tuple[int, int]] = []
        for segment_index in range(len(waypoints) - 1):
            segment_centerline_points.extend(
                bresenham_line(
                    waypoints[segment_index][0],
                    waypoints[segment_index][1],
                    waypoints[segment_index + 1][0],
                    waypoints[segment_index + 1][1],
                )
            )
        dedupe_seen: set[tuple[int, int]] = set()
        unique_centerline: list[tuple[int, int]] = []
        for cell in segment_centerline_points:
            if cell not in dedupe_seen:
                dedupe_seen.add(cell)
                unique_centerline.append(cell)
        if channel_width_tiles > 1:
            corridor_cells = _widen_line(unique_centerline, channel_width_tiles)
        else:
            corridor_cells = unique_centerline
        occupied_channel_tiles.update(corridor_cells)
    return occupied_channel_tiles


def road_dict_allows_water_crossing(road_dict: dict) -> bool:
    """True when the road is treated as a bridge or deliberate water crossing (may occupy water tiles)."""
    if road_dict.get("crosses_water") is True or road_dict.get("allow_water_crossing") is True:
        return True
    road_type_key = str(road_dict.get("type", "")).strip().lower()
    if road_type_key in {"ponte", "bridge", "aqueduct", "causeway", "ford"}:
        return True
    name_lower = str(road_dict.get("name", "")).lower()
    for crossing_marker in ("bridge", "ponte", "causeway", "ford"):
        if crossing_marker in name_lower:
            return True
    return False


def collect_road_tile_placements(
    world: WorldState,
    road_dict: dict,
    blueprint: CityBlueprint | None = None,
) -> list[tuple[int, int, dict]]:
    """Return ``(x, y, tile_payload)`` triples a road raster would place (no ``place_tile`` calls)."""
    name = road_dict.get("name", "road")
    road_type = road_dict.get("type", "vicus")
    raw_points = road_dict.get("points", [])
    width = road_dict.get("width", 1)

    if not raw_points or len(raw_points) < 2:
        return []

    waypoints: list[tuple[int, int]] = []
    for p in raw_points:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            waypoints.append((int(p[0]), int(p[1])))
        elif isinstance(p, dict):
            waypoints.append((int(p.get("x", 0)), int(p.get("y", 0))))

    if len(waypoints) < 2:
        return []

    allows_water_crossing = road_dict_allows_water_crossing(road_dict)
    blueprint_water_channel_tiles: set[tuple[int, int]] = set()
    if blueprint is not None and not allows_water_crossing:
        blueprint_water_channel_tiles = blueprint.water_channel_tile_set(
            system_configuration=world.system_configuration
        )
    forbidden_for_widen = blueprint_water_channel_tiles

    all_line_points: list[tuple[int, int]] = []
    for i in range(len(waypoints) - 1):
        segment = bresenham_line(waypoints[i][0], waypoints[i][1],
                                  waypoints[i + 1][0], waypoints[i + 1][1])
        all_line_points.extend(segment)

    seen: set[tuple[int, int]] = set()
    unique_points: list[tuple[int, int]] = []
    for p in all_line_points:
        if p not in seen:
            seen.add(p)
            unique_points.append(p)

    if width > 1:
        road_tiles = _widen_line(
            unique_points,
            width,
            forbidden_tile_xy_set=forbidden_for_widen if not allows_water_crossing else None,
        )
    else:
        road_tiles = unique_points

    road_colors = world.system_configuration.terrain.road_surface_colors_by_type_dictionary
    road_type_key = str(road_type).strip().lower()
    color = road_colors.get(road_type_key) or road_colors.get("default", "#808080")

    out: list[tuple[int, int, dict]] = []
    for x, y in road_tiles:
        existing = world.get_tile(x, y)
        terrain_kind = getattr(existing, "terrain", "empty") if existing else "empty"

        if not allows_water_crossing:
            if (x, y) in blueprint_water_channel_tiles:
                continue
            if terrain_kind in ("water", "marsh", "swamp"):
                continue
            if existing and terrain_kind not in ("empty", "road"):
                continue
        else:
            if existing and terrain_kind not in ("empty", "road", "water", "marsh", "swamp"):
                continue

        elev = 0.0
        if blueprint and blueprint.elevation_map:
            elev = blueprint.elevation_map.get((x, y), 0.0)
        elif blueprint and blueprint.hills:
            elev = compute_elevation(blueprint.hills, x, y)

        tile_data = {
            "terrain": "road",
            "building_name": name,
            "building_type": "road",
            "description": f"{name} ({road_type})",
            "color": color,
            "elevation": round(elev, 3),
        }
        out.append((x, y, tile_data))
    return out


def rasterize_road(
    world: WorldState,
    road_dict: dict,
    blueprint: CityBlueprint | None = None,
    *,
    apply_placements: bool = True,
) -> int:
    """Place road tiles along a road's waypoints using Bresenham line drawing.

    road_dict: {name, type, points:[(x,y),...], width}
    When ``apply_placements`` is False, no ``WorldState.place_tile`` calls are made and the
    return value is the count of tiles that *would* have been placed.
    """
    triples = collect_road_tile_placements(world, road_dict, blueprint)
    if not apply_placements:
        return len(triples)
    if not triples:
        return 0
    from orchestration.world_commit import apply_tile_placements

    apply_tile_placements(
        world,
        triples,
        system_configuration=world.system_configuration,
    )
    return len(triples)


def compute_elevation(hills: list[dict], x: int, y: int, terrain_features: dict | None = None) -> float:
    """Compute elevation at a point from hills and terrain features.

    Enhanced with rivers, coastlines, and natural formations.
    Uses gaussian falloff: elev = sum(peak * exp(-dist^2 / (2 * radius^2)))
    """
    if not hills and not terrain_features:
        return 0.0

    total = 0.0

    # Basic hill elevation (Gaussian) + wide foothill octave for smoother gradients between peaks
    for hill in hills:
        cx = hill.get("cx", 0)
        cy = hill.get("cy", 0)
        radius = hill.get("radius", 1)
        peak = hill.get("peak", 1.0)

        dist_sq = (x - cx) ** 2 + (y - cy) ** 2
        sigma_sq = 2.0 * radius * radius
        if sigma_sq > 0:
            contribution = peak * math.exp(-dist_sq / sigma_sq)
            total += contribution
        # Rolling foothills — same peak sign, ~2.5× wider, low amplitude (matches renderer3d.js)
        sigma2 = 2.5 * radius
        sigma2_sq = 2.0 * sigma2 * sigma2
        if sigma2_sq > 0:
            total += peak * 0.22 * math.exp(-dist_sq / sigma2_sq)

    # Enhanced terrain features
    if terrain_features:
        total += _compute_river_elevation(x, y, terrain_features.get("rivers", []))
        total += _compute_coastline_elevation(x, y, terrain_features.get("coastlines", []))
        total += _compute_valley_elevation(x, y, terrain_features.get("valleys", []))
        total += _compute_plateau_elevation(x, y, terrain_features.get("plateaus", []))

    return total


def smooth_elevation_max_gradient(
    heights: dict[tuple[int, int], float],
    max_step: float,
    iterations: int,
    *,
    convergence_epsilon: float | None = None,
) -> dict[tuple[int, int], float]:
    """Reduce cliffs by bounding orthogonal slope: |h(x)-h(x')| ≤ max_step per edge.

    Iteratively splits elevation excess across each violated edge (half to each endpoint),
    preserving approximate mass and spreading a total rise of *Y* over enough tiles that
    the path length × max_step can absorb it (given enough iterations).

    Args:
        heights: tile coordinate → raw elevation (typically from ``compute_elevation``).
        max_step: maximum allowed absolute difference between 4-neighbors (world units).
        iterations: relaxation passes over all edges.
        convergence_epsilon: when > 0, stop once a full pass moves no coordinate by more
            than this amount (max-norm of per-coordinate deltas for that pass).

    Returns:
        New dict of smoothed elevations (does not mutate the input dict).
    """
    if max_step <= 0 or not heights:
        return dict(heights)
    height_by_coordinate: dict[tuple[int, int], float] = {k: float(v) for k, v in heights.items()}
    if len(height_by_coordinate) < 2:
        return height_by_coordinate

    edges: list[tuple[tuple[int, int], tuple[int, int]]] = []
    seen: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for (x, y) in height_by_coordinate:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if (nx, ny) not in height_by_coordinate:
                continue
            a, b = (x, y), (nx, ny)
            if a < b:
                edge_key = (a, b)
            else:
                edge_key = (b, a)
            if edge_key in seen:
                continue
            seen.add(edge_key)
            edges.append(edge_key)

    epsilon = float(convergence_epsilon) if convergence_epsilon is not None else 0.0
    use_early_exit = epsilon > 0.0

    for _ in range(max(1, iterations)):
        snapshot_before = {k: float(v) for k, v in height_by_coordinate.items()} if use_early_exit else None
        for ua, ub in edges:
            va = height_by_coordinate[ua]
            vb = height_by_coordinate[ub]
            if va > vb + max_step:
                excess = (va - vb - max_step) / 2.0
                height_by_coordinate[ua] = va - excess
                height_by_coordinate[ub] = vb + excess
            elif vb > va + max_step:
                excess = (vb - va - max_step) / 2.0
                height_by_coordinate[ua] = va + excess
                height_by_coordinate[ub] = vb - excess
        if use_early_exit and snapshot_before is not None:
            max_delta = 0.0
            for coord, prev_h in snapshot_before.items():
                max_delta = max(max_delta, abs(height_by_coordinate[coord] - prev_h))
            if max_delta <= epsilon:
                break

    return height_by_coordinate


def _compute_river_elevation(x: int, y: int, rivers: list[dict]) -> float:
    """Compute elevation modification from rivers (typically lower elevation)."""
    if not rivers:
        return 0.0

    min_elevation = 0.0
    for river in rivers:
        # Rivers follow paths and create valleys
        path = river.get("path", [])
        width = river.get("width", 2)
        depth = river.get("depth", 0.5)

        min_dist = float("inf")
        for pt in path:
            if isinstance(pt, dict):
                px = int(pt.get("x", 0))
                py = int(pt.get("y", 0))
            elif isinstance(pt, (list, tuple)) and len(pt) >= 2:
                px, py = int(pt[0]), int(pt[1])
            else:
                continue
            dist = math.sqrt((x - px) ** 2 + (y - py) ** 2)
            min_dist = min(min_dist, dist)

        if min_dist <= width:
            # Inside river: lower elevation
            river_depth = depth * (1 - min_dist / width)
            min_elevation = min(min_elevation, -river_depth)

    return min_elevation


def _compute_coastline_elevation(x: int, y: int, coastlines: list[dict]) -> float:
    """Compute elevation modification from coastlines (gradual slope to water)."""
    if not coastlines:
        return 0.0

    for coastline in coastlines:
        boundary_y = coastline.get("y_boundary", 0)
        slope_width = coastline.get("slope_width", 10)
        water_level = coastline.get("water_level", -0.2)

        if coastline.get("direction", "north") == "north":
            # Northern coastline: lower elevations as y decreases
            if y <= boundary_y:
                return water_level
            elif y <= boundary_y + slope_width:
                # Gradual slope up from water
                progress = (y - boundary_y) / slope_width
                return water_level + (progress * 0.3)
        else:
            # Southern coastline: lower elevations as y increases
            if y >= boundary_y:
                return water_level
            elif y >= boundary_y - slope_width:
                progress = (boundary_y - y) / slope_width
                return water_level + (progress * 0.3)

    return 0.0


def _compute_valley_elevation(x: int, y: int, valleys: list[dict]) -> float:
    """Sum Gaussian depression contributions from all valleys (deepest combined effect)."""
    if not valleys:
        return 0.0

    total_depth = 0.0
    for valley in valleys:
        cx = valley.get("cx", 0)
        cy = valley.get("cy", 0)
        length = valley.get("length", 20)
        width = valley.get("width", 5)
        depth = valley.get("depth", 0.8)

        dx = x - cx
        dy = y - cy

        angle = valley.get("angle", 0) * math.pi / 180
        rotated_dx = dx * math.cos(angle) + dy * math.sin(angle)
        rotated_dy = -dx * math.sin(angle) + dy * math.cos(angle)

        sigma_length = max(length / 3, 1e-6)
        sigma_width = max(width / 3, 1e-6)

        length_factor = math.exp(-(rotated_dx ** 2) / (2 * sigma_length ** 2))
        width_factor = math.exp(-(rotated_dy ** 2) / (2 * sigma_width ** 2))

        total_depth += -depth * length_factor * width_factor

    return total_depth


def _compute_plateau_elevation(x: int, y: int, plateaus: list[dict]) -> float:
    """Compute elevation modification from plateaus (flat elevated areas)."""
    if not plateaus:
        return 0.0

    for plateau in plateaus:
        cx = plateau.get("cx", 0)
        cy = plateau.get("cy", 0)
        width = plateau.get("width", 10)
        height = plateau.get("height", 10)
        elevation = plateau.get("elevation", 0.5)

        # Simple box plateau
        if abs(x - cx) <= width / 2 and abs(y - cy) <= height / 2:
            return elevation

    return 0.0
