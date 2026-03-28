"""Faber — the master builder who places tiles on the world grid."""

from agents.base import BaseAgent
from config import SYSTEM_PROMPTS


class Builder(BaseAgent):
    def __init__(self):
        super().__init__(
            role="faber",
            display_name="Faber",
            system_prompt=SYSTEM_PROMPTS["faber"],
        )

    async def build(
        self, proposal: dict, historical_notes: list[str], chat_history: str
    ) -> dict:
        tiles_desc = ""
        for t in proposal.get("tiles", []):
            name = t.get("building_name", t.get("terrain", "unknown"))
            tiles_desc += f"  ({t.get('x')},{t.get('y')}): {name} [{t.get('building_type', t.get('terrain', ''))}] — {t.get('description', '')}\n"

        notes = "\n".join(f"  - {n}" for n in historical_notes) if historical_notes else "  (none)"

        instruction = f"""Recent team discussion:
{chat_history}

The following layout has been APPROVED by the Historian. Finalize the tile placements with colors and icons.

Approved tiles:
{tiles_desc}

Historian's notes:
{notes}

Place these tiles now with appropriate colors and icons."""

        result = await self.generate(instruction)
        if "placements" not in result:
            # Fall back to using the proposal tiles directly
            result["placements"] = proposal.get("tiles", [])
        return result
