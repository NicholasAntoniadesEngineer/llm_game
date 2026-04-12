"""Tests for core/ — config, errors, persistence, token_usage, run_log."""

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# core.errors
# ---------------------------------------------------------------------------

from core.errors import (
    AgentGenerationError,
    EternalCitiesError,
    SaveIndexError,
    UrbanistaValidationError,
    classify_agent_failure,
)


class TestErrorHierarchy:
    def test_base_exception_exists(self):
        assert issubclass(EternalCitiesError, Exception)

    def test_agent_generation_error_is_eternal(self):
        assert issubclass(AgentGenerationError, EternalCitiesError)

    def test_urbanista_validation_error_is_eternal(self):
        assert issubclass(UrbanistaValidationError, EternalCitiesError)

    def test_agent_generation_error_fields(self):
        err = AgentGenerationError("rate_limit", "Too many requests")
        assert err.pause_reason == "rate_limit"
        assert err.pause_detail == "Too many requests"
        assert "rate_limit" in str(err)
        assert "Too many requests" in str(err)

    def test_urbanista_validation_error_message(self):
        err = UrbanistaValidationError("bad tile")
        assert "bad tile" in str(err)

    def test_save_index_error_is_eternal(self):
        assert issubclass(SaveIndexError, EternalCitiesError)


class TestClassifyAgentFailure:
    def test_file_not_found(self):
        reason, detail = classify_agent_failure("", FileNotFoundError("claude"))
        assert reason == "cli_missing"

    def test_timeout_error(self):
        reason, _ = classify_agent_failure("", TimeoutError("timed out"))
        assert reason == "network"

    def test_connection_refused(self):
        reason, _ = classify_agent_failure("", ConnectionRefusedError())
        assert reason == "network"

    def test_broken_pipe(self):
        reason, _ = classify_agent_failure("", BrokenPipeError())
        assert reason == "network"

    def test_connection_reset(self):
        reason, _ = classify_agent_failure("", ConnectionResetError())
        assert reason == "network"

    def test_rate_limit_429(self):
        reason, _ = classify_agent_failure("HTTP 429 Too Many Requests", None)
        assert reason == "rate_limit"

    def test_rate_limit_text(self):
        reason, _ = classify_agent_failure("rate limit exceeded", None)
        assert reason == "rate_limit"

    def test_503_service_unavailable(self):
        reason, _ = classify_agent_failure("503 Service Unavailable", None)
        assert reason == "api_error"

    def test_502_bad_gateway(self):
        reason, _ = classify_agent_failure("502 Bad Gateway", None)
        assert reason == "api_error"

    def test_504_gateway_timeout(self):
        reason, _ = classify_agent_failure("504 Gateway Timeout", None)
        assert reason == "api_error"

    def test_overloaded(self):
        reason, _ = classify_agent_failure("server overloaded, try later", None)
        assert reason == "api_error"

    def test_authentication_401(self):
        reason, _ = classify_agent_failure("401 Unauthorized", None)
        assert reason == "api_error"

    def test_api_key_error(self):
        reason, _ = classify_agent_failure("invalid api key", None)
        assert reason == "api_error"

    def test_dns_failure(self):
        reason, _ = classify_agent_failure("getaddrinfo failed", None)
        assert reason == "network"

    def test_connection_refused_text(self):
        reason, _ = classify_agent_failure("connection refused", None)
        assert reason == "network"

    def test_econnreset(self):
        reason, _ = classify_agent_failure("ECONNRESET", None)
        assert reason == "network"

    def test_empty_stderr_no_exc(self):
        reason, _ = classify_agent_failure("", None)
        assert reason == "api_error"

    def test_unknown_stderr(self):
        reason, detail = classify_agent_failure("something weird happened", None)
        assert reason == "unknown"
        assert "something weird happened" in detail

    def test_detail_truncated_at_400(self):
        long_msg = "x" * 600
        _, detail = classify_agent_failure(long_msg, None)
        assert len(detail) <= 400

    def test_oserror_network_errno(self):
        exc = OSError(60, "Operation timed out")
        reason, _ = classify_agent_failure("", exc)
        assert reason == "network"

    def test_oserror_non_network_errno(self):
        exc = OSError(2, "No such file")
        # OSError with errno 2 is a FileNotFoundError subclass, classified as cli_missing
        reason, _ = classify_agent_failure("", exc)
        assert reason == "cli_missing"


