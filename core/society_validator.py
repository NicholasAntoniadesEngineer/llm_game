"""Society JSON Schema Validation.

Provides comprehensive validation for society configuration files
to ensure data integrity and provide helpful error messages.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

try:
    import jsonschema
    from jsonschema import ValidationError, SchemaError
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False
    ValidationError = Exception
    SchemaError = Exception

from .constants import (
    MAX_SOCIETY_FILE_SIZE,
    SOCIETY_FILE_EXTENSION,
    HISTORICAL_PERIODS,
    CULTURAL_COMPLEXITY,
    COLUMN_ORDERS,
)

logger = logging.getLogger(__name__)

# JSON Schema for society files
SOCIETY_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["name", "period", "characteristics"],
    "properties": {
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 100,
            "description": "Display name of the society/culture"
        },
        "period": {
            "type": "string",
            "enum": list(HISTORICAL_PERIODS.keys()),
            "description": "Historical period this society belongs to"
        },
        "characteristics": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 20,
            "description": "Key architectural and cultural characteristics"
        },
        "building_types": {
            "type": "object",
            "patternProperties": {
                ".*": {
                    "type": "object",
                    "properties": {
                        "orders": {
                            "type": "array",
                            "items": {"type": "string", "enum": COLUMN_ORDERS}
                        },
                        "roof_style": {"type": "string"},
                        "foundation": {"type": "string"},
                        "materials": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1
                        },
                        "style": {"type": "string"}
                    }
                }
            }
        },
        "proportions": {
            "type": "object",
            "patternProperties": {
                ".*": {"type": ["number", "array"]}
            }
        },
        "color_palette": {
            "type": "array",
            "items": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
            "minItems": 1,
            "maxItems": 10
        },
        "adaptation_rules": {
            "type": "object",
            "patternProperties": {
                "climate_.*": {"type": "object"},
                "terrain_.*": {"type": "object"}
            }
        },
        "building_components": {
            "type": "object",
            "patternProperties": {
                ".*": {
                    "type": "object",
                    "properties": {
                        "ref_w": {"type": "number", "minimum": 0},
                        "ref_d": {"type": "number", "minimum": 0},
                        "components": {
                            "type": "array",
                            "items": {"type": "object"}
                        }
                    }
                }
            }
        },
        "prompt_hints": {
            "type": "object",
            "properties": {
                "variety_suggestions": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "material_alternatives": {"type": "object"},
                "scale_hints": {"type": "object"},
                "grammar_types": {
                    "type": "array",
                    "items": {"type": "string"}
                }
            }
        },
        "template_adaptations": {
            "type": "object",
            "patternProperties": {
                ".*": {
                    "type": "object",
                    "properties": {
                        "cultural_modifiers": {"type": "object"}
                    }
                }
            }
        }
    },
    "additionalProperties": False
}


class SocietyValidationError(Exception):
    """Custom exception for society validation errors."""

    def __init__(self, message: str, file_path: Optional[Path] = None, errors: Optional[List[str]] = None):
        self.file_path = file_path
        self.errors = errors or []
        super().__init__(message)


class SocietyValidator:
    """Validates society JSON files against schema and business rules."""

    def __init__(self):
        self.schema = SOCIETY_SCHEMA
        self._compiled_schema = None

    def validate_file(self, file_path: Path) -> Tuple[bool, List[str]]:
        """Validate a single society file.

        Returns:
            Tuple of (is_valid, error_messages)
        """
        errors = []

        # Check file exists
        if not file_path.exists():
            errors.append(f"File does not exist: {file_path}")
            return False, errors

        # Check file extension (Path.suffix is only the last segment, e.g. ".json" for "x.society.json")
        if not file_path.name.endswith(SOCIETY_FILE_EXTENSION):
            errors.append(f"Invalid file extension. Expected *{SOCIETY_FILE_EXTENSION}, got {file_path.name!r}")

        # Check file size
        file_size = file_path.stat().st_size
        if file_size > MAX_SOCIETY_FILE_SIZE:
            errors.append(f"File too large: {file_size} bytes (max {MAX_SOCIETY_FILE_SIZE})")
            return False, errors

        # Load and parse JSON
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            errors.append(f"Invalid JSON: {e}")
            return False, errors
        except Exception as e:
            errors.append(f"Failed to read file: {e}")
            return False, errors

        # Validate against schema
        schema_errors = self._validate_against_schema(data)
        errors.extend(schema_errors)

        # Validate business rules
        business_errors = self._validate_business_rules(data, file_path)
        errors.extend(business_errors)

        return len(errors) == 0, errors

    def validate_all_societies(self, societies_dir: Path) -> Dict[str, List[str]]:
        """Validate all society files in a directory.

        Returns:
            Dict mapping society names to their validation errors (empty list if valid)
        """
        results = {}

        if not societies_dir.exists():
            raise SocietyValidationError(f"Societies directory does not exist: {societies_dir}")

        society_files = list(societies_dir.glob(f"*{SOCIETY_FILE_EXTENSION}"))

        if not society_files:
            logger.warning(f"No society files found in {societies_dir}")
            return results

        for file_path in society_files:
            society_name = file_path.stem.replace('.society', '')
            is_valid, errors = self.validate_file(file_path)

            if not is_valid:
                results[society_name] = errors
                logger.error(f"Validation failed for {society_name}: {errors}")
            else:
                results[society_name] = []
                logger.info(f"Validation passed for {society_name}")

        return results

    def _validate_against_schema(self, data: Dict[str, Any]) -> List[str]:
        """Validate data against JSON schema."""
        if not HAS_JSONSCHEMA:
            logger.warning("jsonschema is not installed; skipping society JSON schema validation")
            return []
        if self._compiled_schema is None:
            try:
                jsonschema.validate(data, self.schema)
                return []
            except (ValidationError, SchemaError) as e:
                msg = getattr(e, "message", str(e))
                return [f"Schema validation error: {msg}"]

        # Use compiled schema for better performance
        try:
            self._compiled_schema.validate(data)
            return []
        except ValidationError as e:
            return [f"Schema validation error at {e.absolute_path}: {e.message}"]

    def _validate_business_rules(self, data: Dict[str, Any], file_path: Path) -> List[str]:
        """Validate business rules beyond basic schema."""
        errors = []

        # Check for duplicate characteristics
        characteristics = data.get("characteristics", [])
        if len(characteristics) != len(set(characteristics)):
            duplicates = [x for x in characteristics if characteristics.count(x) > 1]
            errors.append(f"Duplicate characteristics found: {set(duplicates)}")

        # Validate color palette uniqueness
        color_palette = data.get("color_palette", [])
        if len(color_palette) != len(set(color_palette)):
            errors.append("Duplicate colors found in color_palette")

        # Validate building type references
        building_types = data.get("building_types", {})
        grammar_types = data.get("prompt_hints", {}).get("grammar_types", [])

        for grammar_type in grammar_types:
            if grammar_type not in building_types:
                errors.append(f"Grammar type '{grammar_type}' not found in building_types")

        # Validate proportion values are reasonable
        proportions = data.get("proportions", {})
        for key, value in proportions.items():
            if isinstance(value, (int, float)):
                if value <= 0:
                    errors.append(f"Proportion '{key}' must be positive, got {value}")
                elif value > 1000:  # Arbitrary large limit
                    errors.append(f"Proportion '{key}' seems unreasonably large: {value}")

        # Validate building component references
        building_components = data.get("building_components", {})
        for component_name, component_data in building_components.items():
            if "ref_w" in component_data and "ref_d" in component_data:
                ref_w = component_data["ref_w"]
                ref_d = component_data["ref_d"]
                if ref_w <= 0 or ref_d <= 0:
                    errors.append(f"Building component '{component_name}' has invalid dimensions: {ref_w}x{ref_d}")

        return errors

    def _basic_validation(self, data: Dict[str, Any]) -> List[str]:
        """Basic validation when jsonschema is not available."""
        errors = []

        # Check required fields
        required_fields = ["name", "period", "characteristics"]
        for field in required_fields:
            if field not in data:
                errors.append(f"Missing required field: {field}")

        # Validate period
        if "period" in data:
            if data["period"] not in HISTORICAL_PERIODS:
                errors.append(f"Invalid period '{data['period']}'. Must be one of: {list(HISTORICAL_PERIODS.keys())}")

        # Validate characteristics
        if "characteristics" in data:
            if not isinstance(data["characteristics"], list):
                errors.append("characteristics must be an array")
            elif len(data["characteristics"]) == 0:
                errors.append("characteristics array cannot be empty")
            elif len(data["characteristics"]) > 20:
                errors.append("characteristics array cannot have more than 20 items")

        # Validate color palette
        if "color_palette" in data:
            if not isinstance(data["color_palette"], list):
                errors.append("color_palette must be an array")
            else:
                for i, color in enumerate(data["color_palette"]):
                    if not isinstance(color, str) or not color.startswith('#') or len(color) != 7:
                        errors.append(f"color_palette[{i}] must be a valid hex color (e.g., #FF0000)")

        return errors

    def get_validation_summary(self, validation_results: Dict[str, List[str]]) -> str:
        """Generate a human-readable summary of validation results."""
        total_societies = len(validation_results)
        valid_societies = sum(1 for errors in validation_results.values() if not errors)
        invalid_societies = total_societies - valid_societies

        summary = f"Society Validation Summary: {valid_societies}/{total_societies} valid"

        if invalid_societies > 0:
            summary += f"\n\nFailed validations ({invalid_societies}):"
            for society, errors in validation_results.items():
                if errors:
                    summary += f"\n\n{society}:"
                    for error in errors:
                        summary += f"\n  - {error}"

        return summary


# Global validator instance
society_validator = SocietyValidator()


def validate_society_file(file_path: Path) -> None:
    """Validate a single society file and raise exception on failure."""
    is_valid, errors = society_validator.validate_file(file_path)
    if not is_valid:
        raise SocietyValidationError(
            f"Validation failed for {file_path.name}",
            file_path=file_path,
            errors=errors
        )


def validate_all_societies(societies_dir: Path) -> Dict[str, List[str]]:
    """Validate all society files in directory."""
    return society_validator.validate_all_societies(societies_dir)