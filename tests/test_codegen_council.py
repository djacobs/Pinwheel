"""Tests for Phase 6c — Council Pipeline (AI calls mocked).

Tests the generate → validate → review → verdict pipeline with mocked
Anthropic client. No real API calls.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from pinwheel.ai.codegen_council import (
    generate_codegen_effect,
    generate_codegen_effect_mock,
    review_adversarial,
    review_gameplay,
    review_security,
    run_council_review,
)
from pinwheel.models.codegen import (
    CodegenTrustLevel,
    ReviewVerdict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_message_response(text: str) -> MagicMock:
    """Build a mock Anthropic Messages API response."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


def _security_approve_json() -> str:
    return json.dumps({
        "verdict": "APPROVE",
        "concerns": [],
        "max_loop_iterations": 3,
        "uses_rng": True,
        "mutates_outside_result": False,
        "confidence": 0.95,
    })


def _security_reject_json() -> str:
    return json.dumps({
        "verdict": "REJECT",
        "concerns": ["Uses eval() to execute dynamic code"],
        "max_loop_iterations": 0,
        "uses_rng": False,
        "mutates_outside_result": True,
        "confidence": 0.9,
    })


def _gameplay_approve_json() -> str:
    return json.dumps({
        "verdict": "APPROVE",
        "faithfulness": 0.9,
        "balance_concern": "none",
        "interaction_risks": [],
        "fun_factor": "exciting",
        "confidence": 0.88,
    })


def _gameplay_reject_json() -> str:
    return json.dumps({
        "verdict": "REJECT",
        "faithfulness": 0.3,
        "balance_concern": "major",
        "interaction_risks": ["Always returns score_modifier=10"],
        "fun_factor": "boring",
        "confidence": 0.85,
    })


def _adversarial_approve_json() -> str:
    return json.dumps({
        "verdict": "APPROVE",
        "exploits_found": [],
        "prompt_injection_detected": False,
        "proposal_text_in_code": False,
        "confidence": 0.92,
    })


def _adversarial_reject_json() -> str:
    return json.dumps({
        "verdict": "REJECT",
        "exploits_found": [{
            "name": "timing_attack",
            "severity": "high",
            "description": "Advantages home team based on turn count",
            "trigger_condition": "When turn_count is even",
        }],
        "prompt_injection_detected": False,
        "proposal_text_in_code": True,
        "confidence": 0.88,
    })


def _generator_response_json(
    code: str = "return HookResult(score_modifier=1)",
    trust_level: str = "numeric",
) -> str:
    return json.dumps({
        "code": code,
        "trust_level": trust_level,
        "hook_points": ["sim.possession.post"],
        "description": "Test effect",
        "example_output": "HookResult(score_modifier=1)",
    })


# ===================================================================
# Review function tests
# ===================================================================


class TestReviewSecurity:
    """Test security reviewer."""

    @pytest.mark.anyio()
    async def test_approve(self) -> None:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_message_response(_security_approve_json())
        )
        with patch(
            "pinwheel.ai.codegen_council._get_council_client",
            return_value=mock_client,
        ):
            verdict = await review_security("return HookResult()", "fake-key")
        assert verdict.reviewer == "security"
        assert verdict.verdict == "APPROVE"
        assert verdict.confidence == 0.95

    @pytest.mark.anyio()
    async def test_reject(self) -> None:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_message_response(_security_reject_json())
        )
        with patch(
            "pinwheel.ai.codegen_council._get_council_client",
            return_value=mock_client,
        ):
            verdict = await review_security("eval('bad')", "fake-key")
        assert verdict.verdict == "REJECT"
        assert "eval" in verdict.rationale


class TestReviewGameplay:
    """Test gameplay reviewer."""

    @pytest.mark.anyio()
    async def test_approve(self) -> None:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_message_response(_gameplay_approve_json())
        )
        with patch(
            "pinwheel.ai.codegen_council._get_council_client",
            return_value=mock_client,
        ):
            verdict = await review_gameplay(
                "return HookResult(score_modifier=1)",
                "Add 1 point on each possession",
                "fake-key",
            )
        assert verdict.verdict == "APPROVE"
        assert verdict.confidence == 0.88

    @pytest.mark.anyio()
    async def test_reject_balance(self) -> None:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_message_response(_gameplay_reject_json())
        )
        with patch(
            "pinwheel.ai.codegen_council._get_council_client",
            return_value=mock_client,
        ):
            verdict = await review_gameplay(
                "return HookResult(score_modifier=10)",
                "Always score 10 points",
                "fake-key",
            )
        assert verdict.verdict == "REJECT"
        assert "Balance" in verdict.rationale


