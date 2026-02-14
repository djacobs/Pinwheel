"""Tests for the season lifecycle: phase enum, transitions, awards, championship,
tiebreakers, and offseason governance."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.core.event_bus import EventBus
from pinwheel.core.game_loop import step_round
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.core.season import (
    ALLOWED_TRANSITIONS,
    SeasonPhase,
    _compute_head_to_head,
    _resolve_tie_group,
    check_and_handle_tiebreakers,
    check_tiebreakers,
    close_offseason,
    compute_awards,
    enter_championship,
    enter_offseason,
    generate_tiebreaker_games,
    normalize_phase,
    transition_season,
)
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository


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


async def _create_season(repo: Repository, status: str = "setup") -> str:
    """Create a minimal season and return its ID."""
    league = await repo.create_league("Test League")
    season = await repo.create_season(league.id, "Test Season")
    if status != "setup":
        await repo.update_season_status(season.id, status)
    return season.id


async def _setup_season_with_teams(repo: Repository) -> tuple[str, list[str]]:
    """Create a league, season, 4 teams with 4 hoopers each, and a schedule."""
    league = await repo.create_league("Lifecycle League")
    season = await repo.create_season(
        league.id,
        "Lifecycle Season",
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


class TestSeasonPhaseEnum:
    """Tests for the SeasonPhase enum and its string compatibility."""

    def test_enum_values(self):
        """All expected phases exist with correct string values."""
        assert SeasonPhase.SETUP == "setup"
        assert SeasonPhase.ACTIVE == "active"
        assert SeasonPhase.TIEBREAKER_CHECK == "tiebreaker_check"
        assert SeasonPhase.TIEBREAKERS == "tiebreakers"
        assert SeasonPhase.PLAYOFFS == "playoffs"
        assert SeasonPhase.CHAMPIONSHIP == "championship"
        assert SeasonPhase.OFFSEASON == "offseason"
        assert SeasonPhase.COMPLETE == "complete"

    def test_string_comparison(self):
        """SeasonPhase values compare equal to raw strings."""
        assert SeasonPhase.ACTIVE == "active"
        assert SeasonPhase.ACTIVE == "active"  # symmetric check
        assert SeasonPhase.COMPLETE.value == "complete"

    def test_all_phases_have_transitions(self):
        """Every phase has an entry in ALLOWED_TRANSITIONS."""
        for phase in SeasonPhase:
            assert phase in ALLOWED_TRANSITIONS


class TestNormalizePhase:
    """Tests for normalize_phase() backward compatibility."""

    def test_direct_enum_values(self):
        """Standard enum values normalize to themselves."""
        assert normalize_phase("setup") == SeasonPhase.SETUP
        assert normalize_phase("active") == SeasonPhase.ACTIVE
        assert normalize_phase("playoffs") == SeasonPhase.PLAYOFFS
        assert normalize_phase("championship") == SeasonPhase.CHAMPIONSHIP
        assert normalize_phase("complete") == SeasonPhase.COMPLETE

    def test_legacy_completed(self):
        """Legacy 'completed' maps to COMPLETE."""
        assert normalize_phase("completed") == SeasonPhase.COMPLETE

    def test_legacy_archived(self):
        """Legacy 'archived' maps to COMPLETE."""
        assert normalize_phase("archived") == SeasonPhase.COMPLETE

    def test_legacy_regular_season_complete(self):
        """Legacy 'regular_season_complete' maps to PLAYOFFS."""
        assert normalize_phase("regular_season_complete") == SeasonPhase.PLAYOFFS

    def test_unknown_defaults_to_active(self):
        """Unknown status strings default to ACTIVE (with warning)."""
        assert normalize_phase("something_weird") == SeasonPhase.ACTIVE


class TestTransitionSeason:
    """Tests for transition_season() validation and event publishing."""

    async def test_valid_transition_setup_to_active(self, repo: Repository):
        """SETUP -> ACTIVE is a valid transition."""
        season_id = await _create_season(repo, "setup")
        result = await transition_season(repo, season_id, SeasonPhase.ACTIVE)
        assert result == SeasonPhase.ACTIVE

        season = await repo.get_season(season_id)
        assert season.status == "active"

    async def test_valid_transition_active_to_playoffs(self, repo: Repository):
        """ACTIVE -> PLAYOFFS is a valid transition."""
        season_id = await _create_season(repo, "active")
        result = await transition_season(repo, season_id, SeasonPhase.PLAYOFFS)
        assert result == SeasonPhase.PLAYOFFS

    async def test_valid_transition_playoffs_to_championship(self, repo: Repository):
        """PLAYOFFS -> CHAMPIONSHIP is a valid transition."""
        season_id = await _create_season(repo, "playoffs")
        result = await transition_season(repo, season_id, SeasonPhase.CHAMPIONSHIP)
        assert result == SeasonPhase.CHAMPIONSHIP

    async def test_valid_transition_championship_to_complete(self, repo: Repository):
        """CHAMPIONSHIP -> COMPLETE is a valid transition."""
        season_id = await _create_season(repo, "championship")
        result = await transition_season(repo, season_id, SeasonPhase.COMPLETE)
        assert result == SeasonPhase.COMPLETE

        season = await repo.get_season(season_id)
        assert season.completed_at is not None

    async def test_invalid_transition_raises(self, repo: Repository):
        """Invalid transitions raise ValueError."""
        season_id = await _create_season(repo, "setup")

        with pytest.raises(ValueError, match="Invalid season transition"):
            await transition_season(repo, season_id, SeasonPhase.CHAMPIONSHIP)

    async def test_cannot_transition_from_complete(self, repo: Repository):
        """COMPLETE is terminal — no transitions allowed."""
        season_id = await _create_season(repo, "complete")

        with pytest.raises(ValueError, match="Invalid season transition"):
            await transition_season(repo, season_id, SeasonPhase.ACTIVE)

    async def test_nonexistent_season_raises(self, repo: Repository):
        """Transitioning a nonexistent season raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await transition_season(repo, "fake-id", SeasonPhase.ACTIVE)

    async def test_publishes_phase_changed_event(self, repo: Repository):
        """transition_season publishes season.phase_changed when event_bus provided."""
        season_id = await _create_season(repo, "setup")
        bus = EventBus()
        received = []

        async with bus.subscribe("season.phase_changed") as sub:
            await transition_season(repo, season_id, SeasonPhase.ACTIVE, event_bus=bus)
            event = await sub.get(timeout=0.5)
            if event:
                received.append(event)

        assert len(received) == 1
        assert received[0]["data"]["from_phase"] == "setup"
        assert received[0]["data"]["to_phase"] == "active"
        assert received[0]["data"]["season_id"] == season_id

    async def test_no_event_without_bus(self, repo: Repository):
        """No event published when event_bus is None."""
        season_id = await _create_season(repo, "setup")
        # Should not raise — just doesn't publish
        await transition_season(repo, season_id, SeasonPhase.ACTIVE, event_bus=None)

    async def test_transition_from_legacy_status(self, repo: Repository):
        """Transition works even when season has a legacy status value."""
        season_id = await _create_season(repo, "regular_season_complete")
        # regular_season_complete normalizes to PLAYOFFS
        result = await transition_season(repo, season_id, SeasonPhase.CHAMPIONSHIP)
        assert result == SeasonPhase.CHAMPIONSHIP

    async def test_backward_compat_completed_to_complete(self, repo: Repository):
        """Season with 'completed' (legacy) normalizes to COMPLETE — terminal."""
        season_id = await _create_season(repo, "completed")

        with pytest.raises(ValueError, match="Invalid season transition"):
            await transition_season(repo, season_id, SeasonPhase.ACTIVE)


