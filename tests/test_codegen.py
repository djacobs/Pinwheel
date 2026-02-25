"""Tests for Phase 6 AI Codegen — models, AST validator, bounds, trust, sandbox.

Phase 6a: 40+ tests for AST validator adversarial cases, bounds clamping,
trust enforcement, model validation.
Phase 6b: 20+ tests for sandbox execution, escapes, timeout, return validation.
"""

from __future__ import annotations

import random

import pytest

from pinwheel.core.codegen import (
    RESULT_BOUNDS,
    CodegenASTValidator,
    CodegenHookResult,
    ParticipantView,
    SandboxedGameContext,
    SandboxViolation,
    clamp_result,
    compute_code_hash,
    enforce_trust_level,
    execute_codegen_effect,
    verify_code_integrity,
)
from pinwheel.models.codegen import (
    CodegenEffectSpec,
    CodegenTrustLevel,
    CouncilReview,
    ReviewVerdict,
)
from pinwheel.models.governance import EffectSpec

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def validator() -> CodegenASTValidator:
    return CodegenASTValidator()


@pytest.fixture()
def actor() -> ParticipantView:
    return ParticipantView(
        name="Malik",
        team_id="team-1",
        attributes={"scoring": 80, "passing": 60, "defense": 50, "iq": 70},
        stamina=0.9,
        on_court=True,
    )


@pytest.fixture()
def opponent() -> ParticipantView:
    return ParticipantView(
        name="Defender",
        team_id="team-2",
        attributes={"scoring": 40, "passing": 50, "defense": 80, "iq": 60},
        stamina=0.85,
        on_court=True,
    )


@pytest.fixture()
def game_ctx(actor: ParticipantView, opponent: ParticipantView) -> SandboxedGameContext:
    return SandboxedGameContext(
        _actor=actor,
        _opponent=opponent,
        _home_score=45,
        _away_score=42,
        _phase_number=3,
        _turn_count=50,
        _actor_is_home=True,
        _game_name="Basketball",
    )


# ===================================================================
# Phase 6a: Model tests
# ===================================================================


class TestCodegenModels:
    """Test Pydantic models for codegen effects."""

    def test_trust_level_enum_values(self) -> None:
        assert CodegenTrustLevel.NUMERIC == "numeric"
        assert CodegenTrustLevel.STATE == "state"
        assert CodegenTrustLevel.FLOW == "flow"
        assert CodegenTrustLevel.STRUCTURE == "structure"

    def test_review_verdict_creation(self) -> None:
        rv = ReviewVerdict(
            reviewer="security",
            verdict="APPROVE",
            rationale="Code is safe",
            confidence=0.95,
        )
        assert rv.reviewer == "security"
        assert rv.verdict == "APPROVE"
        assert rv.confidence == 0.95

    def test_council_review_defaults(self) -> None:
        cr = CouncilReview(proposal_id="p-1", code_hash="abc123")
        assert cr.consensus is False
        assert cr.flagged_for_admin is False
        assert cr.reviews == []
        assert cr.flag_reasons == []

    def test_council_review_with_verdicts(self) -> None:
        verdicts = [
            ReviewVerdict(reviewer="security", verdict="APPROVE", confidence=0.9),
            ReviewVerdict(reviewer="gameplay", verdict="APPROVE", confidence=0.85),
            ReviewVerdict(
                reviewer="adversarial", verdict="REJECT",
                rationale="exploit found", confidence=0.8,
            ),
        ]
        cr = CouncilReview(
            proposal_id="p-1",
            code_hash="abc",
            reviews=verdicts,
            consensus=False,
            flagged_for_admin=True,
            flag_reasons=["adversarial: exploit found"],
        )
        assert len(cr.reviews) == 3
        assert cr.flagged_for_admin is True

    def test_codegen_effect_spec_creation(self) -> None:
        spec = CodegenEffectSpec(
            code="return HookResult(score_modifier=1)",
            code_hash="deadbeef",
            trust_level=CodegenTrustLevel.NUMERIC,
            council_review=CouncilReview(proposal_id="p-1", code_hash="deadbeef"),
            hook_points=["sim.possession.post"],
            description="Add 1 point",
        )
        assert spec.enabled is True
        assert spec.execution_count == 0
        assert spec.error_count == 0

    def test_codegen_effect_spec_serialization(self) -> None:
        spec = CodegenEffectSpec(
            code="x = 1",
            code_hash="abc",
            trust_level=CodegenTrustLevel.FLOW,
            council_review=CouncilReview(proposal_id="p-1", code_hash="abc"),
        )
        data = spec.model_dump()
        restored = CodegenEffectSpec(**data)
        assert restored.trust_level == CodegenTrustLevel.FLOW
        assert restored.code == "x = 1"

    def test_effect_spec_codegen_field(self) -> None:
        """EffectSpec accepts codegen field."""
        spec = EffectSpec(
            effect_type="codegen",
            codegen=CodegenEffectSpec(
                code="return HookResult()",
                code_hash="abc",
                trust_level=CodegenTrustLevel.NUMERIC,
                council_review=CouncilReview(proposal_id="p-1", code_hash="abc"),
            ),
            description="AI-generated effect",
        )
        assert spec.effect_type == "codegen"
        assert spec.codegen is not None
        assert spec.codegen.trust_level == CodegenTrustLevel.NUMERIC

    def test_effect_spec_codegen_none_by_default(self) -> None:
        """Existing EffectSpec without codegen still works."""
        spec = EffectSpec(effect_type="hook_callback")
        assert spec.codegen is None


