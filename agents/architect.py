"""Urbanista — the master architect who proposes building layouts."""

from agents.base import BaseAgent
from config import SYSTEM_PROMPTS


class Architect(BaseAgent):
    def __init__(self):
        super().__init__(
            role="urbanista",
            display_name="Urbanista",
            system_prompt=SYSTEM_PROMPTS["urbanista"],
        )

    async def propose_layout(
        self, directive: dict, region: dict, world_context: str, chat_history: str,
        revision_notes: str | None = None,
    ) -> dict:
        instruction = f"""The current state of Rome:
{world_context}

Recent team discussion:
{chat_history}

DIRECTIVE from Praefectus: {directive.get('directive', directive.get('commentary', ''))}
District: {directive.get('district', 'Unknown')}
Buildings requested: {', '.join(directive.get('buildings', []))}
Grid region: x={region['x1']}-{region['x2']}, y={region['y1']}-{region['y2']}
Year: {directive.get('year', -44)}"""

        if revision_notes:
            instruction += f"""

IMPORTANT — The Historian has rejected your previous proposal with these corrections:
{revision_notes}

Please revise your layout to address these issues."""

        instruction += "\n\nPropose your layout now. Place tiles ONLY within the specified region bounds."

        result = await self.generate(instruction)
        if "tiles" not in result:
            result["tiles"] = []
        return result
