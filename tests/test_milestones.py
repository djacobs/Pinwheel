"""Tests for the milestone system — earned move acquisition.

Covers:
- Milestone threshold detection
- Career stats aggregation (via repository)
- Move type and name correctness
- Milestone idempotency (not re-triggered)
- Multiple milestones in one check
- Repository: add_hooper_move
- Repository: get_hooper_season_stats
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.core.milestones import DEFAULT_MILESTONES, MilestoneDefinition, check_milestones
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository

# --- Unit tests for check_milestones (pure function, no DB) ---


class TestCheckMilestones:
    """Pure-function tests for milestone checking logic."""

    def test_no_milestones_below_threshold(self) -> None:
        """Stats below all thresholds should return no moves."""
        stats = {"points": 10, "assists": 5, "steals": 3, "three_pointers_made": 2}
        result = check_milestones(stats, set())
        assert result == []

    def test_single_milestone_at_threshold(self) -> None:
        """Exactly meeting a threshold should trigger the milestone."""
        stats = {"points": 50}
        result = check_milestones(stats, set())
        fadeaway = [m for m in result if m.name == "Fadeaway"]
        assert len(fadeaway) == 1
        assert fadeaway[0].source == "earned"
        assert fadeaway[0].trigger == "half_court_setup"

    def test_single_milestone_above_threshold(self) -> None:
        """Exceeding a threshold should also trigger the milestone."""
        stats = {"points": 100}
        result = check_milestones(stats, set())
        fadeaway = [m for m in result if m.name == "Fadeaway"]
        assert len(fadeaway) == 1

    def test_milestone_skipped_if_already_has_move(self) -> None:
        """If hooper already has the move, don't grant it again."""
        stats = {"points": 80, "assists": 30}
        existing = {"Fadeaway", "No-Look Pass"}
        result = check_milestones(stats, existing)
        # Both Fadeaway and No-Look Pass thresholds are met, but already owned
        assert all(m.name not in existing for m in result)

    def test_multiple_milestones_in_one_check(self) -> None:
        """Multiple milestones can trigger in a single check."""
        stats = {
            "points": 60,
            "assists": 25,
            "steals": 20,
            "three_pointers_made": 15,
        }
        result = check_milestones(stats, set())
        names = {m.name for m in result}
        assert "Fadeaway" in names
        assert "No-Look Pass" in names
        assert "Strip Steal" in names
        assert "Deep Three" in names

    def test_partial_milestones(self) -> None:
        """Only milestones that are met should be returned."""
        stats = {"points": 60, "assists": 5}
        result = check_milestones(stats, set())
        names = {m.name for m in result}
        assert "Fadeaway" in names
        assert "No-Look Pass" not in names

    def test_custom_milestones(self) -> None:
        """Custom milestone definitions work correctly."""
        custom = [
            MilestoneDefinition(
                stat="turnovers",
                threshold=5,
                move_name="Iron Will",
                move_trigger="stamina_below_40",
                move_effect="stamina floor at 0.35",
                move_type="resilience",
                attribute_gate={},
                description="5 turnovers unlocks Iron Will",
            ),
        ]
        stats = {"turnovers": 6}
        result = check_milestones(stats, set(), milestones=custom)
        assert len(result) == 1
        assert result[0].name == "Iron Will"

    def test_empty_stats(self) -> None:
        """Empty stats dict should return no milestones."""
        result = check_milestones({}, set())
        assert result == []

    def test_milestone_move_source_is_earned(self) -> None:
        """All milestone-granted moves should have source='earned'."""
        stats = {"points": 999, "assists": 999, "steals": 999, "three_pointers_made": 999}
        result = check_milestones(stats, set())
        for move in result:
            assert move.source == "earned"

    def test_milestone_definition_to_move(self) -> None:
        """MilestoneDefinition.to_move() produces correct Move."""
        md = DEFAULT_MILESTONES[0]
        move = md.to_move()
        assert move.name == md.move_name
        assert move.trigger == md.move_trigger
        assert move.effect == md.move_effect
        assert move.source == "earned"
        assert move.attribute_gate == md.attribute_gate


# --- Integration tests with database ---


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


async def _setup_season(repo: Repository) -> tuple[str, str, str]:
    """Create league, season, team with 1 hooper. Returns (season_id, team_id, hooper_id)."""
    league = await repo.create_league("Test League")
    season = await repo.create_season(
        league.id,
        "Season 1",
        starting_ruleset={"quarter_minutes": 3},
    )
    team = await repo.create_team(
        season.id,
        "Test Team",
        venue={"name": "Arena", "capacity": 5000},
    )
    hooper = await repo.create_hooper(
        team_id=team.id,
        season_id=season.id,
        name="TestHooper",
        archetype="sharpshooter",
        attributes=_hooper_attrs(),
        moves=[{"name": "Heat Check", "trigger": "made_three_last_possession",
                "effect": "+15%", "attribute_gate": {}, "source": "archetype"}],
    )
    return season.id, team.id, hooper.id


