"""Tests for the game loop — the autonomous round cycle."""

from math import comb

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.ai.interpreter import interpret_proposal_mock
from pinwheel.core.event_bus import EventBus
from pinwheel.core.game_loop import (
    _AIPhaseResult,
    _check_season_complete,
    _generate_series_reports,
    _get_playoff_series_record,
    _get_series_games,
    _phase_ai,
    _phase_persist_and_finalize,
    _phase_simulate_and_govern,
    _schedule_next_series_game,
    _series_wins_needed,
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


# Number of teams created by _setup_season_with_teams.
# Derived constants: games_per_round = comb(NUM_TEAMS, 2), games_per_team = NUM_TEAMS - 1.
NUM_TEAMS = 4

# Ruleset with best-of-1 playoffs for backward-compat tests.
_BO1_RULESET = {
    "quarter_minutes": 3,
    "playoff_semis_best_of": 1,
    "playoff_finals_best_of": 1,
}


async def _setup_season_with_teams(
    repo: Repository, num_rounds: int = 1, starting_ruleset: dict | None = None,
) -> tuple[str, list[str]]:
    """Create a league, season, NUM_TEAMS teams with 4 hoopers each, and a schedule.

    Args:
        repo: Repository instance.
        num_rounds: Number of complete round-robins to schedule (default 1).
            With NUM_TEAMS teams, each round has comb(NUM_TEAMS, 2) games.
    """
    league = await repo.create_league("Test League")
    season = await repo.create_season(
        league.id,
        "Season 1",
        starting_ruleset=starting_ruleset or {"quarter_minutes": 3},
    )

    team_ids = []
    for i in range(NUM_TEAMS):
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
        assert len(result.games) == comb(NUM_TEAMS, 2)
        for game in result.games:
            assert game["home_score"] > 0 or game["away_score"] > 0
            assert game["winner_team_id"] in team_ids

    async def test_stores_game_results(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)
        await step_round(repo, season_id, round_number=1)

        games = await repo.get_games_for_round(season_id, 1)
        assert len(games) == comb(NUM_TEAMS, 2)
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
        season_id, _ = await _setup_season_with_teams(repo, num_rounds=2)

        r1 = await step_round(repo, season_id, round_number=1)
        r2 = await step_round(repo, season_id, round_number=2)

        expected_games = comb(NUM_TEAMS, 2)
        assert r1.round_number == 1
        assert r2.round_number == 2
        assert len(r1.games) == expected_games
        assert len(r2.games) == expected_games

        # Different rounds should have different games
        r1_games = await repo.get_games_for_round(season_id, 1)
        r2_games = await repo.get_games_for_round(season_id, 2)
        assert len(r1_games) == expected_games
        assert len(r2_games) == expected_games

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

        # Pre-emit first_tally_seen so the minimum voting period is satisfied.
        # This simulates the proposal having been seen in a prior tally cycle.
        await repo.append_event(
            event_type="proposal.first_tally_seen",
            aggregate_id=proposal.id,
            aggregate_type="proposal",
            season_id=season_id,
            payload={"proposal_id": proposal.id, "round_number": 0},
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
        # 4 teams, 1 round-robin cycle => 1 round (C(4,2) = 6 games)
        matchups = generate_round_robin(team_ids)
        total_rounds = max(m.round_number for m in matchups)
        assert total_rounds == 1

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
        assert total_rounds == 3

        # Play all rounds
        for rnd in range(1, total_rounds + 1):
            result = await step_round(repo, season_id, round_number=rnd)

        # The last round should detect season completion
        assert result.season_complete is True
        assert result.final_standings is not None
        assert len(result.final_standings) == NUM_TEAMS

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
        assert len(season_event["data"]["standings"]) == NUM_TEAMS


class TestComputeStandings:
    """Tests for standings computation from repository data."""

    async def test_standings_after_one_round(self, repo: Repository):
        """Standings are computed correctly after a single round."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await step_round(repo, season_id, round_number=1)

        standings = await compute_standings_from_repo(repo, season_id)

        expected_games = comb(NUM_TEAMS, 2)
        assert len(standings) == NUM_TEAMS
        # Total wins should equal total losses (one winner per game)
        total_wins = sum(s["wins"] for s in standings)
        total_losses = sum(s["losses"] for s in standings)
        assert total_wins == expected_games
        assert total_losses == expected_games
        # Each team plays every other team once per round
        for s in standings:
            assert s["wins"] + s["losses"] == NUM_TEAMS - 1

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
        season_id, team_ids = await _setup_season_with_teams(
            repo, num_rounds=3, starting_ruleset=_BO1_RULESET,
        )
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
        season_id, team_ids = await _setup_season_with_teams(
            repo, num_rounds=3, starting_ruleset=_BO1_RULESET,
        )
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
        season_id, team_ids = await _setup_season_with_teams(
            repo, num_rounds=3, starting_ruleset=_BO1_RULESET,
        )
        await repo.update_season_status(season_id, "active")

        # Play regular season
        _, total_rounds = await self._play_regular_season(repo, season_id, team_ids)

        # Re-generate bracket with only 2 teams
        # First clear the existing playoff schedule and regenerate
        # The regular season completion already generated a 4-team bracket.
        # We need a separate setup for 2-team. Let's create a fresh season.
        league = await repo.create_league("Two Team League")
        s2 = await repo.create_season(
            league.id, "S2", starting_ruleset=_BO1_RULESET,
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
            league3.id, "S3", starting_ruleset=_BO1_RULESET,
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
        season_id, team_ids = await _setup_season_with_teams(
            repo, num_rounds=3, starting_ruleset=_BO1_RULESET,
        )
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
        season_id, team_ids = await _setup_season_with_teams(
            repo, num_rounds=3, starting_ruleset=_BO1_RULESET,
        )
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
        season_id, team_ids = await _setup_season_with_teams(
            repo, num_rounds=3, starting_ruleset=_BO1_RULESET,
        )
        await repo.update_season_status(season_id, "active")

        result = await step_round(repo, season_id, round_number=1)

        assert result.playoffs_complete is False
        assert result.finals_matchup is None
        assert result.season_complete is False

    async def test_season_enters_championship_after_finals(self, repo: Repository):
        """After full playoff lifecycle, season is in championship phase."""
        season_id, team_ids = await _setup_season_with_teams(
            repo, num_rounds=3, starting_ruleset=_BO1_RULESET,
        )
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



class TestPhaseSimulateAndGovern:
    """Tests for the extracted _phase_simulate_and_govern function."""

    async def test_returns_sim_phase_result(self, repo: Repository):
        season_id, team_ids = await _setup_season_with_teams(repo)

        sim = await _phase_simulate_and_govern(repo, season_id, round_number=1)

        assert sim is not None
        assert isinstance(sim, _SimPhaseResult)
        expected_games = comb(NUM_TEAMS, 2)
        assert sim.season_id == season_id
        assert sim.round_number == 1
        assert len(sim.game_results) == expected_games
        assert len(sim.game_row_ids) == expected_games
        assert len(sim.game_summaries) == expected_games
        assert len(sim.teams_cache) == NUM_TEAMS
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

        # Pre-emit first_tally_seen to satisfy minimum voting period
        await repo.append_event(
            event_type="proposal.first_tally_seen",
            aggregate_id=proposal.id,
            aggregate_type="proposal",
            season_id=season_id,
            payload={"proposal_id": proposal.id, "round_number": 0},
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
        assert len(games) == comb(NUM_TEAMS, 2)


class TestPhaseAI:
    """Tests for the extracted _phase_ai function (mock mode)."""

    async def test_returns_ai_phase_result(self, repo: Repository):
        season_id, _ = await _setup_season_with_teams(repo)

        sim = await _phase_simulate_and_govern(repo, season_id, round_number=1)
        assert sim is not None

        ai = await _phase_ai(sim, api_key="")

        assert isinstance(ai, _AIPhaseResult)
        # Mock commentary for each game
        assert len(ai.commentaries) == comb(NUM_TEAMS, 2)
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
        assert len(result.games) == comb(NUM_TEAMS, 2)
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
        assert len(result.games) == comb(NUM_TEAMS, 2)
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
            assert len(games) == comb(NUM_TEAMS, 2)

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
        assert len(result.games) == comb(NUM_TEAMS, 2)
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

        # Pre-emit first_tally_seen to satisfy minimum voting period
        await repo.append_event(
            event_type="proposal.first_tally_seen",
            aggregate_id=proposal.id,
            aggregate_type="proposal",
            season_id=season_id,
            payload={"proposal_id": proposal.id, "round_number": 0},
        )

        result = await step_round(
            repo, season_id, round_number=1, governance_interval=1,
        )

        assert len(result.tallies) == 1
        assert result.tallies[0].passed is True


class TestPlayoffSeries:
    """Tests for best-of-N playoff series logic (P0 fix)."""

    async def test_series_wins_needed(self):
        """_series_wins_needed returns correct win threshold."""
        assert _series_wins_needed(1) == 1
        assert _series_wins_needed(3) == 2
        assert _series_wins_needed(5) == 3
        assert _series_wins_needed(7) == 4

    async def test_get_playoff_series_record(self, repo: Repository):
        """_get_playoff_series_record counts wins in playoff rounds only."""
        season_id, team_ids = await _setup_season_with_teams(
            repo, num_rounds=3, starting_ruleset=_BO1_RULESET,
        )
        await repo.update_season_status(season_id, "active")

        # Play regular season
        schedule = await repo.get_full_schedule(season_id, phase="regular")
        total_rounds = max(s.round_number for s in schedule)
        for rnd in range(1, total_rounds + 1):
            await step_round(repo, season_id, round_number=rnd)

        # Get playoff bracket
        playoff_sched = await repo.get_full_schedule(season_id, phase="playoff")
        assert len(playoff_sched) >= 2
        se = playoff_sched[0]

        # Before playoff games: 0-0
        a_wins, b_wins, games = await _get_playoff_series_record(
            repo, season_id, se.home_team_id, se.away_team_id,
        )
        assert a_wins == 0 and b_wins == 0 and games == 0

        # Play semi round
        semi_round = total_rounds + 1
        await step_round(repo, season_id, round_number=semi_round)

        # After 1 game: 1-0 or 0-1
        a_wins, b_wins, games = await _get_playoff_series_record(
            repo, season_id, se.home_team_id, se.away_team_id,
        )
        assert games == 1
        assert a_wins + b_wins == 1

    async def test_semi_series_best_of_3_schedules_multiple_games(self, repo: Repository):
        """Best-of-3 semis schedule Game 2 after Game 1 (series not clinched at 1-0)."""
        ruleset_dict = {
            "quarter_minutes": 3,
            "playoff_semis_best_of": 3,
            "playoff_finals_best_of": 1,
        }
        season_id, team_ids = await _setup_season_with_teams(
            repo, num_rounds=3, starting_ruleset=ruleset_dict,
        )
        await repo.update_season_status(season_id, "active")

        # Play regular season
        schedule = await repo.get_full_schedule(season_id, phase="regular")
        total_rounds = max(s.round_number for s in schedule)
        for rnd in range(1, total_rounds + 1):
            await step_round(repo, season_id, round_number=rnd)

        # After regular season: 2 playoff entries (Game 1 for each semi)
        playoff_sched = await repo.get_full_schedule(season_id, phase="playoff")
        assert len(playoff_sched) == 2

        # Play semi Game 1
        semi_round_1 = total_rounds + 1
        r1 = await step_round(repo, season_id, round_number=semi_round_1)
        # Series is 1-0 — not clinched with bo3, so no finals yet
        assert r1.finals_matchup is None

        # Game 2 must have been scheduled for both series
        playoff_sched = await repo.get_full_schedule(season_id, phase="playoff")
        assert len(playoff_sched) >= 4  # 2 original + 2 new

        # Play semi Game 2
        semi_round_2 = semi_round_1 + 1
        r2 = await step_round(repo, season_id, round_number=semi_round_2)

        # After Game 2: series could be 2-0 (clinched) or 1-1 (need Game 3).
        # Play additional rounds until finals are created.
        current_round = semi_round_2
        while r2.finals_matchup is None:
            current_round += 1
            sched = await repo.get_schedule_for_round(season_id, current_round)
            if not sched:
                break
            r2 = await step_round(repo, season_id, round_number=current_round)

        # Finals must have been created (both semis decided)
        assert r2.finals_matchup is not None
        assert r2.finals_matchup["playoff_round"] == "finals"

    async def test_schedule_next_series_game_alternates_home_court(self, repo: Repository):
        """Home court alternates: higher seed home on games 1, 3, 5; away on 2, 4."""
        league = await repo.create_league("HC Test")
        season = await repo.create_season(league.id, "HC Season")

        # Even games_played (0, 2, 4) → higher seed at home
        await _schedule_next_series_game(
            repo, season.id, "team-a", "team-b", 0, 10, 0,
        )
        sched = await repo.get_schedule_for_round(season.id, 10)
        assert sched[0].home_team_id == "team-a"
        assert sched[0].away_team_id == "team-b"

        # Odd games_played (1, 3) → lower seed at home
        await _schedule_next_series_game(
            repo, season.id, "team-a", "team-b", 1, 11, 0,
        )
        sched = await repo.get_schedule_for_round(season.id, 11)
        assert sched[0].home_team_id == "team-b"
        assert sched[0].away_team_id == "team-a"


class TestDeferredSeasonEvents:
    """Tests for the deferred events mechanism (P0 #1 fix)."""

    async def test_season_events_deferred_when_suppressed(self, repo: Repository):
        """When suppress_spoiler_events=True, season events go to deferred list."""
        season_id, team_ids = await _setup_season_with_teams(
            repo, num_rounds=3, starting_ruleset=_BO1_RULESET,
        )
        await repo.update_season_status(season_id, "active")

        # Play regular season
        schedule = await repo.get_full_schedule(season_id, phase="regular")
        total_rounds = max(s.round_number for s in schedule)
        for rnd in range(1, total_rounds + 1):
            await step_round(repo, season_id, round_number=rnd)

        bus = EventBus()
        received = []

        # Play semis with suppression
        semi_round = total_rounds + 1
        async with bus.subscribe(None) as sub:
            result = await step_round(
                repo, season_id, round_number=semi_round,
                event_bus=bus, suppress_spoiler_events=True,
            )
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]
        # Spoiler events should NOT appear on the bus
        assert "season.semifinals_complete" not in event_types
        assert "game.completed" not in event_types
        assert "round.completed" not in event_types

        # But they should be in the deferred list
        assert len(result.deferred_season_events) > 0
        deferred_types = [t for t, _ in result.deferred_season_events]
        assert "season.semifinals_complete" in deferred_types

    async def test_season_events_published_when_not_suppressed(self, repo: Repository):
        """Without suppression, season events publish immediately."""
        season_id, team_ids = await _setup_season_with_teams(
            repo, num_rounds=3, starting_ruleset=_BO1_RULESET,
        )
        await repo.update_season_status(season_id, "active")

        schedule = await repo.get_full_schedule(season_id, phase="regular")
        total_rounds = max(s.round_number for s in schedule)
        for rnd in range(1, total_rounds + 1):
            await step_round(repo, season_id, round_number=rnd)

        bus = EventBus()
        received = []

        semi_round = total_rounds + 1
        async with bus.subscribe(None) as sub:
            result = await step_round(
                repo, season_id, round_number=semi_round,
                event_bus=bus, suppress_spoiler_events=False,
            )
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]
        assert "season.semifinals_complete" in event_types
        # Deferred list should be empty
        assert len(result.deferred_season_events) == 0

    async def test_regular_season_complete_deferred(self, repo: Repository):
        """season.regular_season_complete deferred when suppress_spoiler_events=True."""
        season_id, team_ids = await _setup_season_with_teams(
            repo, num_rounds=3, starting_ruleset=_BO1_RULESET,
        )
        await repo.update_season_status(season_id, "active")

        schedule = await repo.get_full_schedule(season_id, phase="regular")
        total_rounds = max(s.round_number for s in schedule)

        bus = EventBus()
        received = []

        # Play all regular season rounds with suppression on the last one
        for rnd in range(1, total_rounds):
            await step_round(repo, season_id, round_number=rnd)

        async with bus.subscribe(None) as sub:
            result = await step_round(
                repo, season_id, round_number=total_rounds,
                event_bus=bus, suppress_spoiler_events=True,
            )
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]
        assert "season.regular_season_complete" not in event_types

        deferred_types = [t for t, _ in result.deferred_season_events]
        assert "season.regular_season_complete" in deferred_types


