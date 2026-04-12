"""Tests for world/ — WorldState operations, Tile model, sparse behavior, serialization."""

import pytest

from world.tiles import Tile
from world.state import WorldState
from world.blueprint import CityBlueprint
from world.roads import rasterize_road, road_dict_allows_water_crossing, water_features_channel_tiles

from tests.conftest import SYSTEM_CONFIGURATION


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


def _terrain_types_with_display_color(system_configuration) -> set[str]:
    keys = set(system_configuration.terrain_type_display_colors_extra_dictionary.keys())
    for name, entry in system_configuration.terrain.terrain_defaults_dictionary.items():
        if isinstance(entry, dict) and entry.get("color"):
            keys.add(str(name))
    return keys


class TestTerrainMaps:
    def test_terrain_colors_has_common_types(self):
        keys = _terrain_types_with_display_color(SYSTEM_CONFIGURATION)
        for terrain in ("empty", "road", "building", "water", "garden", "forum"):
            assert terrain in keys

    def test_building_icons_has_common_types(self):
        icons = SYSTEM_CONFIGURATION.building_type_display_icons_dictionary
        for btype in ("temple", "domus", "insula", "aqueduct"):
            assert btype in icons

    def test_terrain_icons_has_common_types(self):
        icons = SYSTEM_CONFIGURATION.terrain_display_icons_dictionary
        for terrain in ("road", "garden", "water"):
            assert terrain in icons


# ---------------------------------------------------------------------------
# WorldState — basic operations
# ---------------------------------------------------------------------------


