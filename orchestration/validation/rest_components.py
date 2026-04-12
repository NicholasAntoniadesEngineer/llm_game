"""Low-level Urbanista component validation (colors, procedural parts, templates).

Dense array formats are expanded elsewhere before these checks run. Public entry
points live in ``rest_flow`` and ``rest_geometry``; this module holds shared helpers.
"""

from __future__ import annotations

import logging

from core.errors import UrbanistaValidationError
from orchestration.schema import (
    _DEFAULT_STACK_ROLES,
    _HEX_COLOR,
    _MAP_URL_MAX_LEN,
    _NAMED_COLOR_MAP,
    _PHASE4_BOOL_KEYS,
    _PHASE4_HEX_KEYS,
    _PHASE4_NUM_KEYS,
    _STACK_ROLE_ORDER,
    GRAMMAR_IDS,
    MATERIAL_NAMES,
    MAX_PROCEDURAL_PARTS,
    PARAMETRIC_TEMPLATE_IDS,
    PROCEDURAL_SHAPES,
    RENDERER_COMPONENT_TYPES,
    STACK_ROLES,
    expand_dense_shapes_in_result,
    resolve_color,
)

logger = logging.getLogger("eternal.validation")


def _is_valid_color(c: str) -> bool:
    """Accept #RRGGBB hex, named colors, or material names."""
    if not isinstance(c, str):
        return False
    s = c.strip()
    if _HEX_COLOR.match(s):
        return True
    lower = s.lower()
    return lower in _NAMED_COLOR_MAP or lower in MATERIAL_NAMES


def _require_color(part: dict, ctx: str) -> None:
    c = part.get("color")
    if not isinstance(c, str) or not _is_valid_color(c):
        raise UrbanistaValidationError(f"{ctx}: each procedural part requires color as #RRGGBB hex or named material")


def _validate_optional_pbr(comp: dict, ctx: str) -> None:
    """Optional roughness / metalness / surface_detail / detail_repeat / map_url — must match renderer3d _matPBR."""
    for key in ("roughness", "metalness"):
        v = comp.get(key)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise UrbanistaValidationError(f"{ctx}: {key} must be a number")
        if not (0.0 <= float(v) <= 1.0):
            raise UrbanistaValidationError(f"{ctx}: {key} must be between 0 and 1")

    sd = comp.get("surface_detail")
    if sd is not None:
        if isinstance(sd, bool) or not isinstance(sd, (int, float)):
            raise UrbanistaValidationError(f"{ctx}: surface_detail must be a number")
        if not (0.0 <= float(sd) <= 1.0):
            raise UrbanistaValidationError(f"{ctx}: surface_detail must be between 0 and 1")

    dr = comp.get("detail_repeat")
    if dr is not None:
        if isinstance(dr, bool) or not isinstance(dr, (int, float)):
            raise UrbanistaValidationError(f"{ctx}: detail_repeat must be a number")
        if not (0.5 <= float(dr) <= 40.0):
            raise UrbanistaValidationError(f"{ctx}: detail_repeat must be between 0.5 and 40")

    mu = comp.get("map_url")
    if mu is not None:
        if not isinstance(mu, str) or not mu.strip():
            raise UrbanistaValidationError(f"{ctx}: map_url must be a non-empty string")
        s = mu.strip()
        if len(s) > _MAP_URL_MAX_LEN:
            raise UrbanistaValidationError(f"{ctx}: map_url exceeds max length")
        if not (s.startswith("https://") or s.startswith("http://")):
            raise UrbanistaValidationError(f"{ctx}: map_url must start with http:// or https://")


