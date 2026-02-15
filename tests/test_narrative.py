"""Tests for the NarrativeContext module — runtime dramatic awareness."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.ai.commentary import (
    _build_game_context,
    generate_game_commentary_mock,
    generate_highlight_reel_mock,
)
from pinwheel.ai.report import (
    generate_governance_report_mock,
    generate_simulation_report_mock,
)
from pinwheel.core.game_loop import step_round
from pinwheel.core.narrative import (
    NarrativeContext,
    _build_rules_narrative,
    _compute_head_to_head,
    _compute_phase,
    _compute_season_arc,
    _compute_streaks,
    compute_narrative_context,
    format_narrative_for_prompt,
)
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.game import GameResult, HooperBoxScore, QuarterScore
from pinwheel.models.rules import RuleSet
from pinwheel.models.team import Hooper, PlayerAttributes, Team, Venue

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NUM_TEAMS = 4


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


async def _setup_season(
    repo: Repository,
    num_rounds: int = 1,
    starting_ruleset: dict | None = None,
) -> tuple[str, list[str]]:
    """Create a league, season, teams, and schedule."""
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
                team.id,
                season.id,
                f"Hooper {i}-{j}",
                "scorer",
                _hooper_attrs(),
            )

    # Generate schedule
    matchups = generate_round_robin(team_ids, num_rounds=num_rounds)
    for m in matchups:
        await repo.create_schedule_entry(
            season.id,
            m.round_number,
            m.matchup_index,
            m.home_team_id,
            m.away_team_id,
        )

    # Set season active
    await repo.update_season_status(season.id, "active")

    return season.id, team_ids


# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------


class TestComputePhase:
    """Tests for _compute_phase()."""

    def test_active_maps_to_regular(self) -> None:
        assert _compute_phase("active") == "regular"

    def test_playoffs_maps_to_semifinal(self) -> None:
        assert _compute_phase("playoffs") == "semifinal"

    def test_championship_maps_to_championship(self) -> None:
        assert _compute_phase("championship") == "championship"

    def test_offseason_maps_to_offseason(self) -> None:
        assert _compute_phase("offseason") == "offseason"

    def test_unknown_defaults_to_regular(self) -> None:
        assert _compute_phase("wacky_status") == "regular"


class TestComputeSeasonArc:
    """Tests for _compute_season_arc()."""

    def test_early_season(self) -> None:
        assert _compute_season_arc(1, 9, "regular") == "early"

    def test_mid_season(self) -> None:
        assert _compute_season_arc(5, 9, "regular") == "mid"

    def test_late_season(self) -> None:
        assert _compute_season_arc(8, 9, "regular") == "late"

    def test_playoff_phase(self) -> None:
        assert _compute_season_arc(10, 9, "semifinal") == "playoff"

    def test_championship_phase(self) -> None:
        assert _compute_season_arc(12, 9, "championship") == "championship"

    def test_zero_total_rounds(self) -> None:
        assert _compute_season_arc(1, 0, "regular") == "early"


class TestComputeStreaks:
    """Tests for _compute_streaks()."""

    def test_empty_games(self) -> None:
        assert _compute_streaks([]) == {}

    def test_win_streak(self) -> None:
        """Team with 3 consecutive wins should have streak=3."""

        class FakeGame:
            def __init__(self, rd: int, mi: int, ht: str, at: str, wt: str) -> None:
                self.round_number = rd
                self.matchup_index = mi
                self.home_team_id = ht
                self.away_team_id = at
                self.winner_team_id = wt

        games = [
            FakeGame(1, 0, "A", "B", "A"),
            FakeGame(2, 0, "A", "C", "A"),
            FakeGame(3, 0, "A", "D", "A"),
        ]
        streaks = _compute_streaks(games)
        assert streaks["A"] == 3

    def test_loss_streak(self) -> None:
        """Team with 2 consecutive losses should have streak=-2."""

        class FakeGame:
            def __init__(self, rd: int, mi: int, ht: str, at: str, wt: str) -> None:
                self.round_number = rd
                self.matchup_index = mi
                self.home_team_id = ht
                self.away_team_id = at
                self.winner_team_id = wt

        games = [
            FakeGame(1, 0, "A", "B", "B"),
            FakeGame(2, 0, "A", "C", "C"),
        ]
        streaks = _compute_streaks(games)
        assert streaks["A"] == -2

    def test_streak_resets(self) -> None:
        """Streak resets when result changes."""

        class FakeGame:
            def __init__(self, rd: int, mi: int, ht: str, at: str, wt: str) -> None:
                self.round_number = rd
                self.matchup_index = mi
                self.home_team_id = ht
                self.away_team_id = at
                self.winner_team_id = wt

        games = [
            FakeGame(1, 0, "A", "B", "A"),  # win
            FakeGame(2, 0, "A", "C", "C"),  # loss
            FakeGame(3, 0, "A", "D", "A"),  # win
        ]
        streaks = _compute_streaks(games)
        assert streaks["A"] == 1  # only most recent win counts


class TestComputeHeadToHead:
    """Tests for _compute_head_to_head()."""

    def test_no_games(self) -> None:
        h2h = _compute_head_to_head([], "A", "B")
        assert h2h["total_games"] == 0
        assert h2h["wins_a"] == 0
        assert h2h["wins_b"] == 0

    def test_with_games(self) -> None:

        class FakeGame:
            def __init__(self, rd: int, ht: str, at: str, wt: str) -> None:
                self.round_number = rd
                self.home_team_id = ht
                self.away_team_id = at
                self.winner_team_id = wt

        games = [
            FakeGame(1, "A", "B", "A"),
            FakeGame(2, "B", "A", "B"),
            FakeGame(3, "A", "C", "A"),  # different matchup, should be ignored
        ]
        h2h = _compute_head_to_head(games, "A", "B")
        assert h2h["total_games"] == 2
        assert h2h["wins_a"] == 1
        assert h2h["wins_b"] == 1
        assert h2h["last_winner"] == "B"


class TestBuildRulesNarrative:
    """Tests for _build_rules_narrative()."""

    def test_empty_changes(self) -> None:
        assert _build_rules_narrative([]) == ""

    def test_single_change(self) -> None:
        changes = [
            {"parameter": "three_point_value", "new_value": 5, "round_enacted": 3}
        ]
        result = _build_rules_narrative(changes)
        assert "Three Point Value" in result
        assert "5" in result
        assert "Round 3" in result

    def test_narrative_override(self) -> None:
        """When a narrative field is present, it should be used."""
        changes = [
            {
                "parameter": "turnover_chaos_factor",
                "new_value": 1.3,
                "round_enacted": 4,
                "narrative": "The court is now circular",
            }
        ]
        result = _build_rules_narrative(changes)
        assert "circular" in result

    def test_multiple_changes(self) -> None:
        changes = [
            {"parameter": "three_point_value", "new_value": 5, "round_enacted": 3},
            {"parameter": "shot_clock_seconds", "new_value": 20, "round_enacted": 5},
        ]
        result = _build_rules_narrative(changes)
        assert ";" in result  # joined by semicolons


class TestFormatNarrativeForPrompt:
    """Tests for format_narrative_for_prompt()."""

    def test_minimal_context(self) -> None:
        ctx = NarrativeContext(round_number=1)
        result = format_narrative_for_prompt(ctx)
        # No standings, no streaks — should be minimal
        assert result == "" or "Round" not in result  # just empty or very sparse

    def test_playoff_context(self) -> None:
        ctx = NarrativeContext(
            phase="semifinal",
            round_number=10,
            total_rounds=9,
            season_arc="playoff",
        )
        result = format_narrative_for_prompt(ctx)
        assert "SEMIFINAL" in result

    def test_standings_included(self) -> None:
        ctx = NarrativeContext(
            phase="regular",
            round_number=5,
            total_rounds=9,
            standings=[
                {"team_id": "A", "team_name": "Alphas", "wins": 4, "losses": 1, "rank": 1},
                {"team_id": "B", "team_name": "Betas", "wins": 2, "losses": 3, "rank": 2},
            ],
        )
        result = format_narrative_for_prompt(ctx)
        assert "Alphas" in result
        assert "4W-1L" in result

    def test_streaks_shown_for_3_plus(self) -> None:
        ctx = NarrativeContext(
            standings=[
                {"team_id": "A", "team_name": "Alphas", "wins": 5, "losses": 0, "rank": 1},
            ],
            streaks={"A": 5},
        )
        result = format_narrative_for_prompt(ctx)
        assert "W5 streak" in result

    def test_rule_changes_shown(self) -> None:
        ctx = NarrativeContext(
            rules_narrative="Three Point Value set to 5 (changed Round 3)",
        )
        result = format_narrative_for_prompt(ctx)
        assert "Three Point Value" in result

    def test_governance_context(self) -> None:
        ctx = NarrativeContext(
            pending_proposals=2,
            governance_window_open=True,
        )
        result = format_narrative_for_prompt(ctx)
        assert "2 proposal(s) pending" in result
        assert "OPEN" in result


# ---------------------------------------------------------------------------
# Integration tests — database-backed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_narrative_context_empty_season(repo: Repository) -> None:
    """Context for a season with no games should return sensible defaults."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1", {"quarter_minutes": 3})
    await repo.update_season_status(season.id, "active")

    ctx = await compute_narrative_context(repo, season.id, 1)

    assert ctx.round_number == 1
    assert ctx.phase == "regular"
    assert ctx.standings == []
    assert ctx.streaks == {}
    assert ctx.active_rule_changes == []
    assert ctx.pending_proposals == 0


