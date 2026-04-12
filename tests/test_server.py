"""Tests for server/ — FastAPI endpoints via TestClient, broadcast, AppState."""

import asyncio
import json
import time
from unittest import mock

import pytest

from orchestration.bus import MessageBus
from server.state import AppState
from server.broadcast import broadcast
from server.app import build_app, _build_llm_settings_payload
from world.state import WorldState
from tests.conftest import APPLICATION_SERVICES, SYSTEM_CONFIGURATION


# ---------------------------------------------------------------------------
# AppState
# ---------------------------------------------------------------------------


class TestAppState:
    def test_initial_state(self):
        state = AppState(
            system_configuration=SYSTEM_CONFIGURATION,
            application_services=APPLICATION_SERVICES,
        )
        assert isinstance(state.world, WorldState)
        assert isinstance(state.bus, MessageBus)
        assert state.ws_connections == []
        assert state.chat_history == []
        assert state.ws_connection_sequence == 0
        assert state.reset_callback is None
        assert state.start_callback is None
        assert state.resume_callback is None
        assert state.pause_callback is None
        assert state.engine_is_running is None
        assert state.terrain_data_for_replay is None
        assert state.build_status_for_replay is None

    def test_asset_version_is_timestamp(self):
        state = AppState(
            system_configuration=SYSTEM_CONFIGURATION,
            application_services=APPLICATION_SERVICES,
        )
        # Should be a numeric string
        assert state.asset_version.isdigit()


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------


class TestBroadcast:
    @pytest.fixture
    def state(self):
        return AppState(
            system_configuration=SYSTEM_CONFIGURATION,
            application_services=APPLICATION_SERVICES,
        )

    @pytest.mark.asyncio
    async def test_chat_message_appended_to_history(self, state):
        msg = {"type": "chat", "text": "hello"}
        await broadcast(state, msg)
        assert msg in state.chat_history

    @pytest.mark.asyncio
    async def test_phase_message_appended(self, state):
        msg = {"type": "phase", "phase": "survey"}
        await broadcast(state, msg)
        assert msg in state.chat_history

    @pytest.mark.asyncio
    async def test_agent_status_cached(self, state):
        msg = {"type": "agent_status", "agent": "cartographus", "status": "thinking"}
        await broadcast(state, msg)
        assert "cartographus" in state.agent_status_by_agent
        assert state.agent_status_by_agent["cartographus"]["status"] == "thinking"

    @pytest.mark.asyncio
    async def test_agent_status_not_in_history(self, state):
        msg = {"type": "agent_status", "agent": "cartographus", "status": "idle"}
        await broadcast(state, msg)
        assert msg not in state.chat_history

    @pytest.mark.asyncio
    async def test_dead_websocket_removed(self, state):
        mock_ws = mock.AsyncMock()
        mock_ws.send_json.side_effect = Exception("disconnected")
        state.ws_connections.append(mock_ws)
        await broadcast(state, {"type": "chat", "text": "test"})
        assert mock_ws not in state.ws_connections

    @pytest.mark.asyncio
    async def test_no_connections_no_error(self, state):
        await broadcast(state, {"type": "chat", "text": "test"})

    @pytest.mark.asyncio
    async def test_history_cap_enforced(self, state):
        cap = state.system_configuration.chat_history_max_messages
        for i in range(cap + 50):
            await broadcast(state, {"type": "chat", "text": f"msg {i}"})
        assert len(state.chat_history) <= cap


# ---------------------------------------------------------------------------
# FastAPI endpoints (TestClient)
# ---------------------------------------------------------------------------


