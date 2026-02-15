"""Tests for the proposal amendment flow.

Covers: amend_proposal(), count_amendments(), amendment cap enforcement,
AMEND token deduction, amended proposal tally behavior, and authorship rules.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.ai.interpreter import interpret_proposal_mock
from pinwheel.core.governance import (
    MAX_AMENDMENTS_PER_PROPOSAL,
    amend_proposal,
    cast_vote,
    confirm_proposal,
    count_amendments,
    submit_proposal,
    tally_governance,
)
from pinwheel.core.tokens import get_token_balance, regenerate_tokens
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.governance import RuleInterpretation
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


@pytest.fixture
async def team_id(repo: Repository, season_id: str) -> str:
    team = await repo.create_team(season_id=season_id, name="Test Team")
    return team.id


@pytest.fixture
async def gov_a(repo: Repository, team_id: str, season_id: str) -> str:
    """Governor A with tokens. Returns governor_id."""
    gov_id = "gov-a"
    await regenerate_tokens(repo, gov_id, team_id, season_id)
    return gov_id


@pytest.fixture
async def gov_b(repo: Repository, team_id: str, season_id: str) -> str:
    """Governor B with tokens. Returns governor_id."""
    gov_id = "gov-b"
    await regenerate_tokens(repo, gov_id, team_id, season_id)
    return gov_id


@pytest.fixture
async def gov_c(repo: Repository, team_id: str, season_id: str) -> str:
    """Governor C with tokens. Returns governor_id."""
    gov_id = "gov-c"
    await regenerate_tokens(repo, gov_id, team_id, season_id)
    return gov_id


@pytest.fixture
async def confirmed_proposal(
    repo: Repository,
    season_id: str,
    team_id: str,
    gov_a: str,
):
    """A confirmed proposal submitted by gov_a."""
    interpretation = interpret_proposal_mock("Make three pointers worth 5", RuleSet())
    proposal = await submit_proposal(
        repo=repo,
        governor_id=gov_a,
        team_id=team_id,
        season_id=season_id,
        window_id="w-1",
        raw_text="Make three pointers worth 5",
        interpretation=interpretation,
        ruleset=RuleSet(),
    )
    proposal = await confirm_proposal(repo, proposal)
    assert proposal.status == "confirmed"
    return proposal


# --- count_amendments Tests ---


class TestCountAmendments:
    async def test_zero_amendments(
        self,
        repo: Repository,
        season_id: str,
        confirmed_proposal,
    ):
        """A fresh proposal has zero amendments."""
        count = await count_amendments(repo, confirmed_proposal.id, season_id)
        assert count == 0

    async def test_one_amendment(
        self,
        repo: Repository,
        season_id: str,
        team_id: str,
        gov_b: str,
        confirmed_proposal,
    ):
        """After one amendment, count returns 1."""
        new_interp = RuleInterpretation(
            parameter="three_point_value",
            new_value=4,
            old_value=3,
            confidence=0.9,
        )
        await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_b,
            team_id=team_id,
            amendment_text="Change value to 4 instead",
            new_interpretation=new_interp,
        )
        count = await count_amendments(repo, confirmed_proposal.id, season_id)
        assert count == 1

    async def test_two_amendments(
        self,
        repo: Repository,
        season_id: str,
        team_id: str,
        gov_b: str,
        gov_c: str,
        confirmed_proposal,
    ):
        """After two amendments, count returns 2."""
        interp1 = RuleInterpretation(
            parameter="three_point_value", new_value=4, old_value=3, confidence=0.9
        )
        await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_b,
            team_id=team_id,
            amendment_text="Change value to 4",
            new_interpretation=interp1,
        )

        interp2 = RuleInterpretation(
            parameter="three_point_value", new_value=6, old_value=4, confidence=0.9
        )
        await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_c,
            team_id=team_id,
            amendment_text="Change value to 6",
            new_interpretation=interp2,
        )

        count = await count_amendments(repo, confirmed_proposal.id, season_id)
        assert count == 2


# --- amend_proposal Tests ---


class TestAmendProposal:
    async def test_amend_creates_event(
        self,
        repo: Repository,
        season_id: str,
        team_id: str,
        gov_b: str,
        confirmed_proposal,
    ):
        """amend_proposal creates a proposal.amended event."""
        new_interp = RuleInterpretation(
            parameter="three_point_value", new_value=4, old_value=3, confidence=0.9
        )
        amendment = await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_b,
            team_id=team_id,
            amendment_text="Change to 4",
            new_interpretation=new_interp,
        )

        assert amendment.proposal_id == confirmed_proposal.id
        assert amendment.governor_id == gov_b
        assert amendment.amendment_text == "Change to 4"

        # Verify event was written
        events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.amended"],
        )
        assert len(events) == 1
        assert events[0].aggregate_id == confirmed_proposal.id

    async def test_amend_updates_interpretation(
        self,
        repo: Repository,
        season_id: str,
        team_id: str,
        gov_b: str,
        confirmed_proposal,
    ):
        """amend_proposal updates the proposal's interpretation."""
        original_param = confirmed_proposal.interpretation.parameter
        assert original_param == "three_point_value"
        original_value = confirmed_proposal.interpretation.new_value
        assert original_value == 5

        new_interp = RuleInterpretation(
            parameter="three_point_value", new_value=4, old_value=3, confidence=0.9
        )
        await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_b,
            team_id=team_id,
            amendment_text="Change to 4",
            new_interpretation=new_interp,
        )

        # Proposal interpretation should now reflect the amendment
        assert confirmed_proposal.interpretation.new_value == 4
        assert confirmed_proposal.status == "amended"

    async def test_amend_sets_status_to_amended(
        self,
        repo: Repository,
        season_id: str,
        team_id: str,
        gov_b: str,
        confirmed_proposal,
    ):
        """amend_proposal sets proposal status to 'amended'."""
        assert confirmed_proposal.status == "confirmed"

        new_interp = RuleInterpretation(
            parameter="three_point_value", new_value=4, old_value=3, confidence=0.9
        )
        await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_b,
            team_id=team_id,
            amendment_text="Change to 4",
            new_interpretation=new_interp,
        )

        assert confirmed_proposal.status == "amended"