def _validate_procedural_component(comp: dict, ctx: str) -> None:
    # stack_role is required for procedural components - validation should be strict
    role = comp.get("stack_role")
    if not role or role not in STACK_ROLES:
        raise UrbanistaValidationError(
            f"{ctx}: procedural component missing or invalid stack_role {role!r}; "
            f"required: one of {sorted(STACK_ROLES)}. This should be auto-fixed by sanitization."
        )

    parts = comp.get("parts")
    if not isinstance(parts, list) or len(parts) == 0:
        raise UrbanistaValidationError(f"{ctx}: procedural requires non-empty parts array")
    if len(parts) > MAX_PROCEDURAL_PARTS:
        raise UrbanistaValidationError(
            f"{ctx}: procedural parts exceed max ({MAX_PROCEDURAL_PARTS})"
        )
    recipe = comp.get("recipe")
    if recipe is not None and not isinstance(recipe, str):
        raise UrbanistaValidationError(f"{ctx}: procedural.recipe must be a string if present")

    # Track X,Z positions to detect degenerate vertical-line layouts
    xz_positions: set[tuple[float, float]] = set()

    for i, p in enumerate(parts):
        pc = f"{ctx} parts[{i}]"
        if not isinstance(p, dict):
            raise UrbanistaValidationError(f"{pc}: must be an object")
        shape = p.get("shape")
        if shape not in PROCEDURAL_SHAPES:
            raise UrbanistaValidationError(
                f"{pc}: shape must be one of {sorted(PROCEDURAL_SHAPES)}, got {shape!r}"
            )
        _require_color(p, pc)
        _validate_optional_pbr(p, pc)

        # Ensure position field exists (renderer defaults to [0,0,0])
        pos = p.get("position")
        if pos is not None:
            if not isinstance(pos, list) or len(pos) < 3:
                raise UrbanistaValidationError(f"{pc}: position must be [x,y,z] (3 numbers)")
            try:
                px_val = float(pos[0])
                pz_val = float(pos[2])
                xz_positions.add((round(px_val, 4), round(pz_val, 4)))
            except (TypeError, ValueError, IndexError):
                pass
        else:
            xz_positions.add((0.0, 0.0))

        if shape == "box":
            if p.get("size") is not None:
                if not isinstance(p.get("size"), list) or len(p["size"]) != 3:
                    raise UrbanistaValidationError(f"{pc}: box.size must be [sx,sy,sz]")
            else:
                for k in ("width", "height", "depth"):
                    if p.get(k) is None:
                        raise UrbanistaValidationError(
                            f"{pc}: box needs width, height, depth or size[3]"
                        )
        elif shape == "cylinder":
            for k in ("radius", "height"):
                if p.get(k) is None:
                    raise UrbanistaValidationError(f"{pc}: cylinder requires radius and height")
        elif shape == "sphere":
            if p.get("radius") is None:
                raise UrbanistaValidationError(f"{pc}: sphere requires radius")
        elif shape == "cone":
            for k in ("radius", "height"):
                if p.get(k) is None:
                    raise UrbanistaValidationError(f"{pc}: cone requires radius and height")
        elif shape == "torus":
            for k in ("radius", "tube"):
                if p.get(k) is None:
                    raise UrbanistaValidationError(f"{pc}: torus requires radius and tube")
        elif shape == "plane":
            for k in ("width", "height"):
                if p.get(k) is None:
                    raise UrbanistaValidationError(f"{pc}: plane requires width and height (XZ extent)")

    # Warn about degenerate layouts where all parts share the same X,Z
    # (produces a vertical line of floating objects instead of a building)
    if len(parts) >= 3 and len(xz_positions) <= 1:
        logger.warning(
            "%s: all %d procedural parts share the same X,Z position — "
            "building will appear as a vertical stack. "
            "Spread parts across X and Z for a coherent building shape.",
            ctx, len(parts),
        )

    # Enhanced architectural coherence validation
    _validate_architectural_coherence(parts, ctx)