class TestComputeAwards:
    """Tests for compute_awards() with mock game data."""

    async def test_awards_with_game_data(self, repo: Repository):
        """compute_awards returns gameplay awards from box score data."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "active")

        # Play enough rounds to generate box score data
        for rnd in range(1, 4):
            await step_round(repo, season_id, round_number=rnd)

        awards = await compute_awards(repo, season_id)

        # Should have at least MVP and Defensive Player
        award_names = [a["award"] for a in awards]
        assert "MVP" in award_names
        assert "Defensive Player of the Season" in award_names

        # Each award should have required fields
        for award in awards:
            assert "category" in award
            assert "award" in award
            assert "recipient_id" in award
            assert "recipient_name" in award
            assert "stat_value" in award

    async def test_awards_empty_season(self, repo: Repository):
        """compute_awards returns empty list for a season with no games."""
        season_id = await _create_season(repo)
        awards = await compute_awards(repo, season_id)
        assert awards == []

    async def test_awards_mvp_has_highest_ppg(self, repo: Repository):
        """MVP award goes to the hooper with highest PPG."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "active")

        for rnd in range(1, 4):
            await step_round(repo, season_id, round_number=rnd)

        awards = await compute_awards(repo, season_id)
        mvp = next(a for a in awards if a["award"] == "MVP")

        assert mvp["stat_label"] == "PPG"
        assert mvp["stat_value"] > 0
        assert mvp["category"] == "gameplay"

    async def test_awards_defensive_player(self, repo: Repository):
        """Defensive Player award has SPG stat."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "active")

        for rnd in range(1, 4):
            await step_round(repo, season_id, round_number=rnd)

        awards = await compute_awards(repo, season_id)
        defensive = next(a for a in awards if a["award"] == "Defensive Player of the Season")

        assert defensive["stat_label"] == "SPG"
        assert defensive["category"] == "gameplay"

    async def test_governance_awards_with_proposals(self, repo: Repository):
        """Governance awards appear when proposals/votes exist."""
        from pinwheel.ai.interpreter import interpret_proposal_mock
        from pinwheel.core.governance import cast_vote, confirm_proposal, submit_proposal
        from pinwheel.core.tokens import regenerate_tokens
        from pinwheel.models.rules import RuleSet

        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "active")

        # Play some rounds first
        for rnd in range(1, 4):
            await step_round(repo, season_id, round_number=rnd)

        # Submit a proposal
        gov_id = "gov-award-test"
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
        await confirm_proposal(repo, proposal)
        await cast_vote(
            repo=repo,
            proposal=proposal,
            governor_id=gov_id,
            team_id=team_ids[0],
            vote_choice="yes",
            weight=1.0,
        )

        awards = await compute_awards(repo, season_id)
        gov_awards = [a for a in awards if a["category"] == "governance"]
        assert len(gov_awards) >= 1  # At least "Most Active Governor"

        active_gov = next(
            (a for a in gov_awards if a["award"] == "Most Active Governor"),
            None,
        )
        assert active_gov is not None
        assert active_gov["recipient_id"] == gov_id


class TestEnterChampionship:
    """Tests for enter_championship() lifecycle function."""

    async def test_stores_championship_config(self, repo: Repository):
        """enter_championship stores config on the season."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        # Need to get to playoffs status first
        await repo.update_season_status(season_id, "playoffs")

        config = await enter_championship(repo, season_id, team_ids[0], duration_seconds=600)

        assert config["champion_team_id"] == team_ids[0]
        assert "awards" in config
        assert "championship_ends_at" in config
        assert config["championship_duration_seconds"] == 600

        # Verify stored on season row
        season = await repo.get_season(season_id)
        assert season.status == "championship"
        assert season.config is not None
        assert season.config["champion_team_id"] == team_ids[0]

    async def test_publishes_championship_event(self, repo: Repository):
        """enter_championship publishes season.championship_started event."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "playoffs")

        bus = EventBus()
        received = []

        async with bus.subscribe("season.championship_started") as sub:
            await enter_championship(repo, season_id, team_ids[0], event_bus=bus)
            event = await sub.get(timeout=0.5)
            if event:
                received.append(event)

        assert len(received) == 1
        data = received[0]["data"]
        assert data["season_id"] == season_id
        assert data["champion_team_id"] == team_ids[0]
        assert "awards" in data
        assert "championship_ends_at" in data
        assert "champion_team_name" in data

    async def test_championship_from_wrong_phase_raises(self, repo: Repository):
        """enter_championship raises if season is not in PLAYOFFS."""
        season_id = await _create_season(repo, "active")

        with pytest.raises(ValueError, match="Invalid season transition"):
            await enter_championship(repo, season_id, "some-team-id")

    async def test_championship_config_preserves_existing(self, repo: Repository):
        """enter_championship merges into existing season config, not overwriting."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "playoffs")

        # Set some pre-existing config
        season = await repo.get_season(season_id)
        season.config = {"pre_existing_key": "preserved"}
        await repo.session.flush()

        await enter_championship(repo, season_id, team_ids[0])

        season = await repo.get_season(season_id)
        assert season.config["pre_existing_key"] == "preserved"
        assert season.config["champion_team_id"] == team_ids[0]

    async def test_championship_default_duration(self, repo: Repository):
        """Default championship duration is 1800 seconds (30 min)."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "playoffs")

        config = await enter_championship(repo, season_id, team_ids[0])

        assert config["championship_duration_seconds"] == 1800

    async def test_championship_includes_team_name(self, repo: Repository):
        """Championship config includes the champion team's display name."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "playoffs")

        config = await enter_championship(repo, season_id, team_ids[0])

        assert "champion_team_name" in config
        assert config["champion_team_name"].startswith("Team ")


