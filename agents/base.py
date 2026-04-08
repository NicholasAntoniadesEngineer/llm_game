"""Base agent — LLM completion via pluggable provider (see llm_agents.py + agents/provider.py).

Includes lightweight memory integration: each agent accumulates a rolling
ConversationMemory and a KnowledgeBase. Memory context is prepended to
instructions as compact strings (<100 tokens) before each LLM call, and
interaction summaries are recorded after each response.
"""

import asyncio
import json
import logging
import re
import time

from agents import llm_routing as llm_agents
from agents.memory import ConversationMemory, KnowledgeBase
from agents.providers import LlmProvider, build_provider_from_spec
from core.token_usage import STORE as TOKEN_USAGE_STORE, aggregate_for_ui, estimate_tokens_from_text, get_token_summary
from core.run_log import log_event
from core.errors import AgentGenerationError, classify_agent_failure

logger = logging.getLogger("eternal.agents")


def _broadcast_prompt_event(msg: dict):
    """Fire-and-forget broadcast of prompt/response data to WebSocket clients."""
    try:
        from server.state import broadcast_fn
        if not broadcast_fn:
            return
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(broadcast_fn(msg))
    except Exception:
        pass  # Never let UI broadcasting break agent calls


def _safe_preview_for_logs(text: str, limit: int = 1200) -> str:
    """First `limit` chars for logs (newlines normalized; truncation marked)."""
    if not text:
        return "(empty)"
    t = text.replace("\r\n", "\n")
    if len(t) > limit:
        return t[:limit] + "\n... [truncated]"
    return t


