"""Lightweight ``BuildEngine.run()`` smoke: mocked build + expansion cooldown exit."""

from __future__ import annotations

from types import MethodType

import pytest

from core.run_session import RunSession
from orchestration.bus import MessageBus
from orchestration.engine import BuildEngine
from world.state import WorldState

from tests.conftest import APPLICATION_SERVICES, SYSTEM_CONFIGURATION


@pytest.mark.asyncio
async def test_engine_run_exits_after_expand_cooldown_sleep(monkeypatch):
    """Districts preloaded, instant build wave, expand false → sleep patched to stop engine."""
    holder: list[BuildEngine | None] = [None]

    async def sleep_stops_engine(_delay: float) -> None:
        eng = holder[0]
        if eng is not None:
            eng.running = False

    monkeypatch.setattr("orchestration.engine.asyncio.sleep", sleep_stops_engine)

    async def instant_run_build_generation(_engine):
        return True

    monkeypatch.setattr("orchestration.engine.run_build_generation", instant_run_build_generation)

    monkeypatch.setattr("core.persistence.save_state", lambda *a, **k: "")
    monkeypatch.setattr("core.persistence.save_blueprint", lambda *a, **k: None)

    world = WorldState(
        chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles,
        system_configuration=SYSTEM_CONFIGURATION,
    )
    bus = MessageBus()
    chat: list = []
    session = RunSession(
        scenario={
            "location": "LiteCity",
            "period": "test",
            "focus_year": 100,
            "started_at_s": 0.0,
        }
    )

    async def silent_broadcast(_msg):
        return None

    engine = BuildEngine(
        world,
        bus,
        silent_broadcast,
        chat,
        run_session=session,
        system_configuration=SYSTEM_CONFIGURATION,
        application_services=APPLICATION_SERVICES,
    )
    holder[0] = engine

    engine.districts = [
        {
            "name": "LiteDistrict",
            "id": "ld0",
            "region": {"x1": 0, "y1": 0, "x2": 20, "y2": 20},
            "period": "test",
            "year": 100,
            "description": "",
        }
    ]

    async def no_expand():
        return False

    monkeypatch.setattr(engine.generators, "expand_city", no_expand)

    async def skip_save(self, flush_mode: str = "incremental"):
        return None

    engine._save_state_thread = MethodType(skip_save, engine)

    async def silent_chat(*_a, **_k):
        return None

    engine._chat = MethodType(silent_chat, engine)

    engine.tasks.start_token_telemetry = lambda: None

    async def noop_async():
        return None

    engine.tasks.stop_token_telemetry = MethodType(noop_async, engine.tasks)
    engine.tasks.await_map_refine_task = MethodType(noop_async, engine.tasks)
    engine.tasks.broadcast_all_agents_idle = MethodType(noop_async, engine.tasks)

    engine.running = True
    await engine.run()

    assert engine.running is False
    assert engine.tasks.run_phase.value == "idle"
