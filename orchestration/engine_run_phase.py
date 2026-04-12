"""High-level lifecycle phase for the build engine (debugging / tracing)."""

from __future__ import annotations

from enum import Enum


class EngineRunPhase(str, Enum):
    """Coarse state: what the orchestrator is doing between user actions."""

    idle = "idle"
    discovering = "discovering"
    building = "building"
    paused_api = "paused_api"
    shutting_down = "shutting_down"
