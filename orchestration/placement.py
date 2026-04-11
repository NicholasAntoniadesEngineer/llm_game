"""Functional placement analysis on survey master_plan — warnings only (no auto-rewrite)."""

from __future__ import annotations

import logging

logger = logging.getLogger("eternal.placement")

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


def suggest_road_improvements(master_plan: list) -> list[str]:
    """Suggest road network improvements based on building placement patterns.

    Analyzes traffic flow, connectivity, and accessibility to recommend
    new roads or road upgrades.
    """
    suggestions = []

    # Collect building and road data
    buildings_by_type = _collect_tiles_by_building_type(master_plan)
    road_tiles = buildings_by_type.get("road", set())

    if not road_tiles:
        suggestions.append("No road network found - consider adding primary roads first")
        return suggestions

    # Analyze connectivity clusters
    building_clusters = _find_building_clusters(master_plan)

    # Check for isolated clusters
    connected_clusters = _find_connected_clusters(building_clusters, road_tiles)

    if len(connected_clusters) > 1:
        suggestions.append(f"Found {len(connected_clusters)} disconnected building clusters - consider adding connecting roads")

        # Suggest specific connection points
        for i, cluster1 in enumerate(connected_clusters):
            for j, cluster2 in enumerate(connected_clusters[i+1:], i+1):
                connection = _suggest_cluster_connection(cluster1, cluster2)
                if connection:
                    suggestions.append(f"Connect cluster {i+1} to cluster {j+1} via {connection}")

    # Check commercial area accessibility
    commercial_buildings = set()
    for btype in ("market", "taberna", "warehouse"):
        commercial_buildings.update(buildings_by_type.get(btype, set()))

    if commercial_buildings:
        poorly_connected_commercial = _find_poorly_connected_buildings(
            commercial_buildings, road_tiles, max_distance=4
        )
        if poorly_connected_commercial:
            suggestions.append(f"{len(poorly_connected_commercial)} commercial buildings are poorly connected to roads - improve accessibility")

    # Check civic building prominence
    civic_buildings = set()
    for btype in ("temple", "basilica", "forum", "monument"):
        civic_buildings.update(buildings_by_type.get(btype, set()))

    if civic_buildings:
        isolated_civic = _find_poorly_connected_buildings(
            civic_buildings, road_tiles, max_distance=6
        )
        if isolated_civic:
            suggestions.append(f"{len(isolated_civic)} civic buildings lack prominent road access - consider processional routes")

    # Suggest road hierarchy improvements
    road_analysis = _analyze_road_hierarchy(master_plan)
    if road_analysis["needs_primary_roads"]:
        suggestions.append("Add primary roads (via) for major traffic arteries")
    if road_analysis["needs_secondary_roads"]:
        suggestions.append("Add secondary roads (vicus) for neighborhood connections")
    if road_analysis["overloaded_segments"]:
        suggestions.append("Some road segments are overloaded - consider widening or adding parallel routes")

    return suggestions


def _find_building_clusters(master_plan: list) -> list[set]:
    """Find clusters of buildings that are spatially grouped."""
    clusters = []

    # Simple clustering: buildings within 10 tiles of each other
    processed = set()

    for struct in master_plan:
        if struct.get("name") in processed:
            continue

        building_tiles = _footprint(struct)
        if not building_tiles:
            continue

        cluster = building_tiles.copy()
        cluster_buildings = {struct.get("name")}

        # Expand cluster by finding nearby buildings
        changed = True
        while changed:
            changed = False
            for other_struct in master_plan:
                if other_struct.get("name") in processed or other_struct.get("name") in cluster_buildings:
                    continue

                other_tiles = _footprint(other_struct)
                if _buildings_are_near(cluster, other_tiles, max_distance=10):
                    cluster.update(other_tiles)
                    cluster_buildings.add(other_struct.get("name"))
                    changed = True

        if len(cluster_buildings) > 1:  # Only count clusters with multiple buildings
            clusters.append(cluster)
            processed.update(cluster_buildings)

    return clusters


