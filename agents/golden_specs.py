"""Golden reference specs — hand-tuned, architecturally correct component specs.
Injected as few-shot examples into the Urbanista prompt so the AI knows what
correct proportions look like at the actual building size."""

import copy
import json

# Each spec is tuned for a reference footprint size.
# Heights and dimensions will be scaled proportionally to the actual footprint.
GOLDEN_SPECS = {
    "temple": {
        "ref_w": 2.7, "ref_d": 1.8,
        "components": [
            {"type": "podium", "steps": 5, "height": 0.12, "color": "#F5E6C8"},
            {"type": "colonnade", "columns": 8, "style": "ionic", "height": 0.42, "color": "#808080", "radius": 0.022},
            {"type": "cella", "height": 0.34, "width": 1.4, "depth": 0.9, "color": "#C8B070"},
            {"type": "pediment", "height": 0.09, "color": "#C45A3C"},
            {"type": "pilasters", "count": 4, "height": 0.35, "color": "#808080"},
            {"type": "door", "width": 0.12, "height": 0.22, "color": "#6B4226"},
        ]
    },
    "basilica": {
        "ref_w": 3.6, "ref_d": 1.8,
        "components": [
            {"type": "podium", "steps": 3, "height": 0.08, "color": "#F5E6C8"},
            {"type": "block", "stories": 1, "storyHeight": 0.45, "color": "#F5E6C8", "windows": 6, "windowColor": "#1A1008"},
            {"type": "colonnade", "columns": 10, "style": "corinthian", "height": 0.38, "color": "#F0F0F0", "radius": 0.018, "peripteral": False},
            {"type": "tiled_roof", "height": 0.1, "color": "#C45A3C"},
            {"type": "door", "width": 0.14, "height": 0.28, "color": "#6B4226"},
            {"type": "pilasters", "count": 6, "height": 0.38, "color": "#F0EAD6"},
        ]
    },
    "insula": {
        "ref_w": 1.8, "ref_d": 1.8,
        "components": [
            {"type": "block", "stories": 4, "storyHeight": 0.18, "color": "#B85C3A", "windows": 4, "windowColor": "#1A1008"},
            {"type": "tiled_roof", "height": 0.08, "color": "#C45A3C"},
            {"type": "door", "width": 0.1, "height": 0.2, "color": "#6B4226"},
        ]
    },
    "domus": {
        "ref_w": 2.7, "ref_d": 1.8,
        "components": [
            {"type": "walls", "height": 0.35, "color": "#F0EAD6", "thickness": 0.06},
            {"type": "atrium", "height": 0.25, "color": "#F0EAD6"},
            {"type": "tiled_roof", "height": 0.08, "color": "#C45A3C"},
            {"type": "door", "width": 0.1, "height": 0.2, "color": "#6B4226"},
            {"type": "colonnade", "columns": 4, "style": "ionic", "height": 0.25, "color": "#F0F0F0", "radius": 0.012, "peripteral": False},
        ]
    },
    "thermae": {
        "ref_w": 3.6, "ref_d": 2.7,
        "components": [
            {"type": "podium", "steps": 2, "height": 0.06, "color": "#F5E6C8"},
            {"type": "block", "stories": 1, "storyHeight": 0.4, "color": "#B85C3A", "windows": 5, "windowColor": "#1A1008"},
            {"type": "dome", "radius": 0.28, "color": "#A09880"},
            {"type": "colonnade", "columns": 6, "style": "corinthian", "height": 0.32, "color": "#F0F0F0", "radius": 0.015, "peripteral": False},
            {"type": "fountain", "radius": 0.12, "height": 0.15, "color": "#F0F0F0"},
            {"type": "door", "width": 0.14, "height": 0.25, "color": "#6B4226"},
        ]
    },
    "amphitheater": {
        "ref_w": 3.6, "ref_d": 3.6,
        "components": [
            {"type": "arcade", "arches": 8, "height": 0.35, "color": "#F5E6C8"},
            {"type": "tier", "height": 0.15, "color": "#F5E6C8"},
            {"type": "tier", "height": 0.12, "color": "#A09880"},
            {"type": "tier", "height": 0.1, "color": "#A09880"},
            {"type": "pilasters", "count": 8, "height": 0.3, "color": "#F5E6C8"},
        ]
    },
    "market": {
        "ref_w": 1.8, "ref_d": 0.9,
        "components": [
            {"type": "block", "stories": 1, "storyHeight": 0.3, "color": "#B85C3A", "windows": 2, "windowColor": "#1A1008"},
            {"type": "awning", "color": "#CC3333"},
            {"type": "flat_roof", "color": "#A09880"},
            {"type": "door", "width": 0.12, "height": 0.2, "color": "#6B4226"},
        ]
    },
    "monument": {
        "ref_w": 1.8, "ref_d": 1.8,
        "components": [
            {"type": "podium", "steps": 5, "height": 0.2, "color": "#F0F0F0"},
            {"type": "statue", "height": 0.35, "color": "#8B6914", "pedestalColor": "#F0F0F0"},
            {"type": "colonnade", "columns": 4, "style": "corinthian", "height": 0.3, "color": "#F0F0F0", "radius": 0.012},
        ]
    },
    "gate": {
        "ref_w": 1.8, "ref_d": 0.9,
        "components": [
            {"type": "arcade", "arches": 1, "height": 0.5, "color": "#F5E6C8"},
            {"type": "battlements", "height": 0.08, "color": "#C8B070"},
            {"type": "colonnade", "columns": 4, "style": "corinthian", "height": 0.4, "color": "#F0F0F0", "radius": 0.015, "peripteral": False},
            {"type": "flat_roof", "color": "#F5E6C8", "overhang": 0.04},
        ]
    },
    "wall": {
        "ref_w": 0.9, "ref_d": 0.9,
        "components": [
            {"type": "walls", "height": 0.45, "color": "#C8B070", "thickness": 0.1},
            {"type": "battlements", "height": 0.08, "color": "#C8B070"},
        ]
    },
    "aqueduct": {
        "ref_w": 0.9, "ref_d": 2.7,
        "components": [
            {"type": "arcade", "arches": 4, "height": 0.6, "color": "#C8B070"},
            {"type": "flat_roof", "color": "#C8B070"},
        ]
    },
}

