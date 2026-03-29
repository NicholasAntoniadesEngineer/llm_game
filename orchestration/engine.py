"""BuildEngine — Fully autonomous, agents discover and build everything."""

import asyncio
import json
import logging

from world.state import WorldState
from orchestration.bus import MessageBus, BusMessage
from agents.base import AgentGenerationError, BaseAgent
from agents.golden_specs import get_golden_example
from llm_agents import (
    KEY_CARTOGRAPHUS_REFINE,
    KEY_CARTOGRAPHUS_SKELETON,
    KEY_CARTOGRAPHUS_SURVEY,
    KEY_URBANISTA,
)
from agents.prompts import (
    CARTOGRAPHUS_PLAN_SKELETON,
    CARTOGRAPHUS_PLAN_REFINE,
    CARTOGRAPHUS_SURVEY,
    URBANISTA,
)
import config as config_module
from config import (
    STEP_DELAY,
    GRID_WIDTH,
    GRID_HEIGHT,
    URBANISTA_MAX_CONCURRENT,
    SURVEY_MAX_CONCURRENT,
    SURVEY_BUILDINGS_PER_CHUNK,
    SAVE_STATE_EVERY_N_STRUCTURES,
)
from orchestration.validation import (
    validate_master_plan,
    validate_urbanista_tiles,
    validate_urbanista_arch_result,
    UrbanistaValidationError,
)
from orchestration.reference_db import format_reference_for_prompt, lookup_architectural_reference
from orchestration.placement import check_functional_placement, log_functional_placement_warnings
from persistence import save_state, save_districts_cache, load_districts_cache, save_surveys_cache, load_surveys_cache

logger = logging.getLogger("roma.engine")