@pytest.mark.asyncio
async def test_compute_narrative_context_with_games(repo: Repository) -> None:
    """After stepping a round, context should have standings and streaks."""
    season_id, team_ids = await _setup_season(repo)

    # Step round 1
    await step_round(repo, season_id, 1)

    ctx = await compute_narrative_context(repo, season_id, 2)

    # Should have standings with all teams
    assert len(ctx.standings) == NUM_TEAMS
    # Every team should have a rank
    ranks = [s["rank"] for s in ctx.standings]
    assert ranks == [1, 2, 3, 4]
    # All teams should have a streak after one round
    # With 4 teams and 1 round-robin, each team plays 3 games in one round,
    # so streaks can range from -3 to 3.
    assert len(ctx.streaks) == NUM_TEAMS
    for tid, streak in ctx.streaks.items():
        assert streak != 0, f"Team {tid} should have a non-zero streak"
        assert -3 <= streak <= 3


@pytest.mark.asyncio
async def test_compute_narrative_context_total_rounds(repo: Repository) -> None:
    """total_rounds should reflect the number of regular-season rounds."""
    season_id, team_ids = await _setup_season(repo, num_rounds=3)

    ctx = await compute_narrative_context(repo, season_id, 1)

    # With 4 teams and 3 round-robins: 3 cycles × 3 ticks = 9 ticks
    assert ctx.total_rounds == 9


