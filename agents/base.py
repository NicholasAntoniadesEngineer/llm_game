"""Base agent — LLM completion via pluggable provider (see llm_agents.py + agents/provider.py)."""

import asyncio
import json
import logging

import llm_agents
from agents.provider import LlmProvider, build_provider_from_spec

logger = logging.getLogger("roma.agents")


class AgentGenerationError(Exception):
    """CLI or model output failed; no synthetic substitute is allowed."""

    def __init__(self, pause_reason: str, pause_detail: str):
        self.pause_reason = pause_reason
        self.pause_detail = pause_detail
        super().__init__(f"{pause_reason}: {pause_detail}")


def classify_agent_failure(stderr_text: str, exc: BaseException | None) -> tuple[str, str]:
    """Map stderr / exception to (pause_reason, short_detail) for UI and engine."""
    raw = (stderr_text or "").strip()
    text = raw.lower()

    if exc is not None:
        if isinstance(exc, FileNotFoundError):
            return ("cli_missing", "LLM backend executable not found on PATH (e.g. claude CLI).")
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return ("network", str(exc) or type(exc).__name__)
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionRefusedError, ConnectionAbortedError)):
            return ("network", str(exc) or type(exc).__name__)
        if isinstance(exc, OSError) and exc.errno is not None:
            if exc.errno in (50, 51, 60, 64, 65):
                return ("network", str(exc))

    if "429" in text or "rate limit" in text or "too many requests" in text:
        return ("rate_limit", raw[:400] if raw else "Rate limit exceeded.")
    if "503" in text or "502" in text or "504" in text:
        return ("api_error", raw[:400] if raw else "Service temporarily unavailable.")
    if "overloaded" in text or "capacity" in text:
        return ("api_error", raw[:400] if raw else "Service overloaded.")
    if "401" in text or "403" in text or "api key" in text or "authentication" in text or ("invalid" in text and "token" in text):
        return ("api_error", raw[:400] if raw else "Authentication or API access error.")
    if "getaddrinfo" in text or "name or service not known" in text or "connection refused" in text:
        return ("network", raw[:400] if raw else "Could not reach the service.")
    if "econnreset" in text or "network is unreachable" in text or "timed out" in text or "timeout" in text:
        return ("network", raw[:400] if raw else "Connection problem.")
    if not raw and exc is None:
        return ("api_error", "CLI exited with an error (no stderr output). Check quota, network, and CLI login.")
    return ("unknown", raw[:400] if raw else "Unknown error.")


class BaseAgent:
    def __init__(
        self,
        role: str,
        display_name: str,
        system_prompt: str,
        *,
        llm_agent_key: str,
        provider: LlmProvider | None = None,
    ):
        self.role = role
        self.display_name = display_name
        self.system_prompt = system_prompt
        self.llm_agent_key = llm_agent_key
        spec = llm_agents.get_agent_llm_spec(llm_agent_key)
        self.model = spec["model"]
        self._provider_override = provider

    async def generate(self, instruction: str) -> dict:
        """Call LLM once and return parsed JSON. Raises AgentGenerationError on any failure."""
        return await self._single_generate(instruction)

    async def _single_generate(self, instruction: str) -> dict:
        """Call LLM once. Raises AgentGenerationError if the process or JSON output is invalid."""
        prompt = instruction + "\n\nRespond with ONLY valid JSON. No markdown, no code fences, no extra text."

        try:
            spec = llm_agents.get_agent_llm_spec(self.llm_agent_key)
            model = spec["model"]
            provider = (
                self._provider_override
                if self._provider_override is not None
                else build_provider_from_spec(spec)
            )
            raw = await provider.complete(
                role=self.role,
                system_prompt=self.system_prompt,
                user_text=prompt,
                model=model,
            )
            logger.info(f"[{self.role}] response ({len(raw)} chars)")
            result = self._parse_json(raw)
            logger.info(f"[{self.role}] parsed: {list(result.keys())}")
            return result

        except AgentGenerationError:
            raise
        except FileNotFoundError as e:
            logger.error("LLM backend executable not found. Is it installed and on PATH?")
            pr, pd = classify_agent_failure("", e)
            raise AgentGenerationError(pr, pd) from e
        except Exception as e:
            logger.error(f"[{self.role}] unexpected error: {e}")
            pr, pd = classify_agent_failure("", e)
            raise AgentGenerationError(pr, pd) from e

    def _parse_json(self, raw: str) -> dict:
        """Parse model output as JSON. Raises AgentGenerationError if parsing fails."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"[{self.role}] failed to parse JSON: {text[:200]}")
            raise AgentGenerationError(
                "api_error",
                f"Model response is not valid JSON: {e!s}",
            ) from e