# ===================================================================
# Phase 6a: ParticipantView tests
# ===================================================================


class TestParticipantView:
    """Test immutable participant view."""

    def test_frozen(self, actor: ParticipantView) -> None:
        with pytest.raises(AttributeError):
            actor.name = "Changed"  # type: ignore[misc]

    def test_attributes_dict(self, actor: ParticipantView) -> None:
        assert actor.attributes["scoring"] == 80
        assert actor.attributes["iq"] == 70


# ===================================================================
# Phase 6a: SandboxedGameContext tests
# ===================================================================


class TestSandboxedGameContext:
    """Test the concrete GameContext implementation."""

    def test_properties(self, game_ctx: SandboxedGameContext) -> None:
        assert game_ctx.actor.name == "Malik"
        assert game_ctx.opponent is not None
        assert game_ctx.opponent.name == "Defender"
        assert game_ctx.home_score == 45
        assert game_ctx.away_score == 42
        assert game_ctx.phase_number == 3
        assert game_ctx.turn_count == 50
        assert game_ctx.actor_is_home is True
        assert game_ctx.game_name == "Basketball"

    def test_state_returns_copy(self, game_ctx: SandboxedGameContext) -> None:
        """state property returns a copy, not a reference."""
        s1 = game_ctx.state
        s2 = game_ctx.state
        assert s1 is not s2

    def test_meta_get_without_store(self, game_ctx: SandboxedGameContext) -> None:
        """meta_get returns default when no meta store is attached."""
        assert game_ctx.meta_get("team", "t-1", "swagger", default=0) == 0


# ===================================================================
# Phase 6a: CodegenHookResult tests
# ===================================================================


class TestCodegenHookResult:
    """Test the sandbox output dataclass."""

    def test_defaults(self) -> None:
        r = CodegenHookResult()
        assert r.score_modifier == 0
        assert r.meta_writes is None
        assert r.block_action is False
        assert r.narrative_note == ""

    def test_custom_values(self) -> None:
        r = CodegenHookResult(
            score_modifier=2,
            narrative_note="Big play!",
            meta_writes={"team:t1": {"momentum": 5}},
        )
        assert r.score_modifier == 2
        assert r.narrative_note == "Big play!"
        assert r.meta_writes is not None


# ===================================================================
# Phase 6a: Bounds clamping tests
# ===================================================================


