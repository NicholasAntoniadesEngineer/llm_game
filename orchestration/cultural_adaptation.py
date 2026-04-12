"""Cultural adaptation system for historically accurate building generation.

Provides cultural context, historical accuracy rules, and adaptation mechanisms
for different civilizations and architectural traditions.
"""

from typing import Dict, List, Any, Optional, Tuple
import random
import math
import json
import os
from pathlib import Path

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import Config

_cultural_adaptation_by_config_id: dict[int, "CulturalAdaptationSystem"] = {}


class CulturalAdaptationSystem:
    """Manages cultural and historical adaptation rules for building generation."""

    def __init__(self, system_configuration: "Config"):
        self._system_configuration = system_configuration
        self._data_dir = Path(system_configuration.data_directory_relative) / "societies"
        self.cultures = self._load_cultures()

        self.historical_periods = {
            "ancient": {"year_range": (-3000, 500), "characteristics": ["monumental", "ritual", "durable"]},
            "classical": {"year_range": (-500, 500), "characteristics": ["proportional", "ornamental", "civic"]},
            "medieval": {"year_range": (500, 1500), "characteristics": ["fortified", "religious", "communal"]},
            "renaissance": {"year_range": (1400, 1700), "characteristics": ["symmetrical", "classical_revival", "humanist"]},
            "industrial": {"year_range": (1700, 1900), "characteristics": ["technological", "urban", "mass_produced"]},
            "modern": {"year_range": (1900, 2100), "characteristics": ["functional", "innovative", "global"]},
        }

    def _load_cultures(self) -> Dict[str, Any]:
        """Load all society data from .society.json files automatically with validation."""
        from core.society_validator import validate_all_societies

        cultures = {}
        if not self._data_dir.exists():
            raise FileNotFoundError(f"Societies directory not found: {self._data_dir}")

        # Validate all society files first
        validation_results = validate_all_societies(
            self._data_dir,
            system_configuration=self._system_configuration,
        )
        invalid_societies = {name: errors for name, errors in validation_results.items() if errors}

        if invalid_societies:
            from core.society_validator import society_validator
            summary = society_validator.get_validation_summary(validation_results)
            print("Society validation issues found:")
            print(summary)
            print("\nContinuing with valid societies only...")

        # Load valid societies
        for society_file in self._data_dir.glob("*.society.json"):
            society_key = society_file.stem.replace('.society', '')

            # Skip invalid societies
            if society_key in invalid_societies:
                print(f"Skipping invalid society: {society_key}")
                continue

            try:
                with open(society_file, 'r', encoding='utf-8') as f:
                    society_data = json.load(f)
                    cultures[society_key] = society_data
                    print(f"Loaded society: {society_key} ({society_data.get('name', 'Unknown')})")
            except Exception as e:
                print(f"Warning: Failed to load society {society_key}: {e}")

        if not cultures:
            raise RuntimeError("No valid societies found! Check society file validation errors above.")

        return cultures



    def adapt_building_for_culture(self, building_spec: Dict[str, Any], culture: str, climate: str = None, terrain: str = None) -> Dict[str, Any]:
        """Adapt a building specification for a specific culture and context."""
        if culture not in self.cultures:
            return building_spec

        culture_data = self.cultures[culture]
        adapted_spec = building_spec.copy()

        # Apply cultural building type rules
        building_type = building_spec.get("building_type", "")
        if building_type in culture_data.get("building_types", {}):
            type_rules = culture_data["building_types"][building_type]

            # Apply material preferences
            if "materials" in type_rules:
                adapted_spec["preferred_materials"] = type_rules["materials"]

            # Apply architectural orders
            if "orders" in type_rules:
                adapted_spec["column_orders"] = type_rules["orders"]

            # Apply roof style preferences
            if "roof_style" in type_rules:
                adapted_spec["preferred_roof"] = type_rules["roof_style"]

        # Apply climate adaptations
        if climate:
            climate_key = f"climate_{climate}"
            adaptations = culture_data.get("adaptation_rules", {}).get(climate_key, {})
            for key, value in adaptations.items():
                adapted_spec[f"climate_{key}"] = value

        # Apply terrain adaptations
        if terrain:
            terrain_key = f"terrain_{terrain}"
            adaptations = culture_data.get("adaptation_rules", {}).get(terrain_key, {})
            for key, value in adaptations.items():
                adapted_spec[f"terrain_{key}"] = value

        # Apply cultural color palette
        if "color_palette" in culture_data:
            adapted_spec["cultural_colors"] = culture_data["color_palette"]

        # Apply proportional rules
        if "proportions" in culture_data:
            adapted_spec["cultural_proportions"] = culture_data["proportions"]

        return adapted_spec

    def get_cultural_context(self, culture: str) -> Dict[str, Any]:
        """Get cultural context information for prompt generation."""
        if culture not in self.cultures:
            return {}

        culture_data = self.cultures[culture]
        return {
            "name": culture_data["name"],
            "period": culture_data["period"],
            "characteristics": culture_data["characteristics"],
            "building_types": list(culture_data.get("building_types", {}).keys()),
            "materials": self._extract_materials_from_culture(culture_data),
            "architectural_features": self._extract_features_from_culture(culture_data)
        }

    def _extract_materials_from_culture(self, culture_data: Dict[str, Any]) -> List[str]:
        """Extract all materials mentioned in a culture definition."""
        materials = set()
        for building_type in culture_data.get("building_types", {}).values():
            if "materials" in building_type:
                materials.update(building_type["materials"])
        return list(materials)

    def _extract_features_from_culture(self, culture_data: Dict[str, Any]) -> List[str]:
        """Extract architectural features from a culture definition."""
        features = culture_data.get("characteristics", []).copy()

        # Add building type specific features
        for building_type in culture_data.get("building_types", {}).values():
            if "orders" in building_type:
                features.extend([f"{order}_order" for order in building_type["orders"]])
            if "style" in building_type:
                features.append(building_type["style"])

        return list(set(features))  # Remove duplicates

    def validate_cultural_accuracy(self, building_spec: Dict[str, Any], culture: str) -> List[str]:
        """Validate that a building specification meets cultural accuracy requirements."""
        issues = []

        if culture not in self.cultures:
            return issues

        culture_data = self.cultures[culture]
        building_type = building_spec.get("building_type", "")

        # Check if building type exists in culture
        if building_type and building_type not in culture_data.get("building_types", {}):
            issues.append(f"Building type '{building_type}' not typical for {culture} culture")

        # Check material appropriateness
        materials = building_spec.get("materials", [])
        preferred_materials = self._extract_materials_from_culture(culture_data)
        if materials and preferred_materials:
            uncommon_materials = set(materials) - set(preferred_materials)
            if uncommon_materials:
                issues.append(f"Materials {list(uncommon_materials)} uncommon for {culture} culture")

        return issues

    def generate_cultural_prompt_context(self, culture: str, building_type: str = None) -> str:
        """Generate cultural context string for use in AI prompts."""
        if culture not in self.cultures:
            return ""

        culture_data = self.cultures[culture]
        context_parts = [f"Culture: {culture_data['name']} ({culture_data['period']})"]

        if building_type and building_type in culture_data.get("building_types", {}):
            type_data = culture_data["building_types"][building_type]
            features = []
            if "orders" in type_data:
                features.append(f"orders: {', '.join(type_data['orders'])}")
            if "style" in type_data:
                features.append(f"style: {type_data['style']}")
            if "materials" in type_data:
                features.append(f"materials: {', '.join(type_data['materials'])}")
            if features:
                context_parts.append(f"Typical {building_type}: {'; '.join(features)}")

        characteristics = culture_data.get("characteristics", [])
        if characteristics:
            context_parts.append(f"Characteristics: {', '.join(characteristics)}")

        return " | ".join(context_parts)


def cultural_adaptation_system_for(system_configuration: "Config") -> CulturalAdaptationSystem:
    """Reuse one CulturalAdaptationSystem per Config object identity."""
    key = id(system_configuration)
    existing = _cultural_adaptation_by_config_id.get(key)
    if existing is not None:
        return existing
    created = CulturalAdaptationSystem(system_configuration)
    _cultural_adaptation_by_config_id[key] = created
    return created