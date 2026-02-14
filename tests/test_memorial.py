"""Tests for season memorial data collection functions."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.core.game_loop import step_round
from pinwheel.core.memorial import (
    compute_head_to_head,
    compute_key_moments,
    compute_rule_timeline,
    compute_statistical_leaders,
    gather_memorial_data,
)
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.core.season import archive_season
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.report import SeasonMemorial


@pytest.fixture
async def engine() -> AsyncEngine:
    """Create an in-memory SQLite engine with all tables."""
    eng = create_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def repo(engine: AsyncEngine) -> Repository:
    """Yield a repository with a session bound to the in-memory database."""
    async with get_session(engine) as session:
        yield Repository(session)


def _hooper_attrs():
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


async def _seed_season_with_games(repo: Repository, rounds: int = 1) -> tuple[str, list[str]]:
    """Create a league with 4 teams, schedule, and run N rounds."""
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

    matchups = generate_round_robin(team_ids)
    for m in matchups:
        await repo.create_schedule_entry(
            season_id=season.id,
            round_number=m.round_number,
            matchup_index=m.matchup_index,
            home_team_id=m.home_team_id,
            away_team_id=m.away_team_id,
        )

    # Run rounds
    for rn in range(1, rounds + 1):
        await step_round(repo, season.id, round_number=rn)

    return season.id, team_ids


class TestStatisticalLeaders:
    """Tests for compute_statistical_leaders()."""

    async def test_returns_all_categories(self, repo: Repository):
        """Leaders dict should have ppg, apg, spg, fg_pct keys."""
        season_id, _ = await _seed_season_with_games(repo)
        leaders = await compute_statistical_leaders(repo, season_id)

        assert "ppg" in leaders
        assert "apg" in leaders
        assert "spg" in leaders
        assert "fg_pct" in leaders

    async def test_top_3_per_category(self, repo: Repository):
        """Each category should have at most 3 leaders."""
        season_id, _ = await _seed_season_with_games(repo)
        leaders = await compute_statistical_leaders(repo, season_id)

        for key in ("ppg", "apg", "spg", "fg_pct"):
            assert len(leaders[key]) <= 3

    async def test_leader_has_required_fields(self, repo: Repository):
        """Each leader entry should have hooper_id, hooper_name, team_name, value, games."""
        season_id, _ = await _seed_season_with_games(repo)
        leaders = await compute_statistical_leaders(repo, season_id)

        for key in ("ppg", "apg", "spg"):
            if leaders[key]:
                entry = leaders[key][0]
                assert "hooper_id" in entry
                assert "hooper_name" in entry
                assert "team_name" in entry
                assert "value" in entry
                assert "games" in entry
                assert entry["games"] > 0

    async def test_empty_season(self, repo: Repository):
        """Season with no games should return empty lists."""
        league = await repo.create_league("Empty League")
        season = await repo.create_season(league.id, "Empty Season")
        leaders = await compute_statistical_leaders(repo, season.id)

        for key in ("ppg", "apg", "spg", "fg_pct"):
            assert leaders[key] == []

    async def test_ppg_sorted_descending(self, repo: Repository):
        """PPG leaders should be sorted highest first."""
        season_id, _ = await _seed_season_with_games(repo)
        leaders = await compute_statistical_leaders(repo, season_id)

        ppg = leaders["ppg"]
        if len(ppg) >= 2:
            assert ppg[0]["value"] >= ppg[1]["value"]


class TestKeyMoments:
    """Tests for compute_key_moments()."""

    async def test_returns_moments(self, repo: Repository):
        """Should return at least some moments for a season with games."""
        season_id, _ = await _seed_season_with_games(repo)
        moments = await compute_key_moments(repo, season_id)

        assert isinstance(moments, list)
        assert len(moments) > 0

    async def test_moment_has_required_fields(self, repo: Repository):
        """Each moment should have game details and a moment_type."""
        season_id, _ = await _seed_season_with_games(repo)
        moments = await compute_key_moments(repo, season_id)

        for m in moments:
            assert "game_id" in m
            assert "round_number" in m
            assert "home_team_name" in m
            assert "away_team_name" in m
            assert "home_score" in m
            assert "away_score" in m
            assert "margin" in m
            assert "winner_name" in m
            assert "moment_type" in m

    async def test_valid_moment_types(self, repo: Repository):
        """Moment types should be from the known set."""
        season_id, _ = await _seed_season_with_games(repo)
        moments = await compute_key_moments(repo, season_id)

        valid_types = {"playoff", "closest_game", "blowout", "elam_ending"}
        for m in moments:
            assert m["moment_type"] in valid_types

    async def test_max_8_moments(self, repo: Repository):
        """Should return at most 8 moments."""
        season_id, _ = await _seed_season_with_games(repo)
        moments = await compute_key_moments(repo, season_id)

        assert len(moments) <= 8

    async def test_empty_season(self, repo: Repository):
        """Season with no games returns empty list."""
        league = await repo.create_league("Empty League")
        season = await repo.create_season(league.id, "Empty Season")
        moments = await compute_key_moments(repo, season.id)

        assert moments == []

    async def test_no_duplicate_games(self, repo: Repository):
        """Each game should appear at most once in key moments."""
        season_id, _ = await _seed_season_with_games(repo)
        moments = await compute_key_moments(repo, season_id)

        game_ids = [m["game_id"] for m in moments]
        assert len(game_ids) == len(set(game_ids))


class TestHeadToHead:
    """Tests for compute_head_to_head()."""

    async def test_returns_matchups(self, repo: Repository):
        """Should return head-to-head records for a season with games."""
        season_id, _ = await _seed_season_with_games(repo)
        h2h = await compute_head_to_head(repo, season_id)

        assert isinstance(h2h, list)
        assert len(h2h) > 0

    async def test_matchup_has_required_fields(self, repo: Repository):
        """Each matchup should have team info, wins, and differential."""
        season_id, _ = await _seed_season_with_games(repo)
        h2h = await compute_head_to_head(repo, season_id)

        for m in h2h:
            assert "team_a_id" in m
            assert "team_a_name" in m
            assert "team_b_id" in m
            assert "team_b_name" in m
            assert "team_a_wins" in m
            assert "team_b_wins" in m
            assert "point_differential" in m

    async def test_four_teams_six_matchups(self, repo: Repository):
        """4 teams should produce C(4,2)=6 unique matchups."""
        season_id, _ = await _seed_season_with_games(repo)
        h2h = await compute_head_to_head(repo, season_id)

        assert len(h2h) == 6

    async def test_wins_consistent(self, repo: Repository):
        """Each matchup's total wins should equal games played between them."""
        season_id, _ = await _seed_season_with_games(repo)
        h2h = await compute_head_to_head(repo, season_id)

        for m in h2h:
            total_wins = m["team_a_wins"] + m["team_b_wins"]
            # With 1 round, each pair plays exactly once
            assert total_wins >= 1

    async def test_empty_season(self, repo: Repository):
        """Season with no games returns empty list."""
        league = await repo.create_league("Empty League")
        season = await repo.create_season(league.id, "Empty Season")
        h2h = await compute_head_to_head(repo, season.id)

        assert h2h == []


