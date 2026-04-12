"""Tile data model and building/terrain catalogs."""

from dataclasses import dataclass
from typing import Optional

# Fields always included in serialized output (even when default/None).
_ALWAYS_SERIALIZE = ("x", "y", "terrain", "elevation", "color", "icon", "turn")


@dataclass(slots=True)
class Tile:
    x: int
    y: int
    terrain: str = "empty"
    elevation: float = 0.0  # Height in world units (0 = sea level, positive = hills)
    building_name: Optional[str] = None
    building_type: Optional[str] = None
    period: Optional[str] = None
    description: Optional[str] = None
    historical_note: Optional[str] = None
    color: str = "#c2b280"
    icon: str = ""
    placed_by: Optional[str] = None
    turn: int = 0
    scene: Optional[str] = None
    spec: Optional[dict] = None  # AI-generated building spec (dimensions, features, colors)
    # Terrain analysis (blueprint / elevation pass; optional on sparsely populated tiles)
    terrain_type: Optional[str] = None
    slope: Optional[float] = None
    aspect: Optional[float] = None
    roughness: Optional[float] = None
    stability: Optional[float] = None
    soil_type: Optional[str] = None
    moisture: Optional[float] = None
    temperature: Optional[float] = None

    def apply_placement_payload(self, data: dict) -> None:
        """Set fields from ``data``; rejects keys that are not ``Tile`` slots (except x/y)."""
        allowed = frozenset(self.__slots__) - {"x", "y"}
        for key, value in data.items():
            if key in ("x", "y"):
                continue
            if key not in allowed:
                raise ValueError(f"Unknown tile placement field: {key!r}")
            if value is not None:
                setattr(self, key, value)

    def to_dict(self) -> dict:
        d: dict = {}
        for k in self.__slots__:
            v = getattr(self, k)
            if v is not None or k in _ALWAYS_SERIALIZE:
                d[k] = v
        return d
