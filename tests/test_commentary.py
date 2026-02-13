"""Tests for the AI commentary engine — mock commentary and game loop integration."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.ai.commentary import (
    generate_game_commentary_mock,
    generate_highlight_reel_mock,
)
from pinwheel.core.event_bus import EventBus
from pinwheel.core.game_loop import step_round
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.discord.embeds import build_commentary_embed
from pinwheel.models.game import GameResult, HooperBoxScore, QuarterScore

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
    """Create a league, season, 4 teams with 3 hoopers each, and a schedule."""
    league = await repo.create_league("Commentary Test League")
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

        assert len(result.games) == 2
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
        assert len(result.games) == 2
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
        assert len(game_events) == 2
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
