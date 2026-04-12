"""Procedural terrain tile generation (no LLM). Extracted from BuildEngine."""

from __future__ import annotations


def generate_terrain_procedurally(
    name: str,
    btype: str,
    tiles: list,
    avg_elevation: float,
    district_palette: dict | None,
    physical_desc: str,
    *,
    terrain_defaults_dictionary: dict,
    procedural_terrain_description_max_chars: int,
    procedural_terrain_fallback_hex_color: str,
) -> dict:
    """Generate terrain tiles without an LLM call.

    Road, forum, garden, water, grass tiles use simple color + scenery spec.
    This saves one Urbanista system prompt per terrain structure.
    """
    defaults = terrain_defaults_dictionary.get(
        btype, {"color": procedural_terrain_fallback_hex_color, "scenery": {}}
    )
    color = defaults["color"]

    if district_palette and isinstance(district_palette, dict):
        if btype in ("road", "forum") and district_palette.get("primary"):
            color = district_palette["primary"]
        elif btype in ("garden", "grass") and district_palette.get("accent"):
            pass

    result_tiles = []
    for t in tiles:
        td = {
            "x": t["x"],
            "y": t["y"],
            "terrain": btype,
            "building_name": name,
            "building_type": btype,
            "description": (
                physical_desc[:procedural_terrain_description_max_chars]
                if physical_desc
                else f"{name} ({btype})"
            ),
            "elevation": t.get("elevation", avg_elevation),
            "color": color,
            "spec": {
                "color": color,
                "scenery": dict(defaults.get("scenery", {})),
            },
        }
        result_tiles.append(td)

    return {"tiles": result_tiles, "commentary": f"Procedural terrain: {name}"}
