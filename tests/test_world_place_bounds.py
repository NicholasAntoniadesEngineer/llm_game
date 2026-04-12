"""Optional coordinate rejection for ``WorldState.place_tile`` (config-driven)."""

from dataclasses import replace

from orchestration.world_commit import apply_tile_placements
from world.state import WorldState

from tests.conftest import SYSTEM_CONFIGURATION


def test_place_tile_accepts_in_bounds_when_reject_enabled():
    cfg = replace(SYSTEM_CONFIGURATION, world_place_tile_reject_out_of_bounds_flag=1)
    w = WorldState(chunk_size_tiles=cfg.grid.chunk_size_tiles, system_configuration=cfg)
    assert w.place_tile(0, 0, {"terrain": "grass", "elevation": 0.0}) is True
    assert w.get_tile(0, 0) is not None


def test_place_tile_rejects_out_of_bounds_when_enabled():
    cfg = replace(SYSTEM_CONFIGURATION, world_place_tile_reject_out_of_bounds_flag=1)
    w = WorldState(chunk_size_tiles=cfg.grid.chunk_size_tiles, system_configuration=cfg)
    hi = int(cfg.maximum_coordinate_value)
    assert w.place_tile(hi + 1, 0, {"terrain": "road"}) is False
    assert w.get_tile(hi + 1, 0) is None


def test_place_tile_allows_out_of_bounds_when_disabled():
    cfg = replace(SYSTEM_CONFIGURATION, world_place_tile_reject_out_of_bounds_flag=0)
    w = WorldState(chunk_size_tiles=cfg.grid.chunk_size_tiles, system_configuration=cfg)
    hi = int(cfg.maximum_coordinate_value)
    assert w.place_tile(hi + 1, 0, {"terrain": "road"}) is True


def test_apply_tile_placements_counts_place_tile_rejections():
    cfg = replace(SYSTEM_CONFIGURATION, world_place_tile_reject_out_of_bounds_flag=1)
    w = WorldState(chunk_size_tiles=cfg.grid.chunk_size_tiles, system_configuration=cfg)
    hi = int(cfg.maximum_coordinate_value)
    batch = apply_tile_placements(
        w,
        [
            (0, 0, {"terrain": "grass", "elevation": 0.0}),
            (hi + 1, 0, {"terrain": "road", "elevation": 0.0}),
        ],
        system_configuration=cfg,
    )
    assert batch.attempted_coordinate_pairs == 2
    assert batch.place_tile_rejections_count == 1
    assert len(batch.placed_tile_dicts) == 1
    assert w.get_tile(hi + 1, 0) is None
