"""Tests for the codegen pipeline router (Phase 3).

Covers: the escalation trigger, the background council run (both vote
orderings, idempotency), the tally-time merge of council output, the
scheduler tick's retry driver, and the /rerun-council consumer.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.config import Settings
from pinwheel.core.codegen import compute_code_hash
from pinwheel.core.codegen_pipeline import (
    CODEGEN_SIM_HOOKS,
    MAX_CODEGEN_RETRIES,
    _consume_rerun_requests,
    run_codegen_for_proposal,
    should_escalate_to_codegen,
    tick_codegen_pipeline,
)
from pinwheel.core.effects import (
    EffectRegistry,
    load_effect_registry,
    register_effects_for_proposal,
)
from pinwheel.core.governance import (
    cast_vote,
    confirm_proposal,
    submit_proposal,
    tally_governance_with_effects,
)
from pinwheel.core.tokens import regenerate_tokens
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.codegen import (
    CodegenEffectSpec,
    CodegenTrustLevel,
    CouncilReview,
)
from pinwheel.models.governance import (
    EffectSpec,
    ProposalInterpretation,
    RuleInterpretation,
)
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


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "pinwheel_codegen_enabled": True,
        "anthropic_api_key": "",
        "pinwheel_env": "development",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _interpretation(
    effect_types: list[str],
    confidence: float = 0.9,
    injection_flagged: bool = False,
    rejection_reason: str = "",
) -> ProposalInterpretation:
    effects = []
    for et in effect_types:
        if et == "custom_mechanic":
            effects.append(
                EffectSpec(
                    effect_type="custom_mechanic",
                    description="A wild mechanic",
                    mechanic_observable_behavior="Chaos",
                )
            )
        else:
            effects.append(
                EffectSpec(effect_type=et, description=f"{et} effect")
            )
    return ProposalInterpretation(
        effects=effects,
        confidence=confidence,
        injection_flagged=injection_flagged,
        rejection_reason=rejection_reason,
        original_text_echo="test",
    )


# --- Escalation trigger ---


class TestShouldEscalate:
    def test_custom_mechanic_escalates(self) -> None:
        assert should_escalate_to_codegen(
            _interpretation(["custom_mechanic"]), _settings(),
        )

    def test_primitives_do_not_escalate(self) -> None:
        assert not should_escalate_to_codegen(
            _interpretation(["narrative"]), _settings(),
        )

    def test_flag_off_blocks(self) -> None:
        assert not should_escalate_to_codegen(
            _interpretation(["custom_mechanic"]),
            _settings(pinwheel_codegen_enabled=False),
        )

    def test_injection_flagged_blocks(self) -> None:
        assert not should_escalate_to_codegen(
            _interpretation(["custom_mechanic"], injection_flagged=True),
            _settings(),
        )

    def test_low_confidence_blocks(self) -> None:
        assert not should_escalate_to_codegen(
            _interpretation(["custom_mechanic"], confidence=0.3), _settings(),
        )

    def test_none_interpretation_blocks(self) -> None:
        assert not should_escalate_to_codegen(None, _settings())


# --- Background council run ---


class TestRunCodegenForProposal:
    async def test_mock_path_appends_ready_event(
        self, engine: AsyncEngine, season_id: str,
    ) -> None:
        ok = await run_codegen_for_proposal(
            engine,
            _settings(),
            proposal_id="p-1",
            season_id=season_id,
            raw_text="every dunk summons a ghost defender",
        )
        assert ok
        async with get_session(engine) as session:
            repo = Repository(session)
            ready = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["proposal.codegen_ready"],
            )
        assert len(ready) == 1
        assert ready[0].aggregate_id == "p-1"
        spec_data = ready[0].payload.get("effect_spec")
        assert isinstance(spec_data, dict)
        assert spec_data.get("effect_type") == "codegen"

    async def test_production_without_key_rejects(
        self, engine: AsyncEngine, season_id: str,
    ) -> None:
        settings = _settings(
            pinwheel_env="production",
            session_secret_key="x" * 32,
        )
        ok = await run_codegen_for_proposal(
            engine,
            settings,
            proposal_id="p-1",
            season_id=season_id,
            raw_text="anything",
        )
        assert not ok
        async with get_session(engine) as session:
            repo = Repository(session)
            rejected = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["proposal.codegen_rejected"],
            )
        assert len(rejected) == 1

    async def test_registers_when_proposal_already_passed(
        self, engine: AsyncEngine, season_id: str,
    ) -> None:
        """Council finished after the vote — the effect registers
        immediately, pending the admin gate."""
        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.append_event(
                event_type="proposal.passed",
                aggregate_id="p-1",
                aggregate_type="proposal",
                season_id=season_id,
                payload={"proposal_id": "p-1"},
            )
            await session.commit()

        ok = await run_codegen_for_proposal(
            engine,
            _settings(),
            proposal_id="p-1",
            season_id=season_id,
            raw_text="gravity reverses in the fourth quarter",
        )
        assert ok

        async with get_session(engine) as session:
            repo = Repository(session)
            registry = await load_effect_registry(repo, season_id)
        codegen_effects = [
            e for e in registry.get_all_active() if e.effect_type == "codegen"
        ]
        assert len(codegen_effects) == 1
        assert codegen_effects[0].codegen_approval_status == "pending"

    async def test_second_run_is_idempotent(
        self, engine: AsyncEngine, season_id: str,
    ) -> None:
        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.append_event(
                event_type="proposal.passed",
                aggregate_id="p-1",
                aggregate_type="proposal",
                season_id=season_id,
                payload={},
            )
            await session.commit()

        for _ in range(2):
            await run_codegen_for_proposal(
                engine,
                _settings(),
                proposal_id="p-1",
                season_id=season_id,
                raw_text="gravity reverses",
            )

        async with get_session(engine) as session:
            repo = Repository(session)
            registry = await load_effect_registry(repo, season_id)
        codegen_effects = [
            e for e in registry.get_all_active() if e.effect_type == "codegen"
        ]
        assert len(codegen_effects) == 1

    async def test_invalid_hook_points_rejected(
        self, engine: AsyncEngine, season_id: str, monkeypatch,
    ) -> None:
        """Council output targeting hooks the sim never fires is rejected
        instead of registering a dead effect."""
        from pinwheel.ai import codegen_council

        code = "return HookResult(score_modifier=1)"

        def _bad_mock(text: str) -> CodegenEffectSpec:
            return CodegenEffectSpec(
                code=code,
                code_hash=compute_code_hash(code),
                trust_level=CodegenTrustLevel.NUMERIC,
                council_review=CouncilReview(
                    proposal_id="p-1",
                    code_hash=compute_code_hash(code),
                    consensus=True,
                ),
                hook_points=["sim.made.up.hook"],
                description="bad hooks",
            )

        monkeypatch.setattr(
            codegen_council, "generate_codegen_effect_mock", _bad_mock,
        )
        ok = await run_codegen_for_proposal(
            engine,
            _settings(),
            proposal_id="p-1",
            season_id=season_id,
            raw_text="anything",
        )
        assert not ok
        async with get_session(engine) as session:
            repo = Repository(session)
            rejected = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["proposal.codegen_rejected"],
            )
        assert len(rejected) == 1
        assert "hook" in str(rejected[0].payload.get("reasons", [])).lower()

    def test_mock_hooks_are_real(self) -> None:
        """The mock generator's hook points must be hooks the sim fires."""
        from pinwheel.ai.codegen_council import generate_codegen_effect_mock

        spec = generate_codegen_effect_mock("test proposal")
        assert all(hp in CODEGEN_SIM_HOOKS for hp in spec.hook_points)


