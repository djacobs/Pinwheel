"""Tests for Phase 6d — Governance Integration.

Tests: fire_codegen, hash verification, auto-disable, trust enforcement,
effect_spec conversion, HookContext→GameContext mapping, tier detection.
"""

from __future__ import annotations

import random

import pytest

from pinwheel.core.codegen import (
    CodegenHookResult,
    compute_code_hash,
)
from pinwheel.core.effects import effect_spec_to_registered
from pinwheel.core.governance import detect_tier_v2
from pinwheel.core.hooks import (
    HookContext,
    RegisteredEffect,
    _build_game_context,
    _codegen_result_to_hook_result,
    fire_effects,
)
from pinwheel.core.state import GameState, HooperState
from pinwheel.models.codegen import (
    CodegenEffectSpec,
    CodegenTrustLevel,
    CouncilReview,
)
from pinwheel.models.governance import EffectSpec, ProposalInterpretation
from pinwheel.models.rules import RuleSet
from pinwheel.models.team import Hooper, PlayerAttributes, suppress_budget_check

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def simple_codegen_spec() -> CodegenEffectSpec:
    code = "return HookResult(score_modifier=1, narrative_note='Codegen fired!')"
    return CodegenEffectSpec(
        code=code,
        code_hash=compute_code_hash(code),
        trust_level=CodegenTrustLevel.FLOW,
        council_review=CouncilReview(
            proposal_id="p-1",
            code_hash=compute_code_hash(code),
            consensus=True,
        ),
        hook_points=["sim.possession.post"],
        description="Add 1 point with narrative",
    )


@pytest.fixture()
def codegen_effect_spec(simple_codegen_spec: CodegenEffectSpec) -> EffectSpec:
    return EffectSpec(
        effect_type="codegen",
        codegen=simple_codegen_spec,
        description="Test codegen effect",
    )


@pytest.fixture()
def game_state() -> GameState:
    with suppress_budget_check():
        home_hooper = Hooper(
            id="h1", name="Malik", team_id="t1", archetype="scorer",
            attributes=PlayerAttributes(
                scoring=80, passing=60, defense=50, speed=70,
                stamina=60, iq=70, ego=40, chaotic_alignment=30, fate=50,
            ),
        )
        away_hooper = Hooper(
            id="h2", name="Defender", team_id="t2", archetype="defender",
            attributes=PlayerAttributes(
                scoring=40, passing=50, defense=80, speed=60,
                stamina=70, iq=60, ego=30, chaotic_alignment=20, fate=50,
            ),
        )
    return GameState(
        home_agents=[HooperState(hooper=home_hooper)],
        away_agents=[HooperState(hooper=away_hooper)],
        home_score=45,
        away_score=42,
        quarter=3,
        possession_number=50,
        home_has_ball=True,
    )


# ===================================================================
# Effect spec conversion
# ===================================================================


class TestEffectSpecToRegistered:
    """Test converting EffectSpec with codegen to RegisteredEffect."""

    def test_codegen_fields_populated(
        self, codegen_effect_spec: EffectSpec, simple_codegen_spec: CodegenEffectSpec,
    ) -> None:
        registered = effect_spec_to_registered(codegen_effect_spec, "p-1", 5)
        assert registered.effect_type == "codegen"
        assert registered.codegen_code == simple_codegen_spec.code
        assert registered.codegen_code_hash == simple_codegen_spec.code_hash
        assert registered.codegen_trust_level == "flow"
        assert registered.codegen_enabled is True

    def test_hook_points_from_codegen(self, codegen_effect_spec: EffectSpec) -> None:
        registered = effect_spec_to_registered(codegen_effect_spec, "p-1", 5)
        assert "sim.possession.post" in registered.hook_points

    def test_non_codegen_has_no_codegen_fields(self) -> None:
        spec = EffectSpec(
            effect_type="hook_callback",
            hook_point="sim.possession.pre",
            action_code={"type": "modify_score", "modifier": 1},
        )
        registered = effect_spec_to_registered(spec, "p-2", 3)
        assert registered.codegen_code is None
        assert registered.codegen_code_hash is None


# ===================================================================
# RegisteredEffect codegen execution
# ===================================================================


class TestFireCodegen:
    """Test codegen execution through RegisteredEffect.apply()."""

    def test_codegen_fires_and_returns_hook_result(
        self, game_state: GameState,
    ) -> None:
        code = "return HookResult(score_modifier=2)"
        code_hash = compute_code_hash(code)
        effect = RegisteredEffect(
            effect_id="e-1",
            proposal_id="p-1",
            _hook_points=["sim.possession.post"],
            effect_type="codegen",
            codegen_code=code,
            codegen_code_hash=code_hash,
            codegen_trust_level="numeric",
        )
        ctx = HookContext(
            game_state=game_state,
            rng=random.Random(42),
        )
        result = effect.apply("sim.possession.post", ctx)
        assert result.score_modifier == 2

    def test_codegen_hash_mismatch_disables(self) -> None:
        effect = RegisteredEffect(
            effect_id="e-2",
            proposal_id="p-1",
            _hook_points=["sim.possession.post"],
            effect_type="codegen",
            codegen_code="return HookResult(score_modifier=1)",
            codegen_code_hash="wrong_hash",
            codegen_trust_level="numeric",
        )
        ctx = HookContext(rng=random.Random(42))
        result = effect.apply("sim.possession.post", ctx)
        assert result.score_modifier == 0  # Falls back to empty
        assert effect.codegen_enabled is False
        assert "integrity" in effect.codegen_disabled_reason.lower()

    def test_codegen_disabled_returns_none(self, game_state: GameState) -> None:
        code = "return HookResult(score_modifier=5)"
        effect = RegisteredEffect(
            effect_id="e-3",
            proposal_id="p-1",
            _hook_points=["sim.possession.post"],
            effect_type="codegen",
            codegen_code=code,
            codegen_code_hash=compute_code_hash(code),
            codegen_trust_level="numeric",
            codegen_enabled=False,
        )
        ctx = HookContext(game_state=game_state, rng=random.Random(42))
        result = effect.apply("sim.possession.post", ctx)
        assert result.score_modifier == 0  # No-op

    def test_sandbox_violation_disables_immediately(
        self, game_state: GameState,
    ) -> None:
        """SandboxViolation (e.g. wrong return type) disables immediately."""
        bad_code = "return 'not a HookResult'"
        effect = RegisteredEffect(
            effect_id="e-4",
            proposal_id="p-1",
            _hook_points=["sim.possession.post"],
            effect_type="codegen",
            codegen_code=bad_code,
            codegen_code_hash=compute_code_hash(bad_code),
            codegen_trust_level="numeric",
        )
        ctx = HookContext(game_state=game_state, rng=random.Random(42))
        effect.apply("sim.possession.post", ctx)

        assert effect.codegen_enabled is False
        assert "Sandbox violation" in effect.codegen_disabled_reason

    def test_codegen_auto_disable_after_3_generic_errors(
        self, game_state: GameState,
    ) -> None:
        """Generic Python errors auto-disable after 3 consecutive failures."""
        # Code that causes a runtime error (not a SandboxViolation)
        bad_code = (
            "x = ctx.actor.attributes['nonexistent_key']\n"
            "return HookResult(score_modifier=x)"
        )
        effect = RegisteredEffect(
            effect_id="e-4b",
            proposal_id="p-1",
            _hook_points=["sim.possession.post"],
            effect_type="codegen",
            codegen_code=bad_code,
            codegen_code_hash=compute_code_hash(bad_code),
            codegen_trust_level="numeric",
        )
        ctx = HookContext(game_state=game_state, rng=random.Random(42))

        for _ in range(3):
            effect.apply("sim.possession.post", ctx)

        assert effect.codegen_enabled is False
        assert effect.codegen_consecutive_errors >= 3

    def test_codegen_trust_level_enforced(
        self, game_state: GameState,
    ) -> None:
        """NUMERIC trust level zeroes out narrative_note."""
        code = 'return HookResult(score_modifier=1, narrative_note="Should be zeroed")'
        effect = RegisteredEffect(
            effect_id="e-5",
            proposal_id="p-1",
            _hook_points=["sim.possession.post"],
            effect_type="codegen",
            codegen_code=code,
            codegen_code_hash=compute_code_hash(code),
            codegen_trust_level="numeric",
        )
        ctx = HookContext(game_state=game_state, rng=random.Random(42))
        result = effect.apply("sim.possession.post", ctx)
        assert result.score_modifier == 1
        assert result.narrative == ""  # Zeroed by trust enforcement

    def test_codegen_execution_count_increments(
        self, game_state: GameState,
    ) -> None:
        code = "return HookResult(score_modifier=1)"
        effect = RegisteredEffect(
            effect_id="e-6",
            proposal_id="p-1",
            _hook_points=["sim.possession.post"],
            effect_type="codegen",
            codegen_code=code,
            codegen_code_hash=compute_code_hash(code),
            codegen_trust_level="numeric",
        )
        ctx = HookContext(game_state=game_state, rng=random.Random(42))
        effect.apply("sim.possession.post", ctx)
        effect.apply("sim.possession.post", ctx)
        assert effect.codegen_execution_count == 2
        assert effect.codegen_consecutive_errors == 0


# ===================================================================
# Serialization
# ===================================================================


class TestCodegenSerialization:
    """Test to_dict/from_dict with codegen fields."""

    def test_round_trip(self) -> None:
        code = "return HookResult(score_modifier=3)"
        effect = RegisteredEffect(
            effect_id="e-ser",
            proposal_id="p-1",
            _hook_points=["sim.possession.post"],
            effect_type="codegen",
            codegen_code=code,
            codegen_code_hash=compute_code_hash(code),
            codegen_trust_level="flow",
            description="Test codegen",
        )
        d = effect.to_dict()
        assert d["codegen_code"] == code
        assert d["codegen_trust_level"] == "flow"

        restored = RegisteredEffect.from_dict(d)
        assert restored.codegen_code == code
        assert restored.codegen_code_hash == compute_code_hash(code)
        assert restored.codegen_trust_level == "flow"
        assert restored.codegen_enabled is True

    def test_non_codegen_no_extra_fields(self) -> None:
        effect = RegisteredEffect(
            effect_id="e-noncg",
            proposal_id="p-2",
            effect_type="hook_callback",
        )
        d = effect.to_dict()
        assert "codegen_code" not in d


# ===================================================================
# HookContext → GameContext mapping
# ===================================================================


class TestBuildGameContext:
    """Test _build_game_context helper."""

    def test_maps_actor_from_offense(self, game_state: GameState) -> None:
        ctx = HookContext(game_state=game_state, rng=random.Random(42))
        game_ctx = _build_game_context(ctx, CodegenTrustLevel.NUMERIC)
        assert game_ctx.actor.name == "Malik"  # type: ignore[union-attr]
        assert game_ctx.home_score == 45  # type: ignore[union-attr]

    def test_maps_opponent_from_defense(self, game_state: GameState) -> None:
        ctx = HookContext(game_state=game_state, rng=random.Random(42))
        game_ctx = _build_game_context(ctx, CodegenTrustLevel.NUMERIC)
        assert game_ctx.opponent is not None  # type: ignore[union-attr]
        assert game_ctx.opponent.name == "Defender"  # type: ignore[union-attr]

    def test_state_trust_level_includes_meta(self, game_state: GameState) -> None:
        ctx = HookContext(game_state=game_state, rng=random.Random(42))
        game_ctx = _build_game_context(ctx, CodegenTrustLevel.STATE)
        # STATE level should have meta_store_ref accessible
        assert hasattr(game_ctx, "_meta_store_ref")

    def test_flow_trust_level_includes_state_dict(self, game_state: GameState) -> None:
        ctx = HookContext(game_state=game_state, rng=random.Random(42))
        game_ctx = _build_game_context(ctx, CodegenTrustLevel.FLOW)
        assert game_ctx._state_dict  # type: ignore[union-attr]
        assert "home_score" in game_ctx._state_dict  # type: ignore[union-attr]


# ===================================================================
# CodegenHookResult → HookResult conversion
# ===================================================================


class TestCodegenResultConversion:
    """Test _codegen_result_to_hook_result helper."""

    def test_basic_conversion(self) -> None:
        cr = CodegenHookResult(score_modifier=3, narrative_note="Hello")
        result = _codegen_result_to_hook_result(cr)
        assert result.score_modifier == 3
        assert result.narrative == "Hello"

    def test_meta_writes_preserved(self) -> None:
        cr = CodegenHookResult(meta_writes={"team:t1": {"swagger": 5}})
        result = _codegen_result_to_hook_result(cr)
        assert result.meta_writes == {"team:t1": {"swagger": 5}}

    def test_block_action_preserved(self) -> None:
        cr = CodegenHookResult(block_action=True)
        result = _codegen_result_to_hook_result(cr)
        assert result.block_action is True


# ===================================================================
# Governance tier detection
# ===================================================================


class TestCodegenTierDetection:
    """Test tier detection for codegen effects."""

    def test_codegen_is_tier_4(self) -> None:
        """Codegen effects are always tier 4."""
        interp = ProposalInterpretation(
            effects=[
                EffectSpec(effect_type="codegen", description="AI code"),
            ],
            confidence=0.9,
        )
        tier = detect_tier_v2(interp, RuleSet())
        assert tier == 4

    def test_codegen_mixed_with_parameter_uses_highest(self) -> None:
        """Mixed proposal: codegen (4) + parameter_change (1) → tier 4."""
        interp = ProposalInterpretation(
            effects=[
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="three_point_value",
                    new_value=4,
                ),
                EffectSpec(effect_type="codegen", description="AI code"),
            ],
            confidence=0.9,
        )
        tier = detect_tier_v2(interp, RuleSet())
        assert tier == 4


# ===================================================================
# fire_effects with codegen
# ===================================================================


class TestFireEffectsWithCodegen:
    """Test fire_effects dispatches codegen correctly."""

    def test_codegen_effect_fires_in_fire_effects(
        self, game_state: GameState,
    ) -> None:
        code = "return HookResult(score_modifier=3)"
        effect = RegisteredEffect(
            effect_id="e-fire",
            proposal_id="p-1",
            _hook_points=["sim.possession.post"],
            effect_type="codegen",
            codegen_code=code,
            codegen_code_hash=compute_code_hash(code),
            codegen_trust_level="numeric",
        )
        ctx = HookContext(game_state=game_state, rng=random.Random(42))
        results = fire_effects("sim.possession.post", ctx, [effect])
        assert len(results) == 1
        assert results[0].score_modifier == 3
