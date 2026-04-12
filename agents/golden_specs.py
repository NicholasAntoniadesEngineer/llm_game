"""Golden reference specs — hand-tuned, architecturally correct component specs.
Injected as few-shot examples into the Urbanista prompt so the AI knows what
correct proportions look like at the actual building size."""

import copy
import json
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Each spec is tuned for a reference footprint size.
# Heights and dimensions will be scaled proportionally to the actual footprint.
GOLDEN_SPECS = json.loads((_DATA_DIR / "golden_specs.json").read_text())

# Every `building_type` the agents may emit must exist in GOLDEN_SPECS (explicit keys; no runtime defaulting).
_EXPLICIT_GOLDEN_ALIASES = json.loads((_DATA_DIR / "golden_aliases.json").read_text())

for _alias_key, _canonical_key in _EXPLICIT_GOLDEN_ALIASES.items():
    if _alias_key not in GOLDEN_SPECS:
        GOLDEN_SPECS[_alias_key] = copy.deepcopy(GOLDEN_SPECS[_canonical_key])


# ── Culture-specific golden specs ────────────────────────────────────────
# Overrides for building types whose proportions, materials, and colours
# differ significantly from the Mediterranean defaults.

_CULTURE_SPEC_OVERRIDES = json.loads((_DATA_DIR / "culture_specs.json").read_text())

# City-name substrings (lowercased) mapped to culture groups.
_CITY_CULTURE_MAP = json.loads((_DATA_DIR / "culture_city_map.json").read_text())


def _detect_culture(city: str) -> str | None:
    """Return the culture group key for *city*, or None (= Mediterranean default)."""
    if not city:
        return None
    city_lower = city.lower()
    for keyword, culture in _CITY_CULTURE_MAP.items():
        if keyword in city_lower:
            return culture
    return None


def _resolve_spec(building_type: str, culture: str | None) -> dict:
    """Pick the best spec dict entry for *building_type* given *culture*.

    Raises KeyError when there is no matching spec at all.
    Priority:
      1. Culture-specific override for the exact building_type
      2. Culture-specific override for the alias target
      3. Base spec from GOLDEN_SPECS (which already includes aliases)
    """
    if culture:
        overrides = _CULTURE_SPEC_OVERRIDES.get(culture, {})
        if building_type in overrides:
            return overrides[building_type]
        alias_target = _EXPLICIT_GOLDEN_ALIASES.get(building_type)
        if alias_target and alias_target in overrides:
            return overrides[alias_target]
    if building_type in GOLDEN_SPECS:
        return GOLDEN_SPECS[building_type]
    raise KeyError(
        f"No golden spec for building_type={building_type!r}, culture={culture!r}. "
        f"Add an entry to data/golden_specs.json or data/golden_aliases.json. "
        f"Known types: {sorted(GOLDEN_SPECS.keys())}"
    )


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
    """Return a scaled golden spec as a JSON string for prompt injection.
    Raises KeyError if building_type has no spec."""
    if building_type not in GOLDEN_SPECS:
        raise KeyError(
            f"No golden spec for building_type={building_type!r}. "
            f"Add to data/golden_specs.json. Known: {sorted(GOLDEN_SPECS.keys())}"
        )
    return _scale_spec(GOLDEN_SPECS[building_type], target_w, target_d)


def get_golden_example_for_culture(building_type, target_w, target_d, city="", year=0):
    """Return a culture-aware scaled golden spec as a JSON string.
    Raises KeyError if building_type has no spec in any culture or base set."""
    culture = _detect_culture(city)
    ref = _resolve_spec(building_type, culture)  # Raises KeyError if not found
    return _scale_spec(ref, target_w, target_d)


def infer_culture_key_for_prompt(city_loc: str) -> str:
    """Culture group key for prompts; defaults to roman when the city has no culture_city_map entry."""
    culture = _detect_culture(city_loc)
    return culture if culture else "roman"