class BuildEngine:
    def __init__(self, world: WorldState, bus: MessageBus, broadcast_fn, chat_history_ref: list):
        self.world = world
        self.bus = bus
        self.broadcast = broadcast_fn
        self.chat_history = chat_history_ref
        self.running = False
        self.district_index = 0
        self.districts = []  # Discovered by Cartographus, NOT hardcoded

        # Phase-1 skeleton planner starts builds early; phase-2 refine adds map prose in background.
        self.planner_skeleton = BaseAgent(
            "cartographus",
            "Cartographus",
            CARTOGRAPHUS_PLAN_SKELETON,
            llm_agent_key=KEY_CARTOGRAPHUS_SKELETON,
        )
        self.planner_refine = BaseAgent(
            "cartographus",
            "Cartographus",
            CARTOGRAPHUS_PLAN_REFINE,
            llm_agent_key=KEY_CARTOGRAPHUS_REFINE,
        )
        self.surveyor = BaseAgent(
            "cartographus",
            "Cartographus",
            CARTOGRAPHUS_SURVEY,
            llm_agent_key=KEY_CARTOGRAPHUS_SURVEY,
        )
        self.urbanista = BaseAgent("urbanista", "Urbanista", URBANISTA, llm_agent_key=KEY_URBANISTA)
        self._survey_semaphore = asyncio.Semaphore(SURVEY_MAX_CONCURRENT)
        self._urbanista_semaphore = asyncio.Semaphore(URBANISTA_MAX_CONCURRENT)
        self._structures_since_save = 0
        self._survey_task_by_index: dict[int, asyncio.Task] = {}
        self._map_refine_task: asyncio.Task | None = None
        self._map_refine_pending = False
        self._fused_seed_master_plan: list | None = None
        self._survey_cache_lock = asyncio.Lock()
        self._run_task: asyncio.Task | None = None

    def reset_pipeline_for_new_run(self):
        """Clear in-flight survey/refine handles when starting a new scenario (sync; cancel tasks from async)."""
        self._survey_task_by_index.clear()
        self._map_refine_task = None
        self._map_refine_pending = False
        self._fused_seed_master_plan = None
        self._structures_since_save = 0

    async def abort_pipeline_tasks(self):
        """Cancel background survey/refine tasks (e.g. new city selected or reset)."""
        await self._cancel_survey_and_refine_tasks()

    async def cancel_run_task_join(self) -> None:
        """Cancel the asyncio Task driving ``run()``, if any, and wait until it finishes."""
        task = self._run_task
        if task is None:
            return
        if task.done():
            self._run_task = None
            return
        logger.info("Cancelling build engine run task — waiting for run() to exit")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Build engine run task ended with an exception", exc_info=True)
        finally:
            if self._run_task is task:
                self._run_task = None

    async def schedule_run(self) -> asyncio.Task:
        """Start ``run()`` as a tracked task; joins any prior run task first (idempotent)."""
        await self.cancel_run_task_join()
        new_task = asyncio.create_task(self.run())
        self._run_task = new_task

        def _on_done(t: asyncio.Task) -> None:
            if self._run_task is t:
                self._run_task = None

        new_task.add_done_callback(_on_done)
        return new_task

    async def graceful_shutdown(self):
        """Stop the build loop, cancel prefetch/refine, and join the main ``run()`` task."""
        self.running = False
        await self._cancel_survey_and_refine_tasks()
        await self.cancel_run_task_join()

    async def run(self):
        try:
            self.running = True
            logger.info("BuildEngine started — Ave Roma!")
            await asyncio.sleep(2)

            # ─── PHASE 0: district skeleton (cached or fresh two-phase discovery) ───
            if not self.districts:
                discovery_ok = await self._discover_districts()
                if not discovery_ok:
                    self.running = False
                    return
            else:
                self._fused_seed_master_plan = None

            # ─── Survey first: only the current district (unblocks buildings ASAP). ───
            # Previously we prefetched all districts at once; only SURVEY_MAX_CONCURRENT run, so the
            # active district could wait behind later districts and sit "thinking" with no tiles.
            self._survey_task_by_index.clear()
            self._start_survey_tasks_from_index(self.district_index, self.district_index + 1)
            if len(self.districts) > self.district_index + 1:
                logger.info(
                    "Survey priority: district %s/%s first; remaining districts prefetch after this survey completes",
                    self.district_index + 1,
                    len(self.districts),
                )

            build_loop_cancelled = False
            # ─── Build each district (survey may still be running ahead for later districts) ───
            try:
                while self.running and self.district_index < len(self.districts):
                    district = self.districts[self.district_index]
                    logger.info(f"=== District: {district['name']} ===")

                    self.world.current_period = district.get("period", "")
                    self.world.current_year = district.get("year", -44)

                    await self.broadcast({"type": "phase", "district": district["name"], "description": district.get("description", "")})
                    await self.broadcast({"type": "timeline", "period": district.get("period", ""), "year": district.get("year", -44)})

                    try:
                        master_plan = await self._await_survey_for_district_index(self.district_index)
                    except asyncio.CancelledError:
                        build_loop_cancelled = True
                        break
                    except AgentGenerationError as err:
                        await self._pause_for_api_issue(err.pause_reason, err.pause_detail, "cartographus")
                        break

                    if self._map_refine_pending and self._map_refine_task is None:
                        self._map_refine_pending = False
                        self._map_refine_task = asyncio.create_task(self._refine_map_description_background())
                        logger.info("Map description refine started after first district survey (non-blocking).")

                    # Prefetch surveys for later districts while Urbanista builds this one.
                    if self.district_index + 1 < len(self.districts):
                        n_rest = len(self.districts) - self.district_index - 1
                        self._start_survey_tasks_from_index(self.district_index + 1, len(self.districts))
                        if self.district_index == 0:
                            await self._chat(
                                "cartographus",
                                "info",
                                f"Prefetching {n_rest} other district survey(s) in parallel while the first district is built.",
                            )

                    district_ok = await self._build_district(district, master_plan)
                    if not district_ok:
                        break

                    self.district_index += 1
                    await asyncio.to_thread(save_state, self.world, self.chat_history, self.district_index, self.districts)
                    logger.info(f"=== Completed: {district['name']} ===")
            finally:
                await self._await_map_refine_task()

            if build_loop_cancelled:
                self.running = False
                return

            if self.running:
                await self.broadcast({"type": "complete"})
            self.running = False

        except asyncio.CancelledError:
            self.running = False
            logger.info("BuildEngine.run cancelled")
            raise

    async def _discover_districts(self) -> bool:
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
            asyncio.create_task(self._find_map_image())
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
            f"Be historically precise: only include structures that existed at this time."
        )
        try:
            result = await self.planner_skeleton.generate(plan_prompt)
        except AgentGenerationError as err:
            await self._set_status("cartographus", "idle")
            await self._pause_for_api_issue(err.pause_reason, err.pause_detail, "cartographus")
            return False

        await self._set_status("cartographus", "speaking")
        await self._chat("cartographus", "research", result.get("commentary", "District layout established."))
        await self._set_status("cartographus", "idle")

        self.districts = result.get("districts", [])
        logger.info(f"Skeleton: {len(self.districts)} districts")

        if not self.districts:
            await self._pause_for_api_issue(
                "unknown",
                "Skeleton planner returned no districts (empty or missing `districts` array).",
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
        # Defer long map narrative until after the first district survey completes so it does not
        # compete with the survey + Urbanista on the Claude CLI (same user log: refine + 3 surveys at once).
        self._map_refine_pending = True
        asyncio.create_task(self._find_map_image())
        return True

    async def _pause_for_api_issue(self, pause_reason: str, pause_detail: str, agent_role: str):
        """Stop the build and notify clients (rate limit, API error, network, etc.)."""
        pause_detail = (pause_detail or "").strip()
        self.running = False
        if self._structures_since_save > 0:
            await asyncio.to_thread(save_state, self.world, self.chat_history, self.district_index, self.districts)
            self._structures_since_save = 0
        await self._cancel_survey_and_refine_tasks()
        summaries = {
            "rate_limit": "Build paused: the AI service rate limit was reached. Wait a bit, then try again.",
            "api_error": "Build paused: the AI service reported an error. Check your account, plan, and CLI login, then try again.",
            "bad_model_output": "Build paused: the model response could not be used (expected JSON). Read the detail below and check the server log for a full preview.",
            "network": "Build paused: a network or connectivity issue occurred. Check your internet connection, then try again.",
            "cli_missing": "Build paused: the Claude CLI was not found. Install it and ensure it is on your PATH.",
            "unknown": "Build paused after an unexpected error. Check logs and try again.",
        }
        summary = summaries.get(pause_reason, summaries["unknown"])
        logger.error(
            "Build paused | reason=%s agent=%s summary=%s | detail=%s",
            pause_reason,
            agent_role,
            summary,
            pause_detail[:2000] if pause_detail else "(none)",
        )
        await self.broadcast({
            "type": "paused",
            "reason": pause_reason,
            "summary": summary,
            "detail": pause_detail[:1200] if pause_detail else "",
            "agent": agent_role,
        })
        for agent_name in ("imperator", "cartographus", "urbanista", "faber", "civis"):
            await self._set_status(agent_name, "idle")

    async def _cancel_survey_and_refine_tasks(self):
        for prefetch_index, task in list(self._survey_task_by_index.items()):
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug(
                    "Survey prefetch task district_index=%s ended with: %s",
                    prefetch_index,
                    exc,
                )
        self._survey_task_by_index.clear()
        rt = self._map_refine_task
        if rt and not rt.done():
            rt.cancel()
            try:
                await rt
            except asyncio.CancelledError:
                pass
        self._map_refine_task = None

    async def _await_map_refine_task(self):
        rt = self._map_refine_task
        if not rt:
            return
        try:
            await rt
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("Map refine task join: %s", e)
        self._map_refine_task = None

    async def _refine_map_description_background(self):
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
            result = await self.planner_refine.generate(instruction, allow_prose_fallback="map_refine")
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

    def _log_survey_prefetch_outcome(self, district_index: int, task: asyncio.Task) -> None:
        """Call task.exception() so asyncio does not log 'Task exception was never retrieved'."""
        try:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                logger.debug(
                    "Prefetch survey task exception retrieved (district_index=%s): %s",
                    district_index,
                    exc,
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[roma.engine] Survey prefetch done-callback error")

    def _start_survey_tasks_from_index(self, start_index: int, end_index: int | None = None):
        """Launch survey tasks for districts [start_index, end_index). Skips indices already running."""
        if end_index is None:
            end_index = len(self.districts)
        for i in range(start_index, end_index):
            existing = self._survey_task_by_index.get(i)
            if existing is not None and not existing.done():
                continue
            survey_task = asyncio.create_task(self._survey_work_item(i))
            survey_task.add_done_callback(
                lambda t, idx=i: self._log_survey_prefetch_outcome(idx, t)
            )
            self._survey_task_by_index[i] = survey_task

    async def _await_survey_for_district_index(self, index: int) -> list:
        task = self._survey_task_by_index.get(index)
        if task is None:
            return await self._survey_work_item(index)
        return await task

    def _occupancy_summary_for_survey(self, master_plan: list) -> str:
        cells = []
        for struct in master_plan:
            for t in struct.get("tiles") or []:
                x, y = t.get("x"), t.get("y")
                if x is not None and y is not None:
                    cells.append(f"({x},{y})")
        if not cells:
            return "None yet."
        return ", ".join(cells[:120]) + ("…" if len(cells) > 120 else "")

    async def _survey_work_item(self, district_index: int) -> list:
        """Resolve master_plan for one district (cache, fused seed, or survey API)."""
        district = self.districts[district_index]
        district_key = district.get("name", "unknown")

        cached_plan = None
        async with self._survey_cache_lock:
            if not hasattr(self, "_survey_cache"):
                self._survey_cache = load_surveys_cache()
            if district_key in self._survey_cache:
                cached_plan = self._survey_cache[district_key]
        if cached_plan is not None:
            logger.info("Survey cache hit: %s", district_key)
            await self._chat("cartographus", "survey", f"Using cached survey of {district_key}.")
            return cached_plan

        if district_index == 0 and self._fused_seed_master_plan:
            raw_seed = self._fused_seed_master_plan
            self._fused_seed_master_plan = None
            master_plan = self._validate_master_plan_structures(raw_seed)
            if master_plan:
                master_plan = self._enforce_spacing(master_plan)
                master_plan = self._apply_master_plan_validation(
                    master_plan, f"Fused seed {district_key!r}"
                )
                async with self._survey_cache_lock:
                    self._survey_cache[district_key] = master_plan
                    await asyncio.to_thread(save_surveys_cache, self._survey_cache)
                logger.info("Using fused seed_master_plan for %s (%d structures)", district_key, len(master_plan))
                return master_plan
            logger.warning("Fused seed_master_plan invalid — running full survey for first district.")

        await self._set_status("cartographus", "thinking")
        try:
            master_plan = await self._survey_district_with_chunking(district)
        except AgentGenerationError:
            await self._set_status("cartographus", "idle")
            raise

        await self._set_status("cartographus", "speaking")
        await self._chat("cartographus", "survey", f"Survey complete for {district_key}: {len(master_plan)} structures.")
        await self._set_status("cartographus", "idle")

        async with self._survey_cache_lock:
            self._survey_cache[district_key] = master_plan
            await asyncio.to_thread(save_surveys_cache, self._survey_cache)

        return master_plan

    def _validate_master_plan_structures(self, raw: list) -> list:
        out = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            tiles = item.get("tiles")
            if not tiles or not isinstance(tiles, list):
                continue
            out.append(item)
        return out

    async def _survey_district_with_chunking(self, district: dict) -> list:
        buildings = district.get("buildings") or []

        if len(buildings) <= SURVEY_BUILDINGS_PER_CHUNK:
            survey = await self._survey_district_single_pass(district, buildings_filter=None, prior_summary="")
            master_plan = survey.get("master_plan", [])
            if not master_plan:
                raise AgentGenerationError(
                    "unknown",
                    f"Surveyor returned no `master_plan` for {district.get('name', '?')}.",
                )
            mp = self._enforce_spacing(master_plan)
            return self._apply_master_plan_validation(mp, f"Survey {district.get('name', '?')}")

        merged: list = []
        occupied_summary = "No tiles placed yet in this chunked survey."
        for chunk_start in range(0, len(buildings), SURVEY_BUILDINGS_PER_CHUNK):
            chunk = buildings[chunk_start : chunk_start + SURVEY_BUILDINGS_PER_CHUNK]
            survey = await self._survey_district_single_pass(district, buildings_filter=chunk, prior_summary=occupied_summary)
            part = survey.get("master_plan", [])
            if not part:
                raise AgentGenerationError(
                    "unknown",
                    f"Surveyor returned empty `master_plan` for chunk in {district.get('name', '?')}.",
                )
            merged.extend(part)
            occupied_summary = self._occupancy_summary_for_survey(merged)

        mp = self._enforce_spacing(merged)
        return self._apply_master_plan_validation(mp, f"Survey (chunked) {district.get('name', '?')}")

    async def _survey_district_single_pass(
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

        return await self._surveyor_generate_bounded(
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
            f"MAP THE COMPLETE DISTRICT (for this pass). Research the REAL layout:\n\n"
            f"1. BUILDINGS: Named footprints in tiles.\n"
            f"2. STREETS/PATHS: road tiles connecting structures.\n"
            f"3. OPEN SPACES: forum, garden, water as needed.\n"
            f"4. ELEVATION per tile.\n"
            f"5. SPACING: historically accurate.\n\n"
            f"For EVERY structure, write RICH prose (see system prompt): `description` = long Historian layer "
            f"(form, orientation, materials, circulation, condition at this date); `historical_note` = long evidence layer "
            f"(sources, phases, excavations, measurable facts). Thin one-liners are not acceptable."
        )

    async def _surveyor_generate_bounded(self, prompt: str) -> dict:
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

    async def _urbanista_generate_bounded(self, prompt: str) -> dict:
        async with self._urbanista_semaphore:
            return await self.urbanista.generate(prompt)

    def _apply_master_plan_validation(self, master_plan: list, context: str) -> list:
        cleaned = validate_master_plan(master_plan, GRID_WIDTH, GRID_HEIGHT)
        if not cleaned and master_plan:
            raise AgentGenerationError(
                "unknown",
                f"{context}: no valid in-bounds tiles after validation (duplicates or out of grid).",
            )
        return cleaned

    async def _persist_progress_after_structure(self):
        """Throttle disk writes while keeping periodic checkpoints."""
        self._structures_since_save += 1
        if self._structures_since_save >= SAVE_STATE_EVERY_N_STRUCTURES:
            await asyncio.to_thread(save_state, self.world, self.chat_history, self.district_index, self.districts)
            self._structures_since_save = 0

    async def _build_district(self, district: dict, master_plan: list) -> bool:
        district_key = district.get("name", "unknown")
        if not master_plan:
            await self._pause_for_api_issue(
                "unknown",
                f"No master plan for district {district_key!r}.",
                "cartographus",
            )
            return False

        try:
            master_plan = self._apply_master_plan_validation(
                master_plan, f"District {district_key!r} master plan"
            )
        except AgentGenerationError as err:
            await self._pause_for_api_issue(err.pause_reason, err.pause_detail, "cartographus")
            return False

        log_functional_placement_warnings(master_plan, district_key)
        placement_warnings = check_functional_placement(master_plan)
        if placement_warnings:
            await self.broadcast(
                {
                    "type": "placement_warnings",
                    "district": district_key,
                    "warnings": placement_warnings,
                    "count": len(placement_warnings),
                }
            )
            preview = placement_warnings[:12]
            await self._chat(
                "cartographus",
                "info",
                "Placement checks: "
                + str(len(placement_warnings))
                + " note(s). "
                + preview[0][:200]
                + ("…" if len(preview[0]) > 200 else ""),
            )

        self._structures_since_save = 0

        # Validate no overlapping tiles
        all_tiles = {}
        for struct in master_plan:
            for t in struct.get("tiles", []):
                key = (t["x"], t["y"])
                if key in all_tiles:
                    logger.warning(f"Tile overlap: {struct['name']} and {all_tiles[key]} at {key}")
                all_tiles[key] = struct.get("name", "?")

        logger.info(f"Master plan: {len(master_plan)} structures")
        await self.broadcast({"type": "master_plan", "plan": master_plan})

        scenario = config_module.SCENARIO or {}
        city_loc = scenario.get("location") or ""
        district_ref_year = district.get("year")
        if district_ref_year is None:
            district_ref_year = scenario.get("year_start", 0)
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
            name = structure.get("name", "Structure")
            btype = structure.get("building_type", "building")
            tiles = structure.get("tiles", [])
            if not tiles:
                continue
            first_tile = tiles[0]
            existing_tile = self.world.get_tile(first_tile["x"], first_tile["y"])
            if existing_tile and existing_tile.terrain != "empty":
                logger.info(f"Skipping {name} — already built")
                await self._chat("cartographus", "info", f"Skipping {name} — already built.")
                continue
            buildable.append(structure)

            my_center = centers_list[struct_idx]
            if not my_center:
                my_center = (0.0, 0.0)
            neighbors = []
            for other_idx, other in enumerate(master_plan):
                if other_idx == struct_idx:
                    continue
                oc = centers_list[other_idx]
                if not oc:
                    continue
                other_name = other.get("name", "")
                dist_tiles = round(((my_center[0] - oc[0]) ** 2 + (my_center[1] - oc[1]) ** 2) ** 0.5, 1)
                dist_meters = round(dist_tiles * 10)
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
        for idx, structure in enumerate(buildable):
            if not self.running:
                return False

            name = structure.get("name", "Structure")
            btype = structure.get("building_type", "building")
            tiles = structure.get("tiles", [])
            desc = (structure.get("description") or "").strip()
            hist_note = (structure.get("historical_note") or "").strip()
            ctx = structure_contexts[idx]
            neighbor_desc = ctx["neighbor_desc"]
            nearest = ctx["nearest"]

            await self._chat("cartographus", "info",
                f"Building: {name} ({btype}, {len(tiles)} tiles). "
                + (f"Nearest: {nearest[0]['name']} at {nearest[0]['distance_m']}m" if nearest else ""))

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
            footprint_w = round(tile_w * 0.9, 2)
            footprint_d = round(tile_d * 0.9, 2)
            anchor_x, anchor_y = min(xs), min(ys)

            tile_elevations = [t.get("elevation", district.get("elevation", 0.0)) for t in tiles]
            avg_elevation = round(sum(tile_elevations) / len(tile_elevations), 2) if tile_elevations else 0.0

            try:
                golden_example_str = get_golden_example(btype, footprint_w, footprint_d)
            except ValueError as exc:
                await self._pause_for_api_issue("unknown", str(exc), "urbanista")
                return False

            ref_entry = lookup_architectural_reference(btype, city_loc, district_ref_year_i)
            ref_db_block = format_reference_for_prompt(ref_entry)
            ref_db_section = ""
            if ref_db_block:
                ref_db_section = (
                    f"MEASURED REFERENCE (curated database — numeric ranges for proportion_rules / sanity checks; "
                    f"use when they align with the site brief):\n{ref_db_block}\n\n"
                )

            prompt = (
                f"Design: {name}\nType: {btype}\n"
                f"City: {city_loc}, {scenario.get('period', '')}\n"
                f"Footprint: {tile_w}x{tile_d} tiles = {footprint_w}x{footprint_d} world units\n"
                f"Anchor tile: ({anchor_x}, {anchor_y}), elevation: {avg_elevation}\n"
                f"All tiles: {json.dumps(tiles)}\n\n"
                f"NEARBY STRUCTURES:\n{neighbor_desc}\n\n"
                f"{ref_db_section}"
                f"REFERENCE EXAMPLE (proportion + layering guide only — same building_type, scaled to this footprint):\n{golden_example_str}\n"
                f"Use it as a REFERENCE for sensible heights, radii, and stack order. You MUST still emit your own full "
                f"spec.components list derived from the site brief below — do not paste this block verbatim. "
                f"Adapt, add, remove, or replace parts (including type procedural) for {city_loc}. "
                f"Only use spec.template if you deliberately choose id \"open\" or a shortcut; the default output is always top-level spec.components.\n\n"
                f"SITE BRIEF (from survey — match this closely):\n{physical_desc}\n\n"
                f"IMPORTANT: Scale all component dimensions to fit a {footprint_w}x{footprint_d} footprint.\n"
                f"- Column/post radius should be ~{round(footprint_w / 60, 3)} for proportional supports\n"
                f"- Total height should be {round(footprint_w * 0.7, 2)} to {round(footprint_w * 1.1, 2)}\n"
                f"- Set elevation={avg_elevation} on all tiles\n"
                f"- Set spec.anchor on EVERY tile to {{\"x\":{anchor_x},\"y\":{anchor_y}}}"
            )
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

        arch_results: list = []
        if urban_jobs:
            await self._set_status("urbanista", "thinking")
            await self._chat(
                "urbanista",
                "info",
                f"Designing {len(urban_jobs)} structures (max {URBANISTA_MAX_CONCURRENT} concurrent)...",
            )
            utasks = [self._urbanista_generate_bounded(job["prompt"]) for job in urban_jobs]
            arch_results = await asyncio.gather(*utasks, return_exceptions=True)
            await self._set_status("urbanista", "idle")

        for idx, job in enumerate(urban_jobs):
            if not self.running:
                return False
            arch_result = arch_results[idx]
            if isinstance(arch_result, AgentGenerationError):
                await self._pause_for_api_issue(
                    arch_result.pause_reason,
                    arch_result.pause_detail,
                    "urbanista",
                )
                return False
            if isinstance(arch_result, BaseException):
                raise arch_result

            name = job["name"]
            tiles = job["tiles"]
            hist_note = job["hist_note"]
            hist_result = job["hist_result"]
            anchor_x = job["anchor_x"]
            anchor_y = job["anchor_y"]

            try:
                validate_urbanista_arch_result(arch_result)
            except UrbanistaValidationError as err:
                await self._pause_for_api_issue("api_error", str(err), "urbanista")
                return False

            await self._set_status("urbanista", "speaking")
            await self._chat("urbanista", "design", arch_result.get("commentary", "Design ready."))
            await self._set_status("urbanista", "idle")

            # Place tiles — ensure multi-tile buildings have anchors
            final_tiles = validate_urbanista_tiles(arch_result.get("tiles", []), GRID_WIDTH, GRID_HEIGHT)
            if not final_tiles:
                await self._pause_for_api_issue(
                    "unknown",
                    f"Urbanista returned no in-bounds `tiles` for structure {name!r}.",
                    "urbanista",
                )
                return False

            # Inject anchors for multi-tile buildings if AI didn't set them
            if len(tiles) > 1:
                for td in final_tiles:
                    if not td.get("spec"):
                        td["spec"] = {}
                    if not td["spec"].get("anchor"):
                        td["spec"]["anchor"] = {"x": anchor_x, "y": anchor_y}

            placed = []
            district_elev = district.get("elevation", 0.0)
            for td in final_tiles:
                x, y = td.get("x"), td.get("y")
                if x is not None and y is not None:
                    # Apply district elevation as default if tile doesn't have its own
                    if "elevation" not in td or td["elevation"] is None:
                        td["elevation"] = district_elev
                    td["period"] = district.get("period", "")
                    td["placed_by"] = "faber"
                    td["historical_note"] = hist_result.get("historical_note", hist_note)
                    if self.world.place_tile(x, y, td):
                        tile = self.world.get_tile(x, y)
                        if tile:
                            placed.append(tile.to_dict())

            if placed:
                await self.broadcast({
                    "type": "tile_update", "tiles": placed,
                    "turn": self.world.turn,
                    "period": district.get("period", ""),
                    "year": district.get("year", ""),
                })

            self.world.turn += 1
            await self._persist_progress_after_structure()

            await asyncio.sleep(STEP_DELAY)

        if self._structures_since_save > 0:
            await asyncio.to_thread(save_state, self.world, self.chat_history, self.district_index, self.districts)
            self._structures_since_save = 0

        return True

    def _enforce_spacing(self, master_plan, min_gap=1):
        """Shift buildings that touch or overlap to create gaps between them."""
        if not master_plan:
            return master_plan

        # Collect all occupied tiles per building (as sets for fast lookup)
        bldg_tiles = []
        for struct in master_plan:
            tiles = struct.get("tiles", [])
            tile_set = set()
            for t in tiles:
                try:
                    x = int(t["x"])
                    y = int(t["y"])
                except (KeyError, TypeError, ValueError):
                    continue
                tile_set.add((x, y))
            bldg_tiles.append(tile_set)

        # Build a buffer zone around each building (tiles within min_gap)
        def get_buffer(tile_set):
            buf = set()
            for (x, y) in tile_set:
                for dx in range(-min_gap, min_gap + 1):
                    for dy in range(-min_gap, min_gap + 1):
                        if dx == 0 and dy == 0:
                            continue
                        buf.add((x + dx, y + dy))
            return buf - tile_set  # exclude the building's own tiles

        # Check each building against all previous ones and shift if needed
        for i in range(1, len(master_plan)):
            occupied = set()
            for j in range(i):
                occupied |= bldg_tiles[j]
                occupied |= get_buffer(bldg_tiles[j])

            # Check if current building overlaps with occupied + buffer zone
            overlap = bldg_tiles[i] & occupied
            if not overlap:
                continue

            # Find shift direction — try right, down, right+down
            tiles = master_plan[i].get("tiles", [])
            best_shift = None
            for sx, sy in [(min_gap + 1, 0), (0, min_gap + 1), (min_gap + 1, min_gap + 1),
                           (-(min_gap + 1), 0), (0, -(min_gap + 1))]:
                shifted = set()
                for t in tiles:
                    try:
                        shifted.add((int(t["x"]) + sx, int(t["y"]) + sy))
                    except (KeyError, TypeError, ValueError):
                        continue
                if not (shifted & occupied):
                    best_shift = (sx, sy)
                    break

            if best_shift:
                sx, sy = best_shift
                logger.info(f"Spacing fix: shifting '{master_plan[i].get('name')}' by ({sx},{sy})")
                for t in tiles:
                    try:
                        t["x"] = int(t["x"]) + sx
                        t["y"] = int(t["y"]) + sy
                    except (KeyError, TypeError, ValueError):
                        continue
                bldg_tiles[i] = set()
                for t in tiles:
                    try:
                        bldg_tiles[i].add((int(t["x"]), int(t["y"])))
                    except (KeyError, TypeError, ValueError):
                        continue

        return master_plan

    async def _find_map_image(self):
        """Provide a known map for the selected city."""
        known_maps = {
            "Rome": ("https://upload.wikimedia.org/wikipedia/commons/thumb/8/88/Plan_de_Rome.jpg/1280px-Plan_de_Rome.jpg", "Paul Bigot's plan of ancient Rome"),
            "Athens": ("https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/Map_ancient_athens.png/800px-Map_ancient_athens.png", "Map of ancient Athens"),
            "Constantinople": ("https://upload.wikimedia.org/wikipedia/commons/thumb/2/2b/Byzantine_Constantinople-en.png/800px-Byzantine_Constantinople-en.png", "Map of Byzantine Constantinople"),
            "Jerusalem": ("https://upload.wikimedia.org/wikipedia/commons/thumb/c/c5/Jerusalem_in_the_first_century.jpg/800px-Jerusalem_in_the_first_century.jpg", "Jerusalem in the 1st century"),
            "Pompeii": ("https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/Pompeii_map-en.svg/800px-Pompeii_map-en.svg.png", "Archaeological map of Pompeii"),
        }
        try:
            location = config_module.SCENARIO.get("location", "Rome")
            if location in known_maps:
                url, source = known_maps[location]
                await self.broadcast({"type": "map_image", "url": url, "source": source})
                logger.info(f"Map image: {source}")
        except Exception as e:
            logger.warning(f"Map image failed: {e}")

    async def _chat(self, sender, msg_type, content, approved=None):
        msg = BusMessage(sender=sender, msg_type=msg_type, content=content, turn=self.world.turn)
        await self.bus.publish(msg)
        data = {"type": "chat", "sender": sender, "msg_type": msg_type, "content": content, "turn": self.world.turn}
        if approved is not None:
            data["approved"] = approved
        await self.broadcast(data)

    async def _set_status(self, agent, status):
        await self.broadcast({"type": "agent_status", "agent": agent, "status": status})
