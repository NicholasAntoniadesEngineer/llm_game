"""Pure road graph helpers for master plans (no BuildEngine dependency)."""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

logger = logging.getLogger("eternal.road_connectivity")


def _ensure_boundary_road_in_master_plan(
    master_plan: list[dict[str, Any]],
    road_coords: set[tuple[int, int]],
    non_road_coords: set[tuple[int, int]],
    region: dict[str, Any],
    *,
    world_grid_width_tiles: int,
    world_grid_height_tiles: int,
    road_bridge_default_elevation: float,
) -> None:
    """Ensure at least one road tile touches the region boundary for inter-district links."""
    x1 = region.get("x1", 0)
    y1 = region.get("y1", 0)
    x2 = region.get("x2", world_grid_width_tiles - 1)
    y2 = region.get("y2", world_grid_height_tiles - 1)

    for rx, ry in road_coords:
        if rx == x1 or rx == x2 or ry == y1 or ry == y2:
            return

    best_road: tuple[int, int] | None = None
    best_dist = float("inf")
    best_edge_pos: tuple[int, int] | None = None
    for rx, ry in road_coords:
        for edge_val, axis in [(x1, "x_min"), (x2, "x_max"), (y1, "y_min"), (y2, "y_max")]:
            if axis.startswith("x"):
                distance = abs(rx - edge_val)
                target = (edge_val, ry)
            else:
                distance = abs(ry - edge_val)
                target = (rx, edge_val)
            if distance < best_dist:
                best_dist = distance
                best_road = (rx, ry)
                best_edge_pos = target

    if best_road is None or best_edge_pos is None or best_dist == 0:
        return

    edge_tiles: list[dict[str, Any]] = []
    x, y = best_road
    tx, ty = best_edge_pos
    while x != tx:
        x += 1 if tx > x else -1
        if (x, y) not in road_coords and (x, y) not in non_road_coords:
            edge_tiles.append({"x": x, "y": y, "elevation": road_bridge_default_elevation})
    while y != ty:
        y += 1 if ty > y else -1
        if (x, y) not in road_coords and (x, y) not in non_road_coords:
            edge_tiles.append({"x": x, "y": y, "elevation": road_bridge_default_elevation})

    if edge_tiles:
        master_plan.append(
            {
                "name": "District edge road",
                "building_type": "road",
                "tiles": edge_tiles,
                "description": "Road extending to district boundary for inter-district connectivity.",
            }
        )
        logger.info("Boundary road: added %d tiles connecting to district edge", len(edge_tiles))


def ensure_road_connectivity_in_master_plan(
    master_plan: list[dict[str, Any]],
    region: dict[str, Any],
    *,
    road_bridge_default_elevation: float,
    world_grid_width_tiles: int,
    world_grid_height_tiles: int,
) -> list[dict[str, Any]]:
    """Ensure road tiles form a connected graph; add bridging tiles if needed.

    Modifies ``master_plan`` in-place and returns it.
    """
    road_coords: set[tuple[int, int]] = set()
    non_road_coords: set[tuple[int, int]] = set()
    for struct in master_plan:
        btype = struct.get("building_type", "")
        for t in struct.get("tiles", []):
            try:
                x, y = int(t["x"]), int(t["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if btype == "road":
                road_coords.add((x, y))
            else:
                non_road_coords.add((x, y))

    if len(road_coords) < 2:
        return master_plan

    visited: set[tuple[int, int]] = set()
    components: list[set[tuple[int, int]]] = []
    for start in road_coords:
        if start in visited:
            continue
        comp: set[tuple[int, int]] = set()
        queue = deque([start])
        while queue:
            cx, cy = queue.popleft()
            if (cx, cy) in visited:
                continue
            visited.add((cx, cy))
            comp.add((cx, cy))
            for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                nb = (cx + dx, cy + dy)
                if nb in road_coords and nb not in visited:
                    queue.append(nb)
        if comp:
            components.append(comp)

    if len(components) <= 1:
        _ensure_boundary_road_in_master_plan(
            master_plan,
            road_coords,
            non_road_coords,
            region,
            world_grid_width_tiles=world_grid_width_tiles,
            world_grid_height_tiles=world_grid_height_tiles,
            road_bridge_default_elevation=road_bridge_default_elevation,
        )
        return master_plan

    bridge_tiles: list[dict[str, Any]] = []
    components.sort(key=len, reverse=True)
    trunk = set(components[0])
    for comp in components[1:]:
        best_dist = float("inf")
        best_pair: tuple[tuple[int, int], tuple[int, int]] | None = None
        for tx, ty in trunk:
            for cx, cy in comp:
                manhattan = abs(tx - cx) + abs(ty - cy)
                if manhattan < best_dist:
                    best_dist = manhattan
                    best_pair = ((tx, ty), (cx, cy))
        if best_pair is None:
            continue
        (ax, ay), (bx, by) = best_pair
        x, y = ax, ay
        while x != bx:
            x += 1 if bx > x else -1
            pos = (x, y)
            if pos not in road_coords and pos not in non_road_coords:
                bridge_tiles.append({"x": x, "y": y, "elevation": road_bridge_default_elevation})
                road_coords.add(pos)
        while y != by:
            y += 1 if by > y else -1
            pos = (x, y)
            if pos not in road_coords and pos not in non_road_coords:
                bridge_tiles.append({"x": x, "y": y, "elevation": road_bridge_default_elevation})
                road_coords.add(pos)
        trunk |= comp

    if bridge_tiles:
        master_plan.append(
            {
                "name": "Connecting road",
                "building_type": "road",
                "tiles": bridge_tiles,
                "description": "Road segment connecting isolated street sections.",
            }
        )
        logger.info(
            "Road connectivity: added %d bridge tiles across %d isolated segments",
            len(bridge_tiles),
            len(components) - 1,
        )

    _ensure_boundary_road_in_master_plan(
        master_plan,
        road_coords,
        non_road_coords,
        region,
        world_grid_width_tiles=world_grid_width_tiles,
        world_grid_height_tiles=world_grid_height_tiles,
        road_bridge_default_elevation=road_bridge_default_elevation,
    )
    return master_plan
