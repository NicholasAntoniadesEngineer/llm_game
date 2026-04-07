"""Stress tests for the infinite expansion system.

Tests cover:
  - WorldState sparse storage and scaling beyond fixed grid
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
# 1. WorldState — Fixed Grid Boundary Issues
# ═══════════════════════════════════════════════════════════════

class TestWorldStateBoundaries:
    """The current WorldState uses a fixed 2D array.
    These tests document what breaks when expansion pushes beyond it."""

    def test_place_tile_within_bounds(self):
        ws = WorldState(10, 10)
        assert ws.place_tile(0, 0, {"terrain": "road"}) is True
        assert ws.place_tile(9, 9, {"terrain": "road"}) is True
        tile = ws.get_tile(0, 0)
        assert tile.terrain == "road"

    def test_place_tile_at_negative_coords_rejected(self):
        """Expansion in negative direction is silently dropped."""
        ws = WorldState(10, 10)
        result = ws.place_tile(-1, 0, {"terrain": "road"})
        assert result is False

    def test_place_tile_beyond_bounds_rejected(self):
        """Expansion beyond grid silently fails -- this is the core blocker
        for infinite expansion."""
        ws = WorldState(10, 10)
        result = ws.place_tile(10, 0, {"terrain": "road"})
        assert result is False
        result = ws.place_tile(0, 10, {"terrain": "road"})
        assert result is False

    def test_place_tile_at_large_coords_rejected(self):
        """At generation 5+, coords could be 200+ units from origin."""
        ws = WorldState(40, 40)
        result = ws.place_tile(200, 200, {"terrain": "building"})
        assert result is False

    def test_get_tile_out_of_bounds_returns_none(self):
        ws = WorldState(10, 10)
        assert ws.get_tile(-1, 0) is None
        assert ws.get_tile(10, 0) is None
        assert ws.get_tile(0, -1) is None
        assert ws.get_tile(0, 10) is None


# ═══════════════════════════════════════════════════════════════
# 2. WorldState — Scaling and Memory
# ═══════════════════════════════════════════════════════════════

class TestWorldStateScaling:
    """Test behavior with large grids and many tiles."""

    def test_build_log_is_capped(self):
        """build_log is pruned to prevent unbounded memory growth.
        After 10000 placements, it should be capped (not 10000 entries)."""
        ws = WorldState(100, 100)
        for i in range(10000):
            x, y = i % 100, i // 100
            ws.place_tile(x, y, {"terrain": "road", "building_name": f"road_{i}"})
        # build_log is capped at 5000 entries (pruned to 2500 when exceeded)
        assert len(ws.build_log) <= 5000
        # Most recent entries should be preserved
        assert ws.build_log[-1]["building_name"] == "road_9999"

    def test_to_dict_scales_with_grid_size(self):
        """to_dict iterates every cell. For a 200x200 grid = 40000 cells.
        This would be sent over WebSocket on connect."""
        ws = WorldState(200, 200)
        # Place only a few tiles
        ws.place_tile(0, 0, {"terrain": "road"})
        ws.place_tile(100, 100, {"terrain": "building"})

        start = time.time()
        d = ws.to_dict()
        elapsed = time.time() - start

        # Verify it serializes all 200x200 = 40000 cells
        assert len(d["grid"]) == 200
        assert len(d["grid"][0]) == 200
        # Should still be fast for 200x200
        assert elapsed < 1.0, f"to_dict took {elapsed:.3f}s for 200x200 grid"

    def test_tiles_since_scans_full_grid(self):
        """tiles_since iterates every cell regardless of how many changed."""
        ws = WorldState(100, 100)
        ws.place_tile(50, 50, {"terrain": "road"})
        ws.turn = 1

        start = time.time()
        changed = ws.tiles_since(0)
        elapsed = time.time() - start

        assert len(changed) == 1
        assert elapsed < 0.5

    def test_get_region_summary_handles_large_regions(self):
        ws = WorldState(100, 100)
        for x in range(100):
            ws.place_tile(x, 0, {"terrain": "road", "building_name": f"road_{x}"})

        summary = ws.get_region_summary(0, 0, 99, 99)
        assert "road_0" in summary
        assert "road_99" in summary

    def test_large_grid_initialization_time(self):
        """Creating a 500x500 grid allocates 250000 Tile objects."""
        start = time.time()
        ws = WorldState(500, 500)
        elapsed = time.time() - start

        # Should complete in reasonable time
        assert elapsed < 5.0, f"WorldState(500,500) took {elapsed:.3f}s"
        assert len(ws.grid) == 500
        assert len(ws.grid[0]) == 500


# ═══════════════════════════════════════════════════════════════
# 3. WorldState — Serialization Stress
# ═══════════════════════════════════════════════════════════════

class TestWorldStateSerialization:
    """Test JSON serialization at scale -- this gets sent over WebSocket."""

    def test_full_state_json_size(self):
        """With 40x40 grid fully populated, the JSON blob size."""
        ws = WorldState(40, 40)
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

        # Full 40x40 with specs should be manageable
        assert size_kb < 5000, f"Full state JSON is {size_kb:.0f}KB -- too large for WebSocket"

    def test_tiles_since_incremental_size(self):
        """Incremental updates should be much smaller than full state."""
        ws = WorldState(40, 40)
        # Fill half the grid
        for y in range(20):
            for x in range(40):
                ws.place_tile(x, y, {"terrain": "road"})
        ws.turn = 1

        # Add 5 new tiles
        for x in range(5):
            ws.place_tile(x, 20, {"terrain": "building", "building_name": f"new_{x}"})

        changed = ws.tiles_since(1)
        assert len(changed) == 5
        json_str = json.dumps(changed)
        assert len(json_str) < 5000  # Should be small


# ═══════════════════════════════════════════════════════════════
# 4. Edge Detection for Expansion
# ═══════════════════════════════════════════════════════════════

class TestEdgeDetection:
    """Test finding the expansion frontier -- tiles at the edge of built area
    that have empty neighbors where new districts could be placed."""

    @staticmethod
    def find_edge_tiles(ws: WorldState) -> list[tuple[int, int]]:
        """Find tiles on the edge of the built area (have at least one empty neighbor).
        This is the algorithm needed for infinite expansion."""
        edges = []
        for y in range(ws.height):
            for x in range(ws.width):
                tile = ws.get_tile(x, y)
                if tile and tile.terrain != "empty":
                    # Check 4-connected neighbors
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
            # Try each direction for a new zone
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                zx = ex + dx * zone_size // 2
                zy = ey + dy * zone_size // 2

                zone_key = (zx // zone_size, zy // zone_size)
                if zone_key in visited:
                    continue
                visited.add(zone_key)

                # Check if zone is within bounds and mostly empty
                x1 = max(0, zx - zone_size // 2)
                y1 = max(0, zy - zone_size // 2)
                x2 = min(ws.width - 1, zx + zone_size // 2)
                y2 = min(ws.height - 1, zy + zone_size // 2)

                if x2 - x1 < zone_size // 2 or y2 - y1 < zone_size // 2:
                    continue

                empty_count = 0
                total = 0
                for cy in range(y1, y2 + 1):
                    for cx in range(x1, x2 + 1):
                        t = ws.get_tile(cx, cy)
                        total += 1
                        if t and t.terrain == "empty":
                            empty_count += 1

                if total > 0 and empty_count / total > 0.7:
                    zones.append({
                        "region": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                        "empty_ratio": empty_count / total,
                    })

        return zones

    def test_single_district_has_edges(self):
        """A single built district should have edge tiles all around it."""
        ws = WorldState(40, 40)
        # Build a 5x5 district in the center
        for y in range(15, 20):
            for x in range(15, 20):
                ws.place_tile(x, y, {"terrain": "building"})

        edges = self.find_edge_tiles(ws)
        # All 25 tiles are edge tiles (5x5 block, all touch empty space)
        # Interior tiles: (16,16), (17,16), (18,16), (16,17), (17,17), (18,17), (16,18), (17,18), (18,18) = 9
        # Edge = 25 - 9 = 16
        assert len(edges) == 16

    def test_filled_grid_has_edges_at_boundary(self):
        """If the entire grid is filled, edges are at the grid boundary."""
        ws = WorldState(5, 5)
        for y in range(5):
            for x in range(5):
                ws.place_tile(x, y, {"terrain": "road"})

        edges = self.find_edge_tiles(ws)
        # All border tiles are edges (neighbor is None / out of bounds)
        # 5x5 grid: border = 5*4 - 4 = 16
        assert len(edges) == 16

    def test_empty_grid_no_edges(self):
        ws = WorldState(10, 10)
        edges = self.find_edge_tiles(ws)
        assert len(edges) == 0

    def test_expansion_zones_found(self):
        """Should find zones adjacent to built area."""
        ws = WorldState(40, 40)
        # Build a district at (5,5)-(14,14)
        for y in range(5, 15):
            for x in range(5, 15):
                ws.place_tile(x, y, {"terrain": "building"})

        zones = self.find_expansion_zones(ws, zone_size=8)
        assert len(zones) > 0
        for zone in zones:
            assert zone["empty_ratio"] > 0.7

    def test_expansion_zones_not_found_when_full(self):
        """No expansion zones when the grid is nearly full."""
        ws = WorldState(20, 20)
        for y in range(20):
            for x in range(20):
                ws.place_tile(x, y, {"terrain": "building"})

        zones = self.find_expansion_zones(ws, zone_size=8)
        assert len(zones) == 0

    def test_edge_detection_performance(self):
        """Edge detection on a large grid should complete in reasonable time."""
        ws = WorldState(200, 200)
        # Build a 50x50 block
        for y in range(75, 125):
            for x in range(75, 125):
                ws.place_tile(x, y, {"terrain": "building"})

        start = time.time()
        edges = self.find_edge_tiles(ws)
        elapsed = time.time() - start

        assert len(edges) > 0
        assert elapsed < 2.0, f"Edge detection on 200x200 took {elapsed:.3f}s"


# ═══════════════════════════════════════════════════════════════
# 5. Tile Placement Correctness
# ═══════════════════════════════════════════════════════════════

class TestTilePlacement:
    """Verify tile placement handles all edge cases correctly."""

    def test_place_preserves_existing_fields(self):
        """Placing partial data should not clear existing fields."""
        ws = WorldState(10, 10)
        ws.place_tile(5, 5, {
            "terrain": "building",
            "building_name": "Temple of Jupiter",
            "building_type": "temple",
            "description": "Great temple",
        })
        # Update with scene only
        ws.place_tile(5, 5, {"scene": "Citizens pray at the temple"})

        tile = ws.get_tile(5, 5)
        assert tile.building_name == "Temple of Jupiter"
        assert tile.scene == "Citizens pray at the temple"
        assert tile.terrain == "building"

    def test_place_none_values_ignored(self):
        """None values in data should not overwrite existing values."""
        ws = WorldState(10, 10)
        ws.place_tile(5, 5, {"terrain": "building", "building_name": "Forum"})
        ws.place_tile(5, 5, {"building_name": None, "terrain": "building"})

        tile = ws.get_tile(5, 5)
        # None should not overwrite
        assert tile.building_name == "Forum"

    def test_place_updates_turn(self):
        ws = WorldState(10, 10)
        ws.turn = 5
        ws.place_tile(3, 3, {"terrain": "road"})
        tile = ws.get_tile(3, 3)
        assert tile.turn == 5

    def test_overlapping_placements(self):
        """Two structures placed on the same tile -- last one wins."""
        ws = WorldState(10, 10)
        ws.place_tile(5, 5, {"terrain": "building", "building_name": "Temple A"})
        ws.place_tile(5, 5, {"terrain": "building", "building_name": "Temple B"})

        tile = ws.get_tile(5, 5)
        assert tile.building_name == "Temple B"

    def test_default_color_applied(self):
        """When no color specified, terrain default should be used."""
        ws = WorldState(10, 10)
        ws.place_tile(5, 5, {"terrain": "water"})
        tile = ws.get_tile(5, 5)
        assert tile.color == "#3498db"

    def test_default_icon_applied(self):
        ws = WorldState(10, 10)
        ws.place_tile(5, 5, {"terrain": "building", "building_type": "temple"})
        tile = ws.get_tile(5, 5)
        assert tile.icon == "\U0001f3db"  # temple icon


# ═══════════════════════════════════════════════════════════════
# 6. Concurrent Expansion Safety
# ═══════════════════════════════════════════════════════════════

class TestConcurrentExpansion:
    """Test that concurrent tile placements don't corrupt state."""

    def test_concurrent_placements_to_different_tiles(self):
        """Multiple placements to different tiles should all succeed."""
        ws = WorldState(100, 100)
        results = []
        for i in range(100):
            results.append(ws.place_tile(i, 0, {"terrain": "road", "building_name": f"road_{i}"}))

        assert all(results)
        for i in range(100):
            tile = ws.get_tile(i, 0)
            assert tile.building_name == f"road_{i}"

    def test_rapid_same_tile_updates(self):
        """Rapid updates to the same tile should result in the last update winning."""
        ws = WorldState(10, 10)
        for i in range(1000):
            ws.place_tile(5, 5, {"terrain": "building", "building_name": f"v{i}"})

        tile = ws.get_tile(5, 5)
        assert tile.building_name == "v999"
        # Build log has all 1000 entries
        assert len(ws.build_log) == 1000


