"""Pytest fixtures: load system configuration from CSV before tests that need it."""

import pytest

from core.bootstrap import apply_llm_routing_from_config
from core.config import load_config

SYSTEM_CONFIGURATION = load_config()
apply_llm_routing_from_config(SYSTEM_CONFIGURATION)


@pytest.fixture(scope="session")
def system_configuration():
    return SYSTEM_CONFIGURATION