def _find_connected_clusters(clusters: list[set], road_tiles: set) -> list[set]:
    """Find which building clusters are connected via roads."""
    connected_clusters = []

    for cluster in clusters:
        # Check if cluster has road access
        has_road_access = any(
            _cardinally_adjacent_to_set({tile}, road_tiles)
            for tile in cluster
        )

        if has_road_access:
            # Find all clusters connected through this one
            connected_group = {frozenset(cluster)}

            # Simple connectivity: clusters that share road access
            for other_cluster in clusters:
                if other_cluster == cluster:
                    continue

                other_has_access = any(
                    _cardinally_adjacent_to_set({tile}, road_tiles)
                    for tile in other_cluster
                )

                if other_has_access:
                    # Check if they could be connected (simplified)
                    min_distance = min(
                        abs(tx - ox) + abs(ty - oy)
                        for tx, ty in cluster
                        for ox, oy in other_cluster
                    )
                    if min_distance < 20:  # Within reasonable connection distance
                        connected_group.add(frozenset(other_cluster))

            # Merge connected clusters
            merged_cluster = set()
            for c in connected_group:
                merged_cluster.update(c)
            connected_clusters.append(merged_cluster)

    return connected_clusters


def _suggest_cluster_connection(cluster1: set, cluster2: set) -> str | None:
    """Suggest how to connect two building clusters."""
    # Find closest points between clusters
    min_distance = float('inf')
    best_points = None

    for x1, y1 in cluster1:
        for x2, y2 in cluster2:
            distance = abs(x1 - x2) + abs(y1 - y2)
            if distance < min_distance:
                min_distance = distance
                best_points = ((x1, y1), (x2, y2))

    if best_points and min_distance > 5:
        (x1, y1), (x2, y2) = best_points
        return f"road from ({x1},{y1}) to ({x2},{y2})"
    elif best_points:
        (x1, y1), (x2, y2) = best_points
        return f"path or alley from ({x1},{y1}) to ({x2},{y2})"

    return None


def _find_poorly_connected_buildings(building_tiles: set, road_tiles: set, max_distance: int) -> set:
    """Find buildings that are poorly connected to the road network."""
    poorly_connected = set()

    for building_tile in building_tiles:
        # Check if building has road access within max_distance
        has_access = False
        bx, by = building_tile

        # Check tiles within max_distance
        for dx in range(-max_distance, max_distance + 1):
            for dy in range(-max_distance, max_distance + 1):
                if abs(dx) + abs(dy) > max_distance:
                    continue

                check_x, check_y = bx + dx, by + dy
                if (check_x, check_y) in road_tiles:
                    has_access = True
                    break
            if has_access:
                break

        if not has_access:
            poorly_connected.add(building_tile)

    return poorly_connected


def _analyze_road_hierarchy(master_plan: list) -> dict:
    """Analyze the road network hierarchy and usage patterns."""
    analysis = {
        "needs_primary_roads": False,
        "needs_secondary_roads": False,
        "overloaded_segments": False,
        "road_coverage": 0.0
    }

    # Count different road types
    road_counts = {"via": 0, "vicus": 0, "semita": 0}

    for struct in master_plan:
        if struct.get("building_type") == "road":
            road_type = struct.get("name", "").lower()
            if "via" in road_type:
                road_counts["via"] += 1
            elif "vicus" in road_type:
                road_counts["vicus"] += 1
            else:
                road_counts["semita"] += 1

    # Analyze road hierarchy
    total_roads = sum(road_counts.values())
    if total_roads > 0:
        # Need primary roads if we have many buildings but few major roads
        building_count = sum(1 for s in master_plan if s.get("building_type") != "road")
        if building_count > 20 and road_counts["via"] < 2:
            analysis["needs_primary_roads"] = True

        # Need secondary roads for connectivity
        if building_count > 10 and road_counts["vicus"] < building_count // 5:
            analysis["needs_secondary_roads"] = True

    return analysis


def _buildings_are_near(cluster1: set, cluster2: set, max_distance: int) -> bool:
    """Check if two building clusters are near each other."""
    for x1, y1 in cluster1:
        for x2, y2 in cluster2:
            if abs(x1 - x2) + abs(y1 - y2) <= max_distance:
                return True
    return False
