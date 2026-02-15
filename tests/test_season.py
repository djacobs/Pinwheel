"""Tests for the season management module (start_new_season, carry_over_teams)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.core.season import (
    carry_over_teams,
    compute_awards,
    regenerate_all_governor_tokens,
    start_new_season,
)
from pinwheel.core.tokens import get_token_balance
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.rules import DEFAULT_RULESET


@pytest.fixture
async def engine() -> AsyncEngine:
    """Create an in-memory SQLite engine with all tables."""
    eng = create_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng  # type: ignore[misc]
    await eng.dispose()


@pytest.fixture
async def repo(engine: AsyncEngine) -> Repository:
    """Yield a repository with a session bound to the in-memory database."""
    async with get_session(engine) as session:
        yield Repository(session)  # type: ignore[misc]


async def _seed_completed_season(
    repo: Repository,
    league_id: str,
    season_name: str = "Season 1",
    ruleset_data: dict | None = None,
) -> str:
    """Create a completed season with teams, hoopers, and governors.

    Returns the season ID.
    """
    if ruleset_data is None:
        ruleset_data = DEFAULT_RULESET.model_dump()

    season = await repo.create_season(
        league_id=league_id,
        name=season_name,
        starting_ruleset=ruleset_data,
    )
    season.status = "completed"
    from datetime import UTC, datetime

    season.completed_at = datetime.now(UTC)
    # Modify the ruleset to simulate governance changes
    season.current_ruleset = ruleset_data
    await repo.session.flush()

    # Create teams with hoopers
    team_a = await repo.create_team(
        season_id=season.id,
        name="Rose City Thorns",
        color="#CC0000",
        color_secondary="#FFFFFF",
        motto="Bloom Where They Plant You",
        venue={"name": "The Thorn Garden", "capacity": 18000},
    )
    await repo.create_hooper(
        team_id=team_a.id,
        season_id=season.id,
        name="Sharpshooter-Alpha",
        archetype="sharpshooter",
        attributes={"scoring": 80, "passing": 40, "defense": 30},
        moves=[{"name": "Heat Check", "trigger": "made_three", "effect": "+10% 3pt"}],
    )
    await repo.create_hooper(
        team_id=team_a.id,
        season_id=season.id,
        name="Playmaker-Alpha",
        archetype="playmaker",
        attributes={"scoring": 50, "passing": 85, "defense": 40},
    )

    team_b = await repo.create_team(
        season_id=season.id,
        name="Burnside Breakers",
        color="#0066CC",
        color_secondary="#333333",
        motto="Break the Pattern",
        venue={"name": "Breaker Bay Arena", "capacity": 6200},
    )
    await repo.create_hooper(
        team_id=team_b.id,
        season_id=season.id,
        name="Enforcer-Beta",
        archetype="enforcer",
        attributes={"scoring": 40, "passing": 30, "defense": 85},
    )
    await repo.create_hooper(
        team_id=team_b.id,
        season_id=season.id,
        name="Wildcard-Beta",
        archetype="wildcard",
        attributes={"scoring": 60, "passing": 55, "defense": 50},
    )

    # Create governors (players enrolled in teams)
    gov_a = await repo.get_or_create_player(
        discord_id="111111",
        username="governor_alpha",
    )
    await repo.enroll_player(gov_a.id, team_a.id, season.id)

    gov_b = await repo.get_or_create_player(
        discord_id="222222",
        username="governor_beta",
    )
    await repo.enroll_player(gov_b.id, team_b.id, season.id)

    return season.id


class TestStartNewSeasonDefaultRules:
    """Test new season creation with default rules."""

    async def test_creates_season_with_default_ruleset(self, repo: Repository) -> None:
        league = await repo.create_league("Test League")
        old_season_id = await _seed_completed_season(repo, league.id)

        new_season = await start_new_season(
            repo=repo,
            league_id=league.id,
            season_name="Season 2",
            carry_forward_rules=False,
        )

        assert new_season.name == "Season 2"
        assert new_season.league_id == league.id
        assert new_season.status == "active"
        assert new_season.starting_ruleset == DEFAULT_RULESET.model_dump()
        assert new_season.current_ruleset == DEFAULT_RULESET.model_dump()
        assert new_season.id != old_season_id

    async def test_raises_for_invalid_league(self, repo: Repository) -> None:
        with pytest.raises(ValueError, match="not found"):
            await start_new_season(
                repo=repo,
                league_id="nonexistent-league",
                season_name="Season 2",
            )


class TestStartNewSeasonCarriedRules:
    """Test new season creation with carried-forward rules."""

    async def test_carries_forward_from_specified_season(self, repo: Repository) -> None:
        league = await repo.create_league("Test League")
        custom_rules = DEFAULT_RULESET.model_dump()
        custom_rules["three_point_value"] = 5
        custom_rules["shot_clock_seconds"] = 20
        old_season_id = await _seed_completed_season(
            repo,
            league.id,
            ruleset_data=custom_rules,
        )

        new_season = await start_new_season(
            repo=repo,
            league_id=league.id,
            season_name="Season 2",
            carry_forward_rules=True,
            previous_season_id=old_season_id,
        )

        assert new_season.starting_ruleset is not None
        assert new_season.starting_ruleset["three_point_value"] == 5
        assert new_season.starting_ruleset["shot_clock_seconds"] == 20

    async def test_carries_forward_from_latest_completed(self, repo: Repository) -> None:
        league = await repo.create_league("Test League")
        custom_rules = DEFAULT_RULESET.model_dump()
        custom_rules["three_point_value"] = 7
        await _seed_completed_season(
            repo,
            league.id,
            season_name="Season 1",
            ruleset_data=custom_rules,
        )

        new_season = await start_new_season(
            repo=repo,
            league_id=league.id,
            season_name="Season 2",
            carry_forward_rules=True,
            # No previous_season_id -- should auto-find latest
        )

        assert new_season.starting_ruleset is not None
        assert new_season.starting_ruleset["three_point_value"] == 7

    async def test_falls_back_to_defaults_when_no_completed_season(
        self,
        repo: Repository,
    ) -> None:
        league = await repo.create_league("Test League")
        # No previous seasons at all

        new_season = await start_new_season(
            repo=repo,
            league_id=league.id,
            season_name="Season 1",
            carry_forward_rules=True,
        )

        assert new_season.starting_ruleset == DEFAULT_RULESET.model_dump()

    async def test_raises_for_invalid_previous_season(self, repo: Repository) -> None:
        league = await repo.create_league("Test League")

        with pytest.raises(ValueError, match="not found"):
            await start_new_season(
                repo=repo,
                league_id=league.id,
                season_name="Season 2",
                carry_forward_rules=True,
                previous_season_id="nonexistent-season",
            )


class TestTeamCarryOver:
    """Test that team carry-over copies teams and hoopers."""

    async def test_copies_teams_to_new_season(self, repo: Repository) -> None:
        league = await repo.create_league("Test League")
        old_season_id = await _seed_completed_season(repo, league.id)

        new_season = await repo.create_season(league.id, "Season 2")
        new_team_ids = await carry_over_teams(repo, old_season_id, new_season.id)

        assert len(new_team_ids) == 2

        new_teams = await repo.get_teams_for_season(new_season.id)
        assert len(new_teams) == 2
        new_team_names = {t.name for t in new_teams}
        assert "Rose City Thorns" in new_team_names
        assert "Burnside Breakers" in new_team_names

    async def test_copies_hoopers_with_fresh_records(self, repo: Repository) -> None:
        league = await repo.create_league("Test League")
        old_season_id = await _seed_completed_season(repo, league.id)

        new_season = await repo.create_season(league.id, "Season 2")
        await carry_over_teams(repo, old_season_id, new_season.id)

        new_teams = await repo.get_teams_for_season(new_season.id)
        total_hoopers = sum(len(t.hoopers) for t in new_teams)
        assert total_hoopers == 4  # 2 per team

        # Check that hoopers have same names/archetypes but new IDs
        old_teams = await repo.get_teams_for_season(old_season_id)
        old_hooper_ids = {h.id for t in old_teams for h in t.hoopers}
        new_hooper_ids = {h.id for t in new_teams for h in t.hoopers}
        assert old_hooper_ids.isdisjoint(new_hooper_ids)

        # Verify attributes carried over
        for new_team in new_teams:
            for hooper in new_team.hoopers:
                assert hooper.archetype in {"sharpshooter", "playmaker", "enforcer", "wildcard"}
                assert "scoring" in hooper.attributes

    async def test_preserves_team_properties(self, repo: Repository) -> None:
        league = await repo.create_league("Test League")
        old_season_id = await _seed_completed_season(repo, league.id)

        new_season = await repo.create_season(league.id, "Season 2")
        await carry_over_teams(repo, old_season_id, new_season.id)

        new_teams = await repo.get_teams_for_season(new_season.id)
        thorns = next(t for t in new_teams if t.name == "Rose City Thorns")
        assert thorns.color == "#CC0000"
        assert thorns.color_secondary == "#FFFFFF"
        assert thorns.motto == "Bloom Where They Plant You"
        assert thorns.venue is not None
        assert thorns.venue["name"] == "The Thorn Garden"

    async def test_backstories_survive_season_transition(self, repo: Repository) -> None:
        """Hooper backstories written via /bio must carry over to the new season."""
        league = await repo.create_league("Test League")
        old_season_id = await _seed_completed_season(repo, league.id)

        # Set backstories on the old season's hoopers (simulating /bio usage)
        old_teams = await repo.get_teams_for_season(old_season_id)
        for team in old_teams:
            for hooper in team.hoopers:
                hooper.backstory = f"The legend of {hooper.name} began on the streets."
                await repo.session.flush()

        # Carry over teams to a new season
        new_season = await repo.create_season(league.id, "Season 2")
        await carry_over_teams(repo, old_season_id, new_season.id)

        # Verify backstories survived the transition
        new_teams = await repo.get_teams_for_season(new_season.id)
        for new_team in new_teams:
            for hooper in new_team.hoopers:
                assert hooper.backstory, (
                    f"Hooper {hooper.name} lost backstory during season transition"
                )
                assert hooper.backstory.startswith("The legend of ")

    async def test_empty_backstory_does_not_break_carryover(self, repo: Repository) -> None:
        """Hoopers without backstories should carry over without errors."""
        league = await repo.create_league("Test League")
        old_season_id = await _seed_completed_season(repo, league.id)

        # Leave backstories at their default (empty string) -- no /bio calls
        new_season = await repo.create_season(league.id, "Season 2")
        await carry_over_teams(repo, old_season_id, new_season.id)

        new_teams = await repo.get_teams_for_season(new_season.id)
        total_hoopers = sum(len(t.hoopers) for t in new_teams)
        assert total_hoopers == 4  # All hoopers still carried over


class TestGovernorEnrollmentCarryOver:
    """Test that governor enrollments are carried over."""

    async def test_governors_enrolled_in_new_season(self, repo: Repository) -> None:
        league = await repo.create_league("Test League")
        old_season_id = await _seed_completed_season(repo, league.id)

        new_season = await repo.create_season(league.id, "Season 2")
        await carry_over_teams(repo, old_season_id, new_season.id)

        new_teams = await repo.get_teams_for_season(new_season.id)
        total_governors = 0
        for team in new_teams:
            govs = await repo.get_governors_for_team(team.id, new_season.id)
            total_governors += len(govs)

        assert total_governors == 2  # Both governors carried over


class TestTokenRegeneration:
    """Test that tokens are regenerated for the new season."""

    async def test_tokens_regenerated_for_new_season(self, repo: Repository) -> None:
        league = await repo.create_league("Test League")
        await _seed_completed_season(repo, league.id)

        new_season = await start_new_season(
            repo=repo,
            league_id=league.id,
            season_name="Season 2",
        )

        # Check that governors have fresh tokens in the new season
        new_teams = await repo.get_teams_for_season(new_season.id)
        for team in new_teams:
            govs = await repo.get_governors_for_team(team.id, new_season.id)
            for gov in govs:
                balance = await get_token_balance(repo, gov.id, new_season.id)
                assert balance.propose == 2  # DEFAULT_PROPOSE_PER_WINDOW
                assert balance.amend == 2  # DEFAULT_AMEND_PER_WINDOW
                assert balance.boost == 2  # DEFAULT_BOOST_PER_WINDOW

    async def test_regenerate_all_governor_tokens(self, repo: Repository) -> None:
        league = await repo.create_league("Test League")
        old_season_id = await _seed_completed_season(repo, league.id)

        # Create new season and carry teams manually
        new_season = await repo.create_season(league.id, "Season 2")
        await carry_over_teams(repo, old_season_id, new_season.id)

        count = await regenerate_all_governor_tokens(repo, new_season.id)
        assert count == 2  # Two governors


class TestScheduleGeneration:
    """Test that schedule is generated for the new season."""

    async def test_schedule_generated_for_new_season(self, repo: Repository) -> None:
        league = await repo.create_league("Test League")
        await _seed_completed_season(repo, league.id)

        new_season = await start_new_season(
            repo=repo,
            league_id=league.id,
            season_name="Season 2",
        )

        # With 2 teams, round-robin produces 1 round
        # With default round_robins_per_season=3, that's 3 rounds
        schedule_r1 = await repo.get_schedule_for_round(new_season.id, 1)
        assert len(schedule_r1) >= 1

    async def test_no_schedule_with_no_teams(self, repo: Repository) -> None:
        league = await repo.create_league("Test League")
        # No previous season means no teams to carry over

        new_season = await start_new_season(
            repo=repo,
            league_id=league.id,
            season_name="Season 1",
        )

        schedule = await repo.get_schedule_for_round(new_season.id, 1)
        assert len(schedule) == 0


class TestStartNewSeasonIntegration:
    """End-to-end integration tests for the full start_new_season flow."""

    async def test_full_flow_with_defaults(self, repo: Repository) -> None:
        """Complete season transition with default rules."""
        league = await repo.create_league("Test League")
        await _seed_completed_season(repo, league.id)

        new_season = await start_new_season(
            repo=repo,
            league_id=league.id,
            season_name="Season 2",
            carry_forward_rules=False,
        )

        # Season created with correct status
        assert new_season.status == "active"

        # Teams carried over
        teams = await repo.get_teams_for_season(new_season.id)
        assert len(teams) == 2

        # Hoopers carried over
        total_hoopers = sum(len(t.hoopers) for t in teams)
        assert total_hoopers == 4

        # Governors carried over
        total_govs = 0
        for team in teams:
            govs = await repo.get_governors_for_team(team.id, new_season.id)
            total_govs += len(govs)
        assert total_govs == 2

        # Schedule generated
        schedule = await repo.get_schedule_for_round(new_season.id, 1)
        assert len(schedule) >= 1

        # Default rules applied
        assert new_season.starting_ruleset == DEFAULT_RULESET.model_dump()

    async def test_full_flow_with_carried_rules(self, repo: Repository) -> None:
        """Complete season transition with carried-forward rules."""
        league = await repo.create_league("Test League")
        custom_rules = DEFAULT_RULESET.model_dump()
        custom_rules["three_point_value"] = 4
        custom_rules["shot_clock_seconds"] = 25
        await _seed_completed_season(
            repo,
            league.id,
            ruleset_data=custom_rules,
        )

        new_season = await start_new_season(
            repo=repo,
            league_id=league.id,
            season_name="Season 2",
            carry_forward_rules=True,
        )

        # Carried-forward rules applied
        assert new_season.starting_ruleset is not None
        assert new_season.starting_ruleset["three_point_value"] == 4
        assert new_season.starting_ruleset["shot_clock_seconds"] == 25

        # Everything else still works
        teams = await repo.get_teams_for_season(new_season.id)
        assert len(teams) == 2

    async def test_previous_season_completed_on_new_season(self, repo: Repository) -> None:
        """Starting a new season should complete the previous one."""
        league = await repo.create_league("Test League")
        old = await _seed_completed_season(repo, league.id)
        # Set old season back to active (simulating admin running /new-season mid-season)
        old_season = await repo.get_season(old)
        old_season.status = "active"
        await repo.session.flush()

        new_season = await start_new_season(
            repo=repo,
            league_id=league.id,
            season_name="Season 2",
            previous_season_id=old,
        )

        # Old season should be completed
        refreshed_old = await repo.get_season(old)
        assert refreshed_old.status == "complete"
        assert new_season.status == "active"

    async def test_untallied_proposals_resolved_on_season_close(
        self, repo: Repository
    ) -> None:
        """Untallied proposals should be tallied (and fail at 0-0) when season closes."""
        league = await repo.create_league("Test League")
        old = await _seed_completed_season(repo, league.id)
        old_season = await repo.get_season(old)
        old_season.status = "active"
        await repo.session.flush()

        # Submit and confirm a proposal in the old season
        teams = await repo.get_teams_for_season(old)
        team = teams[0]
        player = await repo.get_or_create_player("disc123", "tester")
        await repo.enroll_player(player.id, old, team.id)
        await repo.append_event(
            event_type="proposal.submitted",
            aggregate_id="prop-1",
            aggregate_type="proposal",
            season_id=old,
            governor_id=player.id,
            payload={
                "id": "prop-1",
                "raw_text": "test proposal",
                "status": "submitted",
                "season_id": old,
                "governor_id": player.id,
                "team_id": team.id,
                "tier": 1,
                "interpretation": {"parameter": "shot_clock_seconds", "new_value": 20,
                                   "old_value": 30, "confidence": 0.9},
            },
        )
        await repo.append_event(
            event_type="proposal.confirmed",
            aggregate_id="prop-1",
            aggregate_type="proposal",
            season_id=old,
            governor_id=player.id,
            payload={"proposal_id": "prop-1"},
        )

        # Start new season — should tally the orphaned proposal
        new_season = await start_new_season(
            repo=repo,
            league_id=league.id,
            season_name="Season 2",
            previous_season_id=old,
        )

        # The proposal should now be resolved (failed — 0 votes, ties fail)
        resolved = await repo.get_events_by_type(
            season_id=old,
            event_types=["proposal.passed", "proposal.failed"],
        )
        assert len(resolved) == 1
        assert resolved[0].event_type == "proposal.failed"
        assert new_season.status == "active"


class TestComputeAwardsTradeAccepted:
    """Test that compute_awards() correctly queries trade.accepted events."""

    async def test_coalition_builder_uses_trade_accepted(self, repo: Repository) -> None:
        """Verify Coalition Builder award picks up trade.accepted events (not trade.completed)."""
        league = await repo.create_league("Test League")
        season = await repo.create_season(league.id, "Season 1")

        # Create a governor
        player_a = await repo.get_or_create_player("aaa111", "governor_a")
        player_b = await repo.get_or_create_player("bbb222", "governor_b")

        # Emit trade.accepted events (the event type accept_trade() actually produces)
        await repo.append_event(
            event_type="trade.accepted",
            aggregate_id="trade-1",
            aggregate_type="trade",
            season_id=season.id,
            governor_id=player_a.id,
            payload={
                "trade_id": "trade-1",
                "from_governor_id": player_a.id,
                "to_governor_id": player_b.id,
            },
        )
        await repo.append_event(
            event_type="trade.accepted",
            aggregate_id="trade-2",
            aggregate_type="trade",
            season_id=season.id,
            governor_id=player_b.id,
            payload={
                "trade_id": "trade-2",
                "from_governor_id": player_b.id,
                "to_governor_id": player_a.id,
            },
        )

        awards = await compute_awards(repo, season.id)

        # Find the Coalition Builder award
        coalition_awards = [a for a in awards if a["award"] == "Coalition Builder"]
        assert len(coalition_awards) == 1
        # Both governors participated in 2 trades each
        assert coalition_awards[0]["stat_value"] == 2
