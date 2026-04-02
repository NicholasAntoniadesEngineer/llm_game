"""BuildEngine — Fully autonomous, agents discover and build everything."""

import asyncio
import json
import logging
import os
import time

from world.state import WorldState
from orchestration.bus import MessageBus, BusMessage
from run_log import log_event
from agents.base import AgentGenerationError, BaseAgent
from agents.golden_specs import get_golden_example, get_golden_example_for_culture

# Master-plan building_types that are rendered as procedural terrain (no Urbanista components).
OPEN_TERRAIN_TYPES = frozenset({"road", "forum", "garden", "water", "grass"})

# Wave 1 (landmarks) — built first across all districts for a quick city skeleton.
# Wave 2 (infill) — fills density after the skeleton is complete.
WAVE1_TYPES = frozenset({
    "temple", "basilica", "gate", "wall", "monument", "amphitheater",
    "thermae", "circus", "bridge", "aqueduct",
    # Open terrain is always wave 1 (roads, plazas, water — defines the street grid)
    "road", "forum", "garden", "water", "grass",
})
# Everything else (insula, domus, market, taberna, warehouse) is wave 2.
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
    sanitize_urbanista_output,
    UrbanistaValidationError,
)
from orchestration.reference_db import format_reference_for_prompt, lookup_architectural_reference
from orchestration.placement import check_functional_placement, log_functional_placement_warnings
from persistence import save_state, save_districts_cache, load_districts_cache, save_surveys_cache, load_surveys_cache

logger = logging.getLogger("roma.engine")


