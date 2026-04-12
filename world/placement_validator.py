"""Validate and repair structural tile placements before committing to ``WorldState``."""

from __future__ import annotations

import copy
import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

from core.errors import PlacementError
from world.tile import Building

if TYPE_CHECKING:
    from core.config import Config
    from world.blueprint import CityBlueprint
    from world.state import WorldState

logger = logging.getLogger("eternal.placement_validator")


def stable_seed_from_labels(*parts: str) -> int:
    """Deterministic 31-bit seed for shuffles from UTF-8 labels."""
    h = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big") & 0x7FFFFFFF


def deterministic_shuffled_cells(
    cells: Sequence[tuple[int, int]],
    *,
    seed: int,
) -> list[tuple[int, int]]:
    """Fisher–Yates shuffle driven by ``seed`` (reproducible)."""
    out = list(cells)
    rng = seed
    for i in range(len(out) - 1, 0, -1):
        rng = (1103515245 * rng + 12345) & 0x7FFFFFFF
        j = rng % (i + 1)
        out[i], out[j] = out[j], out[i]
    return out


def translate_tile_triples(
    triples: Sequence[tuple[int | float, int | float, dict[str, Any]]],
    delta_x: int,
    delta_y: int,
) -> list[tuple[int | float, int | float, dict[str, Any]]]:
    """Shift every (x, y) by (delta_x, delta_y); payloads are shallow-copied."""
    shifted: list[tuple[int | float, int | float, dict[str, Any]]] = []
    for raw_x, raw_y, payload in triples:
        shifted.append(
            (
                int(raw_x) + delta_x,
                int(raw_y) + delta_y,
                dict(payload),
            )
        )
    return shifted


def _relative_footprint_from_triples(
    triples: Sequence[tuple[int | float, int | float, Any]],
    anchor_x: int,
    anchor_y: int,
) -> tuple[tuple[int, int], ...]:
    cells: set[tuple[int, int]] = set()
    for raw_x, raw_y, _ in triples:
        cells.add((int(raw_x) - anchor_x, int(raw_y) - anchor_y))
    if (0, 0) not in cells:
        cells.add((0, 0))
    return tuple(sorted(cells))


def _anchor_spec_from_triples(
    triples: Sequence[tuple[int | float, int | float, dict[str, Any]]],
    anchor_x: int,
    anchor_y: int,
) -> dict[str, Any]:
    for raw_x, raw_y, payload in triples:
        if int(raw_x) == anchor_x and int(raw_y) == anchor_y:
            spec = payload.get("spec")
            return dict(spec) if isinstance(spec, dict) else {}
    for _rx, _ry, payload in triples:
        spec = payload.get("spec")
        if isinstance(spec, dict) and spec:
            return copy.deepcopy(spec)
    return {}


def _merge_spec_into_triples(
    triples: list[tuple[int | float, int | float, dict[str, Any]]],
    anchor_x: int,
    anchor_y: int,
    new_spec: dict[str, Any],
) -> None:
    for idx, (rx, ry, payload) in enumerate(triples):
        if int(rx) == anchor_x and int(ry) == anchor_y:
            merged = dict(payload)
            merged["spec"] = copy.deepcopy(new_spec)
            triples[idx] = (rx, ry, merged)
            return
    if triples:
        rx, ry, payload = triples[0]
        merged = dict(payload)
        merged["spec"] = copy.deepcopy(new_spec)
        triples[0] = (rx, ry, merged)


def _filter_triples_to_relative_footprint(
    triples: Sequence[tuple[int | float, int | float, dict[str, Any]]],
    anchor_x: int,
    anchor_y: int,
    relative_footprint_cells: set[tuple[int, int]],
) -> list[tuple[int | float, int | float, dict[str, Any]]] | None:
    kept: list[tuple[int | float, int | float, dict[str, Any]]] = []
    for raw_x, raw_y, payload in triples:
        ox = int(raw_x) - anchor_x
        oy = int(raw_y) - anchor_y
        if (ox, oy) in relative_footprint_cells:
            kept.append((raw_x, raw_y, dict(payload)))
    rel_from_kept = {(int(rx) - anchor_x, int(ry) - anchor_y) for rx, ry, _ in kept}
    if (0, 0) not in rel_from_kept or not kept:
        return None
    return kept


