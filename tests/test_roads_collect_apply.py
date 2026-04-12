"""Road raster collect vs apply (dry-run then commit)."""

from world.blueprint import CityBlueprint
from world.roads import collect_road_tile_placements, rasterize_road
from world.state import WorldState

from tests.conftest import SYSTEM_CONFIGURATION


def test_collect_then_apply_matches_single_rasterize():
    w = WorldState(
        chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles,
        system_configuration=SYSTEM_CONFIGURATION,
    )
    bp = CityBlueprint.from_config(SYSTEM_CONFIGURATION)
    road = {
        "name": "TestVia",
        "type": "vicus",
        "points": [(0, 0), (3, 0)],
        "width": 1,
    }
    triples = collect_road_tile_placements(w, road, bp)
    dry = rasterize_road(w, road, bp, apply_placements=False)
    assert dry == len(triples)
    n = rasterize_road(w, road, bp, apply_placements=True)
    assert n == len(triples)
    assert sum(1 for t in w.tiles.values() if t.terrain == "road") == len(triples)


def test_blueprint_collect_road_raster_triples_dry_run_count():
    w = WorldState(
        chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles,
        system_configuration=SYSTEM_CONFIGURATION,
    )
    bp = CityBlueprint.from_config(SYSTEM_CONFIGURATION)
    bp.roads = [
        {"name": "A", "type": "vicus", "points": [(1, 1), (1, 3)], "width": 1},
        {"name": "B", "type": "vicus", "points": [(5, 5), (6, 5)], "width": 1},
    ]
    triples = bp.collect_road_raster_triples(w)
    assert sum(1 for t in w.tiles.values() if t.terrain == "road") == 0
    assert len(triples) > 0
    placed = bp.rasterize_roads(w, apply_placements=False)
    assert placed == len(triples)
    assert sum(1 for _k, t in w.tiles.items() if t.terrain == "road") == 0
    bp.rasterize_roads(w, apply_placements=True)
    assert sum(1 for _k, t in w.tiles.items() if t.terrain == "road") == len(triples)
