"""Factory -- build_provider_from_spec() constructs a provider from an llm_agents config entry."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from agents.providers.base import LlmProvider
from agents.providers.claude_cli import ClaudeCliProvider
from agents.providers.openai_compatible import OpenAICompatibleProvider

if TYPE_CHECKING:
    from core.config import Config


def build_provider_from_spec(spec: dict, system_configuration: "Config") -> LlmProvider:
    """Construct a provider instance from one llm_agents.AGENT_LLM entry."""
    llm_defaults_raw = system_configuration.load_llm_defaults()
    kind = (spec.get("provider") or "xai").strip().lower()
    if kind in ("claude", "claude_cli"):
        binary = spec.get("claude_binary")
        if binary is None or (isinstance(binary, str) and not binary.strip()):
            binary = system_configuration.llm.claude_cli_binary_name
        return ClaudeCliProvider(binary=binary, system_configuration=system_configuration)
    if kind in ("openai_compatible", "openai", "chatgpt", "xai", "grok"):
        if kind in ("xai", "grok"):
            xai_section = llm_defaults_raw["xai"]
            base = (
                spec.get("openai_base_url")
                or spec.get("base_url")
                or str(xai_section["base_url"]).strip()
            )
            key = (
                spec.get("openai_api_key")
                or spec.get("api_key")
                or os.environ.get("XAI_API_KEY")
            )
            default_model = spec.get("model") or str(xai_section["default_model"]).strip() or None
            http_timeout_s = float(xai_section["request_timeout_seconds"])
        else:
            openai_section = llm_defaults_raw["openai_compatible"]
            base = spec.get("openai_base_url")
            if isinstance(base, str) and not base.strip():
                base = None
            key = spec.get("openai_api_key")
            if isinstance(key, str) and not key.strip():
                key = None
            dm = openai_section.get("default_model") or ""
            default_model = spec.get("model") or (str(dm).strip() or None)
            http_timeout_s = float(openai_section["request_timeout_seconds"])
        if isinstance(base, str) and not base.strip():
            base = None
        if isinstance(key, str) and not key.strip():
            key = None
        return OpenAICompatibleProvider(
            base_url=base,
            api_key=key,
            default_model=default_model,
            http_timeout_s=http_timeout_s,
            system_configuration=system_configuration,
        )
    raise ValueError(
        f"Unknown provider {kind!r} in llm_agents -- use 'claude_cli', 'openai_compatible', or 'xai'."
    )
