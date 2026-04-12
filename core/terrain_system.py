"""Advanced Terrain and Environment System.

Provides sophisticated terrain generation, object placement, and environmental adaptation
for realistic 3D city rendering with proper object-terrain interaction.
"""

import math
import random
from typing import Dict, Any, List, Tuple, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

from .constants import (
    MAX_ELEVATION,
    MATERIAL_ROUGHNESS_LOW,
    MATERIAL_ROUGHNESS_MEDIUM,
    MATERIAL_ROUGHNESS_HIGH,
    WARM_MATERIALS,
    COOL_MATERIALS,
)


class TerrainType(Enum):
    """Comprehensive terrain classification system."""
    FLAT = "flat"
    GENTLE_SLOPE = "gentle_slope"
    STEEP_SLOPE = "steep_slope"
    CLIFF = "cliff"
    VALLEY = "valley"
    RIDGE = "ridge"
    PLATEAU = "plateau"
    DEPRESSION = "depression"
    WATER = "water"
    MARSH = "marsh"
    SAND = "sand"
    ROCK = "rock"
    FOREST = "forest"
    URBAN = "urban"


class ClimateType(Enum):
    """Climate classification for environmental adaptation."""
    TROPICAL = "tropical"
    TEMPERATE = "temperate"
    ARID = "arid"
    DESERT = "desert"
    MOUNTAIN = "mountain"
    ARCTIC = "arctic"
    MEDITERRANEAN = "mediterranean"
    CONTINENTAL = "continental"


@dataclass
class TerrainCell:
    """Represents a single terrain cell with comprehensive properties."""
    x: int
    y: int
    elevation: float = 0.0
    terrain_type: TerrainType = TerrainType.FLAT
    slope: float = 0.0
    aspect: float = 0.0  # Direction of slope (radians)
    roughness: float = 0.0
    moisture: float = 0.5
    temperature: float = 20.0
    soil_type: str = "loam"
    vegetation_density: float = 0.0
    climate: ClimateType = ClimateType.TEMPERATE

    # Derived properties
    stability: float = 1.0  # How stable the terrain is for building
    accessibility: float = 1.0  # How easy it is to access
    fertility: float = 0.5  # Agricultural potential
    resources: Dict[str, float] = field(default_factory=dict)

    # Environmental effects
    erosion_rate: float = 0.0
    weathering: float = 0.0
    contamination: float = 0.0


@dataclass
class ObjectPlacement:
    """Represents how an object should be placed on terrain."""
    position: Tuple[float, float, float]
    rotation: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    foundation_type: str = "standard"
    foundation_depth: float = 0.0
    adaptations: List[str] = field(default_factory=list)
    stability_score: float = 1.0
    accessibility_score: float = 1.0


