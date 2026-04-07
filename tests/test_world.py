"""Tests for world/ — WorldState operations, Tile model, sparse behavior, serialization."""

import pytest

from world.tiles import Tile, TERRAIN_COLORS, BUILDING_ICONS, TERRAIN_ICONS
from world.state import WorldState


# ---------------------------------------------------------------------------
# Tile dataclass
# ---------------------------------------------------------------------------


class TestTile:
    def test_default_values(self):
        t = Tile(x=0, y=0)
        assert t.terrain == "empty"
        assert t.elevation == 0.0
        assert t.building_name is None
        assert t.building_type is None
        assert t.color == "#c2b280"
        assert t.icon == ""
        assert t.turn == 0
        assert t.spec is None

    def test_to_dict_includes_required_keys(self):
        t = Tile(x=5, y=10, terrain="building", building_name="Temple")
        d = t.to_dict()
        assert d["x"] == 5
        assert d["y"] == 10
        assert d["terrain"] == "building"
        assert d["building_name"] == "Temple"
        assert "elevation" in d
        assert "color" in d
        assert "turn" in d

    def test_to_dict_omits_none_non_required(self):
        t = Tile(x=0, y=0)
        d = t.to_dict()
        # description should be None and not in always-serialize, so might be omitted
        # Only check that always-serialized keys are present
        assert "x" in d
        assert "y" in d
        assert "terrain" in d

    def test_to_dict_includes_spec_when_set(self):
        t = Tile(x=0, y=0, spec={"components": [{"type": "podium"}]})
        d = t.to_dict()
        assert d["spec"] == {"components": [{"type": "podium"}]}


class TestTerrainMaps:
    def test_terrain_colors_has_common_types(self):
        for terrain in ("empty", "road", "building", "water", "garden", "forum"):
            assert terrain in TERRAIN_COLORS

    def test_building_icons_has_common_types(self):
        for btype in ("temple", "domus", "insula", "aqueduct"):
            assert btype in BUILDING_ICONS

    def test_terrain_icons_has_common_types(self):
        for terrain in ("road", "garden", "water"):
            assert terrain in TERRAIN_ICONS


# ---------------------------------------------------------------------------
# WorldState — basic operations
# ---------------------------------------------------------------------------


class TestWorldStateInit:
    def test_empty_world(self):
        w = WorldState()
        assert len(w.tiles) == 0
        assert w.width == 0
        assert w.height == 0
        assert w.turn == 0
        assert w.current_period == ""
        assert w.current_year == 0

    def test_clear(self):
        w = WorldState()
        w.place_tile(5, 5, {"terrain": "road"})
        w.turn = 10
        w.clear()
        assert len(w.tiles) == 0
        assert w.min_x == 0
        assert w.max_x == 0
        assert w.turn == 0
        assert len(w.build_log) == 0


class TestWorldStatePlaceTile:
    def test_place_single_tile(self):
        w = WorldState()
        result = w.place_tile(10, 20, {"terrain": "building", "building_name": "Temple"})
        assert result is True
        tile = w.tiles[(10, 20)]
        assert tile.terrain == "building"
        assert tile.building_name == "Temple"

    def test_place_updates_bounds(self):
        w = WorldState()
        w.place_tile(5, 10, {"terrain": "road"})
        assert w.min_x == 5
        assert w.max_x == 5
        assert w.min_y == 10
        assert w.max_y == 10

    def test_multiple_tiles_expand_bounds(self):
        w = WorldState()
        w.place_tile(0, 0, {"terrain": "road"})
        w.place_tile(10, 20, {"terrain": "building"})
        assert w.min_x == 0
        assert w.max_x == 10
        assert w.min_y == 0
        assert w.max_y == 20

    def test_width_and_height(self):
        w = WorldState()
        w.place_tile(5, 10, {"terrain": "road"})
        w.place_tile(15, 30, {"terrain": "road"})
        assert w.width == 11   # 15 - 5 + 1
        assert w.height == 21  # 30 - 10 + 1

    def test_overwrite_existing_tile(self):
        w = WorldState()
        w.place_tile(0, 0, {"terrain": "road"})
        w.place_tile(0, 0, {"terrain": "building", "building_name": "Temple"})
        tile = w.tiles[(0, 0)]
        assert tile.terrain == "building"
        assert tile.building_name == "Temple"

    def test_elevation_clamping_high(self):
        w = WorldState()
        w.place_tile(0, 0, {"terrain": "building", "elevation": 100.0})
        assert w.tiles[(0, 0)].elevation == 30.0

    def test_elevation_clamping_low(self):
        w = WorldState()
        w.place_tile(0, 0, {"terrain": "water", "elevation": -50.0})
        assert w.tiles[(0, 0)].elevation == -5.0

    def test_normal_elevation(self):
        w = WorldState()
        w.place_tile(0, 0, {"terrain": "building", "elevation": 2.5})
        assert w.tiles[(0, 0)].elevation == 2.5

    def test_default_color_for_terrain(self):
        w = WorldState()
        w.place_tile(0, 0, {"terrain": "road"})
        assert w.tiles[(0, 0)].color == TERRAIN_COLORS["road"]

    def test_default_icon_for_building_type(self):
        w = WorldState()
        w.place_tile(0, 0, {"terrain": "building", "building_type": "temple"})
        assert w.tiles[(0, 0)].icon == BUILDING_ICONS["temple"]

    def test_default_icon_for_terrain_type(self):
        w = WorldState()
        w.place_tile(0, 0, {"terrain": "water"})
        assert w.tiles[(0, 0)].icon == TERRAIN_ICONS["water"]

    def test_dirty_chunks_tracked(self):
        w = WorldState()
        w.place_tile(0, 0, {"terrain": "road"})
        assert len(w._dirty_chunks) >= 1

    def test_build_log_appended(self):
        w = WorldState()
        w.place_tile(5, 10, {"terrain": "building"})
        assert len(w.build_log) == 1
        assert w.build_log[0]["x"] == 5
        assert w.build_log[0]["y"] == 10

    def test_negative_coordinates(self):
        w = WorldState()
        w.place_tile(-10, -20, {"terrain": "road"})
        assert w.min_x == -10
        assert w.min_y == -20
        tile = w.get_tile(-10, -20)
        assert tile is not None
        assert tile.terrain == "road"

    def test_x_y_not_overwritten_from_data(self):
        """Tile x,y should be set by place_tile args, not from data dict."""
        w = WorldState()
        w.place_tile(5, 10, {"terrain": "road", "x": 999, "y": 888})
        tile = w.tiles[(5, 10)]
        assert tile.x == 5
        assert tile.y == 10


