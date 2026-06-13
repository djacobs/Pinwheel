"""Tests for the codegen pre-execution approval gate (Phase 2).

Lifecycle: council approval registers the effect as ``pending`` (inert);
an admin approves or rejects; decisions persist as events and survive
registry reloads. Also covers the /disable-effect persistence regression.
"""

from __future__ import annotations

import random

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.core.codegen import compute_code_hash
from pinwheel.core.effects import (
    EffectRegistry,
    approve_codegen_effect,
    effect_spec_to_registered,
    load_effect_registry,
    register_effects_for_proposal,
    reject_codegen_effect,
)
from pinwheel.core.hooks import HookContext, RegisteredEffect, fire_effects
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.codegen import (
    CodegenEffectSpec,
    CodegenTrustLevel,
    CouncilReview,
)
from pinwheel.models.governance import EffectSpec
from pinwheel.models.rules import RuleSet

# --- Fixtures ---


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def repo(engine: AsyncEngine) -> Repository:
    async with get_session(engine) as session:
        yield Repository(session)


@pytest.fixture
async def season_id(repo: Repository) -> str:
    league = await repo.create_league("Test League")
    season = await repo.create_season(
        league_id=league.id,
        name="Season 1",
        starting_ruleset=RuleSet().model_dump(),
    )
    return season.id


_CODE = "return HookResult(score_modifier=1)"


def _codegen_spec() -> EffectSpec:
    return EffectSpec(
        effect_type="codegen",
        codegen=CodegenEffectSpec(
            code=_CODE,
            code_hash=compute_code_hash(_CODE),
            trust_level=CodegenTrustLevel.NUMERIC,
            council_review=CouncilReview(
                proposal_id="p-1",
                code_hash=compute_code_hash(_CODE),
                consensus=True,
            ),
            hook_points=["sim.possession.post"],
            description="Add 1 point",
        ),
        description="Test codegen effect",
    )


def _custom_mechanic_spec() -> EffectSpec:
    return EffectSpec(
        effect_type="custom_mechanic",
        description="Approximation placeholder",
        mechanic_observable_behavior="Something chaotic happens",
    )


# --- Registration state ---


class TestPendingRegistration:
    def test_codegen_registers_pending_by_default(self) -> None:
        effect = effect_spec_to_registered(_codegen_spec(), "p-1", 1)
        assert effect.codegen_approval_status == "pending"

    def test_auto_approve_skips_gate(self) -> None:
        effect = effect_spec_to_registered(
            _codegen_spec(), "p-1", 1, codegen_auto_approve=True,
        )
        assert effect.codegen_approval_status == "approved"

    def test_non_codegen_unaffected(self) -> None:
        effect = effect_spec_to_registered(_custom_mechanic_spec(), "p-1", 1)
        assert effect.codegen_approval_status == "approved"

    def test_serialization_roundtrip_preserves_status(self) -> None:
        effect = effect_spec_to_registered(_codegen_spec(), "p-1", 1)
        restored = RegisteredEffect.from_dict(effect.to_dict())
        assert restored.codegen_approval_status == "pending"

    def test_legacy_payload_defaults_to_approved(self) -> None:
        """Effects serialized before the gate existed keep working."""
        effect = effect_spec_to_registered(
            _codegen_spec(), "p-1", 1, codegen_auto_approve=True,
        )
        payload = effect.to_dict()
        del payload["codegen_approval_status"]
        restored = RegisteredEffect.from_dict(payload)
        assert restored.codegen_approval_status == "approved"


# --- Execution gating ---


