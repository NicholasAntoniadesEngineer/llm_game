"""Shared exception types and failure classification for Eternal Cities."""

import asyncio


class EternalCitiesError(Exception):
    """Base exception for all Eternal Cities errors."""


class AgentGenerationError(EternalCitiesError):
    """CLI or model output failed; no synthetic substitute is allowed."""

    def __init__(self, pause_reason: str, pause_detail: str):
        self.pause_reason = pause_reason
        self.pause_detail = pause_detail
        super().__init__(f"{pause_reason}: {pause_detail}")


class UrbanistaValidationError(EternalCitiesError):
    """Urbanista JSON violates renderer contract; do not strip or substitute."""


def classify_agent_failure(stderr_text: str, exc: BaseException | None) -> tuple[str, str]:
    """Map stderr / exception to (pause_reason, short_detail) for UI and engine."""
    raw = (stderr_text or "").strip()
    text = raw.lower()

    if exc is not None:
        if isinstance(exc, FileNotFoundError):
            return ("cli_missing", "LLM backend executable not found on PATH (e.g. claude CLI).")
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return ("network", str(exc) or type(exc).__name__)
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionRefusedError, ConnectionAbortedError)):
            return ("network", str(exc) or type(exc).__name__)
        if isinstance(exc, OSError) and exc.errno is not None:
            if exc.errno in (50, 51, 60, 64, 65):
                return ("network", str(exc))

    if "429" in text or "rate limit" in text or "too many requests" in text:
        return ("rate_limit", raw[:400] if raw else "Rate limit exceeded.")
    if "503" in text or "502" in text or "504" in text:
        return ("api_error", raw[:400] if raw else "Service temporarily unavailable.")
    if "overloaded" in text or "capacity" in text:
        return ("api_error", raw[:400] if raw else "Service overloaded.")
    if "401" in text or "403" in text or "api key" in text or "authentication" in text or ("invalid" in text and "token" in text):
        return ("api_error", raw[:400] if raw else "Authentication or API access error.")
    if "getaddrinfo" in text or "name or service not known" in text or "connection refused" in text:
        return ("network", raw[:400] if raw else "Could not reach the service.")
    if "econnreset" in text or "network is unreachable" in text or "timed out" in text or "timeout" in text:
        return ("network", raw[:400] if raw else "Connection problem.")
    if not raw and exc is None:
        return ("api_error", "CLI exited with an error (no stderr output). Check quota, network, and CLI login.")
    return ("unknown", raw[:400] if raw else "Unknown error.")
