"""Praefectus — the project director who issues build directives."""

from agents.base import BaseAgent
from config import SYSTEM_PROMPTS, DISTRICTS


class Prefect(BaseAgent):
    def __init__(self):
        super().__init__(
            role="praefectus",
            display_name="Praefectus",
            system_prompt=SYSTEM_PROMPTS["praefectus"],
        )
        self._district_index = 0

    def current_district(self) -> dict | None:
        if self._district_index < len(DISTRICTS):
            return DISTRICTS[self._district_index]
        return None

    def advance_district(self):
        self._district_index += 1

    async def issue_directive(self, world_context: str, chat_history: str) -> dict:
        district = self.current_district()
        if not district:
            return {"commentary": "Roma is complete. Glory to the Empire!", "done": True}

        instruction = f"""The current state of Rome:
{world_context}

Recent team discussion:
{chat_history}

Your next task: Direct the team to build the district of {district['name']}.
Description: {district['description']}
Grid region: x={district['region']['x1']}-{district['region']['x2']}, y={district['region']['y1']}-{district['region']['y2']}
Historical period: {district['period']} (year: {district['year']})

Issue your directive now."""

        result = await self.generate(instruction)
        if "district" not in result:
            result["district"] = district["name"]
        if "year" not in result:
            result["year"] = district["year"]
        return result
