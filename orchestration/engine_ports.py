"""Narrow protocols for extracted engine modules — avoids ``Any`` and documents the surface area."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from core.config import Config
from world.state import WorldState


@runtime_checkable
class MasterPlanPreplaceEnginePort(Protocol):
    """Narrow surface for master-plan preplace pipeline steps."""

    world: WorldState
    system_configuration: Config

    async def broadcast(self, message: dict) -> None: ...

    async def _chat(
        self, sender: str, msg_type: str, content: str, approved: Any = None
    ) -> None: ...

    def _apply_master_plan_validation(self, master_plan: list, context: str) -> list: ...


@runtime_checkable
class BuildGenerationEnginePort(Protocol):
    """Subset of BuildEngine used by ``run_build_generation``."""

    generation: int
    district_index: int
    districts: list
    running: bool
    world: WorldState
    system_configuration: Config
    _wave_one_building_types_set: frozenset
    tasks: Any
    _auto_retry_pending: bool

    async def broadcast(self, message: dict) -> None: ...
    async def _chat(self, agent: str, level: str, text: str) -> None: ...
    async def _build_district(self, district: dict, wave_plan: list) -> bool: ...
    async def _save_state_thread(self, flush_mode: str = "incremental") -> None: ...
    async def _pause_for_api_issue(self, reason: str, detail: str, agent_role: str) -> None: ...
    async def schedule_run(self) -> Any: ...
    def update_trace_snapshot(self, **kwargs: Any) -> None: ...


@runtime_checkable
class UrbanistaBatchEnginePort(Protocol):
    """Subset of BuildEngine used by ``execute_batch_urbanista``."""

    urbanista: Any
    tasks: Any


@runtime_checkable
class DistrictBuildEnginePort(Protocol):
    """Subset of BuildEngine used by ``run_district_build``."""

    system_configuration: Config
    world: WorldState
    blueprint: Any
    running: bool
    _open_terrain_types_set: frozenset
    _batchable_types_set: frozenset
    style_memory: Any
    world_tools: Any
    urbanista: Any
    generators: Any
    tasks: Any
    _district_scenery_summaries: dict[str, str]
    _district_palettes: dict[str, dict]

    @property
    def scenario(self) -> dict[str, Any] | None: ...

    def update_trace_snapshot(self, **kwargs: Any) -> None: ...
    async def broadcast(self, message: dict) -> None: ...
    async def _pause_for_api_issue(
        self, pause_reason: str, pause_detail: str, agent_role: str
    ) -> None: ...
    async def _chat(
        self, sender: str, msg_type: str, content: str, approved: Any = None
    ) -> None: ...
    async def _set_status(self, agent: str, status: str, detail: Any = None) -> None: ...
    async def _save_state_thread(self, flush_mode: str = "incremental") -> None: ...
    def _apply_master_plan_validation(self, master_plan: list, context: str) -> list: ...
    def _compute_city_center_and_radius(self) -> tuple[tuple[float, float] | None, float]: ...
    def _compute_transition_hint(self, anchor_x: int, anchor_y: int, district: dict) -> str: ...
    async def _execute_batch_urbanista(
        self, wu_idx: int, work_unit: dict, urban_jobs: list
    ) -> tuple[int, list[tuple[int, Any]]]: ...


@runtime_checkable
class TaskManagerPersistenceReadsPort(Protocol):
    """Live district index, generation, and scenario for throttled saves."""

    district_index: int
    generation: int

    @property
    def scenario(self) -> dict[str, Any] | None: ...


@runtime_checkable
class GeneratorsHostPort(Protocol):
    """Surface required by ``Generators`` (BuildEngine implements this protocol)."""

    system_configuration: Config
    world: WorldState
    chat_history: list[Any]
    districts: list[Any]
    district_index: int
    generation: int
    blueprint: Any
    tasks: Any
    planner_skeleton: Any
    planner_refine: Any
    surveyor: Any
    urbanista: Any
    _source_policy: str
    _fused_seed_master_plan: list | None
    _survey_cache_lock: Any
    _survey_cache: dict[str, list] | None
    _district_scenery_summaries: dict[str, str]
    _district_palettes: dict[str, dict]
    _last_skeleton_result: dict[str, Any] | None

    @property
    def scenario(self) -> dict[str, Any] | None: ...

    @property
    def run_fingerprint(self) -> str: ...

    async def broadcast(self, message: dict) -> None: ...

    def update_trace_snapshot(self, **kwargs: Any) -> None: ...