class BuildEngine:
    # Toolbar / start-screen status strip (must match static/tiles.js AGENT_NAMES keys).
    UI_STATUS_STRIP_AGENT_KEYS = ("cartographus", "urbanista")

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
        self._district_scenery_summaries: dict[str, str] = {}
        self._district_palettes: dict[str, dict] = {}  # {district_name: {primary, secondary, accent}}
        self._map_refine_task: asyncio.Task | None = None
        self._fused_seed_master_plan: list | None = None
        self._survey_cache_lock = asyncio.Lock()
        self._run_task: asyncio.Task | None = None
        # Wall-clock start when an agent enters "thinking" (for UI timer across reconnect / refresh)
        self._agent_thinking_started: dict[str, float] = {}
        self._token_telemetry_task: asyncio.Task | None = None

    def _token_telemetry_interval_s(self) -> int:
        raw = os.environ.get("ROMA_TOKEN_TELEMETRY_INTERVAL_S", "").strip()
        if not raw:
            return 5
        try:
            v = int(raw)
        except ValueError:
            return 5
        return 1 if v < 1 else v

    def _start_token_telemetry(self) -> None:
        if self._token_telemetry_task is not None and not self._token_telemetry_task.done():
            return

        async def _loop() -> None:
            try:
                from token_usage import STORE as TOKEN_USAGE_STORE
                from token_usage import aggregate_for_ui as token_aggregate_for_ui
                prev_totals: dict[str, int] = {}
                interval_s = self._token_telemetry_interval_s()
                logger.info("Token telemetry enabled: interval_s=%s", interval_s)
                while self.running:
                    await asyncio.sleep(interval_s)
                    payload = TOKEN_USAGE_STORE.to_payload()
                    # Flatten totals by agent_key for delta computation.
                    current_totals: dict[str, int] = {}
                    for agent_key, row in payload.items():
                        total = row.get("total") if isinstance(row, dict) else None
                        if not isinstance(total, dict):
                            continue
                        tt = total.get("total_tokens")
                        if isinstance(tt, int):
                            current_totals[str(agent_key)] = int(tt)
                    if not current_totals:
                        continue
                    # Only broadcast when totals have changed since the last send.
                    if current_totals != prev_totals:
                        try:
                            await self.broadcast(
                                {
                                    "type": "token_usage",
                                    "by_ui_agent": token_aggregate_for_ui(),
                                    "by_llm_key": payload,
                                }
                            )
                        except Exception:
                            logger.debug("Token telemetry: broadcast failed", exc_info=True)
                        deltas = {
                            k: (current_totals.get(k, 0) - prev_totals.get(k, 0))
                            for k in current_totals.keys()
                        }
                        prev_totals = current_totals
                        # Log top deltas only when something changed (suppress idle noise).
                        top = sorted(deltas.items(), key=lambda kv: kv[1], reverse=True)[:6]
                        top_str = ", ".join(f"{k}:+{v}" for k, v in top if v)
                        if top_str:
                            total_all = sum(current_totals.values())
                            logger.info("Token telemetry: total_tokens=%s | deltas=%s", total_all, top_str)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Token telemetry loop failed")

        self._token_telemetry_task = asyncio.create_task(_loop())

    async def _stop_token_telemetry(self) -> None:
        t = self._token_telemetry_task
        self._token_telemetry_task = None
        if t is None:
            return
        if not t.done():
            t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Token telemetry task ended with exception", exc_info=True)

    def reset_pipeline_for_new_run(self):
        """Clear in-flight survey/refine handles when starting a new scenario (sync; cancel tasks from async)."""
        for idx, task in self._survey_task_by_index.items():
            if task.done() and not task.cancelled():
                try:
                    task.exception()
                except Exception:
                    pass
        self._survey_task_by_index.clear()
        self._district_scenery_summaries.clear()
        self._district_palettes.clear()
        self._map_refine_task = None
        self._fused_seed_master_plan = None
        self._structures_since_save = 0
        self._agent_thinking_started.clear()
        # Clear in-memory survey cache so a new city does not reuse surveys
        # from the previous city (disk cache is deleted separately by handle_start).
        if hasattr(self, "_survey_cache"):
            del self._survey_cache

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

    async def broadcast_all_agents_idle(self) -> None:
        """Ensure the UI never shows 'thinking' when this build is not in progress."""
        self._agent_thinking_started.clear()
        for agent_name in self.UI_STATUS_STRIP_AGENT_KEYS:
            await self._set_status(agent_name, "idle")

    async def graceful_shutdown(self):
        """Stop the build loop, cancel prefetch/refine, and join the main ``run()`` task."""
        self.running = False
        await self._cancel_survey_and_refine_tasks()
        await self.cancel_run_task_join()
        await self.broadcast_all_agents_idle()

    async def run(self):
        try:
            self.running = True
            self._start_token_telemetry()
            logger.info("BuildEngine started — Ave Roma!")
            log_event("engine", "Build started",
                      scenario=str(config_module.SCENARIO.get("location", "?") if config_module.SCENARIO else "none"),
                      period=str(config_module.SCENARIO.get("period", "?") if config_module.SCENARIO else "none"),
                      grid=f"{GRID_WIDTH}x{GRID_HEIGHT}")
            # ─── PHASE 0: district skeleton (cached or fresh two-phase discovery) ───
            if not self.districts:
                discovery_ok = await self._discover_districts()
                if not discovery_ok:
                    self.running = False
                    return
            else:
                self._fused_seed_master_plan = None
                # On resume: if map refine task is dead/cancelled, re-launch it
                if self._map_refine_task is None or self._map_refine_task.done():
                    cached = load_districts_cache()
                    map_desc = cached[1] if cached else ""
                    if not map_desc:
                        self._map_refine_task = asyncio.create_task(self._refine_map_description_background())
                        logger.info("Map refine (re)started on resume (no cached narrative).")
                    asyncio.create_task(self._find_map_image())

            # Persist immediately so roma_save.json exists (enables "Continue" on reload).
            await asyncio.to_thread(save_state, self.world, self.chat_history, self.district_index, self.districts)

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

            # ─── TWO-WAVE BUILD: landmarks first, then density ───
            # Wave 1: landmarks + terrain (temples, gates, roads, plazas) across ALL districts
            # Wave 2: infill (houses, shops, workshops) across ALL districts
            # Result: user sees full city skeleton fast, then it fills in

            # Collect all surveys upfront (prefetch everything)
            self._start_survey_tasks_from_index(self.district_index, len(self.districts))
            district_plans: dict[int, list] = {}

            async def _get_plan(di: int) -> list | None:
                try:
                    return await self._await_survey_for_district_index(di)
                except asyncio.CancelledError:
                    return None
                except AgentGenerationError as err:
                    await self._pause_for_api_issue(err.pause_reason, err.pause_detail, "cartographus")
                    return None

            for wave_label, type_filter in [("Wave 1 — Landmarks", WAVE1_TYPES), ("Wave 2 — Infill", None)]:
                if not self.running:
                    build_loop_cancelled = True
                    break

                await self._chat("cartographus", "info", f"=== {wave_label} ===")
                log_event("engine", wave_label)

                for di in range(self.district_index, len(self.districts)):
                    if not self.running:
                        build_loop_cancelled = True
                        break

                    district = self.districts[di]
                    district_name = district["name"]

                    self.world.current_period = district.get("period", "")
                    self.world.current_year = district.get("year", -44)

                    scenery = self._district_scenery_summaries.get(district_name, "")
                    await self.broadcast({
                        "type": "phase",
                        "district": district_name,
                        "description": district.get("description", ""),
                        "scenery_summary": scenery,
                        "index": di + 1,
                        "total_districts": len(self.districts),
                        "wave": wave_label,
                    })
                    await self.broadcast({"type": "timeline", "period": district.get("period", ""), "year": district.get("year", -44)})

                    # Get survey (cached or wait for prefetch)
                    if di not in district_plans:
                        plan = await _get_plan(di)
                        if plan is None:
                            build_loop_cancelled = True
                            break
                        district_plans[di] = plan

                    master_plan = district_plans[di]

                    # Filter to only wave-relevant structures
                    if type_filter is not None:
                        wave_plan = [s for s in master_plan if s.get("building_type", "") in type_filter]
                    else:
                        # Wave 2: everything NOT in wave 1
                        wave_plan = [s for s in master_plan if s.get("building_type", "") not in WAVE1_TYPES]

                    if not wave_plan:
                        continue

                    logger.info(f"=== {wave_label}: {district_name} ({len(wave_plan)} structures) ===")
                    log_event("district", f"{wave_label}: {district_name}",
                              buildings=str(len(wave_plan)))

                    district_ok = await self._build_district(district, wave_plan)
                    if not district_ok:
                        build_loop_cancelled = True
                        break

                    await asyncio.to_thread(save_state, self.world, self.chat_history, di, self.districts)
                    log_event("district", f"Completed {wave_label}: {district_name}")

                if build_loop_cancelled:
                    break

            # Update district_index to reflect completion
            if not build_loop_cancelled:
                self.district_index = len(self.districts)

            await self._await_map_refine_task()

            if build_loop_cancelled:
                self.running = False
                await self._stop_token_telemetry()
                await self.broadcast_all_agents_idle()
                return

            if self.running:
                # Log completion with total token usage
                from token_usage import STORE as _tu_store
                tu = _tu_store.to_payload()
                total_tokens = sum(
                    (r.get("total") or {}).get("total_tokens", 0)
                    for r in tu.values() if isinstance(r, dict)
                )
                log_event("engine", f"Build COMPLETE — {len(self.districts)} districts, {self.world.turn} structures",
                          total_tokens=total_tokens)
                await self.broadcast({"type": "complete"})
            await self.broadcast_all_agents_idle()
            self.running = False
            await self._stop_token_telemetry()

        except asyncio.CancelledError:
            self.running = False
            logger.info("BuildEngine.run cancelled")
            await self._stop_token_telemetry()
            await self.broadcast_all_agents_idle()
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
                _sp.run(["pkill", "-f", r"claude.*--print.*--system-prompt"], capture_output=True)
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
        self._map_refine_task = asyncio.create_task(self._refine_map_description_background())
        logger.info("Map refine started immediately after skeleton (non-blocking).")
        asyncio.create_task(self._find_map_image())
        return True

    async def _pause_for_api_issue(self, pause_reason: str, pause_detail: str, agent_role: str):
        """Stop the build and notify clients (rate limit, API error, network, etc.)."""
        pause_detail = (pause_detail or "").strip()
        self.running = False
        await self._stop_token_telemetry()
        # Always save on pause — ensures no progress is lost
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
        for agent_name in self.UI_STATUS_STRIP_AGENT_KEYS:
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
        count = 0
        for struct in master_plan:
            count += len(struct.get("tiles") or [])
        if count == 0:
            return "None yet."
        names = [s.get("name", "?") for s in master_plan[:10]]
        extra = len(master_plan) - len(names)
        name_str = ", ".join(names)
        if extra > 0:
            name_str += f", +{extra} more"
        return f"{count} tiles placed across {len(master_plan)} structures ({name_str})."

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

        district_key = district.get("name", "unknown")

        if len(buildings) <= SURVEY_BUILDINGS_PER_CHUNK:
            survey = await self._survey_district_single_pass(district, buildings_filter=None, prior_summary="")
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
            mp = self._enforce_spacing(master_plan)
            return self._apply_master_plan_validation(mp, f"Survey {district.get('name', '?')}")

        merged: list = []
        occupied_summary = "No tiles placed yet in this chunked survey."
        chunks_failed = 0
        total_chunks = (len(buildings) + SURVEY_BUILDINGS_PER_CHUNK - 1) // SURVEY_BUILDINGS_PER_CHUNK
        district_name = district.get("name", "?")
        for i, chunk_start in enumerate(range(0, len(buildings), SURVEY_BUILDINGS_PER_CHUNK)):
            chunk = buildings[chunk_start : chunk_start + SURVEY_BUILDINGS_PER_CHUNK]
            try:
                survey = await self._survey_district_single_pass(district, buildings_filter=chunk, prior_summary=occupied_summary)
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
                occupied_summary = self._occupancy_summary_for_survey(merged)
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

        mp = self._enforce_spacing(merged)
        return self._apply_master_plan_validation(mp, f"Survey (chunked) {district_name}")

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
            f"Map the complete district for this pass: buildings, roads/paths, open spaces, and per-tile elevation.\n"
            f"Follow the system prompt's prose and evidence requirements for description/historical_note/environment_note."
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

        # Filter out structures whose tiles are already occupied by prior districts
        filtered_plan = []
        for struct in master_plan:
            dominated = True
            for t in struct.get("tiles", []):
                existing = self.world.get_tile(t.get("x", 0), t.get("y", 0))
                if not existing or existing.terrain == "empty":
                    dominated = False
                    break
            if not dominated:
                filtered_plan.append(struct)
            else:
                logger.info("Skipping %s — all tiles already occupied by prior district", struct.get("name", "?"))
        if filtered_plan != master_plan:
            logger.info("Cross-district overlap: %d/%d structures remain after filtering", len(filtered_plan), len(master_plan))
            master_plan = filtered_plan

        logger.info(f"Master plan: {len(master_plan)} structures")
        await self.broadcast({"type": "master_plan", "plan": master_plan})

        scenario = config_module.SCENARIO or {}
        city_loc = scenario.get("location") or ""
        district_scenery = self._district_scenery_summaries.get(district_key, "")
        district_palette = self._district_palettes.get(district_key)
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

            await self._chat(
                "cartographus",
                "info",
                (
                    f"Scenery: {name} ({btype}, {len(tiles)} tiles). "
                    if btype in OPEN_TERRAIN_TYPES
                    else f"Building: {name} ({btype}, {len(tiles)} tiles). "
                )
                + (f"Nearest: {nearest[0]['name']} at {nearest[0]['distance_m']}m" if nearest else ""),
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
            footprint_w = round(tile_w * 0.9, 2)
            footprint_d = round(tile_d * 0.9, 2)
            anchor_x, anchor_y = min(xs), min(ys)

            tile_elevations = [t.get("elevation", district.get("elevation", 0.0)) for t in tiles]
            avg_elevation = round(sum(tile_elevations) / len(tile_elevations), 2) if tile_elevations else 0.0

            env_note = (structure.get("environment_note") or "").strip()

            if btype in OPEN_TERRAIN_TYPES:
                golden_example_str = "[]"
                ref_db_section = ""
                # For large open terrain (>40 tiles), use bounding box + sample tiles
                if len(tiles) > 40:
                    sample_tiles = tiles[:5] + tiles[-5:] if len(tiles) > 10 else tiles[:5]
                    terrain_tiles_str = (
                        f"Bounding box: ({anchor_x},{anchor_y}) to ({max(xs)},{max(ys)}) — "
                        f"{len(tiles)} tiles total.\n"
                        f"Sample tiles: {json.dumps(sample_tiles)}\n"
                        f"Output a SINGLE representative tile with color/scenery, plus `commentary`. "
                        f"The engine replicates it to all {len(tiles)} coordinates."
                    )
                else:
                    terrain_tiles_str = f"Survey tile list (coordinates and elevations): {json.dumps(tiles)}"

                prompt = (
                    f"OPEN SPACE / SCENERY (not a building): {name}\n"
                    f"Surface type: {btype}\n"
                    f"City: {city_loc}, {scenario.get('period', '')}\n"
                    f"Footprint: {tile_w}x{tile_d} tiles = {footprint_w}x{footprint_d} world units\n"
                    f"Reference corner tile: ({anchor_x}, {anchor_y}), mean elevation: {avg_elevation}\n"
                    f"{terrain_tiles_str}\n\n"
                    f"NEARBY STRUCTURES:\n{neighbor_desc}\n\n"
                )
                if env_note:
                    prompt += f"SURVEYOR `environment_note`:\n{env_note}\n\n"
                prompt += (
                    f"SITE BRIEF (Historian + evidence):\n{physical_desc}\n\n"
                    f"OUTPUT REQUIREMENTS:\n"
                    f"- Return JSON with `tiles` — one entry per survey coordinate above.\n"
                    f"- Each tile MUST set `terrain` to the literal string \"{btype}\".\n"
                    f"- Each tile MAY include `spec`: {{ \"color\": \"#RRGGBB\", \"scenery\": {{ "
                    f"\"vegetation_density\": 0..1 (garden/grass), \"pavement_detail\": 0..1 (road/forum), "
                    f"\"water_murk\": 0..1 (water) }} }}.\n"
                    f"- Do NOT emit spec.components, spec.template, or spec.anchor — the client uses procedural meshes.\n"
                    f"- Rich `description` on every tile; substantive `commentary` and `reference` (paving, hydrology, planting).\n"
                    f"- Match elevations to the survey (mean {avg_elevation}).\n"
                )
            else:
                try:
                    golden_example_str = get_golden_example_for_culture(btype, footprint_w, footprint_d, city_loc, district_ref_year_i)
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

                # For large buildings (>30 tiles), simplify tile list to bounding box
                # to reduce prompt size. The model only needs to output the anchor tile's
                # spec — the engine populates secondary tiles from the anchor.
                if len(tiles) > 30:
                    tiles_str = (
                        f"Bounding box: ({anchor_x},{anchor_y}) to ({max(xs)},{max(ys)}) — "
                        f"{len(tiles)} tiles total. Output ONLY the anchor tile ({anchor_x},{anchor_y}) "
                        f"with full spec.components. The engine auto-fills secondary tiles."
                    )
                else:
                    tiles_str = f"All tiles: {json.dumps(tiles)}"

                prompt = (
                    f"Design: {name}\nType: {btype}\n"
                    f"City: {city_loc}, {scenario.get('period', '')}\n"
                    f"Footprint: {tile_w}x{tile_d} tiles = {footprint_w}x{footprint_d} world units\n"
                    f"Anchor tile: ({anchor_x}, {anchor_y}), elevation: {avg_elevation}\n"
                    f"{tiles_str}\n\n"
                    f"NEARBY STRUCTURES:\n{neighbor_desc}\n\n"
                    f"{ref_db_section}"
                    f"REFERENCE EXAMPLE (proportion + layering guide only — same building_type, scaled to this footprint):\n{golden_example_str}\n"
                    f"Use the reference example for proportion/stacking only; derive your design from the site brief (do not paste).\n\n"
                    f"SITE BRIEF (from survey — match this closely):\n{physical_desc}\n"
                    + (f"\nDISTRICT SCENERY (circulation, hydrology, green/blue network — orient facades and entrances accordingly):\n{district_scenery}\n\n" if district_scenery else "\n")
                    + f"IMPORTANT: Scale all component dimensions to fit a {footprint_w}x{footprint_d} footprint.\n"
                    f"- Max total building height: {round(max(footprint_w, footprint_d) * 1.2, 2)} world units\n"
                    + (f"- For small buildings (footprint < 2.0): use fewer components (3-6), shorter columns\n" if footprint_w < 2.0 or footprint_d < 2.0 else "")
                    + (f"- For large buildings (footprint > 5.0): use more components (8-14), add procedural details\n" if footprint_w > 5.0 or footprint_d > 5.0 else "")
                    + f"- Column/post radius should be ~{round(footprint_w / 60, 3)} for proportional supports\n"
                    f"- Total height should be {round(footprint_w * 0.7, 2)} to {round(footprint_w * 1.1, 2)}\n"
                    f"- Set elevation={avg_elevation} on all tiles\n"
                    f"- Set spec.anchor on EVERY tile to {{\"x\":{anchor_x},\"y\":{anchor_y}}}"
                )

                # PBR material guidance based on building type
                pbr_hints = {
                    "temple": "Use roughness 0.3-0.5 for polished marble columns, 0.7-0.9 for weathered stone podium. Use surface_detail 0.4-0.7 on large stone surfaces.",
                    "basilica": "Use roughness 0.5-0.7 for plastered walls, 0.3-0.4 for marble floors. Add surface_detail 0.5+ on exterior walls.",
                    "insula": "Use roughness 0.7-0.9 for brick, 0.5-0.7 for plaster. Vary colors between floors (ground = darker brick, upper = lighter plaster).",
                    "domus": "Use roughness 0.4-0.6 for stucco, 0.3 for polished interior. Add surface_detail 0.3-0.5 on exterior walls.",
                    "thermae": "Use roughness 0.2-0.4 for wet/glazed surfaces, 0.6-0.8 for exterior. Metalness 0.1+ for bronze fittings.",
                    "market": "Use roughness 0.7-0.9 for timber/rough surfaces. Add awning with contrasting color.",
                    "gate": "Use roughness 0.5-0.7 for dressed stone. Add battlements. Surface_detail 0.5+ for ashlar blocks.",
                    "wall": "Use roughness 0.8-0.95 for rough defensive walls. Surface_detail 0.6+ for rusticated stone.",
                    "monument": "Use roughness 0.2-0.4 for polished stone/bronze. Metalness 0.3-0.7 for bronze elements.",
                    "amphitheater": "Use roughness 0.5-0.7 for travertine. Layer arcade + tier + arcade for multi-story effect.",
                }
                hint = pbr_hints.get(btype, "Use roughness 0.5-0.8 for stone, 0.7-0.9 for weathered surfaces. Add surface_detail 0.3-0.6 on large planes.")
                prompt += f"\n- MATERIAL QUALITY: {hint}"

                if env_note:
                    prompt += (
                        f"\n\nSURVEYOR `environment_note` (edges, planting, circulation — use for façades and setting):\n"
                        f"{env_note}\n"
                    )

            # Inject survey-suggested palette (from Cartographus) so all buildings share coherent materials
            if district_palette and isinstance(district_palette, dict):
                parts = []
                for role in ("primary", "secondary", "accent"):
                    c = district_palette.get(role)
                    if isinstance(c, str) and c.startswith("#"):
                        parts.append(f"{role}={c}")
                if parts:
                    prompt += f"\n- DISTRICT PALETTE (from surveyor): {', '.join(parts)}. Use these as your base materials; vary per building ±10% lightness for uniqueness."

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
        skipped = 0
        consecutive_failures = 0
        max_consecutive_failures = 3
        placed_count = 0

        if urban_jobs:
            await self._set_status("urbanista", "thinking")
            await self._chat(
                "urbanista",
                "info",
                f"Designing {len(urban_jobs)} structures (max {URBANISTA_MAX_CONCURRENT} concurrent) — placing as each completes...",
            )

            # Wrap each task to carry its index
            async def _design_with_index(idx: int, prompt: str) -> tuple[int, dict | BaseException]:
                try:
                    result = await self._urbanista_generate_bounded(prompt)
                    return (idx, result)
                except BaseException as err:
                    return (idx, err)

            pending = [
                asyncio.create_task(_design_with_index(i, job["prompt"]))
                for i, job in enumerate(urban_jobs)
            ]

            try:
              for coro in asyncio.as_completed(pending):
                if not self.running:
                    break

                idx, arch_result = await coro
                job = urban_jobs[idx]
                name = job["name"]
                placed_count += 1
                logger.info(
                    "Streaming result %d/%d: %s — type=%s keys=%s",
                    placed_count, len(urban_jobs), name,
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
                    await self._chat("urbanista", "info", f"Skipped {name} — design failed ({arch_result.pause_reason}). Continuing.")
                    if consecutive_failures >= max_consecutive_failures:
                        for t in pending:
                            if not t.done():
                                t.cancel()
                        await self._pause_for_api_issue(
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
                    arch_result = sanitize_urbanista_output(arch_result)
                    validate_urbanista_arch_result(arch_result)
                except UrbanistaValidationError as err:
                    skipped += 1
                    logger.warning("Urbanista validation failed for %s: %s — skipping", name, err)
                    await self._chat("urbanista", "info", f"Skipped {name} — validation error. Continuing.")
                    continue

                await self._set_status("urbanista", "speaking")
                commentary = arch_result.get("commentary", "Design ready.")
                if len(commentary) > 400:
                    commentary = commentary[:397] + "..."
                await self._chat("urbanista", "design", commentary)
                await self._set_status("urbanista", "thinking" if placed_count < len(urban_jobs) else "idle")

                # Place tiles — ensure multi-tile buildings have anchors
                final_tiles = validate_urbanista_tiles(arch_result.get("tiles", []), GRID_WIDTH, GRID_HEIGHT)
                if not final_tiles:
                    skipped += 1
                    logger.warning("Urbanista returned no in-bounds tiles for %s — skipping", name)
                    await self._chat("urbanista", "info", f"Skipped {name} — no valid tiles. Continuing.")
                    continue

                # Auto-fill: if Urbanista returned fewer tiles than the survey
                # (because we told large buildings to output only the anchor), fill the rest
                survey_coords = {(t["x"], t["y"]) for t in tiles}
                returned_coords = {(td.get("x"), td.get("y")) for td in final_tiles}
                missing_coords = survey_coords - returned_coords
                if missing_coords and len(final_tiles) >= 1:
                    template_td = final_tiles[0]
                    for td in final_tiles:
                        if td.get("x") == anchor_x and td.get("y") == anchor_y:
                            template_td = td
                            break
                    is_terrain = job["btype"] in OPEN_TERRAIN_TYPES
                    for (mx, my) in missing_coords:
                        if is_terrain:
                            sec_tile = {
                                "x": mx, "y": my,
                                "terrain": job["btype"],
                                "building_name": template_td.get("building_name", name),
                                "building_type": job["btype"],
                                "description": f"Part of {name}",
                                "elevation": avg_elevation,
                            }
                            # Copy scenery spec from template if present
                            t_spec = template_td.get("spec")
                            if t_spec and isinstance(t_spec, dict):
                                sec_tile["spec"] = {k: v for k, v in t_spec.items() if k != "anchor"}
                            # Copy color from template
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

                # Inject anchors for multi-tile buildings if AI didn't set them (not for procedural terrain)
                if len(tiles) > 1 and job["btype"] not in OPEN_TERRAIN_TYPES:
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
                        if "elevation" not in td or td["elevation"] is None:
                            td["elevation"] = district_elev
                        td["period"] = district.get("period", "")
                        td["placed_by"] = "faber"
                        td["historical_note"] = hist_result.get("historical_note", hist_note)
                        if self.world.place_tile(x, y, td):
                            tile = self.world.get_tile(x, y)
                            if tile:
                                placed.append(tile.to_dict())

                logger.info("Placing %d tiles for %s", len(placed), name)
                if placed:
                    await self.broadcast({
                        "type": "tile_update", "tiles": placed,
                        "turn": self.world.turn,
                        "period": district.get("period", ""),
                        "year": district.get("year", ""),
                    })
                    await self.broadcast({
                        "type": "build_progress",
                        "structure": name,
                        "building_type": job["btype"],
                        "done": placed_count,
                        "total": len(urban_jobs),
                        "district": district_key,
                    })

                self.world.turn += 1
                await self._persist_progress_after_structure()
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

            if not self.running:
                return False
            await self._set_status("urbanista", "idle")

        if self._structures_since_save > 0:
            await asyncio.to_thread(save_state, self.world, self.chat_history, self.district_index, self.districts)
            self._structures_since_save = 0

        if skipped:
            logger.warning("District %s: %d/%d structures skipped due to errors", district_key, skipped, len(urban_jobs))
            await self._chat(
                "urbanista", "info",
                f"District complete — {len(urban_jobs) - skipped} placed, {skipped} skipped due to errors.",
            )

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

            # Find shift direction — try right, down, right+down, left, up, and diagonals
            tiles = master_plan[i].get("tiles", [])
            best_shift = None
            step = min_gap + 1
            shift_candidates = [
                (step, 0), (0, step), (step, step),
                (-step, 0), (0, -step), (-step, step), (step, -step), (-step, -step),
            ]
            for sx, sy in shift_candidates:
                shifted = set()
                in_bounds = True
                for t in tiles:
                    try:
                        nx, ny = int(t["x"]) + sx, int(t["y"]) + sy
                    except (KeyError, TypeError, ValueError):
                        continue
                    if not (0 <= nx < GRID_WIDTH and 0 <= ny < GRID_HEIGHT):
                        in_bounds = False
                        break
                    shifted.add((nx, ny))
                if in_bounds and shifted and not (shifted & occupied):
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

    async def _chat(self, sender, msg_type, content, approved=None):
        msg = BusMessage(sender=sender, msg_type=msg_type, content=content, turn=self.world.turn)
        await self.bus.publish(msg)
        data = {"type": "chat", "sender": sender, "msg_type": msg_type, "content": content, "turn": self.world.turn}
        if approved is not None:
            data["approved"] = approved
        await self.broadcast(data)

    async def _set_status(self, agent, status):
        payload = {"type": "agent_status", "agent": agent, "status": status}
        if status == "thinking":
            if agent not in self._agent_thinking_started:
                self._agent_thinking_started[agent] = time.time()
            payload["thinking_started_at_s"] = self._agent_thinking_started[agent]
        else:
            self._agent_thinking_started.pop(agent, None)
        await self.broadcast(payload)
