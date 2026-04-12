"""Commercial footprint must touch a road when any road exists in the world."""

import pytest

from orchestration.placement import validate_footprint_commercial_road_adjacency
from world.state import WorldState

from tests.conftest import SYSTEM_CONFIGURATION


def test_commercial_skips_when_no_roads():
    w = WorldState(
        chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles,
        system_configuration=SYSTEM_CONFIGURATION,
    )
    w.place_tile(0, 0, {"terrain": "grass", "elevation": 0.0, "stability": 0.9, "slope": 0.01})
    validate_footprint_commercial_road_adjacency(w, 0, 0, ((0, 0),), "taberna")


def test_commercial_raises_without_road_touch():
    w = WorldState(
        chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles,
        system_configuration=SYSTEM_CONFIGURATION,
    )
    w.place_tile(0, 0, {"terrain": "grass", "elevation": 0.0, "stability": 0.9, "slope": 0.01})
    w.place_tile(20, 20, {"terrain": "road", "elevation": 0.0, "stability": 1.0, "slope": 0.0})
    with pytest.raises(ValueError, match="commercial"):
        validate_footprint_commercial_road_adjacency(w, 0, 0, ((0, 0),), "taberna")


def test_commercial_ok_with_cardinal_road():
    w = WorldState(
        chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles,
        system_configuration=SYSTEM_CONFIGURATION,
    )
    w.place_tile(0, 0, {"terrain": "grass", "elevation": 0.0, "stability": 0.9, "slope": 0.01})
    w.place_tile(1, 0, {"terrain": "road", "elevation": 0.0, "stability": 1.0, "slope": 0.0})
    w.place_tile(10, 10, {"terrain": "road", "elevation": 0.0, "stability": 1.0, "slope": 0.0})
    validate_footprint_commercial_road_adjacency(w, 0, 0, ((0, 0),), "market")
