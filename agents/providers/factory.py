"""Factory -- build_provider_from_spec() constructs a provider from an llm_agents config entry."""

from __future__ import annotations

from agents.providers.base import LlmProvider
from agents.providers.claude_cli import ClaudeCliProvider
from agents.providers.openai_compatible import OpenAICompatibleProvider
from core import config


def build_provider_from_spec(spec: dict) -> LlmProvider:
    """Construct a provider instance from one llm_agents.AGENT_LLM entry."""
    kind = (spec.get("provider") or "xai").strip().lower()
    if kind in ("claude", "claude_cli"):
        binary = spec.get("claude_binary")
        if binary is None or (isinstance(binary, str) and not binary.strip()):
            binary = getattr(config, "CLAUDE_CLI_BINARY", None) or "claude"
        return ClaudeCliProvider(binary=binary)
    if kind in ("openai_compatible", "openai", "chatgpt", "xai", "grok"):
        if kind in ("xai", "grok"):
            # Prefer spec overrides, fall back to XAI_* from config.py (which pulls from XAI_API_KEY env)
            base = (
                spec.get("openai_base_url")
                or spec.get("base_url")
                or getattr(config, "XAI_BASE_URL", None)
            )
            key = (
                spec.get("openai_api_key")
                or spec.get("api_key")
                or getattr(config, "XAI_API_KEY", None)
            )
            default_model = spec.get("model") or getattr(config, "XAI_DEFAULT_MODEL", None)
        else:
            base = spec.get("openai_base_url")
            if isinstance(base, str) and not base.strip():
                base = None
            key = spec.get("openai_api_key")
            if isinstance(key, str) and not key.strip():
                key = None
            default_model = None
        if isinstance(base, str) and not base.strip():
            base = None
        if isinstance(key, str) and not key.strip():
            key = None
        return OpenAICompatibleProvider(base_url=base, api_key=key, default_model=default_model)
    raise ValueError(
        f"Unknown provider {kind!r} in llm_agents -- use 'claude_cli', 'openai_compatible', or 'xai'."
    )