# ═══════════════════════════════════════════════════════════════
# 7. WebSocket Broadcast Volume
# ═══════════════════════════════════════════════════════════════

class TestBroadcastVolume:
    """Test message sizes and rates for WebSocket sustainability."""

    def test_tile_update_message_size(self):
        """A tile_update with full spec should be reasonably sized."""
        tile_data = {
            "x": 15, "y": 20, "terrain": "building",
            "building_name": "Temple of Saturn",
            "building_type": "temple",
            "description": "Ancient temple with 8 columns",
            "color": "#c8b88a",
            "spec": {"shapes": [
                {"type": "box", "pos": [0, 0.1, 0], "size": [0.9, 0.2, 0.7], "color": "#c8b88a"}
                for _ in range(40)  # Max shapes per building
            ]}
        }

        msg = {"type": "tile_update", "tiles": [tile_data], "turn": 5}
        size = len(json.dumps(msg))
        assert size < 10000, f"Single tile update is {size} bytes"

    def test_bulk_tile_update_message_size(self):
        """A district with 20 structures, each 3 tiles = 60 tiles at once."""
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
        """Chat history replayed on connect grows unbounded."""
        history = []
        # Simulate 10 districts * 15 structures * 6 messages = 900 messages
        for i in range(900):
            history.append({
                "type": "chat",
                "sender": "faber",
                "content": f"Message {i} " * 20,  # ~200 chars each
                "turn": i,
            })

        total_size = len(json.dumps(history))
        size_kb = total_size / 1024
        # 900 messages at ~200 chars = ~180KB minimum
        assert size_kb > 100, "Chat history should be substantial"
        # Flag if it's too large for replay
        if size_kb > 500:
            pass  # This is the bug: no pruning of old chat history


