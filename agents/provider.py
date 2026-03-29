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
        logger.info(
            "[%s] Claude CLI start | model=%s | %s | system=%s user=%s chars",
            role,
            model,
            self.binary,
            len(system_prompt),
            len(user_text),
        )
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
            logger.error(
                "[%s] Claude CLI exit=%s model=%s | stderr (first 2000 chars):\n%s",
                role,
                proc.returncode,
                model,
                (stderr_text[:2000] + "\n...") if len(stderr_text) > 2000 else stderr_text,
            )
            raise AgentGenerationError(pause_reason, pause_detail)
        out = stdout.decode().strip()
        if not out:
            from agents.base import AgentGenerationError

            stderr_preview = (stderr_text[:1200] + "\n...") if len(stderr_text) > 1200 else stderr_text
            logger.error(
                "[%s] Claude CLI returned empty stdout (exit 0). Model=%s. stderr:\n%s",
                role,
                model,
                stderr_preview or "(empty stderr)",
            )
            raise AgentGenerationError(
                "bad_model_output",
                "The Claude CLI produced no text on stdout (exit code 0). "
                "Check the server log for stderr from the CLI (login, model, or quota). "
                "Try `claude login`, confirm `claude --version`, and verify the model name in AI Settings.",
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

        base_log = self.base_url if len(self.base_url) <= 96 else self.base_url[:93] + "..."
        logger.info(
            "[%s] OpenAI-compatible POST /chat/completions | base=%s | model=%s | system=%s user=%s chars",
            role,
            base_log,
            use_model,
            len(system_prompt),
            len(user_text),
        )

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
            raw_preview = (err or body or "")[:2000]
            logger.error(
                "[%s] openai_compatible HTTP %s model=%s | detail=%s | raw_preview=%r",
                role,
                status,
                use_model,
                detail,
                raw_preview,
            )
            raise AgentGenerationError(
                "api_error",
                f"openai_compatible HTTP {status}: {detail}",
            )
        body_stripped = (body or "").strip()
        if not body_stripped:
            logger.error(
                "[%s] openai_compatible: empty response body (HTTP 200) model=%s",
                role,
                use_model,
            )
            raise AgentGenerationError(
                "bad_model_output",
                "The API returned an empty body (HTTP 200). Check base URL, model id, and proxy settings in AI Settings.",
            )
        try:
            data = json.loads(body_stripped)
        except json.JSONDecodeError as e:
            logger.error(
                "[%s] openai_compatible: response body is not JSON (model=%s): %s | body_preview=%r",
                role,
                use_model,
                e,
                body[:2000],
            )
            raise AgentGenerationError(
                "bad_model_output",
                f"OpenAI-compatible response was not valid JSON: {e}. "
                "Confirm the base URL points to /v1 chat/completions (not an HTML page).",
            ) from e
        # Best-effort usage reporting (OpenAI-compatible servers often include usage.*).
        try:
            self.last_usage = data.get("usage") if isinstance(data, dict) else None
        except Exception:
            self.last_usage = None
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
            body_preview = (body[:900] + "\n...") if len(body) > 900 else body
            logger.error(
                "[%s] openai_compatible: empty assistant content (model=%s). Response body preview:\n%s",
                role,
                use_model,
                body_preview,
            )
            raise AgentGenerationError(
                "bad_model_output",
                "The API returned an empty assistant message. "
                "Check the server log for the raw HTTP response preview. "
                "Verify model id, context length, and API key in AI Settings.",
            )
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
