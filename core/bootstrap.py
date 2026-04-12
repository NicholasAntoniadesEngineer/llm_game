"""Composition-root helpers: LLM routing tables from validated CSV + llm_defaults.json."""

from __future__ import annotations

from agents import llm_routing as llm_agents
from core.application_services import ApplicationServices
from core.config import Config
from core.errors import ConfigLoadError


def apply_llm_routing_from_config(
    system_configuration: Config,
    application_services: ApplicationServices,
) -> None:
    """Populate ``application_services`` agent LLM specs and labels from llm_defaults.json."""
    llm_raw = system_configuration.load_llm_defaults()
    services = application_services
    required_keys = (
        llm_agents.KEY_CARTOGRAPHUS_SKELETON,
        llm_agents.KEY_CARTOGRAPHUS_REFINE,
        llm_agents.KEY_CARTOGRAPHUS_SURVEY,
        llm_agents.KEY_URBANISTA,
    )
    try:
        agents_section = llm_raw["agents"]
        labels_section = llm_raw["agent_labels"]
    except (KeyError, TypeError) as section_error:
        raise ConfigLoadError(
            "LLM defaults must contain 'agents' and 'agent_labels' objects."
        ) from section_error
    services.agent_llm_specs_dictionary.clear()
    for agent_key in required_keys:
        try:
            entry = agents_section[agent_key]
            services.agent_llm_specs_dictionary[agent_key] = {
                "provider": str(entry["provider"]).strip(),
                "model": str(entry["model"]).strip(),
            }
        except (KeyError, TypeError) as agent_error:
            raise ConfigLoadError(
                f"LLM defaults agents.{agent_key} must include provider and model."
            ) from agent_error
    services.agent_llm_labels_dictionary.clear()
    for agent_key in required_keys:
        try:
            services.agent_llm_labels_dictionary[agent_key] = str(labels_section[agent_key]).strip()
        except (KeyError, TypeError) as label_error:
            raise ConfigLoadError(
                f"LLM defaults agent_labels.{agent_key} is missing or not a string."
            ) from label_error