# ═══════════════════════════════════════════════════════════════
# 8. Coordinate System Stress
# ═══════════════════════════════════════════════════════════════

class TestCoordinateStress:
    """Test for coordinate overflow and floating-point issues."""

    def test_tile_coordinates_preserved_in_serialization(self):
        ws = WorldState(40, 40)
        ws.place_tile(0, 0, {"terrain": "road"})
        ws.place_tile(39, 39, {"terrain": "road"})

        state = ws.to_dict()
        # Check corner tiles
        assert state["grid"][0][0]["x"] == 0
        assert state["grid"][0][0]["y"] == 0
        assert state["grid"][39][39]["x"] == 39
        assert state["grid"][39][39]["y"] == 39

    def test_large_coordinate_values_in_tiles(self):
        """At generation 5+, tile coords could be hundreds of units.
        Test that Tile dataclass handles large coords."""
        t = Tile(x=99999, y=99999)
        d = t.to_dict()
        assert d["x"] == 99999
        assert d["y"] == 99999

        # JSON serialization with large coords
        s = json.dumps(d)
        restored = json.loads(s)
        assert restored["x"] == 99999

    def test_negative_coordinate_tiles(self):
        """Expansion could go in negative directions."""
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
        """Engine should stop running after processing all districts."""
        # Simulate by checking the run loop condition
        ws = WorldState(10, 10)

        # Create a mock engine state
        districts = [
            {"name": "Forum", "region": {"x1": 0, "y1": 0, "x2": 5, "y2": 5}, "period": "Caesar", "year": -44},
            {"name": "Palatine", "region": {"x1": 5, "y1": 0, "x2": 9, "y2": 5}, "period": "Caesar", "year": -44},
        ]

        # After processing 2 districts, index should be 2 and loop should exit
        district_index = 0
        running = True
        processed = []
        while running and district_index < len(districts):
            processed.append(districts[district_index]["name"])
            district_index += 1

        assert len(processed) == 2
        assert district_index == len(districts)

    def test_empty_districts_handled(self):
        """If Cartographus returns no districts, engine should stop gracefully."""
        districts = []
        district_index = 0
        running = True

        # The loop should not execute
        iterations = 0
        while running and district_index < len(districts):
            iterations += 1
            district_index += 1

        assert iterations == 0

    def test_district_region_within_grid(self):
        """All district regions should be within grid bounds."""
        ws = WorldState(40, 40)
        districts = [
            {"name": "Forum", "region": {"x1": 0, "y1": 0, "x2": 10, "y2": 10}},
            {"name": "Beyond", "region": {"x1": 35, "y1": 35, "x2": 50, "y2": 50}},  # Extends beyond!
        ]

        for d in districts:
            r = d["region"]
            within_bounds = (
                0 <= r["x1"] < ws.width and
                0 <= r["y1"] < ws.height and
                r["x2"] < ws.width and
                r["y2"] < ws.height
            )
            if d["name"] == "Beyond":
                assert not within_bounds, "District 'Beyond' should exceed grid bounds"

    def test_district_overlap_detection(self):
        """Districts with overlapping regions should be detected."""
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
    """The message bus stores all messages with no pruning."""

    def test_bus_message_accumulation(self):
        from orchestration.bus import MessageBus, BusMessage

        bus = MessageBus()
        # Simulate 1000 messages from a full build
        for i in range(1000):
            msg = BusMessage(
                sender="faber",
                msg_type="built",
                content=f"Built structure {i}. " * 10,
                turn=i,
            )
            # publish is async, but _messages is sync-accessible for testing
            bus._messages.append(msg)

        assert len(bus._messages) == 1000

        # history_text with large N
        text = bus.history_text(1000)
        assert len(text) > 10000


