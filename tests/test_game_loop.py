"""Tests for the game loop — the autonomous round cycle."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.ai.interpreter import interpret_proposal_mock
from pinwheel.core.event_bus import EventBus
from pinwheel.core.game_loop import step_round
from pinwheel.core.governance import cast_vote, confirm_proposal, submit_proposal
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.core.tokens import get_token_balance, regenerate_tokens
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
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


def _hooper_attrs() -> dict:
    return {
        "scoring": 50,
        "passing": 40,
        "defense": 35,
        "speed": 45,
        "stamina": 40,
        "iq": 50,
        "ego": 30,
        "chaotic_alignment": 40,
        "fate": 30,
    }


async def _setup_season_with_teams(repo: Repository) -> tuple[str, list[str]]:
    """Create a league, season, 4 teams with 3 hoopers each, and a schedule."""
    league = await repo.create_league("Test League")
    season = await repo.create_season(
        league.id,
        "Season 1",
        starting_ruleset={"quarter_minutes": 3},
    )

    team_ids = []
    for i in range(4):
        team = await repo.create_team(
            season.id,
            f"Team {i + 1}",
            venue={"name": f"Arena {i + 1}", "capacity": 5000},
        )
        team_ids.append(team.id)
        for j in range(3):
            await repo.create_hooper(
                team_id=team.id,
                season_id=season.id,
                name=f"Hooper-{i + 1}-{j + 1}",
                archetype="sharpshooter",
                attributes=_hooper_attrs(),
            )

    # Generate round-robin schedule and store
    matchups = generate_round_robin(team_ids)
    for m in matchups:
        await repo.create_schedule_entry(
            season_id=season.id,
            round_number=m.round_number,
            matchup_index=m.matchup_index,
            home_team_id=m.home_team_id,
            away_team_id=m.away_team_id,
        )

    return season.id, team_ids


class TestStepRound:
    async def test_simulates_games(self, repo: Repository):
        season_id, team_ids = await _setup_season_with_teams(repo)

        result = await step_round(repo, season_id, round_number=1)

        assert result.round_number == 1
        assert len(result.games) == 2  # 4 teams → 2 games per round
        for game in result.games:
            assert game["home_score"] > 0 or game["away_score"] > 0
            assert game["winner_team_id"] in team_ids

    async def test_stores_game_results(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)
        await step_round(repo, season_id, round_number=1)

        games = await repo.get_games_for_round(season_id, 1)
        assert len(games) == 2
        for g in games:
            assert g.home_score >= 0
            assert g.away_score >= 0

    async def test_generates_simulation_mirror(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)
        result = await step_round(repo, season_id, round_number=1)

        sim_mirrors = [m for m in result.mirrors if m.mirror_type == "simulation"]
        assert len(sim_mirrors) == 1
        assert len(sim_mirrors[0].content) > 0

    async def test_generates_governance_mirror(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)
        result = await step_round(repo, season_id, round_number=1)

        gov_mirrors = [m for m in result.mirrors if m.mirror_type == "governance"]
        assert len(gov_mirrors) == 1

    async def test_stores_mirrors_in_db(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)
        await step_round(repo, season_id, round_number=1)

        mirrors = await repo.get_mirrors_for_round(season_id, 1)
        assert len(mirrors) >= 2  # sim + gov at minimum

    async def test_publishes_events_to_bus(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)
        bus = EventBus()
        received = []

        async with bus.subscribe(None) as sub:
            await step_round(repo, season_id, round_number=1, event_bus=bus)
            # Drain all events
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]
        assert "game.completed" in event_types
        assert "mirror.generated" in event_types
        assert "round.completed" in event_types

    async def test_empty_round(self, repo: Repository):
        """Round with no scheduled games should not crash."""
        league = await repo.create_league("Empty")
        season = await repo.create_season(league.id, "Empty Season")

        result = await step_round(repo, season.id, round_number=99)
        assert result.games == []
        assert result.mirrors == []

    async def test_bad_season_id(self, repo: Repository):
        with pytest.raises(ValueError, match="not found"):
            await step_round(repo, "nonexistent", round_number=1)


class TestMultipleRounds:
    async def test_two_consecutive_rounds(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)

        r1 = await step_round(repo, season_id, round_number=1)
        r2 = await step_round(repo, season_id, round_number=2)

        assert r1.round_number == 1
        assert r2.round_number == 2
        assert len(r1.games) == 2
        assert len(r2.games) == 2

        # Different rounds should have different games
        r1_games = await repo.get_games_for_round(season_id, 1)
        r2_games = await repo.get_games_for_round(season_id, 2)
        assert len(r1_games) == 2
        assert len(r2_games) == 2

    async def test_mirrors_stored_per_round(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)

        await step_round(repo, season_id, round_number=1)
        await step_round(repo, season_id, round_number=2)

        m1 = await repo.get_mirrors_for_round(season_id, 1)
        m2 = await repo.get_mirrors_for_round(season_id, 2)
        assert len(m1) >= 2
        assert len(m2) >= 2

        latest = await repo.get_latest_mirror(season_id, "simulation")
        assert latest is not None
        assert latest.round_number == 2


class TestGovernanceInterval:
    async def _submit_and_confirm_proposal(
        self, repo: Repository, season_id: str, team_id: str,
    ) -> str:
        """Submit and confirm a proposal, returning the proposal_id."""
        gov_id = "gov-interval-test"
        await regenerate_tokens(repo, gov_id, team_id, season_id)
        interpretation = interpret_proposal_mock("Make three pointers worth 5", RuleSet())
        proposal = await submit_proposal(
            repo=repo, governor_id=gov_id, team_id=team_id, season_id=season_id,
            window_id="", raw_text="Make three pointers worth 5",
            interpretation=interpretation, ruleset=RuleSet(),
        )
        proposal = await confirm_proposal(repo, proposal)

        # Cast a yes vote so it passes
        await cast_vote(
            repo=repo, proposal=proposal, governor_id=gov_id,
            team_id=team_id, vote_choice="yes", weight=1.0,
        )
        return proposal.id

    async def test_tallies_on_interval_round(self, repo: Repository):
        """Governance tallies on round 3 with interval=3."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # Submit a proposal before round 3
        await self._submit_and_confirm_proposal(
            repo, season_id, team_ids[0],
        )

        # Round 3 should tally (interval=3, 3 % 3 == 0)
        result = await step_round(
            repo, season_id, round_number=3, governance_interval=3,
        )

        assert len(result.tallies) == 1
        assert result.tallies[0].passed is True
        assert result.governance_summary is not None
        assert result.governance_summary["proposals_count"] == 1

    async def test_skips_non_interval_round(self, repo: Repository):
        """Governance does NOT tally on round 1 with interval=3."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        await self._submit_and_confirm_proposal(
            repo, season_id, team_ids[0],
        )

        # Round 1 should NOT tally (1 % 3 != 0)
        result = await step_round(
            repo, season_id, round_number=1, governance_interval=3,
        )

        assert len(result.tallies) == 0
        assert result.governance_summary is None

    async def test_interval_1_tallies_every_round(self, repo: Repository):
        """With interval=1, governance tallies every round."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        await self._submit_and_confirm_proposal(
            repo, season_id, team_ids[0],
        )

        result = await step_round(
            repo, season_id, round_number=1, governance_interval=1,
        )

        assert len(result.tallies) == 1
        assert result.governance_summary is not None

    async def test_resolved_proposals_not_retallied(self, repo: Repository):
        """Proposals resolved in round 1 are not tallied again in round 2."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        await self._submit_and_confirm_proposal(
            repo, season_id, team_ids[0],
        )

        # Round 1 tallies the proposal (interval=1)
        r1 = await step_round(
            repo, season_id, round_number=1, governance_interval=1,
        )
        assert len(r1.tallies) == 1

        # Round 2 should have nothing to tally (proposal already resolved)
        r2 = await step_round(
            repo, season_id, round_number=2, governance_interval=1,
        )
        assert len(r2.tallies) == 0
        # governance_summary still produced (with 0 proposals)
        assert r2.governance_summary is not None
        assert r2.governance_summary["proposals_count"] == 0

    async def test_no_governance_event_in_step_round(self, repo: Repository):
        """step_round should NOT publish governance.window_closed (moved to scheduler)."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        await self._submit_and_confirm_proposal(
            repo, season_id, team_ids[0],
        )

        bus = EventBus()
        received = []

        async with bus.subscribe(None) as sub:
            await step_round(
                repo, season_id, round_number=3,
                governance_interval=3, event_bus=bus,
            )
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]
        assert "governance.window_closed" not in event_types

    async def test_tokens_regenerated_on_governance_tally(self, repo: Repository):
        """Enrolled governors receive token regeneration when governance tallies."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # Enroll a governor on a team
        player = await repo.get_or_create_player(
            discord_id="discord-regen-test", username="Regen Tester",
        )
        await repo.enroll_player(player.id, team_ids[0], season_id)

        # Check starting balance (should be 0 — no tokens yet)
        balance_before = await get_token_balance(repo, player.id, season_id)
        assert balance_before.propose == 0

        # Run round 3 with interval=3 — should trigger governance + token regen
        await step_round(
            repo, season_id, round_number=3, governance_interval=3,
        )

        # Governor should have received tokens
        balance_after = await get_token_balance(repo, player.id, season_id)
        assert balance_after.propose == 2
        assert balance_after.amend == 2
        assert balance_after.boost == 2

    async def test_tokens_not_regenerated_on_non_tally_round(self, repo: Repository):
        """Tokens are NOT regenerated on non-governance rounds."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        player = await repo.get_or_create_player(
            discord_id="discord-no-regen", username="No Regen",
        )
        await repo.enroll_player(player.id, team_ids[0], season_id)

        # Run round 1 with interval=3 — should NOT trigger governance
        await step_round(
            repo, season_id, round_number=1, governance_interval=3,
        )

        balance = await get_token_balance(repo, player.id, season_id)
        assert balance.propose == 0
        assert balance.amend == 0
        assert balance.boost == 0
