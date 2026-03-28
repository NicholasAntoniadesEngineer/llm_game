"""Historicus — the fact-checker who validates historical accuracy."""

from agents.base import BaseAgent
from config import SYSTEM_PROMPTS


class Historian(BaseAgent):
    def __init__(self):
        super().__init__(
            role="historicus",
            display_name="Historicus",
            system_prompt=SYSTEM_PROMPTS["historicus"],
        )

    async def fact_check(
        self, proposal: dict, district: dict, chat_history: str
    ) -> dict:
        tiles_desc = ""
        for t in proposal.get("tiles", []):
            name = t.get("building_name", t.get("terrain", "unknown"))
            tiles_desc += f"  ({t.get('x')},{t.get('y')}): {name} — {t.get('description', 'no description')}\n"

        instruction = f"""Recent team discussion:
{chat_history}

The Architect (Urbanista) has proposed the following layout for {district.get('name', 'Unknown')}:

Architect's rationale: {proposal.get('proposal', proposal.get('commentary', ''))}

Proposed tiles:
{tiles_desc}

Historical period: {district.get('period', 'Unknown')} (year: {district.get('year', 'Unknown')})

Please fact-check this proposal for historical accuracy. Check building names, dates, locations, and anachronisms."""

        result = await self.generate(instruction)
        if "approved" not in result:
            result["approved"] = True  # default to approved if parsing fails
        if "corrections" not in result:
            result["corrections"] = []
        if "historical_notes" not in result:
            result["historical_notes"] = []
        return result