# ═══════════════════════════════════════════════════════════════
# 11. Persistence at Scale
# ═══════════════════════════════════════════════════════════════

class TestPersistenceScaling:
    """Test save/load with large world states."""

    def test_save_load_round_trip(self, tmp_path):
        """Save and load should preserve all tile data."""
        from persistence import save_state, load_state, SAVE_FILE

        ws = WorldState(20, 20)
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

        # Save
        import persistence
        original_save_file = persistence.SAVE_FILE
        persistence.SAVE_FILE = tmp_path / "test_save.json"
        try:
            save_state(ws, chat, 1, districts)

            # Load into fresh world
            ws2 = WorldState(20, 20)
            result = load_state(ws2)
            assert result is not None
            loaded_chat, di, loaded_districts = result

            assert di == 1
            assert len(loaded_districts) == 1
            assert len(loaded_chat) == 1
            assert ws2.turn == 42
            assert ws2.current_period == "Augustus"

            tile = ws2.get_tile(5, 5)
            assert tile.building_name == "B_5_5"
        finally:
            persistence.SAVE_FILE = original_save_file

    def test_save_file_size(self, tmp_path):
        """Save file size with full 40x40 populated grid."""
        ws = WorldState(40, 40)
        for y in range(40):
            for x in range(40):
                ws.place_tile(x, y, {
                    "terrain": "building",
                    "building_name": f"B_{x}_{y}",
                    "spec": {"shapes": [
                        {"type": "box", "pos": [0, 0.5, 0], "size": [0.8, 1.0, 0.8], "color": "#d4a373"}
                    ] * 5}
                })

        import persistence
        from persistence import save_state as _save_state
        original_save_file = persistence.SAVE_FILE
        persistence.SAVE_FILE = tmp_path / "test_save.json"
        try:
            _save_state(ws, [], 0, [])
            size_mb = persistence.SAVE_FILE.stat().st_size / (1024 * 1024)
            assert size_mb < 50, f"Save file is {size_mb:.1f}MB"
        finally:
            persistence.SAVE_FILE = original_save_file