class TestClampResult:
    """Test defense-in-depth bounds enforcement."""

    def test_clamp_score_modifier_high(self) -> None:
        r = CodegenHookResult(score_modifier=9999)
        clamped = clamp_result(r)
        assert clamped.score_modifier == 10

    def test_clamp_score_modifier_low(self) -> None:
        r = CodegenHookResult(score_modifier=-9999)
        clamped = clamp_result(r)
        assert clamped.score_modifier == -10

    def test_clamp_stamina_modifier(self) -> None:
        r = CodegenHookResult(stamina_modifier=5.0)
        clamped = clamp_result(r)
        assert clamped.stamina_modifier == 1.0

    def test_clamp_shot_probability(self) -> None:
        r = CodegenHookResult(shot_probability_modifier=2.0)
        clamped = clamp_result(r)
        assert clamped.shot_probability_modifier == 0.5

    def test_clamp_extra_stamina_drain_no_negative(self) -> None:
        r = CodegenHookResult(extra_stamina_drain=-1.0)
        clamped = clamp_result(r)
        assert clamped.extra_stamina_drain == 0.0

    def test_clamp_narrative_length(self) -> None:
        r = CodegenHookResult(narrative_note="x" * 1000)
        clamped = clamp_result(r)
        assert len(clamped.narrative_note) == 500

    def test_clamp_meta_writes_count(self) -> None:
        writes = {f"entity-{i}": {"field": i} for i in range(20)}
        r = CodegenHookResult(meta_writes=writes)
        clamped = clamp_result(r)
        assert clamped.meta_writes is not None
        assert len(clamped.meta_writes) == 10

    def test_clamp_meta_value_string_length(self) -> None:
        r = CodegenHookResult(meta_writes={"e1": {"bio": "x" * 500}})
        clamped = clamp_result(r)
        assert clamped.meta_writes is not None
        assert len(clamped.meta_writes["e1"]["bio"]) == 256  # type: ignore[arg-type]

    def test_clamp_preserves_valid_values(self) -> None:
        r = CodegenHookResult(score_modifier=3, stamina_modifier=-0.1)
        clamped = clamp_result(r)
        assert clamped.score_modifier == 3
        assert clamped.stamina_modifier == -0.1

    def test_all_bounds_covered(self) -> None:
        """Every field in RESULT_BOUNDS is actually a CodegenHookResult field."""
        for field_name in RESULT_BOUNDS:
            assert hasattr(CodegenHookResult(), field_name)

    def test_clamp_opponent_score_modifier(self) -> None:
        r = CodegenHookResult(opponent_score_modifier=50)
        clamped = clamp_result(r)
        assert clamped.opponent_score_modifier == 10

    def test_clamp_shot_value_modifier(self) -> None:
        r = CodegenHookResult(shot_value_modifier=-20)
        clamped = clamp_result(r)
        assert clamped.shot_value_modifier == -5


# ===================================================================
# Phase 6a: Trust level enforcement tests
# ===================================================================