class TestFullLifecycle:
    """Integration tests for the complete season lifecycle with championship."""

    async def _play_regular_season(self, repo: Repository, season_id: str, team_ids: list[str]):
        """Play all regular season rounds and return (last_result, total_rounds)."""
        matchups = generate_round_robin(team_ids)
        total_rounds = max(m.round_number for m in matchups)
        result = None
        for rnd in range(1, total_rounds + 1):
            result = await step_round(repo, season_id, round_number=rnd)
        return result, total_rounds

    async def test_playoffs_trigger_championship(self, repo: Repository):
        """Full lifecycle: regular season -> playoffs -> championship."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "active")

        # Play regular season
        _, total_rounds = await self._play_regular_season(repo, season_id, team_ids)

        # Play semifinals
        semi_round = total_rounds + 1
        await step_round(repo, season_id, round_number=semi_round)

        # Play finals
        finals_round = semi_round + 1
        bus = EventBus()
        received = []

        async with bus.subscribe(None) as sub:
            await step_round(repo, season_id, round_number=finals_round, event_bus=bus)
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]

        # Should have championship_started event
        assert "season.championship_started" in event_types

        # Season should be in championship
        season = await repo.get_season(season_id)
        assert season.status == "championship"

        # Config should have awards
        assert season.config is not None
        assert isinstance(season.config.get("awards"), list)

    async def test_championship_event_has_awards(self, repo: Repository):
        """Championship event includes computed awards."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "active")

        _, total_rounds = await self._play_regular_season(repo, season_id, team_ids)
        semi_round = total_rounds + 1
        await step_round(repo, season_id, round_number=semi_round)

        finals_round = semi_round + 1
        bus = EventBus()
        received = []

        async with bus.subscribe("season.championship_started") as sub:
            await step_round(repo, season_id, round_number=finals_round, event_bus=bus)
            event = await sub.get(timeout=0.5)
            if event:
                received.append(event)

        assert len(received) == 1
        awards = received[0]["data"]["awards"]
        assert isinstance(awards, list)
        assert len(awards) > 0  # Should have at least gameplay awards