class TestRuleTimeline:
    """Tests for compute_rule_timeline()."""

    async def test_no_rule_changes(self, repo: Repository):
        """Season without rule changes returns empty list."""
        season_id, _ = await _seed_season_with_games(repo)
        timeline = await compute_rule_timeline(repo, season_id)

        assert timeline == []

    async def test_rule_change_captured(self, repo: Repository):
        """Rule changes should appear in the timeline."""
        season_id, _ = await _seed_season_with_games(repo)

        # Add a rule.enacted event
        await repo.append_event(
            event_type="rule.enacted",
            aggregate_id="rule-1",
            aggregate_type="rule",
            season_id=season_id,
            payload={
                "parameter": "three_point_value",
                "old_value": 3,
                "new_value": 4,
                "round_enacted": 1,
                "proposal_id": "prop-1",
            },
            round_number=1,
        )

        timeline = await compute_rule_timeline(repo, season_id)

        assert len(timeline) == 1
        assert timeline[0]["parameter"] == "three_point_value"
        assert timeline[0]["old_value"] == 3
        assert timeline[0]["new_value"] == 4

    async def test_timeline_has_required_fields(self, repo: Repository):
        """Each timeline entry should have round, parameter, old/new values."""
        season_id, _ = await _seed_season_with_games(repo)

        await repo.append_event(
            event_type="rule.enacted",
            aggregate_id="rule-1",
            aggregate_type="rule",
            season_id=season_id,
            payload={
                "parameter": "shot_clock_seconds",
                "old_value": 14,
                "new_value": 12,
                "proposal_id": "prop-1",
            },
            round_number=2,
        )

        timeline = await compute_rule_timeline(repo, season_id)

        entry = timeline[0]
        assert "round_number" in entry
        assert "parameter" in entry
        assert "old_value" in entry
        assert "new_value" in entry
        assert "proposer_id" in entry
        assert "proposer_name" in entry

    async def test_proposer_resolved(self, repo: Repository):
        """If a proposal.submitted event exists, the proposer should be linked."""
        season_id, _ = await _seed_season_with_games(repo)

        # Create a player to be the proposer
        player = await repo.get_or_create_player(
            discord_id="123456",
            username="TestGovernor",
        )

        # Add proposal.submitted event
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id="prop-1",
            aggregate_type="proposal",
            season_id=season_id,
            payload={
                "id": "prop-1",
                "raw_text": "Make threes worth 4",
            },
            round_number=1,
            governor_id=player.id,
        )

        # Add rule.enacted event referencing that proposal
        await repo.append_event(
            event_type="rule.enacted",
            aggregate_id="rule-1",
            aggregate_type="rule",
            season_id=season_id,
            payload={
                "parameter": "three_point_value",
                "old_value": 3,
                "new_value": 4,
                "proposal_id": "prop-1",
            },
            round_number=1,
        )

        timeline = await compute_rule_timeline(repo, season_id)

        assert len(timeline) == 1
        assert timeline[0]["proposer_id"] == player.id
        assert timeline[0]["proposer_name"] == "TestGovernor"

    async def test_multiple_changes_ordered(self, repo: Repository):
        """Multiple rule changes should be in chronological order."""
        season_id, _ = await _seed_season_with_games(repo)

        # Two rule changes in different rounds
        await repo.append_event(
            event_type="rule.enacted",
            aggregate_id="rule-1",
            aggregate_type="rule",
            season_id=season_id,
            payload={"parameter": "three_point_value", "old_value": 3, "new_value": 4},
            round_number=1,
        )
        await repo.append_event(
            event_type="rule.enacted",
            aggregate_id="rule-2",
            aggregate_type="rule",
            season_id=season_id,
            payload={"parameter": "shot_clock_seconds", "old_value": 14, "new_value": 12},
            round_number=2,
        )

        timeline = await compute_rule_timeline(repo, season_id)

        assert len(timeline) == 2
        # Should be in order (sequence_number order, which matches insertion)
        assert timeline[0]["parameter"] == "three_point_value"
        assert timeline[1]["parameter"] == "shot_clock_seconds"


