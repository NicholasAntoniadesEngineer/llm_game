"""Post-restore read-only checks (no mutations; log from ``main``)."""

from __future__ import annotations

from typing import Any


def quick_integrity_check(
    world: Any,
    *,
    blueprint_dict: dict[str, Any] | None,
    restored_from_save: bool,
) -> list[str]:
    """Return human-readable notes for anything suspicious after ``load_state``."""
    notes: list[str] = []
    if not restored_from_save:
        return notes
    if isinstance(blueprint_dict, dict):
        if not blueprint_dict.get("environment_finalized", False):
            notes.append(
                "Blueprint on disk has environment_finalized=false; "
                "the engine will run finalize_environment on the next build loop entry."
            )
    try:
        tile_count = len(getattr(world, "tiles", {}))
    except TypeError:
        tile_count = 0
    if tile_count > 0 and blueprint_dict is None:
        notes.append("World has tiles but no blueprint file was readable for invariant checks.")
    return notes
