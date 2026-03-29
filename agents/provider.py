"""LLM backends — pluggable completion used by BaseAgent.

Per-agent routing is configured in llm_agents.py; resolved specs via get_agent_llm_spec + build_provider_from_spec.

Backends:
  - claude_cli: Anthropic Claude Code CLI (subprocess).
  - openai_compatible: POST /chat/completions (OpenAI, LM Studio, vLLM, etc.).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Protocol, runtime_checkable

import config

logger = logging.getLogger("roma.agents.provider")


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


class ClaudeCliProvider:
    """Subprocess: claude --print --system-prompt ... (Anthropic Claude Code CLI)."""

    def __init__(self, binary: str | None = None):
        self.binary = binary or getattr(config, "CLAUDE_CLI_BINARY", None) or "claude"

    async def complete(
        self,
        *,
        role: str,
        system_prompt: str,
        user_text: str,
        model: str,
    ) -> str:
        proc = await asyncio.create_subprocess_exec(
            self.binary,
            "--print",
            "--system-prompt",
            system_prompt,
            "--output-format",
            "text",
            "--model",
            model,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=user_text.encode())
        stderr_text = stderr.decode(errors="replace")
        if proc.returncode != 0:
            from agents.base import AgentGenerationError, classify_agent_failure

            pause_reason, pause_detail = classify_agent_failure(stderr_text, None)
            logger.error(f"[{role}] CLI error: {stderr_text[:200]}")
            raise AgentGenerationError(pause_reason, pause_detail)
        out = stdout.decode().strip()
        if not out:
            from agents.base import AgentGenerationError

            raise AgentGenerationError(
                "api_error",
                "CLI returned empty stdout with exit code 0.",
            )
        return out


def _openai_compatible_request_sync(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_text: str,
) -> tuple[int, str, str]:
    """Returns (http_status, response_body, error_body_if_any)."""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.4,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.getcode(), raw, ""
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return e.code, "", err_body
    except urllib.error.URLError as e:
        return 0, "", str(e.reason) if e.reason else str(e)


class OpenAICompatibleProvider:
    """OpenAI-compatible Chat Completions HTTP API (no extra Python deps)."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
    ):
        self.base_url = (
            base_url
            or os.environ.get("OPENAI_COMPATIBLE_BASE_URL")
            or getattr(config, "OPENAI_COMPATIBLE_BASE_URL", "")
            or ""
        ).strip()
        self.api_key = (
            api_key
            or os.environ.get("OPENAI_COMPATIBLE_API_KEY")
            or getattr(config, "OPENAI_COMPATIBLE_API_KEY", "")
            or ""
        )
        self.default_model = (
            default_model
            or os.environ.get("OPENAI_COMPATIBLE_MODEL")
            or getattr(config, "OPENAI_COMPATIBLE_MODEL", "")
            or ""
        )

    async def complete(
        self,
        *,
        role: str,
        system_prompt: str,
        user_text: str,
        model: str,
    ) -> str:
        from agents.base import AgentGenerationError

        if not self.base_url:
            raise AgentGenerationError(
                "api_error",
                "openai_compatible: set OPENAI_COMPATIBLE_BASE_URL or config.OPENAI_COMPATIBLE_BASE_URL (e.g. https://api.openai.com/v1).",
            )
        if not self.api_key:
            raise AgentGenerationError(
                "api_error",
                "openai_compatible: set OPENAI_COMPATIBLE_API_KEY or config.OPENAI_COMPATIBLE_API_KEY.",
            )
        use_model = model
        if self.default_model:
            use_model = self.default_model

        status, body, err = await asyncio.to_thread(
            _openai_compatible_request_sync,
            self.base_url,
            self.api_key,
            use_model,
            system_prompt,
            user_text,
        )
        if status != 200:
            detail = (err or body)[:800]
            raise AgentGenerationError(
                "api_error",
                f"openai_compatible HTTP {status}: {detail}",
            )
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise AgentGenerationError("api_error", f"Invalid JSON from API: {e}") from e
        choices = data.get("choices")
        if not choices:
            raise AgentGenerationError("api_error", "API response missing choices[].")
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if content is None:
            raise AgentGenerationError("api_error", "API response missing message.content.")
        if isinstance(content, list):
            text = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        else:
            text = str(content).strip()
        if not text:
            raise AgentGenerationError("api_error", "API returned empty content.")
        return text


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
        f"Unknown provider {kind!r} in llm_agents — use 'claude_cli' or 'openai_compatible'."
    )
