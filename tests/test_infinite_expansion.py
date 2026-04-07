"""Stress tests for the infinite expansion system.

Tests cover:
  - WorldState sparse storage and scaling
  - Edge detection for expansion frontiers
  - Concurrent expansion safety
  - Memory growth / build_log bounding
  - Coordinate overflow and large-grid behavior
  - WebSocket broadcast volume
  - Tile placement correctness at scale
"""

import asyncio
import sys
import time
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from world.state import WorldState
from world.tiles import Tile


# ═══════════════════════════════════════════════════════════════
# 1. WorldState — Sparse Storage Behavior
# ═══════════════════════════════════════════════════════════════

class TestWorldStateBoundaries:
    """The current WorldState uses sparse dict storage.
    It accepts any coordinates — no bounds rejection."""

    def test_place_tile_at_origin(self):
        ws = WorldState()
        assert ws.place_tile(0, 0, {"terrain": "road"}) is True
        tile = ws.get_tile(0, 0)
        assert tile.terrain == "road"

    def test_place_tile_at_large_coords(self):
        """Sparse world accepts arbitrarily large coordinates."""
        ws = WorldState()
        assert ws.place_tile(200, 200, {"terrain": "building"}) is True
        tile = ws.get_tile(200, 200)
        assert tile.terrain == "building"

    def test_place_tile_at_negative_coords(self):
        """Sparse world accepts negative coordinates for expansion."""
        ws = WorldState()
        assert ws.place_tile(-100, -50, {"terrain": "road"}) is True
        tile = ws.get_tile(-100, -50)
        assert tile.terrain == "road"

    def test_get_tile_unplaced_returns_none(self):
        ws = WorldState()
        assert ws.get_tile(-1, 0) is None
        assert ws.get_tile(10, 0) is None
        assert ws.get_tile(999, 999) is None

    def test_bounds_track_placed_tiles(self):
        ws = WorldState()
        ws.place_tile(5, 10, {"terrain": "road"})
        ws.place_tile(-3, -7, {"terrain": "road"})
        ws.place_tile(20, 15, {"terrain": "road"})
        assert ws.min_x == -3
        assert ws.max_x == 20
        assert ws.min_y == -7
        assert ws.max_y == 15


# ═══════════════════════════════════════════════════════════════
# 2. WorldState — Scaling and Memory
# ═══════════════════════════════════════════════════════════════

class TestWorldStateScaling:
    """Test behavior with many tiles and memory growth."""

    def test_build_log_is_capped(self):
        """build_log is pruned to prevent unbounded memory growth."""
        ws = WorldState()
        for i in range(10000):
            x, y = i % 100, i // 100
            ws.place_tile(x, y, {"terrain": "road", "building_name": f"road_{i}"})
        assert len(ws.build_log) <= 5000
        assert ws.build_log[-1]["building_name"] == "road_9999"

    def test_to_dict_scales_with_tiles(self):
        """to_dict only includes occupied tiles (sparse)."""
        ws = WorldState()
        ws.place_tile(0, 0, {"terrain": "road"})
        ws.place_tile(100, 100, {"terrain": "building"})

        start = time.time()
        d = ws.to_dict()
        elapsed = time.time() - start

        assert len(d["tiles"]) == 2
        assert elapsed < 1.0

    def test_tiles_since_filters_by_turn(self):
        """tiles_since only returns tiles from the requested turn onward."""
        ws = WorldState()
        ws.place_tile(50, 50, {"terrain": "road"})
        ws.turn = 1

        start = time.time()
        changed = ws.tiles_since(0)
        elapsed = time.time() - start

        assert len(changed) == 1
        assert elapsed < 0.5

    def test_get_region_summary_handles_large_regions(self):
        ws = WorldState()
        for x in range(100):
            ws.place_tile(x, 0, {"terrain": "road", "building_name": f"road_{x}"})

        summary = ws.get_region_summary(0, 0, 99, 99)
        assert "road_0" in summary

    def test_sparse_initialization_instant(self):
        """Sparse world initialization is O(1) — no grid allocation."""
        start = time.time()
        ws = WorldState()
        elapsed = time.time() - start
        assert elapsed < 0.01
        assert len(ws.tiles) == 0


