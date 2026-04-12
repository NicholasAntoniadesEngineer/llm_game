"""Explicit build-pipeline checkpoint for progression tracing and resume consistency."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

_fsm_logger = logging.getLogger("eternal.engine_state_machine")

if TYPE_CHECKING:
    from orchestration.engine_ports import BuildGenerationEnginePort


class EngineBuildPipelineState(str, Enum):
    """High-level wave position (orthogonal to ``EngineRunPhase`` process state)."""

    idle = "idle"
    awaiting_survey = "awaiting_survey"
    building_district_wave = "building_district_wave"
    between_district_checkpoints = "between_district_checkpoints"
    wave_complete = "wave_complete"


@dataclass
class EngineStateMachine:
    """Checkpoint dict + guarded transitions for the two-wave district build."""

    pipeline_state: EngineBuildPipelineState = EngineBuildPipelineState.idle
    last_successful_district_name: str = ""
    last_completed_district_index: int = -1
    district_retry_count_by_index: dict[int, int] = field(default_factory=dict)

    def transition(
        self,
        to_state: EngineBuildPipelineState,
        *,
        from_states: tuple[EngineBuildPipelineState, ...] | None = None,
    ) -> bool:
        if from_states is not None and self.pipeline_state not in from_states:
            return False
        self.pipeline_state = to_state
        return True

    def record_district_wave_success(self, district_index: int, district_name: str) -> None:
        allowed_priors = (
            EngineBuildPipelineState.building_district_wave,
            EngineBuildPipelineState.between_district_checkpoints,
            EngineBuildPipelineState.awaiting_survey,
        )
        prior = self.pipeline_state
        if prior not in allowed_priors:
            _fsm_logger.warning(
                "record_district_wave_success from unexpected pipeline_state=%s (district=%r index=%s)",
                prior.value,
                district_name,
                district_index,
            )
        self.last_completed_district_index = int(district_index)
        self.last_successful_district_name = str(district_name)
        self.district_retry_count_by_index.pop(int(district_index), None)
        self.pipeline_state = EngineBuildPipelineState.between_district_checkpoints

    def bump_district_retry(self, district_index: int) -> int:
        di = int(district_index)
        n = self.district_retry_count_by_index.get(di, 0) + 1
        self.district_retry_count_by_index[di] = n
        return n

    def reset_retry_counters(self) -> None:
        self.district_retry_count_by_index.clear()

    def to_checkpoint_dict(self) -> dict[str, Any]:
        return {
            "pipeline_state": self.pipeline_state.value,
            "last_successful_district_name": self.last_successful_district_name,
            "last_completed_district_index": self.last_completed_district_index,
            "district_retry_count_by_index": dict(self.district_retry_count_by_index),
        }

    def sync_from_engine(self, engine: BuildGenerationEnginePort) -> None:
        """Best-effort align checkpoint indices with engine counters after load."""
        di = int(getattr(engine, "district_build_cursor", 0))
        if self.last_completed_district_index < 0 and di > 0:
            self.last_completed_district_index = max(0, di - 1)

    def reconcile_loaded_cursors(
        self,
        *,
        district_index: int,
        district_build_cursor: int,
        districts_len: int,
    ) -> list[str]:
        """Return notes when persisted cursors look inconsistent (caller may log)."""
        notes: list[str] = []
        if districts_len > 0:
            if district_build_cursor < 0 or district_build_cursor > districts_len:
                notes.append(
                    f"district_build_cursor={district_build_cursor} out of range for "
                    f"districts_len={districts_len} — clamping recommended"
                )
            if district_index < 0 or district_index > districts_len:
                notes.append(
                    f"district_index={district_index} out of range for districts_len={districts_len}"
                )
            if (
                district_build_cursor >= 0
                and district_index >= 0
                and district_build_cursor < district_index
            ):
                notes.append(
                    f"district_build_cursor={district_build_cursor} trails district_index={district_index} "
                    f"— verify resume intent (partial wave vs. index bookkeeping)"
                )
        return notes