def _prune_weakest_non_anchor_cell_from_footprint(
    world: WorldState,
    anchor_x: int,
    anchor_y: int,
    footprint: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...] | None:
    if len(footprint) <= 1:
        return None
    scored: list[tuple[float, tuple[int, int]]] = []
    for dx, dy in footprint:
        if (dx, dy) == (0, 0):
            continue
        tile = world.get_tile(anchor_x + int(dx), anchor_y + int(dy))
        if tile is None or tile.stability is None:
            scored.append((0.0, (int(dx), int(dy))))
        else:
            scored.append((float(tile.stability), (int(dx), int(dy))))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0])
    worst_cell = scored[0][1]
    next_cells = tuple(sorted({c for c in footprint if c != worst_cell}))
    if (0, 0) not in set(next_cells):
        return None
    return next_cells


@dataclass(frozen=True)
class PlacementValidationContext:
    """Optional metadata when applying structural (non-procedural-only) batches."""

    anchor_x: int
    anchor_y: int
    building_name: str
    building_type: str
    district_key: str = ""


def validate_and_repair_placement(
    world: WorldState,
    blueprint: CityBlueprint,
    tile_triples: Sequence[tuple[int | float, int | float, dict[str, Any]]],
    *,
    system_configuration: Config,
    context: PlacementValidationContext,
) -> list[tuple[int | float, int | float, dict[str, Any]]]:
    """Return tile triples (possibly spec-repaired) that pass ``Building.validate_placement``.

    Raises ``PlacementError`` if stability/slope/water rules cannot be satisfied after
    ``apply_terrain_modifications`` and, on stability failure, an optional single footprint prune.
    """
    from core.config import Config as ConfigClass
    from world.blueprint import CityBlueprint as CityBlueprintClass

    if not isinstance(system_configuration, ConfigClass):
        raise TypeError("system_configuration must be Config")
    if not isinstance(blueprint, CityBlueprintClass):
        raise TypeError("blueprint must be CityBlueprint")

    open_types = system_configuration.terrain.open_terrain_types_set
    btype_lower = str(context.building_type).strip().lower()
    if btype_lower in open_types or btype_lower == "road":
        return [(x, y, dict(d)) for x, y, d in tile_triples]

    working: list[tuple[int | float, int | float, dict[str, Any]]] = [
        (x, y, dict(d)) for x, y, d in tile_triples
    ]
    if not working:
        raise PlacementError("empty tile triples", building_name=context.building_name)

    footprint = _relative_footprint_from_triples(working, context.anchor_x, context.anchor_y)
    water_channel_xy = blueprint.water_channel_tile_set(system_configuration=system_configuration)

    def _attempt_validate(spec_dict: dict[str, Any], footprint_rel: tuple[tuple[int, int], ...]) -> None:
        building = Building(
            name=str(context.building_name or "structure"),
            building_type=str(context.building_type or "building"),
            period="",
            spec=copy.deepcopy(spec_dict),
            footprint_relative_tiles=footprint_rel,
        )
        building.validate_placement(
            world,
            context.anchor_x,
            context.anchor_y,
            system_configuration=system_configuration,
            water_channel_tile_xy_set=water_channel_xy,
        )

    def _finalize_commercial_adjacency(footprint_rel: tuple[tuple[int, int], ...]) -> None:
        from orchestration.placement import validate_footprint_commercial_road_adjacency

        validate_footprint_commercial_road_adjacency(
            world,
            context.anchor_x,
            context.anchor_y,
            footprint_rel,
            context.building_type,
        )

    base_spec = _anchor_spec_from_triples(working, context.anchor_x, context.anchor_y)
    try:
        _attempt_validate(base_spec, footprint)
        _finalize_commercial_adjacency(footprint)
        trace_placement_outcome(context, "ok", repair="none")
        return working
    except ValueError as first_err:
        logger.debug(
            "placement first pass failed for %s: %s",
            context.building_name,
            first_err,
        )

    repaired_spec = blueprint.apply_terrain_modifications(
        copy.deepcopy(base_spec) if base_spec else {"components": []},
        context.anchor_x,
        context.anchor_y,
        world,
    )
    _merge_spec_into_triples(working, context.anchor_x, context.anchor_y, repaired_spec)
    try:
        _attempt_validate(repaired_spec, footprint)
        _finalize_commercial_adjacency(footprint)
        trace_placement_outcome(context, "ok", repair="adaptive_foundation")
        return working
    except ValueError as second_err:
        logger.debug(
            "placement adaptive foundation still failed for %s: %s",
            context.building_name,
            second_err,
        )

    msg_lower = str(second_err).lower()
    if "stability" in msg_lower or "below minimum" in msg_lower:
        pruned_footprint = _prune_weakest_non_anchor_cell_from_footprint(
            world, context.anchor_x, context.anchor_y, footprint
        )
        if pruned_footprint is not None:
            filtered_working = _filter_triples_to_relative_footprint(
                working,
                context.anchor_x,
                context.anchor_y,
                set(pruned_footprint),
            )
            if filtered_working is not None:
                working.clear()
                working.extend(filtered_working)
                footprint_pruned = _relative_footprint_from_triples(
                    working, context.anchor_x, context.anchor_y
                )
                pruned_anchor_spec = _anchor_spec_from_triples(
                    working, context.anchor_x, context.anchor_y
                )
                repaired_pruned = blueprint.apply_terrain_modifications(
                    copy.deepcopy(pruned_anchor_spec) if pruned_anchor_spec else {"components": []},
                    context.anchor_x,
                    context.anchor_y,
                    world,
                )
                _merge_spec_into_triples(
                    working, context.anchor_x, context.anchor_y, repaired_pruned
                )
                try:
                    _attempt_validate(repaired_pruned, footprint_pruned)
                    _finalize_commercial_adjacency(footprint_pruned)
                    trace_placement_outcome(context, "ok", repair="footprint_prune")
                    return working
                except ValueError as third_err:
                    trace_placement_outcome(
                        context,
                        "failed",
                        repair="footprint_prune",
                        detail=str(third_err),
                    )
                    raise PlacementError(
                        f"placement invalid after footprint prune: {third_err}",
                        building_name=context.building_name,
                        anchor=(context.anchor_x, context.anchor_y),
                    ) from third_err

    trace_placement_outcome(context, "failed", repair="adaptive_foundation", detail=str(second_err))
    raise PlacementError(
        f"placement invalid after repair: {second_err}",
        building_name=context.building_name,
        anchor=(context.anchor_x, context.anchor_y),
    ) from second_err


