"""Tests for ``world.placement_validator`` and placement-aware ``apply_tile_placements``."""

import pytest

from core.errors import PlacementError
from orchestration.world_commit import apply_tile_placements
from world.blueprint import CityBlueprint
from world.placement_validator import (
    PlacementValidationContext,
    deterministic_shuffled_cells,
    stable_seed_from_labels,
    validate_and_repair_placement,
)
from world.state import WorldState
from world.tile import Building, Tile

from tests.conftest import SYSTEM_CONFIGURATION


def test_stable_seed_deterministic():
    assert stable_seed_from_labels("a", "b") == stable_seed_from_labels("a", "b")
    assert stable_seed_from_labels("a", "b") != stable_seed_from_labels("a", "c")


def test_shuffle_reproducible():
    cells = [(0, 0), (1, 0), (2, 0), (0, 1)]
    a = deterministic_shuffled_cells(cells, seed=42)
    b = deterministic_shuffled_cells(cells, seed=42)
    assert a == b
    assert sorted(a) == sorted(cells)


def test_validate_and_repair_accepts_flat_building():
    w = WorldState(
        chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles,
        system_configuration=SYSTEM_CONFIGURATION,
    )
    w.place_tile(5, 5, {"terrain": "grass", "elevation": 0.5, "stability": 0.9, "slope": 0.01})
    bp = CityBlueprint.from_config(SYSTEM_CONFIGURATION)
    triples = [
        (
            5,
            5,
            {
                "terrain": "building",
                "building_name": "A",
                "building_type": "domus",
                "spec": {"components": []},
            },
        )
    ]
    ctx = PlacementValidationContext(5, 5, "A", "domus", "D1")
    out = validate_and_repair_placement(
        w,
        bp,
        triples,
        system_configuration=SYSTEM_CONFIGURATION,
        context=ctx,
    )
    assert len(out) == 1


def test_apply_tile_placements_with_validation():
    w = WorldState(
        chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles,
        system_configuration=SYSTEM_CONFIGURATION,
    )
    w.place_tile(2, 2, {"terrain": "grass", "elevation": 0.0, "stability": 0.95, "slope": 0.02})
    bp = CityBlueprint.from_config(SYSTEM_CONFIGURATION)
    batch = apply_tile_placements(
        w,
        [
            (
                2,
                2,
                {
                    "terrain": "building",
                    "building_name": "Shop",
                    "building_type": "taberna",
                    "spec": {},
                },
            )
        ],
        system_configuration=SYSTEM_CONFIGURATION,
        blueprint=bp,
        placement_context=PlacementValidationContext(2, 2, "Shop", "taberna", "M"),
    )
    assert len(batch.placed_tile_dicts) == 1


def test_building_frozen_footprint_validation():
    with pytest.raises(ValueError):
        Building("x", "domus", "", {}, footprint_relative_tiles=((1, 0),))
    b = Building("x", "domus", "", {}, footprint_relative_tiles=((0, 0), (1, 0)))
    assert len(b.footprint_relative_tiles) == 2


def test_tile_terrain_analysis_dataclass_roundtrip():
    t = Tile(x=0, y=0, terrain="grass")
    t.stability = 0.8
    t.slope = 0.1
    d = t.to_dict()
    assert d.get("stability") == 0.8
    t2 = Tile(x=1, y=1)
    t2.apply_placement_payload(d)
    assert t2.stability == pytest.approx(0.8)