try:
    from httpx import ASGITransport, AsyncClient
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
class TestFastAPIEndpoints:
    @pytest.fixture
    def app_and_state(self):
        cfg = SYSTEM_CONFIGURATION
        state = AppState(
            system_configuration=cfg,
            application_services=APPLICATION_SERVICES,
        )
        app = build_app(state, system_configuration=cfg)
        return app, state

    @pytest.mark.asyncio
    async def test_get_cities(self, app_and_state):
        app, state = app_and_state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/cities")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert "name" in data[0]

    @pytest.mark.asyncio
    async def test_get_session_no_scenario(self, app_and_state):
        app, state = app_and_state
        orig = state.run_session.scenario
        state.run_session.scenario = None
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/session")
            assert resp.status_code == 200
            assert resp.json()["has_active_scenario"] is False
        finally:
            state.run_session.scenario = orig

    @pytest.mark.asyncio
    async def test_get_session_with_scenario(self, app_and_state):
        app, state = app_and_state
        orig = state.run_session.scenario
        state.run_session.scenario = {
            "location": "Rome",
            "period": "around 44 BC",
            "focus_year": -44,
            "started_at_s": time.time(),
        }
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/session")
            data = resp.json()
            assert data["has_active_scenario"] is True
            assert data["city"] == "Rome"
        finally:
            state.run_session.scenario = orig

    @pytest.mark.asyncio
    async def test_get_llm_settings(self, app_and_state):
        app, state = app_and_state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/llm-settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "type" in data
        assert data["type"] == "llm_settings"
        assert "agents" in data
        assert "labels" in data

    @pytest.mark.asyncio
    async def test_post_llm_settings_no_callback(self, app_and_state):
        app, state = app_and_state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/llm-settings", json={"overrides": {}})
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_post_llm_settings_invalid_overrides_type(self, app_and_state):
        app, state = app_and_state

        async def _noop_llm_settings(_overrides):
            return None

        state.llm_settings_callback = _noop_llm_settings
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/llm-settings", json={"overrides": "not-a-dict"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_get_health(self, app_and_state):
        app, _state = app_and_state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "ok"

    @pytest.mark.asyncio
    async def test_get_logs(self, app_and_state):
        app, state = app_and_state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/logs")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_restart_server_no_callback(self, app_and_state):
        app, state = app_and_state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/restart-server")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    @pytest.mark.asyncio
    async def test_reset_timeline_no_callback(self, app_and_state):
        app, state = app_and_state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/reset-timeline")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    @pytest.mark.asyncio
    async def test_favicon_redirects(self, app_and_state):
        app, state = app_and_state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.get("/favicon.ico")
        assert resp.status_code == 302
        assert "/static/favicon.svg" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_index_page(self, app_and_state):
        app, state = app_and_state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# _build_llm_settings_payload
# ---------------------------------------------------------------------------


class TestBuildLlmSettingsPayload:
    def test_payload_structure(self):
        payload = _build_llm_settings_payload(
            SYSTEM_CONFIGURATION,
            APPLICATION_SERVICES,
        )
        assert payload["type"] == "llm_settings"
        assert "agents" in payload
        assert "labels" in payload
        assert "token_usage" in payload
        assert "xai_model_suggestions" in payload
        assert "model_id_suggestions" in payload

    def test_agents_have_provider_and_model(self):
        payload = _build_llm_settings_payload(
            SYSTEM_CONFIGURATION,
            APPLICATION_SERVICES,
        )
        for key, row in payload["agents"].items():
            assert "provider" in row
            assert "model" in row

    def test_api_key_masked(self):
        """openai_api_key should be replaced with has_openai_api_key."""
        from agents import llm_routing as llm_agents

        orig = llm_agents.get_runtime_overrides(
            application_services=APPLICATION_SERVICES,
        )
        llm_agents.set_runtime_overrides(
            {
                llm_agents.KEY_URBANISTA: {"openai_api_key": "sk-secret"},
            },
            application_services=APPLICATION_SERVICES,
        )
        try:
            payload = _build_llm_settings_payload(
                SYSTEM_CONFIGURATION,
                APPLICATION_SERVICES,
            )
            agent = payload["agents"][llm_agents.KEY_URBANISTA]
            assert "openai_api_key" not in agent
            assert agent.get("has_openai_api_key") is True
        finally:
            llm_agents.set_runtime_overrides(
                orig,
                application_services=APPLICATION_SERVICES,
            )