def _validate_architectural_coherence(parts: list, ctx: str) -> None:
    """Validate architectural coherence of procedural parts.

    Checks for:
    1. Proper base-to-top stacking (foundation at bottom, roof at top)
    2. Structural integrity (walls support roofs, doors in walls)
    3. Scale relationships (proportions make architectural sense)
    4. Material consistency within architectural roles
    5. Opening placement (doors/windows in appropriate locations)
    """
    if not parts:
        return

    # Analyze Y positions for stacking coherence
    y_positions = []
    for i, part in enumerate(parts):
        pos = part.get("position", [0, 0, 0])
        if isinstance(pos, list) and len(pos) >= 2:
            try:
                y = float(pos[1])
                y_positions.append((y, i, part))
            except (TypeError, ValueError):
                continue

    y_positions.sort(key=lambda x: x[0])  # Sort by Y position

    # Check for proper architectural layering
    base_parts = []
    middle_parts = []
    top_parts = []

    for y, idx, part in y_positions:
        shape = part.get("shape", "")
        if y < 0.1:  # Near ground level
            base_parts.append((y, idx, part))
        elif y > 0.5:  # High up
            top_parts.append((y, idx, part))
        else:  # Middle
            middle_parts.append((y, idx, part))

    # Validate foundation exists
    has_foundation = any(
        part.get("shape") in ("box", "cylinder") and
        part.get("position", [0, 0, 0])[1] < 0.05
        for _, _, part in base_parts
    )

    if len(parts) > 3 and not has_foundation:
        logger.warning(
            "%s: Large building (%d parts) lacks clear foundation/base structure. "
            "Consider adding a base platform or podium at Y=0.",
            ctx, len(parts)
        )

    # Check for structural walls if there are openings
    has_walls = any(
        part.get("shape") == "box" and
        max(part.get("width", 0), part.get("depth", 0)) > min(part.get("width", 0), part.get("depth", 0)) * 2
        for _, _, part in base_parts + middle_parts
    )

    door_parts = [part for _, _, part in y_positions if part.get("shape") == "box" and
                 part.get("position", [0, 0, 0])[1] < 0.3 and
                 part.get("width", 0) < 0.3 and part.get("height", 0) < 0.4]

    if door_parts and not has_walls:
        logger.warning(
            "%s: Door-like openings detected but no enclosing walls found. "
            "Doors should be placed within wall structures.",
            ctx
        )

    # Check scale relationships
    volumes = []
    for _, _, part in y_positions:
        try:
            if part.get("shape") == "box":
                w = float(part.get("width", 0.2))
                h = float(part.get("height", 0.2))
                d = float(part.get("depth", w))
                volumes.append(w * h * d)
            elif part.get("shape") in ("cylinder", "cone"):
                r = float(part.get("radius", 0.1))
                h = float(part.get("height", 0.2))
                volumes.append(3.14159 * r * r * h)
            elif part.get("shape") == "sphere":
                r = float(part.get("radius", 0.1))
                volumes.append(4/3 * 3.14159 * r * r * r)
        except (TypeError, ValueError):
            continue

    if volumes:
        avg_volume = sum(volumes) / len(volumes)
        max_volume = max(volumes)
        min_volume = min(volumes)

        # Warn if volume ratios are extreme
        if max_volume / min_volume > 50:
            logger.warning(
                "%s: Extreme size differences between parts (ratio %.1f:1). "
                "Consider more consistent architectural proportions.",
                ctx, max_volume / min_volume
            )

        # Check for parts that are too small relative to the building
        tiny_parts = sum(1 for v in volumes if v < avg_volume * 0.01)
        if tiny_parts > len(volumes) * 0.3:
            logger.warning(
                "%s: Many parts are very small (%d/%d < 1%% of average volume). "
                "Consider consolidating tiny details or increasing their scale.",
                ctx, tiny_parts, len(volumes)
            )

    # Validate material consistency by architectural role
    material_roles = {}
    for _, _, part in y_positions:
        y_pos = part.get("position", [0, 0, 0])[1]
        color = part.get("color", "").lower()

        # Infer architectural role from position and shape
        if y_pos < 0.1:
            role = "foundation"
        elif y_pos > 0.6:
            role = "roof"
        elif part.get("shape") in ("cylinder", "cone") and part.get("radius", 0) < 0.05:
            role = "column"
        elif part.get("shape") == "box" and part.get("height", 0) > part.get("width", 0) * 2:
            role = "wall"
        else:
            role = "general"

        if role not in material_roles:
            material_roles[role] = []
        material_roles[role].append(color)

    # Check for inconsistent materials within roles
    for role, colors in material_roles.items():
        if len(colors) > 1:
            # Simple check: warn if too many different colors for same role
            unique_colors = set(colors)
            if len(unique_colors) > 3 and len(colors) > 5:
                logger.warning(
                    "%s: %s elements use %d different colors. "
                    "Consider more consistent material usage within architectural roles.",
                    ctx, role, len(unique_colors)
                )

    # Check for openings without proper framing
    opening_shapes = ["box"]  # Could expand to include arch shapes
    openings = [part for _, _, part in y_positions
               if part.get("shape") in opening_shapes and
               part.get("position", [0, 0, 0])[1] < 0.4 and
               max(part.get("width", 0), part.get("depth", 0)) < 0.4]

    if openings and len(parts) > 5:
        # Look for surrounding structural elements
        structural_count = sum(1 for _, _, part in y_positions
                              if part.get("shape") in ("box", "cylinder") and
                              part.get("position", [0, 0, 0])[1] < 0.5 and
                              max(part.get("width", 0), part.get("depth", 0)) > 0.3)

        if structural_count < 2:
            logger.warning(
                "%s: Openings detected but insufficient surrounding structure. "
                "Openings should be framed by walls or structural elements.",
                ctx
            )


def _validate_template(tmpl: dict, ctx: str) -> None:
    if not isinstance(tmpl, dict):
        raise UrbanistaValidationError(f"{ctx}: template must be an object")
    tid = tmpl.get("id")
    if not isinstance(tid, str) or not tid.strip():
        raise UrbanistaValidationError(f"{ctx}: template.id must be a non-empty string")
    if tid not in PARAMETRIC_TEMPLATE_IDS:
        raise UrbanistaValidationError(
            f"{ctx}: template.id must be one of {sorted(PARAMETRIC_TEMPLATE_IDS)}"
        )
    params = tmpl.get("params")
    if tid == "open":
        if not isinstance(params, dict):
            raise UrbanistaValidationError(f"{ctx}: template open requires params as an object")
        oc = params.get("components")
        if not isinstance(oc, list) or len(oc) == 0:
            raise UrbanistaValidationError(
                f"{ctx}: template open requires params.components as a non-empty array"
            )
        for i, c in enumerate(oc):
            _validate_component(c, f"{ctx} template.open.params.components[{i}]")
        rw = params.get("ref_w")
        rd = params.get("ref_d")
        if (rw is not None or rd is not None) and not (
            isinstance(rw, (int, float))
            and not isinstance(rw, bool)
            and isinstance(rd, (int, float))
            and not isinstance(rd, bool)
            and rw > 0
            and rd > 0
        ):
            raise UrbanistaValidationError(
                f"{ctx}: template open: ref_w and ref_d must both be positive numbers if either is set"
            )
        return
    if params is not None and not isinstance(params, dict):
        raise UrbanistaValidationError(f"{ctx}: template.params must be an object if present")