# --- Tally-time merge ---


class TestTallyMergesCodegenReady:
    async def test_codegen_ready_registers_at_pass_time(
        self, repo: Repository, season_id: str,
    ) -> None:
        team = await repo.create_team(season_id=season_id, name="Team A")
        gov_id = "gov-1"
        await regenerate_tokens(repo, gov_id, team.id, season_id)

        proposal = await submit_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team.id,
            season_id=season_id,
            window_id="",
            raw_text="every dunk summons a ghost",
            interpretation=RuleInterpretation(parameter=None),
            ruleset=RuleSet(),
        )
        proposal = await confirm_proposal(repo, proposal)

        # Council finished BEFORE the tally — ready event is on the books
        code = "return HookResult(score_modifier=1)"
        spec = EffectSpec(
            effect_type="codegen",
            codegen=CodegenEffectSpec(
                code=code,
                code_hash=compute_code_hash(code),
                trust_level=CodegenTrustLevel.NUMERIC,
                council_review=CouncilReview(
                    proposal_id=proposal.id,
                    code_hash=compute_code_hash(code),
                    consensus=True,
                ),
                hook_points=["sim.possession.post"],
                description="ghost",
            ),
            description="ghost",
        )
        await repo.append_event(
            event_type="proposal.codegen_ready",
            aggregate_id=proposal.id,
            aggregate_type="proposal",
            season_id=season_id,
            payload={
                "code_hash": compute_code_hash(code),
                "effect_spec": spec.model_dump(mode="json"),
            },
        )

        vote = await cast_vote(
            repo=repo,
            proposal=proposal,
            governor_id=gov_id,
            team_id=team.id,
            vote_choice="yes",
            weight=1.0,
        )

        registry = EffectRegistry()
        await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={proposal.id: [vote]},
            current_ruleset=RuleSet(),
            round_number=2,
            effect_registry=registry,
        )

        codegen_effects = [
            e for e in registry.get_all_active() if e.effect_type == "codegen"
        ]
        assert len(codegen_effects) == 1
        assert codegen_effects[0].codegen_approval_status == "pending"

    async def test_already_registered_hash_not_duplicated(
        self, repo: Repository, season_id: str,
    ) -> None:
        """If the pipeline already registered the spec (council finished
        after pass), the tally merge must not register it again."""
        team = await repo.create_team(season_id=season_id, name="Team A")
        gov_id = "gov-1"
        await regenerate_tokens(repo, gov_id, team.id, season_id)

        proposal = await submit_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team.id,
            season_id=season_id,
            window_id="",
            raw_text="ghost defender",
            interpretation=RuleInterpretation(parameter=None),
            ruleset=RuleSet(),
        )
        proposal = await confirm_proposal(repo, proposal)

        code = "return HookResult(score_modifier=1)"
        spec = EffectSpec(
            effect_type="codegen",
            codegen=CodegenEffectSpec(
                code=code,
                code_hash=compute_code_hash(code),
                trust_level=CodegenTrustLevel.NUMERIC,
                council_review=CouncilReview(
                    proposal_id=proposal.id,
                    code_hash=compute_code_hash(code),
                    consensus=True,
                ),
                hook_points=["sim.possession.post"],
                description="ghost",
            ),
            description="ghost",
        )
        registry = EffectRegistry()
        # Pipeline registered it directly
        await register_effects_for_proposal(
            repo, registry, proposal.id, [spec], season_id, current_round=1,
        )
        await repo.append_event(
            event_type="proposal.codegen_ready",
            aggregate_id=proposal.id,
            aggregate_type="proposal",
            season_id=season_id,
            payload={
                "code_hash": compute_code_hash(code),
                "effect_spec": spec.model_dump(mode="json"),
            },
        )

        vote = await cast_vote(
            repo=repo,
            proposal=proposal,
            governor_id=gov_id,
            team_id=team.id,
            vote_choice="yes",
            weight=1.0,
        )
        await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={proposal.id: [vote]},
            current_ruleset=RuleSet(),
            round_number=2,
            effect_registry=registry,
        )

        codegen_effects = [
            e for e in registry.get_all_active() if e.effect_type == "codegen"
        ]
        assert len(codegen_effects) == 1


