"""Pytest fixtures: load system configuration from CSV before tests that need it.

Application services are configured once at import time so ``from tests.conftest import
SYSTEM_CONFIGURATION`` matches the same bundle used by orchestration and agents.
"""

import pytest

from core.application_services import configure_application_services
from core.bootstrap import apply_llm_routing_from_config
from core.config import load_config
from core.token_usage import TokenUsageStore

SYSTEM_CONFIGURATION = load_config()
configure_application_services(
    system_configuration=SYSTEM_CONFIGURATION,
    token_usage_store=TokenUsageStore(),
)
apply_llm_routing_from_config(SYSTEM_CONFIGURATION)


@pytest.fixture(scope="session")
def system_configuration():
    return SYSTEM_CONFIGURATION
