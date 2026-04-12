"""CityBlueprint — persistent city-wide coherence data created during planning.

Stores topography, roads, materials, district character, and sightlines.
Provides compact context strings for injection into Urbanista prompts.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, List

from core.config import Config
from core.terrain_system import terrain_analyzer
from world.roads import compute_elevation, smooth_elevation_max_gradient

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

    def elevation_at(self, x: int, y: int) -> float:
        """Authoritative elevation at a tile; prefers smoothed ``elevation_map`` when present."""
        if (x, y) in self.elevation_map:
            return float(self.elevation_map[(x, y)])
        if not self.hills:
            return 0.0
        return float(compute_elevation(self.hills, x, y))

    def _recompute_smoothed_elevation_for_world(
        self,
        world: "WorldState",
        *,
        system_configuration: Config,
    ) -> dict[tuple[int, int], float]:
        """Rebuild ``elevation_map`` and return smoothed elevations for placed tiles.

        Uses a dirty-chunk + halo subset when the world is large and ``_dirty_chunks`` is
        non-empty; otherwise recomputes all occupied tiles (full pass).
        """
        if not self.hills:
            return {}

        tile_keys = set(world.tiles.keys())
        dirty = getattr(world, "_dirty_chunks", None) or set()
        incremental_threshold = system_configuration.blueprint_incremental_tile_threshold
        use_full = (not dirty) or (len(tile_keys) < incremental_threshold)

        if use_full:
            raw_coords = tile_keys
        else:
            inner: set[tuple[int, int]] = set()
            for ck in dirty:
                inner |= world.chunk_tile_coords(ck)
            if not inner:
                use_full = True
                raw_coords = tile_keys
            else:
                expanded = set(inner)
                for _ in range(system_configuration.blueprint_halo_expand_iterations):
                    nxt = set(expanded)
                    for tx, ty in expanded:
                        for ddx, ddy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            p = (tx + ddx, ty + ddy)
                            if p in world.tiles:
                                nxt.add(p)
                    expanded = nxt
                raw_coords = expanded

        raw: dict[tuple[int, int], float] = {}
        for (x, y) in raw_coords:
            raw[(x, y)] = float(compute_elevation(self.hills, x, y))

        smoothed = smooth_elevation_max_gradient(
            raw,
            system_configuration.terrain.maximum_gradient_value,
            system_configuration.terrain.gradient_iterations_count,
        )
        if use_full:
            self.elevation_map.clear()
        for k, v in smoothed.items():
            self.elevation_map[k] = v
        return smoothed

    def populate_elevation(self, world: WorldState, *, system_configuration: Config) -> int:
        """Set tile elevation from hills (Gaussian), then bound slope between neighbors.

        Returns number of tiles updated.
        """
        smoothed = self._recompute_smoothed_elevation_for_world(
            world, system_configuration=system_configuration
        )
        if not smoothed:
            return 0

        updated = 0
        for (x, y), elev in smoothed.items():
            tile = world.get_tile(x, y)
            if not tile:
                continue
            tile.elevation = round(elev, 3)

            neighbors = self._get_neighbor_elevations(x, y, world)
            slope, aspect = terrain_analyzer.calculate_slope(elev, neighbors)
            roughness = terrain_analyzer.calculate_roughness([elev] + neighbors)

            moisture_val = tile.moisture
            if moisture_val is None:
                moisture_val = 0.5
            temperature_val = tile.temperature
            if temperature_val is None:
                temperature_val = 20.0

            terrain_type = terrain_analyzer.classify_terrain(
                elev,
                slope,
                neighbors,
                moisture=moisture_val,
                temperature=temperature_val,
                roughness=roughness,
            )

            soil_type = tile.soil_type or "loam"
            stability = terrain_analyzer.assess_stability(terrain_type, slope, soil_type, moisture_val)

            tile.terrain_type = terrain_type.value
            tile.slope = slope
            tile.aspect = aspect
            tile.roughness = roughness
            tile.stability = stability

            updated += 1

        logger.info(
            "Elevation populated (max_gradient=%s, iterations=%s): %d tiles, %d hills",
            system_configuration.terrain.maximum_gradient_value,
            system_configuration.terrain.gradient_iterations_count,
            updated,
            len(self.hills),
        )
        return updated

    def _get_neighbor_elevations(self, x: int, y: int, world: WorldState) -> List[float]:
        """Get elevations of neighboring tiles for terrain analysis."""
        neighbors = []
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                if dx == 0 and dy == 0:
                    continue
                neighbor_tile = world.get_tile(x + dx, y + dy)
                if neighbor_tile:
                    neighbors.append(neighbor_tile.elevation)
                else:
                    nx, ny = x + dx, y + dy
                    neighbors.append(float(self.elevation_at(nx, ny)))
        return neighbors

    def apply_elevation_to_world(self, world: WorldState, *, system_configuration: Config) -> int:
        """Recompute smoothed elevations for every placed tile (e.g. after expansion).

        Returns number of tiles updated.
        """
        smoothed = self._recompute_smoothed_elevation_for_world(
            world, system_configuration=system_configuration
        )
        if not smoothed:
            return 0

        updated = 0
        for (x, y), elev in smoothed.items():
            tile = world.get_tile(x, y)
            if not tile:
                continue
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

    def get_adaptive_foundation(self, building_type: str, x: int, y: int, world: WorldState) -> dict[str, Any]:
        """Generate adaptive foundation based on terrain, climate, and building type.

        Returns foundation specification with height, material, and adaptations.
        """
        # Get local terrain characteristics
        local_elevation = self.elevation_map.get((x, y), 0.0)
        slope = self._calculate_local_slope(x, y)

        # Determine terrain type
        terrain_type = self._classify_terrain_at(x, y, world)

        # Get climate context
        climate = self._determine_climate_context(x, y)

        # Base foundation specification
        foundation = {
            "type": "adaptive",
            "height": 0.0,
            "material": "stone",
            "adaptations": [],
            "terrain_type": terrain_type,
            "climate": climate,
            "slope": slope
        }

        # Terrain-specific adaptations
        if terrain_type in ["hills", "mountain"] or slope > 0.3:
            foundation["adaptations"].append("retaining_walls")
            foundation["height"] = max(foundation["height"], 0.2 + slope * 0.3)
            foundation["material"] = "stone"

        if terrain_type in ["water", "marsh", "swamp"]:
            foundation["adaptations"].extend(["stilts", "waterproofing"])
            foundation["height"] = max(foundation["height"], 0.8)
            foundation["material"] = "wood"

        if terrain_type == "sand" or climate == "desert":
            foundation["adaptations"].append("thermal_mass")
            foundation["material"] = "sandstone"

        # Climate-specific adaptations
        if climate == "tropical":
            foundation["adaptations"].extend(["ventilation", "termite_resistant"])
            foundation["material"] = "concrete" if foundation["material"] == "stone" else foundation["material"]

        elif climate == "arctic":
            foundation["adaptations"].extend(["insulation", "frost_protection"])
            foundation["height"] = max(foundation["height"], 0.3)
            foundation["material"] = "stone"

        elif climate == "mountain":
            foundation["adaptations"].append("avalanche_protection")
            foundation["material"] = "stone"

        # Building type specific adaptations
        if building_type in ["temple", "monument", "palace"]:
            foundation["adaptations"].append("elevated_platform")
            foundation["height"] = max(foundation["height"], 0.4)

        elif building_type in ["warehouse", "barracks"]:
            foundation["adaptations"].append("load_bearing")
            foundation["material"] = "stone"

        elif building_type in ["thermae", "aqueduct"]:
            foundation["adaptations"].extend(["waterproofing", "drainage"])
            foundation["material"] = "concrete"

        # Ensure minimum foundation height
        foundation["height"] = max(foundation["height"], 0.1)

        return foundation

    def _calculate_local_slope(self, x: int, y: int) -> float:
        """Calculate the local terrain slope at a position."""
        elevations = []
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                elev = self.elevation_map.get((x + dx, y + dy), 0.0)
                elevations.append(elev)

        if len(elevations) < 3:
            return 0.0

        # Calculate standard deviation as slope measure
        mean = sum(elevations) / len(elevations)
        variance = sum((e - mean) ** 2 for e in elevations) / len(elevations)
        return math.sqrt(variance)  # Standard deviation as slope measure

    def _classify_terrain_at(self, x: int, y: int, world: WorldState) -> str:
        """Classify the terrain type at a specific location."""
        # Check immediate tile
        tile = world.get_tile(x, y)
        if tile and tile.terrain != "empty":
            return tile.terrain

        # Check elevation-based terrain
        elevation = self.elevation_map.get((x, y), 0.0)
        if elevation > 2.0:
            return "mountain"
        elif elevation > 1.0:
            return "hills"
        elif elevation < -0.5:
            return "water"

        # Check nearby tiles for terrain influence
        nearby_terrain = []
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                if dx == 0 and dy == 0:
                    continue
                nearby_tile = world.get_tile(x + dx, y + dy)
                if nearby_tile and nearby_tile.terrain not in ["empty", "road"]:
                    nearby_terrain.append(nearby_tile.terrain)

        # Return most common nearby terrain or default
        if nearby_terrain:
            from collections import Counter
            most_common = Counter(nearby_terrain).most_common(1)[0][0]
            return most_common

        return "grass"  # Default terrain

    def _determine_climate_context(self, x: int, y: int) -> str:
        """Determine the climate context for a location."""
        # Simple climate determination based on position and elevation
        elevation = self.elevation_map.get((x, y), 0.0)

        if elevation > 3.0:
            return "mountain"
        elif elevation > 1.5:
            return "temperate"
        elif x > 50:  # East side = potentially different climate
            return "desert"
        elif y < 25:  # North side = cooler
            return "temperate"
        else:
            return "temperate"  # Default

    def apply_terrain_modifications(
        self, building_spec: dict[str, Any], x: int, y: int, world: WorldState
    ) -> dict[str, Any]:
        """Apply terrain-based modifications to building specifications."""
        modified_spec = building_spec.copy()

        # Get adaptive foundation
        foundation = self.get_adaptive_foundation(
            building_spec.get("building_type", "building"),
            x, y, world
        )

        # Add foundation component if not present
        has_foundation = any(comp.get("stack_role") == "foundation" for comp in modified_spec.get("components", []))
        if not has_foundation and foundation["height"] > 0.1:
            foundation_component = {
                "type": "podium",
                "height": foundation["height"],
                "color": foundation["material"],
                "stack_role": "foundation",
                "adaptations": foundation["adaptations"]
            }
            modified_spec["components"].insert(0, foundation_component)

        # Apply terrain-specific building modifications
        terrain_type = foundation["terrain_type"]
        climate = foundation["climate"]

        # Modify building based on terrain constraints
        if terrain_type in ["water", "marsh"]:
            # Buildings over water need special considerations
            modified_spec["water_adapted"] = True

        if terrain_type == "mountain" or foundation["slope"] > 0.5:
            # Steep terrain buildings need terracing
            modified_spec["terraced"] = True

        # Climate-specific material adaptations
        if climate == "desert":
            # Desert buildings use heat-resistant materials
            modified_spec["climate_materials"] = ["sandstone", "adobe", "terracotta"]
        elif climate == "arctic":
            # Arctic buildings need insulation
            modified_spec["insulated"] = True

        return modified_spec

    def get_geography_context(self) -> str:
        """Compact geography summary for expansion prompts (< 50 tokens).

        Format: 'GEO:hills=Name(cx,cy,rR,hP),...;water=Name(type,[x0,y0]->[x1,y1])'
        Returns empty string if no geography data exists.
        """
        parts = []
        if self.hills:
            hill_strs = []
            for h in self.hills[:4]:  # Cap at 4 hills for token budget
                name = h.get("name", "hill")
                # Truncate name to 12 chars
                name = name[:12] if len(name) > 12 else name
                cx, cy = h.get("cx", 0), h.get("cy", 0)
                r = h.get("radius", 1)
                p = h.get("peak", 1.0)
                hill_strs.append(f"{name}({cx},{cy},r{r},h{p})")
            parts.append("hills=" + ",".join(hill_strs))
        if self.water:
            water_strs = []
            for w in self.water[:3]:  # Cap at 3 water features
                name = w.get("name", "water")
                name = name[:12] if len(name) > 12 else name
                wtype = w.get("type", "river")
                pts = w.get("points", [])
                if pts and len(pts) >= 2:
                    p0, p1 = pts[0], pts[-1]
                    # Points may be [x,y] lists or dicts
                    if isinstance(p0, (list, tuple)):
                        water_strs.append(f"{name}({wtype},[{p0[0]},{p0[1]}]->[{p1[0]},{p1[1]}])")
                    elif isinstance(p0, dict):
                        water_strs.append(f"{name}({wtype},[{p0.get('x',0)},{p0.get('y',0)}]->[{p1.get('x',0)},{p1.get('y',0)}])")
                    else:
                        water_strs.append(f"{name}({wtype})")
                else:
                    water_strs.append(f"{name}({wtype})")
            parts.append("water=" + ",".join(water_strs))
        if not parts:
            return ""
        return "GEO:" + ";".join(parts)

    def is_water_region(self, x1: int, y1: int, x2: int, y2: int, threshold: float = 0.5) -> bool:
        """Check if a region is mostly water based on water feature proximity.

        Returns True if more than `threshold` fraction of tiles are near water.
        Uses a simple distance check against water feature polylines.
        """
        if not self.water:
            return False

        total_tiles = max(1, (x2 - x1 + 1) * (y2 - y1 + 1))
        water_tiles = 0
        water_radius = 2  # tiles within 2 of a water polyline count as "water"

        for x in range(x1, x2 + 1):
            for y in range(y1, y2 + 1):
                for w in self.water:
                    pts = w.get("points", [])
                    for pt in pts:
                        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                            px, py = pt[0], pt[1]
                        elif isinstance(pt, dict):
                            px, py = pt.get("x", 0), pt.get("y", 0)
                        else:
                            continue
                        if abs(x - px) <= water_radius and abs(y - py) <= water_radius:
                            water_tiles += 1
                            break
                    else:
                        continue
                    break  # Already counted this tile

        return (water_tiles / total_tiles) > threshold

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