# --- Scheduler tick ---


class TestTickCodegenPipeline:
    async def test_tick_redrives_pending_request(
        self, engine: AsyncEngine, season_id: str,
    ) -> None:
        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.append_event(
                event_type="proposal.codegen_requested",
                aggregate_id="p-1",
                aggregate_type="proposal",
                season_id=season_id,
                payload={"raw_text": "ghost defender"},
            )
            await session.commit()

        await tick_codegen_pipeline(engine, _settings())

        async with get_session(engine) as session:
            repo = Repository(session)
            ready = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["proposal.codegen_ready"],
            )
        assert len(ready) == 1

    async def test_tick_respects_retry_cap(
        self, engine: AsyncEngine, season_id: str,
    ) -> None:
        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.append_event(
                event_type="proposal.codegen_requested",
                aggregate_id="p-1",
                aggregate_type="proposal",
                season_id=season_id,
                payload={"raw_text": "ghost defender"},
            )
            for _ in range(MAX_CODEGEN_RETRIES):
                await repo.append_event(
                    event_type="proposal.codegen_failed",
                    aggregate_id="p-1",
                    aggregate_type="proposal",
                    season_id=season_id,
                    payload={"reason": "exception"},
                )
            await session.commit()

        await tick_codegen_pipeline(engine, _settings())

        async with get_session(engine) as session:
            repo = Repository(session)
            ready = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["proposal.codegen_ready"],
            )
        assert ready == []

    async def test_tick_never_raises_without_season(
        self, engine: AsyncEngine,
    ) -> None:
        await tick_codegen_pipeline(engine, _settings())


