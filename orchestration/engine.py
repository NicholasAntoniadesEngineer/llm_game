"""BuildEngine — Fully autonomous, agents discover and build everything."""

import asyncio
import json
import logging
import os
import time
from typing import Any

from world.state import WorldState
from world.blueprint import CityBlueprint
from agents.memory import StyleMemory
from agents.tools import WorldQueryTools
from orchestration.debate import DebateProtocol
from orchestration.proposals import ProposalQueue, WorkProposal
from orchestration.bus import MessageBus, BusMessage
from orchestration.task_manager import TaskManager
from core.run_log import log_event, trace_event
from core.errors import AgentGenerationError
from agents.base import BaseAgent
from agents.ui_notifier import AsyncBroadcastNotifier
from agents.llm_routing import (
    KEY_CARTOGRAPHUS_REFINE,
    KEY_CARTOGRAPHUS_SKELETON,
    KEY_CARTOGRAPHUS_SURVEY,
    KEY_URBANISTA,
)
from prompts import load_prompt, format_building_types, format_material_palette
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import Config

from orchestration.validation import validate_master_plan
from core.fingerprint import compute_run_fingerprint, ensure_district_ids
from core.persistence import save_state, load_districts_cache, save_blueprint, load_blueprint
from core.run_session import RunSession
from orchestration.generators import Generators
from orchestration.engine_build_loop import run_build_generation
from orchestration.engine_urbanista import execute_batch_urbanista
from orchestration.engine_district_build import run_district_build
from orchestration.engine_run_phase import EngineRunPhase

logger = logging.getLogger("eternal.engine")