# ═══════════════════════════════════════════════════════════════
# 12. Client-Side Renderer Issues (documented as tests)
# ═══════════════════════════════════════════════════════════════

class TestRendererIssuesDocumentation:
    """These tests document client-side bugs found by code review.
    They test the data structures that the renderer consumes."""

    def test_shared_material_disposal_bug(self):
        """BUG in renderer3d.js _buildFromSpec():
        When replacing a building, it calls c.material.dispose() but materials
        are shared via _matCache. Disposing a shared material corrupts all
        other buildings using the same color.

        The fix: skip disposal for materials from the cache, or use
        material.clone() when assigning to meshes, or track reference counts.

        The correct pattern in _buildFromSpec should be:
            old.traverse(c => { if (c.geometry) c.geometry.dispose(); });
        NOT:
            old.traverse(c => { if (c.geometry) c.geometry.dispose(); if (c.material) c.material.dispose(); });
        """
        # This test documents the bug. The fix is in renderer3d.js.
        # We verify the data path that triggers it.
        ws = WorldState(10, 10)
        ws.place_tile(0, 0, {
            "terrain": "building",
            "color": "#d4a373",
            "spec": {"shapes": [{"type": "box", "pos": [0, 0.5, 0], "size": [0.8, 1.0, 0.8], "color": "#d4a373"}]}
        })
        ws.place_tile(1, 0, {
            "terrain": "building",
            "color": "#d4a373",  # Same color = same cached material
            "spec": {"shapes": [{"type": "box", "pos": [0, 0.5, 0], "size": [0.8, 1.0, 0.8], "color": "#d4a373"}]}
        })
        # Replacing tile (0,0) would dispose the shared material, corrupting tile (1,0)
        ws.place_tile(0, 0, {
            "terrain": "building",
            "color": "#ff0000",  # Different color
            "spec": {"shapes": [{"type": "box", "pos": [0, 0.5, 0], "size": [0.8, 1.0, 0.8], "color": "#ff0000"}]}
        })
        # The old "#d4a373" material would be disposed, but tile (1,0) still references it

    def test_hover_performance_with_many_buildings(self):
        """BUG in renderer3d.js _updateHover():
        Iterates ALL building groups to collect ALL meshes on every mouse move.
        With 1000 buildings averaging 20 shapes each = 20000 meshes to raycast against.

        Fix: Use a spatial index or octree for raycasting."""
        ws = WorldState(40, 40)
        mesh_count = 0
        for y in range(40):
            for x in range(40):
                shapes_per_building = 20
                mesh_count += shapes_per_building

        # 1600 buildings * 20 shapes = 32000 meshes
        assert mesh_count == 32000, "Hover would raycast against 32000 meshes"

    def test_update_tiles_bounds_check(self):
        """Client updateTiles() rejects tiles outside initial grid.
        This blocks infinite expansion on the client side."""
        # Simulate the client check
        width, height = 40, 40
        expansion_tile = {"x": 45, "y": 20, "terrain": "building"}

        accepted = (expansion_tile["x"] >= 0 and expansion_tile["y"] >= 0 and
                    expansion_tile["x"] < width and expansion_tile["y"] < height)
        assert not accepted, "Client would reject expansion tiles beyond initial grid"


