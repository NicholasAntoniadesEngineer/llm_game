"""Generators — extracted generation/discovery methods from BuildEngine."""

import asyncio
import json
import logging
import time

from core.errors import AgentGenerationError
from core.run_log import trace_event
from core.fingerprint import district_survey_key, ensure_district_ids
from core.persistence import (
    load_districts_cache,
    load_surveys_cache,
    save_blueprint,
    save_districts_cache,
    save_state,
    save_surveys_cache,
)
from orchestration.district_inference import infer_district_style_string
from orchestration.engine_ports import GeneratorsHostPort
from orchestration.spatial import enforce_spacing, get_district_spacing, occupancy_summary_for_survey
from world.road_connectivity import ensure_road_connectivity_in_master_plan

logger = logging.getLogger("eternal.generators")


class Generators:
    """Holds generation/discovery methods extracted from BuildEngine.

    Uses a narrow ``GeneratorsHostPort`` (implemented by ``BuildEngine``) instead of a generic engine reference.
    """

    def __init__(self, host: GeneratorsHostPort):
        self._host = host

    def _spacing_enforced_plan(self, master_plan: list, min_gap: int) -> list:
        system_configuration = self._host.system_configuration
        return enforce_spacing(
            master_plan,
            min_gap=min_gap,
            system_configuration=system_configuration,
            world_grid_width_tiles=system_configuration.grid.world_grid_width,
            world_grid_height_tiles=system_configuration.grid.world_grid_height,
            spatial_optimal_shift_step_tiles=system_configuration.spatial_optimal_shift_step_tiles,
        )

    def _apply_road_connectivity(self, master_plan: list, region: dict) -> list:
        cfg = self._host.system_configuration
        return ensure_road_connectivity_in_master_plan(
            master_plan,
            region,
            road_bridge_default_elevation=cfg.road_bridge_default_elevation,
            world_grid_width_tiles=cfg.grid.world_grid_width,
            world_grid_height_tiles=cfg.grid.world_grid_height,
        )

    # ── Convenience delegates ──────────────────────────────────────────

    @property
    def world(self):
        return self._host.world

    @property
    def broadcast(self):
        return self._host.broadcast

    @property
    def districts(self):
        return self._host.districts

    @districts.setter
    def districts(self, value):
        self._host.districts = value

    @property
    def running(self):
        return self._host.tasks.running

    @property
    def planner_skeleton(self):
        return self._host.planner_skeleton

    @property
    def planner_refine(self):
        return self._host.planner_refine

    @property
    def surveyor(self):
        return self._host.surveyor

    @property
    def urbanista(self):
        return self._host.urbanista

    @property
    def _source_policy(self):
        return self._host._source_policy

    @property
    def _survey_semaphore(self):
        return self._host.tasks._survey_semaphore

    @property
    def _fused_seed_master_plan(self):
        return self._host._fused_seed_master_plan

    @_fused_seed_master_plan.setter
    def _fused_seed_master_plan(self, value):
        self._host._fused_seed_master_plan = value

    @property
    def _survey_cache_lock(self):
        return self._host._survey_cache_lock

    @property
    def _district_scenery_summaries(self):
        return self._host._district_scenery_summaries

    @property
    def _district_palettes(self):
        return self._host._district_palettes

    async def _chat(self, *args, **kwargs):
        return await self._host._chat(*args, **kwargs)

    async def _set_status(self, *args, **kwargs):
        return await self._host._set_status(*args, **kwargs)

    async def _pause_for_api_issue(self, *args, **kwargs):
        return await self._host._pause_for_api_issue(*args, **kwargs)

    def _apply_master_plan_validation(self, *args, **kwargs):
        return self._host._apply_master_plan_validation(*args, **kwargs)

    def _district_style(self, district: dict) -> str | None:
        """Extract district style from blueprint or infer from description."""
        return infer_district_style_string(
            district.get("description", ""),
            district_name=str(district.get("name", "")),
            blueprint=self._host.blueprint,
        )

    # ── Extracted methods ──────────────────────────────────────────────

    async def discover_districts(self) -> bool:
        """Load cached layout, or run phase-1 skeleton planner then background map refine."""
        scen = self._host.scenario
        if not isinstance(scen, dict):
            logger.error("discover_districts: RunSession.scenario is missing or not a dict")
            return False
        run_fp = self._host.run_fingerprint
        cached = load_districts_cache(
            expected_run_fingerprint=run_fp,
            system_configuration=self._host.system_configuration,
        )
        if cached:
            self.districts, map_desc = cached
            ensure_district_ids(self.districts)
            self._fused_seed_master_plan = None
            logger.info(f"Using cached districts: {len(self.districts)}")
            trace_event("discovery", "Using cached districts layout", districts=len(self.districts))
            self._host.update_trace_snapshot(phase="discover_cached", districts=len(self.districts))
            await self._chat("cartographus", "research",
                f"Using cached survey of {scen['location']} — {len(self.districts)} districts mapped.")
            if map_desc:
                await self.broadcast({"type": "map_description", "description": map_desc})
            asyncio.create_task(self.find_map_image())
            return True

        trace_event("discovery", "No district cache — running skeleton planner (long-running)", location=scen.get("location", ""))
        self._host.update_trace_snapshot(phase="skeleton_planner", step="before_generate")

        await self.broadcast({
            "type": "loading",
            "agent": "cartographus",
            "message": f"Mapping districts for {scen['location']}...",
        })
        await self._chat("cartographus", "research",
            f"Phase 1 — district skeleton for {scen['location']} ({scen['period']}). "
            f"A detailed map narrative will follow in the background while we build.")
        await self._set_status(
            "cartographus",
            "thinking",
            detail="Mapping districts — first AI pass can take several minutes.",
        )
        scfg = self._host.system_configuration
        gw, gh = scfg.grid.world_grid_width, scfg.grid.world_grid_height
        mpt = scfg.grid.world_scale_meters_per_tile
        plan_prompt = (
            f"Research and map the city of {scen['location']} during {scen['period']}.\n"
            f"Time span: {scen['year_start']} to {scen['year_end']}.\n"
            f"Ruler context: {scen['ruler']}.\n\n"
            f"ABOUT THIS CITY:\n{scen.get('description', '')}\n"
            f"Key features: {scen.get('features', '')}\n"
            f"Layout notes: {scen.get('grid_note', '')}\n\n"
            f"Grid size: {gw}x{gh} (each tile ≈ {mpt} meters = {gw * mpt}m x {gh * mpt}m).\n\n"
            f"RESEARCH DEEPLY: What districts existed at this exact time? Which buildings had been built? "
            f"Which hadn't been constructed yet? What was the terrain like?\n\n"
            f"For each district: real name, function, footprint in tile coordinates, named buildings that existed, "
            f"roads and natural features.\n\n"
            f"IMPORTANT: List at most {scfg.grid.maximum_buildings_per_district_count} buildings per district. "
            f"Choose the most significant and visually distinctive structures. Include roads and open spaces between them.\n\n"
            f"Be historically precise: only include structures that existed at this time."
        )
        for attempt in range(2):
            try:
                trace_event("discovery", "Calling planner_skeleton.generate() for district skeleton", attempt=attempt)
                self._host.update_trace_snapshot(phase="skeleton_planner", step="generate_in_progress", attempt=attempt)
                result = await asyncio.wait_for(
                    self.planner_skeleton.generate(plan_prompt),
                    timeout=scfg.llm.agent_timeout_long_seconds,
                )
                break
            except (AgentGenerationError, asyncio.TimeoutError) as err:
                if int(scfg.skeleton_cli_kill_subprocess_on_timeout) == 1:
                    import subprocess as _sp

                    await asyncio.to_thread(
                        _sp.run,
                        ["pkill", "-f", r"claude.*--print.*--system-prompt"],
                        capture_output=True,
                    )
                if attempt == 0:
                    reason = "network" if isinstance(err, asyncio.TimeoutError) else err.pause_reason
                    retriable = reason in ("bad_model_output", "api_error", "network")
                    if retriable:
                        logger.warning("Skeleton planner failed (%s), retrying once", reason)
                        await asyncio.sleep(scfg.skeleton_planner_inter_retry_wait_seconds)
                        continue
                # Second attempt or non-retriable
                await self._set_status("cartographus", "idle")
                if isinstance(err, asyncio.TimeoutError):
                    await self._pause_for_api_issue(
                        "network",
                        (
                            "Skeleton planner timed out after "
                            f"{scfg.llm.agent_timeout_long_seconds}s wait limit."
                        ),
                        "cartographus",
                    )
                else:
                    await self._pause_for_api_issue(err.pause_reason, err.pause_detail, "cartographus")
                return False

        await self._set_status("cartographus", "speaking", detail="Sharing district map narrative.")
        trace_event(
            "discovery",
            "Skeleton planner returned",
            districts_count=len(result.get("districts") or []),
            keys=sorted(result.keys()),
        )
        self._host.update_trace_snapshot(phase="skeleton_planner", step="after_generate", districts=len(result.get("districts") or []))
        logger.info(
            "Skeleton planner result keys=%s districts_count=%s commentary_len=%s",
            sorted(result.keys()),
            len(result.get("districts") or []),
            len(result.get("commentary") or ""),
        )
        await self._chat("cartographus", "research", result.get("commentary", "District layout established."))
        await self._set_status("cartographus", "idle")

        self.districts = result.get("districts", [])
        self._host._last_skeleton_result = result
        max_dist = scfg.grid.maximum_districts_count
        if len(self.districts) > max_dist:
            logger.warning("District count %d exceeds cap of %d — truncating", len(self.districts), max_dist)
            self.districts = self.districts[:max_dist]
        logger.info(f"Skeleton: {len(self.districts)} districts")
        if self.districts:
            from core.run_log import log_event
            log_event("discovery", f"Mapped {len(self.districts)} districts",
                      districts=", ".join(d.get("name", "?") for d in self.districts))

        if not self.districts:
            # Log the full result for debugging
            dbg_max = scfg.skeleton_planner_debug_json_max_chars
            logger.error(
                "Skeleton planner returned no districts. Full result: %s",
                json.dumps(result, indent=2)[:dbg_max],
            )
            await self._pause_for_api_issue(
                "bad_model_output",
                "Skeleton planner returned no districts (empty or missing `districts` array). "
                "The model may have returned prose instead of the required JSON schema. "
                f"Result keys: {sorted(result.keys())}. Check the server log for the full output.",
                "cartographus",
            )
            return False

        ensure_district_ids(self.districts)

        seed = result.get("seed_master_plan")
        if isinstance(seed, list) and len(seed) > 0:
            self._fused_seed_master_plan = seed
            logger.info("Fused seed_master_plan from skeleton — skipping survey API for first district if valid.")
        else:
            self._fused_seed_master_plan = None

        save_districts_cache(
            self.districts,
            "",
            run_fingerprint=self._host.run_fingerprint,
            system_configuration=self._host.system_configuration,
        )
        self._host.tasks.start_map_refine_background(self.refine_map_description_background())
        logger.info("Map refine started immediately after skeleton (non-blocking).")
        asyncio.create_task(self.find_map_image())
        return True

    async def expand_city(self) -> bool:
        """Discover new districts at the city edges. Returns True if new districts found."""
        scenario = self._host.scenario
        if not scenario:
            return False

        trace_event("expansion", "expand_city() start", generation=self._host.generation, districts=len(self.districts))
        self._host.update_trace_snapshot(phase="expand_city", step="start")

        city_name = scenario.get("location", "Unknown")
        period = scenario.get("period", "")
        year = scenario.get("focus_year", 0)

        # Build existing districts summary
        existing = []
        existing_regions = []
        for d in self.districts:
            r = d.get("region", {})
            existing.append(f"  - {d['name']}: ({r.get('x1',0)},{r.get('y1',0)}) to ({r.get('x2',0)},{r.get('y2',0)})")
            existing_regions.append(r)
        existing_str = "\n".join(existing) if existing else "  (none)"

        occupied_tiles: set[tuple[int, int]] = set()
        for ck in self.world.chunk_keys_with_tiles():
            occupied_tiles |= self.world.chunk_tile_coords(ck)

        # Build geography context from blueprint (if available)
        bp = self._host.blueprint
        geo_context = ""
        mat_context = ""
        if bp:
            geo = bp.get_geography_context()
            if geo:
                geo_context = f"GEOGRAPHY: {geo}"
            mat = bp.get_material_palette_context()
            if mat:
                mat_context = f"MATERIALS: {mat}"

        # Compute direction hint: prefer expanding toward least-developed edges
        direction_hint = self._compute_expansion_direction_hint(existing_regions)

        from prompts import load_prompt
        w = self.world
        prompt = load_prompt("cartographus_expand",
            SOURCE_POLICY=self._source_policy,
            CITY_NAME=city_name,
            GENERATION=self._host.generation,
            MIN_X=w.min_x, MAX_X=w.max_x,
            MIN_Y=w.min_y, MAX_Y=w.max_y,
            WIDTH=w.width, HEIGHT=w.height,
            PERIOD=period,
            YEAR=year,
            EXISTING_DISTRICTS=existing_str,
            GEO_CONTEXT=geo_context,
            MAT_CONTEXT=mat_context,
            DIRECTION_HINT=direction_hint,
        )

        await self._set_status(
            "cartographus",
            "thinking",
            detail="Discovering new districts at the city edge…",
        )
        await self._chat("cartographus", "info", f"Expanding city — generation {self._host.generation + 1}...")

        # Use skeleton planner agent for expansion (same LLM routing)
        try:
            trace_event("expansion", "Calling planner_skeleton.generate() for expansion")
            self._host.update_trace_snapshot(phase="expand_city", step="planner_generate")
            result = await self.planner_skeleton.generate(prompt)
        except AgentGenerationError as err:
            trace_event(
                "expansion",
                "expand_city planner raised AgentGenerationError",
                pause_reason=getattr(err, "pause_reason", ""),
            )
            await self._chat("cartographus", "warning", f"Expansion failed: {err}")
            await self._set_status("cartographus", "idle")
            return False

        new_districts = result.get("districts", [])
        trace_event("expansion", "Expansion planner returned", proposed=len(new_districts))
        if not new_districts:
            await self._chat("cartographus", "info", "No expansion districts found.")
            await self._set_status("cartographus", "idle")
            self._host.update_trace_snapshot(phase="expand_city", step="no_new_districts")
            return False

        # Validate new districts: no overlap with existing, no water regions
        validated = []
        for nd in new_districts:
            r = nd.get("region", {})
            x1, y1 = r.get("x1", 0), r.get("y1", 0)
            x2, y2 = r.get("x2", 0), r.get("y2", 0)

            # Check overlap with existing district regions
            overlaps = False
            for er in existing_regions:
                if (x1 <= er.get("x2", 0) and x2 >= er.get("x1", 0) and
                        y1 <= er.get("y2", 0) and y2 >= er.get("y1", 0)):
                    logger.warning("Expansion district %s overlaps existing district region — skipping",
                                   nd.get("name", "?"))
                    overlaps = True
                    break
            if overlaps:
                continue

            # Check overlap with already-placed tiles (> 25% overlap means bad placement)
            region_tiles = set()
            for rx in range(x1, x2 + 1):
                for ry in range(y1, y2 + 1):
                    region_tiles.add((rx, ry))
            overlap_count = len(region_tiles & occupied_tiles)
            if region_tiles and (overlap_count / len(region_tiles)) > 0.25:
                logger.warning("Expansion district %s has >25%% tile overlap (%d/%d) — skipping",
                               nd.get("name", "?"), overlap_count, len(region_tiles))
                continue

            # Check if region is mostly water (from blueprint)
            if bp and bp.is_water_region(x1, y1, x2, y2, threshold=0.5):
                logger.warning("Expansion district %s is mostly water — skipping",
                               nd.get("name", "?"))
                continue

            validated.append(nd)

        if not validated:
            await self._chat("cartographus", "info", "All proposed expansion districts failed validation.")
            await self._set_status("cartographus", "idle")
            return False

        for nd in validated:
            nd["expansion_generation"] = self._host.generation + 1
            self.districts.append(nd)

        ensure_district_ids(self.districts)

        await self._chat("cartographus", "info",
                         f"Discovered {len(validated)} new districts: "
                         + ", ".join(d['name'] for d in validated))
        await self._set_status("cartographus", "idle")

        # Save expanded districts cache
        await asyncio.to_thread(
            save_districts_cache,
            self.districts,
            "",
            run_fingerprint=self._host.run_fingerprint,
            system_configuration=self._host.system_configuration,
        )
        # Align index.json with expanded district list (crash safety before new tiles)
        await asyncio.to_thread(
            save_state,
            self._host.world,
            self._host.chat_history,
            self._host.district_index,
            self._host.districts,
            self._host.generation,
            scenario=self._host.scenario,
            system_configuration=self._host.system_configuration,
            flush_mode="full",
            build_wave_phase=self._host.build_wave_phase,
            district_build_cursor=self._host.district_build_cursor,
        )

        self._host.build_wave_phase = "landmark"
        self._host.district_build_cursor = self._host.district_index

        # Start surveys for new districts
        self._host.tasks.start_survey_tasks_from_index(self._host.district_index, len(self.districts))

        logger.info(f"Expansion: +{len(validated)} districts, total now {len(self.districts)}")
        trace_event("expansion", "expand_city() validated new districts", added=len(validated), total_districts=len(self.districts))
        self._host.update_trace_snapshot(phase="expand_city", step="done", added=len(validated))

        # Send updated world bounds so client can expand its grid
        await self.broadcast(self.world.to_dict())

        # Broadcast updated terrain_data if blueprint exists so 3D terrain mesh
        # extends to cover the new district regions
        if bp and (bp.hills or bp.water):
            # Apply elevation to any newly created tiles in expanded area
            elev_count = bp.apply_elevation_to_world(
                self.world, system_configuration=self._host.system_configuration
            )
            if elev_count:
                logger.info("Expansion: applied elevation to %d new tiles", elev_count)
            await self.broadcast({
                "type": "terrain_data",
                "hills": bp.hills,
                "water": bp.water,
                "roads": bp.roads,
                "max_gradient": self._host.system_configuration.terrain.maximum_gradient_value,
                "gradient_iterations": self._host.system_configuration.terrain.gradient_iterations_count,
            })
            await asyncio.to_thread(
                save_blueprint,
                bp.to_dict(),
                system_configuration=self._host.system_configuration,
            )

        return True

    def _compute_expansion_direction_hint(self, existing_regions: list[dict]) -> str:
        """Compute which direction has least development for expansion guidance.

        Returns a compact hint string like 'EXPANSION HINT: Least developed: N,E'
        or empty string if no clear preference.
        """
        if not existing_regions:
            return ""

        w = self.world
        if not w.tiles:
            return ""

        cx = (w.min_x + w.max_x) / 2
        cy = (w.min_y + w.max_y) / 2

        # Count districts in each quadrant relative to center
        direction_counts = {"N": 0, "S": 0, "E": 0, "W": 0}
        for r in existing_regions:
            mid_x = (r.get("x1", 0) + r.get("x2", 0)) / 2
            mid_y = (r.get("y1", 0) + r.get("y2", 0)) / 2
            if mid_y < cy:
                direction_counts["N"] += 1
            else:
                direction_counts["S"] += 1
            if mid_x > cx:
                direction_counts["E"] += 1
            else:
                direction_counts["W"] += 1

        if not any(direction_counts.values()):
            return ""

        min_count = min(direction_counts.values())
        least_developed = [d for d, c in direction_counts.items() if c == min_count]

        if len(least_developed) == 4:
            return ""  # All equally developed — no hint

        return f"EXPANSION HINT: Least developed direction(s): {','.join(least_developed)}. Prefer expanding this way."

    async def refine_map_description_background(self):
        """Phase-2 planner: long map_description while districts are being surveyed/built."""
        try:
            if not self.running:
                return
            scen = self._host.scenario
            if not isinstance(scen, dict):
                return
            skeleton_payload = json.dumps(
                {"districts": self.districts, "city": scen.get("location", "")},
                indent=2,
            )
            instruction = (
                f"City: {scen['location']}, {scen['period']}.\n"
                f"Year span: {scen['year_start']} — {scen['year_end']}.\n"
                f"Ruler context: {scen['ruler']}.\n\n"
                f"FIXED district skeleton (names and regions are authoritative):\n{skeleton_payload}\n\n"
                f"Write map_description as a very long, multi-paragraph archaeologist's overview of the whole city at this time: "
                f"terrain, hydrology, walls, arteries, landmark sightlines, district character, sensory texture, and what distinguishes "
                f"this decade from earlier/later phases. Avoid short summaries."
            )
            result = await self.planner_refine.generate(instruction)
            if not self.running:
                return
            map_desc = (result.get("map_description") or "").strip()
            if map_desc:
                await self.broadcast({"type": "map_description", "description": map_desc})
                await asyncio.to_thread(
                    save_districts_cache,
                    self.districts,
                    map_desc,
                    run_fingerprint=self._host.run_fingerprint,
                    system_configuration=self._host.system_configuration,
                )
                logger.info("Background map_description saved (%s chars)", len(map_desc))
            commentary = result.get("commentary", "")
            if commentary:
                await self._chat("cartographus", "research", commentary)
        except asyncio.CancelledError:
            logger.info("Map refine cancelled")
            raise
        except AgentGenerationError as err:
            logger.warning("Map refine failed (non-fatal): %s", err)
        except Exception as e:
            logger.warning("Map refine failed (non-fatal): %s", e)

    async def survey_work_item(self, district_index: int) -> list:
        """Resolve master_plan for one district (cache, fused seed, or survey API)."""
        district = self.districts[district_index]
        district_key = district.get("name", "unknown")
        survey_sid = district_survey_key(district)

        cached_plan = None
        async with self._survey_cache_lock:
            if self._host._survey_cache is None:
                self._host._survey_cache = load_surveys_cache(
                    expected_run_fingerprint=self._host.run_fingerprint,
                    system_configuration=self._host.system_configuration,
                )
            if survey_sid in self._host._survey_cache:
                cached_plan = self._host._survey_cache[survey_sid]
        if cached_plan is not None:
            if isinstance(cached_plan, list) and len(cached_plan) > 0:
                if all(isinstance(s, dict) and "name" in s for s in cached_plan):
                    expected = len(district.get("buildings", []))
                    if expected > 3 and len(cached_plan) < 3:
                        logger.warning(
                            "Survey cache for %s has only %d structures but district lists %d buildings — "
                            "keeping cache (clear surveys cache manually if a full re-survey is required)",
                            district_key,
                            len(cached_plan),
                            expected,
                        )
                    logger.info("Survey cache hit: %s (%d structures)", district_key, len(cached_plan))
                    await self._chat(
                        "cartographus",
                        "survey",
                        f"Using cached survey of {district_key} ({len(cached_plan)} structures).",
                    )
                    return cached_plan
            logger.warning("Survey cache invalid for %s — re-surveying", district_key)

        if district_index == 0 and self._fused_seed_master_plan:
            raw_seed = self._fused_seed_master_plan
            self._fused_seed_master_plan = None
            master_plan = self.validate_master_plan_structures(raw_seed)
            if master_plan:
                gap = get_district_spacing(
                    self._district_style(district),
                    system_configuration=self._host.system_configuration,
                )
                master_plan = self._spacing_enforced_plan(master_plan, min_gap=gap)
                region = district.get("region", {"x1": 0, "y1": 0, "x2": 10, "y2": 10})
                master_plan = self._apply_road_connectivity(master_plan, region)
                master_plan = self._apply_master_plan_validation(
                    master_plan, f"Fused seed {district_key!r}"
                )
                async with self._survey_cache_lock:
                    self._host._survey_cache[survey_sid] = master_plan
                    await asyncio.to_thread(
                        save_surveys_cache,
                        self._host._survey_cache,
                        run_fingerprint=self._host.run_fingerprint,
                        system_configuration=self._host.system_configuration,
                    )
                logger.info("Using fused seed_master_plan for %s (%d structures)", district_key, len(master_plan))
                return master_plan
            logger.warning("Fused seed_master_plan invalid — running full survey for first district.")

        await self._set_status(
            "cartographus",
            "thinking",
            detail=f"Surveying {district_key} — listing structures…",
        )
        try:
            master_plan = await self.survey_district_with_chunking(district)
        except AgentGenerationError:
            await self._set_status("cartographus", "idle")
            raise

        await self._set_status("cartographus", "speaking", detail="Summarizing survey results.")
        await self._chat("cartographus", "survey", f"Survey complete for {district_key}: {len(master_plan)} structures.")
        await self._set_status("cartographus", "idle")

        async with self._survey_cache_lock:
            self._host._survey_cache[survey_sid] = master_plan
            await asyncio.to_thread(
                save_surveys_cache,
                self._host._survey_cache,
                run_fingerprint=self._host.run_fingerprint,
                system_configuration=self._host.system_configuration,
            )

        return master_plan

    def validate_master_plan_structures(self, raw: list) -> list:
        out = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            tiles = item.get("tiles")
            if not tiles or not isinstance(tiles, list):
                continue
            out.append(item)
        return out

    async def survey_district_with_chunking(self, district: dict) -> list:
        buildings = district.get("buildings") or []

        district_key = district.get("name", "unknown")
        survey_sid = district_survey_key(district)

        survey_chunk = self._host.system_configuration.grid.survey_buildings_per_chunk_count
        if len(buildings) <= survey_chunk:
            survey = await self.survey_district_single_pass(district, buildings_filter=None, prior_summary="")
            master_plan = survey.get("master_plan", [])
            if not master_plan:
                # LLM sometimes returns a single structure dict instead of the wrapper.
                # Detect this: if the result has "tiles" and "building_type", wrap it.
                if survey.get("tiles") and survey.get("building_type"):
                    logger.warning("Surveyor returned a bare structure (no master_plan wrapper) — wrapping as single-entry plan")
                    master_plan = [survey]
                elif survey.get("name") and survey.get("tiles"):
                    logger.warning("Surveyor returned a bare structure (no master_plan wrapper) — wrapping as single-entry plan")
                    master_plan = [survey]
                else:
                    raise AgentGenerationError(
                        "bad_model_output",
                        f"Surveyor returned no `master_plan` for {district.get('name', '?')}. "
                        f"Keys returned: {sorted(survey.keys())}",
                    )
            scenery_sum = (survey.get("district_scenery_summary") or "").strip()
            if scenery_sum:
                self._district_scenery_summaries[survey_sid] = scenery_sum
            palette = survey.get("suggested_palette")
            if isinstance(palette, dict):
                self._district_palettes[survey_sid] = palette
                logger.info("District %s palette: %s", district_key, palette)
            gap = get_district_spacing(
                    self._district_style(district),
                    system_configuration=self._host.system_configuration,
                )
            mp = self._spacing_enforced_plan(master_plan, min_gap=gap)
            region = district.get("region", {"x1": 0, "y1": 0, "x2": 10, "y2": 10})
            mp = self._apply_road_connectivity(mp, region)
            return self._apply_master_plan_validation(mp, f"Survey {district.get('name', '?')}")

        merged: list = []
        occupied_summary = "No tiles placed yet in this chunked survey."
        chunks_failed = 0
        total_chunks = (len(buildings) + survey_chunk - 1) // survey_chunk
        district_name = district.get("name", "?")
        for i, chunk_start in enumerate(range(0, len(buildings), survey_chunk)):
            chunk = buildings[chunk_start : chunk_start + survey_chunk]
            try:
                survey = await self.survey_district_single_pass(district, buildings_filter=chunk, prior_summary=occupied_summary)
                part = survey.get("master_plan", [])
                if not part:
                    # Try bare-structure fallback
                    if survey.get("tiles") and (survey.get("building_type") or survey.get("name")):
                        logger.warning("Surveyor returned bare structure in chunk — wrapping")
                        part = [survey]
                    else:
                        raise AgentGenerationError(
                            "bad_model_output",
                            f"Surveyor returned empty `master_plan` for chunk in {district_name}.",
                        )
                merged.extend(part)
                # Capture scenery summary and palette from the first successful chunk
                if survey_sid not in self._district_scenery_summaries:
                    scenery_sum = (survey.get("district_scenery_summary") or "").strip()
                    if scenery_sum:
                        self._district_scenery_summaries[survey_sid] = scenery_sum
                if survey_sid not in self._district_palettes:
                    palette = survey.get("suggested_palette")
                    if isinstance(palette, dict):
                        self._district_palettes[survey_sid] = palette
                occupied_summary = occupancy_summary_for_survey(merged)
            except Exception as exc:
                chunks_failed += 1
                logger.warning(
                    "Survey chunk %d/%d failed for %s (buildings %d-%d): %s",
                    i + 1, total_chunks, district_name,
                    chunk_start, chunk_start + len(chunk) - 1, exc,
                )
                await self._chat(
                    "cartographus", "warning",
                    f"Survey chunk {i+1} failed — skipping {len(chunk)} buildings. Continuing with remaining.",
                )

        if not merged:
            raise AgentGenerationError(
                "unknown",
                f"All {total_chunks} survey chunks failed for {district_name}. No master_plan produced.",
            )

        if chunks_failed:
            logger.warning(
                "Survey for %s completed with %d/%d chunks failed — returning partial results.",
                district_name, chunks_failed, total_chunks,
            )
            trace_event(
                "survey",
                "chunked_survey_partial",
                district=district_name,
                chunks_failed=chunks_failed,
                total_chunks=total_chunks,
            )

        gap = get_district_spacing(
                    self._district_style(district),
                    system_configuration=self._host.system_configuration,
                )
        mp = self._spacing_enforced_plan(merged, min_gap=gap)
        region = district.get("region", {"x1": 0, "y1": 0, "x2": 10, "y2": 10})
        mp = self._apply_road_connectivity(mp, region)
        return self._apply_master_plan_validation(mp, f"Survey (chunked) {district_name}")

    async def survey_district_single_pass(
        self,
        district: dict,
        buildings_filter: list | None,
        prior_summary: str,
    ) -> dict:
        region = district.get("region", {"x1": 0, "y1": 0, "x2": 10, "y2": 10})
        region_str = f"x={region['x1']}-{region['x2']}, y={region['y1']}-{region['y2']}"
        existing = self.world.get_region_summary(region["x1"], region["y1"], region["x2"], region["y2"])
        district_elev = district.get("elevation", 0.0)
        terrain_notes = district.get("terrain_notes", "")
        scen = self._host.scenario
        if not isinstance(scen, dict):
            raise AgentGenerationError("bad_model_output", "survey_district_single_pass: missing RunSession.scenario")

        scope_extra = ""
        if buildings_filter is not None:
            scope_extra = (
                f"\n\nTHIS PASS — place ONLY these named structures (plus roads/water connecting them): "
                f"{', '.join(buildings_filter)}.\n"
                f"Tiles already assigned in earlier passes for this district (do NOT overlap): {prior_summary}\n"
            )

        return await self.surveyor_generate_bounded(
            f"Survey district: {district['name']}\n"
            f"City: {scen['location']}, {scen['period']}\n"
            f"Description: {district.get('description', '')}\n"
            f"Terrain: {terrain_notes}\n"
            f"Base elevation: {district_elev} (0.0=water level, 0.3=gentle hill, 0.6=steep hill)\n"
            f"Grid region: {region_str} (each tile = {self._host.system_configuration.grid.world_scale_meters_per_tile} meters, "
            f"full grid is {self._host.system_configuration.grid.world_grid_width}x{self._host.system_configuration.grid.world_grid_height} = "
            f"{self._host.system_configuration.grid.world_grid_width * self._host.system_configuration.grid.world_scale_meters_per_tile}m x "
            f"{self._host.system_configuration.grid.world_grid_height * self._host.system_configuration.grid.world_scale_meters_per_tile}m)\n"
            f"Period: {district.get('period', '')}, Year: {district.get('year', '')}\n"
            f"Known buildings to place (full list for context): {', '.join(district.get('buildings', []))}\n"
            f"Already built in nearby areas:\n{existing}\n"
            f"{scope_extra}\n"
            f"Map the complete district for this pass: buildings, roads/paths, open spaces, and per-tile elevation.\n"
            f"Follow the system prompt's prose and evidence requirements for description/historical_note/environment_note.",
            trace_extra={
                "district": str(district.get("name", "?")),
                "phase": "survey_single_pass",
            },
        )

    async def _await_llm_with_optional_trace_heartbeat(
        self,
        coro_factory,
        *,
        agent_label: str,
        trace_extra: dict | None,
    ):
        """Run awaitable from ``coro_factory``; emit periodic ``trace_event`` while waiting if configured."""
        interval_s = float(self._host.system_configuration.timing.llm_trace_heartbeat_interval_seconds)
        extra = dict(trace_extra) if trace_extra else {}
        trace_event("llm", f"{agent_label}_call_start", **extra)
        started = time.monotonic()
        main_task = asyncio.create_task(coro_factory())

        async def _ticker() -> None:
            while not main_task.done():
                await asyncio.sleep(interval_s)
                if not main_task.done():
                    trace_event(
                        "llm",
                        f"{agent_label}_call_waiting",
                        elapsed_s=round(time.monotonic() - started, 1),
                        **extra,
                    )

        tick_task: asyncio.Task | None = None
        if interval_s > 0:
            tick_task = asyncio.create_task(_ticker())
        try:
            return await main_task
        finally:
            if tick_task is not None:
                tick_task.cancel()
                try:
                    await tick_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.debug("llm trace ticker cleanup", exc_info=True)
            trace_event(
                "llm",
                f"{agent_label}_call_done",
                elapsed_s=round(time.monotonic() - started, 2),
                **extra,
            )

    async def _generate_with_retry(
        self,
        agent,
        prompt: str,
        semaphore: asyncio.Semaphore,
        *,
        retry_delay_seconds: float,
        agent_label: str,
        trace_extra: dict | None = None,
    ) -> dict:
        for attempt in range(2):
            try:

                timeout_s = float(self._host.system_configuration.llm.agent_timeout_long_seconds)

                async def _guarded_generate():
                    async with semaphore:
                        return await asyncio.wait_for(
                            agent.generate(prompt),
                            timeout=timeout_s,
                        )

                return await self._await_llm_with_optional_trace_heartbeat(
                    _guarded_generate,
                    agent_label=agent_label,
                    trace_extra=trace_extra,
                )
            except asyncio.TimeoutError:
                err = AgentGenerationError(
                    "network",
                    f"{agent_label} timed out after {timeout_s}s (agent_timeout)",
                )
                if attempt == 0:
                    logger.warning(
                        "[roma.engine] %s call timed out, retrying once",
                        agent_label,
                    )
                    await asyncio.sleep(retry_delay_seconds)
                    continue
                raise err
            except AgentGenerationError as err:
                retriable = err.pause_reason in ("bad_model_output", "api_error", "network")
                if attempt == 0 and retriable:
                    logger.warning(
                        "[roma.engine] %s call failed (%s), retrying once: %s",
                        agent_label,
                        err.pause_reason,
                        err.pause_detail[:200] if err.pause_detail else "",
                    )
                    await asyncio.sleep(retry_delay_seconds)
                    continue
                raise

    async def surveyor_generate_bounded(self, prompt: str, *, trace_extra: dict | None = None) -> dict:
        return await self._generate_with_retry(
            self.surveyor,
            prompt,
            self._survey_semaphore,
            retry_delay_seconds=1.5,
            agent_label="Surveyor",
            trace_extra=trace_extra,
        )

    async def urbanista_generate_bounded(self, prompt: str, *, trace_extra: dict | None = None) -> dict:
        return await self._generate_with_retry(
            self.urbanista,
            prompt,
            self._host.tasks.urbanista_concurrency_semaphore,
            retry_delay_seconds=2.0,
            agent_label="Urbanista",
            trace_extra=trace_extra,
        )

    async def urbanista_generate_batch_bounded(
        self, batch_prompt: str, batch_count: int, *, trace_extra: dict | None = None
    ) -> list[dict]:
        """Single batched Urbanista call under concurrency semaphore, with trace heartbeats."""
        timeout_s = float(self._host.system_configuration.llm.agent_timeout_long_seconds)

        async def _batch_call():
            async with self._host.tasks.urbanista_concurrency_semaphore:
                return await asyncio.wait_for(
                    self.urbanista.generate_batch(batch_prompt, batch_count),
                    timeout=timeout_s,
                )

        try:
            return await self._await_llm_with_optional_trace_heartbeat(
                _batch_call,
                agent_label="Urbanista_batch",
                trace_extra=trace_extra,
            )
        except asyncio.TimeoutError as exc:
            raise AgentGenerationError(
                "network",
                f"Urbanista batch timed out after {timeout_s}s",
            ) from exc

    async def find_map_image(self):
        """Provide a known map for the selected city (URLs from system_config.csv)."""
        try:
            scen = self._host.scenario
            location = scen.get("location", "Rome") if isinstance(scen, dict) else "Rome"
            catalog = self._host.system_configuration.known_cartography_map_dictionary
            entry = catalog.get(location) if isinstance(catalog, dict) else None
            if isinstance(entry, dict):
                url = str(entry.get("image_url", "")).strip()
                source = str(entry.get("attribution", "")).strip()
                if url:
                    await self.broadcast({"type": "map_image", "url": url, "source": source})
                    logger.info("Map image: %s", source or url)
        except Exception as e:
            logger.warning("Map image failed: %s", e)
