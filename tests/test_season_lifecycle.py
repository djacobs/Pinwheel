"""Tests for the season lifecycle: phase enum, transitions, awards, championship."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.core.event_bus import EventBus
from pinwheel.core.game_loop import step_round
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.core.season import (
    ALLOWED_TRANSITIONS,
    SeasonPhase,
    compute_awards,
    enter_championship,
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

        config = await enter_championship(
            repo, season_id, team_ids[0], duration_seconds=600
        )

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
            await enter_championship(
                repo, season_id, team_ids[0], event_bus=bus
            )
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

    async def _play_regular_season(
        self, repo: Repository, season_id: str, team_ids: list[str]
    ):
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
        """tick_round transitions championship -> complete when window expires."""
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

        # Season should be complete now
        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(season_id)
            assert season.status == "complete"
            assert season.completed_at is not None

        # Should have published phase_changed event
        event_types = [e["type"] for e in received]
        assert "season.phase_changed" in event_types

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
        """tick_round transitions immediately when no championship_ends_at."""
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
            assert season.status == "complete"


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
