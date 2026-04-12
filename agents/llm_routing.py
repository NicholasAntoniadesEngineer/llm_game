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

from core.application_services import get_application_services

KEY_CARTOGRAPHUS_SKELETON = "cartographus_skeleton"
KEY_CARTOGRAPHUS_REFINE = "cartographus_refine"
KEY_CARTOGRAPHUS_SURVEY = "cartographus_survey"
KEY_URBANISTA = "urbanista"


class AgentLlmSpec(TypedDict, total=False):
    """Per-agent LLM routing."""

    provider: str  # claude_cli | openai_compatible | xai
    model: str  # Claude CLI model id or API model id (e.g. grok-4.20-reasoning for xAI)
    claude_binary: str | None
    openai_base_url: str | None
    openai_api_key: str | None


def iter_registered_agent_llm_keys() -> tuple[str, ...]:
    """Stable ordering of agent keys after bootstrap."""
    return tuple(sorted(get_application_services().agent_llm_specs_dictionary.keys()))


def get_agent_llm_specs_dictionary() -> dict[str, dict[str, Any]]:
    """Shallow copy of base routing specs (not merged with runtime overrides)."""
    return {k: dict(v) for k, v in get_application_services().agent_llm_specs_dictionary.items()}


def get_agent_llm_labels_dictionary() -> dict[str, str]:
    """Copy of display labels keyed by llm_agent_key."""
    return dict(get_application_services().agent_llm_labels_dictionary)


def set_runtime_overrides(overrides: dict[str, dict[str, Any]] | None) -> None:
    """Replace runtime overrides (only keys in registered agent specs are kept)."""
    services = get_application_services()
    services.runtime_llm_overrides_dictionary.clear()
    base = services.agent_llm_specs_dictionary
    if not overrides:
        return
    for agent_key, patch in overrides.items():
        if agent_key not in base or not isinstance(patch, dict):
            continue
        cleaned = {attribute_key: value for attribute_key, value in patch.items() if value is not None}
        if cleaned:
            services.runtime_llm_overrides_dictionary[agent_key] = cleaned


def get_runtime_overrides() -> dict[str, dict[str, Any]]:
    """Copy of persisted/runtime-only fields (may include API keys)."""
    return {
        agent_key: dict(patch)
        for agent_key, patch in get_application_services().runtime_llm_overrides_dictionary.items()
    }


def get_agent_llm_spec(agent_key: str) -> dict[str, Any]:
    """Base agent specs merged with runtime overrides (from file or UI)."""
    services = get_application_services()
    base_specs = services.agent_llm_specs_dictionary
    if agent_key not in base_specs:
        raise KeyError(
            f"Unknown llm_agent_key={agent_key!r}. Add it to llm_defaults.json agents section. "
            f"Valid keys: {sorted(base_specs.keys())}"
        )
    merged: dict[str, Any] = dict(base_specs[agent_key])
    patch = services.runtime_llm_overrides_dictionary.get(agent_key)
    if patch:
        for attribute_key, value in patch.items():
            if value is None:
                continue
            if attribute_key == "openai_api_key" and isinstance(value, str) and not value.strip():
                continue
            merged[attribute_key] = value
    return merged
