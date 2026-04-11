"""Validate survey master plans and Urbanista output against the grid and renderer contract.

Supports both verbose (dict) and dense (array) formats.  Dense data is expanded
to verbose form *before* validation so the rest of the pipeline is format-agnostic.
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
    role = comp.get("stack_role")
    if role not in STACK_ROLES:
        raise UrbanistaValidationError(
            f"{ctx}: type procedural requires stack_role in {sorted(STACK_ROLES)}"
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
        raise UrbanistaValidationError(f"{ctx}: component missing type")
    if ct not in RENDERER_COMPONENT_TYPES:
        raise UrbanistaValidationError(
            f"{ctx}: unknown component type {ct!r}; use a known type or type procedural with parts[]"
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



def sanitize_urbanista_output(arch_result: dict) -> dict:
    """Auto-correct common Urbanista errors before validation. Returns a cleaned copy.

    Handles:
    1. Dense format expansion (arrays -> dicts, short keys -> verbose keys)
    2. Material/color name resolution to #RRGGBB hex
    3. Missing stack_role / parts on procedural components
    4. Grammar reference passthrough
    """
    if not isinstance(arch_result, dict):
        return arch_result

    # Step 1: Expand any dense format data to verbose dicts
    arch_result = expand_dense_shapes_in_result(arch_result)

    fixes = 0
    tiles = arch_result.get("tiles")
    if not isinstance(tiles, list):
        return arch_result
    for td in tiles:
        if not isinstance(td, dict):
            continue

        # Grammar tiles are valid — mark terrain and pass through
        if td.get("grammar"):
            td.setdefault("terrain", "building")
            fixes += 1

        spec = td.get("spec")
        if not isinstance(spec, dict):
            continue
        # Fix color names → hex in components
        for comp in spec.get("components", []):
            if not isinstance(comp, dict):
                continue
            c = comp.get("color")
            if isinstance(c, str) and not c.startswith("#"):
                resolved = resolve_color(c)
                if resolved != "#808080" or c.lower().strip() in _NAMED_COLOR_MAP or c.lower().strip() in MATERIAL_NAMES:
                    comp["color"] = resolved
                    fixes += 1
            # Fix procedural with missing parts
            if comp.get("type") == "procedural" and not comp.get("parts"):
                comp["parts"] = [{"shape": "box", "width": 0.2, "height": 0.2, "depth": 0.2, "color": comp.get("color", "#808080"), "position": [0, 0, 0]}]
                if not comp.get("stack_role"):
                    comp["stack_role"] = "structural"
                fixes += 1
            # Fix missing stack_role on procedural
            if comp.get("type") == "procedural" and not comp.get("stack_role"):
                comp["stack_role"] = "structural"
                fixes += 1
        # Fix color names / material names in procedural parts
        # Also ensure every part has an explicit position to prevent silent [0,0,0] defaults
        for comp in spec.get("components", []):
            if not isinstance(comp, dict):
                continue
            for part in comp.get("parts", []):
                if not isinstance(part, dict):
                    continue
                c = part.get("color")
                if isinstance(c, str) and not c.startswith("#"):
                    resolved = resolve_color(c)
                    if resolved != "#808080" or c.lower().strip() in _NAMED_COLOR_MAP or c.lower().strip() in MATERIAL_NAMES:
                        part["color"] = resolved
                        fixes += 1
                # Ensure position exists — renderer defaults to [0,0,0] but
                # being explicit prevents silent vertical-line layouts
                if part.get("position") is None:
                    part["position"] = [0, 0, 0]
                    fixes += 1
        # Fix double-offset: when a spec mixes foundation components (podium, etc.)
        # with procedural structural/infill/roof components, the renderer adds
        # the foundation height (anchorY) to procedural Y positions automatically.
        # If the LLM already accounted for the foundation in its Y positions
        # (absolute from ground), the shapes float above their intended location.
        # Detect and correct by shifting procedural Y positions down.
        comps = spec.get("components", [])
        if isinstance(comps, list) and len(comps) >= 2:
            # Estimate foundation height from foundation-role components
            foundation_h = 0.0
            has_foundation = False
            for comp in comps:
                if not isinstance(comp, dict):
                    continue
                role = comp.get("stack_role", _DEFAULT_STACK_ROLES.get(comp.get("type", ""), ""))
                if role == "foundation":
                    has_foundation = True
                    h = comp.get("height")
                    if isinstance(h, (int, float)) and not isinstance(h, bool) and h > 0:
                        foundation_h = max(foundation_h, float(h))
                    elif comp.get("type") == "podium":
                        steps = comp.get("steps", 3)
                        if isinstance(steps, (int, float)):
                            foundation_h = max(foundation_h, int(steps) * 0.06)

            if has_foundation and foundation_h > 0.01:
                for comp in comps:
                    if not isinstance(comp, dict) or comp.get("type") != "procedural":
                        continue
                    role = comp.get("stack_role", "structural")
                    if role not in ("structural", "infill", "roof", "decorative"):
                        continue
                    parts = comp.get("parts", [])
                    if not isinstance(parts, list) or not parts:
                        continue
                    # Check if parts' Y positions look like absolute (include foundation offset)
                    min_y = None
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        pos = part.get("position")
                        if isinstance(pos, list) and len(pos) >= 2:
                            py = pos[1]
                            if isinstance(py, (int, float)):
                                if min_y is None or py < min_y:
                                    min_y = py
                    # If the lowest procedural Y is >= foundation height,
                    # the LLM likely used absolute positions. Shift down by foundation_h
                    # so the renderer's anchorY addition produces the correct result.
                    if min_y is not None and min_y >= foundation_h * 0.8:
                        for part in parts:
                            if not isinstance(part, dict):
                                continue
                            pos = part.get("position")
                            if isinstance(pos, list) and len(pos) >= 2:
                                if isinstance(pos[1], (int, float)):
                                    pos[1] = pos[1] - foundation_h
                        fixes += 1
                        logger.debug(
                            "Shifted procedural Y positions down by %.3f (foundation offset)",
                            foundation_h,
                        )

    if fixes:
        logger.info("Sanitized Urbanista output: %d auto-corrections applied", fixes)
    return arch_result


def validate_urbanista_arch_result(arch_result: dict) -> dict:
    """
    Validate building specs: each anchor tile must have spec.components OR spec.template
    OR a grammar reference (mutually exclusive). Template ids match
    static/parametric_templates.js. Grammar tiles carry "grammar" + optional
    "grammar_params". Secondary multi-tile cells may carry only spec.anchor.
    Raises UrbanistaValidationError on violation.

    Assumes dense format has already been expanded by sanitize_urbanista_output.
    """
    if not isinstance(arch_result, dict):
        raise UrbanistaValidationError("Urbanista result must be a JSON object")
    tiles_in = arch_result.get("tiles")
    if tiles_in is None:
        return arch_result
    if not isinstance(tiles_in, list):
        raise UrbanistaValidationError("tiles must be an array")

    for td in tiles_in:
        if not isinstance(td, dict):
            continue

        # Grammar tiles are valid with just grammar + grammar_params
        grammar = td.get("grammar")
        if grammar is not None:
            if not isinstance(grammar, str) or grammar not in GRAMMAR_IDS:
                raise UrbanistaValidationError(
                    f"tile ({td.get('x')},{td.get('y')}): grammar must be one of {sorted(GRAMMAR_IDS)}, got {grammar!r}"
                )
            gparams = td.get("grammar_params")
            if gparams is not None and not isinstance(gparams, dict):
                raise UrbanistaValidationError(
                    f"tile ({td.get('x')},{td.get('y')}): grammar_params must be an object"
                )
            # Grammar tiles skip spec validation — the grammar engine handles expansion
            continue

        spec = td.get("spec")
        if not isinstance(spec, dict):
            continue
        p4 = spec.get("phase4")
        if p4 is not None:
            _validate_phase4(p4, f"tile ({td.get('x')},{td.get('y')})")
        trad = spec.get("tradition")
        if trad is not None and not isinstance(trad, str):
            raise UrbanistaValidationError(
                f"tile ({td.get('x')},{td.get('y')}): tradition must be a string"
            )
        xy = td.get("x"), td.get("y")
        comps = spec.get("components")
        tmpl = spec.get("template")
        terrain = td.get("terrain")

        if terrain == "building":
            anchor = spec.get("anchor")
            is_secondary = (
                isinstance(anchor, dict)
                and anchor.get("x") is not None
                and anchor.get("y") is not None
                and (td.get("x"), td.get("y")) != (anchor.get("x"), anchor.get("y"))
            )
            if not is_secondary:
                has_tmpl = isinstance(tmpl, dict) and isinstance(tmpl.get("id"), str) and str(tmpl.get("id")).strip()
                has_comps = isinstance(comps, list) and len(comps) > 0
                if has_tmpl and has_comps:
                    raise UrbanistaValidationError(
                        f"tile ({td.get('x')},{td.get('y')}): use either spec.template or spec.components, not both"
                    )
                if has_tmpl:
                    _validate_template(tmpl, f"tile ({td.get('x')},{td.get('y')})")
                elif has_comps:
                    for i, c in enumerate(comps):
                        _validate_component(c, f"tile {xy} component[{i}]")
                else:
                    raise UrbanistaValidationError(
                        f"tile ({td.get('x')},{td.get('y')}): building anchor spec requires "
                        "spec.template or non-empty spec.components"
                    )
        elif isinstance(comps, list):
            for i, c in enumerate(comps):
                _validate_component(c, f"tile {xy} component[{i}]")

    return arch_result


# ── Geometry collision detection ─────────────────────────────────────────
# Simulates the renderer's component stacking and checks for bounding-box
# intersections between components. Returns a list of collision descriptions
# that can be fed back to Urbanista for regeneration.


def _resolve_stack_role(comp: dict) -> str:
    if comp.get("stack_role") and comp["stack_role"] in _STACK_ROLE_ORDER:
        return comp["stack_role"]
    return _DEFAULT_STACK_ROLES.get(comp.get("type", ""), "structural")


def _estimate_component_bounds(comp: dict, base_y: float, w: float, d: float) -> dict | None:
    """Estimate the 3D axis-aligned bounding box (AABB) for a component.

    Returns {min_x, max_x, min_y, max_y, min_z, max_z, top_y, label} or None if unknown.
    """
    ctype = comp.get("type", "")
    label = f"{ctype}"

    if ctype == "podium":
        h = float(comp.get("height", (comp.get("steps", 3)) * 0.06))
        return {"min_x": -w / 2, "max_x": w / 2, "min_y": base_y, "max_y": base_y + h,
                "min_z": -d / 2, "max_z": d / 2, "top_y": base_y + h, "label": label}

    if ctype == "block":
        stories = int(comp.get("stories", 1))
        story_h = float(comp.get("storyHeight", 0.3))
        h = stories * story_h
        return {"min_x": -w / 2, "max_x": w / 2, "min_y": base_y, "max_y": base_y + h,
                "min_z": -d / 2, "max_z": d / 2, "top_y": base_y + h, "label": label}

    if ctype == "walls":
        h = float(comp.get("height", 0.5))
        t = float(comp.get("thickness", 0.08))
        return {"min_x": -w / 2, "max_x": w / 2, "min_y": base_y, "max_y": base_y + h,
                "min_z": -d / 2, "max_z": d / 2, "top_y": base_y + h, "label": label}

    if ctype == "colonnade":
        h = float(comp.get("height", 0.7))
        return {"min_x": -w / 2, "max_x": w / 2, "min_y": base_y, "max_y": base_y + h,
                "min_z": -d / 2, "max_z": d / 2, "top_y": base_y + h, "label": label}

    if ctype == "arcade":
        h = float(comp.get("height", 0.5))
        return {"min_x": -w / 2, "max_x": w / 2, "min_y": base_y, "max_y": base_y + h,
                "min_z": -d / 2, "max_z": d / 2, "top_y": base_y + h, "label": label}

    if ctype in ("dome", "pediment", "tiled_roof", "flat_roof", "vault"):
        h = float(comp.get("height", comp.get("radius", 0.3)))
        return {"min_x": -w / 2, "max_x": w / 2, "min_y": base_y, "max_y": base_y + h,
                "min_z": -d / 2, "max_z": d / 2, "top_y": base_y + h, "label": label}

    if ctype == "door":
        dw = float(comp.get("width", 0.1))
        dh = float(comp.get("height", 0.2))
        return {"min_x": -dw / 2, "max_x": dw / 2, "min_y": base_y, "max_y": base_y + dh,
                "min_z": -d / 2 - 0.01, "max_z": -d / 2 + 0.02, "top_y": base_y + dh, "label": label}

    if ctype == "procedural":
        parts = comp.get("parts", [])
        if not parts:
            return None
        min_x = min_y = min_z = float("inf")
        max_x = max_y = max_z = float("-inf")
        for part in parts:
            if not isinstance(part, dict):
                continue
            pos = part.get("position", [0, 0, 0])
            px = float(pos[0]) if len(pos) > 0 else 0
            py = float(pos[1]) if len(pos) > 1 else 0
            pz = float(pos[2]) if len(pos) > 2 else 0
            shape = part.get("shape", "box")
            if shape == "box":
                bw = float(part.get("width", 0.2))
                bh = float(part.get("height", 0.2))
                bd = float(part.get("depth", bw))
                min_x = min(min_x, px - bw / 2); max_x = max(max_x, px + bw / 2)
                min_y = min(min_y, base_y + py - bh / 2); max_y = max(max_y, base_y + py + bh / 2)
                min_z = min(min_z, pz - bd / 2); max_z = max(max_z, pz + bd / 2)
            elif shape in ("cylinder", "cone"):
                r = float(part.get("radius", 0.1))
                bh = float(part.get("height", 0.3))
                min_x = min(min_x, px - r); max_x = max(max_x, px + r)
                min_y = min(min_y, base_y + py - bh / 2); max_y = max(max_y, base_y + py + bh / 2)
                min_z = min(min_z, pz - r); max_z = max(max_z, pz + r)
            elif shape == "sphere":
                r = float(part.get("radius", 0.1))
                min_x = min(min_x, px - r); max_x = max(max_x, px + r)
                min_y = min(min_y, base_y + py - r); max_y = max(max_y, base_y + py + r)
                min_z = min(min_z, pz - r); max_z = max(max_z, pz + r)
            else:
                # Unknown shape — approximate as 0.2 cube
                min_x = min(min_x, px - 0.1); max_x = max(max_x, px + 0.1)
                min_y = min(min_y, base_y + py - 0.1); max_y = max(max_y, base_y + py + 0.1)
                min_z = min(min_z, pz - 0.1); max_z = max(max_z, pz + 0.1)
        if min_x == float("inf"):
            return None
        label = f"procedural({comp.get('recipe', comp.get('stack_role', '?'))})"
        return {"min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y,
                "min_z": min_z, "max_z": max_z, "top_y": max_y, "label": label}

    # Fallback for other types — rough box
    h = float(comp.get("height", 0.3))
    return {"min_x": -w / 2, "max_x": w / 2, "min_y": base_y, "max_y": base_y + h,
            "min_z": -d / 2, "max_z": d / 2, "top_y": base_y + h, "label": label}


def _aabb_overlap_volume(a: dict, b: dict) -> float:
    """Compute the overlap volume of two AABBs. Returns 0 if no overlap."""
    ox = max(0, min(a["max_x"], b["max_x"]) - max(a["min_x"], b["min_x"]))
    oy = max(0, min(a["max_y"], b["max_y"]) - max(a["min_y"], b["min_y"]))
    oz = max(0, min(a["max_z"], b["max_z"]) - max(a["min_z"], b["min_z"]))
    return ox * oy * oz


def check_component_collisions(spec: dict, footprint_w: float, footprint_d: float) -> list[str]:
    """Check a building spec for component geometry collisions.

    Simulates the renderer's stacking layout, computes bounding boxes, and
    reports pairs of components whose volumes intersect beyond a small tolerance.

    Returns a list of human-readable collision descriptions (empty = no issues).
    """
    comps = spec.get("components", [])
    if not isinstance(comps, list) or len(comps) < 2:
        return []

    w = footprint_w
    d = footprint_d

    # Bucket by stack role (same as renderer)
    buckets: dict[str, list] = {r: [] for r in _STACK_ROLE_ORDER}
    for comp in comps:
        if not isinstance(comp, dict) or not comp.get("type"):
            continue
        role = _resolve_stack_role(comp)
        buckets[role].append(comp)

    # Simulate stacking to get Y bases
    base_level = 0.0
    structural_top = 0.0
    all_bounds: list[dict] = []

    for comp in buckets["foundation"]:
        bounds = _estimate_component_bounds(comp, base_level, w, d)
        if bounds:
            all_bounds.append(bounds)
            base_level = max(base_level, bounds["top_y"])
    structural_top = base_level

    for comp in buckets["structural"]:
        bounds = _estimate_component_bounds(comp, base_level, w, d)
        if bounds:
            all_bounds.append(bounds)
            structural_top = max(structural_top, bounds["top_y"])

    for comp in buckets["infill"]:
        bounds = _estimate_component_bounds(comp, base_level, w, d)
        if bounds:
            all_bounds.append(bounds)

    for comp in buckets["roof"]:
        bounds = _estimate_component_bounds(comp, structural_top, w, d)
        if bounds:
            all_bounds.append(bounds)
            structural_top = max(structural_top, bounds["top_y"])

    for comp in buckets["decorative"]:
        bounds = _estimate_component_bounds(comp, base_level, w, d)
        if bounds:
            all_bounds.append(bounds)

    for comp in buckets["freestanding"]:
        bounds = _estimate_component_bounds(comp, structural_top, w, d)
        if bounds:
            all_bounds.append(bounds)

    # Check pairwise collisions
    collisions: list[str] = []
    tolerance = 0.01  # Small overlap tolerance for touching components
    for i in range(len(all_bounds)):
        for j in range(i + 1, len(all_bounds)):
            a, b = all_bounds[i], all_bounds[j]
            vol = _aabb_overlap_volume(a, b)
            if vol > tolerance:
                # Compute overlap as % of the smaller component's volume
                vol_a = max(0.001, (a["max_x"] - a["min_x"]) * (a["max_y"] - a["min_y"]) * (a["max_z"] - a["min_z"]))
                vol_b = max(0.001, (b["max_x"] - b["min_x"]) * (b["max_y"] - b["min_y"]) * (b["max_z"] - b["min_z"]))
                pct = vol / min(vol_a, vol_b) * 100
                if pct > 15:  # Only report significant overlaps (>15% of smaller component)
                    collisions.append(
                        f"COLLISION: {a['label']} and {b['label']} overlap by {pct:.0f}% "
                        f"(Y: {a['min_y']:.2f}-{a['max_y']:.2f} vs {b['min_y']:.2f}-{b['max_y']:.2f})"
                    )

    # Check for components that exceed the footprint
    for bounds in all_bounds:
        x_span = bounds["max_x"] - bounds["min_x"]
        z_span = bounds["max_z"] - bounds["min_z"]
        if x_span > w * 1.3 or z_span > d * 1.3:
            collisions.append(
                f"OVERFLOW: {bounds['label']} exceeds footprint "
                f"({x_span:.2f}x{z_span:.2f} vs {w:.2f}x{d:.2f})"
            )

    # Check for unreasonable total height
    max_y = max((b["max_y"] for b in all_bounds), default=0)
    max_dim = max(w, d)
    if max_y > max_dim * 2.5:
        collisions.append(
            f"HEIGHT: total height {max_y:.2f} exceeds 2.5× footprint ({max_dim:.2f})"
        )

    if collisions:
        logger.info("Geometry check: %d issues found — %s", len(collisions), "; ".join(collisions[:3]))

    return collisions


def generate_architectural_feedback(spec: dict, footprint_w: float, footprint_d: float) -> str:
    """Generate detailed feedback for the Urbanista LLM about architectural issues.

    Analyzes the building spec and provides actionable suggestions for improvement
    based on architectural coherence, proportion, and structural integrity.
    """
    comps = spec.get("components", [])
    if not isinstance(comps, list):
        return ""

    feedback_parts = []

    # Analyze component types and roles
    component_types = {}
    stack_roles = {}
    for comp in comps:
        if not isinstance(comp, dict):
            continue
        ctype = comp.get("type", "")
        role = comp.get("stack_role", "")

        component_types[ctype] = component_types.get(ctype, 0) + 1
        if role:
            stack_roles[role] = stack_roles.get(role, 0) + 1

    # Check for architectural completeness
    has_foundation = any(c.get("stack_role") == "foundation" for c in comps)
    has_structural = any(c.get("stack_role") == "structural" for c in comps)
    has_roof = any(c.get("stack_role") == "roof" for c in comps)

    if not has_foundation and len(comps) > 2:
        feedback_parts.append("Add a foundation component (podium, walls, or platform) at the base for structural stability.")

    if not has_structural and len(comps) > 3:
        feedback_parts.append("Include structural components to support the building mass.")

    if not has_roof:
        feedback_parts.append("Add a roof component (tiled_roof, flat_roof, dome, etc.) to complete the building.")

    # Check for procedural component issues
    procedural_comps = [c for c in comps if c.get("type") == "procedural"]
    for i, comp in enumerate(procedural_comps):
        parts = comp.get("parts", [])
        if len(parts) > 10:
            feedback_parts.append(f"Procedural component {i+1} has {len(parts)} parts - consider consolidating small details.")

        # Check for vertical stacking issues
        y_positions = []
        for part in parts:
            if isinstance(part, dict):
                pos = part.get("position", [0, 0, 0])
                if isinstance(pos, list) and len(pos) > 1:
                    y_positions.append(pos[1])

        if len(set(y_positions)) <= 2 and len(parts) > 3:
            feedback_parts.append(f"Procedural component {i+1} parts are mostly at the same height - distribute vertically for better architectural form.")

    # Check for material consistency
    materials = []
    for comp in comps:
        color = comp.get("color", "").lower()
        if color and not color.startswith("#"):
            materials.append(color)

    unique_materials = set(materials)
    if len(unique_materials) > 5:
        feedback_parts.append(f"Using {len(unique_materials)} different materials - consider limiting to 2-3 for architectural harmony.")

    # Check for scale relationships
    heights = []
    for comp in comps:
        if "height" in comp:
            heights.append(comp["height"])
        elif comp.get("type") == "block" and "stories" in comp:
            heights.append(comp["stories"] * comp.get("storyHeight", 0.3))

    if heights and max(heights) / min(heights) > 3:
        feedback_parts.append("Height variations are extreme - ensure proportions create a coherent architectural form.")

    # Check for openings without context
    openings = sum(1 for c in comps if c.get("type") in ("door", "arcade"))
    structural_elements = sum(1 for c in comps if c.get("stack_role") in ("structural", "foundation"))

    if openings > 0 and structural_elements == 0:
        feedback_parts.append("Openings (doors/arcades) need surrounding structural elements for architectural context.")

    # Check for collision issues
    collisions = check_component_collisions(spec, footprint_w, footprint_d)
    if collisions:
        feedback_parts.append("Geometry conflicts detected - adjust component positions to prevent overlaps.")

    if feedback_parts:
        return " ".join(feedback_parts)
    else:
        return "Building design is structurally sound and architecturally coherent."


def validate_master_plan(
    master_plan: list,
) -> list[dict]:
    """
    Keep structures with at least one in-bounds tile; drop OOB tiles; enforce global
    uniqueness of (x, y) — first claim wins. Logs dropped duplicates and OOB.
    Tile x/y are stored as int (JSON floats/strings are normalized) so downstream
    math (footprints, centers) is never lexicographic or type-mixed.
    """
    if not master_plan:
        return []

    seen: set[tuple[int, int]] = set()
    cleaned: list[dict] = []
    dup_dropped = 0
    oob_dropped = 0
    dup_first: tuple[int, int] | None = None
    dup_first_structure_name: str | None = None
    oob_first: tuple[int, int] | None = None

    for struct in master_plan:
        if not isinstance(struct, dict):
            continue
        structure_label = struct.get("name")
        structure_name_str = structure_label if isinstance(structure_label, str) else None
        raw_tiles = struct.get("tiles")
        if not isinstance(raw_tiles, list):
            continue
        new_tiles: list[dict] = []
        for t in raw_tiles:
            if not isinstance(t, dict):
                continue
            x, y = t.get("x"), t.get("y")
            if x is None or y is None:
                continue
            try:
                xi, yi = int(x), int(y)
            except (TypeError, ValueError):
                continue
            # World is unbounded — no OOB rejection
            key = (xi, yi)
            if key in seen:
                dup_dropped += 1
                if dup_first is None:
                    dup_first = key
                    dup_first_structure_name = structure_name_str
                continue
            seen.add(key)
            normalized = dict(t)
            normalized["x"] = xi
            normalized["y"] = yi
            new_tiles.append(normalized)

        if new_tiles:
            out = dict(struct)
            out["tiles"] = new_tiles
            cleaned.append(out)

    if dup_dropped:
        logger.warning(
            "Master plan: dropped %s duplicate tile assignments (first structure wins per tile); "
            "first duplicate at %s in %s",
            dup_dropped,
            dup_first,
            dup_first_structure_name or "?",
        )
    if oob_dropped:
        logger.warning(
            "Master plan: dropped %s out-of-bounds tiles (grid %s×%s; first at %s)",
            oob_dropped,
            grid_width,
            grid_height,
            oob_first,
        )

    return cleaned


def validate_urbanista_tiles(
    tiles: list,
) -> list[dict]:
    """Normalize tile dicts; drop invalid entries. x/y are int in output. No bounds check — world is unbounded."""
    if not tiles:
        return []
    out: list[dict] = []
    for td in tiles:
        if not isinstance(td, dict):
            continue
        x, y = td.get("x"), td.get("y")
        if x is None or y is None:
            continue
        try:
            xi, yi = int(x), int(y)
        except (TypeError, ValueError):
            continue
        normalized = dict(td)
        normalized["x"] = xi
        normalized["y"] = yi
        out.append(normalized)
    return out
