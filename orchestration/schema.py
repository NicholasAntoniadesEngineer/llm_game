"""Contract definitions: component types, shapes, roles, and validation constants.

These constants define the renderer contract shared between the Python orchestration
layer and static/renderer3d.js / static/parametric_templates.js.  Extracted from
orchestration/validation.py so that other modules can depend on the schema without
pulling in the full validation logic.
"""

from __future__ import annotations

import re

# ── Renderer component & shape vocabulary ──────────────────────────────

# Must match static/renderer3d.js component builders + procedural
RENDERER_COMPONENT_TYPES = frozenset({
    "podium", "colonnade", "pediment", "dome", "block", "arcade",
    "tiled_roof", "atrium", "statue", "fountain", "awning", "battlements",
    "tier", "door", "pilasters", "vault", "flat_roof", "cella", "walls",
    "procedural",
})

PROCEDURAL_SHAPES = frozenset({
    "box", "cylinder", "sphere", "cone", "torus", "plane",
    # Compound shapes (renderer expands into multiple meshes)
    "stacked_tower", "tiered_pyramid", "colonnade_ring", "water_channel", "arch",
})

STACK_ROLES = frozenset({
    "foundation", "structural", "infill", "roof", "decorative", "freestanding",
})

MAX_PROCEDURAL_PARTS = 48

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

# ── Named-color lookup ─────────────────────────────────────────────────

_NAMED_COLOR_MAP = {
    "white": "#F0F0F0", "black": "#1A1008", "red": "#CC3333", "blue": "#2E86AB",
    "green": "#3A7D44", "gold": "#FFD700", "bronze": "#8B6914", "silver": "#C0C0C0",
    "grey": "#808080", "gray": "#808080", "brown": "#6B4226", "orange": "#CC5533",
    "yellow": "#DAA520", "cream": "#F5E6C8", "tan": "#C8B070", "beige": "#F0EAD6",
    "terracotta": "#C45A3C", "sandstone": "#C8B070", "marble": "#F0F0F0",
    "limestone": "#F5E6C8", "granite": "#808080", "obsidian": "#2A2A2A",
    "jade": "#3A7D44", "ivory": "#FFFFF0", "copper": "#8B6914",
}

# ── Stack-role ordering & defaults ─────────────────────────────────────

_STACK_ROLE_ORDER = ["foundation", "structural", "infill", "roof", "decorative", "freestanding"]
_DEFAULT_STACK_ROLES = {
    "podium": "foundation", "colonnade": "structural", "block": "structural",
    "walls": "structural", "arcade": "structural", "cella": "infill",
    "atrium": "infill", "tier": "infill", "pediment": "roof", "dome": "roof",
    "tiled_roof": "roof", "flat_roof": "roof", "vault": "roof",
    "door": "decorative", "pilasters": "decorative", "awning": "decorative",
    "battlements": "decorative", "statue": "freestanding", "fountain": "freestanding",
    "procedural": "structural",
}