def _try_decode_json_object(text: str) -> dict | None:
    """Extract the first JSON object from text that may contain prose, markdown fences, etc."""
    if not text or not text.strip():
        return None
    # Stage 1: Strip markdown code fences
    cleaned = re.sub(r'```(?:json)?\s*\n?', '', text).strip()
    # Stage 2: Try direct parse
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    return item
    except json.JSONDecodeError:
        pass
    # Stage 3: Find first { to last } and try parsing
    i = cleaned.find("{")
    j = cleaned.rfind("}")
    if i >= 0 and j > i:
        try:
            obj = json.loads(cleaned[i:j + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    # Stage 4: Try raw_decode from first {
    if i >= 0:
        dec = json.JSONDecoder()
        try:
            obj, _end = dec.raw_decode(cleaned, i)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def _try_decode_json_array(text: str) -> list | None:
    """Extract the first JSON array from text that may contain prose, markdown fences, etc."""
    if not text or not text.strip():
        return None
    cleaned = re.sub(r'```(?:json)?\s*\n?', '', text).strip()
    # Try direct parse
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, list):
            return obj
    except json.JSONDecodeError:
        pass
    # Find first [ to last ] and try parsing
    i = cleaned.find("[")
    j = cleaned.rfind("]")
    if i >= 0 and j > i:
        try:
            obj = json.loads(cleaned[i:j + 1])
            if isinstance(obj, list):
                return obj
        except json.JSONDecodeError:
            pass
    return None


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
        self.system_prompt = system_prompt + (
            "\n\nIMPORTANT: ALWAYS respond with ONLY valid JSON. "
            "No markdown fences, no prose before or after. "
            "Start with '{' and end with '}'."
        )
        self.llm_agent_key = llm_agent_key
        spec = llm_agents.get_agent_llm_spec(llm_agent_key)
        self.model = spec["model"]
        self._provider_override = provider

        # Memory subsystems — lightweight, token-efficient.
        self.memory = ConversationMemory(max_entries=10)
        self.knowledge = KnowledgeBase()
        self._turn_counter = 0

    async def generate(self, instruction: str) -> dict:
        """Call LLM once and return parsed JSON. Raises AgentGenerationError on any failure.

        Memory integration:
        - Before the call, conversation memory context is prepended to the instruction.
        - After a successful call, both the instruction summary and response keys are recorded.
        """
        # Prepend memory context (compact, <100 tokens)
        enriched = self._prepend_memory_context(instruction)

        # Record the outgoing instruction in conversation memory
        self._turn_counter += 1
        self.memory.add("user", instruction, self._turn_counter)

        result = await self._single_generate(enriched)

        # Record the response summary in conversation memory
        response_keys = ",".join(sorted(result.keys())[:6])
        commentary = (result.get("commentary") or "")[:100]
        response_summary = f"keys={response_keys}"
        if commentary:
            response_summary += f"|{commentary}"
        self.memory.add("assistant", response_summary, self._turn_counter)

        return result

    async def generate_batch(self, instruction: str, count: int) -> list[dict]:
        """Call LLM once expecting a JSON array of `count` results.

        Returns a list of parsed dicts. On failure, returns an empty list
        (caller should fall back to individual calls).

        This is used for batching multiple small buildings into a single
        Urbanista call to reduce token overhead from repeated system prompts.
        """
        enriched = self._prepend_memory_context(instruction)
        prompt = (
            enriched
            + f"\n\nRespond with ONLY a valid JSON array of {count} objects. "
            "No markdown, no code fences, no extra text. Start with '[' and end with ']'."
        )

        try:
            spec = llm_agents.get_agent_llm_spec(self.llm_agent_key)
            model = spec["model"]
            provider_kind = str(spec.get("provider") or "claude_cli")
            provider = (
                self._provider_override
                if self._provider_override is not None
                else build_provider_from_spec(spec)
            )

            sys_tokens_est = estimate_tokens_from_text(self.system_prompt)
            inst_tokens_est = estimate_tokens_from_text(prompt)
            logger.info(
                "LLM batch query → | role=%s agent_key=%s | batch_size=%d | "
                "total_prompt ~%d tok",
                self.role,
                self.llm_agent_key,
                count,
                sys_tokens_est + inst_tokens_est,
            )

            _t0 = time.monotonic()
            raw = await provider.complete(
                role=self.role,
                system_prompt=self.system_prompt,
                user_text=prompt,
                model=model,
            )
            _elapsed_ms = int((time.monotonic() - _t0) * 1000)

            # Token tracking
            prompt_tokens_est = sys_tokens_est + inst_tokens_est
            completion_tokens_est = estimate_tokens_from_text(raw)
            prompt_tokens = prompt_tokens_est
            completion_tokens = completion_tokens_est
            total_tokens = prompt_tokens + completion_tokens
            exact = False
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

            logger.info(
                "LLM batch reply ← | role=%s | batch_size=%d | response_chars=%s | "
                "total_tokens=%s (%s) | %sms",
                self.role, count, len(raw),
                total_tokens, "exact" if exact else "estimated", _elapsed_ms,
            )

            # Parse as array
            arr = _try_decode_json_array(raw)
            if arr is not None and isinstance(arr, list):
                results = [item for item in arr if isinstance(item, dict)]
                if results:
                    logger.info(
                        "Batch parse OK: expected %d, got %d dicts", count, len(results)
                    )
                    return results

            # Fallback: try parsing as single object wrapping array
            obj = _try_decode_json_object(raw)
            if isinstance(obj, dict):
                # Check common wrapper keys
                for key in ("buildings", "results", "tiles", "designs"):
                    inner = obj.get(key)
                    if isinstance(inner, list):
                        results = [item for item in inner if isinstance(item, dict)]
                        if results:
                            logger.info(
                                "Batch parse via wrapper key '%s': got %d dicts",
                                key, len(results),
                            )
                            return results

            logger.warning(
                "Batch parse failed for %s — returning empty (caller will retry individually)",
                self.role,
            )
            return []

        except Exception as e:
            logger.warning("Batch generate failed for %s: %s — returning empty", self.role, e)
            return []

    def _prepend_memory_context(self, instruction: str) -> str:
        """Prepend compact memory context to an instruction if available.

        Only injects context when memory has content. The injected block is
        always <100 tokens to avoid inflating prompt costs.

        Args:
            instruction: The original instruction string.

        Returns:
            Instruction with memory context prepended (or unchanged if no memory).
        """
        parts = []
        mem_ctx = self.memory.format_context()
        if mem_ctx:
            parts.append(mem_ctx)
        if parts:
            return "\n".join(parts) + "\n\n" + instruction
        return instruction

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
            # Prompt size logging in estimated tokens (helps identify wastefully large prompts)
            sys_tokens_est = estimate_tokens_from_text(self.system_prompt)
            inst_tokens_est = estimate_tokens_from_text(prompt)
            total_prompt_tokens_est = sys_tokens_est + inst_tokens_est
            logger.info(
                "LLM query → | role=%s agent_key=%s | provider=%s model=%s | "
                "system=%s chars (~%d tok) instruction=%s chars (~%d tok) | "
                "total_prompt ~%d tok",
                self.role,
                self.llm_agent_key,
                provider_kind,
                model,
                len(self.system_prompt),
                sys_tokens_est,
                len(prompt),
                inst_tokens_est,
                total_prompt_tokens_est,
            )
            logger.info("LLM query instruction preview [%s]:\n%s", self.role, inst_preview)
            # Broadcast prompt to UI
            _broadcast_prompt_event({
                "type": "agent_prompt",
                "agent": self.role,
                "agent_key": self.llm_agent_key,
                "model": model,
                "system_prompt_len": len(self.system_prompt),
                "instruction": instruction[:2000],
                "timestamp": time.time(),
            })
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
            # Broadcast response to UI
            _broadcast_prompt_event({
                "type": "agent_response",
                "agent": self.role,
                "agent_key": self.llm_agent_key,
                "response_preview": raw[:2000],
                "parse_success": True,
                "elapsed_ms": _elapsed_ms,
                "tokens": total_tokens,
                "timestamp": time.time(),
            })
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
                        "summary": get_token_summary(),
                    }
                )
        except Exception:
            pass
