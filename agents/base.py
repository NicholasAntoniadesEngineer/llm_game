"""Base agent — runs claude CLI via subprocess."""

import asyncio
import json
import logging

logger = logging.getLogger("roma.agents")


class BaseAgent:
    def __init__(self, role: str, display_name: str, system_prompt: str, model: str = "sonnet"):
        self.role = role
        self.display_name = display_name
        self.system_prompt = system_prompt
        self.model = model

    async def generate(self, instruction: str, max_retries: int = 2) -> dict:
        """Call claude CLI and return parsed JSON response. Retries on failure."""
        for attempt in range(max_retries + 1):
            result = await self._single_generate(instruction)
            if not result.get("error"):
                return result
            if attempt < max_retries:
                logger.warning(f"[{self.role}] attempt {attempt+1} failed, retrying...")
        return result

    async def _single_generate(self, instruction: str) -> dict:
        """Call claude CLI once and return parsed JSON response."""
        prompt = instruction + "\n\nRespond with ONLY valid JSON. No markdown, no code fences, no extra text."

        try:
            proc = await asyncio.create_subprocess_exec(
                "claude",
                "--print",
                "--system-prompt", self.system_prompt,
                "--output-format", "text",
                "--model", self.model,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(input=prompt.encode())

            if proc.returncode != 0:
                logger.error(f"[{self.role}] CLI error: {stderr.decode()[:200]}")
                return self._fallback_response()

            raw = stdout.decode().strip()
            logger.info(f"[{self.role}] response ({len(raw)} chars)")
            result = self._parse_json(raw)
            logger.info(f"[{self.role}] parsed: {list(result.keys())}")
            return result

        except FileNotFoundError:
            logger.error("claude CLI not found. Is it installed?")
            return self._fallback_response()
        except Exception as e:
            logger.error(f"[{self.role}] unexpected error: {e}")
            return self._fallback_response()

    def _parse_json(self, raw: str) -> dict:
        """Extract JSON from response, handling markdown fences."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
            logger.warning(f"[{self.role}] failed to parse JSON: {text[:200]}")
            return self._fallback_response(commentary=text[:200])

    def _fallback_response(self, commentary: str = "...") -> dict:
        """Return a safe fallback if the agent fails."""
        return {"commentary": commentary, "error": True}
