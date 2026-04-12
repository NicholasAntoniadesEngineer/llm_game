"""Persisted wave FSM within one generation (landmark pass vs infill pass).

``save_state`` / ``load_state`` store ``build_wave_phase`` as ``landmark`` or ``infill``.
This module is the single place for coercion, progress, and environment gating.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from core.config import Config
from world.state import WorldState


class BuildWavePhase(str, Enum):
    """Two-pass district build: monuments/anchors first, then remaining structures."""

    landmark = "landmark"
    infill = "infill"


def coerce_build_wave_phase_string(raw: str | None) -> BuildWavePhase:
    """Map persisted or in-memory strings to enum (defaults to landmark)."""
    s = str(raw or "").strip().lower()
    if s == BuildWavePhase.infill.value:
        return BuildWavePhase.infill
    return BuildWavePhase.landmark


def compute_build_generation_progress_percent(
    *,
    district_build_cursor: int,
    district_index: int,
    build_wave_phase: str,
    total_districts: int,
) -> int:
    """Rough 0–100 progress for UI/heartbeat across both waves (resume-safe).

    Each district is visited twice (landmark then infill). Mid-wave resume uses
    ``district_build_cursor``; completed generations set ``district_index == total``.
    """
    n = max(0, int(total_districts))
    if n == 0:
        return 100
    wave = coerce_build_wave_phase_string(build_wave_phase)
    cursor = max(0, int(district_build_cursor))
    idx = max(0, int(district_index))
    if idx >= n:
        return 100
    if wave == BuildWavePhase.landmark:
        completed = min(cursor, n)
        return int(100 * (completed / max(1, 2 * n)))
    completed_first_pass = n
    second_pass_done = min(cursor, n)
    return int(100 * ((completed_first_pass + second_pass_done) / max(1, 2 * n)))


def ensure_blueprint_environment_for_generation(engine: Any) -> None:
    """Idempotent procedural terrain + road masks before any structure placement."""
    bp = getattr(engine, "blueprint", None)
    if bp is None:
        return
    world: WorldState = engine.world
    system_configuration: Config = engine.system_configuration
    districts: list | None = getattr(engine, "districts", None)
    bp.finalize_environment(
        world,
        system_configuration=system_configuration,
        districts=districts,
    )
