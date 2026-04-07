"""WorldState — sparse, unbounded tile map for infinite world generation."""

from world.tiles import Tile, TERRAIN_COLORS, BUILDING_ICONS, TERRAIN_ICONS
from core.config import CHUNK_SIZE


class WorldState:
    """Sparse tile storage with no fixed bounds. World grows as tiles are placed."""

    def __init__(self):
        self.tiles: dict[tuple[int, int], Tile] = {}
        self.min_x: int = 0
        self.max_x: int = 0
        self.min_y: int = 0
        self.max_y: int = 0
        self.current_period: str = ""
        self.current_year: int = 0
        self.turn: int = 0
        self.build_log: list[dict] = []
        self._dirty_chunks: set[tuple[int, int]] = set()

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

    def clear(self):
        """Remove all tiles and reset bounds."""
        self.tiles.clear()
        self.min_x = 0
        self.max_x = 0
        self.min_y = 0
        self.max_y = 0
        self.turn = 0
        self.build_log.clear()
        self._dirty_chunks.clear()

    def place_tile(self, x: int, y: int, data: dict) -> bool:
        """Place or update a tile. World expands to fit — never rejects."""
        elev = data.get("elevation")
        if isinstance(elev, (int, float)):
            data = dict(data)
            data["elevation"] = max(-5.0, min(float(elev), 30.0))

        tile = self.tiles.get((x, y))
        if tile is None:
            tile = Tile(x=x, y=y)
            self.tiles[(x, y)] = tile

        for key, value in data.items():
            if key in ("x", "y"):
                continue
            if hasattr(tile, key) and value is not None:
                setattr(tile, key, value)

        # Apply default color/icon if not specified
        if "color" not in data or data.get("color") is None:
            terrain = data.get("terrain", tile.terrain)
            tile.color = TERRAIN_COLORS[terrain] if terrain in TERRAIN_COLORS else "#c2b280"
        if "icon" not in data or data.get("icon") is None:
            btype = data.get("building_type", tile.building_type)
            terrain = data.get("terrain", tile.terrain)
            if btype and btype in BUILDING_ICONS:
                tile.icon = BUILDING_ICONS[btype]
            elif terrain in TERRAIN_ICONS:
                tile.icon = TERRAIN_ICONS[terrain]

        tile.turn = self.turn

        # Expand world bounds
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

        # Track dirty chunk for persistence
        self._dirty_chunks.add((x // CHUNK_SIZE, y // CHUNK_SIZE))

        self.build_log.append({"turn": self.turn, "x": x, "y": y, **data})
        return True

    def get_tile(self, x: int, y: int) -> Tile | None:
        return self.tiles.get((x, y))

    def get_region_summary(self, x1: int, y1: int, x2: int, y2: int,
                           max_tiles: int = 40) -> str:
        """Text summary of occupied tiles in a region for agent context."""
        entries: list[str] = []
        for (tx, ty), tile in self.tiles.items():
            if x1 <= tx <= x2 and y1 <= ty <= y2 and tile.terrain != "empty":
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
        return [tile.to_dict() for tile in self.tiles.values()
                if tile.terrain != "empty"]

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
        }

    def tiles_since(self, since_turn: int) -> list[dict]:
        """Get tiles changed since a given turn."""
        return [tile.to_dict() for tile in self.tiles.values()
                if tile.turn >= since_turn and tile.terrain != "empty"]