# --- AMEND Token Tests ---


class TestAmendTokenDeduction:
    async def test_amend_deducts_token(
        self,
        repo: Repository,
        season_id: str,
        team_id: str,
        gov_b: str,
        confirmed_proposal,
    ):
        """Amending a proposal deducts 1 AMEND token."""
        balance_before = await get_token_balance(repo, gov_b, season_id)
        assert balance_before.amend == 2  # From regeneration

        new_interp = RuleInterpretation(
            parameter="three_point_value", new_value=4, old_value=3, confidence=0.9
        )
        await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_b,
            team_id=team_id,
            amendment_text="Change to 4",
            new_interpretation=new_interp,
        )

        balance_after = await get_token_balance(repo, gov_b, season_id)
        assert balance_after.amend == 1  # Deducted from 2

    async def test_two_amendments_cost_two_tokens(
        self,
        repo: Repository,
        season_id: str,
        team_id: str,
        gov_b: str,
        gov_c: str,
        confirmed_proposal,
    ):
        """Two amendments by different governors each cost 1 AMEND token."""
        interp1 = RuleInterpretation(
            parameter="three_point_value", new_value=4, old_value=3, confidence=0.9
        )
        await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_b,
            team_id=team_id,
            amendment_text="Change to 4",
            new_interpretation=interp1,
        )

        interp2 = RuleInterpretation(
            parameter="three_point_value", new_value=6, old_value=4, confidence=0.9
        )
        await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_c,
            team_id=team_id,
            amendment_text="Change to 6",
            new_interpretation=interp2,
        )

        b_balance = await get_token_balance(repo, gov_b, season_id)
        c_balance = await get_token_balance(repo, gov_c, season_id)
        assert b_balance.amend == 1
        assert c_balance.amend == 1


# --- Amendment Cap Tests ---


class TestAmendmentCap:
    async def test_max_amendments_constant(self):
        """MAX_AMENDMENTS_PER_PROPOSAL is 2."""
        assert MAX_AMENDMENTS_PER_PROPOSAL == 2

    async def test_cap_reached_after_max(
        self,
        repo: Repository,
        season_id: str,
        team_id: str,
        gov_b: str,
        gov_c: str,
        confirmed_proposal,
    ):
        """After MAX_AMENDMENTS_PER_PROPOSAL amendments, count equals the cap."""
        for i, gov_id in enumerate([gov_b, gov_c]):
            interp = RuleInterpretation(
                parameter="three_point_value",
                new_value=4 + i,
                old_value=3 + i,
                confidence=0.9,
            )
            await amend_proposal(
                repo=repo,
                proposal=confirmed_proposal,
                governor_id=gov_id,
                team_id=team_id,
                amendment_text=f"Amendment {i + 1}",
                new_interpretation=interp,
            )

        count = await count_amendments(repo, confirmed_proposal.id, season_id)
        assert count == MAX_AMENDMENTS_PER_PROPOSAL


# --- Amended Proposal Tally Tests ---


