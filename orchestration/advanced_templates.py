"""Advanced building templates for sophisticated architectural generation.

Provides pre-designed building templates with complex architectural features,
procedural generation rules, and cultural/historical accuracy.
"""

from typing import Dict, List, Any, Optional
import random
import math


class AdvancedBuildingTemplates:
    """Collection of advanced building templates for complex architectural forms.

    Enhanced with parametric customization, cultural adaptation, and procedural generation.
    """

    def __init__(self):
        self.templates = {
            "roman_villa": self._roman_villa_template(),
            "medieval_castle": self._medieval_castle_template(),
            "gothic_cathedral": self._gothic_cathedral_template(),
            "renaissance_palace": self._renaissance_palace_template(),
            "mesoamerican_pyramid": self._mesoamerican_pyramid_template(),
            "islamic_palace": self._islamic_palace_template(),
            "chinese_pagoda": self._chinese_pagoda_template(),
            "victorian_mansion": self._victorian_mansion_template(),
        }

        # Cultural adaptation rules - loaded from society JSON files
        from orchestration.cultural_adaptation import cultural_system
        self.cultural_adaptations = {}
        for culture_name, culture_data in cultural_system.cultures.items():
            self.cultural_adaptations[culture_name] = culture_data.get("template_adaptations", {})

        # Parametric modifiers for customization
        self.parametric_modifiers = {
            "scale": self._scale_modifier,
            "ornamentation": self._ornamentation_modifier,
            "roof_style": self._roof_style_modifier,
            "material_quality": self._material_quality_modifier,
            "cultural_adaptation": self._cultural_adaptation_modifier,
        }

    def get_template(self, template_name: str, **params) -> Dict[str, Any]:
        """Get a building template with parameterized customization."""
        if template_name not in self.templates:
            raise ValueError(f"Unknown template: {template_name}")

        template = self.templates[template_name].copy()

        # Apply parameter overrides
        for key, value in params.items():
            if key in template:
                template[key] = value

        return template

    def _roman_villa_template(self) -> Dict[str, Any]:
        """Roman villa with atrium, peristyle, and multiple wings."""
        return {
            "name": "Roman Villa",
            "building_type": "villa",
            "components": [
                # Foundation and base
                {
                    "type": "podium",
                    "height": 0.15,
                    "color": "travertine",
                    "stack_role": "foundation"
                },
                # Main structural walls
                {
                    "type": "walls",
                    "height": 0.8,
                    "thickness": 0.12,
                    "color": "brick",
                    "stack_role": "structural"
                },
                # Atrium (central courtyard)
                {
                    "type": "procedural",
                    "stack_role": "infill",
                    "parts": [
                        {"shape": "box", "width": 2.5, "height": 0.05, "depth": 2.5, "color": "marble", "position": [0, 0.4, 0]},
                        {"shape": "cylinder", "radius": 0.8, "height": 0.3, "color": "marble", "position": [0, 0.45, 0]}
                    ]
                },
                # Colonnades around atrium
                {
                    "type": "colonnade",
                    "columns": 12,
                    "height": 0.6,
                    "radius": 0.08,
                    "style": "corinthian",
                    "color": "marble",
                    "stack_role": "structural"
                },
                # Roof system
                {
                    "type": "tiled_roof",
                    "height": 0.25,
                    "color": "terracotta",
                    "stack_role": "roof"
                },
                # Decorative elements
                {
                    "type": "procedural",
                    "stack_role": "decorative",
                    "parts": [
                        {"shape": "box", "width": 0.1, "height": 0.05, "depth": 0.1, "color": "marble", "position": [1.2, 0.9, 1.2]},
                        {"shape": "cylinder", "radius": 0.03, "height": 0.15, "color": "marble", "position": [1.2, 0.95, 1.2]}
                    ]
                }
            ],
            "description": "Elegant Roman villa with central atrium and peristyle garden",
            "cultural_context": "Ancient Roman domestic architecture",
            "complexity": "high"
        }

    def _medieval_castle_template(self) -> Dict[str, Any]:
        """Medieval castle with towers, walls, and keep."""
        return {
            "name": "Medieval Castle",
            "building_type": "castle",
            "components": [
                # Stone foundation
                {
                    "type": "podium",
                    "height": 0.2,
                    "color": "granite",
                    "stack_role": "foundation"
                },
                # Main keep walls
                {
                    "type": "walls",
                    "height": 1.2,
                    "thickness": 0.25,
                    "color": "stone",
                    "stack_role": "structural"
                },
                # Corner towers
                {
                    "type": "procedural",
                    "stack_role": "structural",
                    "parts": [
                        {"shape": "cylinder", "radius": 0.4, "height": 1.8, "color": "stone", "position": [1.5, 0.9, 1.5]},
                        {"shape": "cylinder", "radius": 0.4, "height": 1.8, "color": "stone", "position": [-1.5, 0.9, 1.5]},
                        {"shape": "cylinder", "radius": 0.4, "height": 1.8, "color": "stone", "position": [1.5, 0.9, -1.5]},
                        {"shape": "cylinder", "radius": 0.4, "height": 1.8, "color": "stone", "position": [-1.5, 0.9, -1.5]}
                    ]
                },
                # Battlements
                {
                    "type": "procedural",
                    "stack_role": "roof",
                    "parts": [
                        {"shape": "box", "width": 3.5, "height": 0.15, "depth": 3.5, "color": "stone", "position": [0, 1.35, 0]},
                        # Crenellations
                        {"shape": "box", "width": 0.1, "height": 0.1, "depth": 0.1, "color": "stone", "position": [0.2, 1.4, 1.8]},
                        {"shape": "box", "width": 0.1, "height": 0.1, "depth": 0.1, "color": "stone", "position": [-0.2, 1.4, 1.8]}
                    ]
                },
                # Gatehouse
                {
                    "type": "procedural",
                    "stack_role": "structural",
                    "parts": [
                        {"shape": "box", "width": 0.8, "height": 1.0, "depth": 0.3, "color": "stone", "position": [0, 0.5, 2.0]},
                        {"shape": "box", "width": 0.4, "height": 0.6, "depth": 0.2, "color": "wood", "position": [0, 0.8, 2.1]}  # Portcullis
                    ]
                }
            ],
            "description": "Formidable medieval castle with towers and defensive walls",
            "cultural_context": "European medieval military architecture",
            "complexity": "very_high"
        }

    def _gothic_cathedral_template(self) -> Dict[str, Any]:
        """Gothic cathedral with flying buttresses and stained glass."""
        return {
            "name": "Gothic Cathedral",
            "building_type": "cathedral",
            "components": [
                # Stone foundation
                {
                    "type": "podium",
                    "height": 0.1,
                    "color": "limestone",
                    "stack_role": "foundation"
                },
                # Nave walls
                {
                    "type": "walls",
                    "height": 2.0,
                    "thickness": 0.2,
                    "color": "limestone",
                    "stack_role": "structural"
                },
                # Gothic arches and vaults
                {
                    "type": "procedural",
                    "stack_role": "structural",
                    "parts": [
                        # Pointed arches
                        {"shape": "arch", "width": 0.8, "height": 1.2, "depth": 0.1, "color": "limestone", "position": [0, 1.0, 0]},
                        {"shape": "arch", "width": 0.8, "height": 1.2, "depth": 0.1, "color": "limestone", "position": [1.0, 1.0, 0]},
                        {"shape": "arch", "width": 0.8, "height": 1.2, "depth": 0.1, "color": "limestone", "position": [-1.0, 1.0, 0]}
                    ]
                },
                # Flying buttresses
                {
                    "type": "procedural",
                    "stack_role": "structural",
                    "parts": [
                        {"shape": "arch", "width": 0.6, "height": 0.8, "depth": 0.15, "color": "limestone", "position": [2.0, 1.2, 0]},
                        {"shape": "buttress", "width": 0.3, "height": 1.5, "depth": 0.2, "color": "limestone", "position": [2.2, 0.75, 0]}
                    ]
                },
                # Rose window
                {
                    "type": "procedural",
                    "stack_role": "decorative",
                    "parts": [
                        {"shape": "cylinder", "radius": 0.8, "height": 0.05, "color": "stained_glass", "position": [0, 1.8, 0]},
                        {"shape": "cylinder", "radius": 0.7, "height": 0.02, "color": "limestone", "position": [0, 1.81, 0]}  # Stone tracery
                    ]
                },
                # Spire
                {
                    "type": "procedural",
                    "stack_role": "roof",
                    "parts": [
                        {"shape": "cone", "radius": 0.2, "height": 0.8, "color": "limestone", "position": [0, 2.4, 0]}
                    ]
                }
            ],
            "description": "Magnificent Gothic cathedral with pointed arches and flying buttresses",
            "cultural_context": "European Gothic ecclesiastical architecture",
            "complexity": "very_high"
        }

    def _renaissance_palace_template(self) -> Dict[str, Any]:
        """Renaissance palace with classical orders and symmetry."""
        return {
            "name": "Renaissance Palace",
            "building_type": "palace",
            "components": [
                # Marble foundation
                {
                    "type": "podium",
                    "height": 0.12,
                    "color": "marble",
                    "stack_role": "foundation"
                },
                # Rusticated stone walls
                {
                    "type": "walls",
                    "height": 1.0,
                    "thickness": 0.15,
                    "color": "rusticated_stone",
                    "stack_role": "structural"
                },
                # Classical colonnade
                {
                    "type": "colonnade",
                    "columns": 8,
                    "height": 0.7,
                    "radius": 0.09,
                    "style": "composite",
                    "color": "marble",
                    "stack_role": "structural"
                },
                # Piano nobile (main floor)
                {
                    "type": "procedural",
                    "stack_role": "infill",
                    "parts": [
                        {"shape": "box", "width": 2.8, "height": 0.4, "depth": 2.8, "color": "stucco", "position": [0, 0.6, 0]}
                    ]
                },
                # Pediment
                {
                    "type": "pediment",
                    "height": 0.3,
                    "color": "marble",
                    "stack_role": "roof"
                },
                # Cornice and entablature
                {
                    "type": "procedural",
                    "stack_role": "decorative",
                    "parts": [
                        {"shape": "box", "width": 3.0, "height": 0.08, "depth": 0.1, "color": "marble", "position": [0, 1.04, 0]},
                        {"shape": "box", "width": 3.0, "height": 0.05, "depth": 0.08, "color": "marble", "position": [0, 1.09, 0]}
                    ]
                }
            ],
            "description": "Grand Renaissance palace with classical proportions and symmetry",
            "cultural_context": "Italian Renaissance palace architecture",
            "complexity": "high"
        }

    def _mesoamerican_pyramid_template(self) -> Dict[str, Any]:
        """Mesoamerican stepped pyramid with temple at summit."""
        return {
            "name": "Mesoamerican Pyramid",
            "building_type": "pyramid",
            "components": [
                # Base platform
                {
                    "type": "podium",
                    "height": 0.2,
                    "color": "limestone",
                    "stack_role": "foundation"
                },
                # Stepped pyramid body
                {
                    "type": "procedural",
                    "stack_role": "structural",
                    "parts": [
                        # Step 1
                        {"shape": "box", "width": 4.0, "height": 0.3, "depth": 4.0, "color": "limestone", "position": [0, 0.15, 0]},
                        # Step 2
                        {"shape": "box", "width": 3.5, "height": 0.3, "depth": 3.5, "color": "limestone", "position": [0, 0.45, 0]},
                        # Step 3
                        {"shape": "box", "width": 3.0, "height": 0.3, "depth": 3.0, "color": "limestone", "position": [0, 0.75, 0]},
                        # Step 4
                        {"shape": "box", "width": 2.5, "height": 0.3, "depth": 2.5, "color": "limestone", "position": [0, 1.05, 0]},
                        # Step 5
                        {"shape": "box", "width": 2.0, "height": 0.3, "depth": 2.0, "color": "limestone", "position": [0, 1.35, 0]}
                    ]
                },
                # Temple sanctuary at top
                {
                    "type": "procedural",
                    "stack_role": "roof",
                    "parts": [
                        {"shape": "box", "width": 1.2, "height": 0.4, "depth": 1.2, "color": "limestone", "position": [0, 1.7, 0]},
                        # Corbelled arch entrance
                        {"shape": "box", "width": 0.3, "height": 0.25, "depth": 0.1, "color": "limestone", "position": [0, 1.8, 0.6]}
                    ]
                },
                # Stairs
                {
                    "type": "procedural",
                    "stack_role": "decorative",
                    "parts": [
                        {"shape": "staircase", "width": 0.8, "height": 1.5, "depth": 0.2, "color": "limestone", "position": [0, 0.75, 2.2]}
                    ]
                }
            ],
            "description": "Majestic stepped pyramid with temple sanctuary at the summit",
            "cultural_context": "Mesoamerican ceremonial architecture",
            "complexity": "high"
        }

    def _islamic_palace_template(self) -> Dict[str, Any]:
        """Islamic palace with domes, arches, and intricate decoration."""
        return {
            "name": "Islamic Palace",
            "building_type": "palace",
            "components": [
                # Stone foundation
                {
                    "type": "podium",
                    "height": 0.1,
                    "color": "marble",
                    "stack_role": "foundation"
                },
                # Main walls with arched openings
                {
                    "type": "walls",
                    "height": 0.9,
                    "thickness": 0.12,
                    "color": "stucco",
                    "stack_role": "structural"
                },
                # Central dome
                {
                    "type": "dome",
                    "radius": 0.8,
                    "color": "tile",
                    "stack_role": "roof"
                },
                # Arched arcades
                {
                    "type": "procedural",
                    "stack_role": "structural",
                    "parts": [
                        {"shape": "arch", "width": 0.6, "height": 0.5, "depth": 0.08, "color": "marble", "position": [0.5, 0.4, 0]},
                        {"shape": "arch", "width": 0.6, "height": 0.5, "depth": 0.08, "color": "marble", "position": [-0.5, 0.4, 0]},
                        {"shape": "arch", "width": 0.6, "height": 0.5, "depth": 0.08, "color": "marble", "position": [0, 0.4, 0.5]},
                        {"shape": "arch", "width": 0.6, "height": 0.5, "depth": 0.08, "color": "marble", "position": [0, 0.4, -0.5]}
                    ]
                },
                # Minaret towers
                {
                    "type": "procedural",
                    "stack_role": "freestanding",
                    "parts": [
                        {"shape": "cylinder", "radius": 0.15, "height": 1.2, "color": "marble", "position": [1.5, 0.6, 1.5]},
                        {"shape": "cylinder", "radius": 0.12, "height": 0.3, "color": "marble", "position": [1.5, 1.35, 1.5]}  # Balcony
                    ]
                },
                # Geometric tile work
                {
                    "type": "procedural",
                    "stack_role": "decorative",
                    "parts": [
                        {"shape": "box", "width": 0.05, "height": 0.05, "depth": 0.02, "color": "tile", "position": [0.3, 0.8, 0.6]},
                        {"shape": "box", "width": 0.05, "height": 0.05, "depth": 0.02, "color": "tile", "position": [-0.3, 0.8, 0.6]}
                    ]
                }
            ],
            "description": "Elegant Islamic palace with domes, arches, and intricate tile work",
            "cultural_context": "Islamic architectural tradition",
            "complexity": "high"
        }

    def _chinese_pagoda_template(self) -> Dict[str, Any]:
        """Chinese pagoda with multiple eaves and curved roofs."""
        return {
            "name": "Chinese Pagoda",
            "building_type": "pagoda",
            "components": [
                # Stone base
                {
                    "type": "podium",
                    "height": 0.15,
                    "color": "stone",
                    "stack_role": "foundation"
                },
                # Multi-story structure
                {
                    "type": "procedural",
                    "stack_role": "structural",
                    "parts": [
                        # First story
                        {"shape": "box", "width": 1.8, "height": 0.6, "depth": 1.8, "color": "wood", "position": [0, 0.3, 0]},
                        # Second story
                        {"shape": "box", "width": 1.6, "height": 0.6, "depth": 1.6, "color": "wood", "position": [0, 0.9, 0]},
                        # Third story
                        {"shape": "box", "width": 1.4, "height": 0.6, "depth": 1.4, "color": "wood", "position": [0, 1.5, 0]}
                    ]
                },
                # Curved roof eaves
                {
                    "type": "procedural",
                    "stack_role": "roof",
                    "parts": [
                        # First roof
                        {"shape": "cone", "radius": 1.2, "height": 0.3, "color": "tile", "position": [0, 0.75, 0]},
                        # Second roof
                        {"shape": "cone", "radius": 1.0, "height": 0.3, "color": "tile", "position": [0, 1.35, 0]},
                        # Third roof
                        {"shape": "cone", "radius": 0.8, "height": 0.3, "color": "tile", "position": [0, 1.95, 0]}
                    ]
                },
                # Supporting brackets
                {
                    "type": "procedural",
                    "stack_role": "decorative",
                    "parts": [
                        {"shape": "box", "width": 0.1, "height": 0.1, "depth": 0.1, "color": "wood", "position": [0.8, 0.6, 0.8]},
                        {"shape": "box", "width": 0.1, "height": 0.1, "depth": 0.1, "color": "wood", "position": [-0.8, 0.6, 0.8]}
                    ]
                },
                # Finial
                {
                    "type": "procedural",
                    "stack_role": "decorative",
                    "parts": [
                        {"shape": "sphere", "radius": 0.05, "color": "bronze", "position": [0, 2.2, 0]}
                    ]
                }
            ],
            "description": "Traditional Chinese pagoda with curved roofs and multiple stories",
            "cultural_context": "Chinese Buddhist architecture",
            "complexity": "high"
        }

    def _victorian_mansion_template(self) -> Dict[str, Any]:
        """Victorian mansion with complex roofline and decorative elements."""
        return {
            "name": "Victorian Mansion",
            "building_type": "mansion",
            "components": [
                # Stone foundation
                {
                    "type": "podium",
                    "height": 0.1,
                    "color": "stone",
                    "stack_role": "foundation"
                },
                # Main structure
                {
                    "type": "block",
                    "stories": 3,
                    "storyHeight": 0.35,
                    "color": "brick",
                    "windows": 8,
                    "windowColor": "white",
                    "stack_role": "structural"
                },
                # Complex roof system
                {
                    "type": "procedural",
                    "stack_role": "roof",
                    "parts": [
                        # Main gable
                        {"shape": "box", "width": 2.5, "height": 0.4, "depth": 2.5, "color": "slate", "position": [0, 1.25, 0]},
                        # Side gables
                        {"shape": "box", "width": 0.8, "height": 0.3, "depth": 0.8, "color": "slate", "position": [1.5, 1.1, 0]},
                        {"shape": "box", "width": 0.8, "height": 0.3, "depth": 0.8, "color": "slate", "position": [-1.5, 1.1, 0]},
                        # Chimneys
                        {"shape": "box", "width": 0.2, "height": 0.4, "depth": 0.2, "color": "brick", "position": [1.0, 1.4, 1.0]},
                        {"shape": "box", "width": 0.2, "height": 0.4, "depth": 0.2, "color": "brick", "position": [-1.0, 1.4, 1.0]}
                    ]
                },
                # Porch with columns
                {
                    "type": "procedural",
                    "stack_role": "structural",
                    "parts": [
                        {"shape": "box", "width": 1.2, "height": 0.1, "depth": 0.8, "color": "wood", "position": [0, 0.05, 1.3]},
                        {"shape": "cylinder", "radius": 0.06, "height": 0.4, "color": "wood", "position": [0.3, 0.2, 1.3]},
                        {"shape": "cylinder", "radius": 0.06, "height": 0.4, "color": "wood", "position": [-0.3, 0.2, 1.3]}
                    ]
                },
                # Decorative elements
                {
                    "type": "procedural",
                    "stack_role": "decorative",
                    "parts": [
                        # Gingerbread trim
                        {"shape": "box", "width": 2.6, "height": 0.05, "depth": 0.05, "color": "wood", "position": [0, 0.8, 1.3]},
                        # Turret
                        {"shape": "cylinder", "radius": 0.3, "height": 0.8, "color": "brick", "position": [1.8, 0.4, -1.8]},
                        {"shape": "cone", "radius": 0.35, "height": 0.2, "color": "slate", "position": [1.8, 0.9, -1.8]}
                    ]
                }
            ],
            "description": "Ornate Victorian mansion with complex roofline and decorative details",
            "cultural_context": "19th century Victorian architecture",
            "complexity": "very_high"
        }


