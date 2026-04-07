"""ClaudeCliProvider -- Anthropic Claude Code CLI subprocess backend."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from core import config

logger = logging.getLogger("eternal.agents.provider")


class ClaudeCliProvider:
    """Subprocess: claude --print --system-prompt ... (Anthropic Claude Code CLI)."""

    def __init__(self, binary: str | None = None):
        self.binary = binary or getattr(config, "CLAUDE_CLI_BINARY", None) or "claude"
        self.last_usage: dict | None = None

    def _get_cli_flags(self) -> list[str]:
        """
        Extra Claude Code CLI flags for cost/control.

        Key flags:
        - --tools "" disables all tools (web search, file access) -- agents only need to produce JSON
        - --max-turns 1 is sufficient when tools are disabled (no tool-use turns needed)
        - ROMA_CLAUDE_BARE=1 enables --bare (can reduce overhead but may require separate login)
        - ROMA_CLAUDE_EFFORT may be low|medium|high|max (max only on some models)
        """
        flags: list[str] = []

        # Disable all tools -- agents produce JSON from training knowledge, no web search needed.
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

            # Fast-fail on connection errors -- don't make the caller wait for slow classification
            if "connectionrefused" in result_lower or "unable to connect" in result_lower:
                from core.errors import AgentGenerationError
                raise AgentGenerationError(
                    "network",
                    f"Claude CLI cannot reach the API: {result_text[:200]}. "
                    "Check internet connection, `claude login` status, and API quota.",
                )

            # error_max_turns: the model hit the turn cap but often still produced
            # valid text in `result`. Use it if non-empty instead of raising.
            if subtype == "error_max_turns" and result_text:
                logger.warning(
                    "Claude CLI: error_max_turns but result is non-empty (%d chars) -- using it. Preview: %s",
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
        # Per-process timeout -- scales with prompt size. Large buildings (192 tiles)
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
                "[%s] Claude CLI pid=%s TIMED OUT after %sms (limit=%ss) -- killing",
                role, proc.pid, elapsed, _cli_timeout_s,
            )
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            from core.errors import AgentGenerationError
            raise AgentGenerationError(
                "network",
                f"Claude CLI timed out after {_cli_timeout_s}s. The API may be unreachable or very slow.",
            )
        except Exception as comm_exc:
            elapsed = int((_time.monotonic() - _t0) * 1000)
            logger.error(
                "[%s] Claude CLI pid=%s FAILED after %sms: %s -- killing process",
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
            from core.errors import AgentGenerationError, classify_agent_failure

            pause_reason, pause_detail = classify_agent_failure(stderr_text, None)
            detail = pause_detail
            if out_text:
                # Often the CLI writes the actual error message to stdout in JSON mode.
                detail = (out_text[:400] + "\u2026") if len(out_text) > 400 else out_text
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
            from core.errors import AgentGenerationError

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
