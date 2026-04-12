"""Build wave loop: survey await, landmark/infill waves, district dispatch. Extracted from BuildEngine."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.errors import AgentGenerationError
from core.fingerprint import district_survey_key
from core.run_log import log_event, trace_event
from orchestration.build_wave_phase import (
    BuildWavePhase,
    coerce_build_wave_phase_string,
    compute_build_generation_progress_percent,
    ensure_blueprint_environment_for_generation,
)
from orchestration.district_model import parse_district_dict

logger = logging.getLogger("eternal.engine")

if TYPE_CHECKING:
    from orchestration.engine_ports import BuildGenerationEnginePort


def sort_wave_plan_open_terrain_first(master_plan: list, open_terrain_types: frozenset) -> list:
    """Deterministic order: procedural open-terrain structures first, then others (stable within each group)."""
    procedural: list = []
    rest: list = []
    for struct in master_plan:
        if struct.get("building_type", "") in open_terrain_types:
            procedural.append(struct)
        else:
            rest.append(struct)
    return procedural + rest


async def _schedule_auto_retry_if_pending(engine: "BuildGenerationEnginePort", failure_label: str) -> None:
    if getattr(engine, "_auto_retry_pending", False):
        engine._auto_retry_pending = False
        logger.info("Auto-retry: re-entering run() after %s", failure_label)
        await engine.schedule_run()


def _build_progress_snapshot(engine: "BuildGenerationEnginePort") -> int:
    return compute_build_generation_progress_percent(
        district_build_cursor=engine.district_build_cursor,
        district_index=engine.district_index,
        build_wave_phase=engine.build_wave_phase,
        total_districts=len(engine.districts),
    )


async def run_phase(
    engine: "BuildGenerationEnginePort",
    wave_phase: BuildWavePhase,
    wave_label: str,
    type_filter: frozenset | None,
    district_plans: dict[int, list],
    *,
    start_di: int,
    _get_plan,
) -> bool:
    """Single FSM advance: one persisted wave (landmark or infill) from ``start_di`` through all districts."""
    if not engine.running:
        return False

    await engine._chat("cartographus", "info", f"=== {wave_label} (gen {engine.generation}) ===")
    log_event("engine", wave_label)

    for di in range(start_di, len(engine.districts)):
        if not engine.running:
            return False

        district = parse_district_dict(
            engine.districts[di],
            system_configuration=engine.system_configuration,
        ).as_engine_dict()
        district_name = district["name"]
        engine.world.current_period = district.get("period", "")
        engine.world.current_year = int(
            district.get("year", engine.system_configuration.world_reset_default_year_int)
        )

        survey_sid = district_survey_key(district)
        scenery = engine._district_scenery_summaries.get(survey_sid, "")
        region = district.get("region", {})
        pct = _build_progress_snapshot(engine)
        await engine.broadcast({
            "type": "phase",
            "district": district_name,
            "description": district.get("description", ""),
            "scenery_summary": scenery,
            "index": di + 1,
            "total_districts": len(engine.districts),
            "wave": wave_label,
            "generation": engine.generation,
            "build_progress_percent": pct,
            "region": {
                "x1": region.get("x1", 0),
                "y1": region.get("y1", 0),
                "x2": region.get("x2", 0),
                "y2": region.get("y2", 0),
            },
        })
        await engine.broadcast({
            "type": "timeline",
            "period": district.get("period", ""),
            "year": district.get(
                "year", engine.system_configuration.world_reset_default_year_int
            ),
        })

        engine.update_trace_snapshot(
            phase="build_wave",
            wave=wave_label,
            district_index=di,
            district=district_name,
            generation=engine.generation,
            build_wave_phase_key=wave_phase.value,
            build_progress_percent=pct,
        )
        if di not in district_plans:
            engine.update_trace_snapshot(
                phase="await_survey",
                district_index=di,
                district=district_name,
                wave=wave_label,
                build_progress_percent=pct,
            )
            trace_event(
                "engine",
                "Awaiting survey master plan",
                district_index=di,
                district=district_name,
                wave=wave_label,
                build_progress_percent=pct,
            )
            plan = await _get_plan(di)
            if plan is None:
                await _schedule_auto_retry_if_pending(engine, "survey failure")
                return False
            district_plans[di] = plan

        master_plan = district_plans[di]
        if type_filter is not None:
            wave_plan = [s for s in master_plan if s.get("building_type", "") in type_filter]
        else:
            wave_plan = [
                s for s in master_plan
                if s.get("building_type", "") not in engine._wave_one_building_types_set
            ]

        wave_plan = sort_wave_plan_open_terrain_first(wave_plan, engine._open_terrain_types_set)

        if not wave_plan:
            logger.info(
                "Empty wave plan — skipping district %r wave %s (no structures match this wave)",
                district_name,
                wave_label,
            )
            trace_event(
                "engine",
                "empty_wave_plan_skip",
                district=district_name,
                wave=wave_label,
                district_index=di,
                build_progress_percent=_build_progress_snapshot(engine),
            )
            engine.district_build_cursor = di + 1
            engine.build_wave_phase = wave_phase.value
            await engine._save_state_thread(flush_mode="incremental")
            continue

        logger.info("=== %s: %s (%d structures) ===", wave_label, district_name, len(wave_plan))
        trace_event(
            "engine",
            "Calling _build_district()",
            district=district_name,
            wave=wave_label,
            structures=len(wave_plan),
            build_progress_percent=_build_progress_snapshot(engine),
        )
        engine.update_trace_snapshot(
            phase="build_district_call",
            district=district_name,
            wave=wave_label,
            structure_count=len(wave_plan),
            build_progress_percent=_build_progress_snapshot(engine),
        )
        district_ok = await engine._build_district(district, wave_plan)
        if not district_ok:
            await _schedule_auto_retry_if_pending(engine, "district build failure")
            return False

        engine.district_build_cursor = di + 1
        engine.build_wave_phase = wave_phase.value
        await engine._save_state_thread(flush_mode="incremental")

    engine.district_build_cursor = engine.district_index
    if wave_phase == BuildWavePhase.landmark:
        engine.build_wave_phase = BuildWavePhase.infill.value
    return True


async def resume_build_generation(engine: "BuildGenerationEnginePort") -> bool:
    """Same contract as ``run_build_generation`` — explicit name for resume/restart paths."""
    return await run_build_generation(engine)


async def run_build_generation(engine: "BuildGenerationEnginePort") -> bool:
    """Build all unbuilt districts in two waves. Returns False if cancelled.

    Invariant: procedural ``CityBlueprint.finalize_environment`` runs (idempotently) before
    any structure placement in this generation — see ``ensure_blueprint_environment_for_generation``.
    """
    trace_event(
        "engine",
        "_build_generation() start",
        generation=engine.generation,
        district_index=engine.district_index,
        district_build_cursor=engine.district_build_cursor,
        build_wave_phase=engine.build_wave_phase,
        districts_total=len(engine.districts),
        build_progress_percent=_build_progress_snapshot(engine),
    )
    ensure_blueprint_environment_for_generation(engine)
    engine.update_trace_snapshot(
        phase="_build_generation",
        step="start",
        generation=engine.generation,
        build_progress_percent=_build_progress_snapshot(engine),
        environment_ready=True,
    )
    await engine.tasks.clear_survey_prefetch_handles()
    engine.tasks.start_survey_tasks_from_index(engine.district_index, len(engine.districts))
    if engine.districts:
        logger.info(
            "Survey prefetch: districts %s..%s",
            engine.district_index + 1,
            len(engine.districts),
        )
    district_plans: dict[int, list] = {}

    if engine.district_build_cursor < engine.district_index:
        engine.district_build_cursor = engine.district_index

    async def _get_plan(di: int) -> list | None:
        try:
            return await engine.tasks.await_survey_for_district_index(di)
        except asyncio.CancelledError:
            return None
        except AgentGenerationError as err:
            await engine._pause_for_api_issue(err.pause_reason, err.pause_detail, "cartographus")
            return None

    wave_defs: list[tuple[BuildWavePhase, str, frozenset | None]] = [
        (BuildWavePhase.landmark, "Wave 1 — Landmarks", engine._wave_one_building_types_set),
        (BuildWavePhase.infill, "Wave 2 — Infill", None),
    ]

    wave_outer_started = False

    for wave_phase, wave_label, type_filter in wave_defs:
        if not wave_outer_started:
            if wave_phase != coerce_build_wave_phase_string(engine.build_wave_phase):
                continue
            wave_outer_started = True
            start_di = engine.district_build_cursor
        else:
            engine.build_wave_phase = wave_phase.value
            start_di = engine.district_index
            engine.district_build_cursor = engine.district_index

        wave_ok = await run_phase(
            engine,
            wave_phase,
            wave_label,
            type_filter,
            district_plans,
            start_di=start_di,
            _get_plan=_get_plan,
        )
        if not wave_ok:
            return False

    engine.build_wave_phase = BuildWavePhase.landmark.value
    engine.district_build_cursor = engine.district_index
    engine.district_index = len(engine.districts)
    return True
