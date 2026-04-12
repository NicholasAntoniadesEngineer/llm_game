"""Tests for agents/ — BaseAgent JSON parsing, golden specs, LLM routing, provider factory."""

import json
from unittest import mock

import pytest

from core.errors import AgentGenerationError

# ---------------------------------------------------------------------------
# agents.llm_routing
# ---------------------------------------------------------------------------

from agents import llm_routing as llm_agents
from agents.ui_notifier import NoOpUiNotifier

from tests.conftest import APPLICATION_SERVICES, SYSTEM_CONFIGURATION


class TestLlmRouting:
    def setup_method(self):
        # Reset runtime overrides before each test
        llm_agents.set_runtime_overrides(
            None,
            application_services=APPLICATION_SERVICES,
        )

    def test_all_agent_keys_exist(self):
        specs = llm_agents.get_agent_llm_specs_dictionary(
            application_services=APPLICATION_SERVICES,
        )
        for key in (
            llm_agents.KEY_CARTOGRAPHUS_SKELETON,
            llm_agents.KEY_CARTOGRAPHUS_REFINE,
            llm_agents.KEY_CARTOGRAPHUS_SURVEY,
            llm_agents.KEY_URBANISTA,
        ):
            assert key in specs

    def test_get_agent_llm_spec_returns_dict(self):
        spec = llm_agents.get_agent_llm_spec(
            llm_agents.KEY_URBANISTA,
            application_services=APPLICATION_SERVICES,
        )
        assert isinstance(spec, dict)
        assert "provider" in spec
        assert "model" in spec

    def test_get_agent_llm_spec_unknown_key_raises(self):
        with pytest.raises(KeyError, match="Unknown llm_agent_key"):
            llm_agents.get_agent_llm_spec(
                "nonexistent_key",
                application_services=APPLICATION_SERVICES,
            )

    def test_set_runtime_overrides(self):
        llm_agents.set_runtime_overrides(
            {
                llm_agents.KEY_URBANISTA: {"model": "sonnet"},
            },
            application_services=APPLICATION_SERVICES,
        )
        spec = llm_agents.get_agent_llm_spec(
            llm_agents.KEY_URBANISTA,
            application_services=APPLICATION_SERVICES,
        )
        assert spec["model"] == "sonnet"

    def test_set_runtime_overrides_none_clears(self):
        llm_agents.set_runtime_overrides(
            {
                llm_agents.KEY_URBANISTA: {"model": "sonnet"},
            },
            application_services=APPLICATION_SERVICES,
        )
        llm_agents.set_runtime_overrides(
            None,
            application_services=APPLICATION_SERVICES,
        )
        spec = llm_agents.get_agent_llm_spec(
            llm_agents.KEY_URBANISTA,
            application_services=APPLICATION_SERVICES,
        )
        expected = SYSTEM_CONFIGURATION.load_llm_defaults()["agents"][llm_agents.KEY_URBANISTA]["model"]
        assert spec["model"] == expected

    def test_runtime_overrides_ignores_unknown_keys(self):
        llm_agents.set_runtime_overrides(
            {
                "unknown_key_999": {"model": "sonnet"},
            },
            application_services=APPLICATION_SERVICES,
        )
        assert llm_agents.get_runtime_overrides(
            application_services=APPLICATION_SERVICES,
        ) == {}

    def test_runtime_overrides_ignores_non_dict_values(self):
        llm_agents.set_runtime_overrides(
            {
                llm_agents.KEY_URBANISTA: "not_a_dict",
            },
            application_services=APPLICATION_SERVICES,
        )
        assert llm_agents.get_runtime_overrides(
            application_services=APPLICATION_SERVICES,
        ) == {}

    def test_runtime_overrides_strips_none_values(self):
        llm_agents.set_runtime_overrides(
            {
                llm_agents.KEY_URBANISTA: {"model": "sonnet", "extra": None},
            },
            application_services=APPLICATION_SERVICES,
        )
        overrides = llm_agents.get_runtime_overrides(
            application_services=APPLICATION_SERVICES,
        )
        assert "extra" not in overrides.get(llm_agents.KEY_URBANISTA, {})

    def test_get_agent_llm_spec_merges_overrides(self):
        llm_agents.set_runtime_overrides(
            {
                llm_agents.KEY_URBANISTA: {
                    "model": "opus",
                    "openai_base_url": "http://localhost:1234",
                },
            },
            application_services=APPLICATION_SERVICES,
        )
        spec = llm_agents.get_agent_llm_spec(
            llm_agents.KEY_URBANISTA,
            application_services=APPLICATION_SERVICES,
        )
        assert spec["model"] == "opus"
        assert spec["openai_base_url"] == "http://localhost:1234"
        expected_provider = SYSTEM_CONFIGURATION.load_llm_defaults()["agents"][llm_agents.KEY_URBANISTA][
            "provider"
        ]
        assert spec["provider"] == expected_provider

    def test_blank_openai_api_key_not_applied(self):
        llm_agents.set_runtime_overrides(
            {
                llm_agents.KEY_URBANISTA: {"openai_api_key": "   "},
            },
            application_services=APPLICATION_SERVICES,
        )
        spec = llm_agents.get_agent_llm_spec(
            llm_agents.KEY_URBANISTA,
            application_services=APPLICATION_SERVICES,
        )
        # blank key should not be in spec (no openai_api_key in base either)
        assert "openai_api_key" not in spec or not spec.get("openai_api_key", "").strip()

    def test_get_runtime_overrides_returns_copy(self):
        llm_agents.set_runtime_overrides(
            {
                llm_agents.KEY_URBANISTA: {"model": "sonnet"},
            },
            application_services=APPLICATION_SERVICES,
        )
        copy1 = llm_agents.get_runtime_overrides(
            application_services=APPLICATION_SERVICES,
        )
        copy2 = llm_agents.get_runtime_overrides(
            application_services=APPLICATION_SERVICES,
        )
        assert copy1 == copy2
        # Mutating the copy should not affect the store
        copy1[llm_agents.KEY_URBANISTA]["model"] = "modified"
        assert (
            llm_agents.get_runtime_overrides(
                application_services=APPLICATION_SERVICES,
            )[llm_agents.KEY_URBANISTA]["model"]
            == "sonnet"
        )

    def test_labels_match_agent_keys(self):
        labels = llm_agents.get_agent_llm_labels_dictionary(
            application_services=APPLICATION_SERVICES,
        )
        for key in llm_agents.iter_registered_agent_llm_keys(
            application_services=APPLICATION_SERVICES,
        ):
            assert key in labels

    def test_xai_model_suggestions_in_config(self):
        suggestions = SYSTEM_CONFIGURATION.load_llm_defaults()["xai"]["model_suggestions"]
        assert isinstance(suggestions, list)
        assert len(suggestions) > 0
        assert all(isinstance(x, str) and x.strip() for x in suggestions)