# ---------------------------------------------------------------------------
# core.config
# ---------------------------------------------------------------------------

from core import config


class TestConfig:
    def test_grid_defaults_positive(self):
        assert config.GRID_WIDTH > 0
        assert config.GRID_HEIGHT > 0

    def test_max_districts_positive(self):
        assert config.MAX_DISTRICTS > 0

    def test_step_delay_non_negative(self):
        assert config.STEP_DELAY >= 0

    def test_chunk_size_positive(self):
        assert config.CHUNK_SIZE > 0

    def test_agents_dict_has_expected_keys(self):
        assert "cartographus" in config.AGENTS
        assert "urbanista" in config.AGENTS
        for key, agent in config.AGENTS.items():
            assert "name" in agent
            assert "purpose" in agent
            assert "color" in agent

    def test_cities_loaded(self):
        assert isinstance(config.CITIES, list)
        assert len(config.CITIES) > 0
        for city in config.CITIES:
            assert "name" in city
            assert "year_min" in city
            assert "year_max" in city

    def test_format_year_bc(self):
        assert config.format_year(-44) == "44 BC"

    def test_format_year_ad(self):
        assert config.format_year(100) == "100"

    def test_format_year_zero(self):
        # Year 0 is technically AD
        assert config.format_year(0) == "0"

    def test_get_city_found(self):
        city = config.get_city("Rome")
        assert city is not None
        assert city["name"] == "Rome"

    def test_get_city_case_insensitive(self):
        city = config.get_city("rome")
        assert city is not None
        assert city["name"] == "Rome"

    def test_get_city_not_found(self):
        city = config.get_city("Atlantis")
        assert city is None

    def test_create_scenario(self):
        scenario = config.create_scenario("Rome", -44)
        assert scenario["location"] == "Rome"
        assert scenario["focus_year"] == -44
        assert "period" in scenario
        assert "year_start" in scenario
        assert "year_end" in scenario
        assert scenario["year_start"] < scenario["year_end"]
        assert "started_at_s" in scenario

    def test_create_scenario_clamps_year(self):
        city = config.get_city("Rome")
        # Year way below minimum
        scenario = config.create_scenario("Rome", -10000)
        assert scenario["focus_year"] == city["year_min"]

    def test_create_scenario_clamps_year_high(self):
        city = config.get_city("Rome")
        scenario = config.create_scenario("Rome", 99999)
        assert scenario["focus_year"] == city["year_max"]

    def test_create_scenario_unknown_city_defaults_to_first(self):
        scenario = config.create_scenario("Atlantis", 100)
        assert scenario["location"] == config.CITIES[0]["name"]

    def test_env_var_override_grid_width(self):
        # Verify the pattern works (grid width is read from env at import time,
        # so we can at least test that the current value is an int)
        assert isinstance(config.GRID_WIDTH, int)


# ---------------------------------------------------------------------------
# core.token_usage
# ---------------------------------------------------------------------------

from core.token_usage import (
    TokenUsageSnapshot,
    TokenUsageStore,
    estimate_tokens_from_text,
)


class TestTokenUsageSnapshot:
    def test_frozen_dataclass(self):
        snap = TokenUsageSnapshot(
            agent_key="urbanista",
            provider="claude_cli",
            model="haiku",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            exact=True,
            timestamp_s=time.time(),
        )
        assert snap.agent_key == "urbanista"
        with pytest.raises(AttributeError):
            snap.agent_key = "other"  # type: ignore


