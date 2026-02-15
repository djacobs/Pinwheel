"""Tests for the /ask bot search feature.

Covers: query parser (mock), query executor, name resolver, rate limiting,
response formatter, repository extensions (stat_leaders, head_to_head,
games_for_team), and the search result embed builder.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from pinwheel.ai.search import (
    NameResolver,
    QueryPlan,
    QueryResult,
    execute_query,
    format_response_mock,
    parse_query_mock,
)
from pinwheel.config import Settings
from pinwheel.core.event_bus import EventBus
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import (
    Base,
    BoxScoreRow,
    GameResultRow,
    HooperRow,
    LeagueRow,
    SeasonRow,
    TeamRow,
)
from pinwheel.db.repository import Repository
from pinwheel.discord.bot import ASK_COOLDOWN_SECONDS, PinwheelBot
from pinwheel.discord.embeds import build_search_result_embed

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def seeded_engine():
    """Create an in-memory DB with league, season, teams, hoopers, and games."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with get_session(engine) as session:
        # League + Season
        league = LeagueRow(id="league-1", name="Test League")
        session.add(league)
        await session.flush()

        season = SeasonRow(
            id="season-1",
            league_id="league-1",
            name="Season ONE",
            status="active",
            current_ruleset={"three_point_value": 3, "quarter_minutes": 8},
        )
        session.add(season)
        await session.flush()

        # Teams
        team_a = TeamRow(
            id="team-a", season_id="season-1", name="Rose City Thorns", color="#FF0000",
        )
        team_b = TeamRow(id="team-b", season_id="season-1", name="Voltage", color="#00FF00")
        session.add_all([team_a, team_b])
        await session.flush()

        # Hoopers
        hooper_1 = HooperRow(
            id="hooper-1",
            team_id="team-a",
            season_id="season-1",
            name="Rivera",
            archetype="Sharpshooter",
            attributes={"shooting": 85, "speed": 70},
        )
        hooper_2 = HooperRow(
            id="hooper-2",
            team_id="team-a",
            season_id="season-1",
            name="Okafor",
            archetype="Rim Protector",
            attributes={"defense": 90, "strength": 85},
        )
        hooper_3 = HooperRow(
            id="hooper-3",
            team_id="team-b",
            season_id="season-1",
            name="Chen",
            archetype="Playmaker",
            attributes={"passing": 88, "speed": 82},
        )
        session.add_all([hooper_1, hooper_2, hooper_3])
        await session.flush()

        # Game results
        game_1 = GameResultRow(
            id="game-1",
            season_id="season-1",
            round_number=1,
            matchup_index=0,
            home_team_id="team-a",
            away_team_id="team-b",
            home_score=55,
            away_score=48,
            winner_team_id="team-a",
            seed=42,
            total_possessions=80,
        )
        game_2 = GameResultRow(
            id="game-2",
            season_id="season-1",
            round_number=2,
            matchup_index=0,
            home_team_id="team-b",
            away_team_id="team-a",
            home_score=60,
            away_score=52,
            winner_team_id="team-b",
            seed=43,
            total_possessions=85,
        )
        session.add_all([game_1, game_2])
        await session.flush()

        # Box scores
        bs1 = BoxScoreRow(
            id="bs-1",
            game_id="game-1",
            hooper_id="hooper-1",
            team_id="team-a",
            points=25,
            assists=5,
            steals=3,
            three_pointers_made=4,
            field_goals_made=10,
            field_goals_attempted=18,
        )
        bs2 = BoxScoreRow(
            id="bs-2",
            game_id="game-1",
            hooper_id="hooper-2",
            team_id="team-a",
            points=20,
            assists=2,
            steals=1,
            three_pointers_made=0,
            field_goals_made=8,
            field_goals_attempted=14,
        )
        bs3 = BoxScoreRow(
            id="bs-3",
            game_id="game-1",
            hooper_id="hooper-3",
            team_id="team-b",
            points=30,
            assists=8,
            steals=2,
            three_pointers_made=3,
            field_goals_made=12,
            field_goals_attempted=20,
        )
        bs4 = BoxScoreRow(
            id="bs-4",
            game_id="game-2",
            hooper_id="hooper-1",
            team_id="team-a",
            points=18,
            assists=3,
            steals=2,
            three_pointers_made=3,
            field_goals_made=7,
            field_goals_attempted=15,
        )
        bs5 = BoxScoreRow(
            id="bs-5",
            game_id="game-2",
            hooper_id="hooper-3",
            team_id="team-b",
            points=35,
            assists=10,
            steals=4,
            three_pointers_made=5,
            field_goals_made=14,
            field_goals_attempted=22,
        )
        session.add_all([bs1, bs2, bs3, bs4, bs5])
        await session.flush()

    yield engine
    await engine.dispose()


