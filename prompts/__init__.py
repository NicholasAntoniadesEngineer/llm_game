"""Prompt loader — reads LLM system prompts from text files in this directory.

Usage:
    from prompts import load_prompt, load_data
    text = load_prompt("urbanista")                       # no substitution
    text = load_prompt("cartographus_plan_skeleton",      # with variables
                       GRID_WIDTH=320, GRID_HEIGHT=320)

Template variables use Python str.format_map() syntax: {VARIABLE_NAME}.
Literal braces in the text (JSON examples) must be doubled: {{ and }}.

Data helpers load JSON from data/ and format it for prompt injection.
"""

import json
from pathlib import Path

_DIR = Path(__file__).parent
_DATA_DIR = _DIR.parent / "data"

# Cache loaded data files (they don't change at runtime)
_data_cache: dict[str, object] = {}


def load_prompt(name: str, **kwargs) -> str:
    """Load prompts/{name}.txt, optionally substituting {KEY} placeholders."""
    path = _DIR / f"{name}.txt"
    text = path.read_text(encoding="utf-8")
    if kwargs:
        text = text.format_map(kwargs)
    return text


def load_data(name: str) -> object:
    """Load data/{name}.json, returning the parsed object. Cached after first load."""
    if name not in _data_cache:
        path = _DATA_DIR / f"{name}.json"
        _data_cache[name] = json.loads(path.read_text(encoding="utf-8"))
    return _data_cache[name]


def format_building_types() -> str:
    """Format building_types.json into a compact prompt-ready string.

    Includes architectural keywords when available to guide the AI
    toward historically accurate, varied building forms.
    """
    types = load_data("building_types")
    parts = []
    for t in types:
        entry = f"{t['type']}={t['description']}"
        if t.get("arch_keywords"):
            entry += f" [{t['arch_keywords']}]"
        parts.append(entry)
    return " | ".join(parts)


def format_material_palette() -> str:
    """Format material_palette.json into a prompt-ready key=value string."""
    palette = load_data("material_palette")
    return " ".join(f"{name}={hex}" for name, hex in palette.items())


def format_pbr_hint(building_type: str) -> str:
    """Get PBR material hint for a building type. Raises KeyError if not found."""
    hints = load_data("pbr_hints")
    if building_type in hints:
        return hints[building_type]
    raise KeyError(
        f"No PBR hint for building_type={building_type!r}. "
        f"Add an entry to data/pbr_hints.json. Known types: {sorted(hints.keys())}"
    )


def format_composition_directive(seed: int) -> str:
    """Select a composition directive based on a seed value."""
    directives = load_data("composition_directives")
    return directives[seed % len(directives)]


def format_compact_neighbors(neighbors: list[dict]) -> str:
    """Format neighbor structures into compact NB: notation for prompt injection.

    Input: list of dicts with keys: direction, name, building_type, color, height
    Output: "NB:N:Temple(temple,#f0ece4,h=12);E:Via Sacra(road)"
    """
    from orchestration.schema import format_compact_neighbors as _fmt
    return _fmt(neighbors)


def format_dense_shape_legend() -> str:
    """Return a compact legend of dense shape type codes for prompt injection."""
    from orchestration.schema import DENSE_SHAPE_CODES
    return " ".join(f"{code}={name}" for code, name in sorted(DENSE_SHAPE_CODES.items(), key=lambda x: x[1]))
