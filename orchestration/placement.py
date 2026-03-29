"""Functional placement analysis on survey master_plan — warnings only (no auto-rewrite)."""

from __future__ import annotations

import logging

logger = logging.getLogger("roma.placement")

# Types that usually need direct adjacency to a road tile (cardinal).
COMMERCIAL_TYPES = frozenset({"taberna", "market", "warehouse"})

# Types that should touch water (harbor / crossing) — cardinal adjacency to water tiles.
WATER_ADJACENT_TYPES = frozenset({"bridge"})

# Major ceremonial / public facades — soft check: should face road OR touch open civic terrain.
CEREMONIAL_APPROACH_TYPES = frozenset({"temple", "monument", "basilica"})

OPEN_APPROACH_TERRAIN = frozenset({"forum", "grass", "garden"})


def _footprint(struct: dict) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for t in struct.get("tiles") or []:
        if not isinstance(t, dict):
            continue
        try:
            x, y = int(t["x"]), int(t["y"])
        except (KeyError, TypeError, ValueError):
            continue
        out.add((x, y))
    return out


def _collect_tiles_by_building_type(master_plan: list) -> dict[str, set[tuple[int, int]]]:
    by_bt: dict[str, set[tuple[int, int]]] = {}
    for struct in master_plan:
        if not isinstance(struct, dict):
            continue
        bt = (struct.get("building_type") or "").lower() or "unknown"
        fp = _footprint(struct)
        if not fp:
            continue
        by_bt.setdefault(bt, set()).update(fp)
    return by_bt


def _cardinally_adjacent_to_set(footprint: set[tuple[int, int]], target: set[tuple[int, int]]) -> bool:
    if not footprint or not target:
        return False
    for x, y in footprint:
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (nx, ny) in target:
                return True
    return False


def _manhattan_within(tiles: set[tuple[int, int]], origin_sets: list[set[tuple[int, int]]], max_dist: int) -> bool:
    """True if any tile in `tiles` is within Manhattan distance `max_dist` of any tile in union of origin_sets."""
    union: set[tuple[int, int]] = set()
    for s in origin_sets:
        union |= s
    if not union:
        return False
    for x, y in tiles:
        for ox, oy in union:
            if abs(x - ox) + abs(y - oy) <= max_dist:
                return True
    return False


def check_functional_placement(master_plan: list) -> list[str]:
    """
    Return human-readable warnings when common functional rules are violated.
    Does not modify the plan — surveyor/Cartographus should fix in a future pass or ignore if historically justified.
    """
    if not isinstance(master_plan, list):
        return []

    by_type = _collect_tiles_by_building_type(master_plan)
    road_tiles = by_type.get("road", set())
    water_tiles = by_type.get("water", set())
    open_tiles = set()
    for bt in OPEN_APPROACH_TERRAIN:
        open_tiles |= by_type.get(bt, set())

    warnings: list[str] = []

    for struct in master_plan:
        if not isinstance(struct, dict):
            continue
        name = struct.get("name", "?")
        bt = (struct.get("building_type") or "").lower()
        fp = _footprint(struct)
        if not fp:
            continue

        if bt in COMMERCIAL_TYPES:
            if road_tiles and not _cardinally_adjacent_to_set(fp, road_tiles):
                warnings.append(
                    f"{name} ({bt}): no cardinal adjacency to a road tile — shops and warehouses usually need street frontage."
                )
            elif not road_tiles:
                warnings.append(
                    f"{name} ({bt}): master plan has no road tiles — cannot verify street access."
                )

        if bt in WATER_ADJACENT_TYPES:
            if water_tiles and not _cardinally_adjacent_to_set(fp, water_tiles):
                warnings.append(
                    f"{name} ({bt}): not cardinally adjacent to water — bridges normally span or touch water."
                )

        if bt in CEREMONIAL_APPROACH_TYPES and road_tiles:
            touches_road = _cardinally_adjacent_to_set(fp, road_tiles)
            touches_open = _cardinally_adjacent_to_set(fp, open_tiles) if open_tiles else False
            if not touches_road and not touches_open:
                near_road = _manhattan_within(fp, [road_tiles], 3)
                if not near_road:
                    warnings.append(
                        f"{name} ({bt}): no road or open plaza (forum/grass/garden) frontage within 3 tiles — "
                        "major civic or sacred buildings usually had a public approach."
                    )

    return warnings


def log_functional_placement_warnings(master_plan: list, context: str) -> None:
    for w in check_functional_placement(master_plan):
        logger.warning("Functional placement [%s]: %s", context, w)
