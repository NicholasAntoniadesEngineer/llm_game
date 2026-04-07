"""Token/cost tracking (best-effort) for agent LLM calls.

We track both:
- last call usage (prompt/completion/total) per agent key
- cumulative totals since server start

Some backends (OpenAI-compatible) report exact usage; others (Claude CLI) do not.
For non-reporting backends we store an estimate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TokenUsageSnapshot:
    agent_key: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    exact: bool
    timestamp_s: float


class TokenUsageStore:
    def __init__(self) -> None:
        self._last_by_agent: dict[str, TokenUsageSnapshot] = {}
        self._totals_by_agent: dict[str, dict[str, int]] = {}

    def record(
        self,
        *,
        agent_key: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        exact: bool,
    ) -> None:
        safe_prompt = max(0, int(prompt_tokens))
        safe_completion = max(0, int(completion_tokens))
        safe_total = max(0, int(total_tokens))
        snap = TokenUsageSnapshot(
            agent_key=agent_key,
            provider=str(provider or ""),
            model=str(model or ""),
            prompt_tokens=safe_prompt,
            completion_tokens=safe_completion,
            total_tokens=safe_total,
            exact=bool(exact),
            timestamp_s=time.time(),
        )
        self._last_by_agent[agent_key] = snap
        totals = self._totals_by_agent.setdefault(
            agent_key, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )
        totals["prompt_tokens"] += safe_prompt
        totals["completion_tokens"] += safe_completion
        totals["total_tokens"] += safe_total

    def to_payload(self) -> dict[str, Any]:
        """Serializable payload keyed by agent_key."""
        out: dict[str, Any] = {}
        for agent_key, last in self._last_by_agent.items():
            totals = self._totals_by_agent.get(agent_key, {})
            out[agent_key] = {
                "last": {
                    "prompt_tokens": last.prompt_tokens,
                    "completion_tokens": last.completion_tokens,
                    "total_tokens": last.total_tokens,
                    "exact": last.exact,
                    "provider": last.provider,
                    "model": last.model,
                    "timestamp_s": last.timestamp_s,
                },
                "total": {
                    "prompt_tokens": int(totals.get("prompt_tokens", 0)),
                    "completion_tokens": int(totals.get("completion_tokens", 0)),
                    "total_tokens": int(totals.get("total_tokens", 0)),
                },
            }
        return out


STORE = TokenUsageStore()


def aggregate_for_ui() -> dict[str, dict[str, int]]:
    """Sum session token totals by header agent (Cartographus = skeleton+refine+survey)."""
    from agents.llm_routing import (
        KEY_CARTOGRAPHUS_REFINE,
        KEY_CARTOGRAPHUS_SKELETON,
        KEY_CARTOGRAPHUS_SURVEY,
        KEY_URBANISTA,
    )

    detail = STORE.to_payload()
    groups: dict[str, list[str]] = {
        "cartographus": [
            KEY_CARTOGRAPHUS_SKELETON,
            KEY_CARTOGRAPHUS_REFINE,
            KEY_CARTOGRAPHUS_SURVEY,
        ],
        "urbanista": [KEY_URBANISTA],
    }
    out: dict[str, dict[str, int]] = {}
    for ui, keys in groups.items():
        pt = ct = tt = 0
        for k in keys:
            row = detail.get(k)
            if not row:
                continue
            t = row.get("total") or {}
            pt += int(t.get("prompt_tokens", 0))
            ct += int(t.get("completion_tokens", 0))
            tt += int(t.get("total_tokens", 0))
        out[ui] = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
    return out


def estimate_tokens_from_text(text: str) -> int:
    """Very rough heuristic (~4 chars/token for English-ish text)."""
    if not text:
        return 0
    return max(1, int(len(text) / 4))

