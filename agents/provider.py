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
        self.last_usage: dict | None = None

    def _get_cli_flags(self) -> list[str]:
        """
        Extra Claude Code CLI flags for cost/control.

        Key flags:
        - --tools "" disables all tools (web search, file access) — agents only need to produce JSON
        - --max-turns 1 is sufficient when tools are disabled (no tool-use turns needed)
        - ROMA_CLAUDE_BARE=1 enables --bare (can reduce overhead but may require separate login)
        - ROMA_CLAUDE_EFFORT may be low|medium|high|max (max only on some models)
        """
        flags: list[str] = []

        # Disable all tools — agents produce JSON from training knowledge, no web search needed.
        # This prevents the model from wasting turns on tool calls and hitting max_turns errors.
        # Override with ROMA_CLAUDE_TOOLS="default" to re-enable if needed.
        tools_raw = os.environ.get("ROMA_CLAUDE_TOOLS", "").strip()
        if not tools_raw:
            flags.extend(["--tools", ""])
        elif tools_raw.lower() != "none":
            flags.extend(["--tools", tools_raw])

        bare_raw = os.environ.get("ROMA_CLAUDE_BARE", "").strip().lower()
        if bare_raw in ("1", "true", "yes"):
            flags.append("--bare")

        max_turns_raw = os.environ.get("ROMA_CLAUDE_MAX_TURNS", "").strip()
        if not max_turns_raw:
            max_turns_raw = "1"  # 1 is enough when tools are disabled
        try:
            max_turns = int(max_turns_raw)
        except ValueError:
            max_turns = 1
        if max_turns > 0:
            flags.extend(["--max-turns", str(max_turns)])

        effort = os.environ.get("ROMA_CLAUDE_EFFORT", "").strip().lower()
        if effort:
            flags.extend(["--effort", effort])

        return flags

    @staticmethod
    def _parse_cli_json_payload(stdout_text: str) -> tuple[str, dict | None]:
        """
        Claude Code `--output-format json` returns a JSON object with:
        - result: text (often contains fenced JSON)
        - usage: token counts (input_tokens, cache_*_input_tokens, output_tokens, ...)
        """
        data = json.loads(stdout_text)
        if not isinstance(data, dict):
            return stdout_text.strip(), None
        result_text = str(data.get("result") or "").strip()
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        if data.get("is_error") is True:
            subtype = data.get("subtype", "")
            result_lower = result_text.lower()

            # Fast-fail on connection errors — don't make the caller wait for slow classification
            if "connectionrefused" in result_lower or "unable to connect" in result_lower:
                from agents.base import AgentGenerationError
                raise AgentGenerationError(
                    "network",
                    f"Claude CLI cannot reach the API: {result_text[:200]}. "
                    "Check internet connection, `claude login` status, and API quota.",
                )

            # error_max_turns: the model hit the turn cap but often still produced
            # valid text in `result`. Use it if non-empty instead of raising.
            if subtype == "error_max_turns" and result_text:
                logger.warning(
                    "Claude CLI: error_max_turns but result is non-empty (%d chars) — using it. Preview: %s",
                    len(result_text),
                    result_text[:300],
                )
                return result_text, usage
            logger.error(
                "Claude CLI error: subtype=%s result_len=%d stop_reason=%s num_turns=%s",
                subtype,
                len(result_text),
                data.get("stop_reason", "?"),
                data.get("num_turns", "?"),
            )
            raise RuntimeError(str(data.get("result") or f"Claude CLI error (subtype={subtype})"))
        return result_text, usage

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
        self.last_usage = None
        cli_flags = self._get_cli_flags()
        import time as _time
        _t0 = _time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            self.binary,
            *cli_flags,
            "--print",
            "--system-prompt",
            system_prompt,
            "--output-format",
            "json",
            "--model",
            model,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info(
            "[%s] Claude CLI pid=%s | flags=%s | model=%s | waiting for response...",
            role, proc.pid, " ".join(cli_flags), model,
        )
        # Per-process timeout — scales with prompt size. Large buildings (192 tiles)
        # generate 12K+ char prompts and can take 10-15 min on Sonnet.
        # Base: 10 min for haiku, 12 min for sonnet/opus. +1 min per 5K chars of input.
        _base_timeout = 600 if "haiku" in str(model).lower() else 720
        _input_chars = len(system_prompt) + len(user_text)
        _cli_timeout_s = _base_timeout + max(0, (_input_chars - 10000)) // 5000 * 60
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=user_text.encode()),
                timeout=_cli_timeout_s,
            )
        except asyncio.TimeoutError:
            elapsed = int((_time.monotonic() - _t0) * 1000)
            logger.error(
                "[%s] Claude CLI pid=%s TIMED OUT after %sms (limit=%ss) — killing",
                role, proc.pid, elapsed, _cli_timeout_s,
            )
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            from agents.base import AgentGenerationError
            raise AgentGenerationError(
                "network",
                f"Claude CLI timed out after {_cli_timeout_s}s. The API may be unreachable or very slow.",
            )
        except Exception as comm_exc:
            elapsed = int((_time.monotonic() - _t0) * 1000)
            logger.error(
                "[%s] Claude CLI pid=%s FAILED after %sms: %s — killing process",
                role, proc.pid, elapsed, comm_exc,
            )
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            raise
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        stdout_text = stdout.decode(errors="replace").strip()
        stderr_text = stderr.decode(errors="replace")
        logger.info(
            "[%s] Claude CLI pid=%s finished | exit=%s | %sms | stdout=%s stderr=%s chars",
            role, proc.pid, proc.returncode, elapsed_ms, len(stdout_text), len(stderr_text),
        )
        if stderr_text.strip():
            logger.info(
                "[%s] Claude CLI stderr preview: %s",
                role, stderr_text.strip()[:500],
            )

        out_text = ""
        try:
            out_text, usage = self._parse_cli_json_payload(stdout_text) if stdout_text else ("", None)
            # Map Claude Code CLI usage fields to our standard prompt/completion tokens.
            if isinstance(usage, dict):
                input_tokens = usage.get("input_tokens")
                cache_create = usage.get("cache_creation_input_tokens")
                cache_read = usage.get("cache_read_input_tokens")
                output_tokens = usage.get("output_tokens")
                if all(isinstance(v, int) for v in (input_tokens, output_tokens)):
                    prompt_tokens = int(input_tokens)
                    if isinstance(cache_create, int):
                        prompt_tokens += int(cache_create)
                    if isinstance(cache_read, int):
                        prompt_tokens += int(cache_read)
                    completion_tokens = int(output_tokens)
                    self.last_usage = {
                        "prompt_tokens": max(0, prompt_tokens),
                        "completion_tokens": max(0, completion_tokens),
                        "total_tokens": max(0, prompt_tokens + completion_tokens),
                    }
        except Exception as parse_exc:
            # If parsing fails, fall back to raw stdout (text mode-like) for downstream JSON parsing.
            logger.warning(
                "[%s] Claude CLI JSON parse failed; falling back to raw stdout: %s",
                role,
                str(parse_exc)[:200],
            )
            out_text = stdout_text

        if proc.returncode != 0:
            from agents.base import AgentGenerationError, classify_agent_failure

            pause_reason, pause_detail = classify_agent_failure(stderr_text, None)
            detail = pause_detail
            if out_text:
                # Often the CLI writes the actual error message to stdout in JSON mode.
                detail = (out_text[:400] + "…") if len(out_text) > 400 else out_text
            logger.error(
                "[%s] Claude CLI exit=%s model=%s | detail=%s | stderr (first 2000 chars):\n%s",
                role,
                proc.returncode,
                model,
                detail,
                (stderr_text[:2000] + "\n...") if len(stderr_text) > 2000 else stderr_text,
            )
            raise AgentGenerationError(pause_reason, detail)

        if not out_text:
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
        return out_text


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
        with urllib.request.urlopen(req, timeout=90) as resp:
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
