"""Factory -- build_provider_from_spec() constructs a provider from an llm_agents config entry."""

from __future__ import annotations

from agents.providers.base import LlmProvider
from agents.providers.claude_cli import ClaudeCliProvider
from agents.providers.openai_compatible import OpenAICompatibleProvider
from core import config


def build_provider_from_spec(spec: dict) -> LlmProvider:
    """Construct a provider instance from one llm_agents.AGENT_LLM entry."""
    kind = (spec.get("provider") or "claude_cli").strip().lower()
    if kind in ("claude", "claude_cli"):
        binary = spec.get("claude_binary")
        if binary is None or (isinstance(binary, str) and not binary.strip()):
            binary = getattr(config, "CLAUDE_CLI_BINARY", None) or "claude"
        return ClaudeCliProvider(binary=binary)
    if kind in ("openai_compatible", "openai", "chatgpt"):
        base = spec.get("openai_base_url")
        if isinstance(base, str) and not base.strip():
            base = None
        key = spec.get("openai_api_key")
        if isinstance(key, str) and not key.strip():
            key = None
        return OpenAICompatibleProvider(base_url=base, api_key=key, default_model=None)
    raise ValueError(
        f"Unknown provider {kind!r} in llm_agents -- use 'claude_cli' or 'openai_compatible'."
    )
