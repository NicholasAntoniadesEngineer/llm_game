"""CityBlueprint — persistent city-wide coherence data created during planning.

Stores topography, roads, materials, district character, and sightlines.
Provides compact context strings for injection into Urbanista prompts.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from world.state import WorldState

logger = logging.getLogger("eternal.blueprint")


@dataclass
class CityBlueprint:
    """Persistent city-wide coherence data created during planning."""

    # Topography
    elevation_map: dict[tuple[int, int], float] = field(default_factory=dict)
    hills: list[dict] = field(default_factory=list)   # {name, cx, cy, radius, peak}
    water: list[dict] = field(default_factory=list)    # {name, type, points:[(x,y),...]}

    # Roads
    roads: list[dict] = field(default_factory=list)    # {name, type:via|vicus|semita, points, width}
    gates: list[dict] = field(default_factory=list)    # {name, x, y}

    # Materials & style
    primary_stone: str = "travertine"
    secondary_stone: str = "tufa"
    brick_type: str = "brick"
    roof_material: str = "terracotta"

    # Districts
    district_characters: dict[str, dict] = field(default_factory=dict)
    # e.g., {"Forum": {"wealth": 10, "height_range": [2,4], "style": "monumental"}}

    # Sightlines
    vista_corridors: list[dict] = field(default_factory=list)
    # {road_name, terminus_building, points}

    # ── Topography ────────────────────────────────────────────────────

    def populate_elevation(self, world: WorldState) -> int:
        """Set tile elevation from hills data using gaussian falloff.

        Returns number of tiles updated.
        """
        from world.roads import compute_elevation

        if not self.hills:
            return 0

        updated = 0
        # Compute bounding box of influence from all hills
        for (x, y), tile in list(world.tiles.items()):
            elev = compute_elevation(self.hills, x, y)
            if elev > 0.01:
                self.elevation_map[(x, y)] = elev
                tile.elevation = round(elev, 3)
                updated += 1

        logger.info("Elevation populated: %d tiles from %d hills", updated, len(self.hills))
        return updated

    def apply_elevation_to_world(self, world: WorldState) -> int:
        """Apply pre-computed elevation_map to any new tiles in the world.

        Called after road rasterization or district expansion to ensure
        newly placed tiles inherit terrain elevation.
        Returns number of tiles updated.
        """
        from world.roads import compute_elevation

        if not self.hills:
            return 0

        updated = 0
        for (x, y), tile in world.tiles.items():
            if (x, y) not in self.elevation_map:
                elev = compute_elevation(self.hills, x, y)
                if elev > 0.01:
                    self.elevation_map[(x, y)] = elev
                    tile.elevation = round(elev, 3)
                    updated += 1
        return updated

    # ── Roads ─────────────────────────────────────────────────────────

    def rasterize_roads(self, world: WorldState) -> int:
        """Place road tiles along road waypoints. Returns count of tiles placed."""
        from world.roads import rasterize_road

        total = 0
        for road in self.roads:
            count = rasterize_road(world, road, self)
            total += count
            logger.info("Road '%s' (%s): %d tiles", road.get("name", "?"), road.get("type", "?"), count)
        logger.info("Total road tiles placed: %d from %d roads", total, len(self.roads))
        return total

    # ── Context Strings ───────────────────────────────────────────────

    def get_neighborhood_context(self, world: WorldState, x: int, y: int, radius: int = 3) -> str:
        """Compact neighbor context for Urbanista prompt injection.

        Format: 'NB:N:Temple(temple,marble,h12);E:Via Sacra(road);S:empty'
        Scans cardinal and intercardinal directions for the nearest non-empty tile.
        """
        directions = {
            "N": (0, -1), "NE": (1, -1), "E": (1, 0), "SE": (1, 1),
            "S": (0, 1), "SW": (-1, 1), "W": (-1, 0), "NW": (-1, -1),
        }
        parts = []
        for label, (dx, dy) in directions.items():
            found = None
            for dist in range(1, radius + 1):
                nx, ny = x + dx * dist, y + dy * dist
                tile = world.get_tile(nx, ny)
                if tile and tile.terrain != "empty":
                    name = tile.building_name or tile.terrain
                    btype = tile.building_type or tile.terrain
                    # Truncate name to 20 chars for token efficiency
                    short_name = name[:20] if len(name) > 20 else name
                    h = f"h{tile.elevation:.0f}" if tile.elevation > 0.1 else ""
                    color = tile.color if tile.color and tile.color != "#c2b280" else ""
                    detail = f"{btype}"
                    if color:
                        detail += f",{color}"
                    if h:
                        detail += f",{h}"
                    found = f"{label}:{short_name}({detail})"
                    break
            if found:
                parts.append(found)
            # Omit empty directions entirely for token savings

        if not parts:
            return ""
        return "NB:" + ";".join(parts)

    def get_district_context(self, district_name: str) -> str:
        """Compact district character context.

        Format: 'DC:wealth=10;hRange=2-4;style=monumental;mats=marble,travertine'
        """
        char = self.district_characters.get(district_name)
        if not char:
            return ""
        parts = []
        if "wealth" in char:
            parts.append(f"wealth={char['wealth']}")
        hr = char.get("height_range")
        if hr and isinstance(hr, (list, tuple)) and len(hr) == 2:
            parts.append(f"hRange={hr[0]}-{hr[1]}")
        if "style" in char:
            parts.append(f"style={char['style']}")
        mats = char.get("materials")
        if mats and isinstance(mats, (list, tuple)):
            parts.append(f"mats={','.join(str(m) for m in mats)}")
        if "density" in char:
            parts.append(f"density={char['density']}")

        if not parts:
            return ""
        return "DC:" + ";".join(parts)

    def get_material_palette_context(self) -> str:
        """Compact material palette.

        Format: 'MAT:pri=travertine;sec=tufa;brick=brick;roof=terracotta'
        """
        return (
            f"MAT:pri={self.primary_stone};sec={self.secondary_stone};"
            f"brick={self.brick_type};roof={self.roof_material}"
        )

    def get_facing_context(self, world: WorldState, x: int, y: int, radius: int = 3) -> str:
        """Determine what major features are in each cardinal direction.

        Format: 'FACE:N=road,E=forum,S=hill'
        """
        directions = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}
        facings = {}
        for label, (dx, dy) in directions.items():
            for dist in range(1, radius + 1):
                nx, ny = x + dx * dist, y + dy * dist
                tile = world.get_tile(nx, ny)
                if tile and tile.terrain != "empty":
                    facings[label] = tile.terrain
                    break
                # Check if there's a hill nearby
                elev = self.elevation_map.get((nx, ny), 0.0)
                if elev > 1.0:
                    facings[label] = "hill"
                    break

        if not facings:
            return ""
        return "FACE:" + ",".join(f"{k}={v}" for k, v in facings.items())

    def build_context_line(self, world: WorldState, x: int, y: int, district_name: str) -> str:
        """Build the full compact context line for an Urbanista prompt.

        Format: 'CTX|NB:...|DC:...|MAT:...|FACE:...'
        Returns empty string if no context is available.
        """
        parts = []
        nb = self.get_neighborhood_context(world, x, y)
        if nb:
            parts.append(nb)
        dc = self.get_district_context(district_name)
        if dc:
            parts.append(dc)
        mat = self.get_material_palette_context()
        if mat:
            parts.append(mat)
        face = self.get_facing_context(world, x, y)
        if face:
            parts.append(face)

        if not parts:
            return ""
        return "CTX|" + "|".join(parts)

    # ── Serialization ─────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize for persistence."""
        # Convert tuple keys to string keys for JSON
        elev_map = {f"{x},{y}": v for (x, y), v in self.elevation_map.items()}
        return {
            "elevation_map": elev_map,
            "hills": self.hills,
            "water": self.water,
            "roads": self.roads,
            "gates": self.gates,
            "primary_stone": self.primary_stone,
            "secondary_stone": self.secondary_stone,
            "brick_type": self.brick_type,
            "roof_material": self.roof_material,
            "district_characters": self.district_characters,
            "vista_corridors": self.vista_corridors,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CityBlueprint:
        """Deserialize from persistence."""
        bp = cls()
        # Restore elevation_map with tuple keys
        raw_elev = d.get("elevation_map", {})
        for key, v in raw_elev.items():
            parts = key.split(",")
            if len(parts) == 2:
                try:
                    bp.elevation_map[(int(parts[0]), int(parts[1]))] = float(v)
                except (ValueError, TypeError):
                    pass
        bp.hills = d.get("hills", [])
        bp.water = d.get("water", [])
        bp.roads = d.get("roads", [])
        bp.gates = d.get("gates", [])
        bp.primary_stone = d.get("primary_stone", "travertine")
        bp.secondary_stone = d.get("secondary_stone", "tufa")
        bp.brick_type = d.get("brick_type", "brick")
        bp.roof_material = d.get("roof_material", "terracotta")
        bp.district_characters = d.get("district_characters", {})
        bp.vista_corridors = d.get("vista_corridors", [])
        return bp

    @classmethod
    def from_known_city(cls, city_data: dict) -> CityBlueprint:
        """Create a blueprint from known_cities.json entry."""
        bp = cls()
        bp.hills = city_data.get("hills", [])
        bp.water = city_data.get("water", [])
        bp.roads = city_data.get("roads", [])

        mats = city_data.get("default_materials", {})
        if mats:
            bp.primary_stone = mats.get("primary_stone", bp.primary_stone)
            bp.secondary_stone = mats.get("secondary_stone", bp.secondary_stone)
            bp.brick_type = mats.get("brick_type", bp.brick_type)
            bp.roof_material = mats.get("roof_material", bp.roof_material)

        # Extract landmarks as vista terminuses
        landmarks = city_data.get("landmarks", {})
        for lname, ldata in landmarks.items():
            if "cx" in ldata and "cy" in ldata:
                bp.gates.append({"name": lname, "x": ldata["cx"], "y": ldata["cy"]})

        return bp

    @classmethod
    def from_districts(cls, districts: list[dict]) -> CityBlueprint:
        """Create a minimal blueprint from discovered districts (no known city data).

        Extracts elevation, terrain notes, and district character from the planner output.
        """
        bp = cls()
        for d in districts:
            name = d.get("name", "")
            elev = d.get("elevation", 0.0)
            desc = d.get("description", "")

            # Infer basic district character from description
            char: dict = {}
            desc_lower = desc.lower()
            if any(w in desc_lower for w in ("monumental", "sacred", "temple", "imperial")):
                char["style"] = "monumental"
                char["wealth"] = 9
            elif any(w in desc_lower for w in ("market", "commerce", "trade", "mercantile")):
                char["style"] = "commercial"
                char["wealth"] = 6
            elif any(w in desc_lower for w in ("residential", "insula", "domus", "housing")):
                char["style"] = "residential"
                char["wealth"] = 4
            elif any(w in desc_lower for w in ("military", "barracks", "fortress", "wall")):
                char["style"] = "military"
                char["wealth"] = 5
            elif any(w in desc_lower for w in ("garden", "park", "grove")):
                char["style"] = "garden"
                char["wealth"] = 7

            if elev > 0.4:
                char["height_range"] = [2, 4]
            elif elev > 0.2:
                char["height_range"] = [1, 3]
            else:
                char["height_range"] = [1, 2]

            if char:
                bp.district_characters[name] = char

        return bp
