"""Validate survey master plans and Urbanista output against the grid and renderer contract."""

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
    MAX_PROCEDURAL_PARTS,
    PARAMETRIC_TEMPLATE_IDS,
    PROCEDURAL_SHAPES,
    RENDERER_COMPONENT_TYPES,
    STACK_ROLES,
)

logger = logging.getLogger("eternal.validation")



def _require_color(part: dict, ctx: str) -> None:
    c = part.get("color")
    if not isinstance(c, str) or not _HEX_COLOR.match(c):
        raise UrbanistaValidationError(f"{ctx}: each procedural part requires color as #RRGGBB hex")


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
        if not isinstance(st, str) or st.lower() not in ("doric", "ionic", "corinthian"):
            raise UrbanistaValidationError(
                f"{ctx}: colonnade requires style as one of doric, ionic, corinthian"
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
    """Auto-correct common Urbanista errors before validation. Returns a cleaned copy."""
    if not isinstance(arch_result, dict):
        return arch_result
    fixes = 0
    tiles = arch_result.get("tiles")
    if not isinstance(tiles, list):
        return arch_result
    for td in tiles:
        if not isinstance(td, dict):
            continue
        spec = td.get("spec")
        if not isinstance(spec, dict):
            continue
        # Fix color names → hex in components
        for comp in spec.get("components", []):
            if not isinstance(comp, dict):
                continue
            c = comp.get("color")
            if isinstance(c, str) and not c.startswith("#"):
                mapped = _NAMED_COLOR_MAP.get(c.lower().strip())
                if mapped:
                    comp["color"] = mapped
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
        # Fix color names in procedural parts
        for comp in spec.get("components", []):
            if not isinstance(comp, dict):
                continue
            for part in comp.get("parts", []):
                if not isinstance(part, dict):
                    continue
                c = part.get("color")
                if isinstance(c, str) and not c.startswith("#"):
                    mapped = _NAMED_COLOR_MAP.get(c.lower().strip())
                    if mapped:
                        part["color"] = mapped
                        fixes += 1
    if fixes:
        logger.info("Sanitized Urbanista output: %d auto-corrections applied", fixes)
    return arch_result


def validate_urbanista_arch_result(arch_result: dict) -> dict:
    """
    Validate building specs: each anchor tile must have spec.components OR spec.template
    (mutually exclusive). Template ids match static/parametric_templates.js. Secondary
    multi-tile cells may carry only spec.anchor. Raises UrbanistaValidationError on violation.
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
