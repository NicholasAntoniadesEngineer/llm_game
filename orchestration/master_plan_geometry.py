"""Master-plan tile coordinate normalization and intra-plan overlap detection."""

from __future__ import annotations

from typing import Any


def normalize_master_plan_tile_coordinates(master_plan: list[dict[str, Any]]) -> int:
    """Coerce each tile ``x``/``y`` to ``int`` in-place. Returns count of tiles touched."""
    touched = 0
    for struct in master_plan:
        tiles = struct.get("tiles")
        if not isinstance(tiles, list):
            continue
        for t in tiles:
            if not isinstance(t, dict):
                continue
            try:
                t["x"] = int(t["x"])
                t["y"] = int(t["y"])
                touched += 1
            except (KeyError, TypeError, ValueError):
                continue
    return touched


def intra_plan_tile_overlaps(master_plan: list[dict[str, Any]]) -> list[str]:
    """Return human-readable overlap descriptions (empty list if none)."""
    all_tiles: dict[tuple[int, int], str] = {}
    messages: list[str] = []
    for struct in master_plan:
        sname = str(struct.get("name", "?"))
        for t in struct.get("tiles", []):
            if not isinstance(t, dict):
                continue
            try:
                key = (int(t["x"]), int(t["y"]))
            except (KeyError, TypeError, ValueError):
                continue
            if key in all_tiles:
                messages.append(
                    f"Tile overlap: {sname!r} and {all_tiles[key]!r} at {key}"
                )
            else:
                all_tiles[key] = sname
    return messages