class BuildEngine:
    # Toolbar / start-screen status strip (must match static/tiles.js AGENT_NAMES keys).
    UI_STATUS_STRIP_AGENT_KEYS = ("cartographus", "urbanista")

    def __init__(
        self,
        world: "WorldState",
        bus: "MessageBus",
        broadcast_fn,
        chat_history_ref: list,
        *,
        run_session: "RunSession",
        system_configuration: "Config",
    ):
        """Updated to accept injected system_configuration. Removes globals from core.config. Uses descriptive parameter name. Later todos will replace globals uses with config values and refactor to thinner coordinator."""
        self.system_configuration = system_configuration
        self._open_terrain_types_set = frozenset(system_configuration.terrain.open_terrain_types_set)
        self._batchable_types_set = frozenset(system_configuration.terrain.batchable_types_set)
        self._wave_one_building_types_set = frozenset(system_configuration.terrain.wave_one_building_types_set)
        self.world = world
        self.bus = bus
        self.broadcast = broadcast_fn
        self._ui_notifier = AsyncBroadcastNotifier(broadcast_fn)
        self.chat_history = chat_history_ref
        self.run_session = run_session
        self.district_index = 0
        self.districts = []  # Discovered by Cartographus, NOT hardcoded

        # Phase-1 skeleton planner starts builds early; phase-2 refine adds map prose in background.
        _source_policy = load_prompt("source_policy")
        grid_width_value = self.system_configuration.grid.world_grid_width
        grid_height_value = self.system_configuration.grid.world_grid_height
        meters_per_tile = self.system_configuration.grid.world_scale_meters_per_tile
        self.planner_skeleton = BaseAgent(
            "cartographus",
            "Cartographus",
            load_prompt(
                "cartographus_plan_skeleton",
                GRID_WIDTH=grid_width_value,
                GRID_HEIGHT=grid_height_value,
                GRID_WIDTH_M=int(grid_width_value * meters_per_tile),
                GRID_HEIGHT_M=int(grid_height_value * meters_per_tile),
                SOURCE_POLICY=_source_policy,
            ),
            llm_agent_key=KEY_CARTOGRAPHUS_SKELETON,
            system_configuration=self.system_configuration,
            ui_notifier=self._ui_notifier,
        )
        self.planner_refine = BaseAgent(
            "cartographus",
            "Cartographus",
            load_prompt("cartographus_plan_refine", SOURCE_POLICY=_source_policy),
            llm_agent_key=KEY_CARTOGRAPHUS_REFINE,
            system_configuration=self.system_configuration,
            ui_notifier=self._ui_notifier,
        )
        self.surveyor = BaseAgent(
            "cartographus",
            "Cartographus",
            load_prompt("cartographus_survey", SOURCE_POLICY=_source_policy, BUILDING_TYPES=format_building_types()),
            llm_agent_key=KEY_CARTOGRAPHUS_SURVEY,
            system_configuration=self.system_configuration,
            ui_notifier=self._ui_notifier,
        )
        self.urbanista = BaseAgent(
            "urbanista",
            "Urbanista",
            load_prompt("urbanista", BUILDING_TYPES=format_building_types(), KEY_COLORS=format_material_palette()),
            llm_agent_key=KEY_URBANISTA,
            system_configuration=self.system_configuration,
            ui_notifier=self._ui_notifier,
        )
        self._source_policy = _source_policy
        self.generation = 0
        self._trace_snapshot: dict[str, Any] = {"phase": "init"}
        self.blueprint: CityBlueprint | None = None
        self._district_scenery_summaries: dict[str, str] = {}
        self._district_palettes: dict[str, dict] = {}  # {district_name: {primary, secondary, accent}}
        self._fused_seed_master_plan: list | None = None
        self._survey_cache_lock = asyncio.Lock()

        # Intelligence subsystems
        self.style_memory = StyleMemory()
        self.world_tools = WorldQueryTools(world)
        self.debate = DebateProtocol()
        self.proposals = ProposalQueue()

        # TaskManager owns running flag, task handles, semaphores, and save-throttling.
        # Note: survey_work_item_fn uses a lambda so it resolves self.generators at call time
        # (generators is created immediately after tasks).
        self.tasks = TaskManager(
            broadcast_fn=broadcast_fn,
            world=world,
            chat_history=chat_history_ref,
            districts_ref=self.districts,
            survey_work_item_fn=lambda di: self.generators.survey_work_item(di),
            set_status_fn=self._set_status,
            district_index_fn=lambda: self.district_index,
            generation_fn=lambda: self.generation,
            scenario_fn=lambda: self.scenario,
            system_configuration=self.system_configuration,
        )

        # Generators — extracted discovery/survey/expansion methods.
        self.generators = Generators(self)

    @property
    def scenario(self) -> dict[str, Any] | None:
        return self.run_session.scenario

    @property
    def run_fingerprint(self) -> str:
        return compute_run_fingerprint(
            self.scenario if isinstance(self.scenario, dict) else None,
            self.world.chunk_size_tiles,
            self.system_configuration.grid.world_grid_width,
            self.system_configuration.grid.world_grid_height,
        )

    async def _save_state_thread(self, flush_mode: str = "incremental") -> None:
        scen = self.scenario
        if not isinstance(scen, dict):
            logger.warning("_save_state_thread skipped: scenario dict missing")
            return
        await asyncio.to_thread(
            save_state,
            self.world,
            self.chat_history,
            self.district_index,
            self.districts,
            self.generation,
            scenario=scen,
            system_configuration=self.system_configuration,
            flush_mode=flush_mode,
        )

    # --- running flag delegates to TaskManager (single source of truth) ---
    @property
    def running(self) -> bool:
        return self.tasks.running

    @running.setter
    def running(self, value: bool) -> None:
        self.tasks.running = value

    # --- Delegation methods for backward-compat with main.py callers ---

    def update_trace_snapshot(self, **kwargs: Any) -> None:
        """Merge keys for the heartbeat thread (no asyncio; safe from any thread context that holds the GIL briefly)."""
        self._trace_snapshot.update(kwargs)
        self._trace_snapshot["monotonic_s"] = time.monotonic()
        self._trace_snapshot["run_phase"] = self.tasks.run_phase.value

    def reset_pipeline_for_new_run(self):
        """Clear in-flight survey/refine handles when starting a new scenario."""
        self.tasks.reset_pipeline_for_new_run()
        # Also clear engine-local caches that TaskManager does not own.
        self._district_scenery_summaries.clear()
        self._district_palettes.clear()
        self._fused_seed_master_plan = None
        self.blueprint = None
        if hasattr(self, "_survey_cache"):
            del self._survey_cache
        # Clear agent memory so stale context from a prior city doesn't leak.
        for agent in (self.planner_skeleton, self.planner_refine, self.surveyor, self.urbanista):
            agent.memory.history.clear()
            agent._turn_counter = 0
        # Clear intelligence subsystems
        self.style_memory = StyleMemory()
        self.proposals = ProposalQueue()
        self._trace_snapshot = {"phase": "reset"}

    async def abort_pipeline_tasks(self):
        await self.tasks.abort_pipeline_tasks()

    async def cancel_run_task_join(self) -> None:
        await self.tasks.cancel_run_task_join()

    async def schedule_run(self) -> asyncio.Task:
        trace_event("engine", "schedule_run — creating asyncio task for run()")
        self.update_trace_snapshot(phase="schedule_run")
        return await self.tasks.schedule_run(self.run)

    async def broadcast_all_agents_idle(self) -> None:
        await self.tasks.broadcast_all_agents_idle()

    async def graceful_shutdown(self):
        await self.tasks.graceful_shutdown()

    async def run(self):
        """Infinite generation loop: discover → build → expand → repeat."""
        try:
            self.running = True
            self.tasks.run_phase = EngineRunPhase.discovering
            # Reset auto-retry state on fresh run (manual resume clears it)
            if not getattr(self, "_auto_retry_pending", False):
                self._auto_retry_count = 0
            self._auto_retry_pending = False
            self.tasks.start_token_telemetry()
            logger.info("BuildEngine started — infinite generation mode")
            scen = self.scenario
            if not isinstance(scen, dict):
                logger.error("BuildEngine.run() requires RunSession.scenario dict — stopping")
                self.running = False
                self.tasks.run_phase = EngineRunPhase.idle
                return
            log_event(
                "engine",
                "Build started",
                scenario=str(scen.get("location", "?") if isinstance(scen, dict) else "none"),
                period=str(scen.get("period", "?") if isinstance(scen, dict) else "none"),
                grid=(
                    f"{self.system_configuration.grid.world_grid_width}x"
                    f"{self.system_configuration.grid.world_grid_height}"
                ),
            )
            trace_event(
                "engine",
                "run() entered — main build loop",
                scenario=str(scen.get("location", "?") if isinstance(scen, dict) else "none"),
                districts_loaded=len(self.districts),
            )
            self.update_trace_snapshot(phase="run", step="after_start")

            if self.districts:
                ensure_district_ids(self.districts)

            # ─── PHASE 0: initial district discovery ───
            if not self.districts:
                self.update_trace_snapshot(phase="discover_districts", step="calling_generators")
                trace_event("engine", "Phase 0 — no districts loaded; starting discover_districts()")
                discovery_ok = await self.generators.discover_districts()
                if not discovery_ok:
                    trace_event(
                        "engine",
                        "Phase 0 — discover_districts failed or paused",
                        auto_retry=bool(getattr(self, "_auto_retry_pending", False)),
                    )
                    if getattr(self, "_auto_retry_pending", False):
                        self._auto_retry_pending = False
                        logger.info("Auto-retry: re-entering run() after discovery failure")
                        await self.schedule_run()
                        return
                    self.running = False
                    self.tasks.run_phase = EngineRunPhase.idle
                    return
                trace_event("engine", "Phase 0 — discover_districts finished", ok=True, districts=len(self.districts))
                self.update_trace_snapshot(phase="post_discovery", districts=len(self.districts))
            else:
                trace_event(
                    "engine",
                    "Phase 0 — skipped (districts already loaded)",
                    districts=len(self.districts),
                    district_index=self.district_index,
                )
                self.update_trace_snapshot(phase="resume_loaded_districts", districts=len(self.districts))
                self._fused_seed_master_plan = None
                if self.tasks.map_refine_task_idle():
                    cached = load_districts_cache(expected_run_fingerprint=self.run_fingerprint)
                    map_desc = cached[1] if cached else ""
                    if not map_desc:
                        self.tasks.start_map_refine_background(
                            self.generators.refine_map_description_background()
                        )
                    asyncio.create_task(self.generators.find_map_image())

            # ─── BLUEPRINT: create city coherence data ───
            self.update_trace_snapshot(phase="blueprint", has_blueprint=self.blueprint is not None)
            if self.blueprint is None:
                # Try loading persisted blueprint first
                bp_data = load_blueprint()
                if bp_data:
                    self.blueprint = CityBlueprint.from_dict(bp_data)
                    logger.info("Blueprint restored from disk")
                else:
                    self.blueprint = self._create_blueprint()
                    if self.blueprint:
                        # Persist for resumption
                        await asyncio.to_thread(save_blueprint, self.blueprint.to_dict())
                if self.blueprint:
                    # Pre-rasterize roads as immutable infrastructure
                    road_count = self.blueprint.rasterize_roads(self.world)
                    # Apply elevation from hills data
                    elev_count = self.blueprint.populate_elevation(
                        self.world, system_configuration=self.system_configuration
                    )
                    if road_count or elev_count:
                        logger.info("Blueprint applied: %d road tiles, %d elevation tiles", road_count, elev_count)
                        # Broadcast terrain data (hills/water) for 3D terrain mesh
                        await self.broadcast({
                            "type": "terrain_data",
                            "hills": self.blueprint.hills,
                            "water": self.blueprint.water,
                            "roads": self.blueprint.roads,
                            "max_gradient": self.system_configuration.terrain.maximum_gradient_value,
                            "gradient_iterations": self.system_configuration.terrain.gradient_iterations_count,
                        })
                        # Broadcast the road tiles so clients see them immediately
                        road_tiles = [t.to_dict() for t in self.world.tiles.values()
                                      if t.terrain == "road" and t.building_type == "road"]
                        if road_tiles:
                            await self.broadcast({
                                "type": "tile_update", "tiles": road_tiles,
                                "turn": self.world.turn,
                            })

            await self._save_state_thread(flush_mode="full")
            trace_event("engine", "Entering infinite generation loop", generation=self.generation, district_index=self.district_index)
            self.update_trace_snapshot(phase="generation_loop", step="pre_while")

            # ─── INFINITE GENERATION LOOP ───
            while self.running:
                self.tasks.run_phase = EngineRunPhase.building
                self.update_trace_snapshot(phase="generation_loop", generation=self.generation, district_index=self.district_index)
                trace_event(
                    "engine",
                    "Generation loop iteration — calling _build_generation()",
                    generation=self.generation,
                    district_index=self.district_index,
                    districts_total=len(self.districts),
                )
                # Build all unbuilt districts (two-wave)
                build_ok = await self._build_generation()
                if not self.running or not build_ok:
                    break

                await self.broadcast({"type": "generation_complete", "generation": self.generation})
                log_event("engine", f"Generation {self.generation} complete — {len(self.districts)} districts")
                trace_event(
                    "engine",
                    f"Generation {self.generation} wave build finished",
                    generation=self.generation,
                    districts=len(self.districts),
                )
                self.update_trace_snapshot(phase="post_build_generation", generation=self.generation)

                # Check generation cap
                max_gen_cap = self.system_configuration.grid.maximum_generations_cap
                if max_gen_cap > 0 and self.generation >= max_gen_cap:
                    logger.info("Reached maximum_generations_cap=%s, stopping", max_gen_cap)
                    break

                # ─── EXPANSION: discover new edge districts ───
                self.tasks.run_phase = EngineRunPhase.discovering
                trace_event("engine", "Calling expand_city()", generation_before=self.generation)
                self.update_trace_snapshot(phase="expand_city", generation=self.generation)
                await self.broadcast({"type": "expanding", "generation": self.generation + 1})
                expanded = await self.generators.expand_city()
                if not expanded:
                    trace_event("engine", "expand_city returned False — cooldown before retry", generation=self.generation)
                    self.update_trace_snapshot(phase="expansion_cooldown", generation=self.generation)
                    await self._chat("cartographus", "info",
                                     "City fully built for this era. Waiting before next expansion attempt...")
                    await asyncio.sleep(self.system_configuration.timing.expansion_cooldown_seconds)
                    continue

                trace_event("engine", "expand_city returned True — incrementing generation", districts=len(self.districts))
                self.generation += 1
                self.update_trace_snapshot(phase="post_expansion", generation=self.generation)
                await self._save_state_thread(flush_mode="full")

            await self.tasks.await_map_refine_task()
            await self.broadcast_all_agents_idle()
            self.running = False
            await self.tasks.stop_token_telemetry()
            self.tasks.run_phase = EngineRunPhase.idle

        except asyncio.CancelledError:
            self.running = False
            self.tasks.run_phase = EngineRunPhase.idle
            logger.info("BuildEngine.run cancelled")
            await self.tasks.stop_token_telemetry()
            await self.broadcast_all_agents_idle()
            raise

    async def _build_generation(self) -> bool:
        """Build all unbuilt districts in two waves. Returns False if cancelled."""
        return await run_build_generation(self)

    async def _pause_for_api_issue(self, pause_reason: str, pause_detail: str, agent_role: str):
        """Stop the build and notify clients (rate limit, API error, network, etc.).

        For retriable errors (bad_model_output, api_error, network), auto-retry
        up to AUTO_RETRY_MAX times with a countdown before falling back to a
        manual pause.
        """
        pause_detail = (pause_detail or "").strip()
        trace_event(
            "engine",
            "pause_for_api_issue invoked",
            reason=pause_reason,
            agent=agent_role,
            detail_preview=(pause_detail[:240] + "…") if len(pause_detail) > 240 else pause_detail,
        )
        self.tasks.run_phase = EngineRunPhase.paused_api
        self.update_trace_snapshot(phase="pause_api", reason=pause_reason, agent=agent_role)

        auto_retry_max = self.system_configuration.performance.maximum_retries_count
        auto_retry_delay_s = self.system_configuration.api_pause_auto_retry_delay_seconds
        retriable_reasons = self.system_configuration.api_pause_retriable_reasons_set

        if pause_reason in retriable_reasons:
            attempt = getattr(self, "_auto_retry_count", 0) + 1
            if attempt <= auto_retry_max:
                self._auto_retry_count = attempt
                logger.warning(
                    "Auto-retry %d/%d for %s (%s): %s",
                    attempt, auto_retry_max, pause_reason, agent_role,
                    pause_detail[:200] if pause_detail else "(none)",
                )
                await self.broadcast({
                    "type": "auto_retry",
                    "attempt": attempt,
                    "max_attempts": auto_retry_max,
                    "delay_s": auto_retry_delay_s,
                    "reason": pause_reason,
                    "detail": pause_detail[:400] if pause_detail else "",
                    "agent": agent_role,
                })
                await self._chat(
                    agent_role, "warning",
                    f"Error ({pause_reason}) — auto-retrying in {auto_retry_delay_s}s (attempt {attempt}/{auto_retry_max})...",
                )
                await asyncio.sleep(auto_retry_delay_s)
                # Signal auto-retry to the caller so it can re-schedule the run.
                self._auto_retry_pending = True
                return

        # Reset auto-retry counter on actual pause (manual retry resets it too)
        self._auto_retry_count = 0
        self._auto_retry_pending = False

        self.running = False
        await self.tasks.stop_token_telemetry()
        await self._save_state_thread(flush_mode="full")
        self.tasks.reset_structure_save_throttle_counter()
        await self.tasks.cancel_survey_and_refine_tasks_for_pause()
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

    def _apply_master_plan_validation(self, master_plan: list, context: str) -> list:
        cleaned = validate_master_plan(master_plan)
        if not cleaned and master_plan:
            raise AgentGenerationError(
                "unknown",
                f"{context}: no valid in-bounds tiles after validation (duplicates or out of grid).",
            )
        return cleaned

    def _create_blueprint(self) -> CityBlueprint | None:
        """Create a CityBlueprint from AI-generated geography in the planner response.
        Falls back to known_cities.json only as a reference seed, not as the primary source.
        The AI RESEARCHES the geography — it's never hardcoded."""

        scenario = self.scenario
        if not scenario:
            return None

        bp = CityBlueprint()

        # PRIMARY: Use AI-researched geography from the planner's response
        # The skeleton planner returns a "geography" field with hills, water, materials
        skeleton_result = getattr(self.generators, '_last_skeleton_result', None)
        if self.districts and skeleton_result:
            geo = skeleton_result.get("geography", {})
            if isinstance(geo, dict):
                if isinstance(geo.get("hills"), list):
                    bp.hills = geo["hills"]
                    logger.info("AI researched %d geographic features (hills/peaks)", len(bp.hills))
                if isinstance(geo.get("water"), list):
                    bp.water = geo["water"]
                    logger.info("AI researched %d water features", len(bp.water))
                bp.primary_stone = geo.get("primary_stone", "travertine")
                bp.secondary_stone = geo.get("secondary_stone", "tufa")
                bp.roof_material = geo.get("roof_material", "terracotta")

        # FALLBACK: If the AI didn't provide geography, try known_cities.json as a seed
        if not bp.hills and not bp.water:
            import json as _json
            from pathlib import Path
            city_name = scenario.get("location", "")
            known_cities_path = Path(__file__).resolve().parent.parent / "data" / "known_cities.json"
            if known_cities_path.exists():
                try:
                    known = _json.loads(known_cities_path.read_text(encoding="utf-8"))
                    if city_name in known:
                        seed = known[city_name]
                        bp.hills = seed.get("hills", [])
                        bp.water = seed.get("water", [])
                        mats = seed.get("default_materials", {})
                        bp.primary_stone = mats.get("primary_stone", bp.primary_stone)
                        bp.secondary_stone = mats.get("secondary_stone", bp.secondary_stone)
                        bp.roof_material = mats.get("roof_material", bp.roof_material)
                        logger.info("Geography fallback from known_cities.json for %s (%d hills, %d water)",
                                    city_name, len(bp.hills), len(bp.water))
                except Exception as exc:
                    logger.warning("Failed to load known_cities.json: %s", exc)

        # Enrich district_characters from discovered districts
        if self.districts:
            for d in self.districts:
                dname = d.get("name", "")
                if dname and dname not in bp.district_characters:
                    char: dict = {}
                    desc = d.get("description", "").lower()
                    if any(w in desc for w in ("monumental", "sacred", "temple", "imperial", "forum")):
                        char = {"style": "monumental", "wealth": 9, "height_range": [2, 4]}
                    elif any(w in desc for w in ("market", "commerce", "trade")):
                        char = {"style": "commercial", "wealth": 6, "height_range": [1, 3]}
                    elif any(w in desc for w in ("residential", "insula", "domus")):
                        char = {"style": "residential", "wealth": 4, "height_range": [1, 3]}
                    elif any(w in desc for w in ("military", "barracks", "wall")):
                        char = {"style": "military", "wealth": 5, "height_range": [1, 2]}
                    if char:
                        bp.district_characters[dname] = char

        return bp

    def _compute_city_center_and_radius(self) -> tuple[tuple[float, float] | None, float]:
        """Compute city center (average of all district region centers) and radius."""
        if not self.districts:
            return None, 0.0
        cx_sum, cy_sum, count = 0.0, 0.0, 0
        for d in self.districts:
            r = d.get("region", {})
            x1, y1 = r.get("x1", 0), r.get("y1", 0)
            x2, y2 = r.get("x2", 0), r.get("y2", 0)
            cx_sum += (x1 + x2) / 2
            cy_sum += (y1 + y2) / 2
            count += 1
        if count == 0:
            return None, 0.0
        center = (cx_sum / count, cy_sum / count)
        # Radius = max distance from center to any district edge
        max_dist = 0.0
        for d in self.districts:
            r = d.get("region", {})
            for corner_x, corner_y in [
                (r.get("x1", 0), r.get("y1", 0)),
                (r.get("x2", 0), r.get("y2", 0)),
            ]:
                dist = ((corner_x - center[0]) ** 2 + (corner_y - center[1]) ** 2) ** 0.5
                max_dist = max(max_dist, dist)
        return center, max_dist

    def _compute_transition_hint(self, anchor_x: int, anchor_y: int,
                                  current_district: dict) -> str:
        """Check if building is near another district's boundary. Returns compact hint or ''."""
        cur_region = current_district.get("region", {})
        cur_name = current_district.get("name", "")
        transition_dist = 3  # tiles

        for d in self.districts:
            other_name = d.get("name", "")
            if other_name == cur_name:
                continue
            r = d.get("region", {})
            ox1, oy1 = r.get("x1", 0), r.get("y1", 0)
            ox2, oy2 = r.get("x2", 0), r.get("y2", 0)
            # Check if anchor is within transition_dist of the other district's region
            if (anchor_x >= ox1 - transition_dist and anchor_x <= ox2 + transition_dist and
                    anchor_y >= oy1 - transition_dist and anchor_y <= oy2 + transition_dist):
                # Confirm it's actually near the border, not deep inside our own region
                cx1 = cur_region.get("x1", 0)
                cy1 = cur_region.get("y1", 0)
                cx2 = cur_region.get("x2", 0)
                cy2 = cur_region.get("y2", 0)
                near_own_edge = (
                    abs(anchor_x - cx1) <= transition_dist or
                    abs(anchor_x - cx2) <= transition_dist or
                    abs(anchor_y - cy1) <= transition_dist or
                    abs(anchor_y - cy2) <= transition_dist
                )
                if near_own_edge:
                    other_style = ""
                    if self.blueprint:
                        char = self.blueprint.district_characters.get(other_name, {})
                        other_style = char.get("style", "")
                    style_note = f" ({other_style})" if other_style else ""
                    return f"TRANSITION: near {other_name}{style_note} — blend styles"
        return ""

    async def _build_district(self, district: dict, master_plan: list) -> bool:
        return await run_district_build(self, district, master_plan)

    async def _execute_batch_urbanista(
        self,
        wu_idx: int,
        work_unit: dict,
        urban_jobs: list[dict],
    ) -> tuple[int, list[tuple[int, dict | BaseException]]]:
        """Execute a batched Urbanista call for 2-3 small buildings."""
        return await execute_batch_urbanista(self, wu_idx, work_unit, urban_jobs)

    async def _chat(self, sender, msg_type, content, approved=None):
        msg = BusMessage(sender=sender, msg_type=msg_type, content=content, turn=self.world.turn)
        await self.bus.publish(msg)
        data = {"type": "chat", "sender": sender, "msg_type": msg_type, "content": content, "turn": self.world.turn}
        if approved is not None:
            data["approved"] = approved
        await self.broadcast(data)

    async def _set_status(self, agent, status, detail=None):
        payload = {"type": "agent_status", "agent": agent, "status": status}
        if status == "thinking":
            if agent not in self.tasks._agent_thinking_started:
                self.tasks._agent_thinking_started[agent] = time.time()
            payload["thinking_started_at_s"] = self.tasks._agent_thinking_started[agent]
        else:
            self.tasks._agent_thinking_started.pop(agent, None)
        if detail is not None:
            d = str(detail).strip()
            payload["detail"] = d[:280] if d else ""
        elif status == "idle":
            payload["detail"] = ""
        await self.broadcast(payload)
