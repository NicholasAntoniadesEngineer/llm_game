"""Fast unit tests for road connectivity, district parsing, and master-plan geometry."""

import pytest

from core.errors import ConfigLoadError
from orchestration.district_model import parse_district_dict
from orchestration.placement import (
    intra_plan_tile_overlaps,
    normalize_master_plan_tile_coordinates,
)
from tests.conftest import SYSTEM_CONFIGURATION
from world.road_connectivity import ensure_road_connectivity_in_master_plan


def test_ensure_road_connectivity_bridges_two_components():
    region = {"x1": 0, "y1": 0, "x2": 20, "y2": 20}
    plan = [
        {
            "name": "R1",
            "building_type": "road",
            "tiles": [{"x": 1, "y": 1, "elevation": 0.0}, {"x": 2, "y": 1, "elevation": 0.0}],
        },
        {
            "name": "R2",
            "building_type": "road",
            "tiles": [{"x": 8, "y": 1, "elevation": 0.0}, {"x": 9, "y": 1, "elevation": 0.0}],
        },
    ]
    cfg = SYSTEM_CONFIGURATION
    out = ensure_road_connectivity_in_master_plan(
        plan,
        region,
        road_bridge_default_elevation=cfg.road_bridge_default_elevation,
        world_grid_width_tiles=cfg.grid.world_grid_width,
        world_grid_height_tiles=cfg.grid.world_grid_height,
    )
    road_tiles = set()
    for s in out:
        if s.get("building_type") != "road":
            continue
        for t in s.get("tiles", []):
            road_tiles.add((int(t["x"]), int(t["y"])))
    assert (3, 1) in road_tiles or (4, 1) in road_tiles or (5, 1) in road_tiles or (6, 1) in road_tiles


def test_parse_district_dict_clamps_region():
    raw = {
        "name": "Forum",
        "region": {"x1": 1, "y1": 2, "x2": 5, "y2": 6},
        "period": "p",
        "year": -100,
        "description": "d",
    }
    spec = parse_district_dict(raw, system_configuration=SYSTEM_CONFIGURATION)
    assert spec.name == "Forum"
    assert spec.region_x1 == 1 and spec.region_y2 == 6
    d2 = spec.as_engine_dict()
    assert d2["region"]["x2"] == 5


def test_parse_district_dict_rejects_missing_region():
    with pytest.raises(ConfigLoadError):
        parse_district_dict({"name": "X"}, system_configuration=SYSTEM_CONFIGURATION)


def test_intra_plan_tile_overlaps_detects_duplicate():
    mp = [
        {"name": "A", "tiles": [{"x": 1, "y": 1}]},
        {"name": "B", "tiles": [{"x": 1, "y": 1}]},
    ]
    assert len(intra_plan_tile_overlaps(mp)) == 1


def test_normalize_master_plan_tile_coordinates():
    mp = [{"name": "A", "tiles": [{"x": "3", "y": "4"}]}]
    normalize_master_plan_tile_coordinates(mp)
    assert mp[0]["tiles"][0]["x"] == 3
    assert mp[0]["tiles"][0]["y"] == 4