def trace_placement_outcome(
    context: PlacementValidationContext,
    outcome: str,
    *,
    repair: str,
    detail: str = "",
) -> None:
    from core.run_log import trace_event

    trace_event(
        "placement",
        "validate_and_repair",
        outcome=outcome,
        repair=repair,
        building=context.building_name,
        building_type=context.building_type,
        district=context.district_key,
        anchor_x=context.anchor_x,
        anchor_y=context.anchor_y,
        detail=detail[:400] if detail else "",
    )


def try_translate_placement_to_candidates(
    world: WorldState,
    blueprint: CityBlueprint,
    tile_triples: Sequence[tuple[int | float, int | float, dict[str, Any]]],
    *,
    system_configuration: Config,
    context: PlacementValidationContext,
    candidate_cells: Sequence[tuple[int, int]],
    max_candidate_tries: int,
) -> tuple[list[tuple[int | float, int | float, dict[str, Any]]], PlacementValidationContext]:
    """Try original anchor then deterministic shuffled district cells (same footprint shape).

    Returns the validated triple list and the ``PlacementValidationContext`` for the winning anchor.
    """
    triples_list = [(x, y, dict(d)) for x, y, d in tile_triples]
    seed = stable_seed_from_labels(
        context.district_key,
        context.building_name,
        str(context.anchor_x),
        str(context.anchor_y),
    )
    ordered = deterministic_shuffled_cells(list(candidate_cells), seed=seed)
    ordered = [c for c in ordered if c != (context.anchor_x, context.anchor_y)]
    tries = 0
    for cand_x, cand_y in [(context.anchor_x, context.anchor_y)] + ordered:
        if tries >= max_candidate_tries:
            break
        tries += 1
        dx = cand_x - context.anchor_x
        dy = cand_y - context.anchor_y
        shifted = translate_tile_triples(triples_list, dx, dy)
        sub_ctx = PlacementValidationContext(
            anchor_x=cand_x,
            anchor_y=cand_y,
            building_name=context.building_name,
            building_type=context.building_type,
            district_key=context.district_key,
        )
        try:
            repaired = validate_and_repair_placement(
                world,
                blueprint,
                shifted,
                system_configuration=system_configuration,
                context=sub_ctx,
            )
            return repaired, sub_ctx
        except PlacementError:
            continue
    raise PlacementError(
        f"exhausted {tries} placement candidates for {context.building_name!r}",
        building_name=context.building_name,
        anchor=(context.anchor_x, context.anchor_y),
    )