@pytest.fixture
def teams() -> list[TeamRow]:
    """Build TeamRow-like objects for NameResolver tests."""
    t1 = MagicMock(spec=TeamRow)
    t1.id = "team-a"
    t1.name = "Rose City Thorns"
    t2 = MagicMock(spec=TeamRow)
    t2.id = "team-b"
    t2.name = "Voltage"
    return [t1, t2]


@pytest.fixture
def hoopers() -> list[HooperRow]:
    """Build HooperRow-like objects for NameResolver tests."""
    h1 = MagicMock(spec=HooperRow)
    h1.id = "hooper-1"
    h1.name = "Rivera"
    h1.team_id = "team-a"
    h2 = MagicMock(spec=HooperRow)
    h2.id = "hooper-2"
    h2.name = "Okafor"
    h2.team_id = "team-a"
    h3 = MagicMock(spec=HooperRow)
    h3.id = "hooper-3"
    h3.name = "Chen"
    h3.team_id = "team-b"
    return [h1, h2, h3]


# ---------------------------------------------------------------------------
# Query Parser Mock — Golden Examples
# ---------------------------------------------------------------------------


class TestParseQueryMock:
    """Test the keyword-based query parser with golden examples."""

    def test_standings(self) -> None:
        plan = parse_query_mock("what are the standings?")
        assert plan.query_type == "standings"

    def test_standings_rankings(self) -> None:
        plan = parse_query_mock("show me the rankings")
        assert plan.query_type == "standings"

    def test_stat_leaders_scoring(self) -> None:
        plan = parse_query_mock("who leads the league in scoring?")
        assert plan.query_type == "stat_leaders"
        assert plan.stat == "points"

    def test_stat_leaders_assists(self) -> None:
        plan = parse_query_mock("who has the most assists?")
        assert plan.query_type == "stat_leaders"
        assert plan.stat == "assists"

    def test_stat_leaders_steals(self) -> None:
        plan = parse_query_mock("top 3 steals leaders")
        assert plan.query_type == "stat_leaders"
        assert plan.stat == "steals"
        assert plan.limit == 3

    def test_stat_leaders_threes(self) -> None:
        plan = parse_query_mock("who has the best 3pt shooting?")
        assert plan.query_type == "stat_leaders"
        assert plan.stat == "three_pointers_made"

    def test_team_record(self) -> None:
        plan = parse_query_mock("what is the Thorns record?")
        assert plan.query_type == "team_record"

    def test_last_game(self) -> None:
        plan = parse_query_mock("what was the last game?")
        assert plan.query_type == "last_game"

    def test_schedule(self) -> None:
        plan = parse_query_mock("what's the schedule?")
        assert plan.query_type == "schedule"

    def test_rules(self) -> None:
        plan = parse_query_mock("what are the current rules?")
        assert plan.query_type == "rules_current"

    def test_roster(self) -> None:
        plan = parse_query_mock("show me the Thorns roster")
        assert plan.query_type == "team_roster"

    def test_head_to_head(self) -> None:
        plan = parse_query_mock("Thorns vs Voltage")
        assert plan.query_type == "head_to_head"
        assert plan.team_a_name == "thorns"
        assert plan.team_b_name == "voltage"

    def test_head_to_head_against(self) -> None:
        plan = parse_query_mock("Thorns against Voltage")
        assert plan.query_type == "head_to_head"

    def test_proposals(self) -> None:
        plan = parse_query_mock("show me the proposals")
        assert plan.query_type == "proposals"

    def test_hooper_stats(self) -> None:
        plan = parse_query_mock("stats for Rivera")
        assert plan.query_type == "hooper_stats"
        # Parser lowercases the question; NameResolver handles case matching
        assert plan.hooper_name is not None
        assert "rivera" in plan.hooper_name.lower()

    def test_unknown(self) -> None:
        plan = parse_query_mock("tell me a joke")
        assert plan.query_type == "unknown"

    def test_top_limit_extraction(self) -> None:
        plan = parse_query_mock("top 10 scorers")
        assert plan.query_type == "stat_leaders"
        assert plan.limit == 10

    def test_limit_capped_at_25(self) -> None:
        plan = parse_query_mock("top 100 scorers")
        assert plan.query_type == "stat_leaders"
        assert plan.limit == 25