# Procedural generation helpers
def generate_procedural_building(base_template: str, complexity: str = "medium", **variations) -> Dict[str, Any]:
    """Generate a procedural building based on a template with variations.

    Args:
        base_template: Name of the base template to use
        complexity: Complexity level (low, medium, high, very_high)
        **variations: Parameter overrides for customization

    Returns:
        Complete building specification
    """
    templates = AdvancedBuildingTemplates()
    building = templates.get_template(base_template, **variations)

    # Apply complexity-based modifications
    if complexity == "low":
        # Simplify by removing decorative elements
        building["components"] = [c for c in building["components"]
                                if c.get("stack_role") != "decorative"]
    elif complexity == "high":
        # Add more procedural details
        _add_procedural_details(building)
    elif complexity == "very_high":
        # Add maximum detail and variation
        _add_procedural_details(building)
        _add_architectural_variations(building)

    return building


def _add_procedural_details(building: Dict[str, Any]) -> None:
    """Add procedural details to increase building complexity."""
    components = building["components"]

    # Add more procedural parts to existing procedural components
    for comp in components:
        if comp.get("type") == "procedural":
            parts = comp.get("parts", [])
            if len(parts) < 5:
                # Add some decorative elements
                additional_parts = [
                    {"shape": "box", "width": 0.05, "height": 0.05, "depth": 0.05,
                     "color": "marble", "position": [random.uniform(-0.5, 0.5),
                                                   random.uniform(0.1, 0.3),
                                                   random.uniform(-0.5, 0.5)]}
                    for _ in range(random.randint(1, 3))
                ]
                parts.extend(additional_parts)
                comp["parts"] = parts


def _add_architectural_variations(building: Dict[str, Any]) -> None:
    """Add architectural variations for maximum complexity."""
    components = building["components"]

    # Add asymmetrical elements
    variation_parts = [
        {"shape": "box", "width": 0.3, "height": 0.2, "depth": 0.3,
         "color": "brick", "position": [random.choice([-1.2, 1.2]),
                                      random.uniform(0.3, 0.6),
                                      random.uniform(-0.5, 0.5)]}
    ]

    # Add to a structural component or create new procedural component
    structural_comp = None
    for comp in components:
        if comp.get("stack_role") == "structural" and comp.get("type") == "procedural":
            structural_comp = comp
            break

    if structural_comp:
        structural_comp["parts"].extend(variation_parts)
    else:
        components.append({
            "type": "procedural",
            "stack_role": "structural",
            "parts": variation_parts
        })


# Template registry for easy access
TEMPLATE_REGISTRY = AdvancedBuildingTemplates()