"""Geometry collision detection for Urbanista building specs."""

from __future__ import annotations

import logging

from orchestration.schema import _DEFAULT_STACK_ROLES, _STACK_ROLE_ORDER

logger = logging.getLogger("eternal.validation")

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


def try_prune_colliding_decorative_components(
    spec: dict,
    footprint_w: float,
    footprint_d: float,
    *,
    max_removals: int = 8,
) -> int:
    """Remove ``decorative`` stack-role components from the tail until collisions shrink.

    Returns how many components were removed (0 if none / not applicable).
    """
    if not isinstance(spec, dict):
        return 0
    comps = spec.get("components")
    if not isinstance(comps, list) or len(comps) < 2:
        return 0

    removed_total = 0
    while removed_total < max_removals:
        collisions_before = check_component_collisions(spec, footprint_w, footprint_d)
        if not collisions_before:
            break
        remove_idx: int | None = None
        for i in range(len(comps) - 1, -1, -1):
            c = comps[i]
            if isinstance(c, dict) and _resolve_stack_role(c) == "decorative":
                remove_idx = i
                break
        if remove_idx is None:
            break
        comps.pop(remove_idx)
        removed_total += 1
        if len(comps) < 2:
            break
        collisions_after = check_component_collisions(spec, footprint_w, footprint_d)
        if len(collisions_after) >= len(collisions_before):
            continue
    if removed_total:
        logger.info(
            "Geometry prune: removed %d decorative component(s) to reduce collisions",
            removed_total,
        )
    return removed_total


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