# Every `building_type` the agents may emit must exist in GOLDEN_SPECS (explicit keys; no runtime defaulting).
_EXPLICIT_GOLDEN_ALIASES = {
    "building": "temple",
    "forum": "basilica",
    "road": "market",
    "water": "market",
    "garden": "domus",
    "grass": "domus",
    "circus": "amphitheater",
    "bridge": "aqueduct",
    "taberna": "market",
    "warehouse": "insula",
}

for _alias_key, _canonical_key in _EXPLICIT_GOLDEN_ALIASES.items():
    if _alias_key not in GOLDEN_SPECS:
        GOLDEN_SPECS[_alias_key] = copy.deepcopy(GOLDEN_SPECS[_canonical_key])


# ── Culture-specific golden specs ────────────────────────────────────────
# Overrides for building types whose proportions, materials, and colours
# differ significantly from the Mediterranean defaults.

GOLDEN_SPECS_EAST_ASIAN = {
    "temple": {
        "ref_w": 3.6, "ref_d": 2.7,
        "components": [
            {"type": "podium", "steps": 3, "height": 0.08, "color": "#C4A77D", "roughness": 0.85},
            {"type": "block", "stories": 1, "storyHeight": 0.4, "color": "#6B4226", "windows": 4, "windowColor": "#1A1008", "roughness": 0.7},
            {"type": "colonnade", "columns": 6, "style": "doric", "height": 0.35, "color": "#6B4226", "radius": 0.025, "roughness": 0.65},
            {"type": "tiled_roof", "height": 0.18, "color": "#2A2A2A", "roughness": 0.5},
            {"type": "door", "width": 0.14, "height": 0.25, "color": "#CC3333", "roughness": 0.4},
        ]
    },
    "basilica": {
        "ref_w": 4.5, "ref_d": 2.7,
        "components": [
            {"type": "podium", "steps": 5, "height": 0.12, "color": "#C4A77D", "roughness": 0.85},
            {"type": "block", "stories": 1, "storyHeight": 0.5, "color": "#CC3333", "windows": 6, "windowColor": "#1A1008", "roughness": 0.6},
            {"type": "colonnade", "columns": 8, "style": "doric", "height": 0.4, "color": "#CC3333", "radius": 0.03, "roughness": 0.55},
            {"type": "tiled_roof", "height": 0.22, "color": "#DAA520", "roughness": 0.45},
        ]
    },
    "gate": {
        "ref_w": 2.7, "ref_d": 1.8,
        "components": [
            {"type": "podium", "steps": 2, "height": 0.06, "color": "#C4A77D", "roughness": 0.85},
            {"type": "arcade", "arches": 3, "height": 0.5, "color": "#C4A77D", "roughness": 0.75},
            {"type": "block", "stories": 1, "storyHeight": 0.3, "color": "#CC3333", "windows": 3, "windowColor": "#1A1008"},
            {"type": "tiled_roof", "height": 0.15, "color": "#2A2A2A"},
            {"type": "battlements", "height": 0.06, "color": "#C4A77D"},
        ]
    },
}

