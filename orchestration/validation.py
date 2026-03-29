"""Validate survey master plans and Urbanista output against the grid and renderer contract."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("roma.validation")

# Must match static/renderer3d.js component builders + procedural
RENDERER_COMPONENT_TYPES = frozenset({
    "podium", "colonnade", "pediment", "dome", "block", "arcade",
    "tiled_roof", "atrium", "statue", "fountain", "awning", "battlements",
    "tier", "door", "pilasters", "vault", "flat_roof", "cella", "walls",
    "procedural",
})

PROCEDURAL_SHAPES = frozenset({"box", "cylinder", "sphere", "cone", "torus", "plane"})

STACK_ROLES = frozenset({
    "foundation", "structural", "infill", "roof", "decorative", "freestanding",
})

MAX_PROCEDURAL_PARTS = 48
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_MAP_URL_MAX_LEN = 2048

# Optional spec.phase4 — must match static/renderer3d.js _applyPhase4ContextualPolish
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


class UrbanistaValidationError(ValueError):
    """Urbanista JSON violates renderer contract; do not strip or substitute."""


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


def validate_master_plan(
    master_plan: list,
    grid_width: int,
    grid_height: int,
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
            if not (0 <= xi < grid_width and 0 <= yi < grid_height):
                oob_dropped += 1
                if oob_first is None:
                    oob_first = (xi, yi)
                continue
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
    grid_width: int,
    grid_height: int,
) -> list[dict]:
    """Keep only in-bounds tile dicts; drop invalid entries. x/y are int in output."""
    if not tiles:
        return []
    out: list[dict] = []
    oob_dropped = 0
    oob_first: tuple[int, int] | None = None
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
        if not (0 <= xi < grid_width and 0 <= yi < grid_height):
            oob_dropped += 1
            if oob_first is None:
                oob_first = (xi, yi)
            continue
        normalized = dict(td)
        normalized["x"] = xi
        normalized["y"] = yi
        out.append(normalized)
    if oob_dropped:
        logger.warning(
            "Urbanista: dropped %s out-of-bounds tiles (grid %s×%s; first at %s)",
            oob_dropped,
            grid_width,
            grid_height,
            oob_first,
        )
    return out
