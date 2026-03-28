"""Tile data model and building/terrain catalogs."""

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Tile:
    x: int
    y: int
    terrain: str = "empty"
    building_name: Optional[str] = None
    building_type: Optional[str] = None
    period: Optional[str] = None
    description: Optional[str] = None
    historical_note: Optional[str] = None
    color: str = "#c2b280"  # default sandy/dirt
    icon: str = ""
    placed_by: Optional[str] = None
    turn: int = 0
    scene: Optional[str] = None  # citizen flavor text

    def to_dict(self) -> dict:
        return asdict(self)


# Default colors for terrain types
TERRAIN_COLORS = {
    "empty": "#c2b280",
    "road": "#808080",
    "building": "#d4a373",
    "water": "#3498db",
    "garden": "#27ae60",
    "forum": "#f0e68c",
    "wall": "#5d4037",
}

# Default icons for building types
BUILDING_ICONS = {
    "temple": "🏛",
    "basilica": "🏛",
    "insula": "🏠",
    "domus": "🏡",
    "aqueduct": "🌉",
    "thermae": "♨️",
    "circus": "🏟",
    "amphitheater": "🏟",
    "market": "🏪",
    "gate": "⛩",
    "bridge": "🌉",
    "monument": "🗿",
    "taberna": "🍷",
    "warehouse": "📦",
}

TERRAIN_ICONS = {
    "road": "▪️",
    "garden": "🌿",
    "water": "🌊",
    "wall": "🧱",
    "forum": "⚖️",
}