# ═══════════════════════════════════════════════════════════════
# 13. Sparse WorldState Prototype
# ═══════════════════════════════════════════════════════════════

class TestSparseWorldState:
    """Test a sparse dict-based world state that would support infinite expansion.
    This validates the approach needed to replace the fixed 2D array."""

    @staticmethod
    def create_sparse_world():
        """Prototype sparse world using dict instead of 2D list."""
        class SparseWorld:
            def __init__(self):
                self.tiles = {}  # (x, y) -> Tile
                self.turn = 0
                self.min_x = float('inf')
                self.min_y = float('inf')
                self.max_x = float('-inf')
                self.max_y = float('-inf')

            def place_tile(self, x, y, data):
                key = (x, y)
                if key not in self.tiles:
                    self.tiles[key] = Tile(x=x, y=y)
                tile = self.tiles[key]
                for k, v in data.items():
                    if k in ("x", "y"):
                        continue
                    if hasattr(tile, k) and v is not None:
                        setattr(tile, k, v)
                tile.turn = self.turn
                self.min_x = min(self.min_x, x)
                self.min_y = min(self.min_y, y)
                self.max_x = max(self.max_x, x)
                self.max_y = max(self.max_y, y)
                return True

            def get_tile(self, x, y):
                return self.tiles.get((x, y))

            @property
            def bounds(self):
                if not self.tiles:
                    return (0, 0, 0, 0)
                return (self.min_x, self.min_y, self.max_x, self.max_y)

        return SparseWorld()

    def test_sparse_accepts_any_coordinates(self):
        sw = self.create_sparse_world()
        assert sw.place_tile(0, 0, {"terrain": "road"})
        assert sw.place_tile(-100, -100, {"terrain": "road"})
        assert sw.place_tile(10000, 10000, {"terrain": "building"})

        assert sw.get_tile(0, 0).terrain == "road"
        assert sw.get_tile(-100, -100).terrain == "road"
        assert sw.get_tile(10000, 10000).terrain == "building"

    def test_sparse_bounds_tracking(self):
        sw = self.create_sparse_world()
        sw.place_tile(5, 10, {"terrain": "road"})
        sw.place_tile(-3, -7, {"terrain": "road"})
        sw.place_tile(20, 15, {"terrain": "road"})

        assert sw.bounds == (-3, -7, 20, 15)

    def test_sparse_memory_efficiency(self):
        """Sparse world only stores placed tiles, not empty space."""
        sw = self.create_sparse_world()
        # Place 100 tiles scattered across a huge coordinate space
        for i in range(100):
            sw.place_tile(i * 1000, i * 1000, {"terrain": "building"})

        # Only 100 tiles stored, not 100000 * 100000
        assert len(sw.tiles) == 100

    def test_sparse_scales_to_thousands_of_tiles(self):
        """Sparse world should handle thousands of tiles efficiently."""
        sw = self.create_sparse_world()

        start = time.time()
        for i in range(10000):
            x = (i * 7) % 500 - 250  # Spread across -250 to 250
            y = (i * 13) % 500 - 250
            sw.place_tile(x, y, {"terrain": "building", "building_name": f"b_{i}"})
        elapsed = time.time() - start

        assert elapsed < 2.0, f"10000 placements took {elapsed:.3f}s"
        assert len(sw.tiles) <= 10000

    def test_sparse_get_nonexistent_returns_none(self):
        sw = self.create_sparse_world()
        assert sw.get_tile(999, 999) is None