# ---------------------------------------------------------------------------
# Name Resolver
# ---------------------------------------------------------------------------


class TestNameResolver:
    """Test team and hooper name resolution."""

    def test_exact_match(self, teams: list[TeamRow], hoopers: list[HooperRow]) -> None:
        resolver = NameResolver(teams, hoopers)
        team = resolver.resolve_team("Rose City Thorns")
        assert team is not None
        assert team.id == "team-a"

    def test_case_insensitive(self, teams: list[TeamRow], hoopers: list[HooperRow]) -> None:
        resolver = NameResolver(teams, hoopers)
        team = resolver.resolve_team("rose city thorns")
        assert team is not None
        assert team.id == "team-a"

    def test_partial_match(self, teams: list[TeamRow], hoopers: list[HooperRow]) -> None:
        resolver = NameResolver(teams, hoopers)
        team = resolver.resolve_team("Thorns")
        assert team is not None
        assert team.id == "team-a"

    def test_no_match(self, teams: list[TeamRow], hoopers: list[HooperRow]) -> None:
        resolver = NameResolver(teams, hoopers)
        team = resolver.resolve_team("Nonexistent Team")
        assert team is None

    def test_hooper_exact(self, teams: list[TeamRow], hoopers: list[HooperRow]) -> None:
        resolver = NameResolver(teams, hoopers)
        hooper = resolver.resolve_hooper("Rivera")
        assert hooper is not None
        assert hooper.id == "hooper-1"

    def test_hooper_case_insensitive(
        self, teams: list[TeamRow], hoopers: list[HooperRow]
    ) -> None:
        resolver = NameResolver(teams, hoopers)
        hooper = resolver.resolve_hooper("rivera")
        assert hooper is not None
        assert hooper.id == "hooper-1"

    def test_hooper_no_match(self, teams: list[TeamRow], hoopers: list[HooperRow]) -> None:
        resolver = NameResolver(teams, hoopers)
        hooper = resolver.resolve_hooper("Nonexistent")
        assert hooper is None

    def test_team_name_lookup(self, teams: list[TeamRow], hoopers: list[HooperRow]) -> None:
        resolver = NameResolver(teams, hoopers)
        name = resolver.team_name("team-a")
        assert name == "Rose City Thorns"

    def test_team_name_missing(self, teams: list[TeamRow], hoopers: list[HooperRow]) -> None:
        resolver = NameResolver(teams, hoopers)
        name = resolver.team_name("team-zzz")
        assert name == "team-zzz"  # Falls back to ID


# ---------------------------------------------------------------------------
# Repository Extensions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_stat_leaders(seeded_engine: object) -> None:
    """stat_leaders aggregates box score stats correctly."""
    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        leaders = await repo.get_stat_leaders("season-1", "points", limit=3)

    assert len(leaders) <= 3
    # Chen scored 30 + 35 = 65, Rivera scored 25 + 18 = 43
    assert leaders[0]["hooper_id"] == "hooper-3"  # Chen
    assert leaders[0]["total"] == 65
    assert leaders[1]["hooper_id"] == "hooper-1"  # Rivera
    assert leaders[1]["total"] == 43


