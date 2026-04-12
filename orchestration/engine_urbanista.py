"""Batched Urbanista LLM execution. Extracted from BuildEngine."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.errors import AgentGenerationError

logger = logging.getLogger("eternal.engine")

if TYPE_CHECKING:
    from orchestration.engine_ports import UrbanistaBatchEnginePort


async def execute_batch_urbanista(
    engine: "UrbanistaBatchEnginePort",
    wu_idx: int,
    work_unit: dict,
    urban_jobs: list[dict],
) -> tuple[int, list[tuple[int, dict | BaseException]]]:
    """Execute a batched Urbanista call for 2-3 small buildings.

    Constructs a combined prompt, calls generate_batch(), and splits
    the results back to individual jobs. On batch failure, falls back
    to individual calls so no buildings are lost.

    All paths acquire the same ``urbanista_concurrency_semaphore`` (via
    ``urbanista_generate_*_bounded``) so global concurrency stays bounded;
    batch plus fallbacks may serialize when the pool is busy.
    """
    indices = work_unit["indices"]
    jobs = [urban_jobs[i] for i in indices]
    names = [j["name"] for j in jobs]

    logger.info("Batch Urbanista: %d buildings [%s]", len(indices), ", ".join(names))

    batch_prompt_parts = [
        f"Design these {len(jobs)} buildings. Return a JSON array with one object per building.\n"
        f"Each object should have the same format as a single-building response "
        f"(tiles[], commentary, reference).\n\n"
    ]
    for bi, job in enumerate(jobs):
        batch_prompt_parts.append(f"--- BUILDING {bi + 1}: {job['name']} ---\n{job['prompt']}\n")

    batch_prompt = "\n".join(batch_prompt_parts)

    try:
        results = await engine.generators.urbanista_generate_batch_bounded(
            batch_prompt,
            len(jobs),
            trace_extra={
                "work_unit_index": wu_idx,
                "batch_buildings": names[:12],
            },
        )
    except AgentGenerationError as err:
        logger.warning(
            "Batch Urbanista failed (%s): %s — falling back to individual",
            err.pause_reason,
            (err.pause_detail or "")[:200],
        )
        results = []
    except Exception as e:
        logger.warning("Batch Urbanista call failed: %s — falling back to individual", e)
        results = []

    if len(results) >= len(jobs):
        logger.info("Batch Urbanista success: got %d results for %d jobs", len(results), len(jobs))
        return (wu_idx, [(indices[i], results[i]) for i in range(len(jobs))])

    if results:
        logger.warning(
            "Batch Urbanista partial: got %d results for %d jobs — using partial + fallback",
            len(results), len(jobs),
        )
        out: list[tuple[int, dict | BaseException]] = []
        for i in range(len(results)):
            out.append((indices[i], results[i]))
        for i in range(len(results), len(jobs)):
            job_idx = indices[i]
            try:
                r = await engine.generators.urbanista_generate_bounded(
                    urban_jobs[job_idx]["prompt"],
                    trace_extra={
                        "structure": urban_jobs[job_idx]["name"],
                        "building_type": urban_jobs[job_idx]["btype"],
                        "phase": "batch_fallback_single",
                    },
                )
                out.append((job_idx, r))
            except BaseException as err:
                out.append((job_idx, err))
        return (wu_idx, out)

    logger.warning("Batch Urbanista empty — falling back to %d individual calls", len(jobs))
    out = []
    for i, job_idx in enumerate(indices):
        try:
            r = await engine.generators.urbanista_generate_bounded(
                urban_jobs[job_idx]["prompt"],
                trace_extra={
                    "structure": urban_jobs[job_idx]["name"],
                    "building_type": urban_jobs[job_idx]["btype"],
                    "phase": "batch_fallback_individual",
                },
            )
            out.append((job_idx, r))
        except BaseException as err:
            out.append((job_idx, err))
    return (wu_idx, out)
