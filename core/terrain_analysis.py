"""Terrain classification and slope analysis (thresholds from system_config.csv)."""

from __future__ import annotations

import math
from enum import Enum
from typing import List, Tuple


class TerrainType(Enum):
    """Terrain classification values written to tiles and used for stability."""

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


class TerrainAnalysis:
    """Terrain analysis and classification for blueprint elevation passes."""

    @staticmethod
    def classify_terrain(
        elevation: float,
        slope: float,
        neighbors: List[float],
        *,
        classification_thresholds: dict[str, float],
        moisture: float = 0.5,
        temperature: float = 20.0,
        roughness: float = 0.0,
    ) -> TerrainType:
        """Classify terrain type based on multiple factors (thresholds from system_config.csv)."""

        tc = classification_thresholds
        water_max = float(tc["water_elevation_max"])
        marsh_elev_max = float(tc["marsh_elevation_max"])
        marsh_moisture_min = float(tc["marsh_moisture_min"])
        cliff_slope_min = float(tc["cliff_slope_min"])
        cliff_elev_min = float(tc["cliff_elevation_min"])
        moderate_slope_min = float(tc["moderate_slope_min"])
        plateau_elev_min = float(tc["plateau_elevation_min"])
        valley_elev_max = float(tc["valley_elevation_max"])
        valley_slope_max = float(tc["valley_slope_max"])
        sand_moisture_max = float(tc["sand_moisture_max"])
        sand_temp_min = float(tc["sand_temperature_min"])
        rock_rough_min = float(tc["rock_roughness_min"])

        if elevation < water_max:
            return TerrainType.WATER
        if elevation < marsh_elev_max and moisture > marsh_moisture_min:
            return TerrainType.MARSH

        if slope > cliff_slope_min:
            if elevation > cliff_elev_min:
                return TerrainType.CLIFF
            return TerrainType.STEEP_SLOPE
        if slope > moderate_slope_min:
            return TerrainType.GENTLE_SLOPE

        if elevation > plateau_elev_min:
            return TerrainType.PLATEAU
        if elevation < valley_elev_max and slope < valley_slope_max:
            return TerrainType.VALLEY

        if moisture < sand_moisture_max and temperature > sand_temp_min:
            return TerrainType.SAND
        if roughness > rock_rough_min:
            return TerrainType.ROCK

        return TerrainType.FLAT

    @staticmethod
    def calculate_slope(elevation: float, neighbors: List[float]) -> Tuple[float, float]:
        """Calculate slope magnitude and aspect from neighboring elevations.

        ``neighbors`` must be eight floats in fixed ring order:
        NW, N, NE, W, E, SW, S, SE — same order as ``CityBlueprint._get_neighbor_elevations``.
        """
        if len(neighbors) < 8:
            return 0.0, 0.0

        north_elevation = neighbors[1]
        south_elevation = neighbors[6]
        west_elevation = neighbors[3]
        east_elevation = neighbors[4]
        dx = (east_elevation - west_elevation) / 2.0
        dy = (south_elevation - north_elevation) / 2.0

        slope = math.sqrt(dx * dx + dy * dy)
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
    def assess_stability(
        terrain_type: TerrainType,
        slope: float,
        soil_type: str,
        moisture: float,
        *,
        classification_thresholds: dict[str, float],
        terrain_type_modifiers: dict[str, float],
        soil_type_modifiers: dict[str, float],
    ) -> float:
        """Assess terrain stability for construction (modifiers from system_config.csv)."""
        base_stability = 1.0
        tc = classification_thresholds
        slope_threshold = float(tc["assess_stability_slope_threshold"])
        slope_penalty = float(tc["assess_stability_slope_penalty_factor"])
        moisture_high = float(tc["assess_stability_moisture_high"])
        moisture_low = float(tc["assess_stability_moisture_low"])

        terrain_key = terrain_type.value
        base_stability *= float(terrain_type_modifiers.get(terrain_key, 0.8))

        if slope > slope_threshold:
            base_stability *= max(0.3, 1.0 - slope * slope_penalty)

        soil_key = str(soil_type).strip().lower()
        base_stability *= float(soil_type_modifiers.get(soil_key, 0.8))

        if moisture > moisture_high:
            base_stability *= 0.7
        elif moisture < moisture_low:
            base_stability *= 0.9

        return max(0.0, min(1.0, base_stability))