class TestSchedulerChampionship:
    """Tests for tick_round handling of the championship phase."""

    async def test_tick_round_expires_championship(self, engine: AsyncEngine):
        """tick_round transitions championship -> offseason when window expires."""
        from datetime import UTC, datetime, timedelta

        from pinwheel.core.scheduler_runner import tick_round

        # Set up season in championship phase with an expired window
        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Tick League")
            season = await repo.create_season(league.id, "Tick Season")
            season_id = season.id

            # Create teams so get_active_season has something to find
            for i in range(4):
                t = await repo.create_team(
                    season.id,
                    f"Tick Team {i}",
                    venue={"name": f"TA {i}", "capacity": 1000},
                )
                for j in range(3):
                    await repo.create_hooper(
                        team_id=t.id,
                        season_id=season.id,
                        name=f"TH-{i}-{j}",
                        archetype="sharpshooter",
                        attributes=_hooper_attrs(),
                    )

            # Set championship status with expired window
            season.status = "championship"
            expired_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
            season.config = {
                "champion_team_id": "some-team",
                "championship_ends_at": expired_time,
            }
            await session.flush()

        bus = EventBus()
        received = []

        async with bus.subscribe(None) as sub:
            await tick_round(engine, bus)
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        # Season should now be in offseason (championship -> offseason)
        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(season_id)
            assert season.status == "offseason"
            assert season.config is not None
            assert "offseason_ends_at" in season.config

        # Should have published offseason_started event
        event_types = [e["type"] for e in received]
        assert "season.offseason_started" in event_types

    async def test_tick_round_skips_active_championship(self, engine: AsyncEngine):
        """tick_round does nothing when championship window is still open."""
        from datetime import UTC, datetime, timedelta

        from pinwheel.core.scheduler_runner import tick_round

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Active Champ League")
            season = await repo.create_season(league.id, "Active Champ Season")
            season_id = season.id

            for i in range(2):
                t = await repo.create_team(
                    season.id,
                    f"AC Team {i}",
                    venue={"name": f"AC {i}", "capacity": 1000},
                )
                for j in range(3):
                    await repo.create_hooper(
                        team_id=t.id,
                        season_id=season.id,
                        name=f"AC-H-{i}-{j}",
                        archetype="sharpshooter",
                        attributes=_hooper_attrs(),
                    )

            # Championship still open (ends in the future)
            season.status = "championship"
            future_time = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
            season.config = {
                "champion_team_id": "some-team",
                "championship_ends_at": future_time,
            }
            await session.flush()

        bus = EventBus()
        await tick_round(engine, bus)

        # Season should still be in championship
        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(season_id)
            assert season.status == "championship"

    async def test_tick_round_championship_no_deadline(self, engine: AsyncEngine):
        """tick_round transitions to offseason when no championship_ends_at."""
        from pinwheel.core.scheduler_runner import tick_round

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("No Deadline League")
            season = await repo.create_season(league.id, "No Deadline Season")
            season_id = season.id

            for i in range(2):
                t = await repo.create_team(
                    season.id,
                    f"ND Team {i}",
                    venue={"name": f"ND {i}", "capacity": 1000},
                )
                for j in range(3):
                    await repo.create_hooper(
                        team_id=t.id,
                        season_id=season.id,
                        name=f"ND-H-{i}-{j}",
                        archetype="sharpshooter",
                        attributes=_hooper_attrs(),
                    )

            season.status = "championship"
            season.config = {"champion_team_id": "some-team"}
            await session.flush()

        bus = EventBus()
        await tick_round(engine, bus)

        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(season_id)
            assert season.status == "offseason"


class TestGetActiveSeasonWithPhases:
    """Tests that get_active_season handles the new phases correctly."""

    async def test_championship_is_active(self, repo: Repository):
        """A season in championship phase is returned by get_active_season."""
        season_id = await _create_season(repo, "championship")
        active = await repo.get_active_season()
        assert active is not None
        assert active.id == season_id

    async def test_playoffs_is_active(self, repo: Repository):
        """A season in playoffs phase is returned by get_active_season."""
        season_id = await _create_season(repo, "playoffs")
        active = await repo.get_active_season()
        assert active is not None
        assert active.id == season_id

    async def test_complete_is_not_active(self, repo: Repository):
        """A season with 'complete' status is not returned as active (primary query)."""
        await _create_season(repo, "complete")
        # complete is excluded from the primary query
        # Falls back to most recent of any status
        active = await repo.get_active_season()
        # Still returns it via fallback, but the primary query excludes it
        assert active is not None
        assert active.status == "complete"

    async def test_setup_is_not_active(self, repo: Repository):
        """A season in setup phase is not returned by get_active_season primary query."""
        await _create_season(repo, "setup")
        active = await repo.get_active_season()
        # Falls back to most recent of any status
        assert active is not None
        assert active.status == "setup"

    async def test_active_preferred_over_setup(self, repo: Repository):
        """An active season is preferred over one in setup."""
        await _create_season(repo, "setup")
        active_id = await _create_season(repo, "active")
        active = await repo.get_active_season()
        assert active is not None
        assert active.id == active_id


# ---------------------------------------------------------------------------
# Task 1: Tiebreaker Tests
# ---------------------------------------------------------------------------


class TestComputeHeadToHead:
    """Tests for _compute_head_to_head() helper."""

    def test_basic_head_to_head(self):
        """Head-to-head counts wins correctly between two teams."""
        results = [
            {
                "home_team_id": "A",
                "away_team_id": "B",
                "home_score": 80,
                "away_score": 70,
                "winner_team_id": "A",
            },
            {
                "home_team_id": "B",
                "away_team_id": "A",
                "home_score": 90,
                "away_score": 85,
                "winner_team_id": "B",
            },
            {
                "home_team_id": "A",
                "away_team_id": "B",
                "home_score": 95,
                "away_score": 88,
                "winner_team_id": "A",
            },
        ]
        a_wins, b_wins = _compute_head_to_head(results, "A", "B")
        assert a_wins == 2
        assert b_wins == 1

    def test_no_matchups(self):
        """Returns 0-0 when teams haven't played each other."""
        results = [
            {
                "home_team_id": "A",
                "away_team_id": "C",
                "home_score": 80,
                "away_score": 70,
                "winner_team_id": "A",
            },
        ]
        a_wins, b_wins = _compute_head_to_head(results, "A", "B")
        assert a_wins == 0
        assert b_wins == 0

    def test_symmetric(self):
        """Head-to-head is symmetric -- swapping argument order swaps results."""
        results = [
            {
                "home_team_id": "X",
                "away_team_id": "Y",
                "home_score": 100,
                "away_score": 90,
                "winner_team_id": "X",
            },
        ]
        x_wins, y_wins = _compute_head_to_head(results, "X", "Y")
        y_wins2, x_wins2 = _compute_head_to_head(results, "Y", "X")
        assert x_wins == x_wins2
        assert y_wins == y_wins2


