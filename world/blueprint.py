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
from core.errors import ConfigLoadError
from core.terrain_analysis import TerrainAnalysis
from orchestration.district_inference import infer_district_character_from_description
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

    # Materials & style (filled by ``from_config`` / ``from_dict``; empty only before init)
    primary_stone: str = ""
    secondary_stone: str = ""
    brick_type: str = ""
    roof_material: str = ""

    # Districts
    district_characters: dict[str, dict] = field(default_factory=dict)
    # e.g., {"Forum": {"wealth": 10, "height_range": [2,4], "style": "monumental"}}

    # Sightlines
    vista_corridors: list[dict] = field(default_factory=list)
    # {road_name, terminus_building, points}

    _water_adjacency_tile_cache: set[tuple[int, int]] | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_config(cls, system_configuration: Config) -> CityBlueprint:
        """New blueprint with material defaults from ``system_config.csv`` only."""
        return cls(
            primary_stone=system_configuration.blueprint_default_primary_stone_string,
            secondary_stone=system_configuration.blueprint_default_secondary_stone_string,
            brick_type=system_configuration.blueprint_default_brick_type_string,
            roof_material=system_configuration.blueprint_default_roof_material_string,
        )

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
        dirty = world.peek_dirty_chunks()
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

    def _apply_smoothed_elevation_to_world_tiles(
        self,
        world: WorldState,
        smoothed: dict[tuple[int, int], float],
        *,
        system_configuration: Config,
        update_full_terrain_analysis: bool,
    ) -> int:
        """Write smoothed elevations onto placed tiles; optionally recompute slope/type/stability."""
        updated = 0
        terrain_cfg = system_configuration.terrain
        threshold_dict = terrain_cfg.terrain_classification_thresholds_dictionary
        thresholds_for_analysis = {str(k): float(v) for k, v in threshold_dict.items()}
        for (x, y), elev in smoothed.items():
            tile = world.get_tile(x, y)
            if not tile:
                continue
            tile.elevation = round(elev, 3)
            if not update_full_terrain_analysis:
                updated += 1
                continue

            neighbors = self._get_neighbor_elevations(x, y, world)
            slope, aspect = TerrainAnalysis.calculate_slope(elev, neighbors)
            roughness = TerrainAnalysis.calculate_roughness([elev] + neighbors)

            moisture_val = tile.moisture
            if moisture_val is None:
                moisture_val = 0.5
            temperature_val = tile.temperature
            if temperature_val is None:
                temperature_val = 20.0

            terrain_type = TerrainAnalysis.classify_terrain(
                elev,
                slope,
                neighbors,
                classification_thresholds=thresholds_for_analysis,
                moisture=moisture_val,
                temperature=temperature_val,
                roughness=roughness,
            )

            soil_type = tile.soil_type or "loam"
            stability = TerrainAnalysis.assess_stability(
                terrain_type,
                slope,
                soil_type,
                moisture_val,
                classification_thresholds=thresholds_for_analysis,
                terrain_type_modifiers=terrain_cfg.terrain_stability_terrain_type_modifiers_dictionary,
                soil_type_modifiers=terrain_cfg.terrain_stability_soil_type_modifiers_dictionary,
            )

            tile.terrain_type = terrain_type.value
            tile.slope = slope
            tile.aspect = aspect
            tile.roughness = roughness
            tile.stability = stability

            updated += 1
        return updated

    def populate_elevation(self, world: WorldState, *, system_configuration: Config) -> int:
        """Set tile elevation from hills (Gaussian), then bound slope between neighbors.

        Returns number of tiles updated.
        """
        smoothed = self._recompute_smoothed_elevation_for_world(
            world, system_configuration=system_configuration
        )
        if not smoothed:
            return 0

        updated = self._apply_smoothed_elevation_to_world_tiles(
            world,
            smoothed,
            system_configuration=system_configuration,
            update_full_terrain_analysis=True,
        )

        logger.info(
            "Elevation populated (max_gradient=%s, iterations=%s): %d tiles, %d hills",
            system_configuration.terrain.maximum_gradient_value,
            system_configuration.terrain.gradient_iterations_count,
            updated,
            len(self.hills),
        )
        return updated

    def _get_neighbor_elevations(self, x: int, y: int, world: WorldState) -> List[float]:
        """Eight neighbor elevations in fixed order: NW, N, NE, W, E, SW, S, SE.

        Order must match ``TerrainAnalysis.calculate_slope`` (cardinals at indices 1,3,4,6).
        """
        neighbor_delta_order = (
            (-1, -1),
            (0, -1),
            (1, -1),
            (-1, 0),
            (1, 0),
            (-1, 1),
            (0, 1),
            (1, 1),
        )
        neighbors: List[float] = []
        for delta_x, delta_y in neighbor_delta_order:
            neighbor_tile = world.get_tile(x + delta_x, y + delta_y)
            if neighbor_tile:
                neighbors.append(neighbor_tile.elevation)
            else:
                neighbor_x = x + delta_x
                neighbor_y = y + delta_y
                neighbors.append(float(self.elevation_at(neighbor_x, neighbor_y)))
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

        return self._apply_smoothed_elevation_to_world_tiles(
            world,
            smoothed,
            system_configuration=system_configuration,
            update_full_terrain_analysis=False,
        )

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
                    default_empty_hex = world.system_configuration.terrain_type_display_colors_extra_dictionary.get(
                        "empty", ""
                    )
                    skip_color = (
                        str(default_empty_hex).strip().lower()
                        if isinstance(default_empty_hex, str) and default_empty_hex.strip()
                        else ""
                    )
                    color = (
                        tile.color
                        if tile.color
                        and (not skip_color or str(tile.color).strip().lower() != skip_color)
                        else ""
                    )
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
        climate = self._determine_climate_context(x, y, system_configuration=world.system_configuration)

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
        """Gradient slope magnitude at (x,y) consistent with ``TerrainAnalysis.calculate_slope``."""
        neighbor_delta_order = (
            (-1, -1),
            (0, -1),
            (1, -1),
            (-1, 0),
            (1, 0),
            (-1, 1),
            (0, 1),
            (1, 1),
        )
        center = float(self.elevation_map.get((x, y), 0.0))
        ring: List[float] = []
        for delta_x, delta_y in neighbor_delta_order:
            ring.append(float(self.elevation_map.get((x + delta_x, y + delta_y), 0.0)))
        slope_mag, _aspect = TerrainAnalysis.calculate_slope(center, ring)
        return float(slope_mag)

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

    def _determine_climate_context(self, x: int, y: int, *, system_configuration: Config) -> str:
        """Climate label from elevation and configurable grid zones (system_config.csv)."""
        elevation = self.elevation_map.get((x, y), 0.0)
        rules = system_configuration.blueprint_climate_determination_dictionary
        elev_mountain = float(rules.get("elevation_mountain_min", 3.0))
        elev_temperate = float(rules.get("elevation_temperate_min", 1.5))
        default_label = str(rules.get("default_label", "temperate"))

        if elevation > elev_mountain:
            return "mountain"
        if elevation > elev_temperate:
            return "temperate"

        zones = rules.get("grid_zones")
        if isinstance(zones, list):
            for zone in zones:
                if not isinstance(zone, dict):
                    continue
                x_min = int(zone.get("x_min", -10**9))
                x_max = int(zone.get("x_max", 10**9))
                y_min = int(zone.get("y_min", -10**9))
                y_max = int(zone.get("y_max", 10**9))
                if x_min <= x <= x_max and y_min <= y <= y_max:
                    return str(zone.get("label", default_label))
        return default_label

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

        components_list = modified_spec.setdefault("components", [])
        has_foundation = any(comp.get("stack_role") == "foundation" for comp in components_list)
        if not has_foundation and foundation["height"] > 0.1:
            foundation_component = {
                "type": "podium",
                "height": foundation["height"],
                "color": foundation["material"],
                "stack_role": "foundation",
                "adaptations": foundation["adaptations"]
            }
            components_list.insert(0, foundation_component)

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

    def reset_water_adjacency_cache(self) -> None:
        """Call when ``water`` waypoints change so ``is_water_region`` recomputes."""
        self._water_adjacency_tile_cache = None

    def _water_adjacency_tile_set(self) -> set[tuple[int, int]]:
        """Tiles within ``water_proximity_radius_tiles`` of any water polyline vertex (cached)."""
        if self._water_adjacency_tile_cache is not None:
            return self._water_adjacency_tile_cache
        water_proximity_radius_tiles = 2
        adjacent: set[tuple[int, int]] = set()
        for w in self.water:
            pts = w.get("points", [])
            for pt in pts:
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    px, py = int(pt[0]), int(pt[1])
                elif isinstance(pt, dict):
                    px, py = int(pt.get("x", 0)), int(pt.get("y", 0))
                else:
                    continue
                for dx in range(-water_proximity_radius_tiles, water_proximity_radius_tiles + 1):
                    for dy in range(-water_proximity_radius_tiles, water_proximity_radius_tiles + 1):
                        adjacent.add((px + dx, py + dy))
        self._water_adjacency_tile_cache = adjacent
        return adjacent

    def is_water_region(self, x1: int, y1: int, x2: int, y2: int, threshold: float = 0.5) -> bool:
        """Check if a region is mostly water based on water feature proximity.

        Returns True if more than `threshold` fraction of tiles are near water.
        Uses a simple distance check against water feature polylines.
        """
        if not self.water:
            return False

        total_tiles = max(1, (x2 - x1 + 1) * (y2 - y1 + 1))
        water_tiles = 0
        adj = self._water_adjacency_tile_set()

        for x in range(x1, x2 + 1):
            for y in range(y1, y2 + 1):
                if (x, y) in adj:
                    water_tiles += 1

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
    def from_dict(cls, d: dict, *, system_configuration: Config) -> CityBlueprint:
        """Deserialize from persistence; missing material keys use CSV defaults."""
        bp = cls.from_config(system_configuration)
        # Restore elevation_map with tuple keys
        raw_elev = d.get("elevation_map", {})
        for key, v in raw_elev.items():
            parts = str(key).split(",")
            if len(parts) == 2:
                try:
                    bp.elevation_map[(int(parts[0]), int(parts[1]))] = float(v)
                except (ValueError, TypeError) as elev_err:
                    logger.error(
                        "blueprint elevation_map invalid key=%r value=%r: %s",
                        key,
                        v,
                        elev_err,
                    )
                    raise ConfigLoadError(
                        f"Invalid elevation_map entry key={key!r} value={v!r}: {elev_err}"
                    ) from elev_err
        bp.hills = d.get("hills", [])
        bp.water = d.get("water", [])
        bp.reset_water_adjacency_cache()
        bp.roads = d.get("roads", [])
        bp.gates = d.get("gates", [])
        def _mat(key: str, fallback: str) -> str:
            raw = d.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
            return fallback

        bp.primary_stone = _mat("primary_stone", bp.primary_stone)
        bp.secondary_stone = _mat("secondary_stone", bp.secondary_stone)
        bp.brick_type = _mat("brick_type", bp.brick_type)
        bp.roof_material = _mat("roof_material", bp.roof_material)
        bp.district_characters = d.get("district_characters", {})
        bp.vista_corridors = d.get("vista_corridors", [])
        return bp

    @classmethod
    def from_known_city(cls, city_data: dict, *, system_configuration: Config) -> CityBlueprint:
        """Create a blueprint from known_cities.json entry."""
        bp = cls.from_config(system_configuration)
        bp.hills = city_data.get("hills", [])
        bp.water = city_data.get("water", [])
        bp.reset_water_adjacency_cache()
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
    def from_districts(cls, districts: list[dict], *, system_configuration: Config) -> CityBlueprint:
        """Create a minimal blueprint from discovered districts (no known city data).

        Extracts elevation, terrain notes, and district character from the planner output.
        """
        bp = cls.from_config(system_configuration)
        for d in districts:
            name = d.get("name", "")
            elev = d.get("elevation", 0.0)
            desc = d.get("description", "")
            char = infer_district_character_from_description(
                str(desc),
                elevation=float(elev) if elev is not None else 0.0,
            )
            if char:
                bp.district_characters[name] = char

        return bp
