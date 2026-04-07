"""Contract definitions: component types, shapes, roles, and validation constants.

These constants define the renderer contract shared between the Python orchestration
layer and static/renderer3d.js / static/parametric_templates.js.  Extracted from
orchestration/validation.py so that other modules can depend on the schema without
pulling in the full validation logic.

Supports both verbose (dict) and dense (array) formats for shapes and tiles.
Dense format saves ~40-60% tokens per building in LLM output.
"""

from __future__ import annotations

import json
import re

# ── Renderer component & shape vocabulary ──────────────────────────────

# Must match static/renderer3d.js component builders + procedural
RENDERER_COMPONENT_TYPES = frozenset({
    "podium", "colonnade", "pediment", "dome", "block", "arcade",
    "tiled_roof", "hipped_roof", "atrium", "statue", "fountain", "awning",
    "battlements", "tier", "door", "pilasters", "vault", "flat_roof",
    "cella", "walls", "procedural",
})

PROCEDURAL_SHAPES = frozenset({
    "box", "cylinder", "sphere", "cone", "torus", "plane",
    "hemisphere",
    # Compound shapes (renderer expands into multiple meshes)
    "stacked_tower", "tiered_pyramid", "colonnade_ring", "water_channel",
    "arch", "barrel_roof", "buttress", "apse", "tower", "shed_roof",
    "balustrade", "lattice_screen", "platform", "stairs", "colonnade",
    "ring", "wedge",
})

STACK_ROLES = frozenset({
    "foundation", "structural", "infill", "roof", "decorative", "freestanding",
})

MAX_PROCEDURAL_PARTS = 48

# ── Dense shape type codes ─────────────────────────────────────────────
# Maps short codes used in dense array format to full shape names.
# Dense format: [code, [x,y,z], [sx,sy,sz], color_or_material, ...extras]

DENSE_SHAPE_CODES: dict[str, str] = {
    "b": "box",
    "c": "cylinder",
    "n": "cone",
    "s": "sphere",
    "t": "torus",
    "h": "hemisphere",
    "br": "barrel_roof",
    "a": "arch",
    "w": "wedge",
    "st": "stairs",
    "co": "colonnade",
    "r": "ring",
    "p": "platform",
    "ls": "lattice_screen",
    "tw": "tower",
    "ap": "apse",
    "bt": "buttress",
    "sd": "shed_roof",
    "bl": "balustrade",
    "pl": "plane",
    "skt": "stacked_tower",
    "tp": "tiered_pyramid",
    "cr": "colonnade_ring",
    "wc": "water_channel",
}

# Reverse map: full name -> code
_SHAPE_CODE_REVERSE: dict[str, str] = {v: k for k, v in DENSE_SHAPE_CODES.items()}

# ── Dense tile keys ────────────────────────────────────────────────────
# Maps short keys in dense tile format to full verbose keys.
# Dense tile: {"n":"Temple","bt":"temple","x":5,"y":10,"s":[...shapes...]}

DENSE_TILE_KEYS: dict[str, str] = {
    "n": "building_name",
    "bt": "building_type",
    "s": "spec",
    "d": "description",
    "e": "elevation",
    "t": "terrain",
    "c": "color",
}

# ── Grammar definitions ────────────────────────────────────────────────
# Architectural grammar identifiers for client-side expansion.
# Grammar tile: {"g":"roman_temple","p":{"order":"corinthian","cols":8,"steps":3}}

GRAMMAR_IDS = frozenset({
    "roman_temple", "basilica", "insula", "domus", "amphitheater",
    "thermae", "triumphal_arch", "aqueduct", "taberna", "warehouse",
    "monument",
})

COLUMN_ORDERS = frozenset({
    "doric", "ionic", "corinthian", "tuscan", "composite",
})

# ── Material name -> hex lookup ────────────────────────────────────────
# Dense shapes can use material names instead of hex: ["b",[0,.5,0],[.8,1,.8],"travertine"]

MATERIAL_NAMES: dict[str, str] = {
    "travertine": "#F5E6C8",
    "tufa": "#A09880",
    "marble": "#F0F0F0",
    "granite": "#808080",
    "porphyry": "#6B2D5B",
    "brick": "#B85C3A",
    "concrete": "#A09880",
    "terracotta": "#C45A3C",
    "bronze": "#8B6914",
    "gilded": "#FFD700",
    "stucco": "#F0EAD6",
    "wood": "#6B4226",
}

# ── Field-level constraints ────────────────────────────────────────────

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_MAP_URL_MAX_LEN = 2048

# ── Phase-4 contextual polish keys ─────────────────────────────────────
# Must match static/renderer3d.js _applyPhase4ContextualPolish

_PHASE4_BOOL_KEYS = frozenset({
    "disable_all",
    "disable_auto_steps",
    "disable_party_walls",
    "disable_street_fascia",
    "disable_water_mooring",
    "disable_garden_hedge",
    "disable_ruin_vegetation",
    "disable_road_awning",
    "disable_street_signs",
})
_PHASE4_HEX_KEYS = frozenset({
    "step_color",
    "party_wall_color",
    "street_front_color",
    "awning_color",
    "sign_color",
})
_PHASE4_NUM_KEYS = frozenset({
    "ruin_overgrowth",
    "party_wall_height",
    "street_fascia_height",
    "awning_height",
})

