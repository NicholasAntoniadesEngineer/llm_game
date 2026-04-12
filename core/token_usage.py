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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.config import Config


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


def aggregate_for_ui(token_usage_store: TokenUsageStore) -> dict[str, dict[str, int]]:
    """Sum session token totals by header agent (Cartographus = skeleton+refine+survey)."""
    store = token_usage_store
    detail = store.to_payload()
    groups = _agent_keys_by_ui_group()
    out: dict[str, dict[str, int]] = {}
    for ui, keys in groups.items():
        prompt_total = completion_total = grand_total = 0
        for routing_key in keys:
            row = detail.get(routing_key)
            if not row:
                continue
            token_row = row.get("total") or {}
            prompt_total += int(token_row.get("prompt_tokens", 0))
            completion_total += int(token_row.get("completion_tokens", 0))
            grand_total += int(token_row.get("total_tokens", 0))
        out[ui] = {
            "prompt_tokens": prompt_total,
            "completion_tokens": completion_total,
            "total_tokens": grand_total,
        }
    return out


def estimate_tokens_from_text(text: str, *, system_configuration: "Config") -> int:
    """Rough token count from character length (divisor from system_config.csv)."""
    if not text:
        return 0
    divisor = float(system_configuration.token.estimated_chars_per_token_for_heuristic)
    if divisor <= 0:
        divisor = 4.0
    return max(1, int(len(text) / divisor))


def get_token_summary(
    *,
    system_configuration: "Config",
    token_usage_store: TokenUsageStore,
) -> dict:
    """Return a comprehensive per-agent breakdown for the UI dashboard.

    Includes:
    - Per-agent totals (prompt, completion, total tokens, call count)
    - Per-UI-group aggregates (cartographus, urbanista)
    - Average tokens per building (urbanista calls)
    - Estimated cost
    """
    store = token_usage_store
    detail = store.to_payload()

    # Per-agent detail with call counts
    agents_detail: dict[str, dict] = {}
    for agent_key, row in detail.items():
        totals = row.get("total", {})
        agents_detail[agent_key] = {
            "prompt_tokens": int(totals.get("prompt_tokens", 0)),
            "completion_tokens": int(totals.get("completion_tokens", 0)),
            "total_tokens": int(totals.get("total_tokens", 0)),
            "call_count": store.call_count(agent_key),
            "model": row.get("last", {}).get("model", ""),
        }

    # UI groups
    groups = _agent_keys_by_ui_group()
    by_group: dict[str, dict] = {}
    for ui_name, keys in groups.items():
        prompt_segment = completion_segment = total_segment = call_segment = 0
        for routing_key in keys:
            row = detail.get(routing_key)
            if not row:
                continue
            token_bucket = row.get("total") or {}
            prompt_segment += int(token_bucket.get("prompt_tokens", 0))
            completion_segment += int(token_bucket.get("completion_tokens", 0))
            total_segment += int(token_bucket.get("total_tokens", 0))
            call_segment += store.call_count(routing_key)
        by_group[ui_name] = {
            "prompt_tokens": prompt_segment,
            "completion_tokens": completion_segment,
            "total_tokens": total_segment,
            "call_count": call_segment,
        }

    # Average tokens per building (urbanista calls)
    from agents.llm_routing import KEY_URBANISTA

    urbanista_calls = store.call_count(KEY_URBANISTA)
    urbanista_total = int(
        (detail.get(KEY_URBANISTA, {}).get("total", {}).get("total_tokens", 0))
    )
    avg_tokens_per_building = round(urbanista_total / urbanista_calls) if urbanista_calls > 0 else 0

    # Estimated cost (rates from system_config.csv via Config)
    total_prompt = sum(group_bucket["prompt_tokens"] for group_bucket in by_group.values())
    total_completion = sum(group_bucket["completion_tokens"] for group_bucket in by_group.values())
    cost_in = float(system_configuration.token.cost_per_million_input_tokens)
    cost_out = float(system_configuration.token.cost_per_million_output_tokens)
    estimated_cost = (
        (total_prompt / 1_000_000) * cost_in
        + (total_completion / 1_000_000) * cost_out
    )

    return {
        "agents": agents_detail,
        "by_group": by_group,
        "avg_tokens_per_building": avg_tokens_per_building,
        "urbanista_calls": urbanista_calls,
        "total_tokens": total_prompt + total_completion,
        "estimated_cost_usd": round(estimated_cost, 4),
    }
