"""LlmProvider Protocol -- the interface every LLM backend must satisfy."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LlmProvider(Protocol):
    """Returns raw model text (JSON string expected by BaseAgent._parse_json)."""

    async def complete(
        self,
        *,
        role: str,
        system_prompt: str,
        user_text: str,
        model: str,
    ) -> str:
        ...
