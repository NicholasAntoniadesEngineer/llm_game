# Society Data Architecture

This directory contains comprehensive society/culture profiles that define all cultural aspects of the architectural generation system.

## Society JSON Schema

Each society is defined in a JSON file with the following structure:

```json
{
  "name": "Society Display Name",
  "period": "historical_period",
  "characteristics": ["list", "of", "key", "cultural", "traits"],
  "building_types": {
    "building_type_name": {
      "orders": ["architectural", "orders"],
      "roof_style": "roof_type",
      "foundation": "foundation_type",
      "materials": ["material1", "material2"]
    }
  },
  "proportions": {
    "column_height_to_diameter": 8.0,
    "entablature_to_column_height": 0.25,
    "pediment_rise_to_width": 0.12
  },
  "color_palette": ["#hex1", "#hex2", "#hex3"],
  "adaptation_rules": {
    "climate_desert": {"materials": ["sandstone"], "roof_pitch": 0.3},
    "climate_tropical": {"ventilation": true, "courtyard": true},
    "terrain_hills": {"terracing": true, "retaining_walls": true}
  },
  "building_components": {
    "building_type": {
      "ref_w": 3.6,
      "ref_d": 2.7,
      "components": [
        {
          "type": "component_type",
          "height": 0.4,
          "color": "#hex",
          "roughness": 0.7
        }
      ]
    }
  },
  "prompt_hints": {
    "variety_suggestions": [
      "Cultural hint for variety generation"
    ],
    "material_alternatives": {
      "current_material": "suggested_alternative"
    },
    "scale_hints": {
      "building_type": "scale guidance text"
    },
    "grammar_types": ["building_type1", "building_type2"]
  },
  "template_adaptations": {
    "template_name": {
      "cultural_modifiers": {
        "modifier_name": "modifier_value"
      }
    }
  }
}
```

## Key Sections

### Basic Information
- `name`: Display name for the society
- `period`: Historical period (ancient, classical, medieval, renaissance, industrial, modern)
- `characteristics`: Key cultural/architectural traits

### Architectural Rules
- `building_types`: Defines architectural rules for each building type
- `proportions`: Mathematical proportions used in the culture
- `color_palette`: Cultural color preferences

### Environmental Adaptation
- `adaptation_rules`: How buildings adapt to different climates and terrains

### Building Components
- `building_components`: Detailed component specifications for procedural generation

### AI Prompt Guidance
- `prompt_hints`: Culture-specific hints for AI generation
  - `variety_suggestions`: Hints for generating building variety
  - `material_alternatives`: Material substitution suggestions
  - `scale_hints`: Scale guidance for different building types
  - `grammar_types`: Building types that have standard forms

### Template System
- `template_adaptations`: Cultural modifications for advanced building templates

## File Naming

Society files must use the `.society.json` extension and be named with lowercase, descriptive names:
- `roman.society.json`
- `greek.society.json`
- `medieval_european.society.json`
- `mesoamerican.society.json`
- etc.

The system automatically discovers and loads all `.society.json` files in this directory. **No code changes are required** to add new societies - just drop a new `.society.json` file in this directory and restart the application.

## Validation

Society JSON files are validated against this schema to ensure consistency and completeness.