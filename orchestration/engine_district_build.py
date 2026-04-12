"""District build wave — extracted from BuildEngine for maintainability."""

import asyncio
import copy
import logging
from typing import TYPE_CHECKING

from agents.base import BaseAgent
from agents.golden_specs import get_fallback_spec_components_for_building_type
from core.errors import AgentGenerationError, PlacementError, UrbanistaValidationError
from core.fingerprint import district_survey_key
from core.run_log import trace_event
from orchestration.build_pipeline import (
    MASTER_PLAN_PREPLACE_STEPS,
    MasterPlanPreplaceContext,
    run_master_plan_preplace_pipeline,
)
from orchestration.engine_terrain import generate_terrain_procedurally
from orchestration.prompt_builder import build_building_prompt
from orchestration.urbanista_place_pipeline import run_traced_urbanista_arch_sanitize_and_validate
from orchestration.validation import (
    validate_urbanista_tiles,
    check_component_collisions,
    try_prune_colliding_decorative_components,
)
from orchestration.placement import generate_valid_candidates
from orchestration.world_commit import apply_tile_placements
from world.placement_validator import PlacementValidationContext

if TYPE_CHECKING:
    from orchestration.engine_ports import DistrictBuildEnginePort

logger = logging.getLogger("eternal.engine")


async def run_district_build(
    engine: "DistrictBuildEnginePort",
    district: dict,
    master_plan: list,
) -> bool:
    district_key = district.get("name", "unknown")
    survey_sid = district_survey_key(district)
    trace_event(
        "engine",
        "_build_district() entered",
        district=district_key,
        master_plan_structures=len(master_plan),
    )
    engine.update_trace_snapshot(phase="build_district_inner", district=district_key, structures=len(master_plan))
    if not master_plan:
        await engine._pause_for_api_issue(
            "unknown",
            f"No master plan for district {district_key!r}.",
            "cartographus",
        )
        return False

    preplace_ctx = MasterPlanPreplaceContext(
        engine=engine,
        district_key=district_key,
        district=district,
        master_plan=master_plan,
    )
    try:
        await run_master_plan_preplace_pipeline(MASTER_PLAN_PREPLACE_STEPS, preplace_ctx)
    except AgentGenerationError as err:
        await engine._pause_for_api_issue(err.pause_reason, err.pause_detail, "cartographus")
        return False

    engine.tasks.reset_structure_save_throttle_counter()

    logger.info(f"Master plan: {len(master_plan)} structures")
    await engine.broadcast({"type": "master_plan", "plan": master_plan})

    scenario = engine.scenario or {}
    city_loc = scenario.get("location") or ""
    district_scenery = engine._district_scenery_summaries.get(survey_sid, "")
    district_palette = engine._district_palettes.get(survey_sid)
    district_ref_year = district.get("year")
    if district_ref_year is None:
        district_ref_year = scenario.get("year_start", 0)

    # Precompute city center and radius for height gradient hints
    city_center, city_radius = engine._compute_city_center_and_radius()
    try:
        district_ref_year_i = int(district_ref_year)
    except (TypeError, ValueError):
        district_ref_year_i = 0

    # Precompute centers by index (duplicate building names would collide if keyed by name only)
    n_plan = len(master_plan)
    centers_list: list[tuple[float, float] | None] = [None] * n_plan
    for idx, s in enumerate(master_plan):
        stiles = s.get("tiles", [])
        if stiles:
            centers_list[idx] = (
                sum(t["x"] for t in stiles) / len(stiles),
                sum(t["y"] for t in stiles) / len(stiles),
            )

    # ─── Neighbor context per structure; physical brief from Cartographus survey only (no Historicus LLM) ───
    structure_contexts: list[dict] = []
    buildable = []
    for struct_idx, structure in enumerate(master_plan):
        if struct_idx > 0 and struct_idx % 24 == 0:
            await asyncio.sleep(0)
            if not engine.running:
                return False
        name = structure.get("name", "Structure")
        btype = structure.get("building_type", "building")
        tiles = structure.get("tiles", [])
        if not tiles:
            continue
        first_tile = tiles[0]
        existing_tile = engine.world.get_tile(first_tile["x"], first_tile["y"])
        if existing_tile and existing_tile.terrain != "empty":
            logger.info(f"Skipping {name} — already built")
            await engine._chat("cartographus", "info", f"Skipping {name} — already built.")
            continue
        buildable.append(structure)

        my_center = centers_list[struct_idx]
        if not my_center:
            my_center = (0.0, 0.0)
        neighbors = []
        for other_idx, other in enumerate(master_plan):
            if other_idx > 0 and other_idx % 48 == 0:
                await asyncio.sleep(0)
                if not engine.running:
                    return False
            if other_idx == struct_idx:
                continue
            oc = centers_list[other_idx]
            if not oc:
                continue
            other_name = other.get("name", "")
            dist_tiles = round(((my_center[0] - oc[0]) ** 2 + (my_center[1] - oc[1]) ** 2) ** 0.5, 1)
            dist_meters = round(dist_tiles * engine.system_configuration.grid.world_scale_meters_per_tile)
            neighbors.append({"name": other_name, "type": other.get("building_type"), "distance_tiles": dist_tiles, "distance_m": dist_meters})
        neighbors.sort(key=lambda n: n["distance_tiles"])
        nearest = neighbors[:5]
        neighbor_desc = "\n".join(
            f"  - {n['name']} ({n['type']}): {n['distance_tiles']} tiles away ({n['distance_m']}m)"
            for n in nearest
        )

        structure_contexts.append({
            "neighbor_desc": neighbor_desc,
            "nearest": nearest,
        })

    # ─── Bounded-parallel Urbanista, then ordered placement ───
    urban_jobs: list[dict] = []
    buildable_total = len(buildable)
    for idx, structure in enumerate(buildable):
        if not engine.running:
            return False

        name = structure.get("name", "Structure")
        btype = structure.get("building_type", "building")
        tiles = structure.get("tiles", [])
        desc = (structure.get("description") or "").strip()
        hist_note = (structure.get("historical_note") or "").strip()
        ctx = structure_contexts[idx]
        neighbor_desc = ctx["neighbor_desc"]
        nearest = ctx["nearest"]

        engine.update_trace_snapshot(
            phase="district_prep_prompts",
            district=district_key,
            prep_index=idx,
            prep_total=buildable_total,
            prep_structure=name,
            prep_building_type=btype,
        )
        logger.info(
            "District %s: preparing Urbanista prompt %d/%d — %s (%s)",
            district_key,
            idx + 1,
            buildable_total,
            name,
            btype,
        )
        await asyncio.sleep(0)

        if btype not in engine._open_terrain_types_set:
            await engine._chat(
                "cartographus",
                "info",
                (
                    f"Building: {name} ({btype}, {len(tiles)} tiles). "
                    + (f"Nearest: {nearest[0]['name']} at {nearest[0]['distance_m']}m" if nearest else "")
                ),
            )

        hist_result = {
            "commentary": desc or f"{name} ({btype}) in {district.get('name', 'this district')}.",
            "historical_note": hist_note,
        }
        physical_desc = hist_result["commentary"]
        hist_detail = hist_result.get("historical_note", "")
        if hist_detail:
            physical_desc += f"\n\nSurveyor detail: {hist_detail}"

        xs = [t["x"] for t in tiles]
        ys = [t["y"] for t in tiles]
        tile_w = max(xs) - min(xs) + 1
        tile_d = max(ys) - min(ys) + 1
        fp_scale = engine.system_configuration.footprint_width_depth_scale_factor
        footprint_w = round(tile_w * fp_scale, 2)
        footprint_d = round(tile_d * fp_scale, 2)
        anchor_x, anchor_y = min(xs), min(ys)

        # Compute elevation from blueprint hills (authoritative) instead of AI-specified values
        # which are on a different scale. This prevents buildings creating sinkholes in terrain.
        if engine.blueprint and engine.blueprint.hills:
            tile_elevations = [
                round(engine.blueprint.elevation_at(t["x"], t["y"]), 2)
                for t in tiles
            ]
        else:
            tile_elevations = [t.get("elevation", district.get("elevation", 0.0)) for t in tiles]
        avg_elevation = round(sum(tile_elevations) / len(tile_elevations), 2) if tile_elevations else 0.0

        env_note = (structure.get("environment_note") or "").strip()

        if btype in engine._open_terrain_types_set:
            # OPTIMIZATION: Generate terrain tiles procedurally (no Urbanista LLM call).
            # Roads, forums, gardens, water, and grass use simple color + scenery spec.
            terrain_result = generate_terrain_procedurally(
                name=name, btype=btype, tiles=tiles,
                avg_elevation=avg_elevation,
                district_palette=district_palette,
                physical_desc=physical_desc,
                terrain_defaults_dictionary=engine.system_configuration.terrain.terrain_defaults_dictionary,
                procedural_terrain_description_max_chars=(
                    engine.system_configuration.procedural_terrain_description_max_chars
                ),
                procedural_terrain_fallback_hex_color=(
                    engine.system_configuration.procedural_terrain_fallback_hex_color
                ),
            )
            terrain_tile_triples: list[tuple[int | float, int | float, dict]] = []
            for td in terrain_result.get("tiles", []):
                x, y = td.get("x"), td.get("y")
                if x is not None and y is not None:
                    td_commit = dict(td)
                    if engine.blueprint and engine.blueprint.hills:
                        td_commit["elevation"] = round(engine.blueprint.elevation_at(x, y), 2)
                    elif "elevation" not in td_commit or td_commit["elevation"] is None:
                        td_commit["elevation"] = district.get("elevation", 0.0)
                    td_commit["period"] = district.get("period", "")
                    td_commit["placed_by"] = "faber"
                    td_commit["historical_note"] = hist_note
                    terrain_tile_triples.append((x, y, td_commit))
            terrain_batch = apply_tile_placements(
                engine.world,
                terrain_tile_triples,
                system_configuration=engine.system_configuration,
            )
            if terrain_batch.place_tile_rejections_count:
                logger.warning(
                    "Procedural terrain batch dropped %s tiles for %r (place_tile rejected)",
                    terrain_batch.place_tile_rejections_count,
                    name,
                )
            placed_terrain = terrain_batch.placed_tile_dicts
            if placed_terrain:
                await engine.broadcast({
                    "type": "tile_update", "tiles": placed_terrain,
                    "turn": engine.world.turn,
                    "period": district.get("period", ""),
                    "year": district.get("year", ""),
                })
                logger.info("Placed %d terrain tiles for %s (no LLM call)", len(placed_terrain), name)
            engine.world.turn += 1
            await engine.tasks.persist_progress_after_structure()
            continue  # Skip Urbanista pipeline for terrain
        else:
            try:
                transition_hint = engine._compute_transition_hint(anchor_x, anchor_y, district)
                prompt = build_building_prompt(
                    name=name, btype=btype, tiles=tiles,
                    anchor_x=anchor_x, anchor_y=anchor_y,
                    tile_w=tile_w, tile_d=tile_d,
                    footprint_w=footprint_w, footprint_d=footprint_d,
                    avg_elevation=avg_elevation,
                    city_loc=city_loc, period=scenario.get("period", ""),
                    district_ref_year_i=district_ref_year_i,
                    neighbor_desc=neighbor_desc,
                    physical_desc=physical_desc,
                    district_scenery=district_scenery,
                    env_note=env_note,
                    system_configuration=engine.system_configuration,
                    district_palette=district_palette,
                    world_state=engine.world,
                    city_center=city_center,
                    city_radius=city_radius,
                    transition_hint=transition_hint,
                )
            except ValueError as exc:
                await engine._pause_for_api_issue("unknown", str(exc), "urbanista")
                return False

        # ── Inject coherence context (~100-150 tokens) ──
        context_parts = []
        if engine.blueprint:
            ctx_line = engine.blueprint.build_context_line(
                engine.world, anchor_x, anchor_y, district_key,
            )
            if ctx_line:
                context_parts.append(ctx_line)
        style_ctx = engine.style_memory.format_style_context()
        if style_ctx:
            context_parts.append(style_ctx)
        spatial_ctx = engine.world_tools.format_context_block(anchor_x, anchor_y, btype)
        if spatial_ctx:
            context_parts.append(spatial_ctx)
        if context_parts:
            prompt = "\n".join(context_parts) + "\n" + prompt

        urban_jobs.append({
            "name": name,
            "btype": btype,
            "tiles": tiles,
            "desc": desc,
            "hist_note": hist_note,
            "hist_result": hist_result,
            "anchor_x": anchor_x,
            "anchor_y": anchor_y,
            "footprint_w": footprint_w,
            "footprint_d": footprint_d,
            "prompt": prompt,
        })

    # ─── Streaming Urbanista: place each structure as soon as its design completes ───
    # Instead of waiting for all N structures, fire all tasks and place each result
    # immediately as it arrives. Buildings appear on screen within seconds of each
    # Urbanista call finishing, not after a 5-10 minute batch wait.
    #
    # OPTIMIZATION: Consecutive small/simple buildings (taberna, warehouse, insula,
    # domus, market) are batched 2-3 into a single Urbanista call. This saves one
    # system prompt worth of tokens per additional building in the batch.
    skipped = 0
    consecutive_failures = 0
    max_consecutive_failures = (
        engine.system_configuration.urbanista_max_consecutive_failures_before_pause
    )
    completed_design_jobs_count = 0

    if urban_jobs:
        await engine._set_status(
            "urbanista",
            "thinking",
            detail=f"Designing {len(urban_jobs)} structure(s) — each AI call may take minutes.",
        )

        # ── Group consecutive batchable jobs ──
        work_units: list[dict] = []  # {"type": "single"|"batch", "indices": [int]}
        i = 0
        while i < len(urban_jobs):
            job = urban_jobs[i]
            job_tiles = len(job["tiles"])
            if (
                job["btype"] in engine._batchable_types_set
                and job_tiles <= engine.system_configuration.urbanista_batchable_tile_max_count
            ):
                batch_indices = [i]
                batch_tiles = job_tiles
                j = i + 1
                while (
                    j < len(urban_jobs)
                    and len(batch_indices) < engine.system_configuration.performance.maximum_batch_size_value
                    and batch_tiles + len(urban_jobs[j]["tiles"]) <= (
                        engine.system_configuration.performance.maximum_batch_tiles_count
                    )
                    and urban_jobs[j]["btype"] in engine._batchable_types_set
                    and len(urban_jobs[j]["tiles"]) <= (
                        engine.system_configuration.urbanista_batchable_tile_max_count
                    )
                ):
                    batch_indices.append(j)
                    batch_tiles += len(urban_jobs[j]["tiles"])
                    j += 1
                if len(batch_indices) >= 2:
                    work_units.append({"type": "batch", "indices": batch_indices})
                    i = j
                    continue
            work_units.append({"type": "single", "indices": [i]})
            i += 1

        batch_count = sum(1 for wu in work_units if wu["type"] == "batch")
        batched_buildings = sum(len(wu["indices"]) for wu in work_units if wu["type"] == "batch")
        await engine._chat(
            "urbanista",
            "info",
            f"Designing {len(urban_jobs)} structures (max "
            f"{engine.system_configuration.performance.urbanista_maximum_concurrent_calls} concurrent)"
            + (f" — {batched_buildings} batched into {batch_count} calls" if batch_count else "")
            + " — placing as each completes...",
        )
        await engine.broadcast({
            "type": "build_progress",
            "structure": "",
            "building_type": "",
            "done": 0,
            "total": len(urban_jobs),
            "district": district_key,
        })

        # Wrap each work unit to carry its index
        async def _design_work_unit(wu_idx: int, work_unit: dict) -> tuple[int, list[tuple[int, dict | BaseException]]]:
            """Returns (wu_idx, [(job_idx, result_or_error), ...])."""
            indices = work_unit["indices"]
            if work_unit["type"] == "batch" and len(indices) >= 2:
                return await engine._execute_batch_urbanista(wu_idx, work_unit, urban_jobs)
            else:
                job_idx = indices[0]
                try:
                    result = await engine.generators.urbanista_generate_bounded(
                        urban_jobs[job_idx]["prompt"],
                        trace_extra={
                            "district": district_key,
                            "structure": urban_jobs[job_idx]["name"],
                            "building_type": urban_jobs[job_idx]["btype"],
                            "work_unit_index": wu_idx,
                        },
                    )
                    return (wu_idx, [(job_idx, result)])
                except BaseException as err:
                    return (wu_idx, [(job_idx, err)])

        pending = [
            asyncio.create_task(_design_work_unit(wu_i, wu))
            for wu_i, wu in enumerate(work_units)
        ]

        try:
          for coro in asyncio.as_completed(pending):
            if not engine.running:
                break

            wu_idx, job_results = await coro

            for idx, arch_result in job_results:
                if not engine.running:
                    break

                job = urban_jobs[idx]
                name = job["name"]
                completed_design_jobs_count += 1
                logger.info(
                    "Streaming result %d/%d: %s — type=%s keys=%s",
                    completed_design_jobs_count, len(urban_jobs), name,
                    type(arch_result).__name__,
                    sorted(arch_result.keys()) if isinstance(arch_result, dict) else "N/A",
                )

                # --- Per-structure error recovery: skip failures, pause only if excessive ---
                if isinstance(arch_result, AgentGenerationError):
                    consecutive_failures += 1
                    skipped += 1
                    logger.warning(
                        "Urbanista failed for %s (%s): %s — skipping",
                        name, arch_result.pause_reason, (arch_result.pause_detail or "")[:200],
                    )
                    await engine._chat("urbanista", "info", f"Skipped {name} — design failed ({arch_result.pause_reason}). Continuing.")
                    # Broadcast skipped building so the client knows
                    await engine.broadcast({
                        "type": "building_skipped",
                        "name": name,
                        "building_type": job["btype"],
                        "reason": arch_result.pause_reason,
                        "detail": (arch_result.pause_detail or "")[:400],
                        "tiles": [{"x": t["x"], "y": t["y"]} for t in job["tiles"]],
                        "district": district_key,
                        "skipped_count": skipped,
                    })
                    if consecutive_failures >= max_consecutive_failures:
                        for t in pending:
                            if not t.done():
                                t.cancel()
                        await engine._pause_for_api_issue(
                            arch_result.pause_reason,
                            f"{max_consecutive_failures} consecutive failures — last: {arch_result.pause_detail or ''}",
                            "urbanista",
                        )
                        return False
                    continue
                if isinstance(arch_result, BaseException):
                    for t in pending:
                        if not t.done():
                            t.cancel()
                    raise arch_result
                consecutive_failures = 0

                tiles = job["tiles"]
                hist_note = job["hist_note"]
                hist_result = job["hist_result"]
                anchor_x = job["anchor_x"]
                anchor_y = job["anchor_y"]

                try:
                    arch_result = run_traced_urbanista_arch_sanitize_and_validate(
                        arch_result,
                        district_key=district_key,
                        structure_name=name,
                    )
                except UrbanistaValidationError as err:
                    skipped += 1
                    logger.warning("Urbanista validation failed for %s: %s — skipping", name, err)
                    await engine._chat("urbanista", "info", f"Skipped {name} — validation error. Continuing.")
                    continue

                # ── Geometry collision check — detect and fix overlapping components ──
                anchor_tile = None
                for td in arch_result.get("tiles", []):
                    if isinstance(td, dict) and td.get("spec") and isinstance(td["spec"].get("components"), list):
                        anchor_tile = td
                        break
                if anchor_tile and job["btype"] not in engine._open_terrain_types_set:
                    _fs = engine.system_configuration.footprint_width_depth_scale_factor
                    fp_w = round((max(t["x"] for t in tiles) - min(t["x"] for t in tiles) + 1) * _fs, 2)
                    fp_d = round((max(t["y"] for t in tiles) - min(t["y"] for t in tiles) + 1) * _fs, 2)
                    collisions = check_component_collisions(anchor_tile["spec"], fp_w, fp_d)
                    if collisions:
                        pruned_n = try_prune_colliding_decorative_components(
                            anchor_tile["spec"],
                            fp_w,
                            fp_d,
                            max_removals=8,
                        )
                        if pruned_n:
                            collisions = check_component_collisions(anchor_tile["spec"], fp_w, fp_d)
                            logger.info(
                                "Geometry prune for %s: removed %d decorative component(s); %d issue(s) remain",
                                name, pruned_n, len(collisions),
                            )
                    if collisions:
                        max_entries = (
                            engine.system_configuration.urbanista_geometry_collision_report_max_entries
                        )
                        collision_report = "\n".join(collisions[:max_entries])
                        logger.warning("Geometry collisions for %s: %d issues", name, len(collisions))
                        await engine._chat("urbanista", "info",
                            f"Geometry issues in {name}: {len(collisions)} collision(s). Requesting fix...")
                        fix_prompt = (
                            f"GEOMETRY FIX for {name} ({fp_w}x{fp_d} footprint):\n"
                            f"{collision_report}\n\n"
                            f"RULES:\n"
                            f"- foundation (podium) is the base — structural sits ABOVE it\n"
                            f"- Do NOT put colonnade + walls + block all as structural — they overlap!\n"
                            f"- colonnade wraps the EXTERIOR; walls/cella are INSIDE (use infill role)\n"
                            f"- Roof sits above the highest structural component\n"
                            f"- decorative (doors, pilasters) go on the facade, NOT filling the volume\n\n"
                            f"Return the CORRECTED JSON. Same format, same design, fixed geometry."
                        )
                        try:
                            fixed = await asyncio.wait_for(
                                engine.urbanista.generate(fix_prompt),
                                timeout=engine.system_configuration.llm.agent_timeout_long_seconds,
                            )
                            fixed = run_traced_urbanista_arch_sanitize_and_validate(
                                fixed,
                                district_key=district_key,
                                structure_name=name + "_geometry_fix",
                            )
                            fixed_anchor = None
                            for td in fixed.get("tiles", []):
                                if isinstance(td, dict) and td.get("spec") and isinstance(td["spec"].get("components"), list):
                                    fixed_anchor = td
                                    break
                            if fixed_anchor:
                                new_collisions = check_component_collisions(fixed_anchor["spec"], fp_w, fp_d)
                                if len(new_collisions) < len(collisions):
                                    arch_result = fixed
                                    logger.info("Geometry fix for %s: %d->%d collisions", name, len(collisions), len(new_collisions))
                                    await engine._chat("urbanista", "info",
                                        f"Fixed {name}: {len(collisions)}->{len(new_collisions)} collision(s)")
                                else:
                                    logger.info("Geometry fix for %s did not improve (%d->%d) — using original", name, len(collisions), len(new_collisions))
                            else:
                                arch_result = fixed
                        except asyncio.TimeoutError:
                            logger.warning("Geometry fix for %s timed out (5min) — using original", name)
                        except Exception as fix_err:
                            logger.warning("Geometry fix failed for %s: %s — using original", name, fix_err)

                await engine._set_status("urbanista", "speaking", detail="Sharing design commentary.")
                commentary = arch_result.get("commentary", "Design ready.")
                cmax = engine.system_configuration.urbanista_commentary_display_max_chars
                if len(commentary) > cmax:
                    commentary = commentary[: max(0, cmax - 3)] + "..."
                await engine._chat("urbanista", "design", commentary)
                await engine._set_status(
                    "urbanista",
                    "thinking" if completed_design_jobs_count < len(urban_jobs) else "idle",
                    detail=(
                        f"Progress {completed_design_jobs_count}/{len(urban_jobs)} — {name} design finished; continuing…"
                        if completed_design_jobs_count < len(urban_jobs)
                        else None
                    ),
                )

                # Place tiles
                final_tiles = validate_urbanista_tiles(arch_result.get("tiles", []))
                if not final_tiles:
                    skipped += 1
                    logger.warning("Urbanista returned no in-bounds tiles for %s — skipping", name)
                    await engine._chat("urbanista", "info", f"Skipped {name} — no valid tiles. Continuing.")
                    continue

                # Auto-fill secondary tiles
                survey_coords = {(t["x"], t["y"]) for t in tiles}
                returned_coords = {(td.get("x"), td.get("y")) for td in final_tiles}
                missing_coords = survey_coords - returned_coords
                if missing_coords and len(final_tiles) >= 1:
                    template_td = final_tiles[0]
                    for td in final_tiles:
                        if td.get("x") == anchor_x and td.get("y") == anchor_y:
                            template_td = td
                            break
                    is_terrain = job["btype"] in engine._open_terrain_types_set
                    for (mx, my) in missing_coords:
                        if is_terrain:
                            fill_elev = (
                                round(engine.blueprint.elevation_at(mx, my), 2)
                                if engine.blueprint and engine.blueprint.hills
                                else avg_elevation
                            )
                            sec_tile = {
                                "x": mx, "y": my,
                                "terrain": job["btype"],
                                "building_name": template_td.get("building_name", name),
                                "building_type": job["btype"],
                                "description": f"Part of {name}",
                                "elevation": fill_elev,
                            }
                            t_spec = template_td.get("spec")
                            if t_spec and isinstance(t_spec, dict):
                                sec_tile["spec"] = {k: v for k, v in t_spec.items() if k != "anchor"}
                            if template_td.get("color"):
                                sec_tile["color"] = template_td["color"]
                        else:
                            sec_tile = {
                                "x": mx, "y": my,
                                "terrain": "building",
                                "building_name": template_td.get("building_name", name),
                                "building_type": template_td.get("building_type", job["btype"]),
                                "description": f"Part of {name}",
                                "elevation": avg_elevation,
                                "spec": {"anchor": {"x": anchor_x, "y": anchor_y}},
                            }
                        final_tiles.append(sec_tile)
                    logger.info("Auto-filled %d secondary tiles for %s (%s)", len(missing_coords), name, job["btype"])

                # Inject anchors for multi-tile buildings
                if len(tiles) > 1 and job["btype"] not in engine._open_terrain_types_set:
                    for td in final_tiles:
                        if not td.get("spec"):
                            td["spec"] = {}
                        if not td["spec"].get("anchor"):
                            td["spec"]["anchor"] = {"x": anchor_x, "y": anchor_y}

                if job["btype"] not in engine._open_terrain_types_set:
                    _fs_fallback = engine.system_configuration.footprint_width_depth_scale_factor
                    fb_w = round((max(t["x"] for t in tiles) - min(t["x"] for t in tiles) + 1) * _fs_fallback, 2)
                    fb_d = round((max(t["y"] for t in tiles) - min(t["y"] for t in tiles) + 1) * _fs_fallback, 2)
                    fallback_components = get_fallback_spec_components_for_building_type(
                        str(job["btype"]),
                        fb_w,
                        fb_d,
                        city_loc=city_loc,
                    )
                    if fallback_components:
                        for td in final_tiles:
                            btt = str(td.get("building_type") or job["btype"] or "")
                            if btt in engine._open_terrain_types_set:
                                continue
                            spec_existing = td.get("spec")
                            if isinstance(spec_existing, dict) and spec_existing.get("components"):
                                continue
                            td["spec"] = {
                                "components": copy.deepcopy(fallback_components),
                                "anchor": {"x": anchor_x, "y": anchor_y},
                            }
                        logger.info("Applied golden fallback components for %s (%s)", name, job["btype"])

                # Nest grammar data into spec
                for td in final_tiles:
                    g = td.pop("grammar", None)
                    gp = td.pop("grammar_params", None)
                    if g:
                        if not td.get("spec"):
                            td["spec"] = {}
                        td["spec"]["grammar"] = g
                        if gp:
                            td["spec"]["params"] = gp

                urban_tile_triples: list[tuple[int | float, int | float, dict]] = []
                district_elev = district.get("elevation", 0.0)
                for td in final_tiles:
                    x, y = td.get("x"), td.get("y")
                    if x is not None and y is not None:
                        td_commit = dict(td)
                        if engine.blueprint and engine.blueprint.hills:
                            td_commit["elevation"] = round(
                                engine.blueprint.elevation_at(int(x), int(y)), 3
                            )
                        elif "elevation" not in td_commit or td_commit["elevation"] is None:
                            td_commit["elevation"] = district_elev
                        td_commit["period"] = district.get("period", "")
                        td_commit["placed_by"] = "faber"
                        td_commit["historical_note"] = hist_result.get(
                            "historical_note", hist_note
                        )
                        urban_tile_triples.append((x, y, td_commit))
                placement_ctx: PlacementValidationContext | None = None
                fallback_cells: tuple[tuple[int, int], ...] = ()
                max_candidate_tries = 0
                if engine.blueprint is not None and job["btype"] not in engine._open_terrain_types_set:
                    placement_ctx = PlacementValidationContext(
                        anchor_x=int(anchor_x),
                        anchor_y=int(anchor_y),
                        building_name=name,
                        building_type=job["btype"],
                        district_key=district_key,
                    )
                    raw_candidates = generate_valid_candidates(
                        engine.world,
                        engine.blueprint,
                        district_key,
                        system_configuration=engine.system_configuration,
                    )
                    fallback_cells = tuple(raw_candidates)
                    max_candidate_tries = min(32, max(1, len(fallback_cells) + 1))
                try:
                    urban_batch = apply_tile_placements(
                        engine.world,
                        urban_tile_triples,
                        system_configuration=engine.system_configuration,
                        blueprint=engine.blueprint if placement_ctx is not None else None,
                        placement_context=placement_ctx,
                        placement_fallback_candidates=fallback_cells if placement_ctx is not None else None,
                        placement_max_candidate_tries=max_candidate_tries,
                    )
                except PlacementError as pe:
                    skipped += 1
                    logger.warning(
                        "Placement rejected for %s (%s): %s",
                        name,
                        job["btype"],
                        pe,
                    )
                    trace_event(
                        "engine",
                        "placement_exhausted",
                        structure=name,
                        district=district_key,
                        detail=str(pe)[:400],
                    )
                    await engine._chat(
                        "urbanista",
                        "info",
                        f"Skipped {name} — could not place on valid terrain ({pe}). Continuing.",
                    )
                    continue
                if urban_batch.place_tile_rejections_count:
                    logger.warning(
                        "Urbanista batch dropped %s tiles for %r (%s) — partial commit",
                        urban_batch.place_tile_rejections_count,
                        name,
                        job["btype"],
                    )
                placed = urban_batch.placed_tile_dicts

                # Record design in style memory
                for td_placed in placed:
                    if td_placed.get("spec"):
                        engine.style_memory.record_design(td_placed.get("spec", {}))

                logger.info("Placing %d tiles for %s", len(placed), name)
                if placed:
                    await engine.broadcast({
                        "type": "tile_update", "tiles": placed,
                        "turn": engine.world.turn,
                        "period": district.get("period", ""),
                        "year": district.get("year", ""),
                    })
                    await engine.broadcast({
                        "type": "build_progress",
                        "structure": name,
                        "building_type": job["btype"],
                        "done": completed_design_jobs_count,
                        "total": len(urban_jobs),
                        "district": district_key,
                    })

                engine.world.turn += 1
                await engine.tasks.persist_progress_after_structure()
                await asyncio.sleep(0.05)  # Brief yield for UI rendering

        finally:
            # Cancel any still-running tasks (pause, error, or normal completion)
            for t in pending:
                if not t.done():
                    t.cancel()
            # Await cancellation to prevent "task was destroyed" warnings
            for t in pending:
                if t.cancelled() or t.done():
                    continue
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

        if not engine.running:
            return False
        await engine._set_status("urbanista", "idle")

    if engine.tasks.get_structures_since_save() > 0:
        await engine._save_state_thread(flush_mode="incremental")
        engine.tasks.reset_structure_save_throttle_counter()

    if skipped:
        logger.warning("District %s: %d/%d structures skipped due to errors", district_key, skipped, len(urban_jobs))
        await engine._chat(
            "urbanista", "info",
            f"District complete — {len(urban_jobs) - skipped} placed, {skipped} skipped due to errors.",
        )

    return True
