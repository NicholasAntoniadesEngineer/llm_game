"""Build wave loop: survey await, landmark/infill waves, district dispatch. Extracted from BuildEngine."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.errors import AgentGenerationError
from core.fingerprint import district_survey_key
from core.run_log import log_event, trace_event
from orchestration.district_model import parse_district_dict

logger = logging.getLogger("eternal.engine")

if TYPE_CHECKING:
    from orchestration.engine_ports import BuildGenerationEnginePort


async def _schedule_auto_retry_if_pending(engine: "BuildGenerationEnginePort", failure_label: str) -> None:
    if getattr(engine, "_auto_retry_pending", False):
        engine._auto_retry_pending = False
        logger.info("Auto-retry: re-entering run() after %s", failure_label)
        await engine.schedule_run()


async def run_build_generation(engine: "BuildGenerationEnginePort") -> bool:
    """Build all unbuilt districts in two waves. Returns False if cancelled."""
    trace_event(
        "engine",
        "_build_generation() start",
        generation=engine.generation,
        district_index=engine.district_index,
        districts_total=len(engine.districts),
    )
    engine.update_trace_snapshot(phase="_build_generation", step="start", generation=engine.generation)
    await engine.tasks.clear_survey_prefetch_handles()
    engine.tasks.start_survey_tasks_from_index(engine.district_index, len(engine.districts))
    if engine.districts:
        logger.info(
            "Survey prefetch: districts %s..%s",
            engine.district_index + 1,
            len(engine.districts),
        )
    district_plans: dict[int, list] = {}

    async def _get_plan(di: int) -> list | None:
        try:
            return await engine.tasks.await_survey_for_district_index(di)
        except asyncio.CancelledError:
            return None
        except AgentGenerationError as err:
            await engine._pause_for_api_issue(err.pause_reason, err.pause_detail, "cartographus")
            return None

    for wave_label, type_filter in [
        ("Wave 1 — Landmarks", engine._wave_one_building_types_set),
        ("Wave 2 — Infill", None),
    ]:
        if not engine.running:
            return False

        await engine._chat("cartographus", "info", f"=== {wave_label} (gen {engine.generation}) ===")
        log_event("engine", wave_label)

        for di in range(engine.district_index, len(engine.districts)):
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
            await engine.broadcast({
                "type": "phase",
                "district": district_name,
                "description": district.get("description", ""),
                "scenery_summary": scenery,
                "index": di + 1,
                "total_districts": len(engine.districts),
                "wave": wave_label,
                "generation": engine.generation,
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
            )
            if di not in district_plans:
                engine.update_trace_snapshot(phase="await_survey", district_index=di, district=district_name, wave=wave_label)
                trace_event(
                    "engine",
                    "Awaiting survey master plan",
                    district_index=di,
                    district=district_name,
                    wave=wave_label,
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

            if not wave_plan:
                continue

            logger.info(f"=== {wave_label}: {district_name} ({len(wave_plan)} structures) ===")
            trace_event(
                "engine",
                "Calling _build_district()",
                district=district_name,
                wave=wave_label,
                structures=len(wave_plan),
            )
            engine.update_trace_snapshot(
                phase="build_district_call",
                district=district_name,
                wave=wave_label,
                structure_count=len(wave_plan),
            )
            district_ok = await engine._build_district(district, wave_plan)
            if not district_ok:
                await _schedule_auto_retry_if_pending(engine, "district build failure")
                return False

            await engine._save_state_thread(flush_mode="incremental")

    engine.district_index = len(engine.districts)
    return True