class TestTokenUsageStore:
    def test_record_and_retrieve(self):
        store = TokenUsageStore()
        store.record(
            agent_key="urbanista",
            provider="claude_cli",
            model="haiku",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            exact=True,
        )
        payload = store.to_payload()
        assert "urbanista" in payload
        assert payload["urbanista"]["last"]["prompt_tokens"] == 100
        assert payload["urbanista"]["last"]["completion_tokens"] == 50
        assert payload["urbanista"]["last"]["exact"] is True
        assert payload["urbanista"]["total"]["total_tokens"] == 150

    def test_cumulative_totals(self):
        store = TokenUsageStore()
        for _ in range(3):
            store.record(
                agent_key="urbanista",
                provider="cli",
                model="haiku",
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                exact=False,
            )
        payload = store.to_payload()
        assert payload["urbanista"]["total"]["prompt_tokens"] == 30
        assert payload["urbanista"]["total"]["completion_tokens"] == 15
        assert payload["urbanista"]["total"]["total_tokens"] == 45

    def test_negative_tokens_clamped(self):
        store = TokenUsageStore()
        store.record(
            agent_key="test",
            provider="test",
            model="test",
            prompt_tokens=-10,
            completion_tokens=-5,
            total_tokens=-15,
            exact=False,
        )
        payload = store.to_payload()
        assert payload["test"]["last"]["prompt_tokens"] == 0
        assert payload["test"]["last"]["completion_tokens"] == 0
        assert payload["test"]["last"]["total_tokens"] == 0

    def test_multiple_agents(self):
        store = TokenUsageStore()
        store.record(agent_key="a", provider="p", model="m", prompt_tokens=10,
                     completion_tokens=5, total_tokens=15, exact=True)
        store.record(agent_key="b", provider="p", model="m", prompt_tokens=20,
                     completion_tokens=10, total_tokens=30, exact=True)
        payload = store.to_payload()
        assert "a" in payload
        assert "b" in payload
        assert payload["a"]["total"]["total_tokens"] == 15
        assert payload["b"]["total"]["total_tokens"] == 30

    def test_empty_store_payload(self):
        store = TokenUsageStore()
        assert store.to_payload() == {}


class TestTokenUsageCallCount:
    def test_call_count_starts_at_zero(self):
        store = TokenUsageStore()
        assert store.call_count("urbanista") == 0

    def test_call_count_increments(self):
        store = TokenUsageStore()
        for _ in range(5):
            store.record(
                agent_key="urbanista",
                provider="cli",
                model="haiku",
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                exact=False,
            )
        assert store.call_count("urbanista") == 5

    def test_call_count_per_agent(self):
        store = TokenUsageStore()
        store.record(agent_key="a", provider="p", model="m", prompt_tokens=10,
                     completion_tokens=5, total_tokens=15, exact=True)
        store.record(agent_key="a", provider="p", model="m", prompt_tokens=10,
                     completion_tokens=5, total_tokens=15, exact=True)
        store.record(agent_key="b", provider="p", model="m", prompt_tokens=10,
                     completion_tokens=5, total_tokens=15, exact=True)
        assert store.call_count("a") == 2
        assert store.call_count("b") == 1


