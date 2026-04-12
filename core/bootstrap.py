"""Composition-root helpers: LLM routing tables and UI broadcast binding."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from agents import llm_routing as llm_agents
from core.config import Config
import server.state as server_state_mod


def apply_llm_routing_from_config(system_configuration: Config) -> None:
    """Populate AGENT_LLM and AGENT_LLM_LABELS from validated CSV + llm_defaults.json."""
    llm_raw = system_configuration.load_llm_defaults()
    llm_agents.AGENT_LLM = {
        k: {
            "provider": str(llm_raw["agents"][k]["provider"]).strip(),
            "model": str(llm_raw["agents"][k]["model"]).strip(),
        }
        for k in (
            llm_agents.KEY_CARTOGRAPHUS_SKELETON,
            llm_agents.KEY_CARTOGRAPHUS_REFINE,
            llm_agents.KEY_CARTOGRAPHUS_SURVEY,
            llm_agents.KEY_URBANISTA,
        )
    }
    llm_agents.AGENT_LLM_LABELS = {
        k: str(llm_raw["agent_labels"][k]).strip() for k in llm_agents.AGENT_LLM
    }


def bind_application_broadcast(broadcast_async: Callable[..., Awaitable[Any]]) -> None:
    """Wire module-level ``server.state.broadcast_fn`` for non-engine callers."""
    server_state_mod.broadcast_fn = broadcast_async
