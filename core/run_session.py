"""Mutable run session (scenario) — single source of truth for the active city run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RunSession:
    """Current Eternal Cities run: scenario dict and optional run clock."""

    scenario: dict[str, Any] | None = None
    """Scenario from ``create_scenario`` / save restore; None before city selection."""

    def clear(self) -> None:
        self.scenario = None

    def replace_scenario(self, scenario: dict[str, Any] | None) -> None:
        self.scenario = scenario
