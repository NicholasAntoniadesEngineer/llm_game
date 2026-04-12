"""
LLM routing per agent (defaults from data/llm_defaults.json via core.config).

**Runtime overrides:** “Configure AI” in the web app; persisted in data/llm_settings.json.

Keys must match what BuildEngine passes as llm_agent_key.

Optional override fields (from UI):
  - openai_base_url / openai_api_key: for OpenAI-compatible and xAI endpoints
  - claude_binary: only if an old save still uses provider claude_cli

provider values:
  - "xai" or "grok" — default; Grok model ids and suggestions in data/llm_defaults.json
  - "openai_compatible" — OpenAI Chat Completions–compatible APIs
  - "claude_cli" — still supported for legacy persisted settings (factory)
"""

from __future__ import annotations

from typing import Any, TypedDict

from core.config import (
    KEY_CARTOGRAPHUS_REFINE,
    KEY_CARTOGRAPHUS_SKELETON,
    KEY_CARTOGRAPHUS_SURVEY,
    KEY_URBANISTA,
    LLM_AGENT_DEFAULTS as AGENT_LLM,
    LLM_AGENT_LABELS as AGENT_LLM_LABELS,
)


class AgentLlmSpec(TypedDict, total=False):
    """Per-agent LLM routing."""

    provider: str  # claude_cli | openai_compatible | xai
    model: str  # Claude CLI model id or API model id (e.g. grok-4.20-reasoning for xAI)
    claude_binary: str | None
    openai_base_url: str | None
    openai_api_key: str | None


# Patches loaded from data/llm_settings.json or set at runtime (UI). Merged over AGENT_LLM.
_RUNTIME_OVERRIDES: dict[str, dict[str, Any]] = {}


def set_runtime_overrides(overrides: dict[str, dict[str, Any]] | None) -> None:
    """Replace runtime overrides (only keys in AGENT_LLM are kept)."""
    global _RUNTIME_OVERRIDES
    _RUNTIME_OVERRIDES = {}
    if not overrides:
        return
    for k, v in overrides.items():
        if k not in AGENT_LLM or not isinstance(v, dict):
            continue
        cleaned = {a: b for a, b in v.items() if b is not None}
        if cleaned:
            _RUNTIME_OVERRIDES[k] = cleaned


def get_runtime_overrides() -> dict[str, dict[str, Any]]:
    """Copy of persisted/runtime-only fields (may include API keys)."""
    return {k: dict(v) for k, v in _RUNTIME_OVERRIDES.items()}


def get_agent_llm_spec(agent_key: str) -> dict[str, Any]:
    """Base AGENT_LLM merged with runtime overrides (from file or UI)."""
    if agent_key not in AGENT_LLM:
        raise KeyError(
            f"Unknown llm_agent_key={agent_key!r}. Add it to llm_agents.AGENT_LLM. "
            f"Valid keys: {sorted(AGENT_LLM.keys())}"
        )
    merged: dict[str, Any] = dict(AGENT_LLM[agent_key])
    patch = _RUNTIME_OVERRIDES.get(agent_key)
    if patch:
        for k, v in patch.items():
            if v is None:
                continue
            if k == "openai_api_key" and isinstance(v, str) and not v.strip():
                continue
            merged[k] = v
    return merged
