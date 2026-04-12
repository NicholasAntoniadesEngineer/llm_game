"""AppState — all shared mutable state for the server."""

import time

from world.state import WorldState
from orchestration.bus import MessageBus
from core.run_session import RunSession
from core.config import CHUNK_SIZE

# Module-level bound broadcast function — set by main.py after AppState + functools.partial are wired.
# Used by agents/base.py (late import) to push token_usage without needing the AppState instance.
broadcast_fn = None


class AppState:
    def __init__(self):
        self.world = WorldState(chunk_size_tiles=CHUNK_SIZE)
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
