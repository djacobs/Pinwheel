"""Tests for the game loop — the autonomous round cycle."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.ai.interpreter import interpret_proposal_mock
from pinwheel.core.event_bus import EventBus
from pinwheel.core.game_loop import (
    _AIPhaseResult,
    _check_all_playoffs_complete,
    _check_season_complete,
    _phase_ai,
    _phase_persist_and_finalize,
    _phase_simulate_and_govern,
    _SimPhaseResult,
    compute_standings_from_repo,
    generate_playoff_bracket,
    step_round,
    step_round_multisession,
)
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


async def _setup_season_with_teams(
    repo: Repository, num_rounds: int = 1
) -> tuple[str, list[str]]:
    """Create a league, season, 4 teams with 4 hoopers each, and a schedule.

    Args:
        repo: Repository instance.
        num_rounds: Number of complete round-robins to schedule (default 1).
            With 4 teams, each round-robin cycle has C(4,2)=6 games across 3 rounds (2 games/round).
    """
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
        for j in range(4):
            await repo.create_hooper(
                team_id=team.id,
                season_id=season.id,
                name=f"Hooper-{i + 1}-{j + 1}",
                archetype="sharpshooter",
                attributes=_hooper_attrs(),
            )

    # Generate round-robin schedule and store
    matchups = generate_round_robin(team_ids, num_rounds=num_rounds)
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
        assert len(result.games) == 2  # 4 teams → 2 games in round 1 (circle method)
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

    async def test_generates_simulation_report(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)
        result = await step_round(repo, season_id, round_number=1)

        sim_reports = [m for m in result.reports if m.report_type == "simulation"]
        assert len(sim_reports) == 1
        assert len(sim_reports[0].content) > 0

    async def test_generates_governance_report(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)
        result = await step_round(repo, season_id, round_number=1)

        gov_reports = [m for m in result.reports if m.report_type == "governance"]
        assert len(gov_reports) == 1

    async def test_stores_reports_in_db(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)
        await step_round(repo, season_id, round_number=1)

        reports = await repo.get_reports_for_round(season_id, 1)
        assert len(reports) >= 2  # sim + gov at minimum

    async def test_publishes_events_to_bus(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)
        bus = EventBus()
        received = []

        async with bus.subscribe(None) as sub:
            result = await step_round(repo, season_id, round_number=1, event_bus=bus)
            # Drain all events
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]
        assert "game.completed" in event_types
        assert "round.completed" in event_types

        # report.generated events are now deferred (returned in RoundResult)
        assert len(result.report_events) >= 2  # sim + gov at minimum
        report_types = [e["report_type"] for e in result.report_events]
        assert "simulation" in report_types
        assert "governance" in report_types

    async def test_suppress_spoiler_events(self, repo: Repository):
        """When suppress_spoiler_events=True, game.completed and round.completed
        are not published to the bus, but report events are still collected."""
        season_id, _ = await _setup_season_with_teams(repo)
        bus = EventBus()
        received = []

        async with bus.subscribe(None) as sub:
            result = await step_round(
                repo,
                season_id,
                round_number=1,
                event_bus=bus,
                suppress_spoiler_events=True,
            )
            # Drain all events
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]
        assert "game.completed" not in event_types
        assert "round.completed" not in event_types

        # Report events still collected in result
        assert len(result.report_events) >= 2

    async def test_empty_round(self, repo: Repository):
        """Round with no scheduled games should not crash."""
        league = await repo.create_league("Empty")
        season = await repo.create_season(league.id, "Empty Season")

        result = await step_round(repo, season.id, round_number=99)
        assert result.games == []
        assert result.reports == []

    async def test_bad_season_id(self, repo: Repository):
        with pytest.raises(ValueError, match="not found"):
            await step_round(repo, "nonexistent", round_number=1)


class TestMultipleRounds:
    async def test_two_consecutive_rounds(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo, num_rounds=1)

        r1 = await step_round(repo, season_id, round_number=1)
        r2 = await step_round(repo, season_id, round_number=2)

        assert r1.round_number == 1
        assert r2.round_number == 2
        assert len(r1.games) == 2  # 4 teams → 2 games in round 1 (circle method)
        assert len(r2.games) == 2  # 4 teams → 2 games in round 2 (circle method)

        # Different rounds should have different games
        r1_games = await repo.get_games_for_round(season_id, 1)
        r2_games = await repo.get_games_for_round(season_id, 2)
        assert len(r1_games) == 2
        assert len(r2_games) == 2

    async def test_reports_stored_per_round(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo, num_rounds=2)

        await step_round(repo, season_id, round_number=1)
        await step_round(repo, season_id, round_number=2)

        m1 = await repo.get_reports_for_round(season_id, 1)
        m2 = await repo.get_reports_for_round(season_id, 2)
        assert len(m1) >= 2
        assert len(m2) >= 2

        latest = await repo.get_latest_report(season_id, "simulation")
        assert latest is not None
        assert latest.round_number == 2


class TestGovernanceInterval:
    async def _submit_and_confirm_proposal(
        self,
        repo: Repository,
        season_id: str,
        team_id: str,
    ) -> str:
        """Submit and confirm a proposal, returning the proposal_id."""
        gov_id = "gov-interval-test"
        await regenerate_tokens(repo, gov_id, team_id, season_id)
        interpretation = interpret_proposal_mock("Make three pointers worth 5", RuleSet())
        proposal = await submit_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_id,
            season_id=season_id,
            window_id="",
            raw_text="Make three pointers worth 5",
            interpretation=interpretation,
            ruleset=RuleSet(),
        )
        proposal = await confirm_proposal(repo, proposal)

        # Cast a yes vote so it passes
        await cast_vote(
            repo=repo,
            proposal=proposal,
            governor_id=gov_id,
            team_id=team_id,
            vote_choice="yes",
            weight=1.0,
        )
        return proposal.id

    async def test_tallies_on_interval_round(self, repo: Repository):
        """Governance tallies on round 3 with interval=3."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)

        # Submit a proposal before round 3
        await self._submit_and_confirm_proposal(
            repo,
            season_id,
            team_ids[0],
        )

        # Round 3 should tally (interval=3, 3 % 3 == 0)
        result = await step_round(
            repo,
            season_id,
            round_number=3,
            governance_interval=3,
        )

        assert len(result.tallies) == 1
        assert result.tallies[0].passed is True
        assert result.governance_summary is not None
        assert result.governance_summary["proposals_count"] == 1

    async def test_skips_non_interval_round(self, repo: Repository):
        """Governance does NOT tally on round 1 with interval=3."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)

        await self._submit_and_confirm_proposal(
            repo,
            season_id,
            team_ids[0],
        )

        # Round 1 should NOT tally (1 % 3 != 0)
        result = await step_round(
            repo,
            season_id,
            round_number=1,
            governance_interval=3,
        )

        assert len(result.tallies) == 0
        assert result.governance_summary is None

    async def test_interval_1_tallies_every_round(self, repo: Repository):
        """With interval=1, governance tallies every round."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        await self._submit_and_confirm_proposal(
            repo,
            season_id,
            team_ids[0],
        )

        result = await step_round(
            repo,
            season_id,
            round_number=1,
            governance_interval=1,
        )

        assert len(result.tallies) == 1
        assert result.governance_summary is not None

    async def test_resolved_proposals_not_retallied(self, repo: Repository):
        """Proposals resolved in round 1 are not tallied again in round 2."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=2)

        await self._submit_and_confirm_proposal(
            repo,
            season_id,
            team_ids[0],
        )

        # Round 1 tallies the proposal (interval=1)
        r1 = await step_round(
            repo,
            season_id,
            round_number=1,
            governance_interval=1,
        )
        assert len(r1.tallies) == 1

        # Round 2 should have nothing to tally (proposal already resolved)
        r2 = await step_round(
            repo,
            season_id,
            round_number=2,
            governance_interval=1,
        )
        assert len(r2.tallies) == 0
        # governance_summary still produced (with 0 proposals)
        assert r2.governance_summary is not None
        assert r2.governance_summary["proposals_count"] == 0

    async def test_no_governance_event_in_step_round(self, repo: Repository):
        """step_round should NOT publish governance.window_closed (moved to scheduler)."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)

        await self._submit_and_confirm_proposal(
            repo,
            season_id,
            team_ids[0],
        )

        bus = EventBus()
        received = []

        async with bus.subscribe(None) as sub:
            await step_round(
                repo,
                season_id,
                round_number=3,
                governance_interval=3,
                event_bus=bus,
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
            discord_id="discord-regen-test",
            username="Regen Tester",
        )
        await repo.enroll_player(player.id, team_ids[0], season_id)

        # Check starting balance (should be 0 — no tokens yet)
        balance_before = await get_token_balance(repo, player.id, season_id)
        assert balance_before.propose == 0

        # Run round 1 with interval=1 — should trigger governance + token regen
        await step_round(
            repo,
            season_id,
            round_number=1,
            governance_interval=1,
        )

        # Governor should have received tokens
        balance_after = await get_token_balance(repo, player.id, season_id)
        assert balance_after.propose == 2
        assert balance_after.amend == 2
        assert balance_after.boost == 0  # BOOST does not regenerate at tally

    async def test_tokens_not_regenerated_on_non_tally_round(self, repo: Repository):
        """Tokens are NOT regenerated on non-governance rounds."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)

        player = await repo.get_or_create_player(
            discord_id="discord-no-regen",
            username="No Regen",
        )
        await repo.enroll_player(player.id, team_ids[0], season_id)

        # Run round 1 with interval=3 — should NOT trigger governance
        await step_round(
            repo,
            season_id,
            round_number=1,
            governance_interval=3,
        )

        balance = await get_token_balance(repo, player.id, season_id)
        assert balance.propose == 0
        assert balance.amend == 0
        assert balance.boost == 0


class TestSeasonEndDetection:
    """Tests for detecting when the regular season is complete."""

    async def test_season_detected_complete_when_all_rounds_played(self, repo: Repository):
        """Season is detected as complete when all scheduled rounds have been played."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        # 4 teams, 1 round-robin cycle => 3 rounds (2 games each = 6 total)
        matchups = generate_round_robin(team_ids)
        total_rounds = max(m.round_number for m in matchups)
        assert total_rounds == 3

        # Play all rounds
        for rnd in range(1, total_rounds + 1):
            await step_round(repo, season_id, round_number=rnd)

        is_complete = await _check_season_complete(repo, season_id)
        assert is_complete is True

    async def test_season_not_complete_when_rounds_remain(self, repo: Repository):
        """Season is NOT detected as complete when scheduled rounds have not been played."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)

        # Only play round 1 of 3
        await step_round(repo, season_id, round_number=1)

        is_complete = await _check_season_complete(repo, season_id)
        assert is_complete is False

    async def test_season_not_complete_with_no_schedule(self, repo: Repository):
        """Returns False when no schedule exists."""
        league = await repo.create_league("Empty League")
        season = await repo.create_season(league.id, "Empty Season")

        is_complete = await _check_season_complete(repo, season.id)
        assert is_complete is False

    async def test_step_round_sets_status_on_final_round(self, repo: Repository):
        """step_round updates season to playoffs (via tiebreaker check) on final round."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)
        # Set season status to active first
        await repo.update_season_status(season_id, "active")

        matchups = generate_round_robin(team_ids, num_rounds=3)
        total_rounds = max(m.round_number for m in matchups)
        assert total_rounds == 9

        # Play all rounds
        for rnd in range(1, total_rounds + 1):
            result = await step_round(repo, season_id, round_number=rnd)

        # The last round should detect season completion
        assert result.season_complete is True
        assert result.final_standings is not None
        assert len(result.final_standings) == 4

        # Season status in DB should be updated.
        # New lifecycle: ACTIVE -> TIEBREAKER_CHECK -> PLAYOFFS (or TIEBREAKERS)
        season = await repo.get_season(season_id)
        assert season.status in ("playoffs", "tiebreakers")

    async def test_step_round_does_not_set_status_mid_season(self, repo: Repository):
        """step_round does NOT set season_complete mid-season."""
        season_id, _ = await _setup_season_with_teams(repo, num_rounds=3)
        await repo.update_season_status(season_id, "active")

        result = await step_round(repo, season_id, round_number=1)

        assert result.season_complete is False
        assert result.final_standings is None

    async def test_season_complete_event_published(self, repo: Repository):
        """season.regular_season_complete event is published on final round."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)
        await repo.update_season_status(season_id, "active")

        matchups = generate_round_robin(team_ids, num_rounds=3)
        total_rounds = max(m.round_number for m in matchups)

        bus = EventBus()
        received = []

        async with bus.subscribe(None) as sub:
            for rnd in range(1, total_rounds + 1):
                await step_round(repo, season_id, round_number=rnd, event_bus=bus)
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]
        assert "season.regular_season_complete" in event_types

        # Check the event data
        season_event = [e for e in received if e["type"] == "season.regular_season_complete"][0]
        assert season_event["data"]["season_id"] == season_id
        assert season_event["data"]["standings"] is not None
        assert len(season_event["data"]["standings"]) == 4


class TestComputeStandings:
    """Tests for standings computation from repository data."""

    async def test_standings_after_one_round(self, repo: Repository):
        """Standings are computed correctly after a single round."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await step_round(repo, season_id, round_number=1)

        standings = await compute_standings_from_repo(repo, season_id)

        assert len(standings) == 4
        # Total wins should equal total losses (2 games = 2 winners + 2 losers)
        total_wins = sum(s["wins"] for s in standings)
        total_losses = sum(s["losses"] for s in standings)
        assert total_wins == 2
        assert total_losses == 2
        # Each team should have played 1 game (circle method: all 4 teams play in round 1)
        for s in standings:
            assert s["wins"] + s["losses"] == 1

    async def test_standings_sorted_by_wins(self, repo: Repository):
        """Standings are sorted by wins descending."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)
        matchups = generate_round_robin(team_ids, num_rounds=3)
        total_rounds = max(m.round_number for m in matchups)

        for rnd in range(1, total_rounds + 1):
            await step_round(repo, season_id, round_number=rnd)

        standings = await compute_standings_from_repo(repo, season_id)

        # Verify sorted by wins descending
        for i in range(len(standings) - 1):
            assert standings[i]["wins"] >= standings[i + 1]["wins"]

    async def test_standings_have_team_names(self, repo: Repository):
        """Standings entries include team names."""
        season_id, _ = await _setup_season_with_teams(repo)
        await step_round(repo, season_id, round_number=1)

        standings = await compute_standings_from_repo(repo, season_id)

        for s in standings:
            assert "team_name" in s
            assert s["team_name"].startswith("Team ")


class TestPlayoffBracket:
    """Tests for playoff bracket generation."""

    async def test_four_team_bracket_structure(self, repo: Repository):
        """4-team playoff bracket has correct matchups: #1v4 and #2v3."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)
        matchups = generate_round_robin(team_ids, num_rounds=3)
        total_rounds = max(m.round_number for m in matchups)

        # Play all regular season rounds
        for rnd in range(1, total_rounds + 1):
            await step_round(repo, season_id, round_number=rnd)

        bracket = await generate_playoff_bracket(repo, season_id, num_playoff_teams=4)

        # Should have 2 semifinals + 1 finals placeholder = 3
        assert len(bracket) == 3

        semis = [m for m in bracket if m["playoff_round"] == "semifinal"]
        finals = [m for m in bracket if m["playoff_round"] == "finals"]
        assert len(semis) == 2
        assert len(finals) == 1

        # Verify semis: #1 vs #4 and #2 vs #3
        standings = await compute_standings_from_repo(repo, season_id)
        seed_1 = standings[0]["team_id"]
        seed_2 = standings[1]["team_id"]
        seed_3 = standings[2]["team_id"]
        seed_4 = standings[3]["team_id"]

        semi_pairs = {(m["home_team_id"], m["away_team_id"]) for m in semis}
        assert (seed_1, seed_4) in semi_pairs
        assert (seed_2, seed_3) in semi_pairs

    async def test_two_team_bracket(self, repo: Repository):
        """With only 2 playoff teams, creates a direct finals matchup."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)
        matchups = generate_round_robin(team_ids, num_rounds=3)
        total_rounds = max(m.round_number for m in matchups)

        for rnd in range(1, total_rounds + 1):
            await step_round(repo, season_id, round_number=rnd)

        bracket = await generate_playoff_bracket(repo, season_id, num_playoff_teams=2)

        assert len(bracket) == 1
        assert bracket[0]["playoff_round"] == "finals"
        # Top 2 seeds play each other
        standings = await compute_standings_from_repo(repo, season_id)
        assert bracket[0]["home_team_id"] == standings[0]["team_id"]
        assert bracket[0]["away_team_id"] == standings[1]["team_id"]

    async def test_playoff_schedule_entries_created(self, repo: Repository):
        """Playoff bracket stores schedule entries with phase='playoff'."""
        # Use a separate season that won't auto-generate brackets via step_round
        # by manually playing games and then calling generate_playoff_bracket
        league = await repo.create_league("Bracket Test League")
        s = await repo.create_season(
            league.id,
            "Bracket S",
            starting_ruleset={"quarter_minutes": 3},
        )
        tids = []
        for i in range(4):
            t = await repo.create_team(
                s.id,
                f"BT {i + 1}",
                venue={"name": f"BT Arena {i + 1}", "capacity": 5000},
            )
            tids.append(t.id)
            for j in range(3):
                await repo.create_hooper(
                    team_id=t.id,
                    season_id=s.id,
                    name=f"BH-{i + 1}-{j + 1}",
                    archetype="sharpshooter",
                    attributes=_hooper_attrs(),
                )
        bt_matchups = generate_round_robin(tids)
        bt_total_rounds = max(m.round_number for m in bt_matchups)
        for m in bt_matchups:
            await repo.create_schedule_entry(
                season_id=s.id,
                round_number=m.round_number,
                matchup_index=m.matchup_index,
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
            )
        # Set status to something that skips auto-bracket in step_round
        await repo.update_season_status(s.id, "regular_season_complete")
        for rnd in range(1, bt_total_rounds + 1):
            await step_round(repo, s.id, round_number=rnd)

        bracket = await generate_playoff_bracket(repo, s.id, num_playoff_teams=4)
        assert len(bracket) == 3

        playoff_schedule = await repo.get_full_schedule(s.id, phase="playoff")
        assert len(playoff_schedule) == 2  # 2 semifinal matchups stored

        for entry in playoff_schedule:
            assert entry.phase == "playoff"
            # Playoff rounds should be after regular season
            assert entry.round_number > bt_total_rounds

    async def test_bracket_with_completed_season_via_step_round(self, repo: Repository):
        """step_round generates playoff bracket when season completes."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)
        await repo.update_season_status(season_id, "active")

        matchups = generate_round_robin(team_ids, num_rounds=3)
        total_rounds = max(m.round_number for m in matchups)

        result = None
        for rnd in range(1, total_rounds + 1):
            result = await step_round(repo, season_id, round_number=rnd)

        assert result is not None
        assert result.season_complete is True
        assert result.playoff_bracket is not None
        assert len(result.playoff_bracket) == 3  # 2 semis + 1 finals placeholder