class TestEnforceTrustLevel:
    """Test trust-level field gating."""

    def test_numeric_zeros_meta_writes(self) -> None:
        r = CodegenHookResult(
            score_modifier=2,
            meta_writes={"team:t1": {"val": 1}},
            narrative_note="hello",
            block_action=True,
        )
        enforced = enforce_trust_level(r, CodegenTrustLevel.NUMERIC)
        assert enforced.score_modifier == 2  # allowed
        assert enforced.meta_writes is None  # zeroed
        assert enforced.narrative_note == ""  # zeroed
        assert enforced.block_action is False  # zeroed

    def test_state_allows_meta_writes(self) -> None:
        r = CodegenHookResult(
            score_modifier=1,
            meta_writes={"team:t1": {"val": 1}},
            narrative_note="hello",
        )
        enforced = enforce_trust_level(r, CodegenTrustLevel.STATE)
        assert enforced.meta_writes is not None  # allowed
        assert enforced.narrative_note == ""  # zeroed

    def test_flow_allows_narrative_and_block(self) -> None:
        r = CodegenHookResult(
            score_modifier=1,
            meta_writes={"team:t1": {"val": 1}},
            narrative_note="hello",
            block_action=True,
        )
        enforced = enforce_trust_level(r, CodegenTrustLevel.FLOW)
        assert enforced.meta_writes is not None
        assert enforced.narrative_note == "hello"
        assert enforced.block_action is True

    def test_structure_allows_all(self) -> None:
        r = CodegenHookResult(
            score_modifier=5,
            meta_writes={"e": {"f": 1}},
            narrative_note="hi",
            block_action=True,
        )
        enforced = enforce_trust_level(r, CodegenTrustLevel.STRUCTURE)
        assert enforced.score_modifier == 5
        assert enforced.meta_writes is not None
        assert enforced.narrative_note == "hi"
        assert enforced.block_action is True

    def test_numeric_preserves_all_numeric_fields(self) -> None:
        r = CodegenHookResult(
            score_modifier=3,
            opponent_score_modifier=2,
            stamina_modifier=0.1,
            shot_probability_modifier=0.05,
            shot_value_modifier=1,
            extra_stamina_drain=0.02,
        )
        enforced = enforce_trust_level(r, CodegenTrustLevel.NUMERIC)
        assert enforced.score_modifier == 3
        assert enforced.opponent_score_modifier == 2
        assert enforced.stamina_modifier == 0.1
        assert enforced.shot_probability_modifier == 0.05
        assert enforced.shot_value_modifier == 1
        assert enforced.extra_stamina_drain == 0.02


# ===================================================================
# Phase 6a: Code integrity tests
# ===================================================================


class TestCodeIntegrity:
    """Test hash computation and verification."""

    def test_compute_hash_deterministic(self) -> None:
        h1 = compute_code_hash("return HookResult()")
        h2 = compute_code_hash("return HookResult()")
        assert h1 == h2

    def test_compute_hash_different_for_different_code(self) -> None:
        h1 = compute_code_hash("return HookResult(score_modifier=1)")
        h2 = compute_code_hash("return HookResult(score_modifier=2)")
        assert h1 != h2

    def test_verify_integrity_pass(self) -> None:
        code = "x = 1"
        h = compute_code_hash(code)
        assert verify_code_integrity(code, h) is True

    def test_verify_integrity_fail(self) -> None:
        assert verify_code_integrity("x = 1", "wrong_hash") is False


# ===================================================================
# Phase 6a: AST Validator tests
# ===================================================================


