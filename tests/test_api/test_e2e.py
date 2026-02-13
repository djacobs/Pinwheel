"""End-to-end test: seed → schedule → simulate → store → API → standings."""

import pytest
from httpx import ASGITransport, AsyncClient

from pinwheel.config import Settings
from pinwheel.core.scheduler import compute_standings, generate_round_robin
from pinwheel.core.seeding import generate_league
from pinwheel.core.simulation import simulate_game
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.main import create_app
from pinwheel.models.rules import DEFAULT_RULESET

# --- Scheduler unit tests ---


class TestRoundRobin:
    def test_8_teams_produces_7_rounds(self):
        team_ids = [f"t-{i}" for i in range(8)]
        matchups = generate_round_robin(team_ids)
        rounds = {m.round_number for m in matchups}
        assert len(rounds) == 7

    def test_4_games_per_round(self):
        team_ids = [f"t-{i}" for i in range(8)]
        matchups = generate_round_robin(team_ids)
        for round_num in range(1, 8):
            games = [m for m in matchups if m.round_number == round_num]
            assert len(games) == 4

    def test_every_team_plays_every_other(self):
        team_ids = [f"t-{i}" for i in range(8)]
        matchups = generate_round_robin(team_ids)
        played: dict[str, set[str]] = {t: set() for t in team_ids}
        for m in matchups:
            played[m.home_team_id].add(m.away_team_id)
            played[m.away_team_id].add(m.home_team_id)
        for t in team_ids:
            assert len(played[t]) == 7, f"{t} only played {len(played[t])} opponents"

    def test_total_games(self):
        team_ids = [f"t-{i}" for i in range(8)]
        matchups = generate_round_robin(team_ids)
        # 8 choose 2 = 28 games
        assert len(matchups) == 28

    def test_two_cycles(self):
        team_ids = [f"t-{i}" for i in range(4)]
        matchups = generate_round_robin(team_ids, num_cycles=2)
        # 4 teams: 3 rounds/cycle * 2 cycles = 6 rounds, 2 games/round = 12 games
        assert len(matchups) == 12

    def test_odd_teams(self):
        team_ids = [f"t-{i}" for i in range(5)]
        matchups = generate_round_robin(team_ids)
        # 5 teams with bye: 5 rounds, 2 games/round = 10 games
        assert len(matchups) == 10


class TestComputeStandings:
    def test_standings_order(self):
        def _game(h, a, hs, as_, w):
            return {
                "home_team_id": h,
                "away_team_id": a,
                "home_score": hs,
                "away_score": as_,
                "winner_team_id": w,
            }

        results = [
            _game("a", "b", 50, 40, "a"),
            _game("b", "c", 45, 40, "b"),
            _game("a", "c", 55, 30, "a"),
        ]
        standings = compute_standings(results)
        assert standings[0]["team_id"] == "a"
        assert standings[0]["wins"] == 2
        assert standings[1]["team_id"] == "b"
        assert standings[2]["team_id"] == "c"


# --- E2E integration test ---


@pytest.fixture
async def app_and_engine():
    """Create test app with in-memory database."""
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    application = create_app(settings)
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    application.state.engine = engine
    yield application, engine
    await engine.dispose()