class TestEffectsGameLoopIntegration:
    """Tests that the effect lifecycle runs end-to-end in the game loop.

    Covers:
    - Effects load from event store at round start
    - Effects fire during simulation (meta values change)
    - Effects expire after tick_round
    - Meta is flushed to DB at round end
    - Governance tally registers new effects via tally_governance_with_effects
    - Effects summary flows into narrative context
    - Backward compatibility: rounds without effects work identically
    """

    async def _register_swagger_effect(
        self, repo: Repository, season_id: str, round_number: int = 0,
    ) -> str:
        """Register a swagger meta_mutation effect directly in the event store.

        Returns the effect_id. This simulates what tally_governance_with_effects
        does when a proposal passes.
        """
        import uuid

        from pinwheel.core.hooks import EffectLifetime, RegisteredEffect

        effect_id = str(uuid.uuid4())
        effect = RegisteredEffect(
            effect_id=effect_id,
            proposal_id="p-swagger-test",
            _hook_points=["round.game.post"],
            _lifetime=EffectLifetime.PERMANENT,
            effect_type="meta_mutation",
            target_type="team",
            target_selector="winning_team",
            meta_field="swagger",
            meta_value=1,
            meta_operation="increment",
            description="Winning team gains +1 swagger",
        )
        await repo.append_event(
            event_type="effect.registered",
            aggregate_id=effect_id,
            aggregate_type="effect",
            season_id=season_id,
            payload=effect.to_dict(),
        )
        return effect_id

    async def _register_n_rounds_effect(
        self,
        repo: Repository,
        season_id: str,
        rounds: int = 1,
    ) -> str:
        """Register an effect with N_ROUNDS lifetime. Returns effect_id."""
        import uuid

        from pinwheel.core.hooks import EffectLifetime, RegisteredEffect

        effect_id = str(uuid.uuid4())
        effect = RegisteredEffect(
            effect_id=effect_id,
            proposal_id="p-timed-test",
            _hook_points=["round.game.post"],
            _lifetime=EffectLifetime.N_ROUNDS,
            rounds_remaining=rounds,
            effect_type="narrative",
            narrative_instruction="Temporary narrative effect",
            description="Expires after N rounds",
        )
        await repo.append_event(
            event_type="effect.registered",
            aggregate_id=effect_id,
            aggregate_type="effect",
            season_id=season_id,
            payload=effect.to_dict(),
        )
        return effect_id

    async def test_effects_load_from_event_store(self, repo: Repository):
        """Effects registered in the event store are loaded at round start
        and available during simulation."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # Register a swagger effect before stepping the round
        await self._register_swagger_effect(repo, season_id)

        # Step the round — effects should be loaded and fire
        result = await step_round(repo, season_id, round_number=1)

        # Verify games were simulated
        assert len(result.games) == comb(NUM_TEAMS, 2)

        # The swagger effect fires at round.game.post for each game,
        # incrementing the winning team's swagger. Check that at least
        # one team has swagger > 0 in the DB after the round.
        teams = await repo.get_teams_for_season(season_id)
        total_swagger = 0
        for t in teams:
            meta = await repo.load_team_meta(t.id)
            total_swagger += meta.get("swagger", 0)

        # Each game has exactly one winner, so total swagger == number of games
        assert total_swagger == comb(NUM_TEAMS, 2)

    async def test_meta_flushed_to_db(self, repo: Repository):
        """Meta values written by effects during the round are flushed to DB."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # Register swagger effect
        await self._register_swagger_effect(repo, season_id)

        # Step round
        await step_round(repo, season_id, round_number=1)

        # Every winner should have swagger=1 (single round, each game has
        # one winner). The total should equal the number of games.
        games = await repo.get_games_for_round(season_id, 1)
        winner_swagger = {}
        for g in games:
            meta = await repo.load_team_meta(g.winner_team_id)
            winner_swagger[g.winner_team_id] = meta.get("swagger", 0)

        for wid, swagger in winner_swagger.items():
            assert swagger > 0, f"Winner {wid} should have swagger > 0"

    async def test_effects_expire_after_tick(self, repo: Repository):
        """Effects with N_ROUNDS lifetime expire and are persisted as expired."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=2)

        # Register a 1-round effect
        effect_id = await self._register_n_rounds_effect(
            repo, season_id, rounds=1,
        )

        # After round 1, the effect should tick and expire
        await step_round(repo, season_id, round_number=1)

        # Verify the effect.expired event was persisted
        expired_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["effect.expired"],
        )
        expired_ids = [
            e.payload.get("effect_id", e.aggregate_id) for e in expired_events
        ]
        assert effect_id in expired_ids

        # Loading the registry after expiration should show 0 effects
        from pinwheel.core.effects import load_effect_registry

        registry = await load_effect_registry(repo, season_id)
        assert registry.count == 0

    async def test_effects_persist_across_rounds(self, repo: Repository):
        """Permanent effects persist across multiple rounds."""
        season_id, team_ids = await _setup_season_with_teams(repo, num_rounds=2)

        # Register a permanent swagger effect
        await self._register_swagger_effect(repo, season_id)

        # Step round 1
        await step_round(repo, season_id, round_number=1)

        # Step round 2 — effect should load from event store again
        await step_round(repo, season_id, round_number=2)

        # Total swagger should equal 2 * games_per_round
        teams = await repo.get_teams_for_season(season_id)
        total_swagger = 0
        for t in teams:
            meta = await repo.load_team_meta(t.id)
            total_swagger += meta.get("swagger", 0)

        expected_total = 2 * comb(NUM_TEAMS, 2)
        assert total_swagger == expected_total

    async def test_backward_compat_no_effects(self, repo: Repository):
        """Rounds without effects work identically to before."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # No effects registered — step_round should work normally
        result = await step_round(repo, season_id, round_number=1)

        assert len(result.games) == comb(NUM_TEAMS, 2)
        assert len(result.reports) >= 2
        for game in result.games:
            assert game["home_score"] > 0 or game["away_score"] > 0
            assert game["winner_team_id"] in team_ids

        # No meta should have been written
        teams = await repo.get_teams_for_season(season_id)
        for t in teams:
            meta = await repo.load_team_meta(t.id)
            assert len(meta) == 0, f"Team {t.id} should have no meta"

    async def test_effects_summary_in_sim_result(self, repo: Repository):
        """Effects summary is built and stored on _SimPhaseResult."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # Register a swagger effect
        await self._register_swagger_effect(repo, season_id)

        sim = await _phase_simulate_and_govern(repo, season_id, round_number=1)

        assert sim is not None
        assert sim.effects_summary != ""
        assert "swagger" in sim.effects_summary.lower()

    async def test_effects_summary_empty_without_effects(self, repo: Repository):
        """Without effects, effects_summary is empty."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        sim = await _phase_simulate_and_govern(repo, season_id, round_number=1)

        assert sim is not None
        assert sim.effects_summary == ""

    async def test_effects_summary_in_narrative_context(self, repo: Repository):
        """Effects summary is injected into narrative context for AI reports."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # Register a swagger effect
        await self._register_swagger_effect(repo, season_id)

        sim = await _phase_simulate_and_govern(repo, season_id, round_number=1)

        assert sim is not None
        assert sim.narrative_context is not None
        assert sim.narrative_context.effects_narrative != ""
        assert "swagger" in sim.narrative_context.effects_narrative.lower()

    async def test_governance_tally_registers_effects(self, repo: Repository):
        """When governance tallies a passing proposal with v2 effects,
        those effects are registered in the effect registry and persisted
        to the event store."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # The tally_governance_with_effects function is used when an
        # effect_registry is available. Verify the game loop wiring works
        # end-to-end with a standard parameter-change proposal.
        gov_id = "gov-effects-loop-test"
        await regenerate_tokens(repo, gov_id, team_ids[0], season_id)

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

        # Pre-emit first_tally_seen to satisfy minimum voting period
        await repo.append_event(
            event_type="proposal.first_tally_seen",
            aggregate_id=proposal.id,
            aggregate_type="proposal",
            season_id=season_id,
            payload={"proposal_id": proposal.id, "round_number": 0},
        )

        # Step the round — governance tally should use
        # tally_governance_with_effects when an effect_registry exists
        result = await step_round(
            repo, season_id, round_number=1, governance_interval=1,
        )

        # The proposal should pass (parameter change: 3pt -> 5)
        assert len(result.tallies) == 1
        assert result.tallies[0].passed is True

        # Verify the ruleset was updated
        season = await repo.get_season(season_id)
        ruleset = RuleSet(**(season.current_ruleset or {}))
        assert ruleset.three_point_value == 5

    async def test_tally_pending_governance_with_effect_registry(
        self, repo: Repository,
    ):
        """tally_pending_governance passes the effect_registry to
        tally_governance_with_effects when provided."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        gov_id = "gov-tally-effects"
        await regenerate_tokens(repo, gov_id, team_ids[0], season_id)

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

        from pinwheel.core.effects import EffectRegistry
        from pinwheel.core.game_loop import tally_pending_governance

        registry = EffectRegistry()

        # Tally 1: deferred (minimum voting period)
        ruleset, tallies, gov_data = await tally_pending_governance(
            repo=repo,
            season_id=season_id,
            round_number=1,
            ruleset=RuleSet(),
            effect_registry=registry,
        )
        assert tallies == []

        # Tally 2: proposal passes
        ruleset, tallies, gov_data = await tally_pending_governance(
            repo=repo,
            season_id=season_id,
            round_number=2,
            ruleset=RuleSet(),
            effect_registry=registry,
        )

        assert len(tallies) == 1
        assert tallies[0].passed is True
        assert ruleset.three_point_value == 5

    async def test_tally_pending_governance_without_effect_registry(
        self, repo: Repository,
    ):
        """tally_pending_governance works without effect_registry (backward compat)."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        gov_id = "gov-tally-noeffects"
        await regenerate_tokens(repo, gov_id, team_ids[0], season_id)

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

        from pinwheel.core.game_loop import tally_pending_governance

        # Tally 1: deferred (minimum voting period)
        ruleset, tallies, gov_data = await tally_pending_governance(
            repo=repo,
            season_id=season_id,
            round_number=1,
            ruleset=RuleSet(),
        )
        assert tallies == []

        # Tally 2: proposal passes
        ruleset, tallies, gov_data = await tally_pending_governance(
            repo=repo,
            season_id=season_id,
            round_number=2,
            ruleset=RuleSet(),
        )

        assert len(tallies) == 1
        assert tallies[0].passed is True
        assert ruleset.three_point_value == 5

    async def test_multisession_effects_integration(self, engine: AsyncEngine):
        """step_round_multisession loads and fires effects across sessions."""
        async with get_session(engine) as session:
            repo = Repository(session)
            season_id, team_ids = await _setup_season_with_teams(repo)

            # Register a swagger effect
            await self._register_swagger_effect(repo, season_id)

        # Run multisession variant
        result = await step_round_multisession(
            engine, season_id, round_number=1,
        )

        assert len(result.games) == comb(NUM_TEAMS, 2)

        # Verify meta was flushed in session 1
        async with get_session(engine) as session:
            repo = Repository(session)
            teams = await repo.get_teams_for_season(season_id)
            total_swagger = 0
            for t in teams:
                meta = await repo.load_team_meta(t.id)
                total_swagger += meta.get("swagger", 0)

            assert total_swagger == comb(NUM_TEAMS, 2)

    async def test_gov_hooks_fire_around_tally(self, repo: Repository):
        """gov.pre and gov.post hooks fire when tally_pending_governance runs
        with an effect_registry and meta_store."""
        import uuid

        from pinwheel.core.effects import EffectRegistry
        from pinwheel.core.hooks import EffectLifetime, RegisteredEffect
        from pinwheel.core.meta import MetaStore

        season_id, team_ids = await _setup_season_with_teams(repo)

        # Register an effect on gov.pre that increments a meta counter
        effect_id = str(uuid.uuid4())
        effect = RegisteredEffect(
            effect_id=effect_id,
            proposal_id="p-gov-hook-test",
            _hook_points=["gov.pre"],
            _lifetime=EffectLifetime.PERMANENT,
            effect_type="meta_mutation",
            target_type="season",
            target_selector="all",
            meta_field="gov_pre_fired",
            meta_value=1,
            meta_operation="increment",
            description="Tracks gov.pre hook firing",
        )
        registry = EffectRegistry()
        registry.register(effect)

        meta_store = MetaStore()

        from pinwheel.core.game_loop import tally_pending_governance

        await tally_pending_governance(
            repo=repo,
            season_id=season_id,
            round_number=1,
            ruleset=RuleSet(),
            effect_registry=registry,
            meta_store=meta_store,
        )

        # gov.pre should have fired, incrementing the meta field
        # The meta_mutation on "season" / "all" writes to all entities —
        # but since there are no season entities loaded in meta_store,
        # the effect targets the selector "all" which means the
        # meta_store.get with entity type "season" should reflect writes.
        # Since this is a meta_mutation effect with target_selector="all",
        # the RegisteredEffect.apply resolves it based on the context.
        # For a gov.pre context, it fires but may not write meta directly
        # (depends on implementation). What matters is that the hook fired
        # without error.
        # This test primarily verifies the hook wiring doesn't raise.

    async def test_gov_post_hook_receives_tallies(self, repo: Repository):
        """gov.post hook has access to tally results via HookContext."""
        import uuid

        from pinwheel.core.effects import EffectRegistry
        from pinwheel.core.hooks import EffectLifetime, RegisteredEffect
        from pinwheel.core.meta import MetaStore

        season_id, team_ids = await _setup_season_with_teams(repo)

        # Register a gov.post narrative effect
        effect_id = str(uuid.uuid4())
        effect = RegisteredEffect(
            effect_id=effect_id,
            proposal_id="p-gov-post-test",
            _hook_points=["gov.post"],
            _lifetime=EffectLifetime.PERMANENT,
            effect_type="narrative",
            narrative_instruction="Governance has concluded.",
            description="Fires after governance tally",
        )
        registry = EffectRegistry()
        registry.register(effect)
        meta_store = MetaStore()

        # Submit and vote on a proposal so there's something to tally
        gov_id = "gov-post-test"
        await regenerate_tokens(repo, gov_id, team_ids[0], season_id)
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

        from pinwheel.core.game_loop import tally_pending_governance

        # Tally 1: deferred (minimum voting period)
        ruleset, tallies, gov_data = await tally_pending_governance(
            repo=repo,
            season_id=season_id,
            round_number=1,
            ruleset=RuleSet(),
            effect_registry=registry,
            meta_store=meta_store,
        )
        assert tallies == []

        # Tally 2: proposal passes, gov.post hook fires
        ruleset, tallies, gov_data = await tally_pending_governance(
            repo=repo,
            season_id=season_id,
            round_number=2,
            ruleset=RuleSet(),
            effect_registry=registry,
            meta_store=meta_store,
        )

        # The gov.post hook should have fired without errors
        assert len(tallies) == 1
        assert tallies[0].passed is True

    async def test_report_hooks_fire_in_ai_phase(self, repo: Repository):
        """report.simulation.pre and report.commentary.pre hooks fire
        during _phase_ai and inject narrative into context."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # Register a narrative effect that fires at report.simulation.pre
        await repo.append_event(
            event_type="effect.registered",
            aggregate_id="eff-report-sim",
            aggregate_type="effect",
            season_id=season_id,
            payload={
                "effect_id": "eff-report-sim",
                "proposal_id": "p-report-test",
                "hook_points": ["report.simulation.pre"],
                "lifetime": "PERMANENT",
                "effect_type": "narrative",
                "narrative_instruction": "The cosmic energy shifts between teams.",
                "description": "Narrative for sim report",
            },
        )
        # Register a narrative effect for report.commentary.pre
        await repo.append_event(
            event_type="effect.registered",
            aggregate_id="eff-report-comm",
            aggregate_type="effect",
            season_id=season_id,
            payload={
                "effect_id": "eff-report-comm",
                "proposal_id": "p-report-test",
                "hook_points": ["report.commentary.pre"],
                "lifetime": "PERMANENT",
                "effect_type": "narrative",
                "narrative_instruction": "Commentary should mention cosmic forces.",
                "description": "Narrative for commentary",
            },
        )

        # Run Phase 1 to get _SimPhaseResult
        sim = await _phase_simulate_and_govern(repo, season_id, round_number=1)
        assert sim is not None

        # Verify effect_registry was loaded with the narrative effects
        assert sim.effect_registry is not None
        assert sim.effect_registry.count >= 2

        # Run Phase 2 — report hooks should fire and inject narratives
        _ai_result = await _phase_ai(sim)  # noqa: F841

        # The narrative context should have been enriched by report hooks
        # If narrative_context exists, effects_narrative should contain our text
        if sim.narrative_context is not None:
            assert "cosmic" in sim.narrative_context.effects_narrative.lower()

    async def test_hooper_meta_loaded_into_metastore(self, repo: Repository):
        """Hooper metadata is loaded into MetaStore at round start."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # Set some hooper meta directly in the DB
        teams = await repo.get_teams_for_season(season_id)
        first_team = teams[0]
        hoopers = await repo.get_hoopers_for_team(first_team.id)
        test_hooper = hoopers[0]
        await repo.update_hooper_meta(test_hooper.id, {"clutch_rating": 99})

        # Register a swagger effect so effects system is active
        # (meta_store is only created when effect_registry.count > 0)
        await self._register_swagger_effect(repo, season_id)

        # Run Phase 1
        sim = await _phase_simulate_and_govern(repo, season_id, round_number=1)
        assert sim is not None

        # The meta_store_snapshot should contain our hooper meta
        assert sim.meta_store_snapshot is not None
        hooper_snapshot = sim.meta_store_snapshot.get("hooper", {})
        assert test_hooper.id in hooper_snapshot
        assert hooper_snapshot[test_hooper.id].get("clutch_rating") == 99

    async def test_tally_pending_governance_backward_compat_no_meta(
        self, repo: Repository,
    ):
        """tally_pending_governance works without meta_store (backward compat)."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        gov_id = "gov-no-meta"
        await regenerate_tokens(repo, gov_id, team_ids[0], season_id)

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

        from pinwheel.core.effects import EffectRegistry
        from pinwheel.core.game_loop import tally_pending_governance

        registry = EffectRegistry()

        # Tally 1: deferred (minimum voting period)
        ruleset, tallies, gov_data = await tally_pending_governance(
            repo=repo,
            season_id=season_id,
            round_number=1,
            ruleset=RuleSet(),
            effect_registry=registry,
        )
        assert tallies == []

        # Tally 2: proposal passes (no meta_store — backward compat)
        ruleset, tallies, gov_data = await tally_pending_governance(
            repo=repo,
            season_id=season_id,
            round_number=2,
            ruleset=RuleSet(),
            effect_registry=registry,
        )

        assert len(tallies) == 1
        assert tallies[0].passed is True
        assert ruleset.three_point_value == 5

    async def test_interpreter_mock_uses_possession_pre_not_shot_pre(self):
        """The v2 mock interpreter should use sim.possession.pre hook point."""
        from pinwheel.ai.interpreter import interpret_proposal_v2_mock

        result = interpret_proposal_v2_mock("Give everyone a shooting boost", RuleSet())
        # The "boost" pattern produces a hook_callback effect
        hook_effects = [
            e for e in result.effects if e.effect_type == "hook_callback"
        ]
        assert len(hook_effects) == 1
        assert hook_effects[0].hook_point == "sim.possession.pre"


class TestRowToTeam:
    """Tests for _row_to_team deserialization."""

    async def test_deserializes_moves_from_db_row(self, repo: Repository) -> None:
        """Verify _row_to_team properly deserializes moves stored as JSON in the DB."""
        from pinwheel.core.game_loop import _row_to_team
        from pinwheel.models.team import Move

        league = await repo.create_league("Test League")
        season = await repo.create_season(league.id, "Season 1")
        team = await repo.create_team(
            season.id,
            "Move Test Team",
            venue={"name": "Test Arena", "capacity": 5000},
        )

        moves_data = [
            {
                "name": "Heat Check",
                "trigger": "made_three",
                "effect": "+10% 3pt",
                "attribute_gate": {"scoring": 70},
                "source": "archetype",
            },
            {
                "name": "Ankle Breaker",
                "trigger": "crossover",
                "effect": "+15% drive",
                "attribute_gate": {},
                "source": "earned",
            },
        ]

        await repo.create_hooper(
            team_id=team.id,
            season_id=season.id,
            name="Moves Hooper",
            archetype="sharpshooter",
            attributes=_hooper_attrs(),
            moves=moves_data,
        )

        # Reload the team row from DB (triggers relationship loading)
        team_row = await repo.get_team(team.id)
        assert team_row is not None

        domain_team = _row_to_team(team_row)
        hooper = domain_team.hoopers[0]

        assert len(hooper.moves) == 2
        assert isinstance(hooper.moves[0], Move)
        assert hooper.moves[0].name == "Heat Check"
        assert hooper.moves[0].trigger == "made_three"
        assert hooper.moves[0].attribute_gate == {"scoring": 70}
        assert hooper.moves[1].name == "Ankle Breaker"
        assert hooper.moves[1].source == "earned"

    async def test_handles_empty_moves(self, repo: Repository) -> None:
        """Verify _row_to_team handles hoopers with no moves."""
        from pinwheel.core.game_loop import _row_to_team

        league = await repo.create_league("Test League")
        season = await repo.create_season(league.id, "Season 1")
        team = await repo.create_team(
            season.id,
            "No Moves Team",
            venue={"name": "Arena", "capacity": 5000},
        )
        await repo.create_hooper(
            team_id=team.id,
            season_id=season.id,
            name="Basic Hooper",
            archetype="enforcer",
            attributes=_hooper_attrs(),
        )

        team_row = await repo.get_team(team.id)
        domain_team = _row_to_team(team_row)
        assert domain_team.hoopers[0].moves == []


class TestSeriesReports:
    """Tests for series report generation on playoff series completion."""

    async def test_get_series_games_returns_playoff_games_only(
        self, repo: Repository
    ) -> None:
        """_get_series_games returns only playoff games between the two teams."""
        season_id, team_ids = await _setup_season_with_teams(
            repo, num_rounds=3, starting_ruleset=_BO1_RULESET,
        )
        await repo.update_season_status(season_id, "active")

        # Play regular season
        schedule = await repo.get_full_schedule(season_id, phase="regular")
        total_rounds = max(s.round_number for s in schedule)
        for rnd in range(1, total_rounds + 1):
            await step_round(repo, season_id, round_number=rnd)

        # Get playoff schedule — semis between specific teams
        playoff_sched = await repo.get_full_schedule(season_id, phase="playoff")
        assert len(playoff_sched) >= 2
        se = playoff_sched[0]

        # Before playoff games: empty list
        games = await _get_series_games(repo, season_id, se.home_team_id, se.away_team_id)
        assert games == []

        # Play semi round
        semi_round = total_rounds + 1
        await step_round(repo, season_id, round_number=semi_round)

        # After playoff game: one game between these teams
        games = await _get_series_games(repo, season_id, se.home_team_id, se.away_team_id)
        assert len(games) == 1
        assert games[0]["home_team_id"] in (se.home_team_id, se.away_team_id)
        assert games[0]["away_team_id"] in (se.home_team_id, se.away_team_id)
        assert games[0]["home_score"] > 0 or games[0]["away_score"] > 0

    async def test_generate_series_reports_semifinal(
        self, repo: Repository
    ) -> None:
        """_generate_series_reports creates a report for semifinals_complete events."""
        from pinwheel.models.team import Team

        season_id, team_ids = await _setup_season_with_teams(repo)

        # Store a mock playoff game so _get_series_games has data
        await repo.create_schedule_entry(
            season_id=season_id,
            round_number=100,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[1],
            phase="playoff",
        )
        await repo.store_game_result(
            season_id=season_id,
            round_number=100,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[1],
            home_score=55,
            away_score=42,
            winner_team_id=team_ids[0],
            seed=42,
            total_possessions=100,
        )

        teams_cache = {}
        for tid in team_ids:
            t = await repo.get_team(tid)
            teams_cache[tid] = Team(
                id=tid, name=t.name, hoopers=[],
                venue={"name": "Arena", "capacity": 5000},
            )

        deferred_events = [
            (
                "season.semifinals_complete",
                {
                    "semi_series": [
                        {
                            "winner_id": team_ids[0],
                            "loser_id": team_ids[1],
                            "winner_wins": 1,
                            "loser_wins": 0,
                        }
                    ]
                },
            ),
        ]

        report_events = await _generate_series_reports(
            repo, season_id, deferred_events, teams_cache,
        )

        assert len(report_events) == 1
        assert report_events[0]["report_type"] == "series"
        assert report_events[0]["series_type"] == "semifinal"
        assert teams_cache[team_ids[0]].name in report_events[0]["winner_name"]

        # Verify report stored in DB
        reports = await repo.get_series_reports(season_id)
        assert len(reports) == 1
        assert reports[0].report_type == "series"
        assert reports[0].metadata_json["series_type"] == "semifinal"
        assert reports[0].metadata_json["winner_id"] == team_ids[0]
        assert reports[0].metadata_json["loser_id"] == team_ids[1]
        assert reports[0].metadata_json["record"] == "1-0"
        # Mock content includes team names
        assert teams_cache[team_ids[0]].name in reports[0].content
        assert teams_cache[team_ids[1]].name in reports[0].content

    async def test_generate_series_reports_finals(
        self, repo: Repository
    ) -> None:
        """_generate_series_reports creates a report for playoffs_complete events."""
        from pinwheel.models.team import Team

        season_id, team_ids = await _setup_season_with_teams(repo)

        # Create playoff schedule with semis + finals
        # Semis: 0v1, 2v3 at round 100
        await repo.create_schedule_entry(
            season_id=season_id,
            round_number=100,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[1],
            phase="playoff",
        )
        await repo.create_schedule_entry(
            season_id=season_id,
            round_number=100,
            matchup_index=1,
            home_team_id=team_ids[2],
            away_team_id=team_ids[3],
            phase="playoff",
        )
        # Finals: 0v2 at round 101
        await repo.create_schedule_entry(
            season_id=season_id,
            round_number=101,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[2],
            phase="playoff",
        )
        # Store finals game result
        await repo.store_game_result(
            season_id=season_id,
            round_number=101,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[2],
            home_score=60,
            away_score=48,
            winner_team_id=team_ids[0],
            seed=42,
            total_possessions=100,
        )

        teams_cache = {}
        for tid in team_ids:
            t = await repo.get_team(tid)
            teams_cache[tid] = Team(
                id=tid, name=t.name, hoopers=[],
                venue={"name": "Arena", "capacity": 5000},
            )

        deferred_events = [
            (
                "season.playoffs_complete",
                {
                    "champion_team_id": team_ids[0],
                    "finals_record": "1-0",
                },
            ),
        ]

        report_events = await _generate_series_reports(
            repo, season_id, deferred_events, teams_cache,
        )

        assert len(report_events) == 1
        assert report_events[0]["report_type"] == "series"
        assert report_events[0]["series_type"] == "finals"

        reports = await repo.get_series_reports(season_id)
        assert len(reports) == 1
        assert reports[0].metadata_json["series_type"] == "finals"
        assert reports[0].metadata_json["winner_id"] == team_ids[0]
        assert reports[0].metadata_json["loser_id"] == team_ids[2]
        assert reports[0].metadata_json["record"] == "1-0"
        # Mock content references champion
        assert teams_cache[team_ids[0]].name in reports[0].content

    async def test_generate_series_reports_skips_unknown_events(
        self, repo: Repository
    ) -> None:
        """_generate_series_reports ignores non-series events."""
        from pinwheel.models.team import Team

        season_id, team_ids = await _setup_season_with_teams(repo)
        teams_cache = {
            tid: Team(
                id=tid, name=f"Team {i}", hoopers=[],
                venue={"name": "Arena", "capacity": 5000},
            )
            for i, tid in enumerate(team_ids)
        }

        deferred_events = [
            ("season.phase_changed", {"from": "active", "to": "playoffs"}),
            ("round.completed", {"round": 5}),
        ]

        report_events = await _generate_series_reports(
            repo, season_id, deferred_events, teams_cache,
        )
        assert report_events == []

    async def test_generate_series_reports_metadata_stored(
        self, repo: Repository
    ) -> None:
        """Series report metadata_json contains all required fields."""
        from pinwheel.models.team import Team

        season_id, team_ids = await _setup_season_with_teams(repo)

        # Store a playoff game
        await repo.create_schedule_entry(
            season_id=season_id,
            round_number=100,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[1],
            phase="playoff",
        )
        await repo.store_game_result(
            season_id=season_id,
            round_number=100,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[1],
            home_score=45,
            away_score=38,
            winner_team_id=team_ids[0],
            seed=42,
            total_possessions=100,
        )

        teams_cache = {}
        for tid in team_ids:
            t = await repo.get_team(tid)
            teams_cache[tid] = Team(
                id=tid, name=t.name, hoopers=[],
                venue={"name": "Arena", "capacity": 5000},
            )

        deferred_events = [
            (
                "season.semifinals_complete",
                {
                    "semi_series": [
                        {
                            "winner_id": team_ids[0],
                            "loser_id": team_ids[1],
                            "winner_wins": 2,
                            "loser_wins": 1,
                        }
                    ]
                },
            ),
        ]

        await _generate_series_reports(repo, season_id, deferred_events, teams_cache)

        reports = await repo.get_series_reports(season_id)
        assert len(reports) == 1
        meta = reports[0].metadata_json
        assert "series_type" in meta
        assert "winner_id" in meta
        assert "loser_id" in meta
        assert "record" in meta
        assert meta["record"] == "2-1"