class TestGetHooperSeasonStats:
    """Test the repository method for aggregating box scores."""

    async def test_no_games_returns_zeros(self, repo: Repository) -> None:
        season_id, _, hooper_id = await _setup_season(repo)
        stats = await repo.get_hooper_season_stats(hooper_id, season_id)
        assert stats["points"] == 0
        assert stats["assists"] == 0
        assert stats["steals"] == 0

    async def test_single_game_stats(self, repo: Repository) -> None:
        season_id, team_id, hooper_id = await _setup_season(repo)

        # Create a game and box score
        game = await repo.store_game_result(
            season_id=season_id,
            round_number=1,
            matchup_index=0,
            home_team_id=team_id,
            away_team_id=team_id,
            home_score=50,
            away_score=40,
            winner_team_id=team_id,
            seed=42,
            total_possessions=80,
        )
        await repo.store_box_score(
            game_id=game.id,
            hooper_id=hooper_id,
            team_id=team_id,
            points=25,
            assists=8,
            steals=3,
            three_pointers_made=4,
        )

        stats = await repo.get_hooper_season_stats(hooper_id, season_id)
        assert stats["points"] == 25
        assert stats["assists"] == 8
        assert stats["steals"] == 3
        assert stats["three_pointers_made"] == 4

    async def test_multi_game_aggregation(self, repo: Repository) -> None:
        season_id, team_id, hooper_id = await _setup_season(repo)

        for rnd in range(1, 4):
            game = await repo.store_game_result(
                season_id=season_id,
                round_number=rnd,
                matchup_index=0,
                home_team_id=team_id,
                away_team_id=team_id,
                home_score=50,
                away_score=40,
                winner_team_id=team_id,
                seed=rnd,
                total_possessions=80,
            )
            await repo.store_box_score(
                game_id=game.id,
                hooper_id=hooper_id,
                team_id=team_id,
                points=20,
                assists=7,
                steals=5,
            )

        stats = await repo.get_hooper_season_stats(hooper_id, season_id)
        assert stats["points"] == 60
        assert stats["assists"] == 21
        assert stats["steals"] == 15


class TestAddHooperMove:
    """Test the repository method for adding moves to a hooper."""

    async def test_add_move_to_hooper(self, repo: Repository) -> None:
        season_id, _, hooper_id = await _setup_season(repo)

        move_data = {
            "name": "Fadeaway",
            "trigger": "half_court_setup",
            "effect": "+12% mid-range",
            "attribute_gate": {},
            "source": "earned",
        }
        await repo.add_hooper_move(hooper_id, move_data)

        hooper = await repo.get_hooper(hooper_id)
        assert hooper is not None
        moves = hooper.moves
        assert len(moves) == 2  # Heat Check + Fadeaway
        names = [m["name"] if isinstance(m, dict) else m.name for m in moves]
        assert "Fadeaway" in names

    async def test_add_move_to_nonexistent_hooper(self, repo: Repository) -> None:
        """Adding a move to a nonexistent hooper should be a no-op."""
        await repo.add_hooper_move("nonexistent-id", {"name": "Test"})
        # No exception raised

    async def test_add_multiple_moves(self, repo: Repository) -> None:
        season_id, _, hooper_id = await _setup_season(repo)

        for name in ("Fadeaway", "Strip Steal", "Deep Three"):
            await repo.add_hooper_move(hooper_id, {
                "name": name,
                "trigger": "any",
                "effect": "test",
                "attribute_gate": {},
                "source": "earned",
            })

        hooper = await repo.get_hooper(hooper_id)
        assert hooper is not None
        # 1 archetype (Heat Check) + 3 earned = 4
        assert len(hooper.moves) == 4


class TestMilestoneIntegration:
    """Integration test combining stats aggregation with milestone checking."""

    async def test_milestone_triggers_from_real_stats(self, repo: Repository) -> None:
        """A hooper with enough stats should earn the correct moves."""
        season_id, team_id, hooper_id = await _setup_season(repo)

        # Play enough games to cross the points threshold (50)
        for rnd in range(1, 4):
            game = await repo.store_game_result(
                season_id=season_id,
                round_number=rnd,
                matchup_index=0,
                home_team_id=team_id,
                away_team_id=team_id,
                home_score=50,
                away_score=40,
                winner_team_id=team_id,
                seed=rnd,
                total_possessions=80,
            )
            await repo.store_box_score(
                game_id=game.id,
                hooper_id=hooper_id,
                team_id=team_id,
                points=20,
                assists=2,
                steals=1,
            )

        stats = await repo.get_hooper_season_stats(hooper_id, season_id)
        assert stats["points"] == 60

        # Hooper already has "Heat Check"
        existing_names = {"Heat Check"}
        new_moves = check_milestones(stats, existing_names)

        fadeaway = [m for m in new_moves if m.name == "Fadeaway"]
        assert len(fadeaway) == 1

    async def test_milestone_not_retriggered_after_grant(self, repo: Repository) -> None:
        """Once a move is granted, checking milestones again should not re-grant."""
        season_id, team_id, hooper_id = await _setup_season(repo)

        # Enough stats for Fadeaway
        game = await repo.store_game_result(
            season_id=season_id,
            round_number=1,
            matchup_index=0,
            home_team_id=team_id,
            away_team_id=team_id,
            home_score=50,
            away_score=40,
            winner_team_id=team_id,
            seed=1,
            total_possessions=80,
        )
        await repo.store_box_score(
            game_id=game.id,
            hooper_id=hooper_id,
            team_id=team_id,
            points=55,
        )

        stats = await repo.get_hooper_season_stats(hooper_id, season_id)
        existing_names = {"Heat Check"}

        # First check: should earn Fadeaway
        new_moves = check_milestones(stats, existing_names)
        assert any(m.name == "Fadeaway" for m in new_moves)

        # Grant it
        await repo.add_hooper_move(hooper_id, new_moves[0].model_dump())

        # Second check: include Fadeaway in existing
        existing_names.add("Fadeaway")
        new_moves_2 = check_milestones(stats, existing_names)
        assert not any(m.name == "Fadeaway" for m in new_moves_2)
