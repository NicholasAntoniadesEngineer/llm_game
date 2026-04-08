"""Spatial layout utilities -- spacing enforcement, neighbor context, occupancy summaries."""

import logging

from core.config import GRID_WIDTH, GRID_HEIGHT

logger = logging.getLogger("eternal.spatial")

# District style -> spacing multiplier.
# Wealthy/garden districts get more space; dense commercial districts pack tighter.
_DISTRICT_SPACING: dict[str, int] = {
    "monumental": 2,   # temples, forums: generous gardens/courtyards
    "garden": 2,        # parks, villas: wide spacing
    "residential": 1,   # standard separation
    "military": 1,      # barracks: functional spacing
    "commercial": 0,    # markets, shops: shared walls allowed
}


def get_district_spacing(district_style: str | None = None) -> int:
    """Return the min_gap for a given district style.

    Args:
        district_style: Style string (e.g., 'monumental', 'commercial').

    Returns:
        Integer gap in tiles (0 = shared walls, 1 = alley, 2 = courtyard).
    """
    if not district_style:
        return 1
    return _DISTRICT_SPACING.get(district_style.lower(), 1)


def enforce_spacing(master_plan: list, min_gap: int = 1) -> list:
    """Shift buildings that touch or overlap to create gaps between them."""
    if not master_plan:
        return master_plan

    # Collect all occupied tiles per building (as sets for fast lookup)
    bldg_tiles = []
    for struct in master_plan:
        tiles = struct.get("tiles", [])
        tile_set = set()
        for t in tiles:
            try:
                x = int(t["x"])
                y = int(t["y"])
            except (KeyError, TypeError, ValueError):
                continue
            tile_set.add((x, y))
        bldg_tiles.append(tile_set)

    # Build a buffer zone around each building (tiles within min_gap)
    def get_buffer(tile_set):
        buf = set()
        for (x, y) in tile_set:
            for dx in range(-min_gap, min_gap + 1):
                for dy in range(-min_gap, min_gap + 1):
                    if dx == 0 and dy == 0:
                        continue
                    buf.add((x + dx, y + dy))
        return buf - tile_set  # exclude the building's own tiles

    # Check each building against all previous ones and shift if needed
    for i in range(1, len(master_plan)):
        occupied = set()
        for j in range(i):
            occupied |= bldg_tiles[j]
            occupied |= get_buffer(bldg_tiles[j])

        # Check if current building overlaps with occupied + buffer zone
        overlap = bldg_tiles[i] & occupied
        if not overlap:
            continue

        # Find shift direction -- try right, down, right+down, left, up, and diagonals
        tiles = master_plan[i].get("tiles", [])
        best_shift = None
        step = min_gap + 1
        shift_candidates = [
            (step, 0), (0, step), (step, step),
            (-step, 0), (0, -step), (-step, step), (step, -step), (-step, -step),
        ]
        for sx, sy in shift_candidates:
            shifted = set()
            in_bounds = True
            for t in tiles:
                try:
                    nx, ny = int(t["x"]) + sx, int(t["y"]) + sy
                except (KeyError, TypeError, ValueError):
                    continue
                if not (0 <= nx < GRID_WIDTH and 0 <= ny < GRID_HEIGHT):
                    in_bounds = False
                    break
                shifted.add((nx, ny))
            if in_bounds and shifted and not (shifted & occupied):
                best_shift = (sx, sy)
                break

        if best_shift:
            sx, sy = best_shift
            logger.info(f"Spacing fix: shifting '{master_plan[i].get('name')}' by ({sx},{sy})")
            for t in tiles:
                try:
                    t["x"] = int(t["x"]) + sx
                    t["y"] = int(t["y"]) + sy
                except (KeyError, TypeError, ValueError):
                    continue
            bldg_tiles[i] = set()
            for t in tiles:
                try:
                    bldg_tiles[i].add((int(t["x"]), int(t["y"])))
                except (KeyError, TypeError, ValueError):
                    continue

    return master_plan


def occupancy_summary_for_survey(master_plan: list) -> str:
    """Build a text summary of occupied tiles for multi-chunk survey context."""
    count = 0
    for struct in master_plan:
        count += len(struct.get("tiles") or [])
    if count == 0:
        return "None yet."
    names = [s.get("name", "?") for s in master_plan[:10]]
    extra = len(master_plan) - len(names)
    name_str = ", ".join(names)
    if extra > 0:
        name_str += f", +{extra} more"
    return f"{count} tiles placed across {len(master_plan)} structures ({name_str})."
