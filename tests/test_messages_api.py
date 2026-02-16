"""Tests for Messages API improvements: prompt caching and structured output.

Phase 1: Prompt Caching -- cacheable_system() helper and extract_usage()
    with cache_creation_input_tokens.
Phase 2: Structured Output -- pydantic_to_response_format() helper,
    ClassificationResult Pydantic migration, response_format at call sites.

All tests mock the Anthropic API -- no real API calls are made.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from pinwheel.ai.classifier import ClassificationResult
from pinwheel.ai.usage import (
    cacheable_system,
    compute_cost,
    extract_usage,
    pydantic_to_response_format,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(text: str) -> MagicMock:
    """Build a mock Anthropic Messages response with usage fields."""
    mock = MagicMock()
    mock.content = [MagicMock(text=text)]
    mock.usage.input_tokens = 50
    mock.usage.output_tokens = 20
    mock.usage.cache_read_input_tokens = 0
    mock.usage.cache_creation_input_tokens = 0
    return mock


# ---------------------------------------------------------------------------
# Phase 1: Prompt Caching Tests
# ---------------------------------------------------------------------------


class TestCacheableSystem:
    """Verify cacheable_system() produces the correct block format."""

    def test_basic_format(self) -> None:
        """Output must be a list with one text block + cache_control."""
        result = cacheable_system("You are a helpful assistant.")
        assert isinstance(result, list)
        assert len(result) == 1
        block = result[0]
        assert block["type"] == "text"
        assert block["text"] == "You are a helpful assistant."
        assert block["cache_control"] == {"type": "ephemeral"}

    def test_empty_string(self) -> None:
        """Empty prompt still produces a valid block."""
        result = cacheable_system("")
        assert result[0]["text"] == ""
        assert result[0]["cache_control"] == {"type": "ephemeral"}

    def test_long_prompt(self) -> None:
        """Long prompts get the same wrapper structure."""
        long_text = "x" * 10_000
        result = cacheable_system(long_text)
        assert result[0]["text"] == long_text
        assert result[0]["cache_control"]["type"] == "ephemeral"

    def test_multiline_prompt(self) -> None:
        """Multiline prompts are preserved exactly."""
        text = "Line 1\nLine 2\nLine 3"
        result = cacheable_system(text)
        assert result[0]["text"] == text


class TestExtractUsageCacheCreation:
    """Verify extract_usage() handles cache_creation_input_tokens."""

    def test_cache_creation_present(self) -> None:
        """Extract cache_creation_input_tokens when present."""
        response = MagicMock()
        response.usage.input_tokens = 100
        response.usage.output_tokens = 200
        response.usage.cache_read_input_tokens = 0
        response.usage.cache_creation_input_tokens = 1500
        inp, out, cache_read, cache_create = extract_usage(response)
        assert cache_create == 1500
        assert cache_read == 0

    def test_cache_creation_none(self) -> None:
        """Return 0 when cache_creation_input_tokens is None."""
        response = MagicMock()
        response.usage.input_tokens = 100
        response.usage.output_tokens = 200
        response.usage.cache_read_input_tokens = 50
        response.usage.cache_creation_input_tokens = None
        _, _, cache_read, cache_create = extract_usage(response)
        assert cache_read == 50
        assert cache_create == 0

    def test_cache_creation_missing_attribute(self) -> None:
        """Return 0 when attribute doesn't exist."""
        response = MagicMock(spec=["usage"])
        response.usage = MagicMock(
            spec=["input_tokens", "output_tokens"]
        )
        response.usage.input_tokens = 100
        response.usage.output_tokens = 200
        _, _, cache_read, cache_create = extract_usage(response)
        assert cache_read == 0
        assert cache_create == 0


class TestComputeCostWithCacheCreation:
    """Verify compute_cost() accounts for cache creation premium."""

    def test_cache_creation_cost(self) -> None:
        """Cache creation tokens cost 25% more than standard input."""
        cost = compute_cost(
            model="claude-sonnet-4-5-20250929",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=1000,
        )
        # Sonnet cache write rate: 3.75 per MTok
        expected = 1000 * 3.75 / 1_000_000
        assert abs(cost - expected) < 1e-8

    def test_combined_cache_cost(self) -> None:
        """Both cache read and creation tokens in the same call."""
        cost = compute_cost(
            model="claude-sonnet-4-5-20250929",
            input_tokens=500,
            output_tokens=100,
            cache_read_tokens=2000,
            cache_creation_tokens=1000,
        )
        expected = (
            500 * 3.00
            + 100 * 15.00
            + 2000 * 0.30
            + 1000 * 3.75
        ) / 1_000_000
        assert abs(cost - expected) < 1e-8