class TestAmendedProposalTally:
    async def test_amended_proposal_uses_new_interpretation_in_tally(
        self,
        repo: Repository,
        season_id: str,
        team_id: str,
        gov_a: str,
        gov_b: str,
        gov_c: str,
        confirmed_proposal,
    ):
        """An amended proposal enacts the amended interpretation, not the original."""
        # Original interpretation: three_point_value = 5
        assert confirmed_proposal.interpretation.parameter == "three_point_value"
        assert confirmed_proposal.interpretation.new_value == 5

        # Amend to change value to 4
        new_interp = RuleInterpretation(
            parameter="three_point_value", new_value=4, old_value=3, confidence=0.9
        )
        await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_b,
            team_id=team_id,
            amendment_text="Change to 4 instead of 5",
            new_interpretation=new_interp,
        )

        # Vote yes from multiple governors
        vote1 = await cast_vote(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_a,
            team_id=team_id,
            vote_choice="yes",
            weight=1.0,
        )
        vote2 = await cast_vote(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_c,
            team_id=team_id,
            vote_choice="yes",
            weight=1.0,
        )

        # Tally
        new_ruleset, tallies = await tally_governance(
            repo=repo,
            season_id=season_id,
            proposals=[confirmed_proposal],
            votes_by_proposal={confirmed_proposal.id: [vote1, vote2]},
            current_ruleset=RuleSet(),
            round_number=1,
        )

        assert len(tallies) == 1
        assert tallies[0].passed is True
        # The amended value (4) should be enacted, not the original (5)
        assert new_ruleset.three_point_value == 4

    async def test_amended_proposal_is_eligible_for_tally(
        self,
        repo: Repository,
        season_id: str,
        team_id: str,
        gov_a: str,
        gov_b: str,
        confirmed_proposal,
    ):
        """Proposals with status 'amended' are eligible for tally."""
        new_interp = RuleInterpretation(
            parameter="three_point_value", new_value=4, old_value=3, confidence=0.9
        )
        await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_b,
            team_id=team_id,
            amendment_text="Change to 4",
            new_interpretation=new_interp,
        )
        assert confirmed_proposal.status == "amended"

        vote1 = await cast_vote(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_a,
            team_id=team_id,
            vote_choice="no",
            weight=1.0,
        )

        new_ruleset, tallies = await tally_governance(
            repo=repo,
            season_id=season_id,
            proposals=[confirmed_proposal],
            votes_by_proposal={confirmed_proposal.id: [vote1]},
            current_ruleset=RuleSet(),
            round_number=1,
        )

        # Should be tallied (even if it fails)
        assert len(tallies) == 1
        assert tallies[0].passed is False
        assert new_ruleset.three_point_value == 3  # Unchanged


# --- Self-Amendment Tests ---


class TestSelfAmendment:
    async def test_proposer_can_amend_own_proposal(
        self,
        repo: Repository,
        season_id: str,
        team_id: str,
        gov_a: str,
        confirmed_proposal,
    ):
        """The original proposer can amend their own proposal.

        Per the user spec: "Test self-amendment (proposer can amend their own)".
        """
        new_interp = RuleInterpretation(
            parameter="three_point_value", new_value=4, old_value=3, confidence=0.9
        )
        amendment = await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_a,
            team_id=team_id,
            amendment_text="Actually, make it 4",
            new_interpretation=new_interp,
        )
        assert amendment.governor_id == gov_a
        assert amendment.proposal_id == confirmed_proposal.id


# --- Non-Proposer Amendment Tests ---


class TestNonProposerAmendment:
    async def test_other_governor_can_amend(
        self,
        repo: Repository,
        season_id: str,
        team_id: str,
        gov_b: str,
        confirmed_proposal,
    ):
        """A governor who did not propose can amend the proposal.

        Per the user spec: "Test non-proposer amendment (other governors can also amend)".
        """
        # confirmed_proposal was submitted by gov_a
        assert confirmed_proposal.governor_id != gov_b

        new_interp = RuleInterpretation(
            parameter="three_point_value", new_value=4, old_value=3, confidence=0.9
        )
        amendment = await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_b,
            team_id=team_id,
            amendment_text="I think 4 is better",
            new_interpretation=new_interp,
        )
        assert amendment.governor_id == gov_b
        assert confirmed_proposal.interpretation.new_value == 4


# --- Multiple Amendments on Same Proposal ---


class TestMultipleAmendments:
    async def test_final_interpretation_wins(
        self,
        repo: Repository,
        season_id: str,
        team_id: str,
        gov_a: str,
        gov_b: str,
        gov_c: str,
        confirmed_proposal,
    ):
        """When a proposal is amended multiple times, the last amendment's
        interpretation is used for tally."""
        # First amendment: value = 4
        interp1 = RuleInterpretation(
            parameter="three_point_value", new_value=4, old_value=3, confidence=0.9
        )
        await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_b,
            team_id=team_id,
            amendment_text="Change to 4",
            new_interpretation=interp1,
        )
        assert confirmed_proposal.interpretation.new_value == 4

        # Second amendment: value = 6
        interp2 = RuleInterpretation(
            parameter="three_point_value", new_value=6, old_value=4, confidence=0.9
        )
        await amend_proposal(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_c,
            team_id=team_id,
            amendment_text="Change to 6",
            new_interpretation=interp2,
        )
        assert confirmed_proposal.interpretation.new_value == 6

        # Vote and tally
        vote1 = await cast_vote(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_a,
            team_id=team_id,
            vote_choice="yes",
            weight=1.0,
        )
        vote2 = await cast_vote(
            repo=repo,
            proposal=confirmed_proposal,
            governor_id=gov_b,
            team_id=team_id,
            vote_choice="yes",
            weight=1.0,
        )

        new_ruleset, tallies = await tally_governance(
            repo=repo,
            season_id=season_id,
            proposals=[confirmed_proposal],
            votes_by_proposal={confirmed_proposal.id: [vote1, vote2]},
            current_ruleset=RuleSet(),
            round_number=1,
        )

        assert tallies[0].passed is True
        # The last amendment (6) should be enacted
        assert new_ruleset.three_point_value == 6
