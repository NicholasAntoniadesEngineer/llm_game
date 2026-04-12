"""Deterministic repairs on survey master plans before Urbanista (config-free heuristics)."""

from __future__ import annotations

import logging

from orchestration.placement import _cardinally_adjacent_to_set, _footprint

logger = logging.getLogger("eternal.placement_repair")


def prune_bridges_not_adjacent_to_water_when_water_exists(master_plan: list) -> int:
    """Drop ``bridge`` structures that cannot touch any water tile when the plan includes water.

    Returns the number of structures removed.
    """
    if not isinstance(master_plan, list) or not master_plan:
        return 0

    water_union: set[tuple[int, int]] = set()
    for struct in master_plan:
        if not isinstance(struct, dict):
            continue
        if (struct.get("building_type") or "").lower() == "water":
            water_union |= _footprint(struct)

    if not water_union:
        return 0

    removed = 0
    kept: list = []
    for struct in master_plan:
        if not isinstance(struct, dict):
            kept.append(struct)
            continue
        bt = (struct.get("building_type") or "").lower()
        if bt == "bridge":
            fp = _footprint(struct)
            if fp and not _cardinally_adjacent_to_set(fp, water_union):
                logger.warning(
                    "Pruned bridge %r — not adjacent to water while river/lake tiles exist",
                    struct.get("name", "?"),
                )
                removed += 1
                continue
        kept.append(struct)

    if removed:
        master_plan.clear()
        master_plan.extend(kept)
    return removed