GOLDEN_SPECS_MESOAMERICAN = {
    "temple": {
        "ref_w": 4.5, "ref_d": 4.5,
        "components": [
            {"type": "podium", "steps": 8, "height": 0.35, "color": "#C4A77D", "roughness": 0.9, "surface_detail": 0.7},
            {"type": "podium", "steps": 6, "height": 0.25, "color": "#C4A77D", "roughness": 0.85, "surface_detail": 0.6},
            {"type": "podium", "steps": 4, "height": 0.15, "color": "#CC5533", "roughness": 0.8},
            {"type": "block", "stories": 1, "storyHeight": 0.25, "color": "#CC5533", "windows": 0, "windowColor": "#1A1008"},
            {"type": "flat_roof", "color": "#C4A77D", "overhang": 0.02},
        ]
    },
}

GOLDEN_SPECS_MIDDLE_EASTERN = {
    "temple": {
        "ref_w": 3.6, "ref_d": 2.7,
        "components": [
            {"type": "podium", "steps": 2, "height": 0.06, "color": "#F5E6C8", "roughness": 0.8},
            {"type": "arcade", "arches": 6, "height": 0.4, "color": "#F5E6C8", "roughness": 0.65},
            {"type": "dome", "radius": 0.3, "color": "#DAA520", "roughness": 0.35, "metalness": 0.15},
            {"type": "walls", "height": 0.3, "thickness": 0.08, "color": "#F0EAD6", "roughness": 0.7},
        ]
    },
}

GOLDEN_SPECS_SOUTH_ASIAN = {
    "temple": {
        "ref_w": 4.5, "ref_d": 3.6,
        "components": [
            {"type": "podium", "steps": 4, "height": 0.1, "color": "#C8B070", "roughness": 0.85, "surface_detail": 0.6},
            {"type": "walls", "height": 0.35, "thickness": 0.08, "color": "#C8B070", "roughness": 0.75, "surface_detail": 0.5},
            {"type": "procedural", "stack_role": "structural", "recipe": "shikhara_tower",
             "parts": [
                 {"shape": "box", "width": 0.35, "height": 0.5, "depth": 0.35, "color": "#C8B070", "position": [0, 0.2, 0], "roughness": 0.7, "surface_detail": 0.65},
                 {"shape": "box", "width": 0.28, "height": 0.35, "depth": 0.28, "color": "#C8B070", "position": [0, 0.55, 0], "roughness": 0.65, "surface_detail": 0.6},
                 {"shape": "box", "width": 0.2, "height": 0.25, "depth": 0.2, "color": "#C8B070", "position": [0, 0.8, 0], "roughness": 0.6},
                 {"shape": "sphere", "radius": 0.08, "color": "#DAA520", "position": [0, 1.0, 0], "roughness": 0.3, "metalness": 0.2},
             ]},
            {"type": "procedural", "stack_role": "decorative", "recipe": "gopuram_tiers",
             "parts": [
                 {"shape": "box", "width": 0.6, "height": 0.08, "depth": 0.15, "color": "#CC3333", "position": [0, 0.0, 0.25], "roughness": 0.7},
                 {"shape": "box", "width": 0.5, "height": 0.08, "depth": 0.12, "color": "#2E86AB", "position": [0, 0.08, 0.25], "roughness": 0.65},
                 {"shape": "box", "width": 0.4, "height": 0.06, "depth": 0.1, "color": "#DAA520", "position": [0, 0.16, 0.25], "roughness": 0.5},
                 {"shape": "cylinder", "radius": 0.04, "height": 0.06, "color": "#FFD700", "position": [0, 0.22, 0.25], "roughness": 0.25, "metalness": 0.3},
             ]},
            {"type": "door", "width": 0.12, "height": 0.2, "color": "#6B4226", "roughness": 0.6},
            {"type": "colonnade", "columns": 6, "style": "doric", "height": 0.3, "color": "#C8B070", "radius": 0.02, "roughness": 0.7},
        ]
    },
    "basilica": {
        "ref_w": 4.5, "ref_d": 3.6,
        "components": [
            {"type": "podium", "steps": 3, "height": 0.08, "color": "#C8B070", "roughness": 0.85},
            {"type": "colonnade", "columns": 8, "style": "doric", "height": 0.35, "color": "#C8B070", "radius": 0.022, "roughness": 0.7},
            {"type": "block", "stories": 2, "storyHeight": 0.25, "color": "#C8B070", "windows": 5, "windowColor": "#1A1008", "roughness": 0.7, "surface_detail": 0.4},
            {"type": "flat_roof", "color": "#C4A77D", "overhang": 0.04},
            {"type": "dome", "radius": 0.2, "color": "#F0F0F0", "roughness": 0.4},
        ]
    },
    "gate": {
        "ref_w": 2.7, "ref_d": 1.8,
        "components": [
            {"type": "podium", "steps": 2, "height": 0.06, "color": "#C8B070", "roughness": 0.8},
            {"type": "procedural", "stack_role": "structural", "recipe": "gopuram_gate",
             "parts": [
                 {"shape": "box", "width": 0.7, "height": 0.6, "depth": 0.3, "color": "#C8B070", "position": [0, 0, 0], "roughness": 0.75, "surface_detail": 0.55},
                 {"shape": "box", "width": 0.6, "height": 0.4, "depth": 0.25, "color": "#CC3333", "position": [0, 0.5, 0], "roughness": 0.65},
                 {"shape": "box", "width": 0.45, "height": 0.3, "depth": 0.2, "color": "#2E86AB", "position": [0, 0.8, 0], "roughness": 0.6},
                 {"shape": "cylinder", "radius": 0.06, "height": 0.08, "color": "#FFD700", "position": [0, 1.0, 0], "roughness": 0.3, "metalness": 0.25},
             ]},
        ]
    },
}

