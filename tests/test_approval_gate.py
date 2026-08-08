"""Tests for the enforced admin approval gate (PINWHEEL_RULES_REQUIRE_APPROVAL).

When the gate is on, passing proposals are recorded as passed but held
(proposal.enactment_held) instead of enacting. The admin then approves
(enact the tally-time snapshot) or rejects (refund, nothing enacts).
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.ai.interpreter import interpret_proposal_mock
from pinwheel.core.effects import EffectRegistry, load_effect_registry
from pinwheel.core.governance import (
    approve_held_proposal,
    cast_vote,
    confirm_proposal,
    get_held_proposals,
    reject_held_proposal,
    submit_proposal,
    tally_governance_with_effects,
)
from pinwheel.core.tokens import get_token_balance, regenerate_tokens
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.governance import EffectSpec, ProposalInterpretation
from pinwheel.models.rules import RuleSet


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


@pytest.fixture
async def team_id(repo: Repository, season_id: str) -> str:
    team = await repo.create_team(season_id=season_id, name="Test Team")
    return team.id


@pytest.fixture
async def gov_a(repo: Repository, team_id: str, season_id: str) -> str:
    gov_id = "gov-a"
    await regenerate_tokens(repo, gov_id, team_id, season_id)
    return gov_id


async def _passed_proposal_setup(
    repo: Repository,
    season_id: str,
    team_id: str,
    gov_a: str,
    with_effects: bool = False,
):
    """Submit, confirm, and yes-vote a three_point_value=5 proposal."""
    interpretation = interpret_proposal_mock("Make three pointers worth 5", RuleSet())
    interp_v2 = None
    if with_effects:
        interp_v2 = ProposalInterpretation(
            effects=[
                EffectSpec(
                    effect_type="hook_callback",
                    hook_point="sim.shot.pre",
                    action_code={"type": "modify_probability", "modifier": 0.05},
                    description="Hot hand",
                )
            ],
            confidence=0.9,
        )
    proposal = await submit_proposal(
        repo=repo,
        governor_id=gov_a,
        team_id=team_id,
        season_id=season_id,
        window_id="w-1",
        raw_text="Make three pointers worth 5",
        interpretation=interpretation,
        ruleset=RuleSet(),
        interpretation_v2=interp_v2,
    )
    proposal = await confirm_proposal(repo, proposal)
    vote = await cast_vote(
        repo=repo,
        proposal=proposal,
        governor_id=gov_a,
        team_id=team_id,
        vote_choice="yes",
        weight=1.0,
    )
    return proposal, vote


class TestApprovalGateHolds:
    async def test_passed_proposal_is_held_not_enacted(
        self, repo: Repository, season_id: str, team_id: str, gov_a: str
    ):
        proposal, vote = await _passed_proposal_setup(repo, season_id, team_id, gov_a)
        registry = EffectRegistry()

        new_ruleset, tallies = await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={proposal.id: [vote]},
            current_ruleset=RuleSet(),
            round_number=1,
            effect_registry=registry,
            require_approval=True,
        )

        # Pass is recorded, but nothing enacted
        assert tallies[0].passed is True
        assert new_ruleset.three_point_value == RuleSet().three_point_value
        held = await get_held_proposals(repo, season_id)
        assert len(held) == 1
        assert held[0]["proposal_id"] == proposal.id
        enacted = await repo.get_events_by_type(
            season_id=season_id, event_types=["rule.enacted"]
        )
        assert not enacted

    async def test_gate_off_enacts_immediately(
        self, repo: Repository, season_id: str, team_id: str, gov_a: str
    ):
        proposal, vote = await _passed_proposal_setup(repo, season_id, team_id, gov_a)

        new_ruleset, tallies = await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={proposal.id: [vote]},
            current_ruleset=RuleSet(),
            round_number=1,
            effect_registry=EffectRegistry(),
            require_approval=False,
        )

        assert tallies[0].passed is True
        assert new_ruleset.three_point_value == 5
        assert not await get_held_proposals(repo, season_id)


class TestApproveHeld:
    async def test_approve_enacts_snapshot(
        self, repo: Repository, season_id: str, team_id: str, gov_a: str
    ):
        proposal, vote = await _passed_proposal_setup(
            repo, season_id, team_id, gov_a, with_effects=True
        )
        registry = EffectRegistry()
        await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={proposal.id: [vote]},
            current_ruleset=RuleSet(),
            round_number=1,
            effect_registry=registry,
            effects_v2_by_proposal={
                proposal.id: [
                    EffectSpec(
                        effect_type="hook_callback",
                        hook_point="sim.shot.pre",
                        action_code={"type": "modify_probability", "modifier": 0.05},
                        description="Hot hand",
                    )
                ]
            },
            require_approval=True,
        )
        held = await get_held_proposals(repo, season_id)
        assert len(held) == 1

        approve_registry = await load_effect_registry(repo, season_id)
        new_ruleset = await approve_held_proposal(
            repo=repo,
            season_id=season_id,
            held_payload=held[0],
            effect_registry=approve_registry,
        )

        # Parameter enacted and persisted to the season
        assert new_ruleset.three_point_value == 5
        season = await repo.get_season(season_id)
        assert season.current_ruleset["three_point_value"] == 5
        # Effect registered
        assert approve_registry.get_effects_for_proposal(proposal.id)
        registered = await repo.get_events_by_type(
            season_id=season_id, event_types=["effect.registered"]
        )
        assert registered
        # No longer pending
        assert not await get_held_proposals(repo, season_id)

    async def test_approved_effects_survive_registry_reload(
        self, repo: Repository, season_id: str, team_id: str, gov_a: str
    ):
        proposal, vote = await _passed_proposal_setup(
            repo, season_id, team_id, gov_a, with_effects=True
        )
        await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={proposal.id: [vote]},
            current_ruleset=RuleSet(),
            round_number=1,
            effect_registry=EffectRegistry(),
            effects_v2_by_proposal={
                proposal.id: [
                    EffectSpec(
                        effect_type="hook_callback",
                        hook_point="sim.shot.pre",
                        action_code={"type": "modify_probability", "modifier": 0.05},
                        description="Hot hand",
                    )
                ]
            },
            require_approval=True,
        )
        held = await get_held_proposals(repo, season_id)
        await approve_held_proposal(
            repo=repo,
            season_id=season_id,
            held_payload=held[0],
            effect_registry=await load_effect_registry(repo, season_id),
        )
        # A fresh load (next round) sees the effect
        reloaded = await load_effect_registry(repo, season_id)
        assert reloaded.get_effects_for_proposal(proposal.id)


class TestRejectHeld:
    async def test_reject_refunds_and_never_enacts(
        self, repo: Repository, season_id: str, team_id: str, gov_a: str
    ):
        proposal, vote = await _passed_proposal_setup(repo, season_id, team_id, gov_a)
        await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={proposal.id: [vote]},
            current_ruleset=RuleSet(),
            round_number=1,
            effect_registry=EffectRegistry(),
            require_approval=True,
        )
        held = await get_held_proposals(repo, season_id)
        balance_before = await get_token_balance(repo, gov_a, season_id)

        await reject_held_proposal(
            repo=repo,
            season_id=season_id,
            held_payload=held[0],
            reason="not tonight",
        )

        balance_after = await get_token_balance(repo, gov_a, season_id)
        assert balance_after.propose == balance_before.propose + proposal.token_cost
        assert not await get_held_proposals(repo, season_id)
        enacted = await repo.get_events_by_type(
            season_id=season_id, event_types=["rule.enacted"]
        )
        assert not enacted
        season = await repo.get_season(season_id)
        assert season.current_ruleset["three_point_value"] == (
            RuleSet().three_point_value
        )
