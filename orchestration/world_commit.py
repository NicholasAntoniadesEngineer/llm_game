"""Orchestration-driven tile writes: validate coordinates then ``WorldState.place_tile``.

Elevation clamp is implemented in ``world.tile_payload.normalize_tile_dict_for_world``;
``apply_tile_placements`` is the preferred entry point for any batch (terrain refresh, roads,
chunk restore) so normalization stays consistent before ``place_tile``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from world.placement_validator import (
    PlacementValidationContext,
    try_translate_placement_to_candidates,
    validate_and_repair_placement,
)
from world.state import WorldState
from world.tile_payload import normalize_tile_dict_for_world

if TYPE_CHECKING:
    from core.config import Config
    from world.blueprint import CityBlueprint

@dataclass(frozen=True)
class TileApplyBatchResult:
    """Outcome of applying a batch of tile payloads at fixed coordinates."""

    placed_tile_dicts: list[dict]
    attempted_coordinate_pairs: int
    skipped_invalid_coordinates: int
    place_tile_rejections_count: int


def apply_tile_placements(
    world: WorldState,
    tile_triples: Sequence[tuple[int | float, int | float, dict]],
    *,
    system_configuration: Config,
    blueprint: CityBlueprint | None = None,
    placement_context: PlacementValidationContext | None = None,
    placement_fallback_candidates: Sequence[tuple[int, int]] | None = None,
    placement_max_candidate_tries: int = 0,
) -> TileApplyBatchResult:
    """For each ``(x, y, payload)``, normalize elevation then ``place_tile``.

    When ``blueprint`` and ``placement_context`` (a ``PlacementValidationContext``) are set
    and the structure is not open-terrain-only, runs ``validate_and_repair_placement`` first.

    Skips entries with non-integral coordinates after coercion or missing x/y.
    """
    work_triples: Sequence[tuple[int | float, int | float, dict]] = tile_triples
    if blueprint is not None and placement_context is not None:
        if (
            placement_fallback_candidates is not None
            and int(placement_max_candidate_tries) > 0
        ):
            work_triples, _ctx_used = try_translate_placement_to_candidates(
                world,
                blueprint,
                list(tile_triples),
                system_configuration=system_configuration,
                context=placement_context,
                candidate_cells=tuple(placement_fallback_candidates),
                max_candidate_tries=int(placement_max_candidate_tries),
            )
        else:
            work_triples = validate_and_repair_placement(
                world,
                blueprint,
                list(tile_triples),
                system_configuration=system_configuration,
                context=placement_context,
            )

    placed_tile_dicts: list[dict] = []
    skipped_invalid_coordinates = 0
    place_tile_rejections_count = 0
    for triple in work_triples:
        if len(triple) != 3:
            skipped_invalid_coordinates += 1
            continue
        raw_x, raw_y, raw_payload = triple
        if raw_x is None or raw_y is None:
            skipped_invalid_coordinates += 1
            continue
        try:
            tile_x = int(raw_x)
            tile_y = int(raw_y)
        except (TypeError, ValueError):
            skipped_invalid_coordinates += 1
            continue
        normalized_payload = normalize_tile_dict_for_world(
            raw_payload, system_configuration=system_configuration
        )
        if world.place_tile(tile_x, tile_y, normalized_payload):
            placed_tile = world.get_tile(tile_x, tile_y)
            if placed_tile:
                placed_tile_dicts.append(placed_tile.to_dict())
        else:
            place_tile_rejections_count += 1
    return TileApplyBatchResult(
        placed_tile_dicts=placed_tile_dicts,
        attempted_coordinate_pairs=len(tile_triples),
        skipped_invalid_coordinates=skipped_invalid_coordinates,
        place_tile_rejections_count=place_tile_rejections_count,
    )
