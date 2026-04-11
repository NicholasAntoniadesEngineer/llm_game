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

# Master-plan building_types that are rendered as procedural terrain (no Urbanista components).
OPEN_TERRAIN_TYPES = frozenset({"road", "forum", "garden", "water", "grass"})

# Small/simple building types eligible for batching (2-3 per Urbanista call).
# These have simpler geometry and shorter outputs, making batching safe.
BATCHABLE_TYPES = frozenset({"taberna", "warehouse", "insula", "domus", "market"})

# Max buildings per batch. Keep small to avoid overly complex responses.
MAX_BATCH_SIZE = 3

# Max total tiles across a batch — larger buildings should not be batched.
MAX_BATCH_TILES = 12


# ── Default terrain colors/scenery by type (used when skipping Urbanista for terrain) ──
_TERRAIN_DEFAULTS = {
    "road": {"color": "#8B8680", "scenery": {"pavement_detail": 0.6}},
    "forum": {"color": "#C8B89A", "scenery": {"pavement_detail": 0.8, "vegetation_density": 0.1}},
    "garden": {"color": "#4A7C3F", "scenery": {"vegetation_density": 0.7}},
    "water": {"color": "#2E5A88", "scenery": {"water_murk": 0.3}},
    "grass": {"color": "#6B8E4E", "scenery": {"vegetation_density": 0.5}},
}


def _generate_terrain_procedurally(
    name: str,
    btype: str,
    tiles: list,
    avg_elevation: float,
    district_palette: dict | None,
    physical_desc: str,
) -> dict:
    """Generate terrain tiles without an LLM call.

    Road, forum, garden, water, grass tiles use simple color + scenery spec.
    This saves one Urbanista system prompt per terrain structure.
    """
    defaults = _TERRAIN_DEFAULTS.get(btype, {"color": "#808080", "scenery": {}})
    color = defaults["color"]

    # Use district palette if available (primary for roads/forums, accent for gardens)
    if district_palette and isinstance(district_palette, dict):
        if btype in ("road", "forum") and district_palette.get("primary"):
            color = district_palette["primary"]
        elif btype in ("garden", "grass") and district_palette.get("accent"):
            # Blend toward green for vegetation
            pass  # Keep default green

    result_tiles = []
    for t in tiles:
        td = {
            "x": t["x"],
            "y": t["y"],
            "terrain": btype,
            "building_name": name,
            "building_type": btype,
            "description": physical_desc[:200] if physical_desc else f"{name} ({btype})",
            "elevation": t.get("elevation", avg_elevation),
            "color": color,
            "spec": {
                "color": color,
                "scenery": dict(defaults.get("scenery", {})),
            },
        }
        result_tiles.append(td)

    return {"tiles": result_tiles, "commentary": f"Procedural terrain: {name}"}

# Wave 1 (landmarks) — built first across all districts for a quick city skeleton.
# Wave 2 (infill) — fills density after the skeleton is complete.
WAVE1_TYPES = frozenset({
    "temple", "basilica", "gate", "wall", "monument", "amphitheater",
    "thermae", "circus", "bridge", "aqueduct",
    # Open terrain is always wave 1 (roads, plazas, water — defines the street grid)
    "road", "forum", "garden", "water", "grass",
})
# Everything else (insula, domus, market, taberna, warehouse) is wave 2.
from agents.llm_routing import (
    KEY_CARTOGRAPHUS_REFINE,
    KEY_CARTOGRAPHUS_SKELETON,
    KEY_CARTOGRAPHUS_SURVEY,
    KEY_URBANISTA,
)
from prompts import load_prompt, format_building_types, format_material_palette
from core import config as config_module
from core.config import (
    GRID_WIDTH,
    GRID_HEIGHT,
    URBANISTA_MAX_CONCURRENT,
    MAX_GENERATIONS,
    EXPANSION_COOLDOWN,
    TERRAIN_GRADIENT_ITERATIONS,
    TERRAIN_MAX_GRADIENT,
)
from orchestration.validation import (
    validate_master_plan,
    validate_urbanista_tiles,
    validate_urbanista_arch_result,
    sanitize_urbanista_output,
    check_component_collisions,
)
from core.errors import UrbanistaValidationError
from orchestration.placement import check_functional_placement, log_functional_placement_warnings
from orchestration.prompt_builder import build_terrain_prompt, build_building_prompt
from core.persistence import save_state, load_districts_cache, save_blueprint, load_blueprint
from orchestration.generators import Generators