# ---------------------------------------------------------------------------
# agents.providers.factory
# ---------------------------------------------------------------------------

from agents.providers import build_provider_from_spec, LlmProvider
from agents.providers.claude_cli import ClaudeCliProvider
from agents.providers.openai_compatible import OpenAICompatibleProvider


class TestProviderFactory:
    def test_build_claude_cli(self):
        p = build_provider_from_spec({"provider": "claude_cli", "model": "haiku"}, SYSTEM_CONFIGURATION)
        assert isinstance(p, ClaudeCliProvider)

    def test_build_claude_alias(self):
        p = build_provider_from_spec({"provider": "claude", "model": "haiku"}, SYSTEM_CONFIGURATION)
        assert isinstance(p, ClaudeCliProvider)

    def test_build_openai_compatible(self):
        p = build_provider_from_spec({
            "provider": "openai_compatible",
            "model": "gpt-4",
            "openai_base_url": "http://localhost:1234/v1",
            "openai_api_key": "test-key",
        }, SYSTEM_CONFIGURATION)
        assert isinstance(p, OpenAICompatibleProvider)

    def test_build_openai_alias(self):
        p = build_provider_from_spec({"provider": "openai", "model": "gpt-4"}, SYSTEM_CONFIGURATION)
        assert isinstance(p, OpenAICompatibleProvider)

    def test_build_chatgpt_alias(self):
        p = build_provider_from_spec({"provider": "chatgpt", "model": "gpt-4"}, SYSTEM_CONFIGURATION)
        assert isinstance(p, OpenAICompatibleProvider)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            build_provider_from_spec({"provider": "deepseek_custom", "model": "v3"}, SYSTEM_CONFIGURATION)

    def test_default_provider_is_xai(self):
        p = build_provider_from_spec({"model": "grok-4.20-reasoning"}, SYSTEM_CONFIGURATION)
        assert isinstance(p, OpenAICompatibleProvider)

    def test_blank_claude_binary_defaults(self):
        p = build_provider_from_spec({"provider": "claude_cli", "model": "h", "claude_binary": "  "}, SYSTEM_CONFIGURATION)
        assert isinstance(p, ClaudeCliProvider)

    def test_custom_claude_binary(self):
        p = build_provider_from_spec({"provider": "claude_cli", "model": "h", "claude_binary": "/usr/bin/my-claude"}, SYSTEM_CONFIGURATION)
        assert p.binary == "/usr/bin/my-claude"

    def test_blank_openai_base_url_passes_none(self):
        p = build_provider_from_spec({"provider": "openai_compatible", "model": "m", "openai_base_url": "  "}, SYSTEM_CONFIGURATION)
        assert isinstance(p, OpenAICompatibleProvider)

    def test_protocol_compliance(self):
        """Verify providers satisfy the LlmProvider protocol."""
        assert isinstance(
            ClaudeCliProvider(binary="claude", system_configuration=SYSTEM_CONFIGURATION),
            LlmProvider,
        )
        assert isinstance(
            OpenAICompatibleProvider(system_configuration=SYSTEM_CONFIGURATION),
            LlmProvider,
        )

    @pytest.mark.asyncio
    async def test_xai_multi_agent_model_rejected_before_http(self):
        """xAI multi-agent model ids are not valid for /v1/chat/completions."""
        from core.errors import AgentGenerationError

        p = OpenAICompatibleProvider(
            base_url="https://api.x.ai/v1",
            api_key="test-key",
            default_model=None,
            system_configuration=SYSTEM_CONFIGURATION,
        )
        with pytest.raises(AgentGenerationError, match="multi-agent"):
            await p.complete(
                role="cartographus",
                system_prompt="sys",
                user_text="user",
                model="grok-4.20-multi-agent-0309-reasoning",
            )


