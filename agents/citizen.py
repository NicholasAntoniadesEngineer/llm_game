"""Civis — the citizen who adds life and flavor to the built world."""

from agents.base import BaseAgent
from config import SYSTEM_PROMPTS


class Citizen(BaseAgent):
    def __init__(self):
        super().__init__(
            role="civis",
            display_name="Civis",
            system_prompt=SYSTEM_PROMPTS["civis"],
        )

    async def add_flavor(
        self, placements: list[dict], district_name: str, year: int, chat_history: str
    ) -> dict:
        tiles_desc = ""
        for t in placements:
            name = t.get("building_name", t.get("terrain", "unknown"))
            tiles_desc += f"  ({t.get('x')},{t.get('y')}): {name}\n"

        instruction = f"""Recent team discussion:
{chat_history}

The builders have just completed these structures in {district_name} (year {year}):

{tiles_desc}

Describe the life and activity happening in this district. What do the citizens see, hear, and smell? What daily activities occur here?"""

        result = await self.generate(instruction)
        if "scenes" not in result:
            result["scenes"] = []
        return result