class TestGetTokenSummary:
    def test_empty_summary(self):
        from core.token_usage import get_token_summary, STORE
        # Save and restore state
        old_last = STORE._last_by_agent.copy()
        old_totals = STORE._totals_by_agent.copy()
        old_counts = STORE._call_counts.copy()
        STORE._last_by_agent.clear()
        STORE._totals_by_agent.clear()
        STORE._call_counts.clear()
        try:
            summary = get_token_summary()
            assert "agents" in summary
            assert "by_group" in summary
            assert "avg_tokens_per_building" in summary
            assert "estimated_cost_usd" in summary
            assert summary["avg_tokens_per_building"] == 0
            assert summary["urbanista_calls"] == 0
        finally:
            STORE._last_by_agent = old_last
            STORE._totals_by_agent = old_totals
            STORE._call_counts = old_counts

    def test_summary_with_data(self):
        from core.token_usage import get_token_summary, STORE
        old_last = STORE._last_by_agent.copy()
        old_totals = STORE._totals_by_agent.copy()
        old_counts = STORE._call_counts.copy()
        STORE._last_by_agent.clear()
        STORE._totals_by_agent.clear()
        STORE._call_counts.clear()
        try:
            STORE.record(
                agent_key="urbanista",
                provider="cli",
                model="haiku",
                prompt_tokens=1000,
                completion_tokens=500,
                total_tokens=1500,
                exact=True,
            )
            summary = get_token_summary()
            assert summary["urbanista_calls"] == 1
            assert summary["avg_tokens_per_building"] == 1500
            assert summary["total_tokens"] == 1500
            assert summary["estimated_cost_usd"] > 0
            assert "urbanista" in summary["by_group"]
            assert summary["by_group"]["urbanista"]["call_count"] == 1
        finally:
            STORE._last_by_agent = old_last
            STORE._totals_by_agent = old_totals
            STORE._call_counts = old_counts


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens_from_text("") == 0

    def test_short_text(self):
        result = estimate_tokens_from_text("hello")
        assert result >= 1

    def test_longer_text(self):
        text = "a" * 400
        result = estimate_tokens_from_text(text)
        assert result == 100  # 400 / 4

    def test_minimum_is_one(self):
        result = estimate_tokens_from_text("a")
        assert result >= 1


# ---------------------------------------------------------------------------
# core.run_log
# ---------------------------------------------------------------------------

from core import run_log


class TestRunLog:
    def setup_method(self):
        """Reset run log state before each test."""
        run_log._LOG_BUFFER.clear()
        run_log._handler = None
        run_log._RUN_START = None

    def test_init_run_log(self):
        run_log.init_run_log()
        assert run_log._handler is not None
        assert run_log._RUN_START is not None
        # Buffer should have the header
        assert len(run_log._LOG_BUFFER) > 0

    def test_init_run_log_idempotent(self):
        run_log.init_run_log()
        handler1 = run_log._handler
        run_log.init_run_log()
        assert run_log._handler is handler1  # Should not create a second handler

    def test_log_event(self):
        run_log.log_event("test", "something happened", key="value")
        found = any("TEST" in line and "something happened" in line for line in run_log._LOG_BUFFER)
        assert found

    def test_log_event_kwargs(self):
        run_log.log_event("engine", "started build", district="Forum")
        found = any("district: Forum" in line for line in run_log._LOG_BUFFER)
        assert found

    def test_get_log_text(self):
        run_log._RUN_START = time.time()
        run_log.log_event("test", "hello world")
        text = run_log.get_log_text()
        assert "ETERNAL CITIES" in text
        assert "hello world" in text

    def test_clear_log(self):
        run_log.log_event("test", "before clear")
        run_log.clear_log()
        assert len(run_log._LOG_BUFFER) == 1  # "Buffer cleared" message
        assert "cleared" in run_log._LOG_BUFFER[0].lower()

    def test_max_log_lines(self):
        assert run_log.MAX_LOG_LINES == 10_000
        # Verify the deque maxlen
        assert run_log._LOG_BUFFER.maxlen == run_log.MAX_LOG_LINES


# ---------------------------------------------------------------------------
# core.persistence (mocked filesystem)
# ---------------------------------------------------------------------------

from core import persistence
from core.fingerprint import compute_run_fingerprint
from world.state import WorldState


class TestPersistenceChunkKey:
    def test_chunk_key_origin(self):
        assert persistence._chunk_key(0, 0, config.CHUNK_SIZE) == (0, 0)

    def test_chunk_key_within_chunk(self):
        cs = config.CHUNK_SIZE
        assert persistence._chunk_key(cs - 1, cs - 1, cs) == (0, 0)

    def test_chunk_key_next_chunk(self):
        cs = config.CHUNK_SIZE
        assert persistence._chunk_key(cs, 0, cs) == (1, 0)
        assert persistence._chunk_key(0, cs, cs) == (0, 1)

    def test_chunk_key_negative(self):
        # Python integer division with negatives
        assert persistence._chunk_key(-1, -1, config.CHUNK_SIZE) == (-1, -1)


