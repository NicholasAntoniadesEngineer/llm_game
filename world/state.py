"""WorldState — sparse, unbounded tile map for infinite world generation."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from world.tiles import Tile

if TYPE_CHECKING:
    from core.config import Config


class WorldState:
    """Sparse tile storage with no fixed bounds. World grows as tiles are placed."""

    def __init__(self, *, chunk_size_tiles: int, system_configuration: "Config"):
        if chunk_size_tiles < 1:
            raise ValueError("chunk_size_tiles must be >= 1")
        self.system_configuration = system_configuration
        self.chunk_size_tiles = chunk_size_tiles
        self.tiles: dict[tuple[int, int], Tile] = {}
        self.min_x: int = 0
        self.max_x: int = 0
        self.min_y: int = 0
        self.max_y: int = 0
        self.current_period: str = ""
        self.current_year: int = 0
        self.turn: int = 0
        self.build_log: deque[dict] = deque(
            maxlen=int(system_configuration.world_build_log_trim_keep_entries)
        )
        self._dirty_chunks: set[tuple[int, int]] = set()
        self._tiles_by_chunk: dict[tuple[int, int], set[tuple[int, int]]] = {}

    def _default_terrain_color_hex(self, terrain: str) -> str:
        td = self.system_configuration.terrain.terrain_defaults_dictionary.get(terrain)
        if isinstance(td, dict) and td.get("color"):
            return str(td["color"])
        extra = self.system_configuration.terrain_type_display_colors_extra_dictionary.get(terrain)
        if isinstance(extra, str) and extra.strip():
            return extra.strip()
        return self.system_configuration.procedural_terrain_fallback_hex_color

    def _default_icon_for_tile(self, building_type: str | None, terrain: str) -> str:
        if building_type and building_type in self.system_configuration.building_type_display_icons_dictionary:
            return self.system_configuration.building_type_display_icons_dictionary[building_type]
        if terrain in self.system_configuration.terrain_display_icons_dictionary:
            return self.system_configuration.terrain_display_icons_dictionary[terrain]
        return ""

    def _chunk_coord_for_tile(self, x: int, y: int) -> tuple[int, int]:
        cs = self.chunk_size_tiles
        return (x // cs, y // cs)

    def rebuild_chunk_tile_index(self) -> int:
        """Rebuild ``_tiles_by_chunk`` from ``tiles`` (e.g. after load). Returns indexed non-empty count."""
        self._tiles_by_chunk.clear()
        indexed_non_empty_tile_count = 0
        for (tx, ty), tile in self.tiles.items():
            if tile.terrain == "empty":
                continue
            ck = self._chunk_coord_for_tile(tx, ty)
            self._tiles_by_chunk.setdefault(ck, set()).add((tx, ty))
            indexed_non_empty_tile_count += 1
        return indexed_non_empty_tile_count

    def chunk_tile_coords(self, ck: tuple[int, int]) -> set[tuple[int, int]]:
        """Tile coordinates in chunk ``ck`` that are tracked as non-empty."""
        return set(self._tiles_by_chunk.get(ck, ()))

    def chunk_keys_with_tiles(self) -> set[tuple[int, int]]:
        """All chunk coordinates that currently contain at least one non-empty tile."""
        return {ck for ck, coords in self._tiles_by_chunk.items() if coords}

    def peek_dirty_chunks(self) -> set[tuple[int, int]]:
        """Snapshot of chunk coordinates marked dirty since last elevation pass (copy for readers)."""
        return set(self._dirty_chunks)

    def _sync_chunk_index_for_tile(self, x: int, y: int, tile: Tile) -> None:
        ck = self._chunk_coord_for_tile(x, y)
        st = self._tiles_by_chunk.setdefault(ck, set())
        if tile.terrain == "empty":
            st.discard((x, y))
            if not st:
                del self._tiles_by_chunk[ck]
        else:
            st.add((x, y))

    @property
    def width(self) -> int:
        if not self.tiles:
            return 0
        return self.max_x - self.min_x + 1

    @property
    def height(self) -> int:
        if not self.tiles:
            return 0
        return self.max_y - self.min_y + 1

    def clear(self) -> int:
        """Remove all tiles and reset bounds. Returns 1 when world is cleared."""
        self.tiles.clear()
        self.min_x = 0
        self.max_x = 0
        self.min_y = 0
        self.max_y = 0
        self.turn = 0
        self.build_log.clear()
        self._dirty_chunks.clear()
        self._tiles_by_chunk.clear()
        return 1

    def place_tile(self, x: int, y: int, data: dict) -> bool:
        """Place or update a tile. World expands to fit — never rejects."""
        elev_min = self.system_configuration.world_place_tile_min_elevation
        elev_max = self.system_configuration.grid.maximum_elevation_value
        elev = data.get("elevation")
        if isinstance(elev, (int, float)):
            data = dict(data)
            data["elevation"] = max(float(elev_min), min(float(elev), float(elev_max)))

        tile = self.tiles.get((x, y))
        if tile is None:
            tile = Tile(x=x, y=y)
            self.tiles[(x, y)] = tile

        tile.apply_placement_payload(data)

        if "color" not in data or data.get("color") is None:
            terrain = data.get("terrain", tile.terrain)
            tile.color = self._default_terrain_color_hex(terrain)
        if "icon" not in data or data.get("icon") is None:
            btype = data.get("building_type", tile.building_type)
            terrain = data.get("terrain", tile.terrain)
            tile.icon = self._default_icon_for_tile(
                str(btype) if btype else None,
                str(terrain) if terrain else "empty",
            )

        tile.turn = self.turn

        if not self.tiles or len(self.tiles) == 1:
            self.min_x = x
            self.max_x = x
            self.min_y = y
            self.max_y = y
        else:
            self.min_x = min(self.min_x, x)
            self.max_x = max(self.max_x, x)
            self.min_y = min(self.min_y, y)
            self.max_y = max(self.max_y, y)

        self._dirty_chunks.add(self._chunk_coord_for_tile(x, y))
        self._sync_chunk_index_for_tile(x, y, tile)

        self.build_log.append({"turn": self.turn, "x": x, "y": y, **data})
        return True

    def get_tile(self, x: int, y: int) -> Tile | None:
        return self.tiles.get((x, y))

    def get_region_summary(self, x1: int, y1: int, x2: int, y2: int, max_tiles: int = 40) -> str:
        """Text summary of occupied tiles in a region for agent context."""
        entries: list[str] = []
        x_lo, x_hi = (x1, x2) if x1 <= x2 else (x2, x1)
        y_lo, y_hi = (y1, y2) if y1 <= y2 else (y2, y1)
        cs = self.chunk_size_tiles
        cx0, cx1 = x_lo // cs, x_hi // cs
        cy0, cy1 = y_lo // cs, y_hi // cs
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                for (tx, ty) in self.chunk_tile_coords((cx, cy)):
                    if not (x_lo <= tx <= x_hi and y_lo <= ty <= y_hi):
                        continue
                    tile = self.tiles.get((tx, ty))
                    if not tile or tile.terrain == "empty":
                        continue
                    name = tile.building_name or tile.terrain
                    entries.append(f"  ({tx},{ty}): {name}")

        if not entries:
            return "  (empty region)"

        total = len(entries)
        if total <= max_tiles:
            return "\n".join(entries)

        step = total / max_tiles
        sampled = [entries[int(i * step)] for i in range(max_tiles)]
        sampled.append(f"  (showing {max_tiles} of {total} tiles)")
        return "\n".join(sampled)

    def occupied_tile_dicts(self) -> list[dict]:
        """Return list of to_dict() for all non-empty tiles."""
        return [tile.to_dict() for tile in self.tiles.values() if tile.terrain != "empty"]

    def to_dict(self) -> dict:
        """Full serialization for WebSocket initial state (sparse format)."""
        tiles = self.occupied_tile_dicts()
        return {
            "type": "world_state",
            "width": self.width,
            "height": self.height,
            "min_x": self.min_x,
            "min_y": self.min_y,
            "turn": self.turn,
            "period": self.current_period,
            "year": self.current_year,
            "tiles": tiles,
            "chunk_size": self.chunk_size_tiles,
        }

    def tiles_since(self, since_turn: int) -> list[dict]:
        """Get tiles changed since a given turn."""
        return [
            tile.to_dict()
            for tile in self.tiles.values()
            if tile.turn >= since_turn and tile.terrain != "empty"
        ]
