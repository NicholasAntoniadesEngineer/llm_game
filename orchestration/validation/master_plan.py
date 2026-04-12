"""Master-plan normalization and Urbanista tile coordinate checks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.errors import AgentGenerationError

if TYPE_CHECKING:
    from core.config import Config

logger = logging.getLogger("eternal.validation")


def validate_master_plan(
    master_plan: list,
    *,
    system_configuration: "Config | None" = None,
) -> list[dict]:
    """
    Keep structures with at least one tile; enforce global uniqueness of (x, y)
    — first claim wins. Logs dropped duplicates.
    Tile x/y are stored as int (JSON floats/strings are normalized) so downstream
    math (footprints, centers) is never lexicographic or type-mixed.
    """
    if not master_plan:
        return []

    seen: set[tuple[int, int]] = set()
    cleaned: list[dict] = []
    dup_dropped = 0
    dup_first: tuple[int, int] | None = None
    dup_first_structure_name: str | None = None

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
        if (
            system_configuration is not None
            and system_configuration.master_plan_duplicate_tile_policy_string == "fail"
        ):
            raise AgentGenerationError(
                "bad_model_output",
                "Master plan contains "
                + str(dup_dropped)
                + " duplicate tile assignment(s); first duplicate at "
                + str(dup_first)
                + " in "
                + (dup_first_structure_name or "?"),
            )
        logger.warning(
            "Master plan: dropped %s duplicate tile assignments (first structure wins per tile); "
            "first duplicate at %s in %s",
            dup_dropped,
            dup_first,
            dup_first_structure_name or "?",
        )

    return cleaned


def validate_urbanista_tiles(tiles: list) -> list[dict]:
    """Normalize tile dicts; drop invalid entries. x/y are int in output."""
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
