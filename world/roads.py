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


def _widen_line(points: list[tuple[int, int]], width: int) -> list[tuple[int, int]]:
    """Expand a line of points to the given width by adding adjacent tiles.

    width=1: just the center line
    width=2: center line + one tile to each side (perpendicular to line direction)
    """
    if width <= 1:
        return points

    expanded = set(points)
    half = width // 2
    for x, y in points:
        for dx in range(-half, half + 1):
            for dy in range(-half, half + 1):
                if dx == 0 and dy == 0:
                    continue
                expanded.add((x + dx, y + dy))
    return list(expanded)


def rasterize_road(world: WorldState, road_dict: dict, blueprint: CityBlueprint | None = None) -> int:
    """Place road tiles along a road's waypoints using Bresenham line drawing.

    road_dict: {name, type, points:[(x,y),...], width}
    Returns number of tiles placed.
    """
    name = road_dict.get("name", "road")
    road_type = road_dict.get("type", "vicus")
    raw_points = road_dict.get("points", [])
    width = road_dict.get("width", 1)

    if not raw_points or len(raw_points) < 2:
        return 0

    # Convert points to integer tuples
    waypoints: list[tuple[int, int]] = []
    for p in raw_points:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            waypoints.append((int(p[0]), int(p[1])))
        elif isinstance(p, dict):
            waypoints.append((int(p.get("x", 0)), int(p.get("y", 0))))

    if len(waypoints) < 2:
        return 0

    # Draw lines between consecutive waypoints
    all_line_points: list[tuple[int, int]] = []
    for i in range(len(waypoints) - 1):
        segment = bresenham_line(waypoints[i][0], waypoints[i][1],
                                  waypoints[i + 1][0], waypoints[i + 1][1])
        all_line_points.extend(segment)

    # Deduplicate while preserving order
    seen = set()
    unique_points = []
    for p in all_line_points:
        if p not in seen:
            seen.add(p)
            unique_points.append(p)

    # Widen for via-class roads
    if width > 1:
        road_tiles = _widen_line(unique_points, width)
    else:
        road_tiles = unique_points

    # Road surface color by type
    road_colors = {
        "via": "#A0907C",      # Major paved road - light stone
        "vicus": "#908070",    # Secondary street - darker
        "semita": "#786858",   # Narrow path - earthy
    }
    color = road_colors.get(road_type, "#808080")

    count = 0
    for x, y in road_tiles:
        # Don't overwrite existing non-empty, non-road tiles
        existing = world.get_tile(x, y)
        if existing and existing.terrain not in ("empty", "road"):
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
        world.place_tile(x, y, tile_data)
        count += 1

    return count


def compute_elevation(hills: list[dict], x: int, y: int) -> float:
    """Compute elevation at a point from hills data.

    Uses gaussian falloff: elev = sum(peak * exp(-dist^2 / (2 * radius^2)))
    """
    if not hills:
        return 0.0

    total = 0.0
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

    return total