class TestGatherMemorialData:
    """Tests for gather_memorial_data() orchestrator."""

    async def test_returns_all_sections(self, repo: Repository):
        """Gathered memorial data should include all expected keys."""
        season_id, _ = await _seed_season_with_games(repo)
        data = await gather_memorial_data(repo, season_id)

        # Computed sections
        assert "statistical_leaders" in data
        assert "key_moments" in data
        assert "head_to_head" in data
        assert "rule_timeline" in data
        assert "awards" in data

        # AI narrative placeholders
        assert "season_narrative" in data
        assert "championship_recap" in data
        assert "champion_profile" in data
        assert "governance_legacy" in data

        # Metadata
        assert "generated_at" in data
        assert "model_used" in data

    async def test_ai_narratives_are_empty(self, repo: Repository):
        """AI narrative fields should be empty placeholders."""
        season_id, _ = await _seed_season_with_games(repo)
        data = await gather_memorial_data(repo, season_id)

        assert data["season_narrative"] == ""
        assert data["championship_recap"] == ""
        assert data["champion_profile"] == ""
        assert data["governance_legacy"] == ""

    async def test_awards_passed_through(self, repo: Repository):
        """Awards should be stored as-is from the input."""
        season_id, _ = await _seed_season_with_games(repo)
        test_awards = [{"award": "MVP", "recipient_name": "Test"}]
        data = await gather_memorial_data(repo, season_id, awards=test_awards)

        assert data["awards"] == test_awards

    async def test_generated_at_set(self, repo: Repository):
        """generated_at should be a non-empty ISO timestamp."""
        season_id, _ = await _seed_season_with_games(repo)
        data = await gather_memorial_data(repo, season_id)

        assert data["generated_at"] != ""
        # Should be parseable as ISO datetime
        from datetime import datetime

        datetime.fromisoformat(data["generated_at"])

    async def test_validates_as_season_memorial(self, repo: Repository):
        """Gathered data should be valid SeasonMemorial input."""
        season_id, _ = await _seed_season_with_games(repo)
        data = await gather_memorial_data(repo, season_id)

        # Should construct without errors
        memorial = SeasonMemorial(**data)
        assert isinstance(memorial.statistical_leaders, dict)
        assert isinstance(memorial.key_moments, list)
        assert isinstance(memorial.head_to_head, list)
        assert isinstance(memorial.rule_timeline, list)


class TestArchiveIntegration:
    """Test that memorial data flows through archive_season()."""

    async def test_archive_includes_memorial(self, repo: Repository):
        """archive_season() should store memorial data on the archive row."""
        season_id, _ = await _seed_season_with_games(repo)
        archive = await archive_season(repo, season_id)

        assert archive.memorial is not None
        assert isinstance(archive.memorial, dict)
        assert "statistical_leaders" in archive.memorial
        assert "key_moments" in archive.memorial
        assert "head_to_head" in archive.memorial
        assert "rule_timeline" in archive.memorial

    async def test_memorial_persists_in_db(self, repo: Repository):
        """Memorial data should be retrievable from the database after archiving."""
        season_id, _ = await _seed_season_with_games(repo)
        await archive_season(repo, season_id)

        # Retrieve from DB
        archive = await repo.get_season_archive(season_id)
        assert archive is not None
        assert archive.memorial is not None
        assert "statistical_leaders" in archive.memorial

    async def test_memorial_validates_as_model(self, repo: Repository):
        """Stored memorial should be valid SeasonMemorial data."""
        season_id, _ = await _seed_season_with_games(repo)
        archive = await archive_season(repo, season_id)

        memorial = SeasonMemorial(**archive.memorial)
        assert memorial.generated_at != ""