GOLDEN_SPECS_SOUTHEAST_ASIAN = {
    "temple": {
        "ref_w": 5.4, "ref_d": 5.4,
        "components": [
            {"type": "podium", "steps": 3, "height": 0.06, "color": "#808080", "roughness": 0.85, "surface_detail": 0.7},
            {"type": "procedural", "stack_role": "foundation", "recipe": "temple_mountain_base",
             "parts": [
                 {"shape": "box", "width": 0.9, "height": 0.2, "depth": 0.9, "color": "#808080", "position": [0, 0, 0], "roughness": 0.8, "surface_detail": 0.65},
                 {"shape": "box", "width": 0.7, "height": 0.2, "depth": 0.7, "color": "#808080", "position": [0, 0.2, 0], "roughness": 0.75, "surface_detail": 0.6},
                 {"shape": "box", "width": 0.5, "height": 0.2, "depth": 0.5, "color": "#808080", "position": [0, 0.4, 0], "roughness": 0.7, "surface_detail": 0.55},
             ]},
            {"type": "procedural", "stack_role": "structural", "recipe": "prasat_tower",
             "parts": [
                 {"shape": "box", "width": 0.3, "height": 0.4, "depth": 0.3, "color": "#A09880", "position": [0, 0.1, 0], "roughness": 0.7, "surface_detail": 0.6},
                 {"shape": "cone", "radius": 0.18, "height": 0.3, "color": "#A09880", "position": [0, 0.5, 0], "roughness": 0.65},
                 {"shape": "sphere", "radius": 0.05, "color": "#DAA520", "position": [0, 0.75, 0], "roughness": 0.3, "metalness": 0.2},
             ]},
            {"type": "door", "width": 0.1, "height": 0.18, "color": "#4A4A4A", "roughness": 0.7},
        ]
    },
}

# Mapping from culture group name to its override dict.
_CULTURE_SPEC_OVERRIDES = {
    "east_asian":       GOLDEN_SPECS_EAST_ASIAN,
    "mesoamerican":     GOLDEN_SPECS_MESOAMERICAN,
    "middle_eastern":   GOLDEN_SPECS_MIDDLE_EASTERN,
    "south_asian":      GOLDEN_SPECS_SOUTH_ASIAN,
    "southeast_asian":  GOLDEN_SPECS_SOUTHEAST_ASIAN,
}

