"""Construct per-building prompts for Urbanista and terrain agents.

Uses compact notation to minimize token usage while preserving all essential
information the LLM needs to produce valid building specs.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import deque
from typing import Any

from agents.golden_specs import get_golden_example_for_culture
from orchestration.reference_db import format_reference_for_prompt, lookup_architectural_reference
from orchestration.schema import format_compact_neighbors as _fmt_compact_nb
from orchestration.cultural_adaptation import cultural_system
from prompts import format_pbr_hint, format_composition_directive


# ── Variety tracking ─────────────────────────────────────────────────────
# Rolling window of recently built structures for variety hints.
# Each entry: {"btype": str, "color": str, "height": float, "order": str|None}

_RECENT_BUILDS: deque[dict[str, Any]] = deque(maxlen=8)

# Column orders and material temperature groups for variety suggestions
_COLUMN_ORDERS = ["doric", "ionic", "corinthian", "tuscan", "composite"]
_WARM_MATERIALS = {"brick", "terracotta", "wood", "ochre", "adobe", "mud", "thatch", "coral"}
_COOL_MATERIALS = {"marble", "travertine", "limestone", "granite", "concrete", "tufa", "slate"}


def record_built(btype: str, color: str = "", height: float = 0.0,
                 order: str | None = None) -> None:
    """Record a completed building for variety tracking.

    Called by the engine after each successful Urbanista response.
    """
    _RECENT_BUILDS.append({
        "btype": btype, "color": color,
        "height": height, "order": order,
    })


def clear_variety_history() -> None:
    """Reset variety tracking (e.g., on city reset)."""
    _RECENT_BUILDS.clear()


def _generate_variety_hint(btype: str, culture: str = "roman", period: str = "classical") -> str:
    """Generate a compact variety suggestion based on recent builds and cultural context.

    Enhanced with cultural and historical awareness for more appropriate variety suggestions.
    """
    if not _RECENT_BUILDS:
        return ""

    hints: list[str] = []

    # Cultural context adjustments
    cultural_hints = _get_cultural_variety_hints(btype, culture, period)
    hints.extend(cultural_hints)

    # 1. Column order rotation for temples/basilicas (culturally appropriate)
    if btype in ("temple", "basilica", "monument"):
        recent_orders = [b["order"] for b in _RECENT_BUILDS
                         if b.get("order") and b["btype"] in ("temple", "basilica")]
        if recent_orders:
            last_order = recent_orders[-1]
            # Suggest culturally appropriate alternatives
            alternatives = _get_culturally_appropriate_orders(culture, period, last_order)
            if alternatives:
                hints.append(f"Prefer {alternatives[0]} columns (last used {last_order})")

    # 2. Material variety with cultural awareness
    recent_materials = []
    for b in _RECENT_BUILDS:
        color = b.get("color", "").lower()
        if color:
            # Infer material from color naming
            if any(mat in color for mat in ("marble", "travertine", "limestone")):
                recent_materials.append("stone")
            elif any(mat in color for mat in ("brick", "terracotta")):
                recent_materials.append("brick")
            elif "wood" in color:
                recent_materials.append("wood")
            else:
                recent_materials.append("other")

    if len(recent_materials) >= 3:
        last_3 = recent_materials[-3:]
        if len(set(last_3)) == 1:
            material = last_3[0]
            alternative = _suggest_alternative_material(material, culture, period)
            if alternative:
                hints.append(f"Use {alternative} instead of {material} — diversify materials")

    # 3. Scale variation with cultural proportions
    recent_heights = [b["height"] for b in _RECENT_BUILDS
                      if b.get("height") and b["height"] > 0]
    if recent_heights and len(recent_heights) >= 2:
        avg_h = sum(recent_heights) / len(recent_heights)
        last_h = recent_heights[-1]
        if last_h > 0 and abs(last_h - avg_h) < avg_h * 0.15:  # Stricter check
            cultural_scale_hint = _get_cultural_scale_hint(btype, culture, period)
            if cultural_scale_hint:
                hints.append(cultural_scale_hint)
            else:
                hints.append("Vary height +/-25% from neighbors for visual interest")

    # 4. Grammar mode suggestion with cultural appropriateness
    grammar_types = _get_grammar_types_for_culture(culture, period)
    if btype in grammar_types:
        recent_grammar_use = sum(1 for b in _RECENT_BUILDS if b["btype"] in grammar_types)
        if recent_grammar_use > 0 and recent_grammar_use % 4 == 0:  # Less frequent
            hints.append(f"Consider standard {btype} form for this {culture} {period} context")

    # 5. Functional relationship hints
    functional_hint = _generate_functional_relationship_hint(btype, _RECENT_BUILDS)
    if functional_hint:
        hints.append(functional_hint)

    if not hints:
        return ""

    # Prioritize hints by cultural relevance
    prioritized_hints = _prioritize_hints_by_culture(hints, culture, period)

    # Return most relevant hint, keeping it compact
    return "VARY: " + prioritized_hints[0]


# ── Urban intelligence hints ────────────────────────────────────────────


def _detect_road_facing(tiles: list, world_state: Any = None) -> str:
    """Detect which side of a building faces a road tile.

    Checks adjacent tiles in cardinal directions from the building's footprint.
    Returns compact hint like 'FACING: road N — orient entrance northward' or ''.
    Token budget: <20 tokens.
    """
    if world_state is None:
        return ""

    # Build set of building's own tile coords
    own_coords: set[tuple[int, int]] = set()
    for t in tiles:
        try:
            own_coords.add((int(t["x"]), int(t["y"])))
        except (KeyError, TypeError, ValueError):
            continue

    if not own_coords:
        return ""

    # Check perimeter tiles for roads/forums
    directions = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}
    road_dirs: list[str] = []
    for label, (dx, dy) in directions.items():
        for ox, oy in own_coords:
            nx, ny = ox + dx, oy + dy
            if (nx, ny) in own_coords:
                continue
            tile = world_state.get_tile(nx, ny)
            if tile and tile.terrain in ("road", "forum"):
                road_dirs.append(label)
                break

    if not road_dirs:
        return ""
    primary = road_dirs[0]
    return f"FACING: road {primary} — orient entrance {primary.lower()}ward"


def _height_gradient_hint(
    anchor_x: int,
    anchor_y: int,
    city_center_x: float,
    city_center_y: float,
    city_radius: float,
) -> str:
    """Suggest building height based on distance from city center.

    Closer to center = taller, further = shorter.
    Returns compact hint like 'HEIGHT: 80m from center — tall (1.2-1.8 units)' or ''.
    Token budget: <25 tokens.
    """
    if city_radius <= 0:
        return ""
    dist = math.sqrt((anchor_x - city_center_x) ** 2 + (anchor_y - city_center_y) ** 2)
    dist_m = round(dist * 10)
    ratio = min(dist / city_radius, 1.0)  # 0.0 = center, 1.0 = edge

    if ratio < 0.3:
        suggestion = "tall (1.2-1.8 units)"
    elif ratio < 0.6:
        suggestion = "moderate (0.8-1.3 units)"
    else:
        suggestion = "low (0.5-0.9 units)"

    return f"HEIGHT: {dist_m}m from center — {suggestion}"


def build_terrain_prompt(
    name: str,
    btype: str,
    tiles: list,
    anchor_x: int,
    anchor_y: int,
    tile_w: int,
    tile_d: int,
    footprint_w: float,
    footprint_d: float,
    avg_elevation: float,
    city_loc: str,
    period: str,
    neighbor_desc: str,
    physical_desc: str,
    env_note: str,
    district_palette: dict | None = None,
) -> str:
    """Construct prompt for open terrain (roads, forums, gardens, water)."""
    xs = [t["x"] for t in tiles]
    ys = [t["y"] for t in tiles]

    # For large open terrain (>40 tiles), use bounding box + sample tiles
    if len(tiles) > 40:
        sample_tiles = tiles[:5] + tiles[-5:] if len(tiles) > 10 else tiles[:5]
        terrain_tiles_str = (
            f"Bounds: ({anchor_x},{anchor_y})-({max(xs)},{max(ys)}) {len(tiles)} tiles.\n"
            f"Samples: {json.dumps(sample_tiles, separators=(',',':'))}\n"
            f"Output ONE representative tile; engine replicates to all {len(tiles)} coords."
        )
    else:
        terrain_tiles_str = f"Tiles: {json.dumps(tiles, separators=(',',':'))}"

    prompt = (
        f"TERRAIN: {name} | {btype} | {city_loc}, {period}\n"
        f"Size: {tile_w}x{tile_d}={footprint_w}x{footprint_d}wu | anchor:({anchor_x},{anchor_y}) elev:{avg_elevation}\n"
        f"{terrain_tiles_str}\n"
        f"{neighbor_desc}\n"
    )
    if env_note:
        prompt += f"ENV: {env_note}\n"
    prompt += (
        f"BRIEF: {physical_desc}\n\n"
        f"OUTPUT: JSON with tiles[]. Each: terrain=\"{btype}\", optional spec:{{color:\"#hex\",scenery:{{vegetation_density:0-1,pavement_detail:0-1,water_murk:0-1}}}}. "
        f"No components/template/anchor. Description per tile. elev~{avg_elevation}."
    )

    prompt += _palette_suffix(district_palette)
    return prompt


def build_building_prompt(
    name: str,
    btype: str,
    tiles: list,
    anchor_x: int,
    anchor_y: int,
    tile_w: int,
    tile_d: int,
    footprint_w: float,
    footprint_d: float,
    avg_elevation: float,
    city_loc: str,
    period: str,
    district_ref_year_i: int,
    neighbor_desc: str,
    physical_desc: str,
    district_scenery: str,
    env_note: str,
    district_palette: dict | None = None,
    world_state: Any = None,
    city_center: tuple[float, float] | None = None,
    city_radius: float = 0.0,
    transition_hint: str = "",
) -> str:
    """Construct prompt for a building structure."""
    xs = [t["x"] for t in tiles]
    ys = [t["y"] for t in tiles]

    golden_example_str = get_golden_example_for_culture(
        btype, footprint_w, footprint_d, city_loc, district_ref_year_i
    )

    ref_entry = lookup_architectural_reference(btype, city_loc, district_ref_year_i)
    ref_db_block = format_reference_for_prompt(ref_entry)
    ref_db_section = ""
    if ref_db_block:
        ref_db_section = f"MEASURED REF: {ref_db_block}\n"

    # For large buildings (>30 tiles), simplify tile list to bounding box
    if len(tiles) > 30:
        tiles_str = (
            f"Bounds: ({anchor_x},{anchor_y})-({max(xs)},{max(ys)}) {len(tiles)} tiles. "
            f"Output anchor ({anchor_x},{anchor_y}) only; engine auto-fills secondary."
        )
    else:
        tiles_str = f"Tiles: {json.dumps(tiles, separators=(',',':'))}"

    max_h = round(max(footprint_w, footprint_d) * 1.2, 2)
    col_r = round(footprint_w / 60, 3)
    h_lo = round(footprint_w * 0.7, 2)
    h_hi = round(footprint_w * 1.1, 2)

    # Build size hint
    size_hint = ""
    if footprint_w < 2.0 or footprint_d < 2.0:
        size_hint = "SMALL(<2.0): 3-6 components, shorter columns. "
    elif footprint_w > 5.0 or footprint_d > 5.0:
        size_hint = "LARGE(>5.0): 8-14 components, add procedural details. "

    prompt = (
        f"Design: {name} | Type: {btype} | {city_loc}, {period}\n"
        f"Footprint: {tile_w}x{tile_d}={footprint_w}x{footprint_d}wu | anchor:({anchor_x},{anchor_y}) elev:{avg_elevation}\n"
        f"{tiles_str}\n"
        f"{neighbor_desc}\n"
        f"{ref_db_section}"
        f"REF EXAMPLE (proportion guide, do not paste):\n{golden_example_str}\n\n"
        f"BRIEF: {physical_desc}\n"
    )
    if district_scenery:
        prompt += f"SCENERY: {district_scenery}\n"

    prompt += (
        f"\nSCALE: fit {footprint_w}x{footprint_d}wu. {size_hint}"
        f"max_h={max_h} col_r~{col_r} height={h_lo}-{h_hi} "
        f"elev={avg_elevation} anchor={{x:{anchor_x},y:{anchor_y}}}"
    )

    # PBR material guidance
    hint = format_pbr_hint(btype)
    prompt += f"\nMATERIAL: {hint}"

    # Generative uniqueness seed
    seed_hash = int(hashlib.md5(f"{anchor_x},{anchor_y},{name}".encode()).hexdigest()[:8], 16)
    directive = format_composition_directive(seed_hash)
    prompt += f"\nUNIQUE({seed_hash & 0xFFFF:04x}): {directive}"

    if env_note:
        prompt += f"\nENV: {env_note}"

    # Road orientation hint (~15 tokens)
    facing = _detect_road_facing(tiles, world_state)
    if facing:
        prompt += f"\n{facing}"

    # Height gradient from city center (~20 tokens)
    if city_center:
        h_hint = _height_gradient_hint(anchor_x, anchor_y, city_center[0], city_center[1], city_radius)
        if h_hint:
            prompt += f"\n{h_hint}"

    # District transition zone hint (~15 tokens)
    if transition_hint:
        prompt += f"\n{transition_hint}"

    # Variety hint based on recent builds
    variety = _generate_variety_hint(btype)
    if variety:
        prompt += f"\n{variety}"

    prompt += _palette_suffix(district_palette)

    # Encourage dense format usage
    prompt += (
        "\n\nPREFER dense shape arrays in procedural parts to save tokens: "
        "[\"b\",[x,y,z],[w,h,d],\"material\"] — see system prompt for codes."
    )

    return prompt


def build_compact_neighbor_desc(neighbors: list[dict]) -> str:
    """Convert structured neighbor data into compact NB: format for prompts.

    Input: list of neighbor dicts with direction, name, building_type, color, height.
    Output: "NB:N:Temple(temple,#f0ece4,h=12);E:Via Sacra(road)"

    Falls back to returning the input as-is if it's already a string.
    """
    if isinstance(neighbors, str):
        return neighbors
    return _fmt_compact_nb(neighbors)


def _get_cultural_variety_hints(btype: str, culture: str, period: str) -> list[str]:
    """Get variety hints specific to cultural and historical context."""
    if culture not in cultural_system.cultures:
        return []

    culture_data = cultural_system.cultures[culture]
    hints = culture_data.get("prompt_hints", {}).get("variety_suggestions", [])
    return hints


def _get_culturally_appropriate_orders(culture: str, period: str, exclude_order: str) -> list[str]:
    """Get column orders appropriate for the culture and period."""
    if culture not in cultural_system.cultures:
        return [order for order in ["doric", "ionic", "corinthian"] if order != exclude_order]

    culture_data = cultural_system.cultures[culture]
    # For now, return all available orders from building types
    all_orders = set()
    for building_type in culture_data.get("building_types", {}).values():
        if "orders" in building_type:
            all_orders.update(building_type["orders"])

    orders = list(all_orders) if all_orders else ["doric", "ionic", "corinthian"]
    return [order for order in orders if order != exclude_order]


def _suggest_alternative_material(current: str, culture: str, period: str) -> str | None:
    """Suggest alternative material based on cultural preferences."""
    if culture not in cultural_system.cultures:
        return None

    culture_data = cultural_system.cultures[culture]
    material_alternatives = culture_data.get("prompt_hints", {}).get("material_alternatives", {})

    if current in material_alternatives:
        return material_alternatives[current]

    return None


def _get_cultural_scale_hint(btype: str, culture: str, period: str) -> str | None:
    """Get culturally appropriate scale variation hints."""
    if culture not in cultural_system.cultures:
        return None

    culture_data = cultural_system.cultures[culture]
    scale_hints = culture_data.get("prompt_hints", {}).get("scale_hints", {})

    if btype in scale_hints:
        return scale_hints[btype]

    return None


def _get_grammar_types_for_culture(culture: str, period: str) -> set[str]:
    """Get building types that have standard forms for the culture/period."""
    if culture not in cultural_system.cultures:
        return {"temple", "basilica", "insula", "domus", "thermae", "amphitheater"}

    culture_data = cultural_system.cultures[culture]
    grammar_types = culture_data.get("prompt_hints", {}).get("grammar_types", [])
    return set(grammar_types) if grammar_types else {"temple", "basilica", "insula", "domus", "thermae", "amphitheater"}


def _generate_functional_relationship_hint(btype: str, recent_builds: deque) -> str | None:
    """Generate hints about functional relationships with recent buildings."""
    if not recent_builds:
        return None

    # Check for complementary building types
    recent_types = {b["btype"] for b in recent_builds}

    complementary_pairs = {
        "temple": ["basilica", "forum", "monument"],
        "basilica": ["temple", "forum"],
        "market": ["taberna", "warehouse"],
        "thermae": ["basilica", "forum"],
        "amphitheater": ["temple", "basilica"]
    }

    if btype in complementary_pairs:
        needed_types = complementary_pairs[btype]
        missing_types = [t for t in needed_types if t not in recent_types]
        if missing_types:
            return f"Consider adding {missing_types[0]} nearby for functional completeness"

    return None


def _prioritize_hints_by_culture(hints: list[str], culture: str, period: str) -> list[str]:
    """Prioritize hints based on cultural relevance."""
    # For now, just return hints in order - could be enhanced with cultural weights
    return hints


def _palette_suffix(district_palette: dict | None) -> str:
    """Return district palette hint string (empty if no palette)."""
    if not district_palette or not isinstance(district_palette, dict):
        return ""
    parts = []
    for role in ("primary", "secondary", "accent"):
        c = district_palette.get(role)
        if isinstance(c, str) and c.startswith("#"):
            parts.append(f"{role}={c}")
    if parts:
        return f"\nPALETTE: {', '.join(parts)} (\u00b110% lightness per building)"
    return ""
