"""Generators — extracted generation/discovery methods from BuildEngine."""

import asyncio
import json
import logging

from core.errors import AgentGenerationError
from core import config as config_module
from core.config import (
    GRID_WIDTH,
    GRID_HEIGHT,
    SURVEY_BUILDINGS_PER_CHUNK,
)
from core.persistence import save_districts_cache, load_districts_cache, load_surveys_cache, save_surveys_cache
from orchestration.spatial import enforce_spacing, occupancy_summary_for_survey

logger = logging.getLogger("eternal.generators")


class Generators:
    """Holds generation/discovery methods extracted from BuildEngine.

    Takes a back-reference to the engine for access to agents, state,
    broadcasting, and helper methods.
    """

    def __init__(self, engine):
        self.engine = engine

    # ── Convenience delegates ──────────────────────────────────────────

    @property
    def world(self):
        return self.engine.world

    @property
    def broadcast(self):
        return self.engine.broadcast

    @property
    def districts(self):
        return self.engine.districts

    @districts.setter
    def districts(self, value):
        self.engine.districts = value

    @property
    def running(self):
        return self.engine.running

    @property
    def planner_skeleton(self):
        return self.engine.planner_skeleton

    @property
    def planner_refine(self):
        return self.engine.planner_refine

    @property
    def surveyor(self):
        return self.engine.surveyor

    @property
    def urbanista(self):
        return self.engine.urbanista

    @property
    def _source_policy(self):
        return self.engine._source_policy

    @property
    def _survey_semaphore(self):
        return self.engine.tasks._survey_semaphore

    @property
    def _urbanista_semaphore(self):
        return self.engine.tasks._urbanista_semaphore

    @property
    def _fused_seed_master_plan(self):
        return self.engine._fused_seed_master_plan

    @_fused_seed_master_plan.setter
    def _fused_seed_master_plan(self, value):
        self.engine._fused_seed_master_plan = value

    @property
    def _survey_cache_lock(self):
        return self.engine._survey_cache_lock

    @property
    def _district_scenery_summaries(self):
        return self.engine._district_scenery_summaries

    @property
    def _district_palettes(self):
        return self.engine._district_palettes

    @property
    def _map_refine_task(self):
        return self.engine.tasks._map_refine_task

    @_map_refine_task.setter
    def _map_refine_task(self, value):
        self.engine.tasks._map_refine_task = value

    async def _chat(self, *args, **kwargs):
        return await self.engine._chat(*args, **kwargs)

    async def _set_status(self, *args, **kwargs):
        return await self.engine._set_status(*args, **kwargs)

    async def _pause_for_api_issue(self, *args, **kwargs):
        return await self.engine._pause_for_api_issue(*args, **kwargs)

    def _apply_master_plan_validation(self, *args, **kwargs):
        return self.engine._apply_master_plan_validation(*args, **kwargs)

    # ── Extracted methods ──────────────────────────────────────────────

    async def discover_districts(self) -> bool:
        """Load cached layout, or run phase-1 skeleton planner then background map refine."""
        cached = load_districts_cache()
        if cached:
            self.districts, map_desc = cached
            self._fused_seed_master_plan = None
            logger.info(f"Using cached districts: {len(self.districts)}")
            await self._chat("cartographus", "research",
                f"Using cached survey of {config_module.SCENARIO['location']} — {len(self.districts)} districts mapped.")
            if map_desc:
                await self.broadcast({"type": "map_description", "description": map_desc})
            asyncio.create_task(self.find_map_image())
            return True

        await self.broadcast({
            "type": "loading",
            "agent": "cartographus",
            "message": f"Mapping districts for {config_module.SCENARIO['location']}...",
        })
        await self._chat("cartographus", "research",
            f"Phase 1 — district skeleton for {config_module.SCENARIO['location']} ({config_module.SCENARIO['period']}). "
            f"A detailed map narrative will follow in the background while we build.")
        await self._set_status("cartographus", "thinking")
        plan_prompt = (
            f"Research and map the city of {config_module.SCENARIO['location']} during {config_module.SCENARIO['period']}.\n"
            f"Time span: {config_module.SCENARIO['year_start']} to {config_module.SCENARIO['year_end']}.\n"
            f"Ruler context: {config_module.SCENARIO['ruler']}.\n\n"
            f"ABOUT THIS CITY:\n{config_module.SCENARIO.get('description', '')}\n"
            f"Key features: {config_module.SCENARIO.get('features', '')}\n"
            f"Layout notes: {config_module.SCENARIO.get('grid_note', '')}\n\n"
            f"Grid size: {GRID_WIDTH}x{GRID_HEIGHT} (each tile ≈ 10 meters = {GRID_WIDTH*10}m x {GRID_HEIGHT*10}m).\n\n"
            f"RESEARCH DEEPLY: What districts existed at this exact time? Which buildings had been built? "
            f"Which hadn't been constructed yet? What was the terrain like?\n\n"
            f"For each district: real name, function, footprint in tile coordinates, named buildings that existed, "
            f"roads and natural features.\n\n"
            f"IMPORTANT: List at most {config_module.MAX_BUILDINGS_PER_DISTRICT} buildings per district. "
            f"Choose the most significant and visually distinctive structures. Include roads and open spaces between them.\n\n"
            f"Be historically precise: only include structures that existed at this time."
        )
        for attempt in range(2):
            try:
                result = await asyncio.wait_for(
                    self.planner_skeleton.generate(plan_prompt), timeout=300  # 5 min (CLI may do web searches)
                )
                break
            except (AgentGenerationError, asyncio.TimeoutError) as err:
                # Kill any orphaned CLI process on timeout
                import subprocess as _sp
                await asyncio.to_thread(_sp.run, ["pkill", "-f", r"claude.*--print.*--system-prompt"], capture_output=True)
                if attempt == 0:
                    reason = "network" if isinstance(err, asyncio.TimeoutError) else err.pause_reason
                    retriable = reason in ("bad_model_output", "api_error", "network")
                    if retriable:
                        logger.warning("Skeleton planner failed (%s), retrying once", reason)
                        await asyncio.sleep(3)
                        continue
                # Second attempt or non-retriable
                await self._set_status("cartographus", "idle")
                if isinstance(err, asyncio.TimeoutError):
                    await self._pause_for_api_issue("network", "Skeleton planner timed out after 3 minutes.", "cartographus")
                else:
                    await self._pause_for_api_issue(err.pause_reason, err.pause_detail, "cartographus")
                return False

        await self._set_status("cartographus", "speaking")
        logger.info(
            "Skeleton planner result keys=%s districts_count=%s commentary_len=%s",
            sorted(result.keys()),
            len(result.get("districts") or []),
            len(result.get("commentary") or ""),
        )
        await self._chat("cartographus", "research", result.get("commentary", "District layout established."))
        await self._set_status("cartographus", "idle")

        self.districts = result.get("districts", [])
        if len(self.districts) > config_module.MAX_DISTRICTS:
            logger.warning("District count %d exceeds cap of %d — truncating", len(self.districts), config_module.MAX_DISTRICTS)
            self.districts = self.districts[:config_module.MAX_DISTRICTS]
        logger.info(f"Skeleton: {len(self.districts)} districts")
        if self.districts:
            from core.run_log import log_event
            log_event("discovery", f"Mapped {len(self.districts)} districts",
                      districts=", ".join(d.get("name", "?") for d in self.districts))

        if not self.districts:
            # Log the full result for debugging
            logger.error(
                "Skeleton planner returned no districts. Full result: %s",
                json.dumps(result, indent=2)[:3000],
            )
            await self._pause_for_api_issue(
                "bad_model_output",
                "Skeleton planner returned no districts (empty or missing `districts` array). "
                "The model may have returned prose instead of the required JSON schema. "
                f"Result keys: {sorted(result.keys())}. Check the server log for the full output.",
                "cartographus",
            )
            return False

        seed = result.get("seed_master_plan")
        if isinstance(seed, list) and len(seed) > 0:
            self._fused_seed_master_plan = seed
            logger.info("Fused seed_master_plan from skeleton — skipping survey API for first district if valid.")
        else:
            self._fused_seed_master_plan = None

        save_districts_cache(self.districts, "")
        self._map_refine_task = asyncio.create_task(self.refine_map_description_background())
        logger.info("Map refine started immediately after skeleton (non-blocking).")
        asyncio.create_task(self.find_map_image())
        return True

    async def expand_city(self) -> bool:
        """Discover new districts at the city edges. Returns True if new districts found."""
        scenario = config_module.SCENARIO
        if not scenario:
            return False

        city_name = scenario.get("location", "Unknown")
        period = scenario.get("period", "")
        year = scenario.get("focus_year", 0)

        # Build existing districts summary
        existing = []
        for d in self.districts:
            r = d.get("region", {})
            existing.append(f"  - {d['name']}: ({r.get('x1',0)},{r.get('y1',0)}) to ({r.get('x2',0)},{r.get('y2',0)})")
        existing_str = "\n".join(existing) if existing else "  (none)"

        from prompts import load_prompt
        w = self.world
        prompt = load_prompt("cartographus_expand",
            SOURCE_POLICY=self._source_policy,
            CITY_NAME=city_name,
            GENERATION=self.engine.generation,
            MIN_X=w.min_x, MAX_X=w.max_x,
            MIN_Y=w.min_y, MAX_Y=w.max_y,
            WIDTH=w.width, HEIGHT=w.height,
            PERIOD=period,
            YEAR=year,
            EXISTING_DISTRICTS=existing_str,
        )

        await self._set_status("cartographus", "thinking")
        await self._chat("cartographus", "info", f"Expanding city — generation {self.engine.generation + 1}...")

        # Use skeleton planner agent for expansion (same LLM routing)
        try:
            result = await self.planner_skeleton.generate(prompt)
        except AgentGenerationError as err:
            await self._chat("cartographus", "warning", f"Expansion failed: {err}")
            await self._set_status("cartographus", "idle")
            return False

        new_districts = result.get("districts", [])
        if not new_districts:
            await self._chat("cartographus", "info", "No expansion districts found.")
            await self._set_status("cartographus", "idle")
            return False

        # Validate: new districts must not overlap existing regions
        for nd in new_districts:
            nd["expansion_generation"] = self.engine.generation + 1
            self.districts.append(nd)

        await self._chat("cartographus", "info",
                         f"Discovered {len(new_districts)} new districts: "
                         + ", ".join(d['name'] for d in new_districts))
        await self._set_status("cartographus", "idle")

        # Save expanded districts cache
        await asyncio.to_thread(save_districts_cache, self.districts, "")

        # Start surveys for new districts
        self.engine._start_survey_tasks_from_index(self.engine.district_index, len(self.districts))

        logger.info(f"Expansion: +{len(new_districts)} districts, total now {len(self.districts)}")

        # Send updated world bounds so client can expand its grid
        await self.broadcast(self.world.to_dict())
        return True

    async def refine_map_description_background(self):
        """Phase-2 planner: long map_description while districts are being surveyed/built."""
        try:
            if not self.running:
                return
            skeleton_payload = json.dumps(
                {"districts": self.districts, "city": config_module.SCENARIO.get("location", "")},
                indent=2,
            )
            instruction = (
                f"City: {config_module.SCENARIO['location']}, {config_module.SCENARIO['period']}.\n"
                f"Year span: {config_module.SCENARIO['year_start']} — {config_module.SCENARIO['year_end']}.\n"
                f"Ruler context: {config_module.SCENARIO['ruler']}.\n\n"
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
                await asyncio.to_thread(save_districts_cache, self.districts, map_desc)
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

        cached_plan = None
        async with self._survey_cache_lock:
            if not hasattr(self.engine, "_survey_cache"):
                self.engine._survey_cache = load_surveys_cache()
            if district_key in self.engine._survey_cache:
                cached_plan = self.engine._survey_cache[district_key]
        if cached_plan is not None:
            if isinstance(cached_plan, list) and len(cached_plan) > 0:
                if all(isinstance(s, dict) and "name" in s for s in cached_plan):
                    # Sanity: if district lists many buildings but cache has very few, re-survey
                    expected = len(district.get("buildings", []))
                    if expected > 3 and len(cached_plan) < 3:
                        logger.warning(
                            "Survey cache for %s has only %d structures but district lists %d buildings — re-surveying",
                            district_key, len(cached_plan), expected,
                        )
                    else:
                        logger.info("Survey cache hit: %s (%d structures)", district_key, len(cached_plan))
                        await self._chat("cartographus", "survey", f"Using cached survey of {district_key} ({len(cached_plan)} structures).")
                        return cached_plan
            # If validation fails, fall through to re-survey
            logger.warning("Survey cache invalid for %s — re-surveying", district_key)

        if district_index == 0 and self._fused_seed_master_plan:
            raw_seed = self._fused_seed_master_plan
            self._fused_seed_master_plan = None
            master_plan = self.validate_master_plan_structures(raw_seed)
            if master_plan:
                master_plan = enforce_spacing(master_plan)
                master_plan = self._apply_master_plan_validation(
                    master_plan, f"Fused seed {district_key!r}"
                )
                async with self._survey_cache_lock:
                    self.engine._survey_cache[district_key] = master_plan
                    await asyncio.to_thread(save_surveys_cache, self.engine._survey_cache)
                logger.info("Using fused seed_master_plan for %s (%d structures)", district_key, len(master_plan))
                return master_plan
            logger.warning("Fused seed_master_plan invalid — running full survey for first district.")

        await self._set_status("cartographus", "thinking")
        try:
            master_plan = await self.survey_district_with_chunking(district)
        except AgentGenerationError:
            await self._set_status("cartographus", "idle")
            raise

        await self._set_status("cartographus", "speaking")
        await self._chat("cartographus", "survey", f"Survey complete for {district_key}: {len(master_plan)} structures.")
        await self._set_status("cartographus", "idle")

        async with self._survey_cache_lock:
            self.engine._survey_cache[district_key] = master_plan
            await asyncio.to_thread(save_surveys_cache, self.engine._survey_cache)

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

        if len(buildings) <= SURVEY_BUILDINGS_PER_CHUNK:
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
                self._district_scenery_summaries[district_key] = scenery_sum
            palette = survey.get("suggested_palette")
            if isinstance(palette, dict):
                self._district_palettes[district_key] = palette
                logger.info("District %s palette: %s", district_key, palette)
            mp = enforce_spacing(master_plan)
            return self._apply_master_plan_validation(mp, f"Survey {district.get('name', '?')}")

        merged: list = []
        occupied_summary = "No tiles placed yet in this chunked survey."
        chunks_failed = 0
        total_chunks = (len(buildings) + SURVEY_BUILDINGS_PER_CHUNK - 1) // SURVEY_BUILDINGS_PER_CHUNK
        district_name = district.get("name", "?")
        for i, chunk_start in enumerate(range(0, len(buildings), SURVEY_BUILDINGS_PER_CHUNK)):
            chunk = buildings[chunk_start : chunk_start + SURVEY_BUILDINGS_PER_CHUNK]
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
                if district_key not in self._district_scenery_summaries:
                    scenery_sum = (survey.get("district_scenery_summary") or "").strip()
                    if scenery_sum:
                        self._district_scenery_summaries[district_key] = scenery_sum
                if district_key not in self._district_palettes:
                    palette = survey.get("suggested_palette")
                    if isinstance(palette, dict):
                        self._district_palettes[district_key] = palette
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

        mp = enforce_spacing(merged)
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

        scope_extra = ""
        if buildings_filter is not None:
            scope_extra = (
                f"\n\nTHIS PASS — place ONLY these named structures (plus roads/water connecting them): "
                f"{', '.join(buildings_filter)}.\n"
                f"Tiles already assigned in earlier passes for this district (do NOT overlap): {prior_summary}\n"
            )

        return await self.surveyor_generate_bounded(
            f"Survey district: {district['name']}\n"
            f"City: {config_module.SCENARIO['location']}, {config_module.SCENARIO['period']}\n"
            f"Description: {district.get('description', '')}\n"
            f"Terrain: {terrain_notes}\n"
            f"Base elevation: {district_elev} (0.0=water level, 0.3=gentle hill, 0.6=steep hill)\n"
            f"Grid region: {region_str} (each tile = 10 meters, full grid is {GRID_WIDTH}x{GRID_HEIGHT} = {GRID_WIDTH*10}m x {GRID_HEIGHT*10}m)\n"
            f"Period: {district.get('period', '')}, Year: {district.get('year', '')}\n"
            f"Known buildings to place (full list for context): {', '.join(district.get('buildings', []))}\n"
            f"Already built in nearby areas:\n{existing}\n"
            f"{scope_extra}\n"
            f"Map the complete district for this pass: buildings, roads/paths, open spaces, and per-tile elevation.\n"
            f"Follow the system prompt's prose and evidence requirements for description/historical_note/environment_note."
        )

    async def surveyor_generate_bounded(self, prompt: str) -> dict:
        for attempt in range(2):
            try:
                async with self._survey_semaphore:
                    return await self.surveyor.generate(prompt)
            except AgentGenerationError as err:
                retriable = err.pause_reason in ("bad_model_output", "api_error", "network")
                if attempt == 0 and retriable:
                    logger.warning(
                        "[roma.engine] Surveyor call failed (%s), retrying once: %s",
                        err.pause_reason,
                        err.pause_detail[:200] if err.pause_detail else "",
                    )
                    await asyncio.sleep(1.5)
                    continue
                raise

    async def urbanista_generate_bounded(self, prompt: str) -> dict:
        for attempt in range(2):
            try:
                async with self._urbanista_semaphore:
                    return await self.urbanista.generate(prompt)
            except AgentGenerationError as err:
                retriable = err.pause_reason in ("bad_model_output", "api_error", "network")
                if attempt == 0 and retriable:
                    logger.warning(
                        "[roma.engine] Urbanista call failed (%s), retrying once: %s",
                        err.pause_reason,
                        err.pause_detail[:200] if err.pause_detail else "",
                    )
                    await asyncio.sleep(2.0)
                    continue
                raise

    async def find_map_image(self):
        """Provide a known map for the selected city."""
        known_maps = {
            "Rome": ("https://upload.wikimedia.org/wikipedia/commons/thumb/8/88/Plan_de_Rome.jpg/1280px-Plan_de_Rome.jpg", "Paul Bigot's plan of ancient Rome"),
            "Athens": ("https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/Map_ancient_athens.png/800px-Map_ancient_athens.png", "Map of ancient Athens"),
            "Constantinople": ("https://upload.wikimedia.org/wikipedia/commons/thumb/2/2b/Byzantine_Constantinople-en.png/800px-Byzantine_Constantinople-en.png", "Map of Byzantine Constantinople"),
            "Jerusalem": ("https://upload.wikimedia.org/wikipedia/commons/thumb/c/c5/Jerusalem_in_the_first_century.jpg/800px-Jerusalem_in_the_first_century.jpg", "Jerusalem in the 1st century"),
            "Pompeii": ("https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/Pompeii_map-en.svg/800px-Pompeii_map-en.svg.png", "Archaeological map of Pompeii"),
            "Alexandria": ("https://upload.wikimedia.org/wikipedia/commons/thumb/0/07/Alexandria_-_Teknisk_Tidskrift_-_1906.jpg/800px-Alexandria_-_Teknisk_Tidskrift_-_1906.jpg", "Map of ancient Alexandria"),
            "Carthage": ("https://upload.wikimedia.org/wikipedia/commons/thumb/5/50/Carthage_topography.svg/800px-Carthage_topography.svg.png", "Topographic map of ancient Carthage"),
            "Baghdad": ("https://upload.wikimedia.org/wikipedia/commons/thumb/c/cc/CASEY2007_BAG_fig3-2.jpg/800px-CASEY2007_BAG_fig3-2.jpg", "Plan of Round City of Baghdad"),
            "Tenochtitlan": ("https://upload.wikimedia.org/wikipedia/commons/thumb/b/b4/Tenochtitlan_y_los_lagos_del_valle_de_Mexico.png/800px-Tenochtitlan_y_los_lagos_del_valle_de_Mexico.png", "Map of Tenochtitlan and the Valley of Mexico"),
            "Chang'an": ("https://upload.wikimedia.org/wikipedia/commons/thumb/1/11/Chang%27an_of_Tang.png/800px-Chang%27an_of_Tang.png", "Map of Tang dynasty Chang'an"),
        }
        try:
            location = config_module.SCENARIO.get("location", "Rome")
            if location in known_maps:
                url, source = known_maps[location]
                await self.broadcast({"type": "map_image", "url": url, "source": source})
                logger.info(f"Map image: {source}")
        except Exception as e:
            logger.warning(f"Map image failed: {e}")