class TestExecutionGate:
    def _fire(self, effect: RegisteredEffect) -> list:
        ctx = HookContext(rng=random.Random(1))
        return fire_effects("sim.possession.post", ctx, [effect])

    def test_pending_effect_does_not_execute(self) -> None:
        effect = effect_spec_to_registered(_codegen_spec(), "p-1", 1)
        results = self._fire(effect)
        assert results == []
        assert effect.codegen_execution_count == 0

    def test_rejected_effect_does_not_execute(self) -> None:
        effect = effect_spec_to_registered(_codegen_spec(), "p-1", 1)
        effect.codegen_approval_status = "rejected"
        assert self._fire(effect) == []

    def test_approved_effect_executes(self) -> None:
        effect = effect_spec_to_registered(
            _codegen_spec(), "p-1", 1, codegen_auto_approve=True,
        )
        results = self._fire(effect)
        assert len(results) == 1
        assert results[0].score_modifier == 1
        assert effect.codegen_execution_count == 1


# --- Admin decisions persist ---


class TestApprovalLifecycle:
    async def _register(
        self, repo: Repository, season_id: str,
        include_placeholder: bool = False,
    ) -> EffectRegistry:
        registry = EffectRegistry()
        specs = [_codegen_spec()]
        if include_placeholder:
            specs.append(_custom_mechanic_spec())
        await register_effects_for_proposal(
            repo, registry, "p-1", specs, season_id, current_round=1,
        )
        return registry

    async def test_approve_persists_across_reload(
        self, repo: Repository, season_id: str,
    ) -> None:
        registry = await self._register(repo, season_id)
        effect = next(
            e for e in registry.get_all_active() if e.effect_type == "codegen"
        )
        assert effect.codegen_approval_status == "pending"

        ok = await approve_codegen_effect(
            repo, registry, effect.effect_id, season_id, admin_id="admin-1",
        )
        assert ok
        assert effect.codegen_approval_status == "approved"

        reloaded = await load_effect_registry(repo, season_id)
        reloaded_effect = reloaded.get_effect(effect.effect_id)
        assert reloaded_effect is not None
        assert reloaded_effect.codegen_approval_status == "approved"

    async def test_reject_persists_across_reload(
        self, repo: Repository, season_id: str,
    ) -> None:
        registry = await self._register(repo, season_id)
        effect = next(
            e for e in registry.get_all_active() if e.effect_type == "codegen"
        )
        ok = await reject_codegen_effect(
            repo, registry, effect.effect_id, season_id,
            admin_id="admin-1", reason="too chaotic",
        )
        assert ok

        reloaded = await load_effect_registry(repo, season_id)
        reloaded_effect = reloaded.get_effect(effect.effect_id)
        assert reloaded_effect is not None
        assert reloaded_effect.codegen_approval_status == "rejected"

    async def test_approve_repeals_custom_mechanic_placeholder(
        self, repo: Repository, season_id: str,
    ) -> None:
        registry = await self._register(
            repo, season_id, include_placeholder=True,
        )
        codegen = next(
            e for e in registry.get_all_active() if e.effect_type == "codegen"
        )
        placeholder = next(
            e for e in registry.get_all_active()
            if e.effect_type == "custom_mechanic"
        )

        await approve_codegen_effect(
            repo, registry, codegen.effect_id, season_id,
        )

        assert registry.get_effect(placeholder.effect_id) is None
        # The repeal is persisted — a reload excludes the placeholder too
        reloaded = await load_effect_registry(repo, season_id)
        assert reloaded.get_effect(placeholder.effect_id) is None
        assert reloaded.get_effect(codegen.effect_id) is not None

    async def test_approve_unknown_effect_returns_false(
        self, repo: Repository, season_id: str,
    ) -> None:
        registry = EffectRegistry()
        assert not await approve_codegen_effect(
            repo, registry, "nope", season_id,
        )


