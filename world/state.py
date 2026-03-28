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

    def place_tile(self, x: int, y: int, data: dict) -> bool:
        """Place or update a tile. Returns False if out of bounds."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False

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
        self.build_log.append({"turn": self.turn, "x": x, "y": y, **data})
        return True

    def get_tile(self, x: int, y: int) -> Tile | None:
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.grid[y][x]
        return None

    def get_region_summary(self, x1: int, y1: int, x2: int, y2: int) -> str:
        """Text summary of a region for agent context."""
        lines = []
        for y in range(max(0, y1), min(self.height, y2 + 1)):
            for x in range(max(0, x1), min(self.width, x2 + 1)):
                tile = self.grid[y][x]
                if tile.terrain != "empty":
                    name = tile.building_name or tile.terrain
                    lines.append(f"  ({x},{y}): {name}")
        return "\n".join(lines) if lines else "  (empty region)"

    def to_dict(self) -> dict:
        """Full serialization for WebSocket initial state."""
        return {
            "type": "world_state",
            "width": self.width,
            "height": self.height,
            "turn": self.turn,
            "period": self.current_period,
            "year": self.current_year,
            "grid": [
                [self.grid[y][x].to_dict() for x in range(self.width)]
                for y in range(self.height)
            ],
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