class TestReviewAdversarial:
    """Test adversarial reviewer."""

    @pytest.mark.anyio()
    async def test_approve(self) -> None:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_message_response(_adversarial_approve_json())
        )
        security_verdict = ReviewVerdict(
            reviewer="security", verdict="APPROVE", confidence=0.95,
        )
        with patch(
            "pinwheel.ai.codegen_council._get_council_client",
            return_value=mock_client,
        ):
            verdict = await review_adversarial(
                "return HookResult()",
                "Simple effect",
                security_verdict,
                "fake-key",
            )
        assert verdict.verdict == "APPROVE"
        assert verdict.confidence == 0.92

    @pytest.mark.anyio()
    async def test_reject_exploit(self) -> None:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_message_response(_adversarial_reject_json())
        )
        security_verdict = ReviewVerdict(
            reviewer="security", verdict="APPROVE", confidence=0.95,
        )
        with patch(
            "pinwheel.ai.codegen_council._get_council_client",
            return_value=mock_client,
        ):
            verdict = await review_adversarial(
                "return HookResult(score_modifier=ctx.turn_count % 2)",
                "Score based on turn count",
                security_verdict,
                "fake-key",
            )
        assert verdict.verdict == "REJECT"
        assert "timing_attack" in verdict.rationale


# ===================================================================
# Generator tests
# ===================================================================


class TestGenerateCodegenEffect:
    """Test code generator."""

    @pytest.mark.anyio()
    async def test_generate_success(self) -> None:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_message_response(_generator_response_json())
        )
        with patch(
            "pinwheel.ai.codegen_council._get_council_client",
            return_value=mock_client,
        ):
            result = await generate_codegen_effect("Add 1 point", "fake-key")
        assert result["code"] == "return HookResult(score_modifier=1)"
        assert result["trust_level"] == "numeric"

    @pytest.mark.anyio()
    async def test_generate_with_markdown_fences(self) -> None:
        """Generator response wrapped in markdown fences is handled."""
        fenced = f"```json\n{_generator_response_json()}\n```"
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_message_response(fenced)
        )
        with patch(
            "pinwheel.ai.codegen_council._get_council_client",
            return_value=mock_client,
        ):
            result = await generate_codegen_effect("Add 1 point", "fake-key")
        assert "code" in result


# ===================================================================
# Council orchestrator tests
# ===================================================================