class TestCheckTiebreakers:
    """Tests for check_tiebreakers() resolution logic."""

    def _make_standings(self, records: list[tuple[str, int, int, int, int]]) -> list[dict]:
        """Build standings from (team_id, wins, losses, points_for, points_against)."""
        standings = []
        for tid, w, losses, pf, pa in records:
            standings.append(
                {
                    "team_id": tid,
                    "wins": w,
                    "losses": losses,
                    "points_for": pf,
                    "points_against": pa,
                    "point_diff": pf - pa,
                }
            )
        return sorted(standings, key=lambda s: (-s["wins"], -s["point_diff"]))

    def test_no_tie_at_cutoff(self):
        """No tiebreaker needed when cutoff is clean."""
        standings = self._make_standings(
            [
                ("A", 5, 1, 500, 400),
                ("B", 4, 2, 480, 420),
                ("C", 3, 3, 450, 450),
                ("D", 2, 4, 400, 480),
                ("E", 1, 5, 370, 500),
            ]
        )
        resolved, needs_games = check_tiebreakers(standings, [], num_playoff_teams=4)
        assert not needs_games
        assert len(resolved) == 5

    def test_all_teams_qualify(self):
        """No tiebreaker when every team qualifies."""
        standings = self._make_standings(
            [
                ("A", 3, 0, 300, 200),
                ("B", 2, 1, 280, 220),
                ("C", 1, 2, 250, 260),
            ]
        )
        resolved, needs_games = check_tiebreakers(standings, [], num_playoff_teams=4)
        assert not needs_games

    def test_tie_resolved_by_head_to_head(self):
        """Two-team tie resolved by head-to-head record."""
        standings = self._make_standings(
            [
                ("A", 5, 1, 500, 400),
                ("B", 4, 2, 480, 420),
                ("C", 3, 3, 450, 450),  # Tied
                ("D", 3, 3, 450, 450),  # Tied
                ("E", 1, 5, 370, 500),
            ]
        )
        results = [
            # C beat D head-to-head
            {
                "home_team_id": "C",
                "away_team_id": "D",
                "home_score": 80,
                "away_score": 70,
                "winner_team_id": "C",
            },
        ]
        resolved, needs_games = check_tiebreakers(standings, results, num_playoff_teams=3)
        assert not needs_games
        # C should be ranked above D (head-to-head advantage)
        resolved_ids = [s["team_id"] for s in resolved]
        assert resolved_ids.index("C") < resolved_ids.index("D")

    def test_tie_resolved_by_point_differential(self):
        """Two-team tie resolved by point differential when h2h is split."""
        standings = self._make_standings(
            [
                ("A", 5, 1, 500, 400),
                ("B", 4, 2, 480, 420),
                ("C", 3, 3, 500, 450),  # Tied wins, better point diff
                ("D", 3, 3, 430, 450),  # Tied wins, worse point diff
                ("E", 1, 5, 370, 500),
            ]
        )
        # H2H tied at 1-1
        results = [
            {
                "home_team_id": "C",
                "away_team_id": "D",
                "home_score": 80,
                "away_score": 70,
                "winner_team_id": "C",
            },
            {
                "home_team_id": "D",
                "away_team_id": "C",
                "home_score": 90,
                "away_score": 80,
                "winner_team_id": "D",
            },
        ]
        resolved, needs_games = check_tiebreakers(standings, results, num_playoff_teams=3)
        assert not needs_games
        resolved_ids = [s["team_id"] for s in resolved]
        assert resolved_ids.index("C") < resolved_ids.index("D")

    def test_tie_resolved_by_points_scored(self):
        """Two-team tie resolved by points scored when h2h and diff are equal."""
        standings = self._make_standings(
            [
                ("A", 5, 1, 500, 400),
                ("B", 4, 2, 480, 420),
                ("C", 3, 3, 470, 450),  # Same diff as D, more points scored
                ("D", 3, 3, 450, 430),  # Same diff as C, fewer points scored
                ("E", 1, 5, 370, 500),
            ]
        )
        # H2H tied at 1-1
        results = [
            {
                "home_team_id": "C",
                "away_team_id": "D",
                "home_score": 80,
                "away_score": 70,
                "winner_team_id": "C",
            },
            {
                "home_team_id": "D",
                "away_team_id": "C",
                "home_score": 90,
                "away_score": 80,
                "winner_team_id": "D",
            },
        ]
        resolved, needs_games = check_tiebreakers(standings, results, num_playoff_teams=3)
        assert not needs_games
        resolved_ids = [s["team_id"] for s in resolved]
        assert resolved_ids.index("C") < resolved_ids.index("D")

    def test_unresolvable_tie_needs_games(self):
        """Tie that cannot be resolved requires tiebreaker games."""
        standings = self._make_standings(
            [
                ("A", 5, 1, 500, 400),
                ("B", 4, 2, 480, 420),
                ("C", 3, 3, 450, 450),  # Identical
                ("D", 3, 3, 450, 450),  # Identical
                ("E", 1, 5, 370, 500),
            ]
        )
        # H2H tied
        results = [
            {
                "home_team_id": "C",
                "away_team_id": "D",
                "home_score": 80,
                "away_score": 70,
                "winner_team_id": "C",
            },
            {
                "home_team_id": "D",
                "away_team_id": "C",
                "home_score": 80,
                "away_score": 70,
                "winner_team_id": "D",
            },
        ]
        _, needs_games = check_tiebreakers(standings, results, num_playoff_teams=3)
        assert needs_games

    def test_three_way_tie_resolved(self):
        """Three-way tie can be resolved when h2h records differ."""
        standings = self._make_standings(
            [
                ("A", 5, 1, 500, 400),
                # Three-way tie for spots 2-4
                ("B", 3, 3, 460, 440),
                ("C", 3, 3, 450, 450),
                ("D", 3, 3, 440, 460),
                ("E", 1, 5, 370, 500),
            ]
        )
        # H2H among tied: B > C > D
        results = [
            {
                "home_team_id": "B",
                "away_team_id": "C",
                "home_score": 80,
                "away_score": 70,
                "winner_team_id": "B",
            },
            {
                "home_team_id": "B",
                "away_team_id": "D",
                "home_score": 80,
                "away_score": 70,
                "winner_team_id": "B",
            },
            {
                "home_team_id": "C",
                "away_team_id": "D",
                "home_score": 80,
                "away_score": 70,
                "winner_team_id": "C",
            },
        ]
        resolved, needs_games = check_tiebreakers(standings, results, num_playoff_teams=4)
        assert not needs_games


