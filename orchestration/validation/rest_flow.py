"""Sanitize and structurally validate Urbanista ``arch_result`` JSON."""

from __future__ import annotations

import logging

from core.errors import UrbanistaValidationError
from orchestration.schema import (
    _DEFAULT_STACK_ROLES,
    _NAMED_COLOR_MAP,
    GRAMMAR_IDS,
    MATERIAL_NAMES,
    expand_dense_shapes_in_result,
    resolve_color,
)
from orchestration.validation.rest_components import (
    _validate_component,
    _validate_phase4,
    _validate_template,
)

logger = logging.getLogger("eternal.validation")


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
            # Fix missing stack_role on procedural - assign based on context
            if comp.get("type") == "procedural" and not comp.get("stack_role"):
                # Try to infer from component position in list and other components
                comps_list = spec.get("components", [])
                comp_idx = comps_list.index(comp) if comp in comps_list else 0

                # First component is often foundation, last is often roof
                if comp_idx == 0 and len(comps_list) > 1:
                    comp["stack_role"] = "foundation"
                elif comp_idx == len(comps_list) - 1:
                    comp["stack_role"] = "roof"
                else:
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