class TestRunCouncilReview:
    """Test the full council pipeline."""

    @pytest.mark.anyio()
    async def test_consensus_approve(self) -> None:
        """All three reviewers approve → consensus."""
        call_count = 0

        async def _mock_create(**kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Generator
                return _mock_message_response(_generator_response_json())
            if call_count == 2:
                # Security
                return _mock_message_response(_security_approve_json())
            if call_count == 3:
                # Gameplay
                return _mock_message_response(_gameplay_approve_json())
            # Adversarial
            return _mock_message_response(_adversarial_approve_json())

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=_mock_create)
        with patch(
            "pinwheel.ai.codegen_council._get_council_client",
            return_value=mock_client,
        ):
            spec, review = await run_council_review(
                "p-1", "Add 1 point per possession", "fake-key",
            )

        assert review.consensus is True
        assert review.flagged_for_admin is False
        assert spec is not None
        assert spec.trust_level == CodegenTrustLevel.NUMERIC
        assert len(review.reviews) == 3

    @pytest.mark.anyio()
    async def test_security_rejects(self) -> None:
        """Security reviewer rejects → no consensus, flagged."""
        call_count = 0

        async def _mock_create(**kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_message_response(_generator_response_json())
            if call_count == 2:
                return _mock_message_response(_security_reject_json())
            if call_count == 3:
                return _mock_message_response(_gameplay_approve_json())
            return _mock_message_response(_adversarial_approve_json())

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=_mock_create)
        with patch(
            "pinwheel.ai.codegen_council._get_council_client",
            return_value=mock_client,
        ):
            spec, review = await run_council_review(
                "p-2", "eval('bad')", "fake-key",
            )

        assert review.consensus is False
        assert review.flagged_for_admin is True
        assert spec is None
        assert any("security" in r for r in review.flag_reasons)

    @pytest.mark.anyio()
    async def test_ast_validation_fails(self) -> None:
        """Generated code that fails AST validation → no consensus."""
        bad_code = "import os\nos.system('rm -rf /')"
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_mock_message_response(
                _generator_response_json(code=bad_code)
            )
        )
        with patch(
            "pinwheel.ai.codegen_council._get_council_client",
            return_value=mock_client,
        ):
            spec, review = await run_council_review(
                "p-3", "Delete everything", "fake-key",
            )

        assert review.consensus is False
        assert spec is None
        assert any("AST" in r for r in review.flag_reasons)

    @pytest.mark.anyio()
    async def test_generation_failure(self) -> None:
        """Generator throws error → failure review."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.APIConnectionError(request=MagicMock())
        )
        with patch(
            "pinwheel.ai.codegen_council._get_council_client",
            return_value=mock_client,
        ):
            spec, review = await run_council_review(
                "p-4", "Something", "fake-key",
            )

        assert review.consensus is False
        assert review.flagged_for_admin is True
        assert spec is None
        assert any("Generation failed" in r for r in review.flag_reasons)

    @pytest.mark.anyio()
    async def test_adversarial_rejects(self) -> None:
        """Adversarial reviewer rejects → no consensus."""
        call_count = 0

        async def _mock_create(**kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_message_response(_generator_response_json())
            if call_count == 2:
                return _mock_message_response(_security_approve_json())
            if call_count == 3:
                return _mock_message_response(_gameplay_approve_json())
            return _mock_message_response(_adversarial_reject_json())

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=_mock_create)
        with patch(
            "pinwheel.ai.codegen_council._get_council_client",
            return_value=mock_client,
        ):
            spec, review = await run_council_review(
                "p-5", "Timing attack proposal", "fake-key",
            )

        assert review.consensus is False
        assert spec is None

    @pytest.mark.anyio()
    async def test_flow_trust_level(self) -> None:
        """Generated code with narrative_note gets FLOW trust level."""
        code = 'return HookResult(score_modifier=1, narrative_note="Nice!")'
        call_count = 0

        async def _mock_create(**kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_message_response(
                    _generator_response_json(code=code, trust_level="flow")
                )
            if call_count == 2:
                return _mock_message_response(_security_approve_json())
            if call_count == 3:
                return _mock_message_response(_gameplay_approve_json())
            return _mock_message_response(_adversarial_approve_json())

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=_mock_create)
        with patch(
            "pinwheel.ai.codegen_council._get_council_client",
            return_value=mock_client,
        ):
            spec, review = await run_council_review(
                "p-6", "Score and narrate", "fake-key",
            )

        assert review.consensus is True
        assert spec is not None
        assert spec.trust_level == CodegenTrustLevel.FLOW


# ===================================================================
# Mock generator tests
# ===================================================================


class TestMockGenerator:
    """Test the mock codegen generator for tests/API-absent fallback."""

    def test_mock_returns_valid_spec(self) -> None:
        spec = generate_codegen_effect_mock("Test proposal")
        assert spec.code != ""
        assert spec.code_hash != ""
        assert spec.trust_level == CodegenTrustLevel.FLOW
        assert spec.council_review.consensus is True
        assert len(spec.council_review.reviews) == 3

    def test_mock_code_is_executable(self) -> None:
        """The mock code should actually work in the sandbox."""
        import random

        from pinwheel.core.codegen import (
            ParticipantView,
            SandboxedGameContext,
            execute_codegen_effect,
        )

        spec = generate_codegen_effect_mock("Test")
        ctx = SandboxedGameContext(
            _actor=ParticipantView(
                name="Test", team_id="t1",
                attributes={"scoring": 50}, stamina=1.0, on_court=True,
            ),
        )
        result = execute_codegen_effect(spec.code, ctx, random.Random(42))
        assert result.score_modifier == 1

    def test_mock_description_includes_proposal(self) -> None:
        spec = generate_codegen_effect_mock("Replace free throws with RPS")
        assert "Replace free throws" in spec.description
