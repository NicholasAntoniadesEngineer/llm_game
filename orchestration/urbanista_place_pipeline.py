"""Traced, ordered Urbanista architecture JSON hardening before tile placement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.run_log import trace_event
from orchestration.validation import sanitize_urbanista_output, validate_urbanista_arch_result


@dataclass(frozen=True)
class UrbanistaArchSanitizeStep:
    """One named stage for architecture JSON normalization."""

    name: str
    sync_fn: Callable[[dict], dict]


def _validate_arch_only(arch: dict) -> dict:
    validate_urbanista_arch_result(arch)
    return arch


SANITIZE_AND_VALIDATE_ARCH_STEPS: tuple[UrbanistaArchSanitizeStep, ...] = (
    UrbanistaArchSanitizeStep(
        name="sanitize_urbanista_output",
        sync_fn=lambda d: sanitize_urbanista_output(d),
    ),
    UrbanistaArchSanitizeStep(
        name="validate_urbanista_arch_result",
        sync_fn=_validate_arch_only,
    ),
)


def run_traced_urbanista_arch_sanitize_and_validate(
    arch_result: dict,
    *,
    district_key: str,
    structure_name: str,
) -> dict:
    """Run sanitize + schema validation with ``trace_event`` per step."""
    current = arch_result
    for step in SANITIZE_AND_VALIDATE_ARCH_STEPS:
        trace_event(
            "engine",
            "urbanista_place_pipeline_step",
            pipeline_step=step.name,
            district=district_key,
            structure=structure_name,
        )
        current = step.sync_fn(current)
    return current