@pytest.mark.asyncio
async def test_get_stat_leaders_assists(seeded_engine: object) -> None:
    """stat_leaders works for assists too."""
    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        leaders = await repo.get_stat_leaders("season-1", "assists", limit=5)

    # Chen: 8 + 10 = 18, Rivera: 5 + 3 = 8, Okafor: 2
    assert leaders[0]["hooper_id"] == "hooper-3"
    assert leaders[0]["total"] == 18


@pytest.mark.asyncio
async def test_get_stat_leaders_invalid_stat(seeded_engine: object) -> None:
    """stat_leaders returns empty list for invalid stat names."""
    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        leaders = await repo.get_stat_leaders("season-1", "nonexistent_stat")

    assert leaders == []


@pytest.mark.asyncio
async def test_get_head_to_head(seeded_engine: object) -> None:
    """head_to_head returns games between two teams."""
    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        games = await repo.get_head_to_head("season-1", "team-a", "team-b")

    assert len(games) == 2
    assert games[0].round_number == 1
    assert games[1].round_number == 2


@pytest.mark.asyncio
async def test_get_head_to_head_no_games(seeded_engine: object) -> None:
    """head_to_head returns empty list when teams haven't played."""
    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        games = await repo.get_head_to_head("season-1", "team-a", "team-nonexistent")

    assert games == []


@pytest.mark.asyncio
async def test_get_games_for_team(seeded_engine: object) -> None:
    """games_for_team returns all games involving a team."""
    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        games = await repo.get_games_for_team("season-1", "team-a")

    assert len(games) == 2
    # Both games involve team-a (once as home, once as away)
    for g in games:
        assert g.home_team_id == "team-a" or g.away_team_id == "team-a"


@pytest.mark.asyncio
async def test_get_games_for_team_no_games(seeded_engine: object) -> None:
    """games_for_team returns empty list for nonexistent team."""
    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        games = await repo.get_games_for_team("season-1", "team-nonexistent")

    assert games == []


# ---------------------------------------------------------------------------
# Query Executor — against seeded DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_standings(seeded_engine: object) -> None:
    """Execute a standings query."""
    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        teams = await repo.get_teams_for_season("season-1")
        all_hoopers = []
        for t in teams:
            all_hoopers.extend(t.hoopers)
        resolver = NameResolver(teams, all_hoopers)

        plan = QueryPlan(query_type="standings")
        result = await execute_query(plan, repo, "season-1", resolver)

    assert result.query_type == "standings"
    assert result.error is None
    standings = result.data.get("standings", [])
    assert len(standings) == 2


@pytest.mark.asyncio
async def test_execute_stat_leaders(seeded_engine: object) -> None:
    """Execute a stat leaders query."""
    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        teams = await repo.get_teams_for_season("season-1")
        all_hoopers = []
        for t in teams:
            all_hoopers.extend(t.hoopers)
        resolver = NameResolver(teams, all_hoopers)

        plan = QueryPlan(query_type="stat_leaders", stat="points", limit=3)
        result = await execute_query(plan, repo, "season-1", resolver)

    assert result.query_type == "stat_leaders"
    leaders = result.data.get("leaders", [])
    assert len(leaders) >= 2
    # Top scorer should be Chen with 65
    assert leaders[0]["hooper_name"] == "Chen"


@pytest.mark.asyncio
async def test_execute_head_to_head(seeded_engine: object) -> None:
    """Execute a head-to-head query."""
    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        teams = await repo.get_teams_for_season("season-1")
        all_hoopers = []
        for t in teams:
            all_hoopers.extend(t.hoopers)
        resolver = NameResolver(teams, all_hoopers)

        plan = QueryPlan(
            query_type="head_to_head",
            team_a_name="Rose City Thorns",
            team_b_name="Voltage",
        )
        result = await execute_query(plan, repo, "season-1", resolver)

    assert result.query_type == "head_to_head"
    assert result.data.get("a_wins") == 1
    assert result.data.get("b_wins") == 1
    assert len(result.data.get("games", [])) == 2