logger = logging.getLogger("eternal.engine")


class BuildEngine:
    # Toolbar / start-screen status strip (must match static/tiles.js AGENT_NAMES keys).
    UI_STATUS_STRIP_AGENT_KEYS = ("cartographus", "urbanista")

    def __init__(self, world: WorldState, bus: MessageBus, broadcast_fn, chat_history_ref: list):
        self.world = world
        self.bus = bus
        self.broadcast = broadcast_fn
        self.chat_history = chat_history_ref
        self.district_index = 0
        self.districts = []  # Discovered by Cartographus, NOT hardcoded

        # Phase-1 skeleton planner starts builds early; phase-2 refine adds map prose in background.
        _source_policy = load_prompt("source_policy")
        self.planner_skeleton = BaseAgent(
            "cartographus",
            "Cartographus",
            load_prompt("cartographus_plan_skeleton", GRID_WIDTH=GRID_WIDTH, GRID_HEIGHT=GRID_HEIGHT, GRID_WIDTH_M=GRID_WIDTH*10, GRID_HEIGHT_M=GRID_HEIGHT*10, SOURCE_POLICY=_source_policy),
            llm_agent_key=KEY_CARTOGRAPHUS_SKELETON,
        )
        self.planner_refine = BaseAgent(
            "cartographus",
            "Cartographus",
            load_prompt("cartographus_plan_refine", SOURCE_POLICY=_source_policy),
            llm_agent_key=KEY_CARTOGRAPHUS_REFINE,
        )
        self.surveyor = BaseAgent(
            "cartographus",
            "Cartographus",
            load_prompt("cartographus_survey", SOURCE_POLICY=_source_policy, BUILDING_TYPES=format_building_types()),
            llm_agent_key=KEY_CARTOGRAPHUS_SURVEY,
        )
        self.urbanista = BaseAgent("urbanista", "Urbanista", load_prompt("urbanista", BUILDING_TYPES=format_building_types(), KEY_COLORS=format_material_palette()), llm_agent_key=KEY_URBANISTA)
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
        )

        # Generators — extracted discovery/survey/expansion methods.
        self.generators = Generators(self)

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
            # Reset auto-retry state on fresh run (manual resume clears it)
            if not getattr(self, "_auto_retry_pending", False):
                self._auto_retry_count = 0
            self._auto_retry_pending = False
            self.tasks.start_token_telemetry()
            logger.info("BuildEngine started — infinite generation mode")
            log_event("engine", "Build started",
                      scenario=str(config_module.SCENARIO.get("location", "?") if config_module.SCENARIO else "none"),
                      period=str(config_module.SCENARIO.get("period", "?") if config_module.SCENARIO else "none"),
                      grid=f"{GRID_WIDTH}x{GRID_HEIGHT}")
            trace_event(
                "engine",
                "run() entered — main build loop",
                scenario=str(config_module.SCENARIO.get("location", "?") if config_module.SCENARIO else "none"),
                districts_loaded=len(self.districts),
            )
            self.update_trace_snapshot(phase="run", step="after_start")

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
                if self.tasks._map_refine_task is None or self.tasks._map_refine_task.done():
                    cached = load_districts_cache()
                    map_desc = cached[1] if cached else ""
                    if not map_desc:
                        self.tasks._map_refine_task = asyncio.create_task(self.generators.refine_map_description_background())
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
                    elev_count = self.blueprint.populate_elevation(self.world)
                    if road_count or elev_count:
                        logger.info("Blueprint applied: %d road tiles, %d elevation tiles", road_count, elev_count)
                        # Broadcast terrain data (hills/water) for 3D terrain mesh
                        await self.broadcast({
                            "type": "terrain_data",
                            "hills": self.blueprint.hills,
                            "water": self.blueprint.water,
                            "roads": self.blueprint.roads,
                            "max_gradient": TERRAIN_MAX_GRADIENT,
                            "gradient_iterations": TERRAIN_GRADIENT_ITERATIONS,
                        })
                        # Broadcast the road tiles so clients see them immediately
                        road_tiles = [t.to_dict() for t in self.world.tiles.values()
                                      if t.terrain == "road" and t.building_type == "road"]
                        if road_tiles:
                            await self.broadcast({
                                "type": "tile_update", "tiles": road_tiles,
                                "turn": self.world.turn,
                            })

            await asyncio.to_thread(save_state, self.world, self.chat_history, self.district_index, self.districts, self.generation)
            trace_event("engine", "Entering infinite generation loop", generation=self.generation, district_index=self.district_index)
            self.update_trace_snapshot(phase="generation_loop", step="pre_while")

            # ─── INFINITE GENERATION LOOP ───
            while self.running:
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
                if MAX_GENERATIONS > 0 and self.generation >= MAX_GENERATIONS:
                    logger.info(f"Reached MAX_GENERATIONS={MAX_GENERATIONS}, stopping")
                    break

                # ─── EXPANSION: discover new edge districts ───
                trace_event("engine", "Calling expand_city()", generation_before=self.generation)
                self.update_trace_snapshot(phase="expand_city", generation=self.generation)
                await self.broadcast({"type": "expanding", "generation": self.generation + 1})
                expanded = await self.generators.expand_city()
                if not expanded:
                    trace_event("engine", "expand_city returned False — cooldown before retry", generation=self.generation)
                    self.update_trace_snapshot(phase="expansion_cooldown", generation=self.generation)
                    await self._chat("cartographus", "info",
                                     "City fully built for this era. Waiting before next expansion attempt...")
                    await asyncio.sleep(EXPANSION_COOLDOWN)
                    continue

                trace_event("engine", "expand_city returned True — incrementing generation", districts=len(self.districts))
                self.generation += 1
                self.update_trace_snapshot(phase="post_expansion", generation=self.generation)
                await asyncio.to_thread(save_state, self.world, self.chat_history, self.district_index, self.districts, self.generation)

            await self.tasks.await_map_refine_task()
            await self.broadcast_all_agents_idle()
            self.running = False
            await self.tasks.stop_token_telemetry()

        except asyncio.CancelledError:
            self.running = False
            logger.info("BuildEngine.run cancelled")
            await self.tasks.stop_token_telemetry()
            await self.broadcast_all_agents_idle()
            raise

    async def _build_generation(self) -> bool:
        """Build all unbuilt districts in two waves. Returns False if cancelled."""
        trace_event(
            "engine",
            "_build_generation() start",
            generation=self.generation,
            district_index=self.district_index,
            districts_total=len(self.districts),
        )
        self.update_trace_snapshot(phase="_build_generation", step="start", generation=self.generation)
        self.tasks._survey_task_by_index.clear()
        self.tasks.start_survey_tasks_from_index(self.district_index, self.district_index + 1)
        if len(self.districts) > self.district_index + 1:
            logger.info("Survey priority: district %s/%s first", self.district_index + 1, len(self.districts))

        self.tasks.start_survey_tasks_from_index(self.district_index, len(self.districts))
        district_plans: dict[int, list] = {}

        async def _get_plan(di: int) -> list | None:
            try:
                return await self.tasks.await_survey_for_district_index(di)
            except asyncio.CancelledError:
                return None
            except AgentGenerationError as err:
                await self._pause_for_api_issue(err.pause_reason, err.pause_detail, "cartographus")
                return None

        for wave_label, type_filter in [("Wave 1 — Landmarks", WAVE1_TYPES), ("Wave 2 — Infill", None)]:
            if not self.running:
                return False

            await self._chat("cartographus", "info", f"=== {wave_label} (gen {self.generation}) ===")
            log_event("engine", wave_label)

            for di in range(self.district_index, len(self.districts)):
                if not self.running:
                    return False

                district = self.districts[di]
                district_name = district["name"]
                self.world.current_period = district.get("period", "")
                self.world.current_year = district.get("year", -44)

                scenery = self._district_scenery_summaries.get(district_name, "")
                region = district.get("region", {})
                await self.broadcast({
                    "type": "phase",
                    "district": district_name,
                    "description": district.get("description", ""),
                    "scenery_summary": scenery,
                    "index": di + 1,
                    "total_districts": len(self.districts),
                    "wave": wave_label,
                    "generation": self.generation,
                    "region": {
                        "x1": region.get("x1", 0),
                        "y1": region.get("y1", 0),
                        "x2": region.get("x2", 0),
                        "y2": region.get("y2", 0),
                    },
                })
                await self.broadcast({"type": "timeline", "period": district.get("period", ""), "year": district.get("year", -44)})

                self.update_trace_snapshot(
                    phase="build_wave",
                    wave=wave_label,
                    district_index=di,
                    district=district_name,
                    generation=self.generation,
                )
                if di not in district_plans:
                    self.update_trace_snapshot(phase="await_survey", district_index=di, district=district_name, wave=wave_label)
                    trace_event(
                        "engine",
                        "Awaiting survey master plan",
                        district_index=di,
                        district=district_name,
                        wave=wave_label,
                    )
                    plan = await _get_plan(di)
                    if plan is None:
                        if getattr(self, "_auto_retry_pending", False):
                            self._auto_retry_pending = False
                            logger.info("Auto-retry: re-entering run() after survey failure")
                            await self.schedule_run()
                        return False
                    district_plans[di] = plan

                master_plan = district_plans[di]
                if type_filter is not None:
                    wave_plan = [s for s in master_plan if s.get("building_type", "") in type_filter]
                else:
                    wave_plan = [s for s in master_plan if s.get("building_type", "") not in WAVE1_TYPES]

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
                self.update_trace_snapshot(
                    phase="build_district_call",
                    district=district_name,
                    wave=wave_label,
                    structure_count=len(wave_plan),
                )
                district_ok = await self._build_district(district, wave_plan)
                if not district_ok:
                    if getattr(self, "_auto_retry_pending", False):
                        self._auto_retry_pending = False
                        logger.info("Auto-retry: re-entering run() after district build failure")
                        await self.schedule_run()
                    return False

                await asyncio.to_thread(save_state, self.world, self.chat_history, di, self.districts, self.generation)

        self.district_index = len(self.districts)
        return True

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
        self.update_trace_snapshot(phase="pause_api", reason=pause_reason, agent=agent_role)

        # Auto-retry for transient/retriable errors
        AUTO_RETRY_MAX = 3
        AUTO_RETRY_DELAY_S = 5
        RETRIABLE_REASONS = {"bad_model_output", "api_error", "network"}

        if pause_reason in RETRIABLE_REASONS:
            attempt = getattr(self, "_auto_retry_count", 0) + 1
            if attempt <= AUTO_RETRY_MAX:
                self._auto_retry_count = attempt
                logger.warning(
                    "Auto-retry %d/%d for %s (%s): %s",
                    attempt, AUTO_RETRY_MAX, pause_reason, agent_role,
                    pause_detail[:200] if pause_detail else "(none)",
                )
                # Broadcast countdown to client
                await self.broadcast({
                    "type": "auto_retry",
                    "attempt": attempt,
                    "max_attempts": AUTO_RETRY_MAX,
                    "delay_s": AUTO_RETRY_DELAY_S,
                    "reason": pause_reason,
                    "detail": pause_detail[:400] if pause_detail else "",
                    "agent": agent_role,
                })
                await self._chat(
                    agent_role, "warning",
                    f"Error ({pause_reason}) — auto-retrying in {AUTO_RETRY_DELAY_S}s (attempt {attempt}/{AUTO_RETRY_MAX})...",
                )
                await asyncio.sleep(AUTO_RETRY_DELAY_S)
                # Signal auto-retry to the caller so it can re-schedule the run.
                self._auto_retry_pending = True
                return

        # Reset auto-retry counter on actual pause (manual retry resets it too)
        self._auto_retry_count = 0
        self._auto_retry_pending = False

        self.running = False
        await self.tasks.stop_token_telemetry()
        # Always save on pause — ensures no progress is lost
        await asyncio.to_thread(
            save_state,
            self.world,
            self.chat_history,
            self.district_index,
            self.districts,
            self.generation,
        )
        self.tasks._structures_since_save = 0
        await self.tasks._cancel_survey_and_refine_tasks()
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

        scenario = config_module.SCENARIO
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
        district_key = district.get("name", "unknown")
        trace_event(
            "engine",
            "_build_district() entered",
            district=district_key,
            master_plan_structures=len(master_plan),
        )
        self.update_trace_snapshot(phase="build_district_inner", district=district_key, structures=len(master_plan))
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

        self.tasks._structures_since_save = 0

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

        # Precompute city center and radius for height gradient hints
        city_center, city_radius = self._compute_city_center_and_radius()
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

            # Compute elevation from blueprint hills (authoritative) instead of AI-specified values
            # which are on a different scale. This prevents buildings creating sinkholes in terrain.
            if self.blueprint and self.blueprint.hills:
                tile_elevations = [
                    round(self.blueprint.elevation_at(t["x"], t["y"]), 2)
                    for t in tiles
                ]
            else:
                tile_elevations = [t.get("elevation", district.get("elevation", 0.0)) for t in tiles]
            avg_elevation = round(sum(tile_elevations) / len(tile_elevations), 2) if tile_elevations else 0.0

            env_note = (structure.get("environment_note") or "").strip()

            if btype in OPEN_TERRAIN_TYPES:
                # OPTIMIZATION: Generate terrain tiles procedurally (no Urbanista LLM call).
                # Roads, forums, gardens, water, and grass use simple color + scenery spec.
                terrain_result = _generate_terrain_procedurally(
                    name=name, btype=btype, tiles=tiles,
                    avg_elevation=avg_elevation,
                    district_palette=district_palette,
                    physical_desc=physical_desc,
                )
                placed_terrain = []
                for td in terrain_result.get("tiles", []):
                    x, y = td.get("x"), td.get("y")
                    if x is not None and y is not None:
                        # Use hill gaussian for elevation (authoritative)
                        if self.blueprint and self.blueprint.hills:
                            td["elevation"] = round(self.blueprint.elevation_at(x, y), 2)
                        elif "elevation" not in td or td["elevation"] is None:
                            td["elevation"] = district.get("elevation", 0.0)
                        td["period"] = district.get("period", "")
                        td["placed_by"] = "faber"
                        td["historical_note"] = hist_note
                        if self.world.place_tile(x, y, td):
                            tile = self.world.get_tile(x, y)
                            if tile:
                                placed_terrain.append(tile.to_dict())
                if placed_terrain:
                    await self.broadcast({
                        "type": "tile_update", "tiles": placed_terrain,
                        "turn": self.world.turn,
                        "period": district.get("period", ""),
                        "year": district.get("year", ""),
                    })
                    logger.info("Placed %d terrain tiles for %s (no LLM call)", len(placed_terrain), name)
                self.world.turn += 1
                await self.tasks.persist_progress_after_structure()
                continue  # Skip Urbanista pipeline for terrain
                # NOTE: build_terrain_prompt path below is now unreachable but kept
                # as documentation of the old approach.
                prompt = build_terrain_prompt(
                    name=name, btype=btype, tiles=tiles,
                    anchor_x=anchor_x, anchor_y=anchor_y,
                    tile_w=tile_w, tile_d=tile_d,
                    footprint_w=footprint_w, footprint_d=footprint_d,
                    avg_elevation=avg_elevation,
                    city_loc=city_loc, period=scenario.get("period", ""),
                    neighbor_desc=neighbor_desc,
                    physical_desc=physical_desc,
                    env_note=env_note,
                    district_palette=district_palette,
                )
            else:
                try:
                    transition_hint = self._compute_transition_hint(anchor_x, anchor_y, district)
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
                        district_palette=district_palette,
                        world_state=self.world,
                        city_center=city_center,
                        city_radius=city_radius,
                        transition_hint=transition_hint,
                    )
                except ValueError as exc:
                    await self._pause_for_api_issue("unknown", str(exc), "urbanista")
                    return False

            # ── Inject coherence context (~100-150 tokens) ──
            context_parts = []
            if self.blueprint:
                ctx_line = self.blueprint.build_context_line(
                    self.world, anchor_x, anchor_y, district_key,
                )
                if ctx_line:
                    context_parts.append(ctx_line)
            style_ctx = self.style_memory.format_style_context()
            if style_ctx:
                context_parts.append(style_ctx)
            spatial_ctx = self.world_tools.format_context_block(anchor_x, anchor_y, btype)
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
        max_consecutive_failures = 3
        placed_count = 0

        if urban_jobs:
            await self._set_status(
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
                    job["btype"] in BATCHABLE_TYPES
                    and job_tiles <= 4  # small footprint
                ):
                    batch_indices = [i]
                    batch_tiles = job_tiles
                    j = i + 1
                    while (
                        j < len(urban_jobs)
                        and len(batch_indices) < MAX_BATCH_SIZE
                        and batch_tiles + len(urban_jobs[j]["tiles"]) <= MAX_BATCH_TILES
                        and urban_jobs[j]["btype"] in BATCHABLE_TYPES
                        and len(urban_jobs[j]["tiles"]) <= 4
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
            await self._chat(
                "urbanista",
                "info",
                f"Designing {len(urban_jobs)} structures (max {URBANISTA_MAX_CONCURRENT} concurrent)"
                + (f" — {batched_buildings} batched into {batch_count} calls" if batch_count else "")
                + " — placing as each completes...",
            )

            # Wrap each work unit to carry its index
            async def _design_work_unit(wu_idx: int, work_unit: dict) -> tuple[int, list[tuple[int, dict | BaseException]]]:
                """Returns (wu_idx, [(job_idx, result_or_error), ...])."""
                indices = work_unit["indices"]
                if work_unit["type"] == "batch" and len(indices) >= 2:
                    return await self._execute_batch_urbanista(wu_idx, work_unit, urban_jobs)
                else:
                    job_idx = indices[0]
                    try:
                        result = await self.generators.urbanista_generate_bounded(urban_jobs[job_idx]["prompt"])
                        return (wu_idx, [(job_idx, result)])
                    except BaseException as err:
                        return (wu_idx, [(job_idx, err)])

            pending = [
                asyncio.create_task(_design_work_unit(wu_i, wu))
                for wu_i, wu in enumerate(work_units)
            ]

            try:
              for coro in asyncio.as_completed(pending):
                if not self.running:
                    break

                wu_idx, job_results = await coro

                for idx, arch_result in job_results:
                    if not self.running:
                        break

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
                        # Broadcast skipped building so the client knows
                        await self.broadcast({
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

                    # ── Geometry collision check — detect and fix overlapping components ──
                    anchor_tile = None
                    for td in arch_result.get("tiles", []):
                        if isinstance(td, dict) and td.get("spec") and isinstance(td["spec"].get("components"), list):
                            anchor_tile = td
                            break
                    if anchor_tile and job["btype"] not in OPEN_TERRAIN_TYPES:
                        fp_w = round((max(t["x"] for t in tiles) - min(t["x"] for t in tiles) + 1) * 0.9, 2)
                        fp_d = round((max(t["y"] for t in tiles) - min(t["y"] for t in tiles) + 1) * 0.9, 2)
                        collisions = check_component_collisions(anchor_tile["spec"], fp_w, fp_d)
                        if collisions:
                            collision_report = "\n".join(collisions[:6])
                            logger.warning("Geometry collisions for %s: %d issues", name, len(collisions))
                            await self._chat("urbanista", "info",
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
                                    self.urbanista.generate(fix_prompt),
                                    timeout=300,
                                )
                                fixed = sanitize_urbanista_output(fixed)
                                validate_urbanista_arch_result(fixed)
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
                                        await self._chat("urbanista", "info",
                                            f"Fixed {name}: {len(collisions)}->{len(new_collisions)} collision(s)")
                                    else:
                                        logger.info("Geometry fix for %s did not improve (%d->%d) — using original", name, len(collisions), len(new_collisions))
                                else:
                                    arch_result = fixed
                            except asyncio.TimeoutError:
                                logger.warning("Geometry fix for %s timed out (5min) — using original", name)
                            except Exception as fix_err:
                                logger.warning("Geometry fix failed for %s: %s — using original", name, fix_err)

                    await self._set_status("urbanista", "speaking", detail="Sharing design commentary.")
                    commentary = arch_result.get("commentary", "Design ready.")
                    if len(commentary) > 400:
                        commentary = commentary[:397] + "..."
                    await self._chat("urbanista", "design", commentary)
                    await self._set_status(
                        "urbanista",
                        "thinking" if placed_count < len(urban_jobs) else "idle",
                        detail=(
                            f"Progress {placed_count}/{len(urban_jobs)} — {name} placed; continuing…"
                            if placed_count < len(urban_jobs)
                            else None
                        ),
                    )

                    # Place tiles
                    final_tiles = validate_urbanista_tiles(arch_result.get("tiles", []))
                    if not final_tiles:
                        skipped += 1
                        logger.warning("Urbanista returned no in-bounds tiles for %s — skipping", name)
                        await self._chat("urbanista", "info", f"Skipped {name} — no valid tiles. Continuing.")
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
                        is_terrain = job["btype"] in OPEN_TERRAIN_TYPES
                        for (mx, my) in missing_coords:
                            if is_terrain:
                                fill_elev = (
                                    round(self.blueprint.elevation_at(mx, my), 2)
                                    if self.blueprint and self.blueprint.hills
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
                    if len(tiles) > 1 and job["btype"] not in OPEN_TERRAIN_TYPES:
                        for td in final_tiles:
                            if not td.get("spec"):
                                td["spec"] = {}
                            if not td["spec"].get("anchor"):
                                td["spec"]["anchor"] = {"x": anchor_x, "y": anchor_y}

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

                    placed = []
                    district_elev = district.get("elevation", 0.0)
                    for td in final_tiles:
                        x, y = td.get("x"), td.get("y")
                        if x is not None and y is not None:
                            if self.blueprint and self.blueprint.hills:
                                td["elevation"] = round(self.blueprint.elevation_at(int(x), int(y)), 3)
                            elif "elevation" not in td or td["elevation"] is None:
                                td["elevation"] = district_elev
                            td["period"] = district.get("period", "")
                            td["placed_by"] = "faber"
                            td["historical_note"] = hist_result.get("historical_note", hist_note)
                            if self.world.place_tile(x, y, td):
                                tile = self.world.get_tile(x, y)
                                if tile:
                                    placed.append(tile.to_dict())

                    # Record design in style memory
                    for td_placed in placed:
                        if td_placed.get("spec"):
                            self.style_memory.record_design(td_placed.get("spec", {}))

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
                    await self.tasks.persist_progress_after_structure()
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

        if self.tasks._structures_since_save > 0:
            await asyncio.to_thread(
                save_state,
                self.world,
                self.chat_history,
                self.district_index,
                self.districts,
                self.generation,
            )
            self.tasks._structures_since_save = 0

        if skipped:
            logger.warning("District %s: %d/%d structures skipped due to errors", district_key, skipped, len(urban_jobs))
            await self._chat(
                "urbanista", "info",
                f"District complete — {len(urban_jobs) - skipped} placed, {skipped} skipped due to errors.",
            )

        return True

    async def _execute_batch_urbanista(
        self,
        wu_idx: int,
        work_unit: dict,
        urban_jobs: list[dict],
    ) -> tuple[int, list[tuple[int, dict | BaseException]]]:
        """Execute a batched Urbanista call for 2-3 small buildings.

        Constructs a combined prompt, calls generate_batch(), and splits
        the results back to individual jobs. On batch failure, falls back
        to individual calls so no buildings are lost.
        """
        indices = work_unit["indices"]
        jobs = [urban_jobs[i] for i in indices]
        names = [j["name"] for j in jobs]

        logger.info("Batch Urbanista: %d buildings [%s]", len(indices), ", ".join(names))

        # Build combined prompt
        batch_prompt_parts = [
            f"Design these {len(jobs)} buildings. Return a JSON array with one object per building.\n"
            f"Each object should have the same format as a single-building response "
            f"(tiles[], commentary, reference).\n\n"
        ]
        for bi, job in enumerate(jobs):
            batch_prompt_parts.append(f"--- BUILDING {bi + 1}: {job['name']} ---\n{job['prompt']}\n")

        batch_prompt = "\n".join(batch_prompt_parts)

        try:
            async with self.generators._urbanista_semaphore:
                results = await self.urbanista.generate_batch(batch_prompt, len(jobs))
        except Exception as e:
            logger.warning("Batch Urbanista call failed: %s — falling back to individual", e)
            results = []

        # If batch returned correct count, map results to jobs
        if len(results) >= len(jobs):
            logger.info("Batch Urbanista success: got %d results for %d jobs", len(results), len(jobs))
            return (wu_idx, [(indices[i], results[i]) for i in range(len(jobs))])

        if results:
            # Partial results — use what we got, fall back for the rest
            logger.warning(
                "Batch Urbanista partial: got %d results for %d jobs — using partial + fallback",
                len(results), len(jobs),
            )
            out: list[tuple[int, dict | BaseException]] = []
            for i in range(len(results)):
                out.append((indices[i], results[i]))
            for i in range(len(results), len(jobs)):
                try:
                    async with self.generators._urbanista_semaphore:
                        r = await self.urbanista.generate(urban_jobs[indices[i]]["prompt"])
                    out.append((indices[i], r))
                except BaseException as err:
                    out.append((indices[i], err))
            return (wu_idx, out)

        # Full batch failure — fall back to individual calls
        logger.warning("Batch Urbanista empty — falling back to %d individual calls", len(jobs))
        out = []
        for i, job_idx in enumerate(indices):
            try:
                r = await self.generators.urbanista_generate_bounded(urban_jobs[job_idx]["prompt"])
                out.append((job_idx, r))
            except BaseException as err:
                out.append((job_idx, err))
        return (wu_idx, out)

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