# City-name substrings (lowercased) mapped to culture groups.
_CITY_CULTURE_MAP = {
    # East Asian
    "chang'an": "east_asian",
    "changan":  "east_asian",
    "luoyang":  "east_asian",
    "nara":     "east_asian",
    "kyoto":    "east_asian",
    "heian":    "east_asian",
    "kaifeng":  "east_asian",
    "hangzhou": "east_asian",
    "beijing":  "east_asian",
    "nanjing":  "east_asian",
    "edo":      "east_asian",
    # Mesoamerican
    "tenochtitlan": "mesoamerican",
    "tikal":        "mesoamerican",
    "chichen":      "mesoamerican",
    "palenque":     "mesoamerican",
    "copan":        "mesoamerican",
    "tula":         "mesoamerican",
    "monte alban":  "mesoamerican",
    # Middle Eastern
    "baghdad":   "middle_eastern",
    "jerusalem": "middle_eastern",
    "damascus":  "middle_eastern",
    "isfahan":   "middle_eastern",
    "cairo":     "middle_eastern",
    "samarkand": "middle_eastern",
    "mecca":     "middle_eastern",
    "medina":    "middle_eastern",
    "petra":     "middle_eastern",
    "palmyra":   "middle_eastern",
    "ctesiphon": "middle_eastern",
    "persepolis": "middle_eastern",
    # South Asian
    "varanasi":      "south_asian",
    "vijayanagara":  "south_asian",
    "hampi":         "south_asian",
    "pataliputra":   "south_asian",
    "taxila":        "south_asian",
    "madurai":       "south_asian",
    "thanjavur":     "south_asian",
    "delhi":         "south_asian",
    "agra":          "south_asian",
    "fatehpur":      "south_asian",
    "mohenjo":       "south_asian",
    # Southeast Asian
    "angkor":        "southeast_asian",
    "pagan":         "southeast_asian",
    "bagan":         "southeast_asian",
    "ayutthaya":     "southeast_asian",
    "borobudur":     "southeast_asian",
    "prambanan":     "southeast_asian",
    "sukhothai":     "southeast_asian",
}


def _detect_culture(city: str) -> str | None:
    """Return the culture group key for *city*, or None (= Mediterranean default)."""
    if not city:
        return None
    city_lower = city.lower()
    for keyword, culture in _CITY_CULTURE_MAP.items():
        if keyword in city_lower:
            return culture
    return None


def _resolve_spec(building_type: str, culture: str | None) -> dict | None:
    """Pick the best spec dict entry for *building_type* given *culture*.

    Returns None when there is no matching spec at all (caller should raise).
    Priority:
      1. Culture-specific override for the exact building_type
      2. Culture-specific override for the alias target
      3. Mediterranean default (GOLDEN_SPECS)
    """
    if culture:
        overrides = _CULTURE_SPEC_OVERRIDES.get(culture, {})
        if building_type in overrides:
            return overrides[building_type]
        alias_target = _EXPLICIT_GOLDEN_ALIASES.get(building_type)
        if alias_target and alias_target in overrides:
            return overrides[alias_target]
    # Fallback to the Mediterranean defaults (which already include aliases).
    return GOLDEN_SPECS.get(building_type)


def _scale_spec(ref: dict, target_w: float, target_d: float) -> str:
    """Scale a golden spec to the target footprint and return JSON string."""
    ref_w, ref_d = ref["ref_w"], ref["ref_d"]
    scale = ((target_w / ref_w) + (target_d / ref_d)) / 2

    scaled = []
    for comp in ref["components"]:
        c = dict(comp)
        for key in ("height", "radius", "width", "depth", "thickness", "storyHeight"):
            if key in c:
                c[key] = round(c[key] * scale, 4)
        scaled.append(c)

    return json.dumps(scaled, indent=2)


def get_golden_example(building_type, target_w, target_d):
    """Return a scaled golden spec as a JSON string for prompt injection."""
    ref = GOLDEN_SPECS.get(building_type)
    if not ref:
        raise ValueError(
            f"No golden spec for building_type={building_type!r}. "
            f"Add an entry to GOLDEN_SPECS or use a known type."
        )
    return _scale_spec(ref, target_w, target_d)


def get_golden_example_for_culture(building_type, target_w, target_d, city="", year=0):
    """Return a culture-aware scaled golden spec as a JSON string.

    Checks *city* against known culture groups and returns culture-appropriate
    specs when available.  Falls back to the Mediterranean defaults for
    unrecognised cities.
    """
    culture = _detect_culture(city)
    ref = _resolve_spec(building_type, culture)
    if not ref:
        raise ValueError(
            f"No golden spec for building_type={building_type!r}. "
            f"Add an entry to GOLDEN_SPECS or use a known type."
        )
    return _scale_spec(ref, target_w, target_d)