class TestPlayoffProgression:
    """Tests for the full playoff progression pipeline (semis → finals → completed)."""

    async def _play_regular_season(self, repo: Repository, season_id: str, team_ids: list[str]):
        """Play all regular season rounds and return the last result.

        The schedule must have already been created with the desired num_rounds.
        """
        schedule = await repo.get_full_schedule(season_id, phase="regular")
        total_rounds = max(s.round_number for s in schedule)
        result = None
        for rnd in range(1, total_rounds + 1):
            result = await step_round(repo, season_id, round_number=rnd)
        return result, total_rounds

    async def test_semifinals_create_finals_entry(self, repo: Repository):
        """Play regular season + semis → verify finals matchup created."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)
        await repo.update_season_status(season_id, "active")

        # Play regular season (3 rounds for 4 teams)
        reg_result, total_rounds = await self._play_regular_season(
            repo, season_id, team_ids
        )
        assert reg_result.season_complete is True

        # Now play the semifinal round (round_number = total_rounds + 1)
        semi_round = total_rounds + 1
        result = await step_round(repo, season_id, round_number=semi_round)

        # Should have created finals entry
        assert result.finals_matchup is not None
        assert result.finals_matchup["home_team_id"] != "TBD"
        assert result.finals_matchup["away_team_id"] != "TBD"
        assert result.finals_matchup["playoff_round"] == "finals"

        # DB should have 3 playoff entries: 2 semis + 1 finals
        playoff_schedule = await repo.get_full_schedule(season_id, phase="playoff")
        assert len(playoff_schedule) == 3

        # Season status should be "playoffs"
        season = await repo.get_season(season_id)
        assert season.status == "playoffs"

    async def test_finals_complete_season(self, repo: Repository):
        """Play through finals → verify season enters championship phase."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)
        await repo.update_season_status(season_id, "active")

        # Play regular season
        _, total_rounds = await self._play_regular_season(repo, season_id, team_ids)

        # Play semifinals
        semi_round = total_rounds + 1
        semi_result = await step_round(repo, season_id, round_number=semi_round)
        assert semi_result.finals_matchup is not None

        # Play finals
        finals_round = semi_round + 1
        finals_result = await step_round(repo, season_id, round_number=finals_round)

        assert finals_result.playoffs_complete is True

        # Season should now be in "championship" (not directly "completed")
        season = await repo.get_season(season_id)
        assert season.status == "championship"
        # Championship config should be stored
        assert season.config is not None
        assert "champion_team_id" in season.config
        assert "awards" in season.config
        assert "championship_ends_at" in season.config

    async def test_two_team_bracket_completes(self, repo: Repository):
        """2-team playoff bracket → after finals, season is completed (no semi step)."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)
        await repo.update_season_status(season_id, "active")

        # Play regular season
        _, total_rounds = await self._play_regular_season(repo, season_id, team_ids)

        # Re-generate bracket with only 2 teams
        # First clear the existing playoff schedule and regenerate
        # The regular season completion already generated a 4-team bracket.
        # We need a separate setup for 2-team. Let's create a fresh season.
        league = await repo.create_league("Two Team League")
        s2 = await repo.create_season(
            league.id, "S2", starting_ruleset={"quarter_minutes": 3}
        )
        t_ids = []
        for i in range(4):
            t = await repo.create_team(
                s2.id, f"TT {i + 1}", venue={"name": f"A {i}", "capacity": 5000}
            )
            t_ids.append(t.id)
            for j in range(3):
                await repo.create_hooper(
                    team_id=t.id,
                    season_id=s2.id,
                    name=f"TT-H-{i}-{j}",
                    archetype="sharpshooter",
                    attributes=_hooper_attrs(),
                )
        matchups = generate_round_robin(t_ids)
        for m in matchups:
            await repo.create_schedule_entry(
                season_id=s2.id,
                round_number=m.round_number,
                matchup_index=m.matchup_index,
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
            )
        await repo.update_season_status(s2.id, "active")

        # Play regular season
        tt_total = max(m.round_number for m in matchups)
        for rnd in range(1, tt_total + 1):
            await step_round(repo, s2.id, round_number=rnd)

        # After regular season, tiebreaker check transitions to playoffs (or tiebreakers)
        season_row = await repo.get_season(s2.id)
        assert season_row.status in ("playoffs", "tiebreakers")

        # Clear existing playoff schedule entries and create 2-team bracket
        await generate_playoff_bracket(repo, s2.id, num_playoff_teams=2)

        # The 4-team bracket was already generated by step_round, but the 2-team
        # generate_playoff_bracket added a direct finals entry too. Let's just
        # work with what step_round created: it already made a 4-team bracket.
        # Instead, test the 2-team path with a simpler approach.
        # Actually, generate_playoff_bracket was already called during step_round
        # with default 4 teams. The 2-team call above adds additional entries.
        # This is getting complex — let's use a cleaner approach.

        # For a true 2-team test, create a season with just 2 teams
        league3 = await repo.create_league("Duo League")
        s3 = await repo.create_season(
            league3.id, "S3", starting_ruleset={"quarter_minutes": 3}
        )
        duo_ids = []
        for i in range(2):
            t = await repo.create_team(
                s3.id, f"Duo {i + 1}", venue={"name": f"D {i}", "capacity": 5000}
            )
            duo_ids.append(t.id)
            for j in range(3):
                await repo.create_hooper(
                    team_id=t.id,
                    season_id=s3.id,
                    name=f"Duo-H-{i}-{j}",
                    archetype="sharpshooter",
                    attributes=_hooper_attrs(),
                )
        duo_matchups = generate_round_robin(duo_ids)
        for m in duo_matchups:
            await repo.create_schedule_entry(
                season_id=s3.id,
                round_number=m.round_number,
                matchup_index=m.matchup_index,
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
            )
        await repo.update_season_status(s3.id, "active")

        # Play regular season (2 teams = 1 round)
        duo_total = max(m.round_number for m in duo_matchups)
        for rnd in range(1, duo_total + 1):
            await step_round(repo, s3.id, round_number=rnd)

        season_row = await repo.get_season(s3.id)
        assert season_row.status in ("playoffs", "tiebreakers")

        # Play finals (the bracket for 2 teams is a direct finals entry)
        finals_round = duo_total + 1
        finals_result = await step_round(repo, s3.id, round_number=finals_round)

        assert finals_result.playoffs_complete is True
        season_row = await repo.get_season(s3.id)
        # After playoffs complete, season enters championship (not directly completed)
        assert season_row.status == "championship"
        assert season_row.config is not None
        assert "champion_team_id" in season_row.config

    async def test_semifinals_complete_event_published(self, repo: Repository):
        """Verify season.semifinals_complete event with finals_matchup."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)
        await repo.update_season_status(season_id, "active")

        _, total_rounds = await self._play_regular_season(repo, season_id, team_ids)

        bus = EventBus()
        received = []
        semi_round = total_rounds + 1

        async with bus.subscribe(None) as sub:
            await step_round(
                repo, season_id, round_number=semi_round, event_bus=bus
            )
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]
        assert "season.semifinals_complete" in event_types

        semi_event = [
            e for e in received if e["type"] == "season.semifinals_complete"
        ][0]
        assert "finals_matchup" in semi_event["data"]
        assert "semifinal_winners" in semi_event["data"]
        assert len(semi_event["data"]["semifinal_winners"]) == 2

    async def test_playoffs_complete_event_published(self, repo: Repository):
        """Verify season.playoffs_complete event with champion_team_id."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)
        await repo.update_season_status(season_id, "active")

        _, total_rounds = await self._play_regular_season(repo, season_id, team_ids)

        # Play semis
        semi_round = total_rounds + 1
        await step_round(repo, season_id, round_number=semi_round)

        # Play finals with event bus
        bus = EventBus()
        received = []
        finals_round = semi_round + 1

        async with bus.subscribe(None) as sub:
            await step_round(
                repo, season_id, round_number=finals_round, event_bus=bus
            )
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]
        assert "season.playoffs_complete" in event_types

        complete_event = [
            e for e in received if e["type"] == "season.playoffs_complete"
        ][0]
        assert "champion_team_id" in complete_event["data"]
        assert complete_event["data"]["champion_team_id"] is not None

    async def test_no_finals_before_semis_done(self, repo: Repository):
        """Playing 1 regular round should not trigger playoff progression."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)
        await repo.update_season_status(season_id, "active")

        result = await step_round(repo, season_id, round_number=1)

        assert result.playoffs_complete is False
        assert result.finals_matchup is None
        assert result.season_complete is False

    async def test_season_enters_championship_after_finals(self, repo: Repository):
        """After full playoff lifecycle, season is in championship phase."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)
        await repo.update_season_status(season_id, "active")

        # Play regular season
        _, total_rounds = await self._play_regular_season(repo, season_id, team_ids)

        # Play semis
        semi_round = total_rounds + 1
        await step_round(repo, season_id, round_number=semi_round)

        # Play finals
        finals_round = semi_round + 1
        await step_round(repo, season_id, round_number=finals_round)

        # Season should be in championship phase (still "active" for get_active_season)
        season = await repo.get_season(season_id)
        assert season.status == "championship"

        # get_active_season should still return it (championship is an active phase)
        active = await repo.get_active_season()
        assert active is not None
        assert active.id == season_id

    async def test_check_all_playoffs_complete(self, repo: Repository):
        """Unit test: _check_all_playoffs_complete returns False/True correctly."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=3)
        await repo.update_season_status(season_id, "active")

        # Before any playoffs
        assert await _check_all_playoffs_complete(repo, season_id) is False

        # Play regular season
        _, total_rounds = await self._play_regular_season(repo, season_id, team_ids)

        # After bracket generated but before playoff games
        assert await _check_all_playoffs_complete(repo, season_id) is False

        # Play semis
        semi_round = total_rounds + 1
        await step_round(repo, season_id, round_number=semi_round)

        # After semis but before finals
        assert await _check_all_playoffs_complete(repo, season_id) is False

        # Play finals
        finals_round = semi_round + 1
        await step_round(repo, season_id, round_number=finals_round)

        # After all playoff games
        assert await _check_all_playoffs_complete(repo, season_id) is True