class TestWorldStateGetTile:
    def test_get_existing_tile(self):
        w = WorldState()
        w.place_tile(3, 4, {"terrain": "garden"})
        tile = w.get_tile(3, 4)
        assert tile is not None
        assert tile.terrain == "garden"

    def test_get_nonexistent_tile(self):
        w = WorldState()
        assert w.get_tile(100, 200) is None


class TestWorldStateGetRegionSummary:
    def test_empty_region(self):
        w = WorldState()
        result = w.get_region_summary(0, 0, 10, 10)
        assert "empty" in result.lower()

    def test_region_with_tiles(self):
        w = WorldState()
        w.place_tile(5, 5, {"terrain": "building", "building_name": "Temple"})
        result = w.get_region_summary(0, 0, 10, 10)
        assert "Temple" in result

    def test_region_excludes_empty_tiles(self):
        w = WorldState()
        w.place_tile(5, 5, {"terrain": "empty"})
        result = w.get_region_summary(0, 0, 10, 10)
        assert "empty" in result.lower()

    def test_region_truncates_large(self):
        w = WorldState()
        for i in range(100):
            w.place_tile(i, 0, {"terrain": "road"})
        result = w.get_region_summary(0, 0, 100, 0, max_tiles=10)
        assert "showing" in result.lower()


class TestWorldStateOccupiedTileDicts:
    def test_empty_world(self):
        w = WorldState()
        assert w.occupied_tile_dicts() == []

    def test_excludes_empty_tiles(self):
        w = WorldState()
        w.place_tile(0, 0, {"terrain": "empty"})
        w.place_tile(1, 0, {"terrain": "building"})
        dicts = w.occupied_tile_dicts()
        assert len(dicts) == 1
        assert dicts[0]["terrain"] == "building"


class TestWorldStateToDict:
    def test_to_dict_structure(self):
        w = WorldState()
        w.current_period = "Late Republic"
        w.current_year = -44
        w.place_tile(5, 10, {"terrain": "building"})
        d = w.to_dict()
        assert d["type"] == "world_state"
        assert d["period"] == "Late Republic"
        assert d["year"] == -44
        assert isinstance(d["tiles"], list)
        assert d["min_x"] == 5
        assert d["min_y"] == 10

    def test_to_dict_empty_world(self):
        w = WorldState()
        d = w.to_dict()
        assert d["width"] == 0
        assert d["height"] == 0
        assert d["tiles"] == []


class TestWorldStateTilesSince:
    def test_tiles_since(self):
        w = WorldState()
        w.turn = 1
        w.place_tile(0, 0, {"terrain": "road"})
        w.turn = 5
        w.place_tile(1, 1, {"terrain": "building"})
        result = w.tiles_since(3)
        assert len(result) == 1
        assert result[0]["x"] == 1

    def test_tiles_since_excludes_empty(self):
        w = WorldState()
        w.turn = 1
        w.place_tile(0, 0, {"terrain": "empty"})
        result = w.tiles_since(0)
        assert len(result) == 0

    def test_tiles_since_includes_all_recent(self):
        w = WorldState()
        w.turn = 0
        w.place_tile(0, 0, {"terrain": "road"})
        w.place_tile(1, 0, {"terrain": "road"})
        result = w.tiles_since(0)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Sparse / unbounded behavior
# ---------------------------------------------------------------------------


class TestWorldStateSparse:
    def test_widely_separated_tiles(self):
        w = WorldState()
        w.place_tile(-1000, -1000, {"terrain": "water"})
        w.place_tile(1000, 1000, {"terrain": "building"})
        # Only 2 tiles stored, even though bounding box is huge
        assert len(w.tiles) == 2
        assert w.width == 2001
        assert w.height == 2001

    def test_no_preallocated_grid(self):
        """World should not allocate tiles for the entire bounding box."""
        w = WorldState()
        w.place_tile(0, 0, {"terrain": "road"})
        w.place_tile(100, 100, {"terrain": "road"})
        assert len(w.tiles) == 2  # Not 101*101