# --- Rerun-council consumer ---


class TestRerunConsumer:
    async def _register_codegen(
        self, engine: AsyncEngine, season_id: str,
    ) -> str:
        code = "return HookResult(score_modifier=1)"
        spec = EffectSpec(
            effect_type="codegen",
            codegen=CodegenEffectSpec(
                code=code,
                code_hash=compute_code_hash(code),
                trust_level=CodegenTrustLevel.NUMERIC,
                council_review=CouncilReview(
                    proposal_id="p-1",
                    code_hash=compute_code_hash(code),
                    consensus=True,
                ),
                hook_points=["sim.possession.post"],
                description="rerun target",
            ),
            description="rerun target",
        )
        async with get_session(engine) as session:
            repo = Repository(session)
            registry = EffectRegistry()
            registered = await register_effects_for_proposal(
                repo, registry, "p-1", [spec], season_id,
                current_round=1, codegen_auto_approve=True,
            )
            effect_id = registered[0].effect_id
            await repo.append_event(
                event_type="effect.council_rerun_requested",
                aggregate_id=effect_id,
                aggregate_type="effect",
                season_id=season_id,
                payload={"effect_id": effect_id},
            )
            await session.commit()
        return effect_id

    async def test_rejecting_rerun_disables_effect(
        self, engine: AsyncEngine, season_id: str, monkeypatch,
    ) -> None:
        effect_id = await self._register_codegen(engine, season_id)

        async def _rejecting_review(code, text, api_key, proposal_id="", model=""):
            return CouncilReview(
                proposal_id=proposal_id,
                code_hash=compute_code_hash(code),
                consensus=False,
                flagged_for_admin=True,
                flag_reasons=["security: suspicious"],
            )

        from pinwheel.ai import codegen_council

        monkeypatch.setattr(
            codegen_council, "review_existing_code", _rejecting_review,
        )

        consumed = await _consume_rerun_requests(
            engine, _settings(anthropic_api_key="k"), season_id,
        )
        assert consumed == 1

        async with get_session(engine) as session:
            repo = Repository(session)
            completed = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["effect.council_rerun_completed"],
            )
            assert len(completed) == 1
            assert completed[0].payload.get("verdict") == "rejected"
            # The disable persists across registry reloads
            registry = await load_effect_registry(repo, season_id)
        effect = registry.get_effect(effect_id)
        assert effect is not None
        assert effect.codegen_enabled is False

    async def test_approving_rerun_keeps_effect_enabled(
        self, engine: AsyncEngine, season_id: str, monkeypatch,
    ) -> None:
        effect_id = await self._register_codegen(engine, season_id)

        async def _approving_review(code, text, api_key, proposal_id="", model=""):
            return CouncilReview(
                proposal_id=proposal_id,
                code_hash=compute_code_hash(code),
                consensus=True,
            )

        from pinwheel.ai import codegen_council

        monkeypatch.setattr(
            codegen_council, "review_existing_code", _approving_review,
        )

        consumed = await _consume_rerun_requests(
            engine, _settings(anthropic_api_key="k"), season_id,
        )
        assert consumed == 1

        async with get_session(engine) as session:
            repo = Repository(session)
            registry = await load_effect_registry(repo, season_id)
        effect = registry.get_effect(effect_id)
        assert effect is not None
        assert effect.codegen_enabled is True

    async def test_rerun_not_consumed_twice(
        self, engine: AsyncEngine, season_id: str, monkeypatch,
    ) -> None:
        await self._register_codegen(engine, season_id)

        async def _approving_review(code, text, api_key, proposal_id="", model=""):
            return CouncilReview(
                proposal_id=proposal_id,
                code_hash=compute_code_hash(code),
                consensus=True,
            )

        from pinwheel.ai import codegen_council

        monkeypatch.setattr(
            codegen_council, "review_existing_code", _approving_review,
        )

        settings = _settings(anthropic_api_key="k")
        assert await _consume_rerun_requests(engine, settings, season_id) == 1
        assert await _consume_rerun_requests(engine, settings, season_id) == 0
