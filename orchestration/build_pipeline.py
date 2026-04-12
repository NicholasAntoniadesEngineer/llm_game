"""Named, ordered pre-place steps for district master plans (trace tags only; semantics unchanged)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

from core.run_log import trace_event
from orchestration.placement import check_functional_placement, log_functional_placement_warnings

logger = logging.getLogger("eternal.engine")


@dataclass
class MasterPlanPreplaceContext:
    """Mutable master plan and engine reference for pipeline steps."""

    engine: Any
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


async def _step_log_intra_plan_tile_overlaps(ctx: MasterPlanPreplaceContext) -> None:
    all_tiles: dict[tuple[int, int], str] = {}
    for struct in ctx.master_plan:
        for t in struct.get("tiles", []):
            key = (t["x"], t["y"])
            if key in all_tiles:
                logger.warning(
                    "Tile overlap: %s and %s at %s",
                    struct["name"],
                    all_tiles[key],
                    key,
                )
            all_tiles[key] = struct.get("name", "?")


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
        name="functional_placement_warnings",
        coro_fn=_step_functional_placement_warnings,
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
