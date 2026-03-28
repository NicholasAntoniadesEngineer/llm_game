"""BuildEngine — Fully autonomous, agents discover and build everything."""

import asyncio
import json
import logging

from world.state import WorldState
from orchestration.bus import MessageBus, BusMessage
from agents.base import BaseAgent
from agents.prompts import (
    CARTOGRAPHUS_PLAN, CARTOGRAPHUS_SURVEY,
    URBANISTA, HISTORICUS,
)
from config import STEP_DELAY, SCENARIO, CLAUDE_MODEL, CLAUDE_MODEL_FAST, GRID_WIDTH, GRID_HEIGHT
from persistence import save_state, save_districts_cache, load_districts_cache

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

        # Core agents — only Urbanista uses sonnet (spatial reasoning critical)
        self.planner = BaseAgent("cartographus", "Cartographus", CARTOGRAPHUS_PLAN, CLAUDE_MODEL)
        self.surveyor = BaseAgent("cartographus", "Cartographus", CARTOGRAPHUS_SURVEY, CLAUDE_MODEL)
        self.historicus = BaseAgent("historicus", "Historicus", HISTORICUS, CLAUDE_MODEL)
        self.urbanista = BaseAgent("urbanista", "Urbanista", URBANISTA, "sonnet")

    async def run(self):
        self.running = True
        logger.info("BuildEngine started — Ave Roma!")
        await asyncio.sleep(2)

        # ─── PHASE 0: Cartographus discovers the districts ───
        if not self.districts:
            await self._discover_districts()

        # ─── Build each district ───
        while self.running and self.district_index < len(self.districts):
            district = self.districts[self.district_index]
            logger.info(f"=== District: {district['name']} ===")

            self.world.current_period = district.get("period", "")
            self.world.current_year = district.get("year", -44)

            await self.broadcast({"type": "phase", "district": district["name"], "description": district.get("description", "")})
            await self.broadcast({"type": "timeline", "period": district.get("period", ""), "year": district.get("year", -44)})

            await self._build_district(district)

            self.district_index += 1
            save_state(self.world, self.chat_history, self.district_index, self.districts)
            logger.info(f"=== Completed: {district['name']} ===")

        if self.running:
            await self.broadcast({"type": "complete"})
        self.running = False

    async def _discover_districts(self):
        """Cartographus researches and decides what districts to build. Uses cache if available."""
        # Try loading from cache first
        cached = load_districts_cache()
        if cached:
            self.districts, map_desc = cached
            logger.info(f"Using cached districts: {len(self.districts)}")
            await self._chat("cartographus", "research",
                f"Using cached survey of {SCENARIO['location']} — {len(self.districts)} districts mapped.")
            if map_desc:
                await self.broadcast({"type": "map_description", "description": map_desc})
            asyncio.create_task(self._find_map_image())
            return

        # No cache — run the full discovery
        await self.broadcast({
            "type": "loading",
            "agent": "cartographus",
            "message": f"Researching the historical layout of {SCENARIO['location']}...",
        })
        await self._chat("cartographus", "research",
            f"Beginning archaeological survey of {SCENARIO['location']} ({SCENARIO['period']}). "
            f"Consulting Grokepedia and academic sources to map the city layout...")
        await self._set_status("cartographus", "thinking")
        result = await self.planner.generate(
            f"Research and map the city of {SCENARIO['location']} during {SCENARIO['period']}.\n"
            f"Time span: {SCENARIO['year_start']} to {SCENARIO['year_end']}.\n"
            f"Ruler context: {SCENARIO['ruler']}.\n"
            f"Grid size: {GRID_WIDTH}x{GRID_HEIGHT} (each tile ≈ 10 meters).\n\n"
            f"Based on your knowledge of the REAL historical and archaeological layout, "
            f"plan the districts of this city. Decide what areas exist, where they go on the grid, "
            f"and what major structures belong in each. YOU are the expert — research and decide."
        )
        await self._set_status("cartographus", "speaking")
        await self._chat("cartographus", "research", result.get("commentary", "Research complete."))
        await self._set_status("cartographus", "idle")

        self.districts = result.get("districts", [])
        logger.info(f"Discovered {len(self.districts)} districts")

        # Cache for next restart
        map_desc = result.get("map_description", "")
        save_districts_cache(self.districts, map_desc)

        if map_desc:
            await self.broadcast({"type": "map_description", "description": map_desc})

        asyncio.create_task(self._find_map_image())

        if not self.districts:
            logger.error("No districts discovered!")
            self.running = False

    async def _build_district(self, district: dict):
        region = district.get("region", {"x1": 0, "y1": 0, "x2": 10, "y2": 10})
        region_str = f"x={region['x1']}-{region['x2']}, y={region['y1']}-{region['y2']}"
        existing = self.world.get_region_summary(region["x1"], region["y1"], region["x2"], region["y2"])

        # ─── Cartographus surveys the specific district ───
        await self.broadcast({
            "type": "loading",
            "agent": "cartographus",
            "message": f"Surveying {district['name']}...",
        })
        await self._set_status("cartographus", "thinking")
        survey = await self.surveyor.generate(
            f"Survey: {district['name']}\n"
            f"Description: {district.get('description', '')}\n"
            f"Grid region: {region_str} (each tile ≈ 10 meters, full grid is {GRID_WIDTH}x{GRID_HEIGHT})\n"
            f"Period: {district.get('period', '')}, Year: {district.get('year', '')}\n"
            f"Known buildings: {', '.join(district.get('buildings', []))}\n"
            f"Already built in nearby areas:\n{existing}\n\n"
            f"Map exact positions for EVERY structure, road, and open space.\n"
            f"- Place roads (1-2 tiles wide) connecting buildings and district edges so they link to adjacent districts.\n"
            f"- Leave open plazas/forum areas (3x3+ tiles) — they are NOT filled with buildings.\n"
            f"- Space buildings realistically with gaps for streets between them."
        )
        await self._set_status("cartographus", "speaking")
        await self._chat("cartographus", "survey", survey.get("commentary", "Survey complete."))
        await self._set_status("cartographus", "idle")

        master_plan = survey.get("master_plan", [])
        if not master_plan:
            return

        logger.info(f"Master plan: {len(master_plan)} structures")
        await self.broadcast({"type": "master_plan", "plan": master_plan})

        # ─── Build each structure — maximum parallelism ───
        for structure in master_plan:
            if not self.running:
                break

            name = structure.get("name", "Structure")
            btype = structure.get("building_type", "building")
            tiles = structure.get("tiles", [])
            desc = structure.get("description", "")
            hist_note = structure.get("historical_note", "")

            if not tiles:
                continue

            # ─── STEP 1: Historicus describes the building ───
            await self._set_status("historicus", "thinking")
            hist_result = await self.historicus.generate(
                f"Describe and fact-check: {name} ({btype})\n"
                f"In {district['name']}, year {district.get('year', '')} ({district.get('period', '')})\n"
                f"Context: {desc}\nInclude detailed physical appearance for the Architect."
            )

            hist_desc = hist_result.get("commentary", "")
            await self._set_status("historicus", "speaking")
            await self._chat("historicus", "fact_check", hist_desc, approved=hist_result.get("approved", True))
            await self._set_status("historicus", "idle")

            # ─── WAVE 2: Urbanista sculpts using historian's description ───
            # Calculate actual footprint for multi-tile buildings
            xs = [t["x"] for t in tiles]
            ys = [t["y"] for t in tiles]
            tile_w = max(xs) - min(xs) + 1
            tile_d = max(ys) - min(ys) + 1
            footprint_w = round(tile_w * 0.9, 2)  # world units
            footprint_d = round(tile_d * 0.9, 2)
            anchor_x, anchor_y = min(xs), min(ys)

            # Merge historian's description for maximum context
            physical_desc = hist_desc
            hist_detail = hist_result.get("historical_note", "")
            if hist_detail:
                physical_desc += f"\n\nArchaeological detail: {hist_detail}"

            await self._set_status("urbanista", "thinking")
            arch_result = await self.urbanista.generate(
                f"Design: {name}\nType: {btype}\n"
                f"Footprint: {tile_w}x{tile_d} tiles = {footprint_w}x{footprint_d} world units\n"
                f"Anchor tile: ({anchor_x}, {anchor_y})\n"
                f"All tiles: {json.dumps(tiles)}\n\n"
                f"HISTORIAN'S PHYSICAL DESCRIPTION (match this closely):\n{physical_desc}\n\n"
                f"Surveyor context: {desc}\n\n"
                f"IMPORTANT: Scale all component dimensions to fit a {footprint_w}x{footprint_d} footprint.\n"
                f"- Column radius should be ~{round(footprint_w / 60, 3)} for proportional columns\n"
                f"- Total height should be {round(footprint_w * 0.7, 2)} to {round(footprint_w * 1.1, 2)}\n"
                f"- Set spec.anchor on EVERY tile to {{\"x\":{anchor_x},\"y\":{anchor_y}}}"
            )
            await self._set_status("urbanista", "speaking")
            await self._chat("urbanista", "design", arch_result.get("commentary", "Design ready."))
            await self._set_status("urbanista", "idle")

            # Validate Urbanista output
            for td in arch_result.get("tiles", []):
                spec = td.get("spec", {})
                comps = spec.get("components", [])
                if comps:
                    comp_types = [c.get("type") for c in comps]
                    total_h = sum(c.get("height", c.get("storyHeight", 0) * c.get("stories", 1)) for c in comps if c.get("type") not in ("door", "pilasters", "awning", "battlements"))
                    if total_h > footprint_w * 2:
                        logger.warning(f"[{name}] Total height {total_h:.2f} exceeds 2x footprint width {footprint_w}. Building may look wrong.")
                    if not any(t in comp_types for t in ["colonnade", "block", "walls", "arcade"]):
                        logger.warning(f"[{name}] No structural component found — building will be flat.")

            # Place tiles — ensure multi-tile buildings have anchors
            final_tiles = arch_result.get("tiles", [])
            if not final_tiles:
                terrain = btype if btype in ("road", "water", "garden", "forum", "grass", "wall") else "building"
                final_tiles = [
                    {"x": t["x"], "y": t["y"], "terrain": terrain,
                     "building_name": name, "building_type": btype, "description": desc}
                    for t in tiles
                ]

            # Inject anchors for multi-tile buildings if AI didn't set them
            if len(tiles) > 1:
                for td in final_tiles:
                    if not td.get("spec"):
                        td["spec"] = {}
                    if not td["spec"].get("anchor"):
                        td["spec"]["anchor"] = {"x": anchor_x, "y": anchor_y}

            placed = []
            for td in final_tiles:
                x, y = td.get("x"), td.get("y")
                if x is not None and y is not None:
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

            # Save after every building so progress survives crashes
            self.world.turn += 1
            save_state(self.world, self.chat_history, self.district_index, self.districts)

            await asyncio.sleep(STEP_DELAY)

    async def _find_map_image(self):
        """Provide a known reliable map of ancient Rome."""
        try:
            # Stanford ORBIS / Digital Augustan Rome — reliable academic sources
            known_maps = {
                "Rome": {
                    "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/88/Plan_de_Rome.jpg/1280px-Plan_de_Rome.jpg",
                    "source": "Paul Bigot's scale model plan of ancient Rome (Universit\u00e9 de Caen)"
                },
            }
            location = SCENARIO.get("location", "Rome")
            entry = known_maps.get(location, known_maps["Rome"])
            await self.broadcast({
                "type": "map_image",
                "url": entry["url"],
                "source": entry["source"],
            })
            logger.info(f"Map image: {entry['source']}")
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
