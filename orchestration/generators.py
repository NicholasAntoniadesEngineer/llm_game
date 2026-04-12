"""Generators — extracted generation/discovery methods from BuildEngine."""

import asyncio
import json
import logging
from collections import deque

from core.errors import AgentGenerationError
from core import config as config_module
from core.config import (
    GRID_WIDTH,
    GRID_HEIGHT,
    SURVEY_BUILDINGS_PER_CHUNK,
    TERRAIN_GRADIENT_ITERATIONS,
    TERRAIN_MAX_GRADIENT,
)
from core.run_log import trace_event
from core.fingerprint import district_survey_key, ensure_district_ids
from core.persistence import (
    load_districts_cache,
    load_surveys_cache,
    save_districts_cache,
    save_state,
    save_surveys_cache,
)
from orchestration.spatial import enforce_spacing, get_district_spacing, occupancy_summary_for_survey

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

    async def _chat(self, *args, **kwargs):
        return await self.engine._chat(*args, **kwargs)

    async def _set_status(self, *args, **kwargs):
        return await self.engine._set_status(*args, **kwargs)

    async def _pause_for_api_issue(self, *args, **kwargs):
        return await self.engine._pause_for_api_issue(*args, **kwargs)

    def _apply_master_plan_validation(self, *args, **kwargs):
        return self.engine._apply_master_plan_validation(*args, **kwargs)

    def _district_style(self, district: dict) -> str | None:
        """Extract district style from blueprint or infer from description."""
        name = district.get("name", "")
        bp = self.engine.blueprint
        if bp and name in bp.district_characters:
            return bp.district_characters[name].get("style")
        # Infer from description
        desc = district.get("description", "").lower()
        if any(w in desc for w in ("monumental", "sacred", "temple", "imperial", "forum")):
            return "monumental"
        if any(w in desc for w in ("market", "commerce", "trade")):
            return "commercial"
        if any(w in desc for w in ("residential", "insula", "domus")):
            return "residential"
        if any(w in desc for w in ("garden", "park", "grove")):
            return "garden"
        return None

    # ── Road connectivity ─────────────────────────────────────────────

    @staticmethod
    def _ensure_road_connectivity(master_plan: list, region: dict) -> list:
        """Ensure road tiles form a connected graph; add bridging tiles if needed.

        1. Build a graph of all road tiles and find connected components.
        2. If isolated road segments exist, connect them via shortest Manhattan bridges.
        3. Ensure at least one road tile touches the region boundary (inter-district link).

        Modifies master_plan in-place and returns it.
        """
        # Collect all road tile coords
        road_coords: set[tuple[int, int]] = set()
        non_road_coords: set[tuple[int, int]] = set()
        for struct in master_plan:
            btype = struct.get("building_type", "")
            for t in struct.get("tiles", []):
                try:
                    x, y = int(t["x"]), int(t["y"])
                except (KeyError, TypeError, ValueError):
                    continue
                if btype == "road":
                    road_coords.add((x, y))
                else:
                    non_road_coords.add((x, y))

        if len(road_coords) < 2:
            return master_plan

        # BFS to find connected components (4-connected)
        visited: set[tuple[int, int]] = set()
        components: list[set[tuple[int, int]]] = []
        for start in road_coords:
            if start in visited:
                continue
            comp: set[tuple[int, int]] = set()
            queue = deque([start])
            while queue:
                cx, cy = queue.popleft()
                if (cx, cy) in visited:
                    continue
                visited.add((cx, cy))
                comp.add((cx, cy))
                for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                    nb = (cx + dx, cy + dy)
                    if nb in road_coords and nb not in visited:
                        queue.append(nb)
            if comp:
                components.append(comp)

        if len(components) <= 1:
            # Already connected -- check boundary connectivity
            Generators._ensure_boundary_road(master_plan, road_coords, non_road_coords, region)
            return master_plan

        # Connect components: greedily bridge closest pairs
        bridge_tiles: list[dict] = []
        # Sort components by size descending so largest is the "trunk"
        components.sort(key=len, reverse=True)
        trunk = components[0]
        for comp in components[1:]:
            # Find closest pair between trunk and this component
            best_dist = float("inf")
            best_pair = None
            for tx, ty in trunk:
                for cx, cy in comp:
                    d = abs(tx - cx) + abs(ty - cy)
                    if d < best_dist:
                        best_dist = d
                        best_pair = ((tx, ty), (cx, cy))
            if best_pair is None:
                continue
            (ax, ay), (bx, by) = best_pair
            # Build Manhattan path: horizontal then vertical
            x, y = ax, ay
            while x != bx:
                x += 1 if bx > x else -1
                pos = (x, y)
                if pos not in road_coords and pos not in non_road_coords:
                    bridge_tiles.append({"x": x, "y": y, "elevation": 0.1})
                    road_coords.add(pos)
            while y != by:
                y += 1 if by > y else -1
                pos = (x, y)
                if pos not in road_coords and pos not in non_road_coords:
                    bridge_tiles.append({"x": x, "y": y, "elevation": 0.1})
                    road_coords.add(pos)
            trunk |= comp

        if bridge_tiles:
            master_plan.append({
                "name": "Connecting road",
                "building_type": "road",
                "tiles": bridge_tiles,
                "description": "Road segment connecting isolated street sections.",
            })
            logger.info("Road connectivity: added %d bridge tiles across %d isolated segments",
                        len(bridge_tiles), len(components) - 1)

        Generators._ensure_boundary_road(master_plan, road_coords, non_road_coords, region)
        return master_plan

    @staticmethod
    def _ensure_boundary_road(
        master_plan: list,
        road_coords: set[tuple[int, int]],
        non_road_coords: set[tuple[int, int]],
        region: dict,
    ) -> None:
        """Ensure at least one road tile touches the region boundary for inter-district links."""
        x1 = region.get("x1", 0)
        y1 = region.get("y1", 0)
        x2 = region.get("x2", GRID_WIDTH - 1)
        y2 = region.get("y2", GRID_HEIGHT - 1)

        # Check if any road tile is on the boundary
        for rx, ry in road_coords:
            if rx == x1 or rx == x2 or ry == y1 or ry == y2:
                return  # Already connected to boundary

        # Find road tile closest to any boundary edge and extend to it
        best_road = None
        best_dist = float("inf")
        best_edge_pos = None
        for rx, ry in road_coords:
            for edge_val, axis in [(x1, "x_min"), (x2, "x_max"), (y1, "y_min"), (y2, "y_max")]:
                if axis.startswith("x"):
                    d = abs(rx - edge_val)
                    target = (edge_val, ry)
                else:
                    d = abs(ry - edge_val)
                    target = (rx, edge_val)
                if d < best_dist:
                    best_dist = d
                    best_road = (rx, ry)
                    best_edge_pos = target

        if best_road is None or best_edge_pos is None or best_dist == 0:
            return

        # Build path from best_road to best_edge_pos
        edge_tiles: list[dict] = []
        x, y = best_road
        tx, ty = best_edge_pos
        while x != tx:
            x += 1 if tx > x else -1
            if (x, y) not in road_coords and (x, y) not in non_road_coords:
                edge_tiles.append({"x": x, "y": y, "elevation": 0.1})
        while y != ty:
            y += 1 if ty > y else -1
            if (x, y) not in road_coords and (x, y) not in non_road_coords:
                edge_tiles.append({"x": x, "y": y, "elevation": 0.1})

        if edge_tiles:
            master_plan.append({
                "name": "District edge road",
                "building_type": "road",
                "tiles": edge_tiles,
                "description": "Road extending to district boundary for inter-district connectivity.",
            })
            logger.info("Boundary road: added %d tiles connecting to district edge", len(edge_tiles))

    # ── Extracted methods ──────────────────────────────────────────────

    async def discover_districts(self) -> bool:
        """Load cached layout, or run phase-1 skeleton planner then background map refine."""
        scen = self.engine.scenario
        if not isinstance(scen, dict):
            logger.error("discover_districts: RunSession.scenario is missing or not a dict")
            return False
        run_fp = self.engine.run_fingerprint
        cached = load_districts_cache(expected_run_fingerprint=run_fp)
        if cached:
            self.districts, map_desc = cached
            ensure_district_ids(self.districts)
            self._fused_seed_master_plan = None
            logger.info(f"Using cached districts: {len(self.districts)}")
            trace_event("discovery", "Using cached districts layout", districts=len(self.districts))
            self.engine.update_trace_snapshot(phase="discover_cached", districts=len(self.districts))
            await self._chat("cartographus", "research",
                f"Using cached survey of {scen['location']} — {len(self.districts)} districts mapped.")
            if map_desc:
                await self.broadcast({"type": "map_description", "description": map_desc})
            asyncio.create_task(self.find_map_image())
            return True

        trace_event("discovery", "No district cache — running skeleton planner (long-running)", location=scen.get("location", ""))
        self.engine.update_trace_snapshot(phase="skeleton_planner", step="before_generate")

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
        plan_prompt = (
            f"Research and map the city of {scen['location']} during {scen['period']}.\n"
            f"Time span: {scen['year_start']} to {scen['year_end']}.\n"
            f"Ruler context: {scen['ruler']}.\n\n"
            f"ABOUT THIS CITY:\n{scen.get('description', '')}\n"
            f"Key features: {scen.get('features', '')}\n"
            f"Layout notes: {scen.get('grid_note', '')}\n\n"
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
                trace_event("discovery", "Calling planner_skeleton.generate() for district skeleton", attempt=attempt)
                self.engine.update_trace_snapshot(phase="skeleton_planner", step="generate_in_progress", attempt=attempt)
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
                    await self._pause_for_api_issue(
                        "network",
                        "Skeleton planner timed out after 5 minutes (300s wait limit).",
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
        self.engine.update_trace_snapshot(phase="skeleton_planner", step="after_generate", districts=len(result.get("districts") or []))
        logger.info(
            "Skeleton planner result keys=%s districts_count=%s commentary_len=%s",
            sorted(result.keys()),
            len(result.get("districts") or []),
            len(result.get("commentary") or ""),
        )
        await self._chat("cartographus", "research", result.get("commentary", "District layout established."))
        await self._set_status("cartographus", "idle")

        self.districts = result.get("districts", [])
        # Store full result so _create_blueprint can access AI-generated geography
        self._last_skeleton_result = result
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

        ensure_district_ids(self.districts)

        seed = result.get("seed_master_plan")
        if isinstance(seed, list) and len(seed) > 0:
            self._fused_seed_master_plan = seed
            logger.info("Fused seed_master_plan from skeleton — skipping survey API for first district if valid.")
        else:
            self._fused_seed_master_plan = None

        save_districts_cache(self.districts, "", run_fingerprint=self.engine.run_fingerprint)
        self.engine.tasks.start_map_refine_background(self.refine_map_description_background())
        logger.info("Map refine started immediately after skeleton (non-blocking).")
        asyncio.create_task(self.find_map_image())
        return True

    async def expand_city(self) -> bool:
        """Discover new districts at the city edges. Returns True if new districts found."""
        scenario = self.engine.scenario
        if not scenario:
            return False

        trace_event("expansion", "expand_city() start", generation=self.engine.generation, districts=len(self.districts))
        self.engine.update_trace_snapshot(phase="expand_city", step="start")

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
        bp = self.engine.blueprint
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
            GENERATION=self.engine.generation,
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
        await self._chat("cartographus", "info", f"Expanding city — generation {self.engine.generation + 1}...")

        # Use skeleton planner agent for expansion (same LLM routing)
        try:
            trace_event("expansion", "Calling planner_skeleton.generate() for expansion")
            self.engine.update_trace_snapshot(phase="expand_city", step="planner_generate")
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
            self.engine.update_trace_snapshot(phase="expand_city", step="no_new_districts")
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
            nd["expansion_generation"] = self.engine.generation + 1
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
            run_fingerprint=self.engine.run_fingerprint,
        )
        # Align index.json with expanded district list (crash safety before new tiles)
        await asyncio.to_thread(
            save_state,
            self.engine.world,
            self.engine.chat_history,
            self.engine.district_index,
            self.engine.districts,
            self.engine.generation,
            scenario=self.engine.scenario,
            flush_mode="full",
        )

        # Start surveys for new districts
        self.engine.tasks.start_survey_tasks_from_index(self.engine.district_index, len(self.districts))

        logger.info(f"Expansion: +{len(validated)} districts, total now {len(self.districts)}")
        trace_event("expansion", "expand_city() validated new districts", added=len(validated), total_districts=len(self.districts))
        self.engine.update_trace_snapshot(phase="expand_city", step="done", added=len(validated))

        # Send updated world bounds so client can expand its grid
        await self.broadcast(self.world.to_dict())

        # Broadcast updated terrain_data if blueprint exists so 3D terrain mesh
        # extends to cover the new district regions
        if bp and (bp.hills or bp.water):
            # Apply elevation to any newly created tiles in expanded area
            elev_count = bp.apply_elevation_to_world(self.world)
            if elev_count:
                logger.info("Expansion: applied elevation to %d new tiles", elev_count)
            await self.broadcast({
                "type": "terrain_data",
                "hills": bp.hills,
                "water": bp.water,
                "roads": bp.roads,
                "max_gradient": TERRAIN_MAX_GRADIENT,
                "gradient_iterations": TERRAIN_GRADIENT_ITERATIONS,
            })

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
            scen = self.engine.scenario
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
                    run_fingerprint=self.engine.run_fingerprint,
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
            if not hasattr(self.engine, "_survey_cache"):
                self.engine._survey_cache = load_surveys_cache(
                    expected_run_fingerprint=self.engine.run_fingerprint,
                )
            if survey_sid in self.engine._survey_cache:
                cached_plan = self.engine._survey_cache[survey_sid]
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
                gap = get_district_spacing(self._district_style(district))
                master_plan = enforce_spacing(master_plan, min_gap=gap)
                region = district.get("region", {"x1": 0, "y1": 0, "x2": 10, "y2": 10})
                master_plan = self._ensure_road_connectivity(master_plan, region)
                master_plan = self._apply_master_plan_validation(
                    master_plan, f"Fused seed {district_key!r}"
                )
                async with self._survey_cache_lock:
                    self.engine._survey_cache[survey_sid] = master_plan
                    await asyncio.to_thread(
                        save_surveys_cache,
                        self.engine._survey_cache,
                        run_fingerprint=self.engine.run_fingerprint,
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
            self.engine._survey_cache[survey_sid] = master_plan
            await asyncio.to_thread(
                save_surveys_cache,
                self.engine._survey_cache,
                run_fingerprint=self.engine.run_fingerprint,
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
                self._district_scenery_summaries[survey_sid] = scenery_sum
            palette = survey.get("suggested_palette")
            if isinstance(palette, dict):
                self._district_palettes[survey_sid] = palette
                logger.info("District %s palette: %s", district_key, palette)
            gap = get_district_spacing(self._district_style(district))
            mp = enforce_spacing(master_plan, min_gap=gap)
            region = district.get("region", {"x1": 0, "y1": 0, "x2": 10, "y2": 10})
            mp = self._ensure_road_connectivity(mp, region)
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

        gap = get_district_spacing(self._district_style(district))
        mp = enforce_spacing(merged, min_gap=gap)
        region = district.get("region", {"x1": 0, "y1": 0, "x2": 10, "y2": 10})
        mp = self._ensure_road_connectivity(mp, region)
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
        scen = self.engine.scenario
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
            scen = self.engine.scenario
            location = scen.get("location", "Rome") if isinstance(scen, dict) else "Rome"
            if location in known_maps:
                url, source = known_maps[location]
                await self.broadcast({"type": "map_image", "url": url, "source": source})
                logger.info(f"Map image: {source}")
        except Exception as e:
            logger.warning(f"Map image failed: {e}")