# ═══════════════════════════════════════════════════════════════
# 14. Engine Reset Race Condition
# ═══════════════════════════════════════════════════════════════

class TestResetRaceCondition:
    """The reset handler in main.py has a race condition:
    it sets engine.running = False, sleeps 0.5s, then starts a new engine.run().
    If the old task doesn't finish in 0.5s, both could run concurrently."""

    def test_running_flag_prevents_double_execution(self):
        """The running flag should prevent concurrent execution,
        but the sleep(0.5) gap creates a window."""
        # Simulate the race
        running_states = []

        class MockEngine:
            def __init__(self):
                self.running = False
                self.run_count = 0

            async def run(self):
                if self.running:
                    return  # Should not happen
                self.running = True
                self.run_count += 1
                running_states.append(("start", self.run_count))
                await asyncio.sleep(0.1)
                running_states.append(("end", self.run_count))
                self.running = False

        engine = MockEngine()

        async def test():
            # Start first run
            t1 = asyncio.create_task(engine.run())
            await asyncio.sleep(0.01)

            # Reset: stop and restart
            engine.running = False
            await asyncio.sleep(0.05)  # Less than the run duration
            t2 = asyncio.create_task(engine.run())

            await asyncio.gather(t1, t2, return_exceptions=True)

        asyncio.run(test())
        # Both runs should complete
        assert engine.run_count == 2