@pytest.mark.asyncio
async def test_compute_narrative_context_rule_changes(repo: Repository) -> None:
    """Rule change events should appear in active_rule_changes."""
    season_id, team_ids = await _setup_season(repo)

    # Simulate a rule change event
    await repo.append_event(
        event_type="rule.enacted",
        aggregate_id="prop-1",
        aggregate_type="proposal",
        season_id=season_id,
        payload={
            "parameter": "three_point_value",
            "old_value": 3,
            "new_value": 5,
            "round_enacted": 1,
            "source_proposal_id": "prop-1",
        },
        round_number=1,
    )

    ctx = await compute_narrative_context(repo, season_id, 2)

    assert len(ctx.active_rule_changes) == 1
    assert ctx.active_rule_changes[0]["parameter"] == "three_point_value"
    assert ctx.active_rule_changes[0]["new_value"] == 5
    assert "Three Point Value" in ctx.rules_narrative


@pytest.mark.asyncio
async def test_compute_narrative_context_governance(repo: Repository) -> None:
    """Pending proposals should be counted correctly."""
    season_id, team_ids = await _setup_season(repo)

    # Add a confirmed proposal
    await repo.append_event(
        event_type="proposal.confirmed",
        aggregate_id="prop-1",
        aggregate_type="proposal",
        season_id=season_id,
        payload={"proposal_id": "prop-1"},
        round_number=1,
    )

    ctx = await compute_narrative_context(repo, season_id, 1)
    assert ctx.pending_proposals == 1

    # Resolve the proposal
    await repo.append_event(
        event_type="proposal.passed",
        aggregate_id="prop-1",
        aggregate_type="proposal",
        season_id=season_id,
        payload={"proposal_id": "prop-1"},
        round_number=1,
    )

    ctx2 = await compute_narrative_context(repo, season_id, 2)
    assert ctx2.pending_proposals == 0


@pytest.mark.asyncio
async def test_compute_narrative_context_governance_interval(repo: Repository) -> None:
    """Governance window and next tally should respect interval."""
    season_id, _team_ids = await _setup_season(repo)

    # Interval=2: governance on rounds 2, 4, 6...
    ctx_round1 = await compute_narrative_context(repo, season_id, 1, governance_interval=2)
    assert ctx_round1.governance_window_open is False
    assert ctx_round1.next_tally_round == 2

    ctx_round2 = await compute_narrative_context(repo, season_id, 2, governance_interval=2)
    assert ctx_round2.governance_window_open is True
    assert ctx_round2.next_tally_round == 4


@pytest.mark.asyncio
async def test_narrative_context_wired_into_step_round(repo: Repository) -> None:
    """step_round should compute narrative_context and pass it to AI phase."""
    season_id, team_ids = await _setup_season(repo)

    # Step round 1 — narrative context should be computed
    result = await step_round(repo, season_id, 1)

    # The round should complete successfully
    assert result.round_number == 1
    assert len(result.games) > 0
    assert len(result.reports) > 0