def _validate_phase4(p4: dict, ctx: str) -> None:
    if not isinstance(p4, dict):
        raise UrbanistaValidationError(f"{ctx}: phase4 must be an object")
    for key, val in p4.items():
        if key in _PHASE4_BOOL_KEYS:
            if not isinstance(val, bool):
                raise UrbanistaValidationError(f"{ctx}.phase4.{key}: must be a boolean")
        elif key in _PHASE4_HEX_KEYS:
            if not isinstance(val, str) or not _HEX_COLOR.match(val):
                raise UrbanistaValidationError(f"{ctx}.phase4.{key}: must be #RRGGBB hex")
        elif key in _PHASE4_NUM_KEYS:
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise UrbanistaValidationError(f"{ctx}.phase4.{key}: must be a number")
            if key == "ruin_overgrowth" and not (0.0 <= float(val) <= 1.0):
                raise UrbanistaValidationError(f"{ctx}.phase4.ruin_overgrowth: must be between 0 and 1")
        else:
            raise UrbanistaValidationError(
                f"{ctx}.phase4: unknown key {key!r}; allowed keys are "
                f"{sorted(_PHASE4_BOOL_KEYS | _PHASE4_HEX_KEYS | _PHASE4_NUM_KEYS)}"
            )


def _validate_component(comp: dict, ctx: str) -> None:
    if not isinstance(comp, dict):
        raise UrbanistaValidationError(f"{ctx}: component must be an object")
    ct = comp.get("type")
    if not isinstance(ct, str) or not ct.strip():
        raise UrbanistaValidationError(f"{ctx}: component missing 'type' field (required string)")
    if ct not in RENDERER_COMPONENT_TYPES:
        # Provide helpful suggestions for common mistakes
        suggestions = []
        if ct in ("roof", "tile_roof"):
            suggestions.append("use 'tiled_roof' instead")
        elif ct in ("hip_roof", "hipped"):
            suggestions.append("use 'hipped_roof' instead")
        elif ct in ("wall", "exterior_wall"):
            suggestions.append("use 'walls' instead")
        elif ct in ("column", "pillar"):
            suggestions.append("use 'colonnade' or procedural parts")
        elif ct in ("window", "opening"):
            suggestions.append("use procedural parts with box shapes")

        suggestion_text = f" Suggestions: {', '.join(suggestions)}" if suggestions else ""
        raise UrbanistaValidationError(
            f"{ctx}: unknown component type {ct!r}; allowed types: {sorted(RENDERER_COMPONENT_TYPES)}. "
            f"Use 'procedural' with parts[] for custom shapes.{suggestion_text}"
        )
    if ct == "procedural":
        _validate_procedural_component(comp, ctx)
        _validate_optional_pbr(comp, ctx)
        return
    if ct == "colonnade":
        st = comp.get("style")
        if not isinstance(st, str) or st.lower() not in ("doric", "ionic", "corinthian", "tuscan", "composite"):
            raise UrbanistaValidationError(
                f"{ctx}: colonnade requires style as one of doric, ionic, corinthian, tuscan, composite"
            )
        for req in ("columns", "height", "radius"):
            if comp.get(req) is None:
                raise UrbanistaValidationError(f"{ctx}: colonnade requires {req!r}")
    if comp.get("stack_role") is not None and comp.get("stack_role") not in STACK_ROLES:
        raise UrbanistaValidationError(f"{ctx}: stack_role must be one of {sorted(STACK_ROLES)}")
    rel = comp.get("relates_to")
    if rel is not None:
        if not isinstance(rel, list):
            raise UrbanistaValidationError(f"{ctx}: relates_to must be an array")
        for j, r in enumerate(rel):
            if not isinstance(r, dict):
                raise UrbanistaValidationError(f"{ctx} relates_to[{j}]: must be object")
            if r.get("target_id") is not None and not isinstance(r["target_id"], str):
                raise UrbanistaValidationError(f"{ctx} relates_to[{j}]: target_id must be string")
            if r.get("relation") is not None and not isinstance(r["relation"], str):
                raise UrbanistaValidationError(f"{ctx} relates_to[{j}]: relation must be string")

    _validate_optional_pbr(comp, ctx)

