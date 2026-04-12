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
        self._call_counts: dict[str, int] = {}

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
        self._call_counts[agent_key] = self._call_counts.get(agent_key, 0) + 1

    def call_count(self, agent_key: str) -> int:
        """Return the number of LLM calls recorded for the given agent key."""
        return self._call_counts.get(agent_key, 0)

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


def _agent_keys_by_ui_group() -> dict[str, list[str]]:
    """Map toolbar agent names to underlying LLM routing keys (single source of truth)."""
    from agents.llm_routing import (
        KEY_CARTOGRAPHUS_REFINE,
        KEY_CARTOGRAPHUS_SKELETON,
        KEY_CARTOGRAPHUS_SURVEY,
        KEY_URBANISTA,
    )

    return {
        "cartographus": [
            KEY_CARTOGRAPHUS_SKELETON,
            KEY_CARTOGRAPHUS_REFINE,
            KEY_CARTOGRAPHUS_SURVEY,
        ],
        "urbanista": [KEY_URBANISTA],
    }


def aggregate_for_ui() -> dict[str, dict[str, int]]:
    """Sum session token totals by header agent (Cartographus = skeleton+refine+survey)."""
    detail = STORE.to_payload()
    groups = _agent_keys_by_ui_group()
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


# ── Cost estimates per 1M tokens (input/output) ──
# Conservative defaults; callers can override.
_COST_PER_1M_INPUT = 0.25   # Haiku-class
_COST_PER_1M_OUTPUT = 1.25


def get_token_summary() -> dict:
    """Return a comprehensive per-agent breakdown for the UI dashboard.

    Includes:
    - Per-agent totals (prompt, completion, total tokens, call count)
    - Per-UI-group aggregates (cartographus, urbanista)
    - Average tokens per building (urbanista calls)
    - Estimated cost
    """
    detail = STORE.to_payload()

    # Per-agent detail with call counts
    agents_detail: dict[str, dict] = {}
    for agent_key, row in detail.items():
        totals = row.get("total", {})
        agents_detail[agent_key] = {
            "prompt_tokens": int(totals.get("prompt_tokens", 0)),
            "completion_tokens": int(totals.get("completion_tokens", 0)),
            "total_tokens": int(totals.get("total_tokens", 0)),
            "call_count": STORE.call_count(agent_key),
            "model": row.get("last", {}).get("model", ""),
        }

    # UI groups
    groups = _agent_keys_by_ui_group()
    by_group: dict[str, dict] = {}
    for ui_name, keys in groups.items():
        pt = ct = tt = calls = 0
        for k in keys:
            row = detail.get(k)
            if not row:
                continue
            t = row.get("total") or {}
            pt += int(t.get("prompt_tokens", 0))
            ct += int(t.get("completion_tokens", 0))
            tt += int(t.get("total_tokens", 0))
            calls += STORE.call_count(k)
        by_group[ui_name] = {
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": tt,
            "call_count": calls,
        }

    # Average tokens per building (urbanista calls)
    from agents.llm_routing import KEY_URBANISTA

    urbanista_calls = STORE.call_count(KEY_URBANISTA)
    urbanista_total = int(
        (detail.get(KEY_URBANISTA, {}).get("total", {}).get("total_tokens", 0))
    )
    avg_tokens_per_building = round(urbanista_total / urbanista_calls) if urbanista_calls > 0 else 0

    # Estimated cost
    total_prompt = sum(g["prompt_tokens"] for g in by_group.values())
    total_completion = sum(g["completion_tokens"] for g in by_group.values())
    estimated_cost = (
        (total_prompt / 1_000_000) * _COST_PER_1M_INPUT
        + (total_completion / 1_000_000) * _COST_PER_1M_OUTPUT
    )

    return {
        "agents": agents_detail,
        "by_group": by_group,
        "avg_tokens_per_building": avg_tokens_per_building,
        "urbanista_calls": urbanista_calls,
        "total_tokens": total_prompt + total_completion,
        "estimated_cost_usd": round(estimated_cost, 4),
    }