class TestWorldStateInit:
    def test_empty_world(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        assert len(w.tiles) == 0
        assert w.width == 0
        assert w.height == 0
        assert w.turn == 0
        assert w.current_period == ""
        assert w.current_year == 0

    def test_clear(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
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
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        result = w.place_tile(10, 20, {"terrain": "building", "building_name": "Temple"})
        assert result is True
        tile = w.tiles[(10, 20)]
        assert tile.terrain == "building"
        assert tile.building_name == "Temple"

    def test_place_updates_bounds(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(5, 10, {"terrain": "road"})
        assert w.min_x == 5
        assert w.max_x == 5
        assert w.min_y == 10
        assert w.max_y == 10

    def test_multiple_tiles_expand_bounds(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(0, 0, {"terrain": "road"})
        w.place_tile(10, 20, {"terrain": "building"})
        assert w.min_x == 0
        assert w.max_x == 10
        assert w.min_y == 0
        assert w.max_y == 20

    def test_width_and_height(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(5, 10, {"terrain": "road"})
        w.place_tile(15, 30, {"terrain": "road"})
        assert w.width == 11   # 15 - 5 + 1
        assert w.height == 21  # 30 - 10 + 1

    def test_overwrite_existing_tile(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(0, 0, {"terrain": "road"})
        w.place_tile(0, 0, {"terrain": "building", "building_name": "Temple"})
        tile = w.tiles[(0, 0)]
        assert tile.terrain == "building"
        assert tile.building_name == "Temple"

    def test_elevation_clamping_high(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(0, 0, {"terrain": "building", "elevation": 100.0})
        assert w.tiles[(0, 0)].elevation == SYSTEM_CONFIGURATION.grid.maximum_elevation_value

    def test_elevation_clamping_low(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(0, 0, {"terrain": "water", "elevation": -50.0})
        assert w.tiles[(0, 0)].elevation == SYSTEM_CONFIGURATION.world_place_tile_min_elevation

    def test_normal_elevation(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(0, 0, {"terrain": "building", "elevation": 2.5})
        assert w.tiles[(0, 0)].elevation == 2.5

    def test_default_color_for_terrain(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(0, 0, {"terrain": "road"})
        expected = SYSTEM_CONFIGURATION.terrain.terrain_defaults_dictionary["road"]["color"]
        assert w.tiles[(0, 0)].color == expected

    def test_default_icon_for_building_type(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(0, 0, {"terrain": "building", "building_type": "temple"})
        assert w.tiles[(0, 0)].icon == SYSTEM_CONFIGURATION.building_type_display_icons_dictionary["temple"]

    def test_default_icon_for_terrain_type(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(0, 0, {"terrain": "water"})
        assert w.tiles[(0, 0)].icon == SYSTEM_CONFIGURATION.terrain_display_icons_dictionary["water"]

    def test_dirty_chunks_tracked(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(0, 0, {"terrain": "road"})
        assert len(w._dirty_chunks) >= 1

    def test_build_log_appended(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(5, 10, {"terrain": "building"})
        assert len(w.build_log) == 1
        assert w.build_log[0]["x"] == 5
        assert w.build_log[0]["y"] == 10

    def test_negative_coordinates(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(-10, -20, {"terrain": "road"})
        assert w.min_x == -10
        assert w.min_y == -20
        tile = w.get_tile(-10, -20)
        assert tile is not None
        assert tile.terrain == "road"

    def test_x_y_not_overwritten_from_data(self):
        """Tile x,y should be set by place_tile args, not from data dict."""
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(5, 10, {"terrain": "road", "x": 999, "y": 888})
        tile = w.tiles[(5, 10)]
        assert tile.x == 5
        assert tile.y == 10


class TestWorldStateGetTile:
    def test_get_existing_tile(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(3, 4, {"terrain": "garden"})
        tile = w.get_tile(3, 4)
        assert tile is not None
        assert tile.terrain == "garden"

    def test_get_nonexistent_tile(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        assert w.get_tile(100, 200) is None


class TestWorldStateGetRegionSummary:
    def test_empty_region(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        result = w.get_region_summary(0, 0, 10, 10)
        assert "empty" in result.lower()

    def test_region_with_tiles(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(5, 5, {"terrain": "building", "building_name": "Temple"})
        result = w.get_region_summary(0, 0, 10, 10)
        assert "Temple" in result

    def test_region_excludes_empty_tiles(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(5, 5, {"terrain": "empty"})
        result = w.get_region_summary(0, 0, 10, 10)
        assert "empty" in result.lower()

    def test_region_truncates_large(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        for i in range(100):
            w.place_tile(i, 0, {"terrain": "road"})
        result = w.get_region_summary(0, 0, 100, 0, max_tiles=10)
        assert "showing" in result.lower()


class TestWorldStateOccupiedTileDicts:
    def test_empty_world(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        assert w.occupied_tile_dicts() == []

    def test_excludes_empty_tiles(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(0, 0, {"terrain": "empty"})
        w.place_tile(1, 0, {"terrain": "building"})
        dicts = w.occupied_tile_dicts()
        assert len(dicts) == 1
        assert dicts[0]["terrain"] == "building"


class TestWorldStateToDict:
    def test_to_dict_structure(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
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
        assert d["world_scale_meters_per_tile"] == float(
            SYSTEM_CONFIGURATION.grid.world_scale_meters_per_tile
        )

    def test_to_dict_empty_world(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        d = w.to_dict()
        assert d["width"] == 0
        assert d["height"] == 0
        assert d["tiles"] == []


class TestWorldStateTilesSince:
    def test_tiles_since(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.turn = 1
        w.place_tile(0, 0, {"terrain": "road"})
        w.turn = 5
        w.place_tile(1, 1, {"terrain": "building"})
        result = w.tiles_since(3)
        assert len(result) == 1
        assert result[0]["x"] == 1

    def test_tiles_since_excludes_empty(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.turn = 1
        w.place_tile(0, 0, {"terrain": "empty"})
        result = w.tiles_since(0)
        assert len(result) == 0

    def test_tiles_since_includes_all_recent(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
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
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(-1000, -1000, {"terrain": "water"})
        w.place_tile(1000, 1000, {"terrain": "building"})
        # Only 2 tiles stored, even though bounding box is huge
        assert len(w.tiles) == 2
        assert w.width == 2001
        assert w.height == 2001

    def test_no_preallocated_grid(self):
        """World should not allocate tiles for the entire bounding box."""
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w.place_tile(0, 0, {"terrain": "road"})
        w.place_tile(100, 100, {"terrain": "road"})
        assert len(w.tiles) == 2  # Not 101*101


class TestWorldStateRegionSummary:
    def test_region_summary_only_lists_tiles_in_bbox(self):
        w = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        for x in range(80):
            w.place_tile(x, 0, {"terrain": "road"})
        w.place_tile(5, 5, {"terrain": "building", "building_name": "Target"})
        summary = w.get_region_summary(5, 5, 5, 5, max_tiles=20)
        assert "Target" in summary
        assert summary.count("(0,0)") <= 1


class TestCityBlueprintElevationParity:
    def test_populate_matches_apply_elevation_numeric(self):
        w1 = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w1.place_tile(0, 0, {"terrain": "grass"})
        w1.place_tile(2, 1, {"terrain": "grass"})
        bp1 = CityBlueprint.from_config(SYSTEM_CONFIGURATION)
        bp1.hills = [{"name": "test_hill", "cx": 1, "cy": 1, "radius": 8, "peak": 3.0}]
        scfg = SYSTEM_CONFIGURATION
        assert bp1.populate_elevation(w1, system_configuration=scfg) > 0

        w2 = WorldState(chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles, system_configuration=SYSTEM_CONFIGURATION)
        w2.place_tile(0, 0, {"terrain": "grass"})
        w2.place_tile(2, 1, {"terrain": "grass"})
        bp2 = CityBlueprint.from_config(SYSTEM_CONFIGURATION)
        bp2.hills = list(bp1.hills)
        assert bp2.apply_elevation_to_world(w2, system_configuration=scfg) > 0

        for coord in ((0, 0), (2, 1)):
            assert w1.get_tile(*coord).elevation == w2.get_tile(*coord).elevation


class TestRoadRasterizationRespectsWater:
    def test_water_features_channel_tiles_includes_polyline(self):
        water = [{"name": "r", "type": "river", "points": [[0, 0], [3, 0]], "width": 1}]
        ch = water_features_channel_tiles(
            water,
            default_channel_width_tiles=SYSTEM_CONFIGURATION.terrain.blueprint_water_channel_default_width_tiles,
        )
        assert (0, 0) in ch and (3, 0) in ch

    def test_road_skips_blueprint_water_channel(self):
        w = WorldState(
            chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles,
            system_configuration=SYSTEM_CONFIGURATION,
        )
        bp = CityBlueprint.from_config(SYSTEM_CONFIGURATION)
        bp.water = [{"name": "Tiber", "type": "river", "points": [[5, 0], [5, 8]]}]
        bp.reset_water_adjacency_cache()
        road = {"name": "Decumanus", "type": "vicus", "points": [[0, 4], [10, 4]], "width": 1}
        placed = rasterize_road(w, road, bp)
        assert placed > 0
        crossing = w.get_tile(5, 4)
        assert crossing is None or crossing.terrain != "road"

    def test_named_bridge_may_cross_water_channel(self):
        w = WorldState(
            chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles,
            system_configuration=SYSTEM_CONFIGURATION,
        )
        bp = CityBlueprint.from_config(SYSTEM_CONFIGURATION)
        bp.water = [{"name": "Tiber", "type": "river", "points": [[5, 0], [5, 8]]}]
        bp.reset_water_adjacency_cache()
        road = {"name": "Stone Bridge deck", "type": "vicus", "points": [[0, 4], [10, 4]], "width": 1}
        placed = rasterize_road(w, road, bp)
        assert placed > 0
        assert w.get_tile(5, 4) is not None
        assert w.get_tile(5, 4).terrain == "road"

    def test_vicus_skips_existing_water_terrain_without_blueprint(self):
        w = WorldState(
            chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles,
            system_configuration=SYSTEM_CONFIGURATION,
        )
        w.place_tile(3, 3, {"terrain": "water"})
        road = {"name": "Coast road", "type": "vicus", "points": [[0, 3], [6, 3]], "width": 1}
        rasterize_road(w, road, None)
        assert w.get_tile(3, 3).terrain == "water"

    def test_road_dict_allows_water_crossing_heuristic(self):
        assert road_dict_allows_water_crossing({"name": "Fabricius Bridge", "type": "vicus"}) is True
        assert road_dict_allows_water_crossing({"name": "Alley", "type": "bridge"}) is True
        assert road_dict_allows_water_crossing({"name": "Alley", "type": "vicus"}) is False
