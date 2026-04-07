"""Base agent — LLM completion via pluggable provider (see llm_agents.py + agents/provider.py)."""

import json
import logging
import time

from agents import llm_routing as llm_agents
from agents.providers import LlmProvider, build_provider_from_spec
from core.token_usage import STORE as TOKEN_USAGE_STORE, aggregate_for_ui, estimate_tokens_from_text
from core.run_log import log_event
from core.errors import AgentGenerationError, classify_agent_failure

logger = logging.getLogger("eternal.agents")


def _safe_preview_for_logs(text: str, limit: int = 1200) -> str:
    """First `limit` chars for logs (newlines normalized; truncation marked)."""
    if not text:
        return "(empty)"
    t = text.replace("\r\n", "\n")
    if len(t) > limit:
        return t[:limit] + "\n... [truncated]"
    return t


def _try_decode_json_object(text: str) -> dict | None:
    """If the model added prose before/after JSON, decode the first top-level object."""
    i = text.find("{")
    if i < 0:
        return None
    dec = json.JSONDecoder()
    try:
        obj, _end = dec.raw_decode(text, i)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


class BaseAgent:
    def __init__(
        self,
        role: str,
        display_name: str,
        system_prompt: str,
        *,
        llm_agent_key: str,
        provider: LlmProvider | None = None,
    ):
        self.role = role
        self.display_name = display_name
        self.system_prompt = system_prompt
        self.llm_agent_key = llm_agent_key
        spec = llm_agents.get_agent_llm_spec(llm_agent_key)
        self.model = spec["model"]
        self._provider_override = provider

    async def generate(self, instruction: str) -> dict:
        """Call LLM once and return parsed JSON. Raises AgentGenerationError on any failure."""
        return await self._single_generate(instruction)

    async def _single_generate(self, instruction: str) -> dict:
        """Call LLM once. Raises AgentGenerationError if the process or JSON output is invalid."""
        prompt = instruction + "\n\nRespond with ONLY valid JSON. No markdown, no code fences, no extra text."

        try:
            spec = llm_agents.get_agent_llm_spec(self.llm_agent_key)
            model = spec["model"]
            provider_kind = str(spec.get("provider") or "claude_cli")
            provider = (
                self._provider_override
                if self._provider_override is not None
                else build_provider_from_spec(spec)
            )
            inst_preview = _safe_preview_for_logs(instruction, 600)
            logger.info(
                "LLM query → | role=%s agent_key=%s | provider=%s model=%s | system=%s instruction=%s user_msg=%s chars",
                self.role,
                self.llm_agent_key,
                provider_kind,
                model,
                len(self.system_prompt),
                len(instruction),
                len(prompt),
            )
            logger.info("LLM query instruction preview [%s]:\n%s", self.role, inst_preview)
            _t0 = time.monotonic()
            raw = await provider.complete(
                role=self.role,
                system_prompt=self.system_prompt,
                user_text=prompt,
                model=model,
            )
            _elapsed_ms = int((time.monotonic() - _t0) * 1000)
            # Token usage tracking (exact when backend provides it, else estimate).
            prompt_tokens_est = estimate_tokens_from_text(self.system_prompt) + estimate_tokens_from_text(prompt)
            completion_tokens_est = estimate_tokens_from_text(raw)
            exact = False
            prompt_tokens = prompt_tokens_est
            completion_tokens = completion_tokens_est
            total_tokens = prompt_tokens + completion_tokens
            usage = getattr(provider, "last_usage", None)
            if isinstance(usage, dict):
                pt = usage.get("prompt_tokens")
                ct = usage.get("completion_tokens")
                tt = usage.get("total_tokens")
                if isinstance(pt, int) and isinstance(ct, int):
                    prompt_tokens = max(0, pt)
                    completion_tokens = max(0, ct)
                    total_tokens = max(0, int(tt) if isinstance(tt, int) else (prompt_tokens + completion_tokens))
                    exact = True
            TOKEN_USAGE_STORE.record(
                agent_key=self.llm_agent_key,
                provider=provider_kind,
                model=str(model or ""),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                exact=exact,
            )
            await self._broadcast_token_usage()
            tok_note = "exact" if exact else "estimated"
            logger.info(
                "LLM reply ← | role=%s agent_key=%s | response_chars=%s | total_tokens=%s (%s) | %sms",
                self.role,
                self.llm_agent_key,
                len(raw),
                total_tokens,
                tok_note,
                _elapsed_ms,
            )
            log_event("llm_call", f"{self.role} ({self.llm_agent_key})",
                      provider=provider_kind, model=str(model or ""),
                      prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                      total_tokens=total_tokens, accuracy=tok_note,
                      latency_ms=_elapsed_ms, response_chars=len(raw))
            result = self._parse_json(
                raw,
                model=str(model or ""),
                provider_kind=provider_kind,
            )
            logger.info(f"[{self.role}] parsed: {list(result.keys())}")
            return result

        except AgentGenerationError:
            raise
        except FileNotFoundError as e:
            logger.error("LLM backend executable not found. Is it installed and on PATH?")
            pr, pd = classify_agent_failure("", e)
            raise AgentGenerationError(pr, pd) from e
        except Exception as e:
            logger.error(f"[{self.role}] unexpected error: {e}")
            pr, pd = classify_agent_failure("", e)
            raise AgentGenerationError(pr, pd) from e

    def _parse_json(self, raw: str, *, model: str, provider_kind: str) -> dict:
        """Parse model output as JSON. Raises AgentGenerationError if parsing fails."""
        raw_str = raw if isinstance(raw, str) else ""
        text = raw_str.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines).strip()

        if not text:
            logger.error(
                "[%s] LLM output empty after normalization | agent_key=%s model=%s provider=%s | raw_len=%s\n--- raw (preview) ---\n%s\n--- end ---",
                self.role,
                self.llm_agent_key,
                model,
                provider_kind,
                len(raw_str),
                _safe_preview_for_logs(raw_str, 800),
            )
            raise AgentGenerationError(
                "bad_model_output",
                "The model returned no usable text (empty or whitespace only). "
                "If you use the Claude CLI: run `claude login`, check `claude --version`, and confirm the model name in AI Settings. "
                "If you use an OpenAI-compatible API: verify the base URL, API key, and model id.",
            )

        try:
            return json.loads(text)
        except json.JSONDecodeError as first_err:
            nested = _try_decode_json_object(text)
            if isinstance(nested, dict):
                logger.warning(
                    "[%s] Parsed JSON after skipping leading/trailing non-JSON text (agent_key=%s)",
                    self.role,
                    self.llm_agent_key,
                )
                return nested
            logger.error(
                "[%s] LLM output is not valid JSON | agent_key=%s model=%s provider=%s | json_err=%s | text_len=%s\n--- model output (preview) ---\n%s\n--- end preview ---",
                self.role,
                self.llm_agent_key,
                model,
                provider_kind,
                first_err,
                len(text),
                _safe_preview_for_logs(text, 1200),
            )
            raise AgentGenerationError(
                "bad_model_output",
                "The model did not return valid JSON (the app expects a single JSON object). "
                "Often the model answered in plain text or markdown instead. "
                "See the server console for a preview of what was returned. "
                "Then check AI Settings (model name and provider) and try again.",
            ) from first_err

    async def _broadcast_token_usage(self) -> None:
        try:
            from server.state import broadcast_fn

            if broadcast_fn is not None:
                await broadcast_fn(
                    {
                        "type": "token_usage",
                        "by_ui_agent": aggregate_for_ui(),
                        "by_llm_key": TOKEN_USAGE_STORE.to_payload(),
                    }
                )
        except Exception:
            pass