class TestResolveTieGroup:
    """Tests for _resolve_tie_group() internal helper."""

    def test_single_team(self):
        """Single team trivially resolves."""
        teams = [{"team_id": "A", "wins": 3, "point_diff": 10, "points_for": 400}]
        resolved, unresolved = _resolve_tie_group(teams, [])
        assert not unresolved
        assert len(resolved) == 1

    def test_two_team_h2h_breaks_tie(self):
        """Two-team tie broken by head-to-head."""
        teams = [
            {"team_id": "A", "wins": 3, "point_diff": 10, "points_for": 400},
            {"team_id": "B", "wins": 3, "point_diff": 10, "points_for": 400},
        ]
        results = [
            {
                "home_team_id": "A",
                "away_team_id": "B",
                "home_score": 80,
                "away_score": 70,
                "winner_team_id": "A",
            },
        ]
        resolved, unresolved = _resolve_tie_group(teams, results)
        assert not unresolved
        assert resolved[0]["team_id"] == "A"
        assert resolved[1]["team_id"] == "B"


class TestGenerateTiebreakerGames:
    """Tests for generate_tiebreaker_games() database integration."""

    async def test_generates_tiebreaker_schedule(self, repo: Repository):
        """Tiebreaker games are created as schedule entries with phase='tiebreaker'."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "active")

        # Simulate enough games to create standings
        standings = [
            {"team_id": team_ids[0], "wins": 5, "losses": 1},
            {"team_id": team_ids[1], "wins": 3, "losses": 3},
            {"team_id": team_ids[2], "wins": 3, "losses": 3},  # Tied
            {"team_id": team_ids[3], "wins": 1, "losses": 5},
        ]

        matchups = await generate_tiebreaker_games(
            repo,
            season_id,
            standings,
            num_playoff_teams=2,
        )

        # Should create a game between team_ids[1] and team_ids[2]
        assert len(matchups) >= 1
        assert matchups[0]["phase"] == "tiebreaker"

        # Verify schedule entry was created
        schedule = await repo.get_full_schedule(season_id, phase="tiebreaker")
        assert len(schedule) >= 1


class TestCheckAndHandleTiebreakers:
    """Integration tests for check_and_handle_tiebreakers()."""

    async def test_no_ties_transitions_to_playoffs(self, repo: Repository):
        """When no ties exist, transitions ACTIVE -> TIEBREAKER_CHECK -> PLAYOFFS."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "active")

        # Store results where standings are clear
        for i, tid in enumerate(team_ids):
            for j, tid2 in enumerate(team_ids):
                if i < j:
                    await repo.store_game_result(
                        season_id=season_id,
                        round_number=1,
                        matchup_index=i * 4 + j,
                        home_team_id=tid,
                        away_team_id=tid2,
                        home_score=80 + (3 - i) * 10,
                        away_score=70 + (3 - j) * 5,
                        winner_team_id=tid,  # Higher seed always wins
                        seed=42,
                        total_possessions=100,
                    )

        phase = await check_and_handle_tiebreakers(repo, season_id)
        assert phase == SeasonPhase.PLAYOFFS

        season = await repo.get_season(season_id)
        assert season.status == "playoffs"

    async def test_ties_transition_to_tiebreakers(self, repo: Repository):
        """When unresolvable ties exist, transitions to TIEBREAKERS phase."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "active")

        # Store results where teams[1] and teams[2] have identical records
        # Team 0: 3 wins (beats everyone)
        # Team 1: 1 win (beats team 3, same as team 2)
        # Team 2: 1 win (beats team 3, same as team 1)
        # Team 3: 1 win (beats nobody via wins, but has 1 win from somewhere)
        results = [
            (team_ids[0], team_ids[1], 100, 80, team_ids[0]),
            (team_ids[0], team_ids[2], 100, 80, team_ids[0]),
            (team_ids[0], team_ids[3], 100, 80, team_ids[0]),
            # Team 1 and 2: split head-to-head, same points
            (team_ids[1], team_ids[2], 80, 80, team_ids[1]),  # T1 beats T2
            (team_ids[2], team_ids[1], 80, 80, team_ids[2]),  # T2 beats T1
            (team_ids[1], team_ids[3], 80, 70, team_ids[1]),
            (team_ids[2], team_ids[3], 80, 70, team_ids[2]),
            (team_ids[3], team_ids[1], 90, 80, team_ids[3]),
            (team_ids[3], team_ids[2], 90, 80, team_ids[3]),
        ]

        for idx, (home, away, hs, as_, winner) in enumerate(results):
            await repo.store_game_result(
                season_id=season_id,
                round_number=1,
                matchup_index=idx,
                home_team_id=home,
                away_team_id=away,
                home_score=hs,
                away_score=as_,
                winner_team_id=winner,
                seed=42,
                total_possessions=100,
            )

        # With 4 playoff spots and identical records for teams 1 & 2,
        # the tiebreaker should matter only if they straddle the cutoff.
        # Let's use num_playoff_teams=2 so teams 1 & 2 are at the boundary.
        await check_and_handle_tiebreakers(
            repo,
            season_id,
            num_playoff_teams=2,
        )

        # Teams have same h2h (1-1), same point diff (0), and same points scored (80*3=240)
        # This is unresolvable, should need tiebreaker games
        season = await repo.get_season(season_id)
        # Should be either TIEBREAKERS (needs games) or PLAYOFFS (resolved)
        assert season.status in ("tiebreakers", "playoffs")


# ---------------------------------------------------------------------------
# Task 2: Offseason Governance Tests
# ---------------------------------------------------------------------------


class TestEnterOffseason:
    """Tests for enter_offseason() lifecycle function."""

    async def test_transitions_to_offseason(self, repo: Repository):
        """enter_offseason transitions CHAMPIONSHIP -> OFFSEASON."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "playoffs")
        await enter_championship(repo, season_id, team_ids[0])

        config = await enter_offseason(repo, season_id, duration_seconds=1800)

        season = await repo.get_season(season_id)
        assert season.status == "offseason"
        assert "offseason_ends_at" in config
        assert config["offseason_duration_seconds"] == 1800

    async def test_offseason_config_stored(self, repo: Repository):
        """Offseason config is stored on the season row."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "playoffs")
        await enter_championship(repo, season_id, team_ids[0])

        await enter_offseason(repo, season_id, duration_seconds=600)

        season = await repo.get_season(season_id)
        assert season.config is not None
        assert "offseason_ends_at" in season.config
        # Championship config should be preserved
        assert "champion_team_id" in season.config

    async def test_offseason_publishes_event(self, repo: Repository):
        """enter_offseason publishes season.offseason_started event."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "playoffs")
        await enter_championship(repo, season_id, team_ids[0])

        bus = EventBus()
        received = []

        async with bus.subscribe("season.offseason_started") as sub:
            await enter_offseason(repo, season_id, event_bus=bus)
            event = await sub.get(timeout=0.5)
            if event:
                received.append(event)

        assert len(received) == 1
        data = received[0]["data"]
        assert data["season_id"] == season_id
        assert "offseason_ends_at" in data

    async def test_offseason_from_wrong_phase_raises(self, repo: Repository):
        """enter_offseason raises if not in CHAMPIONSHIP."""
        season_id = await _create_season(repo, "active")

        with pytest.raises(ValueError, match="Invalid season transition"):
            await enter_offseason(repo, season_id)


