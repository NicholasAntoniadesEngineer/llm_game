"""Process-wide application services: token accounting, LLM routing tables, broadcast.

``system_configuration`` is stored here only for startup convenience (LLM routing bootstrap,
token summaries). **Disk IO** (``core.persistence``), ``orchestration.reference_db``, and build
code paths must take an explicit ``system_configuration: Config`` argument so tests and tools
never rely on an implicit global config resolution.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from core.config import Config
from core.errors import EternalCitiesError
from core.token_usage import TokenUsageStore


@dataclass
class ApplicationServices:
    """Holds process-wide services; not a second code path for persistence config resolution."""

    system_configuration: Config
    token_usage_store: TokenUsageStore
    agent_llm_specs_dictionary: dict[str, dict[str, Any]] = field(default_factory=dict)
    agent_llm_labels_dictionary: dict[str, str] = field(default_factory=dict)
    runtime_llm_overrides_dictionary: dict[str, dict[str, Any]] = field(default_factory=dict)
    broadcast_async: Callable[..., Awaitable[Any]] | None = None


_application_services: ApplicationServices | None = None


def configure_application_services(
    *,
    system_configuration: Config,
    token_usage_store: TokenUsageStore,
    broadcast_async: Callable[..., Awaitable[Any]] | None = None,
) -> ApplicationServices:
    """Replace the global services bundle (startup and isolated tests)."""
    global _application_services
    _application_services = ApplicationServices(
        system_configuration=system_configuration,
        token_usage_store=token_usage_store,
        broadcast_async=broadcast_async,
    )
    return _application_services


def get_application_services() -> ApplicationServices:
    """Return the configured services bundle or fail hard."""
    if _application_services is None:
        raise EternalCitiesError(
            "Application services are not configured. Call configure_application_services() "
            "after load_config() (see main.py or tests/conftest.py)."
        )
    return _application_services


def set_broadcast_async(broadcast_async: Callable[..., Awaitable[Any]]) -> None:
    """Attach the UI broadcast coroutine after ``AppState`` exists (avoids import cycles)."""
    svc = get_application_services()
    svc.broadcast_async = broadcast_async
