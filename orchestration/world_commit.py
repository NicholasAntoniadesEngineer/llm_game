"""Orchestration-driven tile writes: validate coordinates then ``WorldState.place_tile``.

Elevation clamp is implemented once in ``world.tile_payload.normalize_tile_dict_for_world``;
``WorldState.place_tile`` applies it for every path (orchestration, roads, persistence).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from world.state import WorldState
from world.tile_payload import normalize_tile_dict_for_world

if TYPE_CHECKING:
    from core.config import Config


@dataclass(frozen=True)
class TileApplyBatchResult:
    """Outcome of applying a batch of tile payloads at fixed coordinates."""

    placed_tile_dicts: list[dict]
    attempted_coordinate_pairs: int
    skipped_invalid_coordinates: int


def apply_tile_placements(
    world: WorldState,
    tile_triples: Sequence[tuple[int | float, int | float, dict]],
    *,
    system_configuration: Config,
) -> TileApplyBatchResult:
    """For each ``(x, y, payload)``, normalize elevation then ``place_tile``.

    Skips entries with non-integral coordinates after coercion or missing x/y.
    """
    placed_tile_dicts: list[dict] = []
    skipped_invalid_coordinates = 0
    for triple in tile_triples:
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
    return TileApplyBatchResult(
        placed_tile_dicts=placed_tile_dicts,
        attempted_coordinate_pairs=len(tile_triples),
        skipped_invalid_coordinates=skipped_invalid_coordinates,
    )