class TestCallSitesCacheableSystem:
    """Verify AI call sites pass system prompts as cacheable blocks.

    We check that the system parameter passed to messages.create() is a
    list (not a string), which proves cacheable_system() is being used.
    """

    async def test_classifier_uses_cacheable_system(self) -> None:
        """classify_injection passes system as cacheable blocks."""
        from pinwheel.ai.classifier import classify_injection

        payload = (
            '{"classification":"legitimate",'
            '"confidence":0.9,"reason":"ok"}'
        )
        mock_resp = _mock_response(payload)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=mock_resp
        )

        with patch(
            "pinwheel.ai.classifier.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            await classify_injection("Test proposal", "fake-key")

        call_kwargs = mock_client.messages.create.call_args.kwargs
        system_arg = call_kwargs["system"]
        assert isinstance(system_arg, list)
        assert system_arg[0]["cache_control"] == {"type": "ephemeral"}

    async def test_interpreter_v1_uses_cacheable_system(self) -> None:
        """interpret_proposal passes system as cacheable blocks."""
        from pinwheel.ai.interpreter import interpret_proposal
        from pinwheel.models.rules import RuleSet

        payload = json.dumps({
            "parameter": "three_point_value",
            "new_value": 4,
            "old_value": 3,
            "impact_analysis": "More threes",
            "confidence": 0.9,
            "clarification_needed": False,
            "injection_flagged": False,
        })
        mock_resp = _mock_response(payload)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=mock_resp
        )

        with patch(
            "pinwheel.ai.interpreter.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            await interpret_proposal(
                "Make threes worth 4", RuleSet(), "fake-key"
            )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        system_arg = call_kwargs["system"]
        assert isinstance(system_arg, list)
        assert system_arg[0]["cache_control"] == {"type": "ephemeral"}

    async def test_report_call_claude_uses_cacheable_system(
        self,
    ) -> None:
        """_call_claude in report.py passes system as cacheable blocks."""
        from pinwheel.ai.report import _call_claude

        mock_resp = _mock_response("A report about the game.")
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=mock_resp
        )

        with patch(
            "pinwheel.ai.report.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            await _call_claude(
                system="You are a reporter.",
                user_message="Report on the game.",
                api_key="fake-key",
            )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        system_arg = call_kwargs["system"]
        assert isinstance(system_arg, list)
        assert system_arg[0]["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# Phase 2: Structured Output Tests
# ---------------------------------------------------------------------------


class TestPydanticToResponseFormat:
    """Verify pydantic_to_response_format() produces valid API spec."""

    def test_basic_model(self) -> None:
        """Simple Pydantic model produces the right structure."""

        class Simple(BaseModel):
            name: str
            value: int

        result = pydantic_to_response_format(Simple, "simple_model")
        assert result["type"] == "json_schema"
        json_schema = result["json_schema"]
        assert json_schema["name"] == "simple_model"
        schema = json_schema["schema"]
        assert "properties" in schema
        assert "name" in schema["properties"]
        assert "value" in schema["properties"]

    def test_classification_result_schema(self) -> None:
        """ClassificationResult generates a valid JSON schema."""
        result = pydantic_to_response_format(
            ClassificationResult, "classification_result"
        )
        schema = result["json_schema"]["schema"]
        props = schema["properties"]
        assert "classification" in props
        assert "confidence" in props
        assert "reason" in props

    def test_rule_interpretation_schema(self) -> None:
        """RuleInterpretation produces a schema with expected fields."""
        from pinwheel.models.governance import RuleInterpretation

        result = pydantic_to_response_format(
            RuleInterpretation, "rule_interpretation"
        )
        schema = result["json_schema"]["schema"]
        props = schema["properties"]
        assert "parameter" in props
        assert "confidence" in props
        assert "impact_analysis" in props

    def test_team_strategy_schema(self) -> None:
        """TeamStrategy model produces a schema."""
        from pinwheel.models.team import TeamStrategy

        result = pydantic_to_response_format(
            TeamStrategy, "team_strategy"
        )
        schema = result["json_schema"]["schema"]
        props = schema["properties"]
        assert "three_point_bias" in props
        assert "pace_modifier" in props

    def test_proposal_interpretation_schema(self) -> None:
        """ProposalInterpretation model produces a schema."""
        from pinwheel.models.governance import ProposalInterpretation

        result = pydantic_to_response_format(
            ProposalInterpretation, "proposal_interpretation"
        )
        schema = result["json_schema"]["schema"]
        assert "properties" in schema

    def test_query_plan_schema(self) -> None:
        """QueryPlan model produces a schema."""
        from pinwheel.ai.search import QueryPlan

        result = pydantic_to_response_format(QueryPlan, "query_plan")
        schema = result["json_schema"]["schema"]
        props = schema["properties"]
        assert "query_type" in props
        assert "team_name" in props


class TestClassificationResultPydantic:
    """Verify ClassificationResult after dataclass -> Pydantic migration."""

    def test_is_pydantic_model(self) -> None:
        """ClassificationResult is now a Pydantic BaseModel."""
        assert issubclass(ClassificationResult, BaseModel)

    def test_frozen(self) -> None:
        """ClassificationResult is still immutable."""
        result = ClassificationResult(
            classification="legitimate",
            confidence=0.9,
            reason="OK",
        )
        with pytest.raises(ValidationError):
            result.classification = "injection"  # type: ignore[misc]

    def test_has_model_json_schema(self) -> None:
        """ClassificationResult has model_json_schema()."""
        schema = ClassificationResult.model_json_schema()
        assert isinstance(schema, dict)
        assert "properties" in schema
        assert "classification" in schema["properties"]

    def test_construction(self) -> None:
        """ClassificationResult can be constructed with kwargs."""
        result = ClassificationResult(
            classification="suspicious",
            confidence=0.5,
            reason="Hmm",
        )
        assert result.classification == "suspicious"
        assert result.confidence == 0.5
        assert result.reason == "Hmm"

    def test_json_roundtrip(self) -> None:
        """ClassificationResult can be serialized to/from JSON."""
        original = ClassificationResult(
            classification="injection",
            confidence=0.98,
            reason="Clear injection",
        )
        json_str = original.model_dump_json()
        restored = ClassificationResult.model_validate_json(json_str)
        assert restored.classification == original.classification
        assert restored.confidence == original.confidence
        assert restored.reason == original.reason


class TestQueryPlanPydantic:
    """Verify QueryPlan after dataclass -> Pydantic migration."""

    def test_is_pydantic_model(self) -> None:
        """QueryPlan is now a Pydantic BaseModel."""
        from pinwheel.ai.search import QueryPlan

        assert issubclass(QueryPlan, BaseModel)

    def test_invalid_query_type_defaults_to_unknown(self) -> None:
        """Invalid query_type is corrected to 'unknown'."""
        from pinwheel.ai.search import QueryPlan

        plan = QueryPlan(query_type="not_a_real_type")
        assert plan.query_type == "unknown"

    def test_valid_query_type_preserved(self) -> None:
        """Valid query_type is preserved."""
        from pinwheel.ai.search import QueryPlan

        plan = QueryPlan(query_type="standings")
        assert plan.query_type == "standings"

    def test_default_values(self) -> None:
        """Default construction produces expected defaults."""
        from pinwheel.ai.search import QueryPlan

        plan = QueryPlan()
        assert plan.query_type == "unknown"
        assert plan.stat is None
        assert plan.limit == 5

    def test_has_model_json_schema(self) -> None:
        """QueryPlan has model_json_schema()."""
        from pinwheel.ai.search import QueryPlan

        schema = QueryPlan.model_json_schema()
        assert isinstance(schema, dict)
        assert "properties" in schema


class TestCallSitesResponseFormat:
    """Verify structured output call sites pass response_format."""

    async def test_classifier_uses_response_format(self) -> None:
        """classify_injection passes response_format."""
        from pinwheel.ai.classifier import classify_injection

        payload = (
            '{"classification":"legitimate",'
            '"confidence":0.9,"reason":"ok"}'
        )
        mock_resp = _mock_response(payload)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=mock_resp
        )

        with patch(
            "pinwheel.ai.classifier.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            await classify_injection("Test", "fake-key")

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "response_format" in call_kwargs
        rf = call_kwargs["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["name"] == "classification_result"

    async def test_interpreter_v1_uses_response_format(self) -> None:
        """interpret_proposal passes response_format."""
        from pinwheel.ai.interpreter import interpret_proposal
        from pinwheel.models.rules import RuleSet

        payload = json.dumps({
            "parameter": "three_point_value",
            "new_value": 4,
            "old_value": 3,
            "impact_analysis": "test",
            "confidence": 0.9,
            "clarification_needed": False,
            "injection_flagged": False,
        })
        mock_resp = _mock_response(payload)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=mock_resp
        )

        with patch(
            "pinwheel.ai.interpreter.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            await interpret_proposal(
                "Make threes worth 4", RuleSet(), "fake-key"
            )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "response_format" in call_kwargs
        name = call_kwargs["response_format"]["json_schema"]["name"]
        assert name == "rule_interpretation"

    async def test_interpreter_strategy_uses_response_format(
        self,
    ) -> None:
        """interpret_strategy passes response_format."""
        from pinwheel.ai.interpreter import interpret_strategy

        payload = json.dumps({
            "three_point_bias": 5.0,
            "mid_range_bias": 0.0,
            "at_rim_bias": 0.0,
            "defensive_intensity": 0.0,
            "pace_modifier": 1.0,
            "substitution_threshold_modifier": 0.0,
            "confidence": 0.8,
        })
        mock_resp = _mock_response(payload)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=mock_resp
        )

        with patch(
            "pinwheel.ai.interpreter.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            await interpret_strategy(
                "Shoot more threes", "fake-key"
            )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "response_format" in call_kwargs
        name = call_kwargs["response_format"]["json_schema"]["name"]
        assert name == "team_strategy"

    async def test_interpreter_v2_uses_response_format(self) -> None:
        """interpret_proposal_v2 passes response_format."""
        from pinwheel.ai.interpreter import interpret_proposal_v2
        from pinwheel.models.rules import RuleSet

        payload = json.dumps({
            "effects": [],
            "impact_analysis": "test",
            "confidence": 0.5,
            "clarification_needed": True,
            "injection_flagged": False,
            "original_text_echo": "test",
        })
        mock_resp = _mock_response(payload)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=mock_resp
        )

        with patch(
            "pinwheel.ai.interpreter.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            await interpret_proposal_v2(
                "Make the ball lava", RuleSet(), "fake-key"
            )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "response_format" in call_kwargs
        name = call_kwargs["response_format"]["json_schema"]["name"]
        assert name == "proposal_interpretation"

    async def test_search_parser_uses_response_format(self) -> None:
        """parse_query_ai passes response_format."""
        from pinwheel.ai.search import parse_query_ai

        payload = '{"query_type":"standings","limit":5}'
        mock_resp = _mock_response(payload)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=mock_resp
        )

        with patch(
            "pinwheel.ai.search.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            await parse_query_ai(
                "What are the standings?",
                "fake-key",
                team_names=["Team A"],
                hooper_names=["Player 1"],
            )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "response_format" in call_kwargs
        name = call_kwargs["response_format"]["json_schema"]["name"]
        assert name == "query_plan"