class TerrainAnalysis:
    """Advanced terrain analysis and classification system."""

    @staticmethod
    def classify_terrain(
        elevation: float,
        slope: float,
        neighbors: List[float],
        moisture: float = 0.5,
        temperature: float = 20.0,
        roughness: float = 0.0,
    ) -> TerrainType:
        """Classify terrain type based on multiple factors."""

        # Water detection
        if elevation < -0.5:
            return TerrainType.WATER
        elif elevation < 0.0 and moisture > 0.8:
            return TerrainType.MARSH

        # Slope-based classification
        if slope > 1.0:  # Very steep
            if elevation > 10.0:
                return TerrainType.CLIFF
            else:
                return TerrainType.STEEP_SLOPE
        elif slope > 0.3:  # Moderately steep
            return TerrainType.GENTLE_SLOPE

        # Elevation-based classification
        if elevation > 15.0:
            return TerrainType.PLATEAU
        elif elevation < 2.0 and slope < 0.1:
            return TerrainType.VALLEY

        # Special conditions
        if moisture < 0.2 and temperature > 25.0:
            return TerrainType.SAND
        elif roughness > 0.7:
            return TerrainType.ROCK

        return TerrainType.FLAT

    @staticmethod
    def calculate_slope(elevation: float, neighbors: List[float]) -> Tuple[float, float]:
        """Calculate slope magnitude and aspect from neighboring elevations."""
        if not neighbors:
            return 0.0, 0.0

        # Calculate gradients in x and y directions
        dx = (neighbors[2] - neighbors[0]) / 2.0  # Right - Left
        dy = (neighbors[5] - neighbors[3]) / 2.0  # Bottom - Top

        # Slope magnitude
        slope = math.sqrt(dx * dx + dy * dy)

        # Aspect (direction)
        aspect = math.atan2(dy, dx) if slope > 0.01 else 0.0

        return slope, aspect

    @staticmethod
    def calculate_roughness(elevations: List[float]) -> float:
        """Calculate terrain roughness from elevation variance."""
        if len(elevations) < 2:
            return 0.0

        mean = sum(elevations) / len(elevations)
        variance = sum((e - mean) ** 2 for e in elevations) / len(elevations)
        return math.sqrt(variance)

    @staticmethod
    def assess_stability(terrain_type: TerrainType, slope: float,
                        soil_type: str, moisture: float) -> float:
        """Assess terrain stability for construction."""
        base_stability = 1.0

        # Terrain type modifiers
        stability_modifiers = {
            TerrainType.FLAT: 1.0,
            TerrainType.GENTLE_SLOPE: 0.9,
            TerrainType.STEEP_SLOPE: 0.6,
            TerrainType.CLIFF: 0.2,
            TerrainType.ROCK: 1.2,
            TerrainType.SAND: 0.7,
            TerrainType.MARSH: 0.4,
            TerrainType.WATER: 0.0,
        }

        base_stability *= stability_modifiers.get(terrain_type, 0.8)

        # Slope penalty
        if slope > 0.5:
            base_stability *= max(0.3, 1.0 - slope * 0.4)

        # Soil type modifiers
        soil_modifiers = {
            "rock": 1.3,
            "clay": 0.9,
            "sand": 0.6,
            "loam": 1.0,
            "gravel": 0.8,
        }
        base_stability *= soil_modifiers.get(soil_type, 0.8)

        # Moisture effects
        if moisture > 0.8:
            base_stability *= 0.7  # Wet soil is less stable
        elif moisture < 0.2:
            base_stability *= 0.9  # Dry soil can be less cohesive

        return max(0.0, min(1.0, base_stability))