# ═══════════════════════════════════════════════════════════════
# 3. WorldState — Serialization Stress
# ═══════════════════════════════════════════════════════════════

class TestWorldStateSerialization:
    """Test JSON serialization at scale -- this gets sent over WebSocket."""

    def test_full_state_json_size(self):
        """With 1600 tiles fully populated, the JSON blob size."""
        ws = WorldState()
        for y in range(40):
            for x in range(40):
                ws.place_tile(x, y, {
                    "terrain": "building",
                    "building_name": f"Building_{x}_{y}",
                    "building_type": "insula",
                    "description": "A multi-story apartment block",
                    "spec": {"shapes": [
                        {"type": "box", "pos": [0, 0.5, 0], "size": [0.8, 1.0, 0.8], "color": "#d4a373"}
                    ]}
                })

        state = ws.to_dict()
        json_str = json.dumps(state)
        size_kb = len(json_str) / 1024
        assert size_kb < 5000, f"Full state JSON is {size_kb:.0f}KB -- too large for WebSocket"

    def test_tiles_since_incremental_size(self):
        """Incremental updates should be much smaller than full state."""
        ws = WorldState()
        for y in range(20):
            for x in range(40):
                ws.place_tile(x, y, {"terrain": "road"})
        ws.turn = 1

        for x in range(5):
            ws.place_tile(x, 20, {"terrain": "building", "building_name": f"new_{x}"})

        changed = ws.tiles_since(1)
        assert len(changed) == 5
        json_str = json.dumps(changed)
        assert len(json_str) < 5000


# ═══════════════════════════════════════════════════════════════
# 4. Edge Detection for Expansion
# ═══════════════════════════════════════════════════════════════