class TestPhaseSimulateAndGovern:
    """Tests for the extracted _phase_simulate_and_govern function."""

    async def test_returns_sim_phase_result(self, repo: Repository):
        season_id, team_ids = await _setup_season_with_teams(repo)

        sim = await _phase_simulate_and_govern(repo, season_id, round_number=1)

        assert sim is not None
        assert isinstance(sim, _SimPhaseResult)
        assert sim.season_id == season_id
        assert sim.round_number == 1
        assert len(sim.game_results) == 2
        assert len(sim.game_row_ids) == 2
        assert len(sim.game_summaries) == 2
        assert len(sim.teams_cache) == 4
        # Summaries should NOT have commentary (that's added in AI phase)
        for gs in sim.game_summaries:
            assert "commentary" not in gs

    async def test_returns_none_for_empty_round(self, repo: Repository):
        league = await repo.create_league("Empty")
        season = await repo.create_season(league.id, "Empty Season")

        sim = await _phase_simulate_and_govern(repo, season.id, round_number=99)
        assert sim is None

    async def test_governance_tallied(self, repo: Repository):
        """Governance tally occurs when interval matches."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # Submit a proposal
        gov_id = "gov-phase-test"
        await regenerate_tokens(repo, gov_id, team_ids[0], season_id)
        from pinwheel.ai.interpreter import interpret_proposal_mock
        from pinwheel.core.governance import cast_vote, confirm_proposal, submit_proposal

        interpretation = interpret_proposal_mock("Make three pointers worth 5", RuleSet())
        proposal = await submit_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_ids[0],
            season_id=season_id,
            window_id="",
            raw_text="Make three pointers worth 5",
            interpretation=interpretation,
            ruleset=RuleSet(),
        )
        proposal = await confirm_proposal(repo, proposal)
        await cast_vote(
            repo=repo,
            proposal=proposal,
            governor_id=gov_id,
            team_id=team_ids[0],
            vote_choice="yes",
            weight=1.0,
        )

        sim = await _phase_simulate_and_govern(
            repo, season_id, round_number=1, governance_interval=1,
        )

        assert sim is not None
        assert len(sim.tallies) == 1
        assert sim.tallies[0].passed is True
        assert sim.governance_summary is not None

    async def test_stores_game_results_in_db(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)

        await _phase_simulate_and_govern(repo, season_id, round_number=1)

        games = await repo.get_games_for_round(season_id, 1)
        assert len(games) == 2


class TestPhaseAI:
    """Tests for the extracted _phase_ai function (mock mode)."""

    async def test_returns_ai_phase_result(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)

        sim = await _phase_simulate_and_govern(repo, season_id, round_number=1)
        assert sim is not None

        ai = await _phase_ai(sim, api_key="")

        assert isinstance(ai, _AIPhaseResult)
        # Mock commentary for each game
        assert len(ai.commentaries) == 2
        assert ai.highlight_reel != ""
        assert ai.sim_report is not None
        assert ai.sim_report.report_type == "simulation"
        assert ai.gov_report is not None
        assert ai.gov_report.report_type == "governance"

    async def test_private_reports_for_active_governors(self, repo: Repository):
        season_id, team_ids = await _setup_season_with_teams(repo)

        # Create governor activity so private reports get generated
        gov_id = "gov-ai-test"
        await regenerate_tokens(repo, gov_id, team_ids[0], season_id)
        from pinwheel.ai.interpreter import interpret_proposal_mock
        from pinwheel.core.governance import confirm_proposal, submit_proposal

        interpretation = interpret_proposal_mock("Test rule", RuleSet())
        proposal = await submit_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_ids[0],
            season_id=season_id,
            window_id="",
            raw_text="Test rule",
            interpretation=interpretation,
            ruleset=RuleSet(),
        )
        await confirm_proposal(repo, proposal)

        sim = await _phase_simulate_and_govern(repo, season_id, round_number=1)
        assert sim is not None
        assert gov_id in sim.active_governor_ids

        ai = await _phase_ai(sim, api_key="")

        assert len(ai.private_reports) >= 1
        gov_ids = [gid for gid, _ in ai.private_reports]
        assert gov_id in gov_ids


class TestPhasePersistAndFinalize:
    """Tests for the extracted _phase_persist_and_finalize function."""

    async def test_stores_reports_and_returns_round_result(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)

        sim = await _phase_simulate_and_govern(repo, season_id, round_number=1)
        assert sim is not None
        ai = await _phase_ai(sim, api_key="")

        result = await _phase_persist_and_finalize(repo, sim, ai)

        assert result.round_number == 1
        assert len(result.games) == 2
        assert len(result.reports) >= 2  # sim + gov

        # Reports stored in DB
        reports = await repo.get_reports_for_round(season_id, 1)
        assert len(reports) >= 2

    async def test_commentary_attached_to_summaries(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)

        sim = await _phase_simulate_and_govern(repo, season_id, round_number=1)
        assert sim is not None
        ai = await _phase_ai(sim, api_key="")

        result = await _phase_persist_and_finalize(repo, sim, ai)

        # After finalization, commentary should be attached to game summaries
        for game in result.games:
            assert "commentary" in game


class TestStepRoundMultisession:
    """Tests for step_round_multisession — same results, separate DB sessions."""

    async def test_produces_same_results_as_step_round(self, engine: AsyncEngine):
        """step_round_multisession produces equivalent results to step_round."""
        # Set up a season using a session
        async with get_session(engine) as session:
            repo = Repository(session)
            season_id, team_ids = await _setup_season_with_teams(repo)

        # Run multisession variant
        result = await step_round_multisession(
            engine, season_id, round_number=1,
        )

        assert result.round_number == 1
        assert len(result.games) == 2
        assert len(result.reports) >= 2
        for game in result.games:
            assert game["home_score"] > 0 or game["away_score"] > 0
            assert game["winner_team_id"] in team_ids

    async def test_stores_games_in_db(self, engine: AsyncEngine):
        async with get_session(engine) as session:
            repo = Repository(session)
            season_id, _ = await _setup_season_with_teams(repo)

        await step_round_multisession(engine, season_id, round_number=1)

        async with get_session(engine) as session:
            repo = Repository(session)
            games = await repo.get_games_for_round(season_id, 1)
            assert len(games) == 2

    async def test_stores_reports_in_db(self, engine: AsyncEngine):
        async with get_session(engine) as session:
            repo = Repository(session)
            season_id, _ = await _setup_season_with_teams(repo)

        await step_round_multisession(engine, season_id, round_number=1)

        async with get_session(engine) as session:
            repo = Repository(session)
            reports = await repo.get_reports_for_round(season_id, 1)
            assert len(reports) >= 2

    async def test_empty_round(self, engine: AsyncEngine):
        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Empty")
            season = await repo.create_season(league.id, "Empty Season")
            season_id = season.id

        result = await step_round_multisession(engine, season_id, round_number=99)
        assert result.games == []
        assert result.reports == []

    async def test_publishes_events(self, engine: AsyncEngine):
        async with get_session(engine) as session:
            repo = Repository(session)
            season_id, _ = await _setup_season_with_teams(repo)

        bus = EventBus()
        received = []

        async with bus.subscribe(None) as sub:
            await step_round_multisession(
                engine, season_id, round_number=1, event_bus=bus,
            )
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]
        assert "game.completed" in event_types
        assert "round.completed" in event_types


class TestStepRoundBackwardCompat:
    """Verify step_round still works identically after refactoring."""

    async def test_existing_behavior_preserved(self, repo: Repository):
        """step_round with single repo still works as before."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        result = await step_round(repo, season_id, round_number=1)

        assert result.round_number == 1
        assert len(result.games) == 2
        assert len(result.reports) >= 2
        for game in result.games:
            assert game["home_score"] > 0 or game["away_score"] > 0
            assert game["winner_team_id"] in team_ids
            # Commentary should be present (added by _phase_ai + _phase_persist_and_finalize)
            assert "commentary" in game

    async def test_governance_still_works(self, repo: Repository):
        season_id, team_ids = await _setup_season_with_teams(repo)

        gov_id = "gov-compat-test"
        await regenerate_tokens(repo, gov_id, team_ids[0], season_id)
        interpretation = interpret_proposal_mock("Make three pointers worth 5", RuleSet())
        from pinwheel.core.governance import cast_vote, confirm_proposal, submit_proposal

        proposal = await submit_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_ids[0],
            season_id=season_id,
            window_id="",
            raw_text="Make three pointers worth 5",
            interpretation=interpretation,
            ruleset=RuleSet(),
        )
        proposal = await confirm_proposal(repo, proposal)
        await cast_vote(
            repo=repo,
            proposal=proposal,
            governor_id=gov_id,
            team_id=team_ids[0],
            vote_choice="yes",
            weight=1.0,
        )

        result = await step_round(
            repo, season_id, round_number=1, governance_interval=1,
        )

        assert len(result.tallies) == 1
        assert result.tallies[0].passed is True
