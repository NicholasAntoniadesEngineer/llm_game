"""Road rasterization and elevation computation for city blueprints."""

from __future__ import annotations

import logging
import math
import random
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
) -> dict[tuple[int, int], float]:
    """Reduce cliffs by bounding orthogonal slope: |h(x)-h(x')| ≤ max_step per edge.

    Iteratively splits elevation excess across each violated edge (half to each endpoint),
    preserving approximate mass and spreading a total rise of *Y* over enough tiles that
    the path length × max_step can absorb it (given enough iterations).

    Args:
        heights: tile coordinate → raw elevation (typically from ``compute_elevation``).
        max_step: maximum allowed absolute difference between 4-neighbors (world units).
        iterations: relaxation passes over all edges.

    Returns:
        New dict of smoothed elevations (does not mutate the input dict).
    """
    if max_step <= 0 or not heights:
        return dict(heights)
    h: dict[tuple[int, int], float] = {k: float(v) for k, v in heights.items()}
    if len(h) < 2:
        return h

    edges: list[tuple[tuple[int, int], tuple[int, int]]] = []
    seen: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for (x, y) in h:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if (nx, ny) not in h:
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

    for _ in range(max(1, iterations)):
        for ua, ub in edges:
            va = h[ua]
            vb = h[ub]
            if va > vb + max_step:
                excess = (va - vb - max_step) / 2.0
                h[ua] = va - excess
                h[ub] = vb + excess
            elif vb > va + max_step:
                excess = (vb - va - max_step) / 2.0
                h[ua] = va + excess
                h[ub] = vb - excess

    return h


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

        min_dist = float('inf')
        for px, py in path:
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
    """Compute elevation modification from valleys (low areas between hills)."""
    if not valleys:
        return 0.0

    for valley in valleys:
        cx = valley.get("cx", 0)
        cy = valley.get("cy", 0)
        length = valley.get("length", 20)
        width = valley.get("width", 5)
        depth = valley.get("depth", 0.8)

        # Valley as an elongated gaussian depression
        dx = x - cx
        dy = y - cy

        # Rotate valley if it has an angle
        angle = valley.get("angle", 0) * math.pi / 180
        rotated_dx = dx * math.cos(angle) + dy * math.sin(angle)
        rotated_dy = -dx * math.sin(angle) + dy * math.cos(angle)

        # Elongated gaussian: wider in length direction, narrower in width
        sigma_length = length / 3
        sigma_width = width / 3

        length_factor = math.exp(-(rotated_dx ** 2) / (2 * sigma_length ** 2))
        width_factor = math.exp(-(rotated_dy ** 2) / (2 * sigma_width ** 2))

        valley_depth = -depth * length_factor * width_factor
        return valley_depth

    return 0.0


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


def generate_terrain_features(city_size: tuple[int, int], culture: str = "roman") -> dict:
    """Generate procedural terrain features based on city size and culture.

    Args:
        city_size: (width, height) of the city area
        culture: Cultural context for terrain generation

    Returns:
        Dictionary of terrain features for elevation computation
    """
    width, height = city_size
    center_x, center_y = width // 2, height // 2

    features = {
        "rivers": [],
        "coastlines": [],
        "valleys": [],
        "plateaus": []
    }

    # Generate rivers based on culture and geography
    if culture in ("roman", "greek"):
        # Mediterranean cultures often have rivers
        if width > 50:  # Only add rivers to larger cities
            num_rivers = random.randint(1, 3)
            for _ in range(num_rivers):
                # River starting from edge, flowing toward center
                start_side = random.choice(["north", "south", "east", "west"])
                if start_side == "north":
                    start_x = random.randint(0, width - 1)
                    start_y = 0
                    end_x = center_x + random.randint(-20, 20)
                    end_y = height - 1
                elif start_side == "south":
                    start_x = random.randint(0, width - 1)
                    start_y = height - 1
                    end_x = center_x + random.randint(-20, 20)
                    end_y = 0
                elif start_side == "east":
                    start_x = width - 1
                    start_y = random.randint(0, height - 1)
                    end_x = 0
                    end_y = center_y + random.randint(-20, 20)
                else:  # west
                    start_x = 0
                    start_y = random.randint(0, height - 1)
                    end_x = width - 1
                    end_y = center_y + random.randint(-20, 20)

                # Create river path
                path = bresenham_line(start_x, start_y, end_x, end_y)
                features["rivers"].append({
                    "path": path,
                    "width": random.randint(2, 4),
                    "depth": random.uniform(0.3, 0.8)
                })

    # Generate coastlines for coastal cities
    coastal_cultures = ("greek", "roman", "caral", "olmec")
    if culture in coastal_cultures and random.random() < 0.4:
        # 40% chance of coastline
        boundary_y = random.choice([height // 4, 3 * height // 4])
        direction = "north" if boundary_y < height // 2 else "south"
        features["coastlines"].append({
            "y_boundary": boundary_y,
            "slope_width": random.randint(8, 15),
            "water_level": -0.2,
            "direction": direction
        })

    # Generate valleys between hills
    if width > 40:
        num_valleys = random.randint(0, 2)
        for _ in range(num_valleys):
            features["valleys"].append({
                "cx": random.randint(10, width - 10),
                "cy": random.randint(10, height - 10),
                "length": random.randint(15, 30),
                "width": random.randint(3, 8),
                "depth": random.uniform(0.4, 0.9),
                "angle": random.randint(0, 180)
            })

    # Generate plateaus for elevated ceremonial areas
    ceremonial_cultures = ("mesoamerican", "andean", "egyptian")
    if culture in ceremonial_cultures and random.random() < 0.6:
        num_plateaus = random.randint(1, 3)
        for _ in range(num_plateaus):
            features["plateaus"].append({
                "cx": random.randint(20, width - 20),
                "cy": random.randint(20, height - 20),
                "width": random.randint(8, 15),
                "height": random.randint(8, 15),
                "elevation": random.uniform(0.3, 0.7)
            })

    return features
