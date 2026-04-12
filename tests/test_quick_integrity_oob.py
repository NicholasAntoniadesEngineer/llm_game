"""quick_integrity_check hints when tiles violate coordinate limits but rejection is off."""

from dataclasses import replace

from core.quick_integrity import quick_integrity_check
from world.state import WorldState

from tests.conftest import SYSTEM_CONFIGURATION


def test_quick_integrity_suggests_oob_flag_when_tiles_outside_limits():
    cfg = replace(SYSTEM_CONFIGURATION, world_place_tile_reject_out_of_bounds_flag=0)
    w = WorldState(chunk_size_tiles=cfg.grid.chunk_size_tiles, system_configuration=cfg)
    hi = int(cfg.maximum_coordinate_value)
    w.place_tile(hi + 1, 0, {"terrain": "road", "elevation": 0.0})
    notes = quick_integrity_check(
        w,
        blueprint_dict=None,
        restored_from_save=True,
    )
    assert any("world_place_tile_reject_out_of_bounds=1" in n for n in notes)