class TestDisablePersistence:
    """Regression: /disable-effect appends effect.codegen_disabled, but
    load_effect_registry never replayed it — the effect came back enabled
    on the next round."""

    async def test_disable_event_survives_reload(
        self, repo: Repository, season_id: str,
    ) -> None:
        registry = EffectRegistry()
        await register_effects_for_proposal(
            repo, registry, "p-1", [_codegen_spec()], season_id,
            current_round=1, codegen_auto_approve=True,
        )
        effect = next(iter(registry.get_all_active()))

        # What /disable-effect does: mutate + append the event
        effect.codegen_enabled = False
        effect.codegen_disabled_reason = "Disabled by admin"
        await repo.append_event(
            event_type="effect.codegen_disabled",
            aggregate_id=effect.effect_id,
            aggregate_type="effect",
            season_id=season_id,
            payload={"effect_id": effect.effect_id, "reason": "admin_disabled"},
        )

        reloaded = await load_effect_registry(repo, season_id)
        reloaded_effect = reloaded.get_effect(effect.effect_id)
        assert reloaded_effect is not None
        assert reloaded_effect.codegen_enabled is False
        assert reloaded_effect.codegen_disabled_reason == "admin_disabled"


# --- Summary strings ---


class TestEffectsSummaryStates:
    def test_summary_shows_pending(self) -> None:
        registry = EffectRegistry()
        registry.register(effect_spec_to_registered(_codegen_spec(), "p-1", 1))
        assert "awaiting admin approval" in registry.build_effects_summary()

    def test_summary_shows_rejected(self) -> None:
        registry = EffectRegistry()
        effect = effect_spec_to_registered(_codegen_spec(), "p-1", 1)
        effect.codegen_approval_status = "rejected"
        registry.register(effect)
        assert "rejected by admin" in registry.build_effects_summary()

    def test_summary_shows_enabled_when_approved(self) -> None:
        registry = EffectRegistry()
        effect = effect_spec_to_registered(
            _codegen_spec(), "p-1", 1, codegen_auto_approve=True,
        )
        registry.register(effect)
        assert "enabled" in registry.build_effects_summary()


class TestAutoDisablePersistence:
    """The sandbox kill switch runs inside the sync sim with no DB access —
    persist_codegen_disables must write the event so the disable survives
    the next registry reload."""

    async def test_auto_disable_persists_across_reload(
        self, repo: Repository, season_id: str,
    ) -> None:
        from pinwheel.core.effects import persist_codegen_disables

        registry = EffectRegistry()
        await register_effects_for_proposal(
            repo, registry, "p-1", [_codegen_spec()], season_id,
            current_round=1, codegen_auto_approve=True,
        )
        effect = next(iter(registry.get_all_active()))

        # What the sandbox kill switch does mid-game (in-memory only)
        effect._disable_codegen("Sandbox violation: timeout")
        assert effect.codegen_enabled is False

        persisted = await persist_codegen_disables(repo, registry, season_id)
        assert persisted == 1

        reloaded = await load_effect_registry(repo, season_id)
        reloaded_effect = reloaded.get_effect(effect.effect_id)
        assert reloaded_effect is not None
        assert reloaded_effect.codegen_enabled is False

    async def test_persist_is_idempotent(
        self, repo: Repository, season_id: str,
    ) -> None:
        from pinwheel.core.effects import persist_codegen_disables

        registry = EffectRegistry()
        await register_effects_for_proposal(
            repo, registry, "p-1", [_codegen_spec()], season_id,
            current_round=1, codegen_auto_approve=True,
        )
        effect = next(iter(registry.get_all_active()))
        effect._disable_codegen("boom")

        assert await persist_codegen_disables(repo, registry, season_id) == 1
        assert await persist_codegen_disables(repo, registry, season_id) == 0

    async def test_enabled_effects_not_persisted(
        self, repo: Repository, season_id: str,
    ) -> None:
        from pinwheel.core.effects import persist_codegen_disables

        registry = EffectRegistry()
        await register_effects_for_proposal(
            repo, registry, "p-1", [_codegen_spec()], season_id,
            current_round=1, codegen_auto_approve=True,
        )
        assert await persist_codegen_disables(repo, registry, season_id) == 0