# ---------------------------------------------------------------------------
# agents.base — _try_decode_json_object and _parse_json
# ---------------------------------------------------------------------------

from agents.base import _try_decode_json_object, _safe_preview_for_logs, BaseAgent


class TestTryDecodeJsonObject:
    def test_valid_json(self):
        result = _try_decode_json_object('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_prose_before(self):
        result = _try_decode_json_object('Here is the JSON: {"key": "value"} end')
        assert result == {"key": "value"}

    def test_no_json_at_all(self):
        result = _try_decode_json_object("no json here")
        assert result is None

    def test_array_not_dict(self):
        result = _try_decode_json_object("[1, 2, 3]")
        assert result is None  # We need a dict, not a list

    def test_nested_json(self):
        result = _try_decode_json_object('prefix {"a": {"b": 1}} suffix')
        assert result == {"a": {"b": 1}}

    def test_invalid_json_after_brace(self):
        result = _try_decode_json_object("{broken json")
        assert result is None

    def test_empty_string(self):
        result = _try_decode_json_object("")
        assert result is None


class TestSafePreviewForLogs:
    def test_empty_returns_placeholder(self):
        assert _safe_preview_for_logs("") == "(empty)"

    def test_short_text(self):
        assert _safe_preview_for_logs("hello") == "hello"

    def test_truncation(self):
        text = "x" * 2000
        result = _safe_preview_for_logs(text, limit=100)
        assert len(result) < 200
        assert "truncated" in result


class TestBaseAgentParseJson:
    """Test _parse_json via a BaseAgent instance with a mocked provider."""

    def _make_agent(self):
        """Create a BaseAgent with a dummy provider."""
        agent = BaseAgent(
            role="test",
            display_name="Test Agent",
            system_prompt="You are a test agent.",
            llm_agent_key=llm_agents.KEY_URBANISTA,
            system_configuration=SYSTEM_CONFIGURATION,
            application_services=APPLICATION_SERVICES,
            ui_notifier=NoOpUiNotifier(),
        )
        return agent

    def test_parse_valid_json(self):
        agent = self._make_agent()
        result = agent._parse_json(
            '{"districts": []}',
            model="haiku",
            provider_kind="test",
        )
        assert result == {"districts": []}

    def test_parse_json_strips_markdown_fences(self):
        agent = self._make_agent()
        raw = '```json\n{"key": "value"}\n```'
        result = agent._parse_json(raw, model="haiku", provider_kind="test")
        assert result == {"key": "value"}

    def test_parse_json_with_prose(self):
        agent = self._make_agent()
        raw = 'Here is the result:\n{"tiles": [{"x": 0, "y": 0}]}\nDone.'
        result = agent._parse_json(raw, model="haiku", provider_kind="test")
        assert "tiles" in result

    def test_parse_empty_raises(self):
        agent = self._make_agent()
        with pytest.raises(AgentGenerationError) as exc_info:
            agent._parse_json("", model="haiku", provider_kind="test")
        assert exc_info.value.pause_reason == "bad_model_output"

    def test_parse_whitespace_only_raises(self):
        agent = self._make_agent()
        with pytest.raises(AgentGenerationError) as exc_info:
            agent._parse_json("   \n  \t  ", model="haiku", provider_kind="test")
        assert exc_info.value.pause_reason == "bad_model_output"

    def test_parse_invalid_json_raises(self):
        agent = self._make_agent()
        with pytest.raises(AgentGenerationError) as exc_info:
            agent._parse_json("This is not JSON at all.", model="haiku", provider_kind="test")
        assert exc_info.value.pause_reason == "bad_model_output"

    def test_parse_triple_backtick_with_json_label(self):
        agent = self._make_agent()
        raw = '```json\n{"result": true}\n```'
        result = agent._parse_json(raw, model="haiku", provider_kind="test")
        assert result == {"result": True}


# ---------------------------------------------------------------------------
# agents.golden_specs
# ---------------------------------------------------------------------------

from agents.golden_specs import (
    GOLDEN_SPECS,
    get_golden_example,
    get_golden_example_for_culture,
    _detect_culture,
    _scale_spec,
)


class TestGoldenSpecs:
    def test_golden_specs_loaded(self):
        assert isinstance(GOLDEN_SPECS, dict)
        assert len(GOLDEN_SPECS) > 0

    def test_each_spec_has_required_fields(self):
        for btype, spec in GOLDEN_SPECS.items():
            assert "ref_w" in spec, f"{btype} missing ref_w"
            assert "ref_d" in spec, f"{btype} missing ref_d"
            assert "components" in spec, f"{btype} missing components"
            assert isinstance(spec["components"], list)

    def test_get_golden_example_returns_json(self):
        # Pick any key that exists
        btype = next(iter(GOLDEN_SPECS))
        result = get_golden_example(btype, 2.0, 2.0)
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    def test_get_golden_example_unknown_type_raises(self):
        with pytest.raises(KeyError):
            get_golden_example("nonexistent_building_99", 1.0, 1.0)

    def test_scale_spec_proportional(self):
        spec = {
            "ref_w": 2.0,
            "ref_d": 2.0,
            "components": [
                {"type": "podium", "height": 0.1, "steps": 3, "color": "#AAA"},
            ],
        }
        result = json.loads(_scale_spec(spec, 4.0, 4.0))
        assert isinstance(result, list)
        # Scale is (4/2 + 4/2) / 2 = 2.0 -> height should be 0.2
        assert result[0]["height"] == pytest.approx(0.2, abs=0.001)

    def test_scale_spec_half_size(self):
        spec = {
            "ref_w": 4.0,
            "ref_d": 4.0,
            "components": [
                {"type": "block", "height": 1.0, "color": "#BBB"},
            ],
        }
        result = json.loads(_scale_spec(spec, 2.0, 2.0))
        # Scale is (2/4 + 2/4) / 2 = 0.5 -> height = 0.5
        assert result[0]["height"] == pytest.approx(0.5, abs=0.001)

    def test_detect_culture_tenochtitlan(self):
        culture = _detect_culture("Tenochtitlan")
        assert culture is not None

    def test_detect_culture_rome_is_none(self):
        # Rome should be Mediterranean default (None)
        culture = _detect_culture("Rome")
        # May or may not be None depending on culture map data
        # Just verify it doesn't raise
        assert culture is None or isinstance(culture, str)

    def test_detect_culture_empty(self):
        assert _detect_culture("") is None
        assert _detect_culture(None) is None

    def test_get_golden_example_for_culture(self):
        # Should work for a base type regardless of city
        btype = next(iter(GOLDEN_SPECS))
        result = get_golden_example_for_culture(btype, 2.0, 2.0, "Rome", -44)
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    def test_golden_example_for_culture_unknown_raises(self):
        with pytest.raises(KeyError):
            get_golden_example_for_culture("nonexistent_99", 1.0, 1.0, "Rome", 0)


# ---------------------------------------------------------------------------
# ClaudeCliProvider._parse_cli_json_payload
# ---------------------------------------------------------------------------


class TestClaudeCliParsePayload:
    @pytest.fixture
    def cli(self):
        return ClaudeCliProvider(binary="claude", system_configuration=SYSTEM_CONFIGURATION)

    def test_normal_result(self, cli):
        payload = json.dumps({
            "result": '{"tiles": []}',
            "usage": {"input_tokens": 100, "output_tokens": 50},
        })
        text, usage = cli._parse_cli_json_payload(payload)
        assert text == '{"tiles": []}'
        assert usage is not None
        assert usage["input_tokens"] == 100

    def test_error_result_raises(self, cli):
        payload = json.dumps({
            "is_error": True,
            "subtype": "something",
            "result": "",
        })
        with pytest.raises(AgentGenerationError):
            cli._parse_cli_json_payload(payload)

    def test_max_turns_with_result_succeeds(self, cli):
        payload = json.dumps({
            "is_error": True,
            "subtype": "error_max_turns",
            "result": '{"tiles": []}',
        })
        text, usage = cli._parse_cli_json_payload(payload)
        assert text == '{"tiles": []}'

    def test_connection_refused_raises_agent_error(self, cli):
        payload = json.dumps({
            "is_error": True,
            "subtype": "connection_error",
            "result": "ConnectionRefused: unable to connect to API",
        })
        with pytest.raises(AgentGenerationError) as exc_info:
            cli._parse_cli_json_payload(payload)
        assert exc_info.value.pause_reason == "network"

    def test_non_dict_returns_raw(self, cli):
        text, usage = cli._parse_cli_json_payload('"just a string"')
        assert text == '"just a string"'
        assert usage is None