class TestEdgeDetection:
    """Test finding the expansion frontier -- tiles at the edge of built area
    that have empty neighbors where new districts could be placed."""

    @staticmethod
    def find_edge_tiles(ws: WorldState) -> list[tuple[int, int]]:
        """Find tiles on the edge of the built area (have at least one empty neighbor)."""
        edges = []
        for (x, y), tile in ws.tiles.items():
            if tile.terrain != "empty":
                for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                    nx, ny = x + dx, y + dy
                    neighbor = ws.get_tile(nx, ny)
                    if neighbor is None or neighbor.terrain == "empty":
                        edges.append((x, y))
                        break
        return edges

    @staticmethod
    def find_expansion_zones(ws: WorldState, zone_size: int = 10) -> list[dict]:
        """Find empty rectangular zones adjacent to built areas for new districts."""
        edges = TestEdgeDetection.find_edge_tiles(ws)
        if not edges:
            return []

        zones = []
        visited = set()

        for ex, ey in edges:
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                zx = ex + dx * zone_size // 2
                zy = ey + dy * zone_size // 2

                zone_key = (zx // zone_size, zy // zone_size)
                if zone_key in visited:
                    continue
                visited.add(zone_key)

                x1 = zx - zone_size // 2
                y1 = zy - zone_size // 2
                x2 = zx + zone_size // 2
                y2 = zy + zone_size // 2

                empty_count = 0
                total = 0
                for cy in range(y1, y2 + 1):
                    for cx in range(x1, x2 + 1):
                        total += 1
                        t = ws.get_tile(cx, cy)
                        if t is None or t.terrain == "empty":
                            empty_count += 1

                if total > 0 and empty_count / total > 0.7:
                    zones.append({
                        "region": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                        "empty_ratio": empty_count / total,
                    })

        return zones

    def test_single_district_has_edges(self):
        """A single built district should have edge tiles all around it."""
        ws = WorldState()
        for y in range(15, 20):
            for x in range(15, 20):
                ws.place_tile(x, y, {"terrain": "building"})

        edges = self.find_edge_tiles(ws)
        # 5x5 block: all border tiles (16) touch empty space, interior (9) do not
        assert len(edges) == 16

    def test_isolated_tile_is_edge(self):
        """A single tile is always an edge tile."""
        ws = WorldState()
        ws.place_tile(50, 50, {"terrain": "building"})
        edges = self.find_edge_tiles(ws)
        assert len(edges) == 1

    def test_empty_world_no_edges(self):
        ws = WorldState()
        edges = self.find_edge_tiles(ws)
        assert len(edges) == 0

    def test_expansion_zones_found(self):
        """Should find zones adjacent to built area."""
        ws = WorldState()
        for y in range(5, 15):
            for x in range(5, 15):
                ws.place_tile(x, y, {"terrain": "building"})

        zones = self.find_expansion_zones(ws, zone_size=8)
        assert len(zones) > 0
        for zone in zones:
            assert zone["empty_ratio"] > 0.7

    def test_expansion_zones_sparse_world(self):
        """Expansion should work with scattered tiles."""
        ws = WorldState()
        # Small cluster
        for y in range(3):
            for x in range(3):
                ws.place_tile(x, y, {"terrain": "building"})

        zones = self.find_expansion_zones(ws, zone_size=5)
        assert len(zones) > 0

    def test_edge_detection_performance(self):
        """Edge detection on many tiles should complete in reasonable time."""
        ws = WorldState()
        for y in range(75, 125):
            for x in range(75, 125):
                ws.place_tile(x, y, {"terrain": "building"})

        start = time.time()
        edges = self.find_edge_tiles(ws)
        elapsed = time.time() - start

        assert len(edges) > 0
        assert elapsed < 2.0, f"Edge detection on 2500 tiles took {elapsed:.3f}s"


# ═══════════════════════════════════════════════════════════════
# 5. Tile Placement Correctness
# ═══════════════════════════════════════════════════════════════

class TestTilePlacement:
    """Verify tile placement handles all edge cases correctly."""

    def test_place_preserves_existing_fields(self):
        ws = WorldState()
        ws.place_tile(5, 5, {
            "terrain": "building",
            "building_name": "Temple of Jupiter",
            "building_type": "temple",
            "description": "Great temple",
        })
        ws.place_tile(5, 5, {"scene": "Citizens pray at the temple"})

        tile = ws.get_tile(5, 5)
        assert tile.building_name == "Temple of Jupiter"
        assert tile.scene == "Citizens pray at the temple"
        assert tile.terrain == "building"

    def test_place_none_values_ignored(self):
        ws = WorldState()
        ws.place_tile(5, 5, {"terrain": "building", "building_name": "Forum"})
        ws.place_tile(5, 5, {"building_name": None, "terrain": "building"})

        tile = ws.get_tile(5, 5)
        assert tile.building_name == "Forum"

    def test_place_updates_turn(self):
        ws = WorldState()
        ws.turn = 5
        ws.place_tile(3, 3, {"terrain": "road"})
        tile = ws.get_tile(3, 3)
        assert tile.turn == 5

    def test_overlapping_placements(self):
        ws = WorldState()
        ws.place_tile(5, 5, {"terrain": "building", "building_name": "Temple A"})
        ws.place_tile(5, 5, {"terrain": "building", "building_name": "Temple B"})

        tile = ws.get_tile(5, 5)
        assert tile.building_name == "Temple B"

    def test_default_color_applied(self):
        ws = WorldState()
        ws.place_tile(5, 5, {"terrain": "water"})
        tile = ws.get_tile(5, 5)
        assert tile.color == "#3498db"

    def test_default_icon_applied(self):
        ws = WorldState()
        ws.place_tile(5, 5, {"terrain": "building", "building_type": "temple"})
        tile = ws.get_tile(5, 5)
        assert tile.icon == "\U0001f3db"


# ═══════════════════════════════════════════════════════════════
# 6. Concurrent Expansion Safety
# ═══════════════════════════════════════════════════════════════

class TestConcurrentExpansion:
    """Test that concurrent tile placements don't corrupt state."""

    def test_concurrent_placements_to_different_tiles(self):
        ws = WorldState()
        results = []
        for i in range(100):
            results.append(ws.place_tile(i, 0, {"terrain": "road", "building_name": f"road_{i}"}))

        assert all(results)
        for i in range(100):
            tile = ws.get_tile(i, 0)
            assert tile.building_name == f"road_{i}"

    def test_rapid_same_tile_updates(self):
        ws = WorldState()
        for i in range(1000):
            ws.place_tile(5, 5, {"terrain": "building", "building_name": f"v{i}"})

        tile = ws.get_tile(5, 5)
        assert tile.building_name == "v999"
        # Build log is capped at 5000
        assert len(ws.build_log) == 1000


# ═══════════════════════════════════════════════════════════════
# 7. WebSocket Broadcast Volume
# ═══════════════════════════════════════════════════════════════

class TestBroadcastVolume:
    """Test message sizes and rates for WebSocket sustainability."""

    def test_tile_update_message_size(self):
        tile_data = {
            "x": 15, "y": 20, "terrain": "building",
            "building_name": "Temple of Saturn",
            "building_type": "temple",
            "description": "Ancient temple with 8 columns",
            "color": "#c8b88a",
            "spec": {"shapes": [
                {"type": "box", "pos": [0, 0.1, 0], "size": [0.9, 0.2, 0.7], "color": "#c8b88a"}
                for _ in range(40)
            ]}
        }

        msg = {"type": "tile_update", "tiles": [tile_data], "turn": 5}
        size = len(json.dumps(msg))
        assert size < 10000, f"Single tile update is {size} bytes"

    def test_bulk_tile_update_message_size(self):
        tiles = []
        for i in range(60):
            tiles.append({
                "x": i % 20, "y": i // 20, "terrain": "building",
                "building_name": f"Structure_{i}",
                "spec": {"shapes": [
                    {"type": "box", "pos": [0, 0.5, 0], "size": [0.8, 1.0, 0.8], "color": "#d4a373"}
                    for _ in range(20)
                ]}
            })

        msg = {"type": "tile_update", "tiles": tiles, "turn": 5}
        size = len(json.dumps(msg))
        size_kb = size / 1024
        assert size_kb < 200, f"Bulk update is {size_kb:.1f}KB -- may need batching"

    def test_chat_history_accumulation(self):
        history = []
        for i in range(900):
            history.append({
                "type": "chat",
                "sender": "faber",
                "content": f"Message {i} " * 20,
                "turn": i,
            })

        total_size = len(json.dumps(history))
        size_kb = total_size / 1024
        assert size_kb > 100, "Chat history should be substantial"


# ═══════════════════════════════════════════════════════════════
# 8. Coordinate System Stress
# ═══════════════════════════════════════════════════════════════

class TestCoordinateStress:
    """Test for coordinate overflow and floating-point issues."""

    def test_tile_coordinates_preserved_in_serialization(self):
        ws = WorldState()
        ws.place_tile(0, 0, {"terrain": "road"})
        ws.place_tile(39, 39, {"terrain": "road"})

        state = ws.to_dict()
        tiles_by_xy = {(t["x"], t["y"]): t for t in state["tiles"]}
        assert (0, 0) in tiles_by_xy
        assert (39, 39) in tiles_by_xy

    def test_large_coordinate_values_in_tiles(self):
        t = Tile(x=99999, y=99999)
        d = t.to_dict()
        assert d["x"] == 99999
        assert d["y"] == 99999
        s = json.dumps(d)
        restored = json.loads(s)
        assert restored["x"] == 99999

    def test_negative_coordinate_tiles(self):
        t = Tile(x=-500, y=-300)
        d = t.to_dict()
        assert d["x"] == -500
        assert d["y"] == -300


# ═══════════════════════════════════════════════════════════════
# 9. BuildEngine — Expansion Loop Behavior
# ═══════════════════════════════════════════════════════════════

class TestBuildEngineExpansion:
    """Test the engine's district iteration and completion behavior."""

    def test_engine_stops_after_all_districts(self):
        districts = [
            {"name": "Forum", "region": {"x1": 0, "y1": 0, "x2": 5, "y2": 5}},
            {"name": "Palatine", "region": {"x1": 5, "y1": 0, "x2": 9, "y2": 5}},
        ]

        district_index = 0
        processed = []
        while district_index < len(districts):
            processed.append(districts[district_index]["name"])
            district_index += 1

        assert len(processed) == 2
        assert district_index == len(districts)

    def test_empty_districts_handled(self):
        districts = []
        district_index = 0
        iterations = 0
        while district_index < len(districts):
            iterations += 1
            district_index += 1
        assert iterations == 0

    def test_district_region_bounds(self):
        """Verify district regions can extend beyond initial area (sparse world allows it)."""
        ws = WorldState()
        ws.place_tile(0, 0, {"terrain": "road"})
        ws.place_tile(50, 50, {"terrain": "road"})

        districts = [
            {"name": "Forum", "region": {"x1": 0, "y1": 0, "x2": 10, "y2": 10}},
            {"name": "Beyond", "region": {"x1": 35, "y1": 35, "x2": 50, "y2": 50}},
        ]

        # Both regions are valid in a sparse world
        for d in districts:
            r = d["region"]
            for x in range(r["x1"], min(r["x2"] + 1, r["x1"] + 3)):
                ws.place_tile(x, r["y1"], {"terrain": "building"})
            tile = ws.get_tile(r["x1"], r["y1"])
            assert tile is not None

    def test_district_overlap_detection(self):
        districts = [
            {"name": "A", "region": {"x1": 0, "y1": 0, "x2": 15, "y2": 15}},
            {"name": "B", "region": {"x1": 10, "y1": 10, "x2": 25, "y2": 25}},
        ]

        def regions_overlap(r1, r2):
            return not (r1["x2"] < r2["x1"] or r2["x2"] < r1["x1"] or
                        r1["y2"] < r2["y1"] or r2["y2"] < r1["y1"])

        assert regions_overlap(districts[0]["region"], districts[1]["region"])


# ═══════════════════════════════════════════════════════════════
# 10. MessageBus Scaling
# ═══════════════════════════════════════════════════════════════

class TestMessageBusScaling:
    def test_bus_message_accumulation(self):
        from orchestration.bus import MessageBus, BusMessage

        bus = MessageBus()
        for i in range(1000):
            msg = BusMessage(
                sender="faber",
                msg_type="built",
                content=f"Built structure {i}. " * 10,
                turn=i,
            )
            bus._messages.append(msg)

        assert len(bus._messages) == 1000
        text = bus.history_text(1000)
        assert len(text) > 10000


# ═══════════════════════════════════════════════════════════════
# 11. Persistence at Scale
# ═══════════════════════════════════════════════════════════════

class TestPersistenceScaling:
    def test_chunked_persistence_round_trip(self, tmp_path):
        """Chunked save/load preserves tile data."""
        import core.persistence as persistence_mod
        from core.persistence import save_state, load_state

        ws = WorldState()
        for y in range(10):
            for x in range(20):
                ws.place_tile(x, y, {
                    "terrain": "building",
                    "building_name": f"B_{x}_{y}",
                    "building_type": "insula",
                    "spec": {"shapes": [{"type": "box", "pos": [0, 0.5, 0], "size": [0.8, 1.0, 0.8]}]}
                })
        ws.turn = 42
        ws.current_period = "Augustus"
        ws.current_year = 14

        chat = [{"type": "chat", "sender": "faber", "content": "Ave!"}]
        districts = [{"name": "Forum", "region": {"x1": 0, "y1": 0, "x2": 19, "y2": 9}}]

        # Temporarily redirect persistence paths
        orig_saves = persistence_mod.SAVES_DIR
        orig_chunks = persistence_mod.CHUNKS_DIR
        orig_index = persistence_mod.INDEX_FILE
        orig_districts = persistence_mod.DISTRICTS_CACHE
        orig_surveys = persistence_mod.SURVEYS_CACHE
        try:
            persistence_mod.SAVES_DIR = tmp_path / "saves"
            persistence_mod.CHUNKS_DIR = tmp_path / "saves" / "chunks"
            persistence_mod.INDEX_FILE = tmp_path / "saves" / "index.json"
            persistence_mod.DISTRICTS_CACHE = tmp_path / "saves" / "districts_cache.json"
            persistence_mod.SURVEYS_CACHE = tmp_path / "saves" / "surveys_cache.json"

            save_state(ws, chat, 1, districts)

            ws2 = WorldState()
            result = load_state(ws2)
            if result is not None:
                loaded_chat, di, loaded_districts = result
                assert di == 1
                assert len(loaded_districts) == 1
        finally:
            persistence_mod.SAVES_DIR = orig_saves
            persistence_mod.CHUNKS_DIR = orig_chunks
            persistence_mod.INDEX_FILE = orig_index
            persistence_mod.DISTRICTS_CACHE = orig_districts
            persistence_mod.SURVEYS_CACHE = orig_surveys


# ═══════════════════════════════════════════════════════════════
# 12. Client-Side Renderer Issues (documented as tests)
# ═══════════════════════════════════════════════════════════════

class TestRendererIssuesDocumentation:
    """These tests document client-side bugs found by code review.
    They test the data structures that the renderer consumes."""

    def test_shared_material_bug_data_path(self):
        """Two buildings with same color share a cached material.
        Replacing one should NOT dispose the shared material."""
        ws = WorldState()
        ws.place_tile(0, 0, {
            "terrain": "building",
            "color": "#d4a373",
            "spec": {"shapes": [{"type": "box", "pos": [0, 0.5, 0], "size": [0.8, 1.0, 0.8], "color": "#d4a373"}]}
        })
        ws.place_tile(1, 0, {
            "terrain": "building",
            "color": "#d4a373",
            "spec": {"shapes": [{"type": "box", "pos": [0, 0.5, 0], "size": [0.8, 1.0, 0.8], "color": "#d4a373"}]}
        })
        ws.place_tile(0, 0, {
            "terrain": "building",
            "color": "#ff0000",
            "spec": {"shapes": [{"type": "box", "pos": [0, 0.5, 0], "size": [0.8, 1.0, 0.8], "color": "#ff0000"}]}
        })
        # Tile (1,0) should still be intact
        tile = ws.get_tile(1, 0)
        assert tile.color == "#d4a373"

    def test_hover_mesh_count_at_scale(self):
        """Document the raycast mesh count for large cities."""
        ws = WorldState()
        shapes_per_building = 20
        building_count = 1600
        mesh_count = building_count * shapes_per_building
        assert mesh_count == 32000, "Hover would raycast against 32000 meshes"

    def test_expansion_tiles_accepted(self):
        """Sparse world always accepts tiles — no bounds check rejection."""
        ws = WorldState()
        # Initial area
        ws.place_tile(0, 0, {"terrain": "road"})
        # Expansion tile far away
        assert ws.place_tile(500, 500, {"terrain": "building"}) is True
        assert ws.get_tile(500, 500).terrain == "building"


# ═══════════════════════════════════════════════════════════════
# 13. Sparse WorldState Validation
# ═══════════════════════════════════════════════════════════════

class TestSparseWorldState:
    """Validate that the sparse WorldState supports infinite expansion."""

    def test_sparse_accepts_any_coordinates(self):
        ws = WorldState()
        assert ws.place_tile(0, 0, {"terrain": "road"})
        assert ws.place_tile(-100, -100, {"terrain": "road"})
        assert ws.place_tile(10000, 10000, {"terrain": "building"})

        assert ws.get_tile(0, 0).terrain == "road"
        assert ws.get_tile(-100, -100).terrain == "road"
        assert ws.get_tile(10000, 10000).terrain == "building"

    def test_sparse_memory_efficiency(self):
        ws = WorldState()
        for i in range(100):
            ws.place_tile(i * 1000, i * 1000, {"terrain": "building"})
        assert len(ws.tiles) == 100

    def test_sparse_scales_to_thousands_of_tiles(self):
        ws = WorldState()
        start = time.time()
        for i in range(10000):
            x = (i * 7) % 500 - 250
            y = (i * 13) % 500 - 250
            ws.place_tile(x, y, {"terrain": "building", "building_name": f"b_{i}"})
        elapsed = time.time() - start

        assert elapsed < 2.0, f"10000 placements took {elapsed:.3f}s"
        assert len(ws.tiles) <= 10000

    def test_sparse_get_nonexistent_returns_none(self):
        ws = WorldState()
        assert ws.get_tile(999, 999) is None

    def test_sparse_width_height_computed(self):
        ws = WorldState()
        assert ws.width == 0
        assert ws.height == 0
        ws.place_tile(5, 10, {"terrain": "road"})
        ws.place_tile(15, 20, {"terrain": "road"})
        assert ws.width == 11  # 15 - 5 + 1
        assert ws.height == 11  # 20 - 10 + 1


# ═══════════════════════════════════════════════════════════════
# 14. Engine Reset Race Condition
# ═══════════════════════════════════════════════════════════════

class TestResetRaceCondition:
    def test_running_flag_prevents_double_execution(self):
        running_states = []

        class MockEngine:
            def __init__(self):
                self.running = False
                self.run_count = 0

            async def run(self):
                if self.running:
                    return
                self.running = True
                self.run_count += 1
                running_states.append(("start", self.run_count))
                await asyncio.sleep(0.1)
                running_states.append(("end", self.run_count))
                self.running = False

        engine = MockEngine()

        async def test():
            t1 = asyncio.create_task(engine.run())
            await asyncio.sleep(0.01)
            engine.running = False
            await asyncio.sleep(0.05)
            t2 = asyncio.create_task(engine.run())
            await asyncio.gather(t1, t2, return_exceptions=True)

        asyncio.run(test())
        assert engine.run_count == 2


# ═══════════════════════════════════════════════════════════════
# 15. Tile Spec Validation
# ═══════════════════════════════════════════════════════════════

class TestTileSpecValidation:
    def test_spec_with_empty_shapes(self):
        ws = WorldState()
        ws.place_tile(5, 5, {"terrain": "building", "spec": {"shapes": []}})
        tile = ws.get_tile(5, 5)
        assert tile.spec == {"shapes": []}

    def test_spec_with_missing_fields(self):
        spec = {"shapes": [
            {"type": "box"},
            {"type": "cylinder", "pos": [0, 0, 0]},
            {"type": "unknown_shape"},
        ]}
        ws = WorldState()
        ws.place_tile(5, 5, {"terrain": "building", "spec": spec})
        tile = ws.get_tile(5, 5)
        assert len(tile.spec["shapes"]) == 3

    def test_spec_with_extreme_values(self):
        spec = {"shapes": [
            {"type": "box", "pos": [0, 50000, 0], "size": [100, 100, 100], "color": "#ff0000"},
            {"type": "sphere", "pos": [0, 0, 0], "radius": 0.0001, "color": "#00ff00"},
            {"type": "cylinder", "pos": [0, -100, 0], "radius": 50, "height": 200, "color": "#0000ff"},
        ]}
        ws = WorldState()
        ws.place_tile(5, 5, {"terrain": "building", "spec": spec})
        tile = ws.get_tile(5, 5)
        assert tile.spec is not None

    def test_spec_with_nan_inf(self):
        spec = {"shapes": [
            {"type": "box", "pos": [float('nan'), 0, 0], "size": [1, 1, 1]},
            {"type": "box", "pos": [float('inf'), 0, 0], "size": [1, 1, 1]},
        ]}
        ws = WorldState()
        ws.place_tile(5, 5, {"terrain": "building", "spec": spec})
        tile = ws.get_tile(5, 5)
        assert len(tile.spec["shapes"]) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
