"""Post-restore read-only checks (no mutations; log from ``main``)."""

from __future__ import annotations

from typing import Any


def quick_integrity_check(
    world: Any,
    *,
    blueprint_dict: dict[str, Any] | None,
    restored_from_save: bool,
    cursor_reconcile_notes: list[str] | None = None,
) -> list[str]:
    """Return human-readable notes for anything suspicious after ``load_state``."""
    notes: list[str] = []
    if not restored_from_save:
        return notes
    if cursor_reconcile_notes:
        notes.extend(cursor_reconcile_notes)
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
    validate_fn = getattr(world, "validate_integrity", None)
    if callable(validate_fn):
        try:
            integrity_notes = validate_fn()
            notes.extend(integrity_notes)
            cfg = getattr(world, "system_configuration", None)
            if cfg is not None and int(cfg.world_place_tile_reject_out_of_bounds_flag) == 0:
                if any("outside configured coordinate bounds" in n for n in integrity_notes):
                    notes.append(
                        "Tiles exist outside configured coordinate limits; set "
                        "world_place_tile_reject_out_of_bounds=1 to reject future out-of-range commits."
                    )
        except Exception as exc:
            notes.append(f"WorldState.validate_integrity raised: {type(exc).__name__}: {exc}")
    return notes
