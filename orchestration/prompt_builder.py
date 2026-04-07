"""Construct per-building prompts for Urbanista and terrain agents."""

import hashlib
import json

from agents.golden_specs import get_golden_example_for_culture
from orchestration.reference_db import format_reference_for_prompt, lookup_architectural_reference
from prompts import format_pbr_hint, format_composition_directive


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
            f"Bounding box: ({anchor_x},{anchor_y}) to ({max(xs)},{max(ys)}) — "
            f"{len(tiles)} tiles total.\n"
            f"Sample tiles: {json.dumps(sample_tiles)}\n"
            f"Output a SINGLE representative tile with color/scenery, plus `commentary`. "
            f"The engine replicates it to all {len(tiles)} coordinates."
        )
    else:
        terrain_tiles_str = f"Survey tile list (coordinates and elevations): {json.dumps(tiles)}"

    prompt = (
        f"OPEN SPACE / SCENERY (not a building): {name}\n"
        f"Surface type: {btype}\n"
        f"City: {city_loc}, {period}\n"
        f"Footprint: {tile_w}x{tile_d} tiles = {footprint_w}x{footprint_d} world units\n"
        f"Reference corner tile: ({anchor_x}, {anchor_y}), mean elevation: {avg_elevation}\n"
        f"{terrain_tiles_str}\n\n"
        f"NEARBY STRUCTURES:\n{neighbor_desc}\n\n"
    )
    if env_note:
        prompt += f"SURVEYOR `environment_note`:\n{env_note}\n\n"
    prompt += (
        f"SITE BRIEF (Historian + evidence):\n{physical_desc}\n\n"
        f"OUTPUT REQUIREMENTS:\n"
        f"- Return JSON with `tiles` — one entry per survey coordinate above.\n"
        f"- Each tile MUST set `terrain` to the literal string \"{btype}\".\n"
        f"- Each tile MAY include `spec`: {{ \"color\": \"#RRGGBB\", \"scenery\": {{ "
        f"\"vegetation_density\": 0..1 (garden/grass), \"pavement_detail\": 0..1 (road/forum), "
        f"\"water_murk\": 0..1 (water) }} }}.\n"
        f"- Do NOT emit spec.components, spec.template, or spec.anchor — the client uses procedural meshes.\n"
        f"- Rich `description` on every tile; substantive `commentary` and `reference` (paving, hydrology, planting).\n"
        f"- Match elevations to the survey (mean {avg_elevation}).\n"
    )

    # Inject survey-suggested palette
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
        ref_db_section = (
            f"MEASURED REFERENCE (curated database — numeric ranges for proportion_rules / sanity checks; "
            f"use when they align with the site brief):\n{ref_db_block}\n\n"
        )

    # For large buildings (>30 tiles), simplify tile list to bounding box
    if len(tiles) > 30:
        tiles_str = (
            f"Bounding box: ({anchor_x},{anchor_y}) to ({max(xs)},{max(ys)}) — "
            f"{len(tiles)} tiles total. Output ONLY the anchor tile ({anchor_x},{anchor_y}) "
            f"with full spec.components. The engine auto-fills secondary tiles."
        )
    else:
        tiles_str = f"All tiles: {json.dumps(tiles)}"

    prompt = (
        f"Design: {name}\nType: {btype}\n"
        f"City: {city_loc}, {period}\n"
        f"Footprint: {tile_w}x{tile_d} tiles = {footprint_w}x{footprint_d} world units\n"
        f"Anchor tile: ({anchor_x}, {anchor_y}), elevation: {avg_elevation}\n"
        f"{tiles_str}\n\n"
        f"NEARBY STRUCTURES:\n{neighbor_desc}\n\n"
        f"{ref_db_section}"
        f"REFERENCE EXAMPLE (proportion + layering guide only — same building_type, scaled to this footprint):\n{golden_example_str}\n"
        f"Use the reference example for proportion/stacking only; derive your design from the site brief (do not paste).\n\n"
        f"SITE BRIEF (from survey — match this closely):\n{physical_desc}\n"
        + (f"\nDISTRICT SCENERY (circulation, hydrology, green/blue network — orient facades and entrances accordingly):\n{district_scenery}\n\n" if district_scenery else "\n")
        + f"IMPORTANT: Scale all component dimensions to fit a {footprint_w}x{footprint_d} footprint.\n"
        f"- Max total building height: {round(max(footprint_w, footprint_d) * 1.2, 2)} world units\n"
        + (f"- For small buildings (footprint < 2.0): use fewer components (3-6), shorter columns\n" if footprint_w < 2.0 or footprint_d < 2.0 else "")
        + (f"- For large buildings (footprint > 5.0): use more components (8-14), add procedural details\n" if footprint_w > 5.0 or footprint_d > 5.0 else "")
        + f"- Column/post radius should be ~{round(footprint_w / 60, 3)} for proportional supports\n"
        f"- Total height should be {round(footprint_w * 0.7, 2)} to {round(footprint_w * 1.1, 2)}\n"
        f"- Set elevation={avg_elevation} on all tiles\n"
        f"- Set spec.anchor on EVERY tile to {{\"x\":{anchor_x},\"y\":{anchor_y}}}"
    )

    # PBR material guidance
    hint = format_pbr_hint(btype)
    prompt += f"\n- MATERIAL QUALITY: {hint}"

    # Generative uniqueness seed
    seed_hash = int(hashlib.md5(f"{anchor_x},{anchor_y},{name}".encode()).hexdigest()[:8], 16)
    directive = format_composition_directive(seed_hash)
    prompt += f"\n\n\U0001f3b2 UNIQUENESS DIRECTIVE (seed {seed_hash & 0xFFFF:04x}): {directive}"

    if env_note:
        prompt += (
            f"\n\nSURVEYOR `environment_note` (edges, planting, circulation — use for facades and setting):\n"
            f"{env_note}\n"
        )

    # Inject survey-suggested palette
    prompt += _palette_suffix(district_palette)

    return prompt


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
        return f"\n- DISTRICT PALETTE (from surveyor): {', '.join(parts)}. Use these as your base materials; vary per building \u00b110% lightness for uniqueness."
    return ""
