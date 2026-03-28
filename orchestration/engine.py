"""BuildEngine — orchestrates the agent build cycle and broadcasts to WebSocket."""

import asyncio
import logging

from world.state import WorldState
from orchestration.bus import MessageBus, BusMessage
from agents.prefect import Prefect
from agents.architect import Architect
from agents.historian import Historian
from agents.builder import Builder
from agents.citizen import Citizen
from config import STEP_DELAY, MAX_REVISIONS, DISTRICTS

logger = logging.getLogger("roma.engine")


class BuildEngine:
    def __init__(self, world: WorldState, bus: MessageBus, broadcast_fn):
        self.world = world
        self.bus = bus
        self.broadcast = broadcast_fn  # async fn to send to all WebSocket clients
        self.running = False

        # Initialize agents
        self.prefect = Prefect()
        self.architect = Architect()
        self.historian = Historian()
        self.builder = Builder()
        self.citizen = Citizen()

    async def run(self):
        """Main loop — build Rome district by district."""
        self.running = True
        logger.info("BuildEngine started — Ave Roma!")

        # Small delay to let clients connect
        await asyncio.sleep(3)

        while self.running:
            district = self.prefect.current_district()
            if not district:
                logger.info("All districts complete!")
                await self.broadcast({"type": "complete"})
                break

            logger.info(f"=== Building district: {district['name']} ===")
            await self._build_district(district)
            logger.info(f"=== Completed district: {district['name']} ===")
            self.prefect.advance_district()
            self.world.turn += 1

            # Pause between districts
            await asyncio.sleep(STEP_DELAY * 2)

        self.running = False

    async def _build_district(self, district: dict):
        """Run one full build cycle for a district."""
        region = district["region"]

        # Update world timeline
        self.world.current_period = district["period"]
        self.world.current_year = district["year"]

        # --- Phase announcement ---
        await self.broadcast({
            "type": "phase",
            "district": district["name"],
            "description": district["description"],
        })
        await self.broadcast({
            "type": "timeline",
            "period": district["period"],
            "year": district["year"],
        })

        # --- 1. Prefect issues directive ---
        await self._send_typing("praefectus")
        world_context = self.world.get_region_summary(
            region["x1"], region["y1"], region["x2"], region["y2"]
        )
        directive = await self.prefect.issue_directive(
            world_context, self.bus.history_text()
        )
        if directive.get("done"):
            return

        await self._publish_chat(
            "praefectus", "directive",
            directive.get("commentary", f"Build {district['name']}!")
        )
        await asyncio.sleep(STEP_DELAY)

        # --- 2. Architect proposes layout ---
        proposal = None
        revision_notes = None

        for attempt in range(MAX_REVISIONS + 1):
            await self._send_typing("urbanista")
            proposal = await self.architect.propose_layout(
                directive, region, world_context, self.bus.history_text(),
                revision_notes=revision_notes,
            )
            await self._publish_chat(
                "urbanista", "proposal",
                proposal.get("commentary", proposal.get("proposal", "Here is my design."))
            )
            await asyncio.sleep(STEP_DELAY)

            # --- 3. Historian fact-checks ---
            await self._send_typing("historicus")
            fact_check = await self.historian.fact_check(
                proposal, district, self.bus.history_text()
            )

            approved = fact_check.get("approved", True)
            commentary = fact_check.get("commentary", "Reviewed.")

            # Add historical notes to commentary
            notes = fact_check.get("historical_notes", [])
            if notes:
                commentary += "\n" + "\n".join(f"Note: {n}" for n in notes[:2])

            await self._publish_chat(
                "historicus", "fact_check", commentary, approved=approved
            )
            await asyncio.sleep(STEP_DELAY)

            if approved:
                break
            else:
                # Build revision notes from corrections
                corrections = fact_check.get("corrections", [])
                revision_notes = "\n".join(
                    f"- {c.get('issue', '')}: {c.get('fix', '')}"
                    for c in corrections
                )
                if attempt < MAX_REVISIONS:
                    await self._publish_chat(
                        "praefectus", "directive",
                        f"Urbanista, please revise your proposal. The Historian has raised valid concerns."
                    )
                    await asyncio.sleep(STEP_DELAY)

        if not proposal or not proposal.get("tiles"):
            logger.warning(f"No tiles produced for {district['name']}")
            return

        # --- 4. Builder places tiles ---
        await self._send_typing("faber")
        historical_notes = fact_check.get("historical_notes", []) if fact_check else []
        build_result = await self.builder.build(
            proposal, historical_notes, self.bus.history_text()
        )

        placements = build_result.get("placements", proposal.get("tiles", []))
        await self._publish_chat(
            "faber", "placement",
            build_result.get("commentary", "The structures are placed.")
        )

        # Apply placements to world state
        placed_tiles = []
        for tile_data in placements:
            x = tile_data.get("x")
            y = tile_data.get("y")
            if x is not None and y is not None:
                tile_data["period"] = district["period"]
                tile_data["placed_by"] = "faber"
                # Attach first historical note to the tile
                if historical_notes:
                    tile_data["historical_note"] = historical_notes[0]
                if self.world.place_tile(x, y, tile_data):
                    tile = self.world.get_tile(x, y)
                    if tile:
                        placed_tiles.append(tile.to_dict())

        # Broadcast tile updates
        if placed_tiles:
            await self.broadcast({
                "type": "tile_update",
                "tiles": placed_tiles,
                "turn": self.world.turn,
                "period": district["period"],
                "year": district["year"],
            })

        await asyncio.sleep(STEP_DELAY)

        # --- 5. Citizen adds flavor ---
        await self._send_typing("civis")
        flavor = await self.citizen.add_flavor(
            placements, district["name"], district["year"], self.bus.history_text()
        )
        await self._publish_chat(
            "civis", "flavor",
            flavor.get("commentary", "The people of Rome go about their day.")
        )

        # Attach scenes to tiles
        scene_tiles = []
        for scene in flavor.get("scenes", []):
            x, y = scene.get("x"), scene.get("y")
            if x is not None and y is not None:
                desc = scene.get("description", "")
                self.world.place_tile(x, y, {"scene": desc})
                tile = self.world.get_tile(x, y)
                if tile:
                    scene_tiles.append(tile.to_dict())

        if scene_tiles:
            await self.broadcast({
                "type": "tile_update",
                "tiles": scene_tiles,
                "turn": self.world.turn,
            })

    async def _publish_chat(self, sender: str, msg_type: str, content: str, approved: bool | None = None):
        """Publish a message to the bus and broadcast to clients."""
        msg = BusMessage(
            sender=sender,
            msg_type=msg_type,
            content=content,
            turn=self.world.turn,
        )
        await self.bus.publish(msg)

        chat_data = {
            "type": "chat",
            "sender": sender,
            "msg_type": msg_type,
            "content": content,
            "turn": self.world.turn,
        }
        if approved is not None:
            chat_data["approved"] = approved

        await self.broadcast(chat_data)

    async def _send_typing(self, sender: str):
        """Send typing indicator to clients."""
        await self.broadcast({
            "type": "typing",
            "sender": sender,
        })