# ── Parametric template ids ────────────────────────────────────────────
# Must match static/parametric_templates.js TEMPLATES keys

PARAMETRIC_TEMPLATE_IDS = frozenset({
    "open",
    "temple",
    "basilica",
    "insula",
    "domus",
    "thermae",
    "amphitheater",
    "market",
    "monument",
    "gate",
    "wall",
    "aqueduct",
    "mesoamerican_temple",
    "mesoamerican_shrine",
    "mesoamerican_civic",
})

# ── Named-color lookup (superset: includes material names + common color words) ──

_NAMED_COLOR_MAP = {
    # Common color words
    "white": "#F0F0F0", "black": "#1A1008", "red": "#CC3333", "blue": "#2E86AB",
    "green": "#3A7D44", "gold": "#FFD700", "bronze": "#8B6914", "silver": "#C0C0C0",
    "grey": "#808080", "gray": "#808080", "brown": "#6B4226", "orange": "#CC5533",
    "yellow": "#DAA520", "cream": "#F5E6C8", "tan": "#C8B070", "beige": "#F0EAD6",
    "terracotta": "#C45A3C", "sandstone": "#C8B070", "marble": "#F0F0F0",
    "limestone": "#F5E6C8", "granite": "#808080", "obsidian": "#2A2A2A",
    "jade": "#3A7D44", "ivory": "#FFFFF0", "copper": "#8B6914",
    # Material names (dense format)
    "travertine": "#F5E6C8", "tufa": "#A09880", "porphyry": "#6B2D5B",
    "brick": "#B85C3A", "concrete": "#A09880", "gilded": "#FFD700",
    "stucco": "#F0EAD6", "wood": "#6B4226",
}

# ── Stack-role ordering & defaults ─────────────────────────────────────

_STACK_ROLE_ORDER = ["foundation", "structural", "infill", "roof", "decorative", "freestanding"]
_DEFAULT_STACK_ROLES = {
    "podium": "foundation", "colonnade": "structural", "block": "structural",
    "walls": "structural", "arcade": "structural", "cella": "infill",
    "atrium": "infill", "tier": "infill", "pediment": "roof", "dome": "roof",
    "tiled_roof": "roof", "hipped_roof": "roof", "flat_roof": "roof", "vault": "roof",
    "door": "decorative", "pilasters": "decorative", "awning": "decorative",
    "battlements": "decorative", "statue": "freestanding", "fountain": "freestanding",
    "procedural": "structural",
}


# ── Dense format expansion ─────────────────────────────────────────────

def resolve_color(color_or_material: str) -> str:
    """Resolve a color string: material name, named color, or hex pass-through."""
    if not isinstance(color_or_material, str):
        return "#808080"
    s = color_or_material.strip()
    if _HEX_COLOR.match(s):
        return s
    lower = s.lower()
    if lower in MATERIAL_NAMES:
        return MATERIAL_NAMES[lower]
    if lower in _NAMED_COLOR_MAP:
        return _NAMED_COLOR_MAP[lower]
    return "#808080"


def expand_dense_shape(arr: list) -> dict:
    """Expand a dense shape array into a verbose dict.

    Dense: [code, [x,y,z], [sx,sy,sz], color, ...optional_extras]
    Verbose: {"shape":"box","position":[x,y,z],"width":sx,"height":sy,"depth":sz,"color":"#hex"}

    For shapes that use radius/height instead of width/height/depth, the size
    array is interpreted contextually.
    """
    if not isinstance(arr, list) or len(arr) < 4:
        return {}

    code = str(arr[0])
    shape = DENSE_SHAPE_CODES.get(code, code)  # Fall back to raw if not a code
    pos = arr[1] if isinstance(arr[1], list) and len(arr[1]) == 3 else [0, 0, 0]
    size = arr[2] if isinstance(arr[2], list) else [0.2, 0.2, 0.2]
    color = resolve_color(str(arr[3]))

    result: dict = {"shape": shape, "position": pos, "color": color}

    # Map size array to shape-specific keys
    if shape in ("box", "plane", "wedge", "platform", "lattice_screen",
                  "barrel_roof", "shed_roof", "stairs"):
        if len(size) >= 3:
            result["width"] = size[0]
            result["height"] = size[1]
            result["depth"] = size[2]
        elif len(size) == 2:
            result["width"] = size[0]
            result["height"] = size[1]
    elif shape in ("cylinder", "cone", "hemisphere"):
        if len(size) >= 2:
            result["radius"] = size[0]
            result["height"] = size[1]
        elif len(size) == 1:
            result["radius"] = size[0]
    elif shape == "sphere":
        result["radius"] = size[0] if len(size) >= 1 else 0.1
    elif shape == "torus":
        if len(size) >= 2:
            result["radius"] = size[0]
            result["tube"] = size[1]
    elif shape in ("arch", "tower", "apse", "buttress"):
        if len(size) >= 3:
            result["width"] = size[0]
            result["height"] = size[1]
            result["depth"] = size[2]
    elif shape in ("stacked_tower", "tiered_pyramid"):
        if len(size) >= 3:
            result["base_width"] = size[0]
            result["height"] = size[1]
            result["base_depth"] = size[2]
    elif shape in ("colonnade_ring", "colonnade", "ring"):
        if len(size) >= 2:
            result["radius"] = size[0]
            result["height"] = size[1]
    elif shape == "water_channel":
        if len(size) >= 3:
            result["width"] = size[0]
            result["height"] = size[1]
            result["depth"] = size[2]
    elif shape == "balustrade":
        if len(size) >= 2:
            result["width"] = size[0]
            result["height"] = size[1]
    else:
        # Generic fallback: treat as w/h/d
        if len(size) >= 3:
            result["width"] = size[0]
            result["height"] = size[1]
            result["depth"] = size[2]

    # Parse optional extras dict (5th element)
    if len(arr) >= 5 and isinstance(arr[4], dict):
        result.update(arr[4])

    return result


