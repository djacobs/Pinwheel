"""Tests for playoff bracket API and page — bracket visualization feature."""

import pytest
from httpx import ASGITransport, AsyncClient

from pinwheel.config import Settings
from pinwheel.core.game_loop import step_round
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.main import create_app


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


@pytest.fixture
async def app_client():
    """Create a test app with an in-memory database and httpx client."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_env="development",
    )
    app = create_app(settings)

    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine

    from pinwheel.core.event_bus import EventBus
    from pinwheel.core.presenter import PresentationState

    app.state.event_bus = EventBus()
    app.state.presentation_state = PresentationState()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, engine

    await engine.dispose()


async def _seed_season(engine, num_round_robins: int = 3) -> tuple[str, list[str]]:
    """Create a league with 4 teams and run 1 round of regular season.

    Uses ``num_round_robins`` to control how many round-robins are scheduled.
    Default of 3 means playing round 1 does NOT complete the regular season.
    """
    async with get_session(engine) as session:
        repo = Repository(session)
        league = await repo.create_league("Test League")
        season = await repo.create_season(
            league.id,
            "Season 1",
            starting_ruleset={"quarter_minutes": 3},
        )
        season.status = "active"

        team_ids = []
        for i in range(4):
            team = await repo.create_team(
                season.id,
                f"Team {i + 1}",
                color=f"#{'abcdef'[i]}{'abcdef'[i]}{'abcdef'[i]}",
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

        matchups = generate_round_robin(team_ids, num_rounds=num_round_robins)
        for m in matchups:
            await repo.create_schedule_entry(
                season_id=season.id,
                round_number=m.round_number,
                matchup_index=m.matchup_index,
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
            )

        # Run 1 round
        await step_round(repo, season.id, round_number=1)

        # Mark games as presented
        games = await repo.get_games_for_round(season.id, 1)
        for g in games:
            await repo.mark_game_presented(g.id)

        await session.commit()
        return season.id, team_ids


async def _seed_playoffs(engine) -> tuple[str, list[str]]:
    """Create a season with playoff bracket (semis scheduled, some games played)."""
    async with get_session(engine) as session:
        repo = Repository(session)
        league = await repo.create_league("Playoff League")
        season = await repo.create_season(
            league.id,
            "Playoff Season",
            starting_ruleset={"quarter_minutes": 3},
        )
        season.status = "playoffs"

        team_ids = []
        for i in range(4):
            team = await repo.create_team(
                season.id,
                f"Seed{i + 1}",
                color=f"#{['f9a825','e94560','53d8fb','b794f4'][i]}",
                venue={"name": f"Court {i + 1}", "capacity": 5000},
            )
            team_ids.append(team.id)
            for j in range(3):
                await repo.create_hooper(
                    team_id=team.id,
                    season_id=season.id,
                    name=f"Player-{i + 1}-{j + 1}",
                    archetype="sharpshooter",
                    attributes=_hooper_attrs(),
                )

        # Create some regular-season game results for seeding computation
        # Team 1 beats everyone = seed 1, Team 2 = seed 2, etc.
        for idx, (h, a) in enumerate(
            [
                (team_ids[0], team_ids[3]),
                (team_ids[1], team_ids[2]),
                (team_ids[0], team_ids[2]),
                (team_ids[1], team_ids[3]),
                (team_ids[0], team_ids[1]),
                (team_ids[2], team_ids[3]),
            ]
        ):
            # Winner is always the home team for simplicity
            await repo.store_game_result(
                season_id=season.id,
                round_number=1,
                matchup_index=idx,
                home_team_id=h,
                away_team_id=a,
                home_score=50,
                away_score=40,
                winner_team_id=h,
                seed=42 + idx,
                total_possessions=80,
            )

        # Create playoff schedule: semifinal round at round 10
        # Semi 1: Seed 1 vs Seed 4
        await repo.create_schedule_entry(
            season_id=season.id,
            round_number=10,
            matchup_index=0,
            home_team_id=team_ids[0],  # Seed 1
            away_team_id=team_ids[3],  # Seed 4
            phase="playoff",
        )
        # Semi 2: Seed 2 vs Seed 3
        await repo.create_schedule_entry(
            season_id=season.id,
            round_number=10,
            matchup_index=1,
            home_team_id=team_ids[1],  # Seed 2
            away_team_id=team_ids[2],  # Seed 3
            phase="playoff",
        )

        # Play one semifinal game in each series
        # Semi 1 game 1: Seed 1 wins
        await repo.store_game_result(
            season_id=season.id,
            round_number=10,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[3],
            home_score=55,
            away_score=45,
            winner_team_id=team_ids[0],
            seed=100,
            total_possessions=85,
        )
        # Semi 2 game 1: Seed 3 upsets Seed 2
        await repo.store_game_result(
            season_id=season.id,
            round_number=10,
            matchup_index=1,
            home_team_id=team_ids[1],
            away_team_id=team_ids[2],
            home_score=42,
            away_score=48,
            winner_team_id=team_ids[2],
            seed=101,
            total_possessions=82,
        )

        # Mark games as presented
        all_games = await repo.get_all_games(season.id)
        for g in all_games:
            await repo.mark_game_presented(g.id)

        await session.commit()
        return season.id, team_ids


async def _seed_championship(engine) -> tuple[str, list[str]]:
    """Create a season with completed finals and a champion."""
    async with get_session(engine) as session:
        repo = Repository(session)
        league = await repo.create_league("Championship League")
        season = await repo.create_season(
            league.id,
            "Championship Season",
            starting_ruleset={"quarter_minutes": 3},
        )
        season.status = "championship"
        season.config = {
            "champion_team_id": None,  # Will be set below
        }

        team_ids = []
        for i in range(4):
            team = await repo.create_team(
                season.id,
                f"Finalist{i + 1}",
                color=f"#{'abcdef'[i]}{'abcdef'[i]}{'abcdef'[i]}",
                venue={"name": f"Court {i + 1}", "capacity": 5000},
            )
            team_ids.append(team.id)
            for j in range(3):
                await repo.create_hooper(
                    team_id=team.id,
                    season_id=season.id,
                    name=f"Champ-{i + 1}-{j + 1}",
                    archetype="sharpshooter",
                    attributes=_hooper_attrs(),
                )

        # Regular season games for seeding
        await repo.store_game_result(
            season_id=season.id,
            round_number=1,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[1],
            home_score=50,
            away_score=40,
            winner_team_id=team_ids[0],
            seed=42,
            total_possessions=80,
        )

        # Semifinal schedule (round 10)
        await repo.create_schedule_entry(
            season_id=season.id,
            round_number=10,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[3],
            phase="playoff",
        )
        await repo.create_schedule_entry(
            season_id=season.id,
            round_number=10,
            matchup_index=1,
            home_team_id=team_ids[1],
            away_team_id=team_ids[2],
            phase="playoff",
        )

        # Semifinal games (Seed 1 and Seed 2 win their semis)
        await repo.store_game_result(
            season_id=season.id,
            round_number=10,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[3],
            home_score=55,
            away_score=45,
            winner_team_id=team_ids[0],
            seed=100,
            total_possessions=85,
        )
        await repo.store_game_result(
            season_id=season.id,
            round_number=10,
            matchup_index=1,
            home_team_id=team_ids[1],
            away_team_id=team_ids[2],
            home_score=50,
            away_score=44,
            winner_team_id=team_ids[1],
            seed=101,
            total_possessions=82,
        )

        # Finals schedule (round 15) — different pair than semis
        await repo.create_schedule_entry(
            season_id=season.id,
            round_number=15,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[1],
            phase="playoff",
        )

        # Finals games (3 games, Finalist1 wins 2-1)
        await repo.store_game_result(
            season_id=season.id,
            round_number=15,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[1],
            home_score=60,
            away_score=55,
            winner_team_id=team_ids[0],
            seed=200,
            total_possessions=90,
        )
        await repo.store_game_result(
            season_id=season.id,
            round_number=16,
            matchup_index=0,
            home_team_id=team_ids[1],
            away_team_id=team_ids[0],
            home_score=58,
            away_score=52,
            winner_team_id=team_ids[1],
            seed=201,
            total_possessions=88,
        )
        # Add round 16 to playoff schedule
        await repo.create_schedule_entry(
            season_id=season.id,
            round_number=16,
            matchup_index=0,
            home_team_id=team_ids[1],
            away_team_id=team_ids[0],
            phase="playoff",
        )
        await repo.store_game_result(
            season_id=season.id,
            round_number=17,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[1],
            home_score=65,
            away_score=58,
            winner_team_id=team_ids[0],
            seed=202,
            total_possessions=92,
        )
        await repo.create_schedule_entry(
            season_id=season.id,
            round_number=17,
            matchup_index=0,
            home_team_id=team_ids[0],
            away_team_id=team_ids[1],
            phase="playoff",
        )

        # Set champion in season config
        season.config = {"champion_team_id": team_ids[0]}

        # Mark games as presented
        all_games = await repo.get_all_games(season.id)
        for g in all_games:
            await repo.mark_game_presented(g.id)

        await session.commit()
        return season.id, team_ids


class TestPlayoffsPageEmpty:
    """Playoffs page and API render correctly with no data or during regular season."""

    async def test_playoffs_page_empty(self, app_client):
        """Playoffs page returns 200 and shows empty state when no season exists."""
        client, _ = app_client
        r = await client.get("/playoffs")
        assert r.status_code == 200
        assert "No Playoff Bracket Yet" in r.text

    async def test_playoffs_api_empty(self, app_client):
        """Bracket API returns valid structure with no season."""
        client, _ = app_client
        r = await client.get("/api/games/playoffs/bracket")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["season_id"] is None
        assert data["semifinals"] == []
        assert data["finals"] is None
        assert data["champion"] is None

    async def test_playoffs_page_regular_season(self, app_client):
        """Playoffs page shows 'regular season in progress' during regular season."""
        client, engine = app_client
        await _seed_season(engine)

        r = await client.get("/playoffs")
        assert r.status_code == 200
        assert "regular season" in r.text.lower()

    async def test_playoffs_api_regular_season(self, app_client):
        """Bracket API returns empty bracket during regular season."""
        client, engine = app_client
        await _seed_season(engine)

        r = await client.get("/api/games/playoffs/bracket")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["phase"] == "regular_season"
        assert data["semifinals"] == []
        assert data["finals"] is None
        assert data["champion"] is None


class TestPlayoffsWithSemifinals:
    """Bracket shows semifinal matchups and series records."""

    async def test_playoffs_page_shows_semis(self, app_client):
        """Playoffs page renders semifinal matchups with team names."""
        client, engine = app_client
        await _seed_playoffs(engine)

        r = await client.get("/playoffs")
        assert r.status_code == 200
        assert "Semifinal" in r.text
        assert "Seed1" in r.text
        assert "Seed4" in r.text
        assert "Seed2" in r.text
        assert "Seed3" in r.text

    async def test_playoffs_api_shows_semis(self, app_client):
        """Bracket API returns semifinal data with wins and games."""
        client, engine = app_client
        _season_id, team_ids = await _seed_playoffs(engine)

        r = await client.get("/api/games/playoffs/bracket")
        assert r.status_code == 200
        data = r.json()["data"]

        assert data["phase"] == "playoffs"
        assert len(data["semifinals"]) == 2

        # Verify first semifinal: Seed1 vs Seed4
        semi1 = data["semifinals"][0]
        team_names_in_semi1 = {
            semi1["seed_high"]["team_name"],
            semi1["seed_low"]["team_name"],
        }
        assert "Seed1" in team_names_in_semi1
        assert "Seed4" in team_names_in_semi1
        # Seed1 won game 1
        high = semi1["seed_high"]
        low = semi1["seed_low"]
        total_wins = high["wins"] + low["wins"]
        assert total_wins == 1  # 1 game played

        # Verify second semifinal: Seed2 vs Seed3
        semi2 = data["semifinals"][1]
        team_names_in_semi2 = {
            semi2["seed_high"]["team_name"],
            semi2["seed_low"]["team_name"],
        }
        assert "Seed2" in team_names_in_semi2
        assert "Seed3" in team_names_in_semi2

        # Games array should have 1 game per semifinal
        assert len(semi1["games"]) == 1
        assert len(semi2["games"]) == 1

    async def test_playoffs_api_no_finals_yet(self, app_client):
        """Bracket API returns no finals when semis are still in progress."""
        client, engine = app_client
        await _seed_playoffs(engine)

        r = await client.get("/api/games/playoffs/bracket")
        data = r.json()["data"]
        assert data["finals"] is None
        assert data["champion"] is None

    async def test_playoffs_page_shows_tbd_finals(self, app_client):
        """Playoffs page shows TBD finals section when semis are in progress."""
        client, engine = app_client
        await _seed_playoffs(engine)

        r = await client.get("/playoffs")
        assert r.status_code == 200
        assert "TBD" in r.text


class TestPlayoffsChampionship:
    """Bracket shows finals and champion when playoffs are complete."""

    async def test_playoffs_page_shows_champion(self, app_client):
        """Playoffs page renders champion banner."""
        client, engine = app_client
        await _seed_championship(engine)

        r = await client.get("/playoffs")
        assert r.status_code == 200
        assert "Champion" in r.text
        assert "Finalist1" in r.text

    async def test_playoffs_api_returns_champion(self, app_client):
        """Bracket API returns champion data when season has a champion."""
        client, engine = app_client
        _season_id, team_ids = await _seed_championship(engine)

        r = await client.get("/api/games/playoffs/bracket")
        assert r.status_code == 200
        data = r.json()["data"]

        assert data["phase"] == "complete"
        assert data["champion"] is not None
        assert data["champion"]["team_name"] == "Finalist1"
        assert data["champion"]["team_id"] == team_ids[0]

    async def test_playoffs_api_returns_finals(self, app_client):
        """Bracket API returns finals series data with wins and games."""
        client, engine = app_client
        await _seed_championship(engine)

        r = await client.get("/api/games/playoffs/bracket")
        data = r.json()["data"]

        assert data["finals"] is not None
        finals = data["finals"]

        # Should have team_a and team_b with wins
        assert "team_a" in finals
        assert "team_b" in finals
        total_finals_wins = finals["team_a"]["wins"] + finals["team_b"]["wins"]
        assert total_finals_wins == 3  # 3 finals games played

        # Should have 3 individual game records
        assert len(finals["games"]) == 3

    async def test_playoffs_api_returns_semifinals_with_championship(self, app_client):
        """Bracket API returns both semifinals and finals for complete season."""
        client, engine = app_client
        await _seed_championship(engine)

        r = await client.get("/api/games/playoffs/bracket")
        data = r.json()["data"]

        assert len(data["semifinals"]) == 2
        assert data["finals"] is not None
        assert data["champion"] is not None

    async def test_playoffs_page_shows_finals_section(self, app_client):
        """Playoffs page renders Championship Finals heading."""
        client, engine = app_client
        await _seed_championship(engine)

        r = await client.get("/playoffs")
        assert r.status_code == 200
        assert "Championship Finals" in r.text
        assert "Finals" in r.text


class TestPlayoffsNavigation:
    """Navigation link appears correctly."""

    async def test_playoffs_page_accessible(self, app_client):
        """Playoffs page renders successfully."""
        client, _ = app_client
        r = await client.get("/playoffs")
        assert r.status_code == 200
        assert "Playoffs" in r.text


class TestSeriesRecordComputation:
    """Verify series records are computed correctly from game results."""

    async def test_series_wins_match_game_results(self, app_client):
        """Series wins in the bracket match the actual game outcomes."""
        client, engine = app_client
        await _seed_championship(engine)

        r = await client.get("/api/games/playoffs/bracket")
        data = r.json()["data"]

        # Check semifinals: each should have 1 game played
        for semi in data["semifinals"]:
            total = semi["seed_high"]["wins"] + semi["seed_low"]["wins"]
            assert total == len(semi["games"])

        # Check finals: 3 games, 2-1 record
        finals = data["finals"]
        total = finals["team_a"]["wins"] + finals["team_b"]["wins"]
        assert total == len(finals["games"])
        # The winner (Finalist1) should have 2 wins
        winner_wins = max(finals["team_a"]["wins"], finals["team_b"]["wins"])
        assert winner_wins == 2