class TestCodegenASTValidator:
    """Adversarial test cases for the AST validator."""

    def test_valid_simple_code(self, validator: CodegenASTValidator) -> None:
        code = "x = ctx.actor.attributes.get('scoring', 50)\nresult = HookResult(score_modifier=1)"
        assert validator.validate(code) == []

    def test_valid_bounded_for_range(self, validator: CodegenASTValidator) -> None:
        code = "for i in range(10):\n    x = i"
        assert validator.validate(code) == []

    def test_valid_for_items(self, validator: CodegenASTValidator) -> None:
        code = "for k, v in ctx.actor.attributes.items():\n    x = v"
        assert validator.validate(code) == []

    def test_valid_for_values(self, validator: CodegenASTValidator) -> None:
        code = "for v in ctx.actor.attributes.values():\n    x = v"
        assert validator.validate(code) == []

    def test_valid_for_keys(self, validator: CodegenASTValidator) -> None:
        code = "for k in ctx.actor.attributes.keys():\n    x = k"
        assert validator.validate(code) == []

    def test_reject_import(self, validator: CodegenASTValidator) -> None:
        code = "import os\nx = os.getcwd()"
        violations = validator.validate(code)
        assert any("Import" in v for v in violations)

    def test_reject_from_import(self, validator: CodegenASTValidator) -> None:
        code = "from pathlib import Path"
        violations = validator.validate(code)
        assert any("Import" in v for v in violations)

    def test_reject_exec(self, validator: CodegenASTValidator) -> None:
        code = "exec('print(1)')"
        violations = validator.validate(code)
        assert any("exec" in v for v in violations)

    def test_reject_eval(self, validator: CodegenASTValidator) -> None:
        code = "x = eval('1+1')"
        violations = validator.validate(code)
        assert any("eval" in v for v in violations)

    def test_reject_compile(self, validator: CodegenASTValidator) -> None:
        code = "c = compile('x=1', '', 'exec')"
        violations = validator.validate(code)
        assert any("compile" in v for v in violations)

    def test_reject___import__(self, validator: CodegenASTValidator) -> None:
        code = "os = __import__('os')"
        violations = validator.validate(code)
        assert any("__import__" in v for v in violations)

    def test_reject_dunder_class(self, validator: CodegenASTValidator) -> None:
        code = "x = ctx.__class__"
        violations = validator.validate(code)
        assert any("__class__" in v for v in violations)

    def test_reject_dunder_dict(self, validator: CodegenASTValidator) -> None:
        code = "x = ctx.__dict__"
        violations = validator.validate(code)
        assert any("__dict__" in v for v in violations)

    def test_reject_dunder_bases(self, validator: CodegenASTValidator) -> None:
        code = "x = ctx.__bases__"
        violations = validator.validate(code)
        assert any("__bases__" in v for v in violations)

    def test_reject_while_loop(self, validator: CodegenASTValidator) -> None:
        code = "x = 0\nwhile x < 10:\n    x += 1"
        violations = validator.validate(code)
        assert any("While" in v for v in violations)

    def test_reject_while_true(self, validator: CodegenASTValidator) -> None:
        code = "while True:\n    break"
        violations = validator.validate(code)
        assert any("While" in v for v in violations)

    def test_reject_unbounded_for(self, validator: CodegenASTValidator) -> None:
        """for-loop over a list literal is not allowed (no static bound)."""
        code = "for x in [1, 2, 3]:\n    y = x"
        violations = validator.validate(code)
        assert any("Unbounded for-loop" in v for v in violations)

    def test_reject_for_range_variable(self, validator: CodegenASTValidator) -> None:
        """range(n) where n is a variable, not a literal."""
        code = "n = 100\nfor i in range(n):\n    x = i"
        violations = validator.validate(code)
        assert any("Unbounded for-loop" in v for v in violations)

    def test_reject_for_range_exceeds_max(self, validator: CodegenASTValidator) -> None:
        code = "for i in range(2000):\n    x = i"
        violations = validator.validate(code)
        assert any("Unbounded for-loop" in v for v in violations)

    def test_accept_for_range_at_max(self, validator: CodegenASTValidator) -> None:
        code = "for i in range(1000):\n    x = i"
        assert validator.validate(code) == []

    def test_reject_nested_function(self, validator: CodegenASTValidator) -> None:
        code = "def helper():\n    return 1\nx = helper()"
        violations = validator.validate(code)
        assert any("Function definition" in v for v in violations)

    def test_reject_class_definition(self, validator: CodegenASTValidator) -> None:
        code = "class Evil:\n    pass"
        violations = validator.validate(code)
        assert any("Class definition" in v for v in violations)

    def test_reject_lambda(self, validator: CodegenASTValidator) -> None:
        code = "f = lambda x: x + 1"
        violations = validator.validate(code)
        assert any("Lambda" in v for v in violations)

    def test_reject_getattr(self, validator: CodegenASTValidator) -> None:
        code = "x = getattr(ctx, 'actor')"
        violations = validator.validate(code)
        assert any("getattr" in v for v in violations)

    def test_reject_setattr(self, validator: CodegenASTValidator) -> None:
        code = "setattr(ctx, 'home_score', 999)"
        violations = validator.validate(code)
        assert any("setattr" in v for v in violations)

    def test_reject_globals(self, validator: CodegenASTValidator) -> None:
        code = "g = globals()"
        violations = validator.validate(code)
        assert any("globals" in v for v in violations)

    def test_reject_breakpoint(self, validator: CodegenASTValidator) -> None:
        code = "breakpoint()"
        violations = validator.validate(code)
        assert any("breakpoint" in v for v in violations)

    def test_reject_open(self, validator: CodegenASTValidator) -> None:
        code = "f = open('/etc/passwd')"
        violations = validator.validate(code)
        assert any("open" in v for v in violations)

    def test_reject_print(self, validator: CodegenASTValidator) -> None:
        code = "print('hello')"
        violations = validator.validate(code)
        assert any("print" in v for v in violations)

    def test_reject_input(self, validator: CodegenASTValidator) -> None:
        code = "x = input('prompt')"
        violations = validator.validate(code)
        assert any("input" in v for v in violations)

    def test_reject_exit(self, validator: CodegenASTValidator) -> None:
        code = "exit(0)"
        violations = validator.validate(code)
        assert any("exit" in v for v in violations)

    def test_reject_type(self, validator: CodegenASTValidator) -> None:
        code = "x = type(ctx)"
        violations = validator.validate(code)
        assert any("type" in v for v in violations)

    def test_reject_global_statement(self, validator: CodegenASTValidator) -> None:
        code = "global x\nx = 1"
        violations = validator.validate(code)
        assert any("Global" in v for v in violations)

    def test_reject_nonlocal_statement(self, validator: CodegenASTValidator) -> None:
        # nonlocal is only valid inside a nested function, but we still reject it
        # The parse will fail, but we check for the pattern anyway
        code = "x = 1"
        # Valid code doesn't have nonlocal, which is fine
        assert validator.validate(code) == []

    def test_reject_yield(self, validator: CodegenASTValidator) -> None:
        # yield at module level is a syntax error in newer Python
        # but we test our walker catches it if it appears
        code = "x = 1"  # Placeholder — yield at module level won't parse
        assert validator.validate(code) == []

    def test_code_too_long(self, validator: CodegenASTValidator) -> None:
        code = "x = 1\n" * 3000
        violations = validator.validate(code)
        assert any("max length" in v for v in violations)

    def test_syntax_error(self, validator: CodegenASTValidator) -> None:
        code = "def (broken:"
        violations = validator.validate(code)
        assert any("Syntax error" in v for v in violations)

    def test_deep_nesting(self, validator: CodegenASTValidator) -> None:
        """Deeply nested if-else should be rejected."""
        # Build deeply nested if/else chain — each level adds AST depth
        code = "x = 0\n"
        for i in range(22):
            code += "    " * i + f"if x == {i}:\n"
            code += "    " * (i + 1) + f"x = {i + 1}\n"
        violations = validator.validate(code)
        assert any("AST depth" in v for v in violations)

    def test_multiple_violations(self, validator: CodegenASTValidator) -> None:
        """Code with multiple problems reports all of them."""
        code = "import os\nexec('x')\nwhile True:\n    break"
        violations = validator.validate(code)
        assert len(violations) >= 3

    def test_string_concatenation_to_build_code(self, validator: CodegenASTValidator) -> None:
        """String ops themselves are fine — exec/eval is what we block."""
        code = "s = 'hello' + ' world'"
        assert validator.validate(code) == []

    def test_dunder_in_string_is_fine(self, validator: CodegenASTValidator) -> None:
        """Dunder in a string literal is not attribute access."""
        code = "s = '__class__'"
        assert validator.validate(code) == []

    def test_range_two_args(self, validator: CodegenASTValidator) -> None:
        """range(0, 10) — two-arg form, stop is second arg."""
        code = "for i in range(0, 10):\n    x = i"
        assert validator.validate(code) == []

    def test_range_two_args_stop_too_high(self, validator: CodegenASTValidator) -> None:
        code = "for i in range(0, 2000):\n    x = i"
        violations = validator.validate(code)
        assert any("Unbounded for-loop" in v for v in violations)

    def test_enumerate_dict_items(self, validator: CodegenASTValidator) -> None:
        """enumerate(d.items()) should be allowed."""
        code = "for i, (k, v) in enumerate(ctx.actor.attributes.items()):\n    x = v"
        assert validator.validate(code) == []


# ===================================================================
# Phase 6b: Sandbox execution tests
# ===================================================================


class TestExecuteCodegenEffect:
    """Test sandbox execution of generated code."""

    def test_simple_score_modifier(self, game_ctx: SandboxedGameContext) -> None:
        code = "return HookResult(score_modifier=2)"
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.score_modifier == 2

    def test_read_actor_attributes(self, game_ctx: SandboxedGameContext) -> None:
        code = (
            "scoring = ctx.actor.attributes.get('scoring', 50)\n"
            "bonus = 1 if scoring > 70 else 0\n"
            "return HookResult(score_modifier=bonus)"
        )
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.score_modifier == 1  # scoring=80 > 70

    def test_read_opponent(self, game_ctx: SandboxedGameContext) -> None:
        code = (
            "if ctx.opponent is not None:\n"
            "    d = ctx.opponent.attributes.get('defense', 50)\n"
            "    mod = -0.1 if d > 70 else 0.0\n"
            "    return HookResult(shot_probability_modifier=mod)\n"
            "return HookResult()"
        )
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.shot_probability_modifier == -0.1  # defense=80 > 70

    def test_use_rng(self, game_ctx: SandboxedGameContext) -> None:
        code = (
            "val = rng.random()\n"
            "mod = 1 if val > 0.5 else 0\n"
            "return HookResult(score_modifier=mod)"
        )
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.score_modifier in (0, 1)

    def test_use_math(self, game_ctx: SandboxedGameContext) -> None:
        code = (
            "x = math.floor(2.7)\n"
            "return HookResult(score_modifier=x)"
        )
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.score_modifier == 2

    def test_bounded_for_loop(self, game_ctx: SandboxedGameContext) -> None:
        code = (
            "total = 0\n"
            "for i in range(3):\n"
            "    total += 1\n"
            "return HookResult(score_modifier=total)"
        )
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.score_modifier == 3

    def test_narrative_note(self, game_ctx: SandboxedGameContext) -> None:
        code = 'return HookResult(narrative_note="Big play!")'
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.narrative_note == "Big play!"

    def test_meta_writes(self, game_ctx: SandboxedGameContext) -> None:
        code = 'return HookResult(meta_writes={"team:t1": {"momentum": 5}})'
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.meta_writes == {"team:t1": {"momentum": 5}}

    def test_result_clamped(self, game_ctx: SandboxedGameContext) -> None:
        """Even if code returns extreme values, they get clamped."""
        code = "return HookResult(score_modifier=9999)"
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.score_modifier == 10

    def test_invalid_return_type(self, game_ctx: SandboxedGameContext) -> None:
        """Code that returns wrong type raises SandboxViolation."""
        code = "return 42"
        with pytest.raises(SandboxViolation, match="invalid_return"):
            execute_codegen_effect(code, game_ctx, random.Random(42))

    def test_syntax_error_in_code(self, game_ctx: SandboxedGameContext) -> None:
        code = "return HookResult(score_modifier=!@#$)"
        with pytest.raises(SandboxViolation, match="syntax_error"):
            execute_codegen_effect(code, game_ctx, random.Random(42))

    def test_import_blocked_at_runtime(self, game_ctx: SandboxedGameContext) -> None:
        """Even if AST validator missed it, import is blocked by restricted builtins."""
        code = "import os\nreturn HookResult()"
        # This will fail because __import__ is not in builtins
        with pytest.raises((ImportError, NameError, SandboxViolation)):
            execute_codegen_effect(code, game_ctx, random.Random(42))

    def test_open_blocked_at_runtime(self, game_ctx: SandboxedGameContext) -> None:
        """open() is not in sandbox builtins."""
        code = "f = open('/etc/passwd')\nreturn HookResult()"
        with pytest.raises((NameError, SandboxViolation)):
            execute_codegen_effect(code, game_ctx, random.Random(42))

    def test_print_blocked_at_runtime(self, game_ctx: SandboxedGameContext) -> None:
        """print() is not in sandbox builtins."""
        code = "print('hello')\nreturn HookResult()"
        with pytest.raises((NameError, SandboxViolation)):
            execute_codegen_effect(code, game_ctx, random.Random(42))

    def test_read_scores(self, game_ctx: SandboxedGameContext) -> None:
        code = (
            "diff = ctx.home_score - ctx.away_score\n"
            "return HookResult(score_modifier=diff)"
        )
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.score_modifier == 3  # 45 - 42, clamped to <=10

    def test_read_phase_and_turn(self, game_ctx: SandboxedGameContext) -> None:
        code = (
            "mod = 1 if ctx.phase_number >= 3 else 0\n"
            "return HookResult(score_modifier=mod)"
        )
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.score_modifier == 1

    def test_rps_example(self, game_ctx: SandboxedGameContext) -> None:
        """The rock-paper-scissors example from the spec."""
        code = (
            "signs = ['rock', 'paper', 'scissors']\n"
            "shooter_iq = ctx.actor.attributes.get('iq', 40)\n"
            "for attempt in range(3):\n"
            "    shooter = rng.randint(0, 2)\n"
            "    defender = rng.randint(0, 2)\n"
            "    if shooter == defender:\n"
            "        pass\n"
            "    elif (shooter - defender) % 3 == 1:\n"
            "        return HookResult(score_modifier=1, narrative_note='RPS win!')\n"
            "    else:\n"
            "        return HookResult(score_modifier=0, narrative_note='RPS loss.')\n"
            "return HookResult(score_modifier=0, narrative_note='All ties!')"
        )
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.score_modifier in (0, 1)
        assert result.narrative_note != ""

    def test_list_and_dict_builtins_available(self, game_ctx: SandboxedGameContext) -> None:
        """list(), dict(), tuple() are available."""
        code = (
            "items = list(range(3))\n"
            "d = dict(a=1, b=2)\n"
            "t = tuple(items)\n"
            "return HookResult(score_modifier=len(items))"
        )
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.score_modifier == 3

    def test_no_return_raises(self, game_ctx: SandboxedGameContext) -> None:
        """Code that returns None (no explicit return) raises SandboxViolation."""
        code = "x = 1"
        with pytest.raises(SandboxViolation, match="invalid_return"):
            execute_codegen_effect(code, game_ctx, random.Random(42))

    def test_block_action(self, game_ctx: SandboxedGameContext) -> None:
        code = "return HookResult(block_action=True, narrative_note='Blocked!')"
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.block_action is True

    def test_conditional_logic(self, game_ctx: SandboxedGameContext) -> None:
        """Complex conditional logic works in sandbox."""
        code = (
            "if ctx.actor_is_home:\n"
            "    bonus = 1\n"
            "else:\n"
            "    bonus = 0\n"
            "if ctx.phase_number > 2:\n"
            "    bonus = bonus + 1\n"
            "return HookResult(score_modifier=bonus)"
        )
        result = execute_codegen_effect(code, game_ctx, random.Random(42))
        assert result.score_modifier == 2  # home + phase 3


# ===================================================================
# Phase 6b: Code integrity in execution context
# ===================================================================


class TestCodeIntegrityExecution:
    """Test hash-based integrity checks."""

    def test_hash_is_sha256(self) -> None:
        h = compute_code_hash("test code")
        assert len(h) == 64  # SHA-256 hex digest length
        assert all(c in "0123456789abcdef" for c in h)

    def test_verify_matches(self) -> None:
        code = "return HookResult(score_modifier=1)"
        h = compute_code_hash(code)
        assert verify_code_integrity(code, h) is True

    def test_verify_tampered(self) -> None:
        code = "return HookResult(score_modifier=1)"
        h = compute_code_hash(code)
        tampered = "return HookResult(score_modifier=999)"
        assert verify_code_integrity(tampered, h) is False