def expand_dense_tile(tile: dict) -> dict:
    """Expand a dense-format tile dict into verbose format.

    Handles:
    - Short tile keys (n -> building_name, bt -> building_type, etc.)
    - Dense shape arrays in spec
    - Grammar references (g/p keys)
    - Passes through verbose-format tiles unchanged
    """
    if not isinstance(tile, dict):
        return tile

    out = {}
    has_dense_keys = any(k in tile for k in ("n", "bt", "g"))

    if not has_dense_keys:
        # Already verbose format — just resolve colors in shapes if needed
        return tile

    # Map dense tile keys to verbose
    for k, v in tile.items():
        verbose_key = DENSE_TILE_KEYS.get(k, k)
        out[verbose_key] = v

    # Ensure x/y pass through
    if "x" in tile:
        out["x"] = tile["x"]
    if "y" in tile:
        out["y"] = tile["y"]

    # Handle grammar reference
    if "g" in tile:
        out["grammar"] = tile["g"]
        if "p" in tile:
            out["grammar_params"] = tile["p"]
        # Grammar tiles need terrain=building
        out.setdefault("terrain", "building")

    # Handle dense shapes in spec
    spec = out.get("spec")
    if isinstance(spec, list):
        # spec is an array of dense shapes — wrap into components
        expanded_components = []
        for item in spec:
            if isinstance(item, list):
                expanded_components.append(expand_dense_shape(item))
            elif isinstance(item, dict):
                expanded_components.append(item)
        out["spec"] = {
            "components": [{
                "type": "procedural",
                "stack_role": "structural",
                "parts": expanded_components,
            }]
        }

    return out


def expand_dense_shapes_in_result(arch_result: dict) -> dict:
    """Walk an Urbanista result and expand any dense-format data in-place.

    This runs BEFORE sanitize/validate so both old and new formats are
    normalized to the verbose dict format the renderer expects.
    """
    if not isinstance(arch_result, dict):
        return arch_result

    tiles = arch_result.get("tiles")
    if not isinstance(tiles, list):
        return arch_result

    expanded_tiles = []
    for tile in tiles:
        expanded_tiles.append(expand_dense_tile(tile))

    arch_result["tiles"] = expanded_tiles

    # Also expand dense shapes inside spec.components[].parts[]
    for tile in arch_result["tiles"]:
        if not isinstance(tile, dict):
            continue
        spec = tile.get("spec")
        if not isinstance(spec, dict):
            continue
        for comp in spec.get("components", []):
            if not isinstance(comp, dict):
                continue
            parts = comp.get("parts")
            if not isinstance(parts, list):
                continue
            expanded_parts = []
            for part in parts:
                if isinstance(part, list):
                    expanded_parts.append(expand_dense_shape(part))
                elif isinstance(part, dict):
                    expanded_parts.append(part)
                else:
                    expanded_parts.append(part)
            comp["parts"] = expanded_parts

    return arch_result


def format_compact_neighbors(neighbors: list[dict]) -> str:
    """Format neighbor structures into compact NB: notation for prompt injection.

    Input: list of dicts with keys: direction, name, building_type, color, height
    Output: "NB:N:Temple(temple,#f0ece4,h=12);E:Via Sacra(road)"
    """
    if not neighbors:
        return ""
    parts = []
    for nb in neighbors:
        if not isinstance(nb, dict):
            continue
        d = nb.get("direction", "?")
        name = nb.get("name", "?")
        btype = nb.get("building_type", "")
        color = nb.get("color", "")
        height = nb.get("height")
        detail = btype
        if color:
            detail += f",{color}"
        if height is not None:
            detail += f",h={height}"
        parts.append(f"{d}:{name}({detail})")
    return "NB:" + ";".join(parts) if parts else ""
