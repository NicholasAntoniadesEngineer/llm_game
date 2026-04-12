"""Named, ordered pre-place steps for district master plans (trace tags only; semantics unchanged)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence

from core.errors import AgentGenerationError
from core.run_log import trace_event
from orchestration.engine_ports import MasterPlanPreplaceEnginePort
from orchestration.master_plan_geometry import (
    intra_plan_tile_overlaps,
    normalize_master_plan_tile_coordinates,
)
from orchestration.placement import check_functional_placement, log_functional_placement_warnings
from orchestration.placement_repair import prune_bridges_not_adjacent_to_water_when_water_exists

logger = logging.getLogger("eternal.engine")


@dataclass
class MasterPlanPreplaceContext:
    """Mutable master plan and engine reference for pipeline steps."""

    engine: MasterPlanPreplaceEnginePort
    district_key: str
    district: dict
    master_plan: list


@dataclass(frozen=True)
class BuildPipelineStep:
    """One async stage; ``name`` appears in ``trace_event`` as ``pipeline_step``."""

    name: str
    coro_fn: Callable[[MasterPlanPreplaceContext], Awaitable[None]]


async def run_master_plan_preplace_pipeline(
    steps: Sequence[BuildPipelineStep],
    ctx: MasterPlanPreplaceContext,
) -> None:
    """Run steps in order; each step is traced before execution."""
    for step in steps:
        trace_event(
            "engine",
            "build_pipeline_step",
            pipeline_step=step.name,
            district=ctx.district_key,
        )
        await step.coro_fn(ctx)


async def _step_validate_master_plan(ctx: MasterPlanPreplaceContext) -> None:
    validated = ctx.engine._apply_master_plan_validation(
        list(ctx.master_plan),
        f"District {ctx.district_key!r} master plan",
    )
    ctx.master_plan.clear()
    ctx.master_plan.extend(validated)


async def _step_normalize_master_plan_tile_coordinates(
    ctx: MasterPlanPreplaceContext,
) -> None:
    normalize_master_plan_tile_coordinates(ctx.master_plan)


async def _step_functional_placement_warnings(ctx: MasterPlanPreplaceContext) -> None:
    log_functional_placement_warnings(ctx.master_plan, ctx.district_key)
    placement_warnings = check_functional_placement(ctx.master_plan)
    if not placement_warnings:
        return
    await ctx.engine.broadcast(
        {
            "type": "placement_warnings",
            "district": ctx.district_key,
            "warnings": placement_warnings,
            "count": len(placement_warnings),
        }
    )
    preview = placement_warnings[:12]
    await ctx.engine._chat(
        "cartographus",
        "info",
        "Placement checks: "
        + str(len(placement_warnings))
        + " note(s). "
        + preview[0][:200]
        + ("…" if len(preview[0]) > 200 else ""),
    )


async def _step_prune_unusable_bridges(ctx: MasterPlanPreplaceContext) -> None:
    removed = prune_bridges_not_adjacent_to_water_when_water_exists(ctx.master_plan)
    if removed:
        trace_event(
            "engine",
            "placement_repair_pruned_bridges",
            district=ctx.district_key,
            removed_count=removed,
        )


async def _step_log_intra_plan_tile_overlaps(ctx: MasterPlanPreplaceContext) -> None:
    overlaps = intra_plan_tile_overlaps(ctx.master_plan)
    for overlap_line in overlaps:
        logger.warning("%s", overlap_line)
    if (
        int(ctx.engine.system_configuration.master_plan_fail_on_intra_plan_tile_overlap_flag) == 1
        and overlaps
    ):
        raise AgentGenerationError(
            "bad_model_output",
            "Intra-plan tile overlap(s): " + "; ".join(overlaps[:24]),
        )


async def _step_filter_already_occupied_structures(ctx: MasterPlanPreplaceContext) -> None:
    world = ctx.engine.world
    filtered_plan: list = []
    for struct in ctx.master_plan:
        dominated = True
        for t in struct.get("tiles", []):
            existing = world.get_tile(t.get("x", 0), t.get("y", 0))
            if not existing or existing.terrain == "empty":
                dominated = False
                break
        if not dominated:
            filtered_plan.append(struct)
        else:
            logger.info(
                "Skipping %s — all tiles already occupied by prior district",
                struct.get("name", "?"),
            )
    if filtered_plan != ctx.master_plan:
        logger.info(
            "Cross-district overlap: %d/%d structures remain after filtering",
            len(filtered_plan),
            len(ctx.master_plan),
        )
        ctx.master_plan.clear()
        ctx.master_plan.extend(filtered_plan)


MASTER_PLAN_PREPLACE_STEPS: tuple[BuildPipelineStep, ...] = (
    BuildPipelineStep(name="validate_master_plan", coro_fn=_step_validate_master_plan),
    BuildPipelineStep(
        name="normalize_master_plan_tile_coordinates",
        coro_fn=_step_normalize_master_plan_tile_coordinates,
    ),
    BuildPipelineStep(
        name="functional_placement_warnings",
        coro_fn=_step_functional_placement_warnings,
    ),
    BuildPipelineStep(
        name="prune_unusable_bridges",
        coro_fn=_step_prune_unusable_bridges,
    ),
    BuildPipelineStep(
        name="log_intra_plan_tile_overlaps",
        coro_fn=_step_log_intra_plan_tile_overlaps,
    ),
    BuildPipelineStep(
        name="filter_already_occupied_structures",
        coro_fn=_step_filter_already_occupied_structures,
    ),
)
