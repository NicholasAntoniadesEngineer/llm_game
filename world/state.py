"""WorldState — the shared 2D grid representing Ancient Rome."""

from world.tiles import Tile, TERRAIN_COLORS, BUILDING_ICONS, TERRAIN_ICONS


class WorldState:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.grid: list[list[Tile]] = [
            [Tile(x=x, y=y) for x in range(width)]
            for y in range(height)
        ]
        self.current_period: str = "Caesar"
        self.current_year: int = -44
        self.turn: int = 0
        self.build_log: list[dict] = []
        self._occupied: set[tuple[int, int]] = set()  # Track non-empty tiles for fast iteration

    def place_tile(self, x: int, y: int, data: dict) -> bool:
        """Place or update a tile. Returns False if out of bounds."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False

        elev = data.get("elevation")
        if isinstance(elev, (int, float)):
            data = dict(data)  # Don't mutate caller's dict
            data["elevation"] = max(-5.0, min(float(elev), 30.0))

        tile = self.grid[y][x]
        for key, value in data.items():
            if key in ("x", "y"):
                continue
            if hasattr(tile, key) and value is not None:
                setattr(tile, key, value)

        # Apply default color/icon if not specified
        if "color" not in data or data.get("color") is None:
            terrain = data.get("terrain", tile.terrain)
            tile.color = TERRAIN_COLORS.get(terrain, "#c2b280")
        if "icon" not in data or data.get("icon") is None:
            btype = data.get("building_type", tile.building_type)
            terrain = data.get("terrain", tile.terrain)
            if btype and btype in BUILDING_ICONS:
                tile.icon = BUILDING_ICONS[btype]
            elif terrain in TERRAIN_ICONS:
                tile.icon = TERRAIN_ICONS[terrain]

        tile.turn = self.turn
        if tile.terrain != "empty":
            self._occupied.add((x, y))
        self.build_log.append({"turn": self.turn, "x": x, "y": y, **data})
        return True

    def get_tile(self, x: int, y: int) -> Tile | None:
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.grid[y][x]
        return None

    def get_region_summary(self, x1: int, y1: int, x2: int, y2: int,
                           max_tiles: int = 40) -> str:
        """Text summary of a region for agent context.

        If there are more than *max_tiles* non-empty tiles, an evenly-spaced
        sample is returned so that survey prompts stay concise.
        """
        entries: list[str] = []
        for y in range(max(0, y1), min(self.height, y2 + 1)):
            for x in range(max(0, x1), min(self.width, x2 + 1)):
                tile = self.grid[y][x]
                if tile.terrain != "empty":
                    name = tile.building_name or tile.terrain
                    entries.append(f"  ({x},{y}): {name}")

        if not entries:
            return "  (empty region)"

        total = len(entries)
        if total <= max_tiles:
            return "\n".join(entries)

        # Even sampling across the full list
        step = total / max_tiles
        sampled = [entries[int(i * step)] for i in range(max_tiles)]
        sampled.append(f"  (showing {max_tiles} of {total} tiles)")
        return "\n".join(sampled)

    def occupied_tile_dicts(self) -> list[dict]:
        """Return list of to_dict() for all non-empty tiles (fast, uses _occupied set)."""
        return [self.grid[y][x].to_dict() for (x, y) in self._occupied
                if self.grid[y][x].terrain != "empty"]

    def to_dict(self) -> dict:
        """Full serialization for WebSocket initial state.

        Only non-empty tiles are included in ``tiles`` (sparse format).
        The client initialises an empty grid from width/height and patches
        the listed tiles on top — typically 90-95 % smaller than the old
        dense grid-of-grids layout.
        """
        tiles = self.occupied_tile_dicts()
        return {
            "type": "world_state",
            "width": self.width,
            "height": self.height,
            "turn": self.turn,
            "period": self.current_period,
            "year": self.current_year,
            "tiles": tiles,
        }

    def tiles_since(self, since_turn: int) -> list[dict]:
        """Get tiles changed since a given turn (for incremental updates)."""
        changed = []
        for y in range(self.height):
            for x in range(self.width):
                tile = self.grid[y][x]
                if tile.turn >= since_turn and tile.terrain != "empty":
                    changed.append(tile.to_dict())
        return changed