class TestE2E:
    async def test_full_season_flow(self, app_and_engine):
        """Seed → schedule → simulate all games → store → API → standings."""
        application, engine = app_and_engine

        # 1. Generate league from archetypes
        league = generate_league(num_teams=4, seed=42)
        assert len(league.teams) == 4

        # 2. Store league in database
        async with get_session(engine) as session:
            repo = Repository(session)
            db_league = await repo.create_league(league.name)
            db_season = await repo.create_season(
                db_league.id,
                "Season 1",
                starting_ruleset=DEFAULT_RULESET.model_dump(),
            )

            team_id_map: dict[str, str] = {}  # model team id → db team id
            hooper_id_map: dict[str, str] = {}

            for team in league.teams:
                db_team = await repo.create_team(
                    season_id=db_season.id,
                    name=team.name,
                    color=team.color,
                    motto=team.motto,
                    venue=team.venue.model_dump(),
                )
                team_id_map[team.id] = db_team.id

                for hooper in team.hoopers:
                    db_hooper = await repo.create_hooper(
                        team_id=db_team.id,
                        season_id=db_season.id,
                        name=hooper.name,
                        archetype=hooper.archetype,
                        attributes=hooper.attributes.model_dump(),
                        moves=[m.model_dump() for m in hooper.moves],
                        is_active=hooper.is_starter,
                    )
                    hooper_id_map[hooper.id] = db_hooper.id

        # 3. Generate round-robin schedule
        db_team_ids = list(team_id_map.values())
        schedule = generate_round_robin(db_team_ids)
        assert len(schedule) == 6  # 4 choose 2 = 6 games

        # 4. Simulate all games and store results
        async with get_session(engine) as session:
            repo = Repository(session)
            for matchup in schedule:
                # Find the corresponding league teams for simulation
                home_team = _find_team_by_db_id(league, team_id_map, matchup.home_team_id)
                away_team = _find_team_by_db_id(league, team_id_map, matchup.away_team_id)

                game_seed = 42 * 1000 + matchup.round_number * 100 + matchup.matchup_index
                result = simulate_game(home_team, away_team, DEFAULT_RULESET, seed=game_seed)

                db_game = await repo.store_game_result(
                    season_id=db_season.id,
                    round_number=matchup.round_number,
                    matchup_index=matchup.matchup_index,
                    home_team_id=matchup.home_team_id,
                    away_team_id=matchup.away_team_id,
                    home_score=result.home_score,
                    away_score=result.away_score,
                    winner_team_id=(
                        matchup.home_team_id
                        if result.winner_team_id == home_team.id
                        else matchup.away_team_id
                    ),
                    seed=game_seed,
                    total_possessions=result.total_possessions,
                    quarter_scores=[qs.model_dump() for qs in result.quarter_scores],
                    elam_target=result.elam_target_score,
                )

                for bs in result.box_scores:
                    # Map hooper IDs
                    orig_hooper_id = bs.hooper_id
                    db_hooper_id = hooper_id_map.get(orig_hooper_id, orig_hooper_id)
                    await repo.store_box_score(
                        game_id=db_game.id,
                        hooper_id=db_hooper_id,
                        team_id=(
                            matchup.home_team_id
                            if bs.team_id == home_team.id
                            else matchup.away_team_id
                        ),
                        points=bs.points,
                        field_goals_made=bs.field_goals_made,
                        field_goals_attempted=bs.field_goals_attempted,
                        three_pointers_made=bs.three_pointers_made,
                        three_pointers_attempted=bs.three_pointers_attempted,
                        free_throws_made=bs.free_throws_made,
                        free_throws_attempted=bs.free_throws_attempted,
                        assists=bs.assists,
                        steals=bs.steals,
                        turnovers=bs.turnovers,
                    )

        # 5. Test API endpoints
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Health
            resp = await client.get("/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

            # Teams
            resp = await client.get(f"/api/teams?season_id={db_season.id}")
            assert resp.status_code == 200
            teams_data = resp.json()["data"]
            assert len(teams_data) == 4

            # Single team
            first_team_id = db_team_ids[0]
            resp = await client.get(f"/api/teams/{first_team_id}")
            assert resp.status_code == 200
            team_data = resp.json()["data"]
            assert "hoopers" in team_data
            assert len(team_data["hoopers"]) == 4

            # Standings
            resp = await client.get(f"/api/standings?season_id={db_season.id}")
            assert resp.status_code == 200
            standings = resp.json()["data"]
            assert len(standings) == 4
            total_wins = sum(s["wins"] for s in standings)
            total_losses = sum(s["losses"] for s in standings)
            assert total_wins == 6  # 6 games total
            assert total_losses == 6

            # Game detail (get a game from round 1)
            async with get_session(engine) as session:
                repo = Repository(session)
                r1_games = await repo.get_games_for_round(db_season.id, 1)
                game_id = r1_games[0].id

            resp = await client.get(f"/api/games/{game_id}")
            assert resp.status_code == 200
            game_data = resp.json()["data"]
            assert game_data["home_score"] > 0
            assert game_data["away_score"] > 0

            # Box score
            resp = await client.get(f"/api/games/{game_id}/boxscore")
            assert resp.status_code == 200
            box_data = resp.json()["data"]
            assert len(box_data) > 0

    async def test_404_on_missing_game(self, app_and_engine):
        application, engine = app_and_engine
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/games/nonexistent")
            assert resp.status_code == 404

    async def test_404_on_missing_team(self, app_and_engine):
        application, engine = app_and_engine
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/teams/nonexistent")
            assert resp.status_code == 404


def _find_team_by_db_id(league, team_id_map, db_team_id):
    """Find the original league Team model given a database team ID."""
    for orig_id, mapped_id in team_id_map.items():
        if mapped_id == db_team_id:
            for team in league.teams:
                if team.id == orig_id:
                    return team
    raise ValueError(f"No team found for db_id {db_team_id}")
