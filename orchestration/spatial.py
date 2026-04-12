"""Spatial layout utilities -- spacing enforcement, neighbor context, occupancy summaries."""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import Config

logger = logging.getLogger("eternal.spatial")


def get_district_spacing(district_style: str | None, *, system_configuration: "Config") -> int:
    """Return the min_gap for a given district style.

    Args:
        district_style: Style string (e.g., 'monumental', 'commercial').

    Returns:
        Integer gap in tiles (0 = shared walls, 1 = alley, 2 = courtyard).
    """
    if not district_style:
        return 1
    spacing_map = system_configuration.district_spacing_by_style_dictionary
    return int(spacing_map.get(district_style.lower(), 1))


def enforce_spacing(
    master_plan: list,
    min_gap: int = 1,
    *,
    system_configuration: "Config",
    world_grid_width_tiles: int | None = None,
    world_grid_height_tiles: int | None = None,
    spatial_optimal_shift_step_tiles: int | None = None,
) -> list:
    """Shift buildings that touch or overlap to create gaps between them.

    Enhanced with intelligent placement that considers building relationships,
    district coherence, and functional requirements.
    """
    if not master_plan:
        return master_plan

    if world_grid_width_tiles is None:
        world_grid_width_tiles = system_configuration.grid.world_grid_width
    if world_grid_height_tiles is None:
        world_grid_height_tiles = system_configuration.grid.world_grid_height
    if spatial_optimal_shift_step_tiles is None:
        spatial_optimal_shift_step_tiles = system_configuration.spatial_optimal_shift_step_tiles

    # Collect all occupied tiles per building (as sets for fast lookup)
    bldg_tiles = []
    bldg_metadata = []
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
        bldg_metadata.append({
            "name": struct.get("name", ""),
            "type": struct.get("building_type", ""),
            "district": struct.get("district", ""),
            "priority": _get_placement_priority(struct),
            "size": len(tile_set)
        })

    # Build a buffer zone around each building (tiles within min_gap)
    def get_buffer(tile_set, gap):
        buf = set()
        for (x, y) in tile_set:
            for dx in range(-gap, gap + 1):
                for dy in range(-gap, gap + 1):
                    if dx == 0 and dy == 0:
                        continue
                    buf.add((x + dx, y + dy))
        return buf - tile_set  # exclude the building's own tiles

    # Enhanced spacing with relationship awareness
    for i in range(1, len(master_plan)):
        occupied = set()
        relationship_zones = {}  # Track special relationship requirements

        for j in range(i):
            occupied |= bldg_tiles[j]
            # Dynamic gap based on building relationships and district style
            gap = _calculate_dynamic_gap(bldg_metadata[i], bldg_metadata[j], min_gap)
            occupied |= get_buffer(bldg_tiles[j], gap)

            # Track relationship zones for functional requirements
            _update_relationship_zones(relationship_zones, bldg_metadata[j], bldg_tiles[j])

        # Check if current building overlaps with occupied + buffer zone
        overlap = bldg_tiles[i] & occupied
        if not overlap:
            # Even if no overlap, check functional relationship requirements
            _enforce_functional_relationships(master_plan[i], relationship_zones, bldg_tiles, i)
            continue

        # Find optimal shift direction considering multiple factors
        tiles = master_plan[i].get("tiles", [])
        best_shift = _find_optimal_shift(
            tiles,
            occupied,
            relationship_zones,
            bldg_metadata[i],
            world_grid_width_tiles=world_grid_width_tiles,
            world_grid_height_tiles=world_grid_height_tiles,
            spatial_optimal_shift_step_tiles=spatial_optimal_shift_step_tiles,
        )

        if best_shift:
            sx, sy = best_shift
            logger.info(f"Spacing fix: shifting '{master_plan[i].get('name')}' by ({sx},{sy}) - {bldg_metadata[i]['type']}")
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


def _get_placement_priority(struct: dict) -> int:
    """Get placement priority for a building (higher = place first)."""
    btype = struct.get("building_type", "").lower()
    priority_map = {
        "temple": 10, "monument": 9, "basilica": 8, "forum": 7,
        "thermae": 6, "amphitheater": 6, "aqueduct": 5,
        "market": 4, "taberna": 3, "warehouse": 3,
        "insula": 2, "domus": 1
    }
    return priority_map.get(btype, 0)


