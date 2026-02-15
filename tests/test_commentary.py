"""Tests for the AI commentary engine — mock commentary and game loop integration."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.ai.commentary import (
    generate_game_commentary_mock,
    generate_highlight_reel_mock,
)
from pinwheel.ai.report import (
    generate_simulation_report_mock,
)
from pinwheel.core.event_bus import EventBus
from pinwheel.core.game_loop import step_round
from pinwheel.core.narrative import NarrativeContext
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.discord.embeds import (
    build_commentary_embed,
    build_game_result_embed,
    build_round_summary_embed,
    build_schedule_embed,
    build_standings_embed,
    build_team_game_result_embed,
)
from pinwheel.models.game import GameResult, HooperBoxScore, QuarterScore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NUM_TEAMS = 4
_EXPECTED_GAMES_PER_TICK = _NUM_TEAMS // 2  # N/2 games per tick

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_game_result(
    home_score: int = 45,
    away_score: int = 38,
    elam: bool = False,
) -> GameResult:
    """Create a minimal GameResult for testing."""
    return GameResult(
        game_id="g-test-0",
        home_team_id="team-home",
        away_team_id="team-away",
        home_score=home_score,
        away_score=away_score,
        winner_team_id="team-home" if home_score > away_score else "team-away",
        seed=42,
        total_possessions=60,
        elam_activated=elam,
        elam_target_score=55 if elam else None,
        quarter_scores=[
            QuarterScore(quarter=1, home_score=12, away_score=10),
            QuarterScore(quarter=2, home_score=12, away_score=10),
            QuarterScore(quarter=3, home_score=11, away_score=10),
            QuarterScore(quarter=4, home_score=10, away_score=8),
        ],
        box_scores=[
            HooperBoxScore(
                hooper_id="a-1",
                hooper_name="Briar Ashwood",
                team_id="team-home",
                points=20,
                assists=5,
                steals=2,
                turnovers=1,
                field_goals_made=8,
                field_goals_attempted=15,
            ),
            HooperBoxScore(
                hooper_id="a-2",
                hooper_name="Rowan Dusk",
                team_id="team-home",
                points=15,
                assists=3,
                steals=1,
                turnovers=2,
                field_goals_made=6,
                field_goals_attempted=12,
            ),
            HooperBoxScore(
                hooper_id="a-3",
                hooper_name="Shade Twilight",
                team_id="team-home",
                points=10,
                assists=2,
                steals=0,
                turnovers=1,
                field_goals_made=4,
                field_goals_attempted=10,
            ),
            HooperBoxScore(
                hooper_id="a-4",
                hooper_name="Kai Sunder",
                team_id="team-away",
                points=18,
                assists=4,
                steals=3,
                turnovers=2,
                field_goals_made=7,
                field_goals_attempted=14,
            ),
            HooperBoxScore(
                hooper_id="a-5",
                hooper_name="Zephyr Flame",
                team_id="team-away",
                points=12,
                assists=2,
                steals=1,
                turnovers=3,
                field_goals_made=5,
                field_goals_attempted=11,
            ),
            HooperBoxScore(
                hooper_id="a-6",
                hooper_name="Nix Cinder",
                team_id="team-away",
                points=8,
                assists=1,
                steals=0,
                turnovers=1,
                field_goals_made=3,
                field_goals_attempted=9,
            ),
        ],
    )


def _make_home_team():
    from pinwheel.models.team import Hooper, PlayerAttributes, Team, Venue

    attrs = PlayerAttributes(
        scoring=50,
        passing=40,
        defense=35,
        speed=45,
        stamina=40,
        iq=50,
        ego=30,
        chaotic_alignment=40,
        fate=30,
    )
    return Team(
        id="team-home",
        name="Rose City Thorns",
        venue=Venue(name="Thorn Arena", capacity=5000),
        hoopers=[
            Hooper(
                id="a-1",
                name="Briar Ashwood",
                team_id="team-home",
                archetype="sharpshooter",
                attributes=attrs,
            ),
            Hooper(
                id="a-2",
                name="Rowan Dusk",
                team_id="team-home",
                archetype="playmaker",
                attributes=attrs,
            ),
            Hooper(
                id="a-3",
                name="Shade Twilight",
                team_id="team-home",
                archetype="enforcer",
                attributes=attrs,
            ),
        ],
    )


def _make_away_team():
    from pinwheel.models.team import Hooper, PlayerAttributes, Team, Venue

    attrs = PlayerAttributes(
        scoring=50,
        passing=40,
        defense=35,
        speed=45,
        stamina=40,
        iq=50,
        ego=30,
        chaotic_alignment=40,
        fate=30,
    )
    return Team(
        id="team-away",
        name="Burnside Breakers",
        venue=Venue(name="Breaker Court", capacity=4000),
        hoopers=[
            Hooper(
                id="a-4",
                name="Kai Sunder",
                team_id="team-away",
                archetype="sharpshooter",
                attributes=attrs,
            ),
            Hooper(
                id="a-5",
                name="Zephyr Flame",
                team_id="team-away",
                archetype="playmaker",
                attributes=attrs,
            ),
            Hooper(
                id="a-6",
                name="Nix Cinder",
                team_id="team-away",
                archetype="enforcer",
                attributes=attrs,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Mock game commentary tests
# ---------------------------------------------------------------------------


class TestGameCommentaryMock:
    def test_generates_readable_text(self) -> None:
        result = _make_game_result()
        home = _make_home_team()
        away = _make_away_team()

        commentary = generate_game_commentary_mock(result, home, away)

        assert len(commentary) > 50
        assert "Rose City Thorns" in commentary
        assert "Burnside Breakers" in commentary

    def test_references_hooper_names(self) -> None:
        result = _make_game_result()
        home = _make_home_team()
        away = _make_away_team()

        commentary = generate_game_commentary_mock(result, home, away)

        # Top scorer is Briar Ashwood (20pts)
        assert "Briar Ashwood" in commentary

    def test_references_scores(self) -> None:
        result = _make_game_result(home_score=55, away_score=48)
        home = _make_home_team()
        away = _make_away_team()

        commentary = generate_game_commentary_mock(result, home, away)

        assert "55" in commentary
        assert "48" in commentary

    def test_close_game_narrative(self) -> None:
        result = _make_game_result(home_score=42, away_score=41)
        home = _make_home_team()
        away = _make_away_team()

        commentary = generate_game_commentary_mock(result, home, away)

        assert "nail-biter" in commentary.lower() or "either way" in commentary.lower()

    def test_blowout_narrative(self) -> None:
        result = _make_game_result(home_score=70, away_score=40)
        home = _make_home_team()
        away = _make_away_team()

        commentary = generate_game_commentary_mock(result, home, away)

        assert "statement" in commentary.lower() or "dismantled" in commentary.lower()

    def test_elam_ending_mentioned(self) -> None:
        result = _make_game_result(home_score=55, away_score=50, elam=True)
        home = _make_home_team()
        away = _make_away_team()

        commentary = generate_game_commentary_mock(result, home, away)

        assert "Elam" in commentary
        assert "55" in commentary  # target score

    def test_multiple_paragraphs(self) -> None:
        result = _make_game_result()
        home = _make_home_team()
        away = _make_away_team()

        commentary = generate_game_commentary_mock(result, home, away)

        # Should have at least 2 paragraphs
        paragraphs = [p for p in commentary.split("\n\n") if p.strip()]
        assert len(paragraphs) >= 2


# ---------------------------------------------------------------------------
# Playoff commentary tests
# ---------------------------------------------------------------------------


class TestPlayoffCommentaryMock:
    def test_semifinal_mentions_playoff(self) -> None:
        result = _make_game_result()
        home = _make_home_team()
        away = _make_away_team()

        commentary = generate_game_commentary_mock(
            result, home, away, playoff_context="semifinal"
        )

        assert "playoff" in commentary.lower() or "semifinal" in commentary.lower()
        assert "go home" in commentary.lower() or "finals" in commentary.lower()

    def test_finals_mentions_championship(self) -> None:
        result = _make_game_result()
        home = _make_home_team()
        away = _make_away_team()

        commentary = generate_game_commentary_mock(
            result, home, away, playoff_context="finals"
        )

        assert "champion" in commentary.lower()

    def test_finals_has_champion_closing(self) -> None:
        result = _make_game_result()
        home = _make_home_team()
        away = _make_away_team()

        commentary = generate_game_commentary_mock(
            result, home, away, playoff_context="finals"
        )

        # Should mention confetti/champion in closing
        assert "confetti" in commentary.lower() or "champion" in commentary.lower()
        # Winner should be named
        assert "Rose City Thorns" in commentary

    def test_semifinal_close_game_narrative(self) -> None:
        result = _make_game_result(home_score=42, away_score=41)
        home = _make_home_team()
        away = _make_away_team()

        commentary = generate_game_commentary_mock(
            result, home, away, playoff_context="semifinal"
        )

        # Should reference the stakes
        assert "semifinal" in commentary.lower() or "go home" in commentary.lower()

    def test_no_playoff_context_unchanged(self) -> None:
        """Regular season commentary should not mention playoffs."""
        result = _make_game_result()
        home = _make_home_team()
        away = _make_away_team()

        commentary = generate_game_commentary_mock(result, home, away)

        assert "playoff" not in commentary.lower()
        assert "semifinal" not in commentary.lower()
        assert "champion" not in commentary.lower()
        assert "finals" not in commentary.lower()

    def test_more_paragraphs_in_playoffs(self) -> None:
        """Playoff commentary should have more content than regular season."""
        result = _make_game_result()
        home = _make_home_team()
        away = _make_away_team()

        regular = generate_game_commentary_mock(result, home, away)
        playoff = generate_game_commentary_mock(
            result, home, away, playoff_context="finals"
        )

        regular_paras = [p for p in regular.split("\n\n") if p.strip()]
        playoff_paras = [p for p in playoff.split("\n\n") if p.strip()]
        assert len(playoff_paras) > len(regular_paras)


# ---------------------------------------------------------------------------
# Playoff highlight reel tests
# ---------------------------------------------------------------------------


class TestPlayoffHighlightReelMock:
    def test_semifinal_highlights(self) -> None:
        summaries = [
            {
                "home_team": "Rose City Thorns",
                "away_team": "Burnside Breakers",
                "home_score": 55,
                "away_score": 48,
                "elam_activated": False,
            },
            {
                "home_team": "Sellwood Herons",
                "away_team": "Alberta Hammers",
                "home_score": 50,
                "away_score": 45,
                "elam_activated": False,
            },
        ]

        reel = generate_highlight_reel_mock(
            summaries, round_number=10, playoff_context="semifinal"
        )

        assert "semifinal" in reel.lower()
        assert "finals await" in reel.lower() or "finals" in reel.lower()

    def test_finals_highlights(self) -> None:
        summaries = [
            {
                "home_team": "Rose City Thorns",
                "away_team": "Sellwood Herons",
                "home_score": 60,
                "away_score": 52,
                "elam_activated": False,
            },
        ]

        reel = generate_highlight_reel_mock(
            summaries, round_number=11, playoff_context="finals"
        )

        assert "champion" in reel.lower()
        assert "Rose City Thorns" in reel

    def test_no_playoff_context_unchanged(self) -> None:
        summaries = [
            {
                "home_team": "A",
                "away_team": "B",
                "home_score": 50,
                "away_score": 45,
                "elam_activated": False,
            }
        ]

        reel = generate_highlight_reel_mock(summaries, round_number=3)

        assert "semifinal" not in reel.lower()
        assert "champion" not in reel.lower()
        assert "playoff" not in reel.lower()


# ---------------------------------------------------------------------------
# Mock highlight reel tests
# ---------------------------------------------------------------------------


class TestHighlightReelMock:
    def test_single_game(self) -> None:
        summaries = [
            {
                "home_team": "Rose City Thorns",
                "away_team": "Burnside Breakers",
                "home_score": 55,
                "away_score": 48,
                "elam_activated": False,
            }
        ]

        reel = generate_highlight_reel_mock(summaries, round_number=3)

        assert "Rose City Thorns" in reel
        assert "Burnside Breakers" in reel
        assert "Round 3" in reel

    def test_multiple_games(self) -> None:
        summaries = [
            {
                "home_team": "Rose City Thorns",
                "away_team": "Burnside Breakers",
                "home_score": 55,
                "away_score": 48,
                "elam_activated": False,
            },
            {
                "home_team": "Sellwood Herons",
                "away_team": "Alberta Hammers",
                "home_score": 42,
                "away_score": 41,
                "elam_activated": False,
            },
        ]

        reel = generate_highlight_reel_mock(summaries, round_number=5)

        assert "Rose City Thorns" in reel
        assert "Sellwood Herons" in reel or "Alberta Hammers" in reel
        assert "Round 5" in reel
        assert "2 games" in reel

    def test_elam_game_highlighted(self) -> None:
        summaries = [
            {
                "home_team": "Thorns",
                "away_team": "Breakers",
                "home_score": 55,
                "away_score": 50,
                "elam_activated": True,
            }
        ]

        reel = generate_highlight_reel_mock(summaries, round_number=1)

        assert "Elam" in reel

    def test_blowout_noted(self) -> None:
        summaries = [
            {
                "home_team": "Thorns",
                "away_team": "Breakers",
                "home_score": 70,
                "away_score": 40,
                "elam_activated": False,
            }
        ]

        reel = generate_highlight_reel_mock(summaries, round_number=2)

        assert "blew out" in reel.lower() or "never close" in reel.lower()

    def test_close_game_noted(self) -> None:
        summaries = [
            {
                "home_team": "Thorns",
                "away_team": "Breakers",
                "home_score": 42,
                "away_score": 41,
                "elam_activated": False,
            }
        ]

        reel = generate_highlight_reel_mock(summaries, round_number=2)

        assert "razor" in reel.lower() or "every possession" in reel.lower()

    def test_empty_round(self) -> None:
        reel = generate_highlight_reel_mock([], round_number=7)

        assert "Round 7" in reel
        assert "No games" in reel

    def test_total_points_reported(self) -> None:
        summaries = [
            {
                "home_team": "A",
                "away_team": "B",
                "home_score": 50,
                "away_score": 45,
                "elam_activated": False,
            },
            {
                "home_team": "C",
                "away_team": "D",
                "home_score": 60,
                "away_score": 55,
                "elam_activated": False,
            },
        ]

        reel = generate_highlight_reel_mock(summaries, round_number=4)

        assert "210" in reel  # 50+45+60+55


# ---------------------------------------------------------------------------
# Commentary embed tests
# ---------------------------------------------------------------------------


class TestCommentaryEmbed:
    def test_build_commentary_embed(self) -> None:
        import discord

        game_data = {
            "home_team": "Rose City Thorns",
            "away_team": "Burnside Breakers",
            "home_score": 55,
            "away_score": 48,
            "commentary": "The Thorns dominated from start to finish.",
        }

        embed = build_commentary_embed(game_data)

        assert isinstance(embed, discord.Embed)
        assert "Rose City Thorns" in embed.title
        assert "Burnside Breakers" in embed.title
        assert "55" in embed.title
        assert "48" in embed.title
        assert "Thorns dominated" in (embed.description or "")

    def test_build_commentary_embed_no_commentary(self) -> None:
        game_data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 40,
            "away_score": 35,
        }

        embed = build_commentary_embed(game_data)

        assert "No commentary available" in (embed.description or "")


# ---------------------------------------------------------------------------
# Game loop integration tests
# ---------------------------------------------------------------------------


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
    """Create a league, season, _NUM_TEAMS teams with 3 hoopers each, and a schedule."""
    league = await repo.create_league("Commentary Test League")
    season = await repo.create_season(
        league.id,
        "Season 1",
        starting_ruleset={"quarter_minutes": 3},
    )

    team_ids = []
    for i in range(_NUM_TEAMS):
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


class TestCommentaryGameLoopIntegration:
    async def test_commentary_in_game_summaries(self, repo: Repository) -> None:
        """Commentary should be present in game_summaries after step_round."""
        season_id, _ = await _setup_season_with_teams(repo)

        result = await step_round(repo, season_id, round_number=1)

        assert len(result.games) == _EXPECTED_GAMES_PER_TICK
        for game in result.games:
            assert "commentary" in game
            assert len(game["commentary"]) > 20  # not empty placeholder
            # Mock commentary should reference team names
            home_in = game["home_team"] in game["commentary"]
            away_in = game["away_team"] in game["commentary"]
            assert home_in or away_in

    async def test_commentary_does_not_break_loop(self, repo: Repository) -> None:
        """Even if commentary generation somehow fails, the game loop should complete."""
        season_id, _ = await _setup_season_with_teams(repo)

        # Run step_round — it should complete without errors
        result = await step_round(repo, season_id, round_number=1)

        # Core game loop functionality should still work
        assert result.round_number == 1
        assert len(result.games) == _EXPECTED_GAMES_PER_TICK
        assert len(result.reports) >= 2  # sim + gov reports

    async def test_event_bus_receives_commentary(self, repo: Repository) -> None:
        """The game.completed event should include commentary."""
        season_id, _ = await _setup_season_with_teams(repo)
        bus = EventBus()
        received = []

        async with bus.subscribe(None) as sub:
            await step_round(repo, season_id, round_number=1, event_bus=bus)
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        game_events = [e for e in received if e["type"] == "game.completed"]
        assert len(game_events) == _EXPECTED_GAMES_PER_TICK
        for event in game_events:
            assert "commentary" in event["data"]

    async def test_round_completed_has_highlight_reel(self, repo: Repository) -> None:
        """The round.completed event should include the highlight reel."""
        season_id, _ = await _setup_season_with_teams(repo)
        bus = EventBus()
        received = []

        async with bus.subscribe(None) as sub:
            await step_round(repo, season_id, round_number=1, event_bus=bus)
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        round_events = [e for e in received if e["type"] == "round.completed"]
        assert len(round_events) == 1
        assert "highlight_reel" in round_events[0]["data"]
        assert len(round_events[0]["data"]["highlight_reel"]) > 20


# ---------------------------------------------------------------------------
# Game Richness — Discord embed playoff awareness tests
# ---------------------------------------------------------------------------


class TestGameResultEmbedPlayoff:
    """Game result embeds should reflect playoff context."""

    def test_regular_season_no_playoff_label(self) -> None:
        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 55,
            "away_score": 48,
            "total_possessions": 60,
        }
        embed = build_game_result_embed(data)

        assert "SEMIFINAL" not in embed.title
        assert "CHAMPIONSHIP" not in embed.title
        assert embed.title == "Thorns vs Breakers"

    def test_semifinal_shows_label(self) -> None:
        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 55,
            "away_score": 48,
            "total_possessions": 60,
        }
        embed = build_game_result_embed(data, playoff_context="semifinal")

        assert "SEMIFINAL" in embed.title
        # Should have a Stage field
        stage_fields = [f for f in embed.fields if f.name == "Stage"]
        assert len(stage_fields) == 1
        assert "Semifinal" in stage_fields[0].value

    def test_finals_shows_championship(self) -> None:
        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 55,
            "away_score": 48,
            "total_possessions": 60,
        }
        embed = build_game_result_embed(data, playoff_context="finals")

        assert "CHAMPIONSHIP" in embed.title
        stage_fields = [f for f in embed.fields if f.name == "Stage"]
        assert len(stage_fields) == 1
        assert "Championship" in stage_fields[0].value


class TestStandingsEmbedStreaks:
    """Standings embeds should show streaks when provided."""

    def test_shows_win_streak(self) -> None:
        standings = [
            {"team_name": "Thorns", "team_id": "t1", "wins": 5, "losses": 1},
            {"team_name": "Breakers", "team_id": "t2", "wins": 3, "losses": 3},
        ]
        streaks = {"t1": 5, "t2": -3}
        embed = build_standings_embed(standings, streaks=streaks)

        assert "W5" in (embed.description or "")
        assert "L3" in (embed.description or "")

    def test_no_streak_below_threshold(self) -> None:
        standings = [
            {"team_name": "Thorns", "team_id": "t1", "wins": 2, "losses": 0},
        ]
        streaks = {"t1": 2}  # below 3 threshold
        embed = build_standings_embed(standings, streaks=streaks)

        assert "W2" not in (embed.description or "")

    def test_playoff_title(self) -> None:
        standings = [
            {"team_name": "Thorns", "team_id": "t1", "wins": 5, "losses": 1},
        ]
        embed = build_standings_embed(
            standings, season_phase="semifinal"
        )

        assert "Playoffs" in embed.title

    def test_championship_title(self) -> None:
        standings = [
            {"team_name": "Thorns", "team_id": "t1", "wins": 5, "losses": 1},
        ]
        embed = build_standings_embed(
            standings, season_phase="finals"
        )

        assert "Championship" in embed.title


class TestScheduleEmbed:
    """Schedule embed tests for slot-based format."""

    def test_single_slot_with_time(self) -> None:
        slots = [
            {
                "start_time": "1:00 PM ET",
                "games": [
                    {"home_team_name": "Thorns", "away_team_name": "Breakers"},
                ],
            },
        ]
        embed = build_schedule_embed(slots)

        assert "1:00 PM ET" in embed.description
        assert "Thorns vs Breakers" in embed.description

    def test_multiple_slots(self) -> None:
        slots = [
            {
                "start_time": "1:00 PM ET",
                "games": [{"home_team_name": "A", "away_team_name": "B"}],
            },
            {
                "start_time": "1:30 PM ET",
                "games": [{"home_team_name": "C", "away_team_name": "D"}],
            },
        ]
        embed = build_schedule_embed(slots)

        assert "1:00 PM ET" in embed.description
        assert "1:30 PM ET" in embed.description

    def test_empty_schedule(self) -> None:
        embed = build_schedule_embed([])

        assert "No games scheduled" in embed.description


class TestCommentaryEmbedPlayoff:
    """Commentary embeds should reflect playoff context."""

    def test_regular_no_label(self) -> None:
        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 55,
            "away_score": 48,
            "commentary": "Great game.",
        }
        embed = build_commentary_embed(data)

        assert "CHAMPIONSHIP" not in embed.title
        assert "SEMIFINAL" not in embed.title

    def test_finals_label(self) -> None:
        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 55,
            "away_score": 48,
            "commentary": "What a game.",
        }
        embed = build_commentary_embed(data, playoff_context="finals")

        assert "CHAMPIONSHIP" in embed.title
        assert "CHAMPIONSHIP FINALS" in embed.footer.text

    def test_semifinal_label(self) -> None:
        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 55,
            "away_score": 48,
            "commentary": "What a game.",
        }
        embed = build_commentary_embed(data, playoff_context="semifinal")

        assert "SEMIFINAL" in embed.title


class TestRoundSummaryEmbedPlayoff:
    """Round summary embeds should reflect playoff context."""

    def test_regular_round(self) -> None:
        data = {"round": 5, "games": 2, "reports": 3}
        embed = build_round_summary_embed(data)

        assert embed.title == "Round 5 Complete"

    def test_semifinal_round(self) -> None:
        data = {"round": 10, "games": 2, "reports": 3}
        embed = build_round_summary_embed(
            data, playoff_context="semifinal"
        )

        assert "SEMIFINAL" in embed.title

    def test_finals_with_playoffs_complete(self) -> None:
        data = {
            "round": 11,
            "games": 1,
            "reports": 3,
            "playoffs_complete": True,
        }
        embed = build_round_summary_embed(data, playoff_context="finals")

        assert "CHAMPIONSHIP" in embed.title
        assert "champion" in (embed.description or "").lower()


class TestTeamGameResultEmbedPlayoff:
    """Team-specific game results should reflect playoff context."""

    def test_regular_win(self) -> None:
        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 55,
            "away_score": 48,
            "winner_team_id": "t1",
            "home_team_id": "t1",
        }
        embed = build_team_game_result_embed(data, "t1")

        assert "Victory" in embed.title

    def test_championship_win(self) -> None:
        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 55,
            "away_score": 48,
            "winner_team_id": "t1",
            "home_team_id": "t1",
        }
        embed = build_team_game_result_embed(
            data, "t1", playoff_context="finals"
        )

        assert "CHAMPIONS" in embed.title

    def test_semifinal_loss(self) -> None:
        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 48,
            "away_score": 55,
            "winner_team_id": "t2",
            "home_team_id": "t1",
            "away_team_id": "t2",
        }
        embed = build_team_game_result_embed(
            data, "t1", playoff_context="semifinal"
        )

        assert "Eliminated" in embed.title
        assert "season is over" in embed.title


# ---------------------------------------------------------------------------
# Game Richness — Mock simulation report playoff awareness tests
# ---------------------------------------------------------------------------


class TestSimReportMockPlayoff:
    """Mock simulation reports should reflect playoff context."""

    def test_playoff_report_mentions_phase(self) -> None:
        """A semifinal sim report should mention the playoffs."""
        round_data = {
            "games": [
                {
                    "home_team": "Thorns",
                    "away_team": "Breakers",
                    "home_score": 55,
                    "away_score": 48,
                },
            ]
        }
        narrative = NarrativeContext(
            phase="semifinal",
            season_arc="playoff",
            round_number=10,
        )
        report = generate_simulation_report_mock(
            round_data, "s1", 10, narrative=narrative
        )

        content = report.content.lower()
        assert "semifinal" in content or "playoff" in content

    def test_championship_report_mentions_finals(self) -> None:
        """A championship sim report should reference the finals."""
        round_data = {
            "games": [
                {
                    "home_team": "Thorns",
                    "away_team": "Breakers",
                    "home_score": 55,
                    "away_score": 48,
                },
            ]
        }
        narrative = NarrativeContext(
            phase="finals",
            season_arc="championship",
            round_number=11,
        )
        report = generate_simulation_report_mock(
            round_data, "s1", 11, narrative=narrative
        )

        content = report.content.lower()
        assert "championship" in content or "finals" in content

    def test_regular_season_no_playoff_mention(self) -> None:
        """A regular season sim report should NOT mention playoffs."""
        round_data = {
            "games": [
                {
                    "home_team": "Thorns",
                    "away_team": "Breakers",
                    "home_score": 55,
                    "away_score": 48,
                },
            ]
        }
        narrative = NarrativeContext(
            phase="regular",
            season_arc="mid",
            round_number=5,
            total_rounds=9,
        )
        report = generate_simulation_report_mock(
            round_data, "s1", 5, narrative=narrative
        )

        content = report.content.lower()
        assert "semifinal" not in content
        assert "championship" not in content

    def test_hot_players_mentioned(self) -> None:
        """Hot players from narrative should appear in mock report."""
        round_data = {
            "games": [
                {
                    "home_team": "Thorns",
                    "away_team": "Breakers",
                    "home_score": 55,
                    "away_score": 48,
                },
            ]
        }
        narrative = NarrativeContext(
            phase="regular",
            season_arc="mid",
            round_number=5,
            hot_players=[
                {
                    "name": "Briar Ashwood",
                    "team_name": "Thorns",
                    "stat": "points",
                    "value": 28,
                    "games": 1,
                }
            ],
        )
        report = generate_simulation_report_mock(
            round_data, "s1", 5, narrative=narrative
        )

        assert "Briar Ashwood" in report.content
        assert "28" in report.content

    def test_late_season_arc_mentioned(self) -> None:
        """Late season arc should produce a winding-down note."""
        round_data = {
            "games": [
                {
                    "home_team": "Thorns",
                    "away_team": "Breakers",
                    "home_score": 50,
                    "away_score": 45,
                },
            ]
        }
        narrative = NarrativeContext(
            phase="regular",
            season_arc="late",
            round_number=8,
            total_rounds=9,
        )
        report = generate_simulation_report_mock(
            round_data, "s1", 8, narrative=narrative
        )

        assert "winding down" in report.content.lower()


# ---------------------------------------------------------------------------
# Game Richness — NarrativeContext in mock commentary tests
# ---------------------------------------------------------------------------


class TestCommentaryNarrativeContext:
    """Mock commentary should use NarrativeContext for richer output."""

    def test_win_streak_mentioned(self) -> None:
        result = _make_game_result()
        home = _make_home_team()
        away = _make_away_team()

        narrative = NarrativeContext(
            streaks={"team-home": 5, "team-away": -2},
        )
        commentary = generate_game_commentary_mock(
            result, home, away, narrative=narrative
        )

        assert "5 straight" in commentary or "streak" in commentary.lower()

    def test_losing_streak_mentioned(self) -> None:
        result = _make_game_result(home_score=38, away_score=45)
        home = _make_home_team()
        away = _make_away_team()

        narrative = NarrativeContext(
            streaks={"team-home": -4, "team-away": 2},
        )
        commentary = generate_game_commentary_mock(
            result, home, away, narrative=narrative
        )

        assert "4" in commentary and (
            "dropped" in commentary.lower() or "skid" in commentary.lower()
        )

    def test_rules_narrative_included(self) -> None:
        result = _make_game_result()
        home = _make_home_team()
        away = _make_away_team()

        narrative = NarrativeContext(
            rules_narrative="Three-pointers worth 5 points",
        )
        commentary = generate_game_commentary_mock(
            result, home, away, narrative=narrative
        )

        assert "Three-pointers worth 5" in commentary

    def test_highlight_reel_with_narrative(self) -> None:
        summaries = [
            {
                "home_team": "Thorns",
                "away_team": "Breakers",
                "home_score": 55,
                "away_score": 48,
                "elam_activated": False,
            }
        ]
        narrative = NarrativeContext(
            rules_narrative="Shot clock reduced to 20 seconds",
        )
        reel = generate_highlight_reel_mock(
            summaries, round_number=5, narrative=narrative
        )

        assert "Shot clock reduced to 20 seconds" in reel


# ---------------------------------------------------------------------------
# Game Richness — Mock governance report playoff awareness tests
# ---------------------------------------------------------------------------


class TestGovReportMockPlayoff:
    """Mock governance reports should reflect playoff context."""

    def test_playoff_gov_report_mentions_phase(self) -> None:
        """A semifinal governance report should mention playoffs."""
        from pinwheel.ai.report import generate_governance_report_mock

        gov_data = {
            "proposals": [{"id": "p1", "raw_text": "Change shot clock"}],
            "votes": [{"vote": "yes"}],
            "rules_changed": [],
        }
        narrative = NarrativeContext(
            phase="semifinal",
            season_arc="playoff",
            round_number=10,
        )
        report = generate_governance_report_mock(
            gov_data, "s1", 10, narrative=narrative,
        )

        content = report.content.lower()
        assert "playoff" in content or "elimination" in content

    def test_championship_gov_report_mentions_finals(self) -> None:
        """A championship governance report should reference the finals."""
        from pinwheel.ai.report import generate_governance_report_mock

        gov_data = {
            "proposals": [],
            "votes": [],
            "rules_changed": [],
        }
        narrative = NarrativeContext(
            phase="finals",
            season_arc="championship",
            round_number=11,
        )
        report = generate_governance_report_mock(
            gov_data, "s1", 11, narrative=narrative,
        )

        content = report.content.lower()
        assert "championship" in content or "finals" in content

    def test_regular_season_no_playoff_mention(self) -> None:
        """Regular season governance report should NOT mention playoffs."""
        from pinwheel.ai.report import generate_governance_report_mock

        gov_data = {
            "proposals": [{"id": "p1", "raw_text": "Test"}],
            "votes": [],
            "rules_changed": [],
        }
        narrative = NarrativeContext(
            phase="regular",
            season_arc="mid",
            round_number=5,
        )
        report = generate_governance_report_mock(
            gov_data, "s1", 5, narrative=narrative,
        )

        content = report.content.lower()
        assert "playoff governance" not in content
        assert "championship governance" not in content

    def test_no_narrative_still_works(self) -> None:
        """Governance report without narrative should still generate."""
        from pinwheel.ai.report import generate_governance_report_mock

        gov_data = {
            "proposals": [],
            "votes": [],
            "rules_changed": [],
        }
        report = generate_governance_report_mock(
            gov_data, "s1", 3,
        )

        assert report.content
        assert report.round_number == 3


# ---------------------------------------------------------------------------
# Game Richness — Presenter playoff_context propagation tests
# ---------------------------------------------------------------------------


class TestPresenterPlayoffContext:
    """Presenter should propagate playoff_context through events."""

    async def test_game_starting_includes_playoff_context(self) -> None:
        """game_starting event should include playoff_context from summaries."""
        from pinwheel.core.presenter import PresentationState, _present_full_game

        bus = EventBus()
        state = PresentationState()
        state.game_summaries = [
            {"playoff_context": "semifinals", "home_team": "A", "away_team": "B"},
        ]
        state.is_active = True

        result = _make_game_result()

        received: list[dict] = []

        async with bus.subscribe(None) as sub:
            await _present_full_game(
                game_idx=0,
                game_result=result,
                total_games=1,
                event_bus=bus,
                state=state,
                quarter_replay_seconds=0,
                names={"team-home": "Home", "team-away": "Away"},
                colors={},
                on_game_finished=None,
            )
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        starting_events = [
            e for e in received
            if e["type"] == "presentation.game_starting"
        ]
        assert len(starting_events) == 1
        assert starting_events[0]["data"]["playoff_context"] == "semifinals"

    async def test_game_finished_includes_playoff_context(self) -> None:
        """game_finished event should include playoff_context from summaries."""
        from pinwheel.core.presenter import PresentationState, _present_full_game

        bus = EventBus()
        state = PresentationState()
        state.game_summaries = [
            {
                "playoff_context": "finals",
                "home_team": "Home",
                "away_team": "Away",
                "winner_team_id": "team-home",
                "elam_activated": False,
                "total_possessions": 60,
                "commentary": "Test",
            },
        ]
        state.is_active = True

        result = _make_game_result()

        received: list[dict] = []

        async with bus.subscribe(None) as sub:
            await _present_full_game(
                game_idx=0,
                game_result=result,
                total_games=1,
                event_bus=bus,
                state=state,
                quarter_replay_seconds=0,
                names={"team-home": "Home", "team-away": "Away"},
                colors={},
                on_game_finished=None,
            )
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        finished_events = [
            e for e in received
            if e["type"] == "presentation.game_finished"
        ]
        assert len(finished_events) == 1
        assert finished_events[0]["data"]["playoff_context"] == "finals"

    async def test_no_summaries_no_playoff_context(self) -> None:
        """Without game_summaries, playoff_context should be None."""
        from pinwheel.core.presenter import PresentationState, _present_full_game

        bus = EventBus()
        state = PresentationState()
        state.game_summaries = []
        state.is_active = True

        result = _make_game_result()

        received: list[dict] = []

        async with bus.subscribe(None) as sub:
            await _present_full_game(
                game_idx=0,
                game_result=result,
                total_games=1,
                event_bus=bus,
                state=state,
                quarter_replay_seconds=0,
                names={"team-home": "Home", "team-away": "Away"},
                colors={},
                on_game_finished=None,
            )
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        starting_events = [
            e for e in received
            if e["type"] == "presentation.game_starting"
        ]
        assert len(starting_events) == 1
        assert starting_events[0]["data"]["playoff_context"] is None

    async def test_round_finished_includes_playoff_context(self) -> None:
        """round_finished event should include playoff_context from summaries."""
        from pinwheel.core.presenter import PresentationState, present_round

        bus = EventBus()
        state = PresentationState()

        result = _make_game_result()
        summaries = [
            {
                "playoff_context": "semifinal",
                "home_team": "Home",
                "away_team": "Away",
                "winner_team_id": "team-home",
                "elam_activated": False,
                "total_possessions": 60,
                "commentary": "Test",
            },
        ]

        received: list[dict] = []

        async with bus.subscribe(None) as sub:
            await present_round(
                game_results=[result],
                event_bus=bus,
                state=state,
                quarter_replay_seconds=0,
                game_summaries=summaries,
            )
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        round_events = [
            e for e in received
            if e["type"] == "presentation.round_finished"
        ]
        assert len(round_events) == 1
        assert round_events[0]["data"]["playoff_context"] == "semifinal"