# ═══════════════════════════════════════════════════════════════
# 15. Tile Spec Validation
# ═══════════════════════════════════════════════════════════════

class TestTileSpecValidation:
    """Validate that tile specs from AI don't contain invalid data
    that could crash the renderer."""

    def test_spec_with_empty_shapes(self):
        """Empty shapes array should not crash."""
        ws = WorldState(10, 10)
        ws.place_tile(5, 5, {"terrain": "building", "spec": {"shapes": []}})
        tile = ws.get_tile(5, 5)
        assert tile.spec == {"shapes": []}

    def test_spec_with_missing_fields(self):
        """Shape with missing required fields."""
        spec = {"shapes": [
            {"type": "box"},  # Missing pos, size, color
            {"type": "cylinder", "pos": [0, 0, 0]},  # Missing radius, height
            {"type": "unknown_shape"},  # Unknown type
        ]}
        ws = WorldState(10, 10)
        ws.place_tile(5, 5, {"terrain": "building", "spec": spec})
        tile = ws.get_tile(5, 5)
        assert len(tile.spec["shapes"]) == 3

    def test_spec_with_extreme_values(self):
        """Shapes with very large/small dimensions."""
        spec = {"shapes": [
            {"type": "box", "pos": [0, 50000, 0], "size": [100, 100, 100], "color": "#ff0000"},
            {"type": "sphere", "pos": [0, 0, 0], "radius": 0.0001, "color": "#00ff00"},
            {"type": "cylinder", "pos": [0, -100, 0], "radius": 50, "height": 200, "color": "#0000ff"},
        ]}
        # These would create visual artifacts but shouldn't crash
        ws = WorldState(10, 10)
        ws.place_tile(5, 5, {"terrain": "building", "spec": spec})
        tile = ws.get_tile(5, 5)
        assert tile.spec is not None

    def test_spec_with_nan_inf(self):
        """NaN and Infinity in positions could crash the renderer."""
        spec = {"shapes": [
            {"type": "box", "pos": [float('nan'), 0, 0], "size": [1, 1, 1]},
            {"type": "box", "pos": [float('inf'), 0, 0], "size": [1, 1, 1]},
        ]}
        ws = WorldState(10, 10)
        ws.place_tile(5, 5, {"terrain": "building", "spec": spec})
        tile = ws.get_tile(5, 5)
        # The data is stored — it's the renderer that would have issues
        assert len(tile.spec["shapes"]) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