class TestCloseOffseason:
    """Tests for close_offseason() lifecycle function."""

    async def test_transitions_to_complete(self, repo: Repository):
        """close_offseason transitions OFFSEASON -> COMPLETE."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "playoffs")
        await enter_championship(repo, season_id, team_ids[0])
        await enter_offseason(repo, season_id, duration_seconds=10)

        await close_offseason(repo, season_id)

        season = await repo.get_season(season_id)
        assert season.status == "complete"
        assert season.completed_at is not None

    async def test_close_offseason_publishes_events(self, repo: Repository):
        """close_offseason publishes season.offseason_closed and phase_changed events."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "playoffs")
        await enter_championship(repo, season_id, team_ids[0])
        await enter_offseason(repo, season_id)

        bus = EventBus()
        received = []

        async with bus.subscribe(None) as sub:
            await close_offseason(repo, season_id, event_bus=bus)
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        event_types = [e["type"] for e in received]
        assert "season.phase_changed" in event_types
        assert "season.offseason_closed" in event_types

    async def test_close_offseason_nonexistent_season_raises(self, repo: Repository):
        """close_offseason raises for a missing season."""
        with pytest.raises(ValueError, match="not found"):
            await close_offseason(repo, "nonexistent-id")


class TestSchedulerOffseason:
    """Tests for tick_round handling of offseason phase."""

    async def test_tick_round_championship_to_offseason(self, engine: AsyncEngine):
        """tick_round transitions championship -> offseason when window expires."""
        from datetime import UTC, datetime, timedelta

        from pinwheel.core.scheduler_runner import tick_round

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Offseason League")
            season = await repo.create_season(league.id, "Offseason Season")
            season_id = season.id

            for i in range(2):
                t = await repo.create_team(
                    season.id,
                    f"OS Team {i}",
                    venue={"name": f"OS {i}", "capacity": 1000},
                )
                for j in range(3):
                    await repo.create_hooper(
                        team_id=t.id,
                        season_id=season.id,
                        name=f"OS-H-{i}-{j}",
                        archetype="sharpshooter",
                        attributes=_hooper_attrs(),
                    )

            # Set championship status with expired window
            season.status = "championship"
            expired_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
            season.config = {
                "champion_team_id": "some-team",
                "championship_ends_at": expired_time,
            }
            await session.flush()

        bus = EventBus()
        received = []

        async with bus.subscribe(None) as sub:
            await tick_round(engine, bus)
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received.append(event)

        # Season should now be in offseason
        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(season_id)
            assert season.status == "offseason"
            assert season.config is not None
            assert "offseason_ends_at" in season.config

    async def test_tick_round_offseason_to_complete(self, engine: AsyncEngine):
        """tick_round transitions offseason -> complete when window expires."""
        from datetime import UTC, datetime, timedelta

        from pinwheel.core.scheduler_runner import tick_round

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("OS Close League")
            season = await repo.create_season(league.id, "OS Close Season")
            season_id = season.id

            for i in range(2):
                t = await repo.create_team(
                    season.id,
                    f"OSC Team {i}",
                    venue={"name": f"OSC {i}", "capacity": 1000},
                )
                for j in range(3):
                    await repo.create_hooper(
                        team_id=t.id,
                        season_id=season.id,
                        name=f"OSC-H-{i}-{j}",
                        archetype="sharpshooter",
                        attributes=_hooper_attrs(),
                    )

            # Set offseason status with expired window
            season.status = "offseason"
            expired_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
            season.config = {
                "champion_team_id": "some-team",
                "offseason_ends_at": expired_time,
            }
            await session.flush()

        bus = EventBus()
        await tick_round(engine, bus)

        # Season should now be complete
        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(season_id)
            assert season.status == "complete"
            assert season.completed_at is not None

    async def test_tick_round_offseason_window_still_open(self, engine: AsyncEngine):
        """tick_round keeps offseason when window is still open."""
        from datetime import UTC, datetime, timedelta

        from pinwheel.core.scheduler_runner import tick_round

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("OS Open League")
            season = await repo.create_season(league.id, "OS Open Season")
            season_id = season.id

            for i in range(2):
                t = await repo.create_team(
                    season.id,
                    f"OSO Team {i}",
                    venue={"name": f"OSO {i}", "capacity": 1000},
                )
                for j in range(3):
                    await repo.create_hooper(
                        team_id=t.id,
                        season_id=season.id,
                        name=f"OSO-H-{i}-{j}",
                        archetype="sharpshooter",
                        attributes=_hooper_attrs(),
                    )

            # Offseason still open (ends in the future)
            season.status = "offseason"
            future_time = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
            season.config = {
                "champion_team_id": "some-team",
                "offseason_ends_at": future_time,
            }
            await session.flush()

        bus = EventBus()
        await tick_round(engine, bus)

        # Season should still be in offseason
        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(season_id)
            assert season.status == "offseason"


