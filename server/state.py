"""AppState — all shared mutable state for the server."""

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.application_services import ApplicationServices
    from core.config import Config

from world.state import WorldState
from orchestration.bus import MessageBus
from core.run_session import RunSession


class AppState:
    def __init__(
        self,
        system_configuration: "Config",
        application_services: "ApplicationServices",
    ):
        """Injected ``Config`` plus single application context (LLM routing, token store)."""
        self.system_configuration = system_configuration
        self.application_services = application_services
        self.world = WorldState(
            chunk_size_tiles=system_configuration.grid.chunk_size_tiles,
            system_configuration=system_configuration,
        )
        self.run_session = RunSession()
        self.bus = MessageBus()
        self.ws_connections: list = []
        self.chat_history: list[dict] = []
        self.ws_connection_sequence = 0
        # Full last agent_status message per agent (includes thinking_started_at_s when thinking)
        self.agent_status_by_agent: dict[str, dict] = {}
        self.asset_version = str(int(time.time()))

        # Callbacks (set by main.py after engine is created)
        self.reset_callback = None
        self.start_callback = None  # Called with (city_name, year) when user clicks Start
        self.resume_callback = None  # Resume build after API/network pause
        self.pause_callback = None  # Pause the build (user-initiated)
        self.llm_settings_callback = None  # async (overrides: dict) — persist + apply LLM routing from UI
        self.restart_server_callback = None  # async () -> dict — persist + touch reload sentinel (dev reload)
        self.reset_timeline_callback = None  # async () -> dict — new run clock only
        # Callable returning whether BuildEngine.run() is active — used to avoid replaying stale "paused" on refresh.
        self.engine_is_running = None
        # Optional replay helpers (set from main.py): WebSocket connect sends terrain + build context after refresh.
        self.terrain_data_for_replay = None
        self.build_status_for_replay = None