# ---------------------------------------------------------------------------
# Output integration tests
# ---------------------------------------------------------------------------


def _make_teams() -> tuple[Team, Team]:
    """Create minimal Team objects for testing."""
    attrs = PlayerAttributes(
        scoring=50, passing=40, defense=35, speed=45,
        stamina=40, iq=50, ego=30, chaotic_alignment=40, fate=30,
    )
    home = Team(
        id="team-home",
        name="Home Team",
        venue=Venue(name="Home Arena", capacity=5000),
        hoopers=[
            Hooper(
                id="h-1", name="Alpha", team_id="team-home",
                archetype="scorer", attributes=attrs,
            ),
        ],
    )
    away = Team(
        id="team-away",
        name="Away Team",
        venue=Venue(name="Away Arena", capacity=5000),
        hoopers=[
            Hooper(
                id="h-2", name="Bravo", team_id="team-away",
                archetype="scorer", attributes=attrs,
            ),
        ],
    )
    return home, away


def _make_game_result() -> GameResult:
    return GameResult(
        game_id="g-test-0",
        home_team_id="team-home",
        away_team_id="team-away",
        home_score=45,
        away_score=38,
        winner_team_id="team-home",
        seed=42,
        total_possessions=60,
        elam_activated=False,
        elam_target_score=None,
        quarter_scores=[
            QuarterScore(quarter=1, home_score=12, away_score=10),
            QuarterScore(quarter=2, home_score=12, away_score=10),
            QuarterScore(quarter=3, home_score=11, away_score=10),
            QuarterScore(quarter=4, home_score=10, away_score=8),
        ],
        box_scores=[
            HooperBoxScore(
                hooper_id="h-1", hooper_name="Alpha", team_id="team-home",
                points=25, assists=5, steals=2, turnovers=1,
                field_goals_made=10, field_goals_attempted=18,
            ),
            HooperBoxScore(
                hooper_id="h-2", hooper_name="Bravo", team_id="team-away",
                points=20, assists=3, steals=1, turnovers=2,
                field_goals_made=8, field_goals_attempted=15,
            ),
        ],
        possession_log=[],
    )


class TestCommentaryNarrativeIntegration:
    """Tests that commentary uses narrative context."""

    def test_build_game_context_includes_narrative(self) -> None:
        """_build_game_context should include narrative block when provided."""
        home, away = _make_teams()
        result = _make_game_result()
        narrative = NarrativeContext(
            standings=[
                {
                    "team_id": "team-home", "team_name": "Home Team",
                    "wins": 5, "losses": 0, "rank": 1,
                },
            ],
            streaks={"team-home": 5},
            rules_narrative="Three Point Value set to 5",
        )
        context = _build_game_context(
            result, home, away, RuleSet(quarter_minutes=3),
            narrative=narrative,
        )
        assert "Dramatic Context" in context
        assert "Home Team" in context
        assert "W5 streak" in context

    def test_mock_commentary_includes_win_streak(self) -> None:
        """Mock commentary should mention a team's win streak."""
        home, away = _make_teams()
        result = _make_game_result()
        narrative = NarrativeContext(
            streaks={"team-home": 4},
        )
        commentary = generate_game_commentary_mock(
            result, home, away, narrative=narrative,
        )
        assert "4 straight wins" in commentary

    def test_mock_commentary_includes_loss_streak(self) -> None:
        """Mock commentary should mention a team's loss streak."""
        home, away = _make_teams()
        result = _make_game_result()
        narrative = NarrativeContext(
            streaks={"team-away": -3},
        )
        commentary = generate_game_commentary_mock(
            result, home, away, narrative=narrative,
        )
        assert "3 in a row" in commentary

    def test_mock_commentary_includes_rule_changes(self) -> None:
        """Mock commentary should mention rule changes."""
        home, away = _make_teams()
        result = _make_game_result()
        narrative = NarrativeContext(
            rules_narrative="Three-pointers worth 5",
        )
        commentary = generate_game_commentary_mock(
            result, home, away, narrative=narrative,
        )
        assert "Three-pointers worth 5" in commentary

    def test_mock_commentary_without_narrative(self) -> None:
        """Mock commentary should work fine without narrative."""
        home, away = _make_teams()
        result = _make_game_result()
        commentary = generate_game_commentary_mock(
            result, home, away, narrative=None,
        )
        assert "Home Team" in commentary or "Away Team" in commentary