@pytest.mark.asyncio
async def test_execute_last_game(seeded_engine: object) -> None:
    """Execute a last game query."""
    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        teams = await repo.get_teams_for_season("season-1")
        resolver = NameResolver(teams)

        plan = QueryPlan(query_type="last_game")
        result = await execute_query(plan, repo, "season-1", resolver)

    assert result.query_type == "last_game"
    assert result.data.get("round") == 2


@pytest.mark.asyncio
async def test_execute_rules_current(seeded_engine: object) -> None:
    """Execute a rules query."""
    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        resolver = NameResolver([])

        plan = QueryPlan(query_type="rules_current")
        result = await execute_query(plan, repo, "season-1", resolver)

    assert result.query_type == "rules_current"
    rules = result.data.get("rules", {})
    assert rules.get("three_point_value") == 3


@pytest.mark.asyncio
async def test_execute_team_roster(seeded_engine: object) -> None:
    """Execute a team roster query."""
    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        teams = await repo.get_teams_for_season("season-1")
        resolver = NameResolver(teams)

        plan = QueryPlan(query_type="team_roster", team_name="Rose City Thorns")
        result = await execute_query(plan, repo, "season-1", resolver)

    assert result.query_type == "team_roster"
    hoopers = result.data.get("hoopers", [])
    assert len(hoopers) == 2
    names = {h["name"] for h in hoopers}
    assert "Rivera" in names
    assert "Okafor" in names


@pytest.mark.asyncio
async def test_execute_unknown(seeded_engine: object) -> None:
    """Execute an unknown query type returns help text."""
    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        resolver = NameResolver([])

        plan = QueryPlan(query_type="unknown")
        result = await execute_query(plan, repo, "season-1", resolver)

    assert result.query_type == "unknown"
    assert "I can answer questions" in str(result.data.get("message", ""))


# ---------------------------------------------------------------------------
# Response Formatter Mock
# ---------------------------------------------------------------------------


class TestFormatResponseMock:
    """Test the mock response formatter produces valid Discord messages."""

    def test_standings_format(self) -> None:
        result = QueryResult(
            query_type="standings",
            data={
                "standings": [
                    {"team_name": "Thorns", "wins": 5, "losses": 2},
                    {"team_name": "Voltage", "wins": 3, "losses": 4},
                ]
            },
        )
        text = format_response_mock("standings", result)
        assert "Thorns" in text
        assert "Voltage" in text
        assert "5W-2L" in text

    def test_error_format(self) -> None:
        result = QueryResult(query_type="standings", error="Something went wrong")
        text = format_response_mock("standings", result)
        assert text == "Something went wrong"

    def test_stat_leaders_format(self) -> None:
        result = QueryResult(
            query_type="stat_leaders",
            data={
                "stat": "points",
                "leaders": [
                    {"hooper_name": "Rivera", "team_name": "Thorns", "total": 43},
                ],
            },
        )
        text = format_response_mock("who leads in scoring", result)
        assert "Rivera" in text
        assert "43" in text

    def test_unknown_format(self) -> None:
        result = QueryResult(
            query_type="unknown",
            data={"message": "I can answer questions about standings."},
        )
        text = format_response_mock("tell me a joke", result)
        assert "I can answer questions" in text

    def test_team_record_format(self) -> None:
        result = QueryResult(
            query_type="team_record",
            data={"team_name": "Thorns", "wins": 3, "losses": 1},
        )
        text = format_response_mock("Thorns record", result)
        assert "Thorns" in text
        assert "3-1" in text

    def test_head_to_head_no_games(self) -> None:
        result = QueryResult(
            query_type="head_to_head",
            data={"team_a": "Thorns", "team_b": "Voltage", "a_wins": 0, "b_wins": 0, "games": []},
        )
        text = format_response_mock("thorns vs voltage", result)
        assert "not played" in text