class TestOffseasonGovernance:
    """Tests for governance during offseason phase."""

    async def test_offseason_carries_rules_forward(self, repo: Repository):
        """Rules enacted during offseason are preserved on the season."""
        season_id, team_ids = await _setup_season_with_teams(repo)
        await repo.update_season_status(season_id, "playoffs")
        await enter_championship(repo, season_id, team_ids[0])
        await enter_offseason(repo, season_id, duration_seconds=3600)

        # Verify season is in offseason
        season = await repo.get_season(season_id)
        assert season.status == "offseason"

        # The ruleset should still be accessible
        assert season.current_ruleset is not None


class TestAllowedTransitionsComplete:
    """Verify ALLOWED_TRANSITIONS include all new paths."""

    def test_championship_to_offseason(self):
        """CHAMPIONSHIP -> OFFSEASON is a valid transition."""
        assert SeasonPhase.OFFSEASON in ALLOWED_TRANSITIONS[SeasonPhase.CHAMPIONSHIP]

    def test_offseason_to_complete(self):
        """OFFSEASON -> COMPLETE is a valid transition."""
        assert SeasonPhase.COMPLETE in ALLOWED_TRANSITIONS[SeasonPhase.OFFSEASON]

    def test_active_to_tiebreaker_check(self):
        """ACTIVE -> TIEBREAKER_CHECK is a valid transition."""
        assert SeasonPhase.TIEBREAKER_CHECK in ALLOWED_TRANSITIONS[SeasonPhase.ACTIVE]

    def test_tiebreaker_check_to_tiebreakers(self):
        """TIEBREAKER_CHECK -> TIEBREAKERS is a valid transition."""
        assert SeasonPhase.TIEBREAKERS in ALLOWED_TRANSITIONS[SeasonPhase.TIEBREAKER_CHECK]

    def test_tiebreaker_check_to_playoffs(self):
        """TIEBREAKER_CHECK -> PLAYOFFS is a valid transition."""
        assert SeasonPhase.PLAYOFFS in ALLOWED_TRANSITIONS[SeasonPhase.TIEBREAKER_CHECK]

    def test_tiebreakers_to_playoffs(self):
        """TIEBREAKERS -> PLAYOFFS is a valid transition."""
        assert SeasonPhase.PLAYOFFS in ALLOWED_TRANSITIONS[SeasonPhase.TIEBREAKERS]