class ObjectPlacementSystem:
    """Advanced object placement system with realistic terrain interaction."""

    def __init__(self):
        self.terrain_analyzer = TerrainAnalysis()

    def calculate_placement(self, x: int, y: int, object_type: str,
                          terrain_cell: TerrainCell, building_spec: Dict[str, Any]) -> ObjectPlacement:
        """Calculate optimal placement for an object on terrain."""

        # Base position from tile coordinates
        base_x = x + 0.5  # Center of tile
        base_y = terrain_cell.elevation
        base_z = y + 0.5

        # Terrain-specific adjustments
        position, rotation, scale = self._adjust_for_terrain(
            (base_x, base_y, base_z), object_type, terrain_cell, building_spec
        )

        # Foundation requirements
        foundation = self._calculate_foundation(object_type, terrain_cell, building_spec)

        # Stability and accessibility assessment
        stability = self._assess_placement_stability(position, object_type, terrain_cell)
        accessibility = self._assess_accessibility(position, object_type, terrain_cell)

        return ObjectPlacement(
            position=position,
            rotation=rotation,
            scale=scale,
            foundation_type=foundation["type"],
            foundation_depth=foundation["depth"],
            adaptations=foundation["adaptations"],
            stability_score=stability,
            accessibility_score=accessibility
        )

    def _adjust_for_terrain(self, base_pos: Tuple[float, float, float],
                          object_type: str, terrain: TerrainCell,
                          spec: Dict[str, Any]) -> Tuple[Tuple[float, float, float],
                                                        Tuple[float, float, float],
                                                        Tuple[float, float, float]]:
        """Adjust position, rotation, and scale based on terrain characteristics."""

        x, y, z = base_pos
        rotation = (0.0, 0.0, 0.0)
        scale = (1.0, 1.0, 1.0)

        # Slope-based adjustments
        if terrain.slope > 0.2:
            # Rotate to follow slope
            rotation = (0.0, terrain.aspect, terrain.slope * 0.3)

            # Adjust vertical position for slope
            slope_offset = terrain.slope * 0.5
            y += slope_offset

        # Terrain type specific adjustments
        if terrain.terrain_type == TerrainType.WATER:
            y = max(y, -0.5)  # Ensure above water
            if object_type in ["bridge", "dock"]:
                y = -0.2  # Bridges/docks sit at water level

        elif terrain.terrain_type == TerrainType.MARSH:
            y += 0.3  # Raise above marshy ground
            scale = (1.0, 1.1, 1.0)  # Slightly taller to appear above marsh

        elif terrain.terrain_type == TerrainType.SAND:
            y += 0.1  # Slight settling in sand
            if object_type == "temple":
                scale = (1.05, 1.0, 1.05)  # Temples appear larger on sand

        elif terrain.terrain_type == TerrainType.ROCK:
            y += 0.2  # Built up on rocky terrain
            rotation = (0.0, random.uniform(0, math.pi * 2), 0.0)  # Random orientation on rock

        elif terrain.terrain_type == TerrainType.FOREST:
            # Clear space for building
            scale = (0.9, 1.0, 0.9)  # Slightly compressed in forest
            y += terrain.vegetation_density * 0.1

        # Roughness adjustments
        if terrain.roughness > 0.5:
            # Add slight random variation
            x += random.uniform(-0.1, 0.1) * terrain.roughness
            z += random.uniform(-0.1, 0.1) * terrain.roughness
            rotation = (rotation[0] + random.uniform(-0.1, 0.1),
                       rotation[1],
                       rotation[2] + random.uniform(-0.1, 0.1))

        return (x, y, z), rotation, scale

    def _calculate_foundation(self, object_type: str, terrain: TerrainCell,
                            spec: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate foundation requirements based on object and terrain."""

        foundation = {
            "type": "standard",
            "depth": 0.5,
            "adaptations": []
        }

        # Object type specific foundations
        if object_type in ["temple", "palace", "monument"]:
            foundation["type"] = "elevated_platform"
            foundation["depth"] = 1.0
            foundation["adaptations"].append("ornamental_base")

        elif object_type in ["tower", "fortress"]:
            foundation["type"] = "deep_foundation"
            foundation["depth"] = 2.0
            foundation["adaptations"].append("reinforced")

        elif object_type in ["bridge"]:
            foundation["type"] = "pile_foundation"
            foundation["depth"] = 3.0
            foundation["adaptations"].extend(["waterproofing", "pile_driving"])

        # Terrain-specific adaptations
        if terrain.terrain_type in [TerrainType.SAND, TerrainType.MARSH]:
            foundation["adaptations"].append("waterproofing")
            foundation["depth"] *= 1.5

        elif terrain.terrain_type == TerrainType.ROCK:
            foundation["type"] = "minimal_foundation"
            foundation["depth"] *= 0.5

        elif terrain.slope > 0.5:
            foundation["adaptations"].append("retaining_walls")
            foundation["depth"] *= 1.2

        # Climate adaptations
        if terrain.climate == ClimateType.ARCTIC:
            foundation["adaptations"].append("permafrost_protection")
            foundation["depth"] *= 1.3

        elif terrain.climate == ClimateType.TROPICAL:
            foundation["adaptations"].append("termite_resistant")
            foundation["type"] = "raised_foundation"

        return foundation

    def _assess_placement_stability(self, position: Tuple[float, float, float],
                                  object_type: str, terrain: TerrainCell) -> float:
        """Assess how stable the placement is."""
        stability = terrain.stability

        # Object type stability requirements
        stability_requirements = {
            "temple": 0.8,
            "palace": 0.8,
            "house": 0.6,
            "tower": 0.9,
            "bridge": 0.7,
            "wall": 0.8,
        }

        required_stability = stability_requirements.get(object_type, 0.5)

        if stability < required_stability:
            # Can be compensated with engineering
            compensation_factor = min(1.0, stability / required_stability)
            stability *= compensation_factor

        return max(0.0, min(1.0, stability))

    def _assess_accessibility(self, position: Tuple[float, float, float],
                            object_type: str, terrain: TerrainCell) -> float:
        """Assess how accessible the placement is."""
        accessibility = terrain.accessibility

        # Slope affects accessibility
        if terrain.slope > 0.3:
            accessibility *= max(0.5, 1.0 - terrain.slope * 0.5)

        # Terrain type effects
        terrain_accessibility = {
            TerrainType.FLAT: 1.0,
            TerrainType.GENTLE_SLOPE: 0.9,
            TerrainType.STEEP_SLOPE: 0.6,
            TerrainType.CLIFF: 0.2,
            TerrainType.WATER: 0.3,
            TerrainType.MARSH: 0.4,
            TerrainType.FOREST: 0.7,
            TerrainType.ROCK: 0.8,
        }

        accessibility *= terrain_accessibility.get(terrain.terrain_type, 0.8)

        return max(0.0, min(1.0, accessibility))


class EnvironmentStylingSystem:
    """Advanced environmental styling and material adaptation system."""

    def __init__(self):
        self.material_adaptations = {
            ClimateType.TROPICAL: {
                "color_shift": [0.1, 0.05, 0.0],
                "roughness_increase": 0.1,
                "detail_increase": 0.2,
                "aging_accelerated": True
            },
            ClimateType.ARID: {
                "color_shift": [0.15, -0.05, -0.1],
                "roughness_increase": 0.2,
                "detail_increase": 0.3,
                "cracking_effects": True
            },
            ClimateType.ARCTIC: {
                "color_shift": [0.2, 0.2, 0.3],
                "roughness_increase": 0.15,
                "detail_increase": 0.1,
                "frost_damage": True
            },
            ClimateType.MOUNTAIN: {
                "color_shift": [0.05, 0.02, -0.05],
                "roughness_increase": 0.25,
                "detail_increase": 0.4,
                "erosion_effects": True
            }
        }

    def adapt_material_for_environment(self, base_material: str, climate: ClimateType,
                                     terrain: TerrainCell, age: str = "weathered") -> Dict[str, Any]:
        """Adapt material properties for environmental conditions."""

        material_props = {
            "name": base_material,
            "roughness": MATERIAL_ROUGHNESS_MEDIUM,
            "metalness": 0.0,
            "color": self._get_base_color(base_material),
            "environmental_effects": []
        }

        # Apply climate adaptations
        if climate in self.material_adaptations:
            adaptation = self.material_adaptations[climate]
            material_props.update(self._apply_climate_adaptation(material_props, adaptation))

        # Apply terrain effects
        material_props.update(self._apply_terrain_effects(material_props, terrain))

        # Apply aging effects
        material_props.update(self._apply_aging_effects(material_props, age, climate))

        return material_props

    def _get_base_color(self, material: str) -> str:
        """Get base color for material type."""
        material_colors = {
            "marble": "#F5F5F5",
            "limestone": "#F5F5DC",
            "sandstone": "#DEB887",
            "granite": "#696969",
            "brick": "#CD853F",
            "terracotta": "#D2691E",
            "wood": "#8B4513",
            "thatch": "#228B22",
            "concrete": "#A9A9A9",
            "glass": "#87CEEB",
            "gold": "#FFD700",
            "bronze": "#CD7F32"
        }
        return material_colors.get(material, "#C0C0C0")

    def _apply_climate_adaptation(self, material_props: Dict[str, Any],
                                adaptation: Dict[str, Any]) -> Dict[str, Any]:
        """Apply climate-specific material adaptations."""
        updated = dict(material_props)

        # Color shifting
        if "color_shift" in adaptation:
            shift = adaptation["color_shift"]
            # Apply HSL color shifting logic here
            updated["environmental_effects"].append("climate_color_shift")

        # Roughness and detail changes
        updated["roughness"] += adaptation.get("roughness_increase", 0)
        updated["roughness"] = max(0.0, min(1.0, updated["roughness"]))

        # Special effects
        if adaptation.get("cracking_effects"):
            updated["environmental_effects"].append("surface_cracking")
        if adaptation.get("frost_damage"):
            updated["environmental_effects"].append("frost_damage")
        if adaptation.get("erosion_effects"):
            updated["environmental_effects"].append("erosion_patterns")

        return updated

    def _apply_terrain_effects(self, material_props: Dict[str, Any],
                             terrain: TerrainCell) -> Dict[str, Any]:
        """Apply terrain-specific material effects."""
        updated = dict(material_props)

        # Slope effects
        if terrain.slope > 0.5:
            updated["roughness"] += 0.1
            updated["environmental_effects"].append("slope_erosion")

        # Moisture effects
        if terrain.moisture > 0.8:
            updated["environmental_effects"].append("water_staining")
            updated["roughness"] += 0.05
        elif terrain.moisture < 0.2:
            updated["environmental_effects"].append("desiccation_cracks")

        # Terrain type specific effects
        if terrain.terrain_type == TerrainType.ROCK:
            updated["roughness"] += 0.2
            updated["environmental_effects"].append("natural_roughness")
        elif terrain.terrain_type == TerrainType.SAND:
            updated["environmental_effects"].append("sand_abrasion")
        elif terrain.terrain_type == TerrainType.FOREST:
            updated["environmental_effects"].append("lichen_growth")

        return updated

    def _apply_aging_effects(self, material_props: Dict[str, Any],
                           age: str, climate: ClimateType) -> Dict[str, Any]:
        """Apply aging effects to materials."""
        updated = dict(material_props)

        aging_multipliers = {
            "pristine": 0.0,
            "weathered": 1.0,
            "ancient": 2.0,
            "ruined": 3.0
        }

        age_multiplier = aging_multipliers.get(age, 1.0)

        # Accelerated aging in harsh climates
        if climate in [ClimateType.TROPICAL, ClimateType.ARID]:
            age_multiplier *= 1.5
        elif climate == ClimateType.ARCTIC:
            age_multiplier *= 0.8  # Slower aging in cold

        # Apply aging effects
        updated["roughness"] += age_multiplier * 0.1
        updated["roughness"] = max(0.0, min(1.0, updated["roughness"]))

        if age_multiplier > 1.0:
            updated["environmental_effects"].append("aging_wear")
        if age_multiplier > 2.0:
            updated["environmental_effects"].append("structural_degradation")

        return updated

    def generate_environmental_variations(self, base_object: Dict[str, Any],
                                        terrain: TerrainCell, climate: ClimateType) -> List[Dict[str, Any]]:
        """Generate environmental variations of an object."""
        variations = []

        # Base variation
        variations.append(dict(base_object))

        # Climate-specific variations
        if climate == ClimateType.TROPICAL:
            tropical_var = dict(base_object)
            tropical_var["adaptations"] = base_object.get("adaptations", []) + ["ventilation", "shade"]
            tropical_var["material_modifiers"] = {"roughness": 1.1, "aging": "accelerated"}
            variations.append(tropical_var)

        elif climate == ClimateType.ARID:
            arid_var = dict(base_object)
            arid_var["adaptations"] = base_object.get("adaptations", []) + ["thermal_mass", "minimal_openings"]
            arid_var["material_modifiers"] = {"color_shift": [0.1, -0.05, -0.1]}
            variations.append(arid_var)

        elif climate == ClimateType.ARCTIC:
            arctic_var = dict(base_object)
            arctic_var["adaptations"] = base_object.get("adaptations", []) + ["insulation", "frost_protection"]
            arctic_var["material_modifiers"] = {"roughness": 0.9}
            variations.append(arctic_var)

        # Terrain-specific variations
        if terrain.slope > 0.5:
            sloped_var = dict(base_object)
            sloped_var["adaptations"] = base_object.get("adaptations", []) + ["terracing", "retaining_walls"]
            variations.append(sloped_var)

        return variations


# Global instances
terrain_analyzer = TerrainAnalysis()
placement_system = ObjectPlacementSystem()
styling_system = EnvironmentStylingSystem()