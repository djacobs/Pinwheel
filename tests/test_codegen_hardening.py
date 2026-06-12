"""Tests for sandbox hardening (Phase 4).

Thread-based timeout (no SIGALRM), compile cache, AST arithmetic guards,
subprocess pre-flight with resource limits, and the per-game execution
budget.
"""

from __future__ import annotations

import random
import threading

import pytest

from pinwheel.core.codegen import (
    _COMPILE_CACHE,
    CodegenASTValidator,
    ParticipantView,
    SandboxedGameContext,
    SandboxViolation,
    compute_code_hash,
    execute_codegen_effect,
    preflight_codegen_effect,
)
from pinwheel.core.hooks import (
    CODEGEN_GAME_BUDGET_NS,
    HookContext,
    RegisteredEffect,
    fire_effects,
)


def _ctx() -> SandboxedGameContext:
    return SandboxedGameContext(
        _actor=ParticipantView(
            name="A", team_id="t1", attributes={"scoring": 50},
            stamina=1.0, on_court=True,
        ),
        _home_score=10,
        _away_score=8,
    )


class TestThreadTimeout:
    def test_simple_code_executes(self) -> None:
        result = execute_codegen_effect(
            "return HookResult(score_modifier=1)", _ctx(), random.Random(1),
        )
        assert result.score_modifier == 1

    def test_timeout_raises_sandbox_violation(self) -> None:
        # AST-legal but slow: nested bounded loops with arithmetic
        slow = (
            "x = 0\n"
            "for i in range(1000):\n"
            "    for j in range(1000):\n"
            "        for k in range(1000):\n"
            "            x = x + i + j + k\n"
            "return HookResult(score_modifier=1)"
        )
        with pytest.raises(SandboxViolation) as exc_info:
            execute_codegen_effect(
                slow, _ctx(), random.Random(1), timeout_seconds=0.05,
            )
        assert exc_info.value.violation_type == "timeout"

    def test_timeout_works_off_main_thread(self) -> None:
        """The SIGALRM approach only worked on the main thread — the
        thread-based timeout must work anywhere."""
        outcome: dict[str, object] = {}

        def _run() -> None:
            try:
                execute_codegen_effect(
                    (
                        "x = 0\n"
                        "for i in range(1000):\n"
                        "    for j in range(1000):\n"
                        "        for k in range(1000):\n"
                        "            x = x + 1\n"
                        "return HookResult()"
                    ),
                    _ctx(),
                    random.Random(1),
                    timeout_seconds=0.05,
                )
                outcome["raised"] = False
            except SandboxViolation as e:
                outcome["raised"] = True
                outcome["type"] = e.violation_type

        t = threading.Thread(target=_run)
        t.start()
        t.join(timeout=30)
        assert outcome.get("raised") is True
        assert outcome.get("type") == "timeout"

    def test_no_sigalrm_remains(self) -> None:
        import inspect

        import pinwheel.core.codegen as codegen_mod

        source = inspect.getsource(codegen_mod)
        assert "SIGALRM" not in source
        assert "signal.alarm" not in source


class TestCompileCache:
    def test_same_code_compiles_once(self) -> None:
        code = "return HookResult(score_modifier=2)"
        key = compute_code_hash(code)
        _COMPILE_CACHE.pop(key, None)

        execute_codegen_effect(code, _ctx(), random.Random(1))
        assert key in _COMPILE_CACHE
        cached = _COMPILE_CACHE[key]
        execute_codegen_effect(code, _ctx(), random.Random(1))
        assert _COMPILE_CACHE[key] is cached


class TestASTArithmeticGuards:
    def _violations(self, code: str) -> list[str]:
        return CodegenASTValidator().validate(code)

    def test_small_literal_pow_allowed(self) -> None:
        assert self._violations("x = ctx.home_score ** 2\nreturn HookResult()") == []

    def test_large_literal_pow_rejected(self) -> None:
        assert self._violations("x = 2 ** 100\nreturn HookResult()")

    def test_variable_exponent_rejected(self) -> None:
        assert self._violations(
            "x = 2 ** ctx.home_score\nreturn HookResult()"
        )

    def test_augmented_pow_rejected(self) -> None:
        assert self._violations("x = 2\nx **= 3\nreturn HookResult()")

    def test_giant_int_literal_rejected(self) -> None:
        assert self._violations("x = 10000000000\nreturn HookResult()")

    def test_reasonable_int_literal_allowed(self) -> None:
        assert self._violations("x = 1000000\nreturn HookResult()") == []

    def test_giant_string_literal_rejected(self) -> None:
        big = "a" * 600
        assert self._violations(f"x = '{big}'\nreturn HookResult()")


class TestPreflight:
    def test_well_behaved_code_passes(self) -> None:
        violations = preflight_codegen_effect(
            "if ctx.actor_is_home and ctx.home_score > ctx.away_score:\n"
            "    return HookResult(score_modifier=1)\n"
            "return HookResult()"
        )
        assert violations == []

    def test_ast_failure_short_circuits(self) -> None:
        violations = preflight_codegen_effect("import os\nreturn HookResult()")
        assert violations
        assert "Import" in violations[0]

    def test_code_crashing_on_edge_context_fails(self) -> None:
        """Code that assumes an opponent exists dies on the no-opponent
        context in the battery."""
        violations = preflight_codegen_effect(
            "x = ctx.opponent.stamina\nreturn HookResult()"
        )
        assert violations
        assert any("AttributeError" in v or "NoneType" in v for v in violations)

    def test_invalid_return_fails(self) -> None:
        violations = preflight_codegen_effect("return 42")
        assert violations
        assert any("invalid_return" in v for v in violations)


class TestPerGameBudget:
    def _effect(self) -> RegisteredEffect:
        code = "return HookResult(score_modifier=1)"
        return RegisteredEffect(
            effect_id="e-budget",
            proposal_id="p-budget",
            _hook_points=["sim.possession.post"],
            effect_type="codegen",
            codegen_code=code,
            codegen_code_hash=compute_code_hash(code),
            codegen_trust_level="numeric",
        )

    def test_exhausted_budget_skips_execution(self) -> None:
        effect = self._effect()
        effect.codegen_game_elapsed_ns = CODEGEN_GAME_BUDGET_NS + 1
        ctx = HookContext(rng=random.Random(1))
        assert fire_effects("sim.possession.post", ctx, [effect]) == []
        assert effect.codegen_execution_count == 0

    def test_fresh_budget_executes_and_accumulates(self) -> None:
        effect = self._effect()
        ctx = HookContext(rng=random.Random(1))
        results = fire_effects("sim.possession.post", ctx, [effect])
        assert len(results) == 1
        assert effect.codegen_game_elapsed_ns > 0

    def test_simulate_game_resets_budget(self) -> None:
        from pinwheel.core.simulation import simulate_game
        from pinwheel.models.rules import RuleSet
        from pinwheel.models.team import (
            Hooper,
            PlayerAttributes,
            Team,
            Venue,
            suppress_budget_check,
        )

        with suppress_budget_check():
            attrs = PlayerAttributes(
                scoring=50, passing=40, defense=40, speed=40, stamina=40,
                iq=50, ego=30, chaotic_alignment=20, fate=30,
            )

        def _team(prefix: str) -> Team:
            return Team(
                id=f"{prefix}-id",
                name=prefix,
                venue=Venue(name=f"{prefix} Arena", capacity=5000),
                hoopers=[
                    Hooper.model_construct(
                        id=f"{prefix}-{i}",
                        name=f"{prefix}-{i}",
                        team_id=f"{prefix}-id",
                        archetype="sharpshooter",
                        backstory="",
                        attributes=attrs,
                        moves=[],
                        is_starter=True,
                    )
                    for i in range(3)
                ],
            )

        effect = self._effect()
        effect.codegen_game_elapsed_ns = CODEGEN_GAME_BUDGET_NS + 1
        simulate_game(
            _team("home"), _team("away"), RuleSet(), seed=1,
            effect_registry=[effect],
        )
        # The game reset the stale budget and the effect executed
        assert effect.codegen_execution_count > 0