class TestHighlightReelNarrativeIntegration:
    """Tests that highlight reel uses narrative context."""

    def test_mock_highlights_include_rule_changes(self) -> None:
        summaries = [
            {
                "home_team": "Team A",
                "away_team": "Team B",
                "home_score": 50,
                "away_score": 40,
                "elam_activated": False,
            }
        ]
        narrative = NarrativeContext(
            rules_narrative="Shot clock reduced to 10 seconds",
        )
        reel = generate_highlight_reel_mock(
            summaries, 3, narrative=narrative,
        )
        assert "Shot clock" in reel

    def test_mock_highlights_without_narrative(self) -> None:
        summaries = [
            {
                "home_team": "Team A",
                "away_team": "Team B",
                "home_score": 50,
                "away_score": 40,
                "elam_activated": False,
            }
        ]
        reel = generate_highlight_reel_mock(summaries, 3, narrative=None)
        assert "Team A" in reel


class TestReportNarrativeIntegration:
    """Tests that reports use narrative context."""

    def test_sim_report_includes_streaks(self) -> None:
        """Mock sim report should mention notable streaks."""
        round_data = {
            "round_number": 5,
            "games": [
                {
                    "home_team": "Team A", "away_team": "Team B",
                    "home_score": 50, "away_score": 40,
                    "home_team_id": "A", "away_team_id": "B",
                    "winner_team_id": "A",
                }
            ],
        }
        narrative = NarrativeContext(
            standings=[
                {"team_id": "A", "team_name": "Team A", "wins": 5, "losses": 0},
            ],
            streaks={"A": 5},
        )
        report = generate_simulation_report_mock(
            round_data, "s1", 5, narrative=narrative,
        )
        assert "5-game win streak" in report.content

    def test_sim_report_includes_late_season(self) -> None:
        """Mock sim report should mention late season arc."""
        round_data = {
            "round_number": 8,
            "games": [
                {
                    "home_team": "Team A", "away_team": "Team B",
                    "home_score": 50, "away_score": 40,
                    "home_team_id": "A", "away_team_id": "B",
                    "winner_team_id": "A",
                }
            ],
        }
        narrative = NarrativeContext(
            season_arc="late",
            round_number=8,
            total_rounds=9,
        )
        report = generate_simulation_report_mock(
            round_data, "s1", 8, narrative=narrative,
        )
        assert "winding down" in report.content

    def test_gov_report_includes_pending(self) -> None:
        """Mock governance report should mention pending proposals."""
        gov_data: dict = {"proposals": [], "votes": [], "rules_changed": []}
        narrative = NarrativeContext(
            pending_proposals=3,
            governance_window_open=False,
            next_tally_round=4,
        )
        report = generate_governance_report_mock(
            gov_data, "s1", 2, narrative=narrative,
        )
        assert "3 proposal(s) remain pending" in report.content
        assert "Round 4" in report.content

    def test_reports_work_without_narrative(self) -> None:
        """Reports should work fine without narrative context."""
        round_data = {
            "round_number": 1,
            "games": [
                {
                    "home_team": "Team A", "away_team": "Team B",
                    "home_score": 50, "away_score": 40,
                    "home_team_id": "A", "away_team_id": "B",
                    "winner_team_id": "A",
                }
            ],
        }
        report = generate_simulation_report_mock(round_data, "s1", 1, narrative=None)
        assert report.content
        assert report.report_type == "simulation"


# ---------------------------------------------------------------------------
# NarrativeContext dataclass tests
# ---------------------------------------------------------------------------


class TestNarrativeContextDataclass:
    """Tests for the NarrativeContext dataclass itself."""

    def test_defaults(self) -> None:
        ctx = NarrativeContext()
        assert ctx.phase == "regular"
        assert ctx.season_arc == "early"
        assert ctx.round_number == 0
        assert ctx.total_rounds == 0
        assert ctx.standings == []
        assert ctx.streaks == {}
        assert ctx.active_rule_changes == []
        assert ctx.rules_narrative == ""
        assert ctx.head_to_head == {}
        assert ctx.hot_players == []
        assert ctx.governance_window_open is False
        assert ctx.pending_proposals == 0
        assert ctx.next_tally_round is None

    def test_construction_with_values(self) -> None:
        ctx = NarrativeContext(
            phase="finals",
            season_arc="playoff",
            round_number=12,
            total_rounds=9,
            standings=[{"team_id": "A", "wins": 6}],
            streaks={"A": 3},
            rules_narrative="Crazy rules",
            governance_window_open=True,
            pending_proposals=2,
            next_tally_round=13,
        )
        assert ctx.phase == "finals"
        assert ctx.streaks["A"] == 3
        assert ctx.pending_proposals == 2