# ---------------------------------------------------------------------------
# Embed Builder
# ---------------------------------------------------------------------------


class TestSearchResultEmbed:
    """Test the search result embed builder."""

    def test_basic_embed(self) -> None:
        embed = build_search_result_embed(
            "who leads in scoring?",
            "Rivera leads with 43 points.",
            "stat_leaders",
        )
        assert embed.title == "who leads in scoring?"
        assert "Rivera" in str(embed.description)
        assert embed.footer
        assert "Stat Leaders" in str(embed.footer.text)

    def test_long_answer_truncated(self) -> None:
        long_answer = "A" * 5000
        embed = build_search_result_embed("test", long_answer, "standings")
        assert len(str(embed.description)) <= 4010

    def test_unknown_query_type(self) -> None:
        embed = build_search_result_embed("what?", "Some answer", "unknown")
        assert embed.footer
        assert "Unknown" in str(embed.footer.text)


# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------


class TestAskRateLimiting:
    """Test the /ask cooldown logic."""

    def test_cooldown_blocks_rapid_queries(self) -> None:
        """A second query within cooldown should be rejected."""
        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
        )
        event_bus = EventBus()
        bot = PinwheelBot(settings, event_bus)

        # Simulate a recent query
        user_id = "user-123"
        bot._ask_cooldowns[user_id] = time.monotonic()

        # Check that a query within 10s would be blocked
        now = time.monotonic()
        last = bot._ask_cooldowns.get(user_id, 0.0)
        assert now - last < ASK_COOLDOWN_SECONDS

    def test_cooldown_allows_after_expiry(self) -> None:
        """A query after cooldown should be allowed."""
        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
        )
        event_bus = EventBus()
        bot = PinwheelBot(settings, event_bus)

        # Simulate a query from 15 seconds ago
        user_id = "user-456"
        bot._ask_cooldowns[user_id] = time.monotonic() - 15.0

        now = time.monotonic()
        last = bot._ask_cooldowns.get(user_id, 0.0)
        assert now - last >= ASK_COOLDOWN_SECONDS

    def test_different_users_independent(self) -> None:
        """Cooldowns for different users don't interfere."""
        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
        )
        event_bus = EventBus()
        bot = PinwheelBot(settings, event_bus)

        bot._ask_cooldowns["user-A"] = time.monotonic()
        # user-B has no cooldown
        assert "user-B" not in bot._ask_cooldowns


# ---------------------------------------------------------------------------
# Full pipeline integration (mocked AI, real DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_standings(seeded_engine: object) -> None:
    """Full parse -> execute -> format pipeline for a standings query."""
    plan = parse_query_mock("what are the standings?")
    assert plan.query_type == "standings"

    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        teams = await repo.get_teams_for_season("season-1")
        resolver = NameResolver(teams)

        result = await execute_query(plan, repo, "season-1", resolver)

    text = format_response_mock("what are the standings?", result)
    assert "Rose City Thorns" in text or "Voltage" in text

    embed = build_search_result_embed("what are the standings?", text, result.query_type)
    assert embed.title == "what are the standings?"


@pytest.mark.asyncio
async def test_full_pipeline_stat_leaders(seeded_engine: object) -> None:
    """Full pipeline for stat leaders."""
    plan = parse_query_mock("who leads the league in scoring?")
    assert plan.query_type == "stat_leaders"

    async with get_session(seeded_engine) as session:  # type: ignore[arg-type]
        repo = Repository(session)
        teams = await repo.get_teams_for_season("season-1")
        all_hoopers = []
        for t in teams:
            all_hoopers.extend(t.hoopers)
        resolver = NameResolver(teams, all_hoopers)

        result = await execute_query(plan, repo, "season-1", resolver)

    text = format_response_mock("who leads the league in scoring?", result)
    assert "Chen" in text
    assert "65" in text