def _calculate_dynamic_gap(meta1: dict, meta2: dict, base_gap: int) -> int:
    """Calculate dynamic spacing based on building relationships."""
    # Same district buildings can be closer
    if meta1.get("district") == meta2.get("district"):
        return max(1, base_gap - 1)

    # Commercial buildings need more space for access
    commercial_types = {"market", "taberna", "warehouse"}
    if meta1["type"] in commercial_types or meta2["type"] in commercial_types:
        return base_gap + 1

    # Monumental buildings need generous spacing
    monumental_types = {"temple", "monument", "basilica"}
    if meta1["type"] in monumental_types or meta2["type"] in monumental_types:
        return base_gap + 2

    return base_gap


def _update_relationship_zones(zones: dict, meta: dict, tiles: set) -> None:
    """Update relationship zones for functional requirements."""
    btype = meta["type"]

    # Road-adjacent buildings create road zones
    if btype in ("road", "via", "vicus"):
        zones["roads"] = zones.get("roads", set()) | tiles

    # Civic buildings create public zones
    if btype in ("forum", "basilica", "temple"):
        zones["civic"] = zones.get("civic", set()) | tiles

    # Commercial buildings create market zones
    if btype in ("market", "taberna"):
        zones["commercial"] = zones.get("commercial", set()) | tiles


def _enforce_functional_relationships(struct: dict, zones: dict, all_tiles: list, idx: int) -> None:
    """Enforce functional relationships even when no spatial conflict exists."""
    btype = struct.get("building_type", "").lower()
    tiles = all_tiles[idx]

    # Commercial buildings should be near roads
    if btype in ("taberna", "market", "warehouse"):
        roads = zones.get("roads", set())
        if roads and not _is_near_tiles(tiles, roads, 3):
            logger.warning(f"{struct.get('name')} ({btype}): should be near roads for accessibility")

    # Civic buildings should be near other civic buildings
    if btype in ("temple", "basilica", "monument"):
        civic = zones.get("civic", set())
        if civic and not _is_near_tiles(tiles, civic, 5):
            logger.warning(f"{struct.get('name')} ({btype}): isolated from civic center")


def _find_optimal_shift(
    tiles: list,
    occupied: set,
    zones: dict,
    meta: dict,
    *,
    world_grid_width_tiles: int,
    world_grid_height_tiles: int,
    spatial_optimal_shift_step_tiles: int,
) -> tuple[int, int] | None:
    """Find the best shift direction considering multiple factors."""
    step = spatial_optimal_shift_step_tiles
    candidates = [
        (step, 0), (0, step), (step, step),
        (-step, 0), (0, -step), (-step, step), (step, -step), (-step, -step),
        (step*2, 0), (0, step*2), (step*2, step*2)  # Larger shifts if needed
    ]

    best_shift = None
    best_score = -1

    for sx, sy in candidates:
        shifted = set()
        in_bounds = True

        for t in tiles:
            try:
                nx, ny = int(t["x"]) + sx, int(t["y"]) + sy
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= nx < world_grid_width_tiles and 0 <= ny < world_grid_height_tiles):
                in_bounds = False
                break
            shifted.add((nx, ny))

        if not in_bounds or not shifted or (shifted & occupied):
            continue

        # Score this shift based on functional relationships
        score = _score_shift(shifted, zones, meta)
        if score > best_score:
            best_score = score
            best_shift = (sx, sy)

    return best_shift


def _score_shift(tiles: set, zones: dict, meta: dict) -> int:
    """Score a potential shift based on functional relationships."""
    score = 0
    btype = meta["type"]

    # Commercial buildings get bonus for road proximity
    if btype in ("taberna", "market", "warehouse"):
        roads = zones.get("roads", set())
        if _is_near_tiles(tiles, roads, 2):
            score += 3

    # Civic buildings get bonus for being near other civic buildings
    if btype in ("temple", "basilica", "monument"):
        civic = zones.get("civic", set())
        if _is_near_tiles(tiles, civic, 4):
            score += 2

    # Residential buildings get bonus for being near commercial areas
    if btype in ("insula", "domus"):
        commercial = zones.get("commercial", set())
        if _is_near_tiles(tiles, commercial, 3):
            score += 1

    return score


def _is_near_tiles(tiles1: set, tiles2: set, max_dist: int) -> bool:
    """Check if any tile in tiles1 is within max_dist of any tile in tiles2."""
    for x1, y1 in tiles1:
        for x2, y2 in tiles2:
            if abs(x1 - x2) + abs(y1 - y2) <= max_dist:
                return True
    return False


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