class TestFenceStrippingFallback:
    """Verify fence stripping still works as fallback."""

    async def test_classifier_handles_fenced_json(self) -> None:
        """Classifier still works with markdown fences."""
        from pinwheel.ai.classifier import classify_injection

        payload = json.dumps({
            "classification": "injection",
            "confidence": 0.9,
            "reason": "Obvious injection",
        })
        fenced = f"```json\n{payload}\n```"
        mock_resp = _mock_response(fenced)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=mock_resp
        )

        with patch(
            "pinwheel.ai.classifier.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            result = await classify_injection("Test", "fake-key")

        assert result.classification == "injection"
        assert result.confidence == pytest.approx(0.9)

    async def test_interpreter_handles_fenced_json(self) -> None:
        """Interpreter V1 still works with markdown fences."""
        from pinwheel.ai.interpreter import interpret_proposal
        from pinwheel.models.rules import RuleSet

        payload = json.dumps({
            "parameter": "three_point_value",
            "new_value": 4,
            "old_value": 3,
            "impact_analysis": "More threes",
            "confidence": 0.9,
            "clarification_needed": False,
            "injection_flagged": False,
        })
        fenced = f"```json\n{payload}\n```"
        mock_resp = _mock_response(fenced)
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=mock_resp
        )

        with patch(
            "pinwheel.ai.interpreter.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            result = await interpret_proposal(
                "Make threes worth 4", RuleSet(), "fake-key"
            )

        assert result.parameter == "three_point_value"
        assert result.new_value == 4