class TestPersistenceChunkFilename:
    def test_filename_format(self):
        assert persistence._chunk_filename(0, 0) == "chunk_0_0.json"
        assert persistence._chunk_filename(3, 5) == "chunk_3_5.json"


class TestPersistenceSaveLoad:
    def setup_method(self):
        self._orig_saves = persistence.SAVES_DIR
        self._orig_chunks = persistence.CHUNKS_DIR
        self._orig_index = persistence.INDEX_FILE
        self._orig_districts = persistence.DISTRICTS_CACHE
        self._orig_surveys = persistence.SURVEYS_CACHE
        self._tmpdir = Path(tempfile.mkdtemp())
        persistence.SAVES_DIR = self._tmpdir / "saves"
        persistence.CHUNKS_DIR = persistence.SAVES_DIR / "chunks"
        persistence.INDEX_FILE = persistence.SAVES_DIR / "index.json"
        persistence.DISTRICTS_CACHE = persistence.SAVES_DIR / "districts_cache.json"
        persistence.SURVEYS_CACHE = persistence.SAVES_DIR / "surveys_cache.json"

    def teardown_method(self):
        persistence.SAVES_DIR = self._orig_saves
        persistence.CHUNKS_DIR = self._orig_chunks
        persistence.INDEX_FILE = self._orig_index
        persistence.DISTRICTS_CACHE = self._orig_districts
        persistence.SURVEYS_CACHE = self._orig_surveys
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_save_and_load_round_trip(self):
        world = WorldState(chunk_size_tiles=config.CHUNK_SIZE)
        world.current_period = "Republican Rome"
        world.current_year = -44
        world.turn = 5
        world.place_tile(10, 20, {"terrain": "building", "building_name": "Temple"})
        world.place_tile(11, 20, {"terrain": "road"})

        scenario = {
            "location": "Rome",
            "period": "around 44 BC",
            "focus_year": -44,
            "started_at_s": time.time(),
            "year_start": -50,
            "year_end": -40,
            "ruler": "Caesar",
        }

        chat = [{"type": "chat", "text": "hello"}]
        persistence.save_state(
            world,
            chat,
            district_index=2,
            districts=[{"name": "Forum"}],
            generation=3,
            scenario=scenario,
        )

        # Load into a fresh world
        world2 = WorldState(chunk_size_tiles=config.CHUNK_SIZE)
        result = persistence.load_state(world2)
        assert result is not None
        loaded_chat, loaded_idx, loaded_districts, loaded_gen, _loaded_scen = result
        assert loaded_idx == 2
        assert loaded_gen == 3
        assert loaded_chat == chat
        assert len(loaded_districts) == 1
        assert world2.current_period == "Republican Rome"
        assert world2.current_year == -44
        assert world2.turn == 5
        # Tiles should be loaded
        t = world2.get_tile(10, 20)
        assert t is not None
        assert t.terrain == "building"

    def test_load_state_no_index_returns_none(self):
        world = WorldState(chunk_size_tiles=config.CHUNK_SIZE)
        result = persistence.load_state(world)
        assert result is None

    def test_clear_saves(self):
        persistence._ensure_dirs()
        assert persistence.SAVES_DIR.exists()
        persistence.clear_saves()
        assert not persistence.SAVES_DIR.exists()

    def test_clear_saves_nonexistent(self):
        # Should not raise
        persistence.clear_saves()

    def test_districts_cache_round_trip(self):
        districts = [{"name": "Forum", "region": {"x1": 0, "y1": 0, "x2": 10, "y2": 10}}]
        scen = {"location": "Rome", "period": "p", "focus_year": 0, "started_at_s": 1.0, "year_start": 0, "year_end": 1, "ruler": "x"}
        fp = compute_run_fingerprint(scen, config.CHUNK_SIZE, config.GRID_WIDTH, config.GRID_HEIGHT)
        persistence.save_districts_cache(districts, "A description", run_fingerprint=fp)
        result = persistence.load_districts_cache(expected_run_fingerprint=fp)
        assert result is not None
        loaded_districts, map_desc = result
        assert len(loaded_districts) == 1
        assert loaded_districts[0]["name"] == "Forum"
        assert map_desc == "A description"

    def test_districts_cache_missing_returns_none(self):
        result = persistence.load_districts_cache()
        assert result is None

    def test_districts_cache_malformed_skipped(self):
        persistence._ensure_dirs()
        # Write malformed district entry
        data = {"districts": [{"bad": "data"}], "map_description": ""}
        persistence.DISTRICTS_CACHE.write_text(json.dumps(data))
        assert persistence.load_districts_cache() is None

    def test_surveys_cache_round_trip(self):
        surveys = {"Forum": [{"name": "Temple", "tiles": [{"x": 0, "y": 0}]}]}
        scen = {"location": "Rome", "period": "p", "focus_year": 0, "started_at_s": 1.0, "year_start": 0, "year_end": 1, "ruler": "x"}
        fp = compute_run_fingerprint(scen, config.CHUNK_SIZE, config.GRID_WIDTH, config.GRID_HEIGHT)
        persistence.save_surveys_cache(surveys, run_fingerprint=fp)
        result = persistence.load_surveys_cache(expected_run_fingerprint=fp)
        assert "Forum" in result
        assert len(result["Forum"]) == 1

    def test_surveys_cache_missing_returns_empty(self):
        result = persistence.load_surveys_cache()
        assert result == {}

    def test_surveys_cache_not_dict_raises(self):
        persistence._ensure_dirs()
        persistence.SURVEYS_CACHE.write_text(json.dumps([1, 2, 3]))
        with pytest.raises(ValueError, match="not a dict"):
            persistence.load_surveys_cache()

    def test_surveys_cache_entry_not_list_raises(self):
        persistence._ensure_dirs()
        persistence.SURVEYS_CACHE.write_text(
            json.dumps(
                {
                    "cache_wrap_version": 1,
                    "run_fingerprint": "0" * 32,
                    "plans": {"Forum": "not_a_list"},
                }
            )
        )
        with pytest.raises(ValueError, match="not a list"):
            persistence.load_surveys_cache(expected_run_fingerprint="0" * 32)

    def test_atomic_write(self):
        path = self._tmpdir / "test.txt"
        persistence._atomic_write(path, "hello")
        assert path.read_text() == "hello"

    def test_atomic_write_creates_parent(self):
        path = self._tmpdir / "sub" / "deep" / "test.txt"
        persistence._atomic_write(path, "nested")
        assert path.read_text() == "nested"


class TestMergeLlmOverrides:
    def test_merge_basic(self):
        current = {"urbanista": {"provider": "claude_cli", "model": "haiku"}}
        incoming = {"urbanista": {"model": "sonnet"}}
        result = persistence.merge_llm_overrides_from_save(current, incoming)
        assert result["urbanista"]["model"] == "sonnet"
        assert result["urbanista"]["provider"] == "claude_cli"

    def test_merge_blank_api_key_keeps_previous(self):
        current = {"urbanista": {"openai_api_key": "sk-secret"}}
        incoming = {"urbanista": {"openai_api_key": ""}}
        result = persistence.merge_llm_overrides_from_save(current, incoming)
        assert result["urbanista"]["openai_api_key"] == "sk-secret"

    def test_merge_none_value_skipped(self):
        current = {"urbanista": {"model": "haiku"}}
        incoming = {"urbanista": {"model": None}}
        result = persistence.merge_llm_overrides_from_save(current, incoming)
        assert result["urbanista"]["model"] == "haiku"

    def test_merge_unknown_agent_key_ignored(self):
        current = {}
        incoming = {"nonexistent_agent": {"model": "test"}}
        result = persistence.merge_llm_overrides_from_save(current, incoming)
        assert "nonexistent_agent" not in result
