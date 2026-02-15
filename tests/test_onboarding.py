"""Tests for new player onboarding -- league context and embed generation.

Tests cover:
- build_league_context() data gathering across season phases
- build_onboarding_embed() formatting and team highlighting
- /status command integration (via mocked bot)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.config import Settings
from pinwheel.core.event_bus import EventBus
from pinwheel.core.onboarding import LeagueContext, build_league_context
from pinwheel.core.season import SeasonPhase
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.discord.bot import PinwheelBot
from pinwheel.discord.embeds import build_onboarding_embed

# ---------------------------------------------------------------------------
# DB Fixtures
# ---------------------------------------------------------------------------


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


async def _seed_league(repo: Repository) -> tuple[str, str, list[str]]:
    """Seed a basic league with 4 teams and return (league_id, season_id, team_ids)."""
    league = await repo.create_league("Test League")
    season = await repo.create_season(league.id, "Season ONE")
    season.status = "active"
    await repo.session.flush()

    team_names = [
        ("Rose City Thorns", "#CC0000"),
        ("Bridge City Bolts", "#0000CC"),
        ("Stumptown Stars", "#00CC00"),
        ("PDX Voltage", "#CCCC00"),
    ]
    team_ids: list[str] = []
    for name, color in team_names:
        team = await repo.create_team(season.id, name, color=color)
        # Create 3 hoopers per team
        for i in range(3):
            await repo.create_hooper(
                team_id=team.id,
                season_id=season.id,
                name=f"{name} Hooper {i + 1}",
                archetype="Sharpshooter",
                attributes={"shooting": 80, "defense": 60},
            )
        team_ids.append(team.id)

    return league.id, season.id, team_ids


async def _seed_games(
    repo: Repository,
    season_id: str,
    team_ids: list[str],
    num_rounds: int = 3,
) -> None:
    """Seed game results for a few rounds."""
    import random

    random.seed(42)
    matchup_idx = 0
    for rn in range(1, num_rounds + 1):
        # Create schedule entries
        pairs = [(team_ids[0], team_ids[1]), (team_ids[2], team_ids[3])]
        for mi, (home, away) in enumerate(pairs):
            await repo.create_schedule_entry(
                season_id=season_id,
                round_number=rn,
                matchup_index=mi,
                home_team_id=home,
                away_team_id=away,
                phase="regular",
            )
            home_score = random.randint(80, 110)
            away_score = random.randint(80, 110)
            winner = home if home_score > away_score else away
            await repo.store_game_result(
                season_id=season_id,
                round_number=rn,
                matchup_index=mi,
                home_team_id=home,
                away_team_id=away,
                home_score=home_score,
                away_score=away_score,
                winner_team_id=winner,
                seed=42,
                total_possessions=100,
            )
            matchup_idx += 1


async def _seed_proposals(repo: Repository, season_id: str) -> None:
    """Seed some governance proposals."""
    # Active proposal
    await repo.append_event(
        event_type="proposal.submitted",
        aggregate_id="prop-1",
        aggregate_type="proposal",
        season_id=season_id,
        payload={
            "id": "prop-1",
            "raw_text": "Make three-pointers worth 5 points",
            "tier": 1,
        },
        round_number=2,
        governor_id="gov-1",
    )
    await repo.append_event(
        event_type="proposal.confirmed",
        aggregate_id="prop-1",
        aggregate_type="proposal",
        season_id=season_id,
        payload={"proposal_id": "prop-1"},
        round_number=2,
    )

    # Second active proposal
    await repo.append_event(
        event_type="proposal.submitted",
        aggregate_id="prop-2",
        aggregate_type="proposal",
        season_id=season_id,
        payload={
            "id": "prop-2",
            "raw_text": "Require 3 passes before shooting",
            "tier": 2,
        },
        round_number=3,
        governor_id="gov-2",
    )
    await repo.append_event(
        event_type="proposal.confirmed",
        aggregate_id="prop-2",
        aggregate_type="proposal",
        season_id=season_id,
        payload={"proposal_id": "prop-2"},
        round_number=3,
    )

    # Passed proposal (should NOT appear in active)
    await repo.append_event(
        event_type="proposal.submitted",
        aggregate_id="prop-3",
        aggregate_type="proposal",
        season_id=season_id,
        payload={
            "id": "prop-3",
            "raw_text": "Shorten shot clock to 18 seconds",
            "tier": 1,
        },
        round_number=1,
        governor_id="gov-1",
    )
    await repo.append_event(
        event_type="proposal.confirmed",
        aggregate_id="prop-3",
        aggregate_type="proposal",
        season_id=season_id,
        payload={"proposal_id": "prop-3"},
        round_number=1,
    )
    await repo.append_event(
        event_type="proposal.passed",
        aggregate_id="prop-3",
        aggregate_type="proposal",
        season_id=season_id,
        payload={"proposal_id": "prop-3"},
        round_number=1,
    )


async def _seed_rule_changes(repo: Repository, season_id: str) -> None:
    """Seed enacted rule change events."""
    await repo.append_event(
        event_type="rule.enacted",
        aggregate_id="rule-1",
        aggregate_type="rule",
        season_id=season_id,
        payload={
            "parameter": "shot_clock_seconds",
            "old_value": 24,
            "new_value": 18,
        },
        round_number=1,
    )
    await repo.append_event(
        event_type="rule.enacted",
        aggregate_id="rule-2",
        aggregate_type="rule",
        season_id=season_id,
        payload={
            "parameter": "three_point_value",
            "old_value": 3,
            "new_value": 4,
        },
        round_number=2,
    )


async def _seed_governors(
    repo: Repository,
    season_id: str,
    team_ids: list[str],
) -> None:
    """Seed some governor enrollments."""
    for i, team_id in enumerate(team_ids):
        for j in range(2):
            player = await repo.get_or_create_player(
                discord_id=f"discord-{i}-{j}",
                username=f"Governor_{i}_{j}",
            )
            await repo.enroll_player(player.id, team_id, season_id)


# ===========================================================================
# Tests: build_league_context()
# ===========================================================================


class TestBuildLeagueContext:
    """Tests for the build_league_context() data gathering function."""

    async def test_active_season_full_data(self, repo: Repository) -> None:
        """Active season with games, proposals, rule changes, governors."""
        _league_id, season_id, team_ids = await _seed_league(repo)
        await _seed_games(repo, season_id, team_ids, num_rounds=3)
        await _seed_proposals(repo, season_id)
        await _seed_rule_changes(repo, season_id)
        await _seed_governors(repo, season_id, team_ids)

        ctx = await build_league_context(
            repo,
            season_id=season_id,
            season_name="Season ONE",
            season_status="active",
            governance_interval=1,
        )

        assert ctx.season_name == "Season ONE"
        assert ctx.season_phase == SeasonPhase.ACTIVE
        assert ctx.current_round == 3
        assert ctx.games_played == 6  # 2 games per round * 3 rounds
        assert len(ctx.standings) == 4
        assert ctx.active_proposals_total == 2
        assert len(ctx.active_proposals) == 2
        assert len(ctx.recent_rule_changes) == 2
        assert ctx.governor_count == 8  # 2 per team * 4 teams
        assert len(ctx.team_governor_counts) == 4
        assert ctx.governance_interval == 1

    async def test_no_games_yet(self, repo: Repository) -> None:
        """Season exists but no games played."""
        _league_id, season_id, _team_ids = await _seed_league(repo)

        ctx = await build_league_context(
            repo,
            season_id=season_id,
            season_name="Season ONE",
            season_status="active",
        )

        assert ctx.current_round == 0
        assert ctx.games_played == 0
        assert len(ctx.standings) == 0
        assert ctx.active_proposals_total == 0
        assert len(ctx.recent_rule_changes) == 0

    async def test_active_proposals_only(self, repo: Repository) -> None:
        """Only confirmed/amended proposals appear as active."""
        _league_id, season_id, _team_ids = await _seed_league(repo)
        await _seed_proposals(repo, season_id)

        ctx = await build_league_context(
            repo,
            season_id=season_id,
            season_name="Season ONE",
            season_status="active",
        )

        # prop-1 and prop-2 are confirmed, prop-3 is passed
        assert ctx.active_proposals_total == 2
        proposal_texts = [str(p.get("raw_text", "")) for p in ctx.active_proposals]
        assert "Make three-pointers worth 5 points" in proposal_texts
        assert "Require 3 passes before shooting" in proposal_texts
        # Passed proposal should NOT be in active
        assert "Shorten shot clock to 18 seconds" not in proposal_texts

    async def test_playoffs_phase(self, repo: Repository) -> None:
        """Season in PLAYOFFS phase."""
        _league_id, season_id, team_ids = await _seed_league(repo)
        await _seed_games(repo, season_id, team_ids, num_rounds=2)

        ctx = await build_league_context(
            repo,
            season_id=season_id,
            season_name="Season ONE",
            season_status="playoffs",
        )

        assert ctx.season_phase == SeasonPhase.PLAYOFFS

    async def test_championship_phase(self, repo: Repository) -> None:
        """Season in CHAMPIONSHIP phase."""
        _league_id, season_id, _team_ids = await _seed_league(repo)

        ctx = await build_league_context(
            repo,
            season_id=season_id,
            season_name="Season ONE",
            season_status="championship",
        )

        assert ctx.season_phase == SeasonPhase.CHAMPIONSHIP

    async def test_offseason_phase(self, repo: Repository) -> None:
        """Season in OFFSEASON phase."""
        _league_id, season_id, _team_ids = await _seed_league(repo)

        ctx = await build_league_context(
            repo,
            season_id=season_id,
            season_name="Season ONE",
            season_status="offseason",
        )

        assert ctx.season_phase == SeasonPhase.OFFSEASON

    async def test_complete_phase(self, repo: Repository) -> None:
        """Season in COMPLETE phase (via legacy 'completed' status)."""
        _league_id, season_id, _team_ids = await _seed_league(repo)

        ctx = await build_league_context(
            repo,
            season_id=season_id,
            season_name="Season ONE",
            season_status="completed",
        )

        assert ctx.season_phase == SeasonPhase.COMPLETE

    async def test_governance_interval_passed_through(self, repo: Repository) -> None:
        """Governance interval is passed through from settings."""
        _league_id, season_id, _team_ids = await _seed_league(repo)

        ctx = await build_league_context(
            repo,
            season_id=season_id,
            season_name="Test",
            season_status="active",
            governance_interval=3,
        )

        assert ctx.governance_interval == 3

    async def test_standings_have_team_names(self, repo: Repository) -> None:
        """Standings entries include team names."""
        _league_id, season_id, team_ids = await _seed_league(repo)
        await _seed_games(repo, season_id, team_ids, num_rounds=1)

        ctx = await build_league_context(
            repo,
            season_id=season_id,
            season_name="Test",
            season_status="active",
        )

        for s in ctx.standings:
            assert "team_name" in s
            assert isinstance(s["team_name"], str)
            assert len(str(s["team_name"])) > 0

    async def test_total_rounds_from_schedule(self, repo: Repository) -> None:
        """total_rounds is derived from the schedule, not played games."""
        _league_id, season_id, team_ids = await _seed_league(repo)
        # Add schedule entries for 6 rounds (only schedule, no games)
        for rn in range(1, 7):
            await repo.create_schedule_entry(
                season_id=season_id,
                round_number=rn,
                matchup_index=0,
                home_team_id=team_ids[0],
                away_team_id=team_ids[1],
                phase="regular",
            )
        # Play only 2 rounds -- store game results directly (no schedule duplication)
        import random

        random.seed(42)
        for rn in range(1, 3):
            home_score = random.randint(80, 110)
            away_score = random.randint(80, 110)
            winner = team_ids[0] if home_score > away_score else team_ids[1]
            await repo.store_game_result(
                season_id=season_id,
                round_number=rn,
                matchup_index=0,
                home_team_id=team_ids[0],
                away_team_id=team_ids[1],
                home_score=home_score,
                away_score=away_score,
                winner_team_id=winner,
                seed=42,
                total_possessions=100,
            )

        ctx = await build_league_context(
            repo,
            season_id=season_id,
            season_name="Test",
            season_status="active",
        )

        assert ctx.total_rounds == 6
        assert ctx.current_round == 2

    async def test_recent_rule_changes_capped(self, repo: Repository) -> None:
        """At most MAX_RECENT_RULE_CHANGES are returned."""
        _league_id, season_id, _team_ids = await _seed_league(repo)

        # Add 5 rule changes
        for i in range(5):
            await repo.append_event(
                event_type="rule.enacted",
                aggregate_id=f"rule-{i}",
                aggregate_type="rule",
                season_id=season_id,
                payload={
                    "parameter": f"param_{i}",
                    "old_value": i,
                    "new_value": i + 1,
                },
                round_number=i + 1,
            )

        ctx = await build_league_context(
            repo,
            season_id=season_id,
            season_name="Test",
            season_status="active",
        )

        # Should be capped at 3 (MAX_RECENT_RULE_CHANGES)
        assert len(ctx.recent_rule_changes) == 3
        # Should be the most recent 3
        params = [rc["parameter"] for rc in ctx.recent_rule_changes]
        assert params == ["param_2", "param_3", "param_4"]


# ===========================================================================
# Tests: build_onboarding_embed()
# ===========================================================================


class TestBuildOnboardingEmbed:
    """Tests for the build_onboarding_embed() Discord embed builder."""

    def _make_context(self, **overrides: object) -> LeagueContext:
        """Create a LeagueContext with sensible defaults."""
        defaults: dict[str, object] = {
            "season_name": "Season TWO",
            "season_phase": SeasonPhase.ACTIVE,
            "current_round": 5,
            "total_rounds": 9,
            "standings": [
                {"team_name": "Rose City Thorns", "team_id": "t1", "wins": 4, "losses": 2},
                {"team_name": "Bridge City Bolts", "team_id": "t2", "wins": 3, "losses": 3},
                {"team_name": "Stumptown Stars", "team_id": "t3", "wins": 3, "losses": 3},
                {"team_name": "PDX Voltage", "team_id": "t4", "wins": 2, "losses": 4},
            ],
            "active_proposals": [
                {"raw_text": "Make three-pointers worth 5 points", "tier": 1},
                {"raw_text": "Require 3 passes before shooting", "tier": 2},
            ],
            "active_proposals_total": 2,
            "recent_rule_changes": [
                {
                    "parameter": "shot_clock_seconds",
                    "old_value": 24,
                    "new_value": 18,
                    "round_number": 3,
                },
            ],
            "governor_count": 8,
            "team_governor_counts": {
                "Rose City Thorns": 2,
                "Bridge City Bolts": 2,
                "Stumptown Stars": 2,
                "PDX Voltage": 2,
            },
            "governance_interval": 1,
            "games_played": 12,
        }
        defaults.update(overrides)
        return LeagueContext(**defaults)  # type: ignore[arg-type]

    def test_basic_embed_structure(self) -> None:
        """Embed has title, description, and fields."""
        ctx = self._make_context()
        embed = build_onboarding_embed(ctx)

        assert embed.title == "State of the League"
        assert embed.description is not None
        assert "Season TWO" in embed.description
        assert "Round 5 of 9" in embed.description
        assert len(embed.fields) >= 1

    def test_standings_field(self) -> None:
        """Embed includes standings field with team names and W-L records."""
        ctx = self._make_context()
        embed = build_onboarding_embed(ctx)

        standings_field = next(
            (f for f in embed.fields if f.name == "Standings"), None
        )
        assert standings_field is not None
        assert "Rose City Thorns" in standings_field.value
        assert "(4W-2L)" in standings_field.value
        assert "PDX Voltage" in standings_field.value
        assert "(2W-4L)" in standings_field.value

    def test_team_highlight(self) -> None:
        """Player's team is highlighted in standings."""
        ctx = self._make_context()
        embed = build_onboarding_embed(ctx, team_name="Rose City Thorns")

        standings_field = next(
            (f for f in embed.fields if f.name == "Standings"), None
        )
        assert standings_field is not None
        assert "<-- your team" in standings_field.value

    def test_no_team_highlight_when_none(self) -> None:
        """No team highlighted when team_name is None."""
        ctx = self._make_context()
        embed = build_onboarding_embed(ctx, team_name=None)

        standings_field = next(
            (f for f in embed.fields if f.name == "Standings"), None
        )
        assert standings_field is not None
        assert "<-- your team" not in standings_field.value

    def test_active_proposals_field(self) -> None:
        """Embed includes active proposals field."""
        ctx = self._make_context()
        embed = build_onboarding_embed(ctx)

        proposals_field = next(
            (f for f in embed.fields if "Floor" in f.name), None
        )
        assert proposals_field is not None
        assert "Make three-pointers" in proposals_field.value
        assert "Require 3 passes" in proposals_field.value
        assert "/vote" in proposals_field.value

    def test_no_proposals(self) -> None:
        """Embed handles zero active proposals gracefully."""
        ctx = self._make_context(active_proposals=[], active_proposals_total=0)
        embed = build_onboarding_embed(ctx)

        # Should NOT have a proposals field
        proposals_field = next(
            (f for f in embed.fields if "Floor" in f.name), None
        )
        assert proposals_field is None

    def test_rule_changes_field(self) -> None:
        """Embed includes recent rule changes field."""
        ctx = self._make_context()
        embed = build_onboarding_embed(ctx)

        rules_field = next(
            (f for f in embed.fields if "Rule" in f.name), None
        )
        assert rules_field is not None
        assert "shot_clock_seconds" in rules_field.value
        assert "24" in rules_field.value
        assert "18" in rules_field.value

    def test_no_rule_changes(self) -> None:
        """Embed handles zero rule changes gracefully."""
        ctx = self._make_context(recent_rule_changes=[])
        embed = build_onboarding_embed(ctx)

        rules_field = next(
            (f for f in embed.fields if "Rule" in f.name), None
        )
        assert rules_field is None

    def test_no_games_standings(self) -> None:
        """Standings show 'No games played yet' when empty."""
        ctx = self._make_context(standings=[], games_played=0)
        embed = build_onboarding_embed(ctx)

        standings_field = next(
            (f for f in embed.fields if f.name == "Standings"), None
        )
        assert standings_field is not None
        assert "No games played yet" in standings_field.value

    def test_footer_governor_count(self) -> None:
        """Footer includes governor count and governance interval."""
        ctx = self._make_context()
        embed = build_onboarding_embed(ctx)

        assert embed.footer is not None
        assert "8 governors" in embed.footer.text
        assert "4 teams" in embed.footer.text
        assert "every round" in embed.footer.text

    def test_footer_multi_round_governance(self) -> None:
        """Footer shows multi-round governance interval correctly."""
        ctx = self._make_context(governance_interval=3)
        embed = build_onboarding_embed(ctx)

        assert embed.footer is not None
        assert "every 3 rounds" in embed.footer.text

    def test_phase_specific_description_active(self) -> None:
        """Active phase description is shown."""
        ctx = self._make_context(season_phase=SeasonPhase.ACTIVE)
        embed = build_onboarding_embed(ctx)

        assert "Regular season is underway" in embed.description

    def test_phase_specific_description_playoffs(self) -> None:
        """Playoffs phase description is shown."""
        ctx = self._make_context(season_phase=SeasonPhase.PLAYOFFS)
        embed = build_onboarding_embed(ctx)

        assert "playoffs are underway" in embed.description

    def test_phase_specific_description_championship(self) -> None:
        """Championship phase description is shown."""
        ctx = self._make_context(season_phase=SeasonPhase.CHAMPIONSHIP)
        embed = build_onboarding_embed(ctx)

        assert "champion has been crowned" in embed.description

    def test_phase_specific_description_offseason(self) -> None:
        """Offseason phase description is shown."""
        ctx = self._make_context(season_phase=SeasonPhase.OFFSEASON)
        embed = build_onboarding_embed(ctx)

        assert "offseason governance window" in embed.description

    def test_phase_specific_description_complete(self) -> None:
        """Complete phase description is shown."""
        ctx = self._make_context(season_phase=SeasonPhase.COMPLETE)
        embed = build_onboarding_embed(ctx)

        assert "season is complete" in embed.description

    def test_overflow_proposals_message(self) -> None:
        """When there are more than 5 active proposals, show overflow message."""
        many_proposals = [
            {"raw_text": f"Proposal {i}", "tier": 1}
            for i in range(5)
        ]
        ctx = self._make_context(
            active_proposals=many_proposals,
            active_proposals_total=8,
        )
        embed = build_onboarding_embed(ctx)

        proposals_field = next(
            (f for f in embed.fields if "Floor" in f.name), None
        )
        assert proposals_field is not None
        assert "...and 3 more" in proposals_field.value
        assert "/proposals" in proposals_field.value

    def test_embed_color(self) -> None:
        """Embed uses the onboarding color (teal)."""
        from pinwheel.discord.embeds import COLOR_ONBOARDING

        ctx = self._make_context()
        embed = build_onboarding_embed(ctx)

        assert embed.color is not None
        assert embed.color.value == COLOR_ONBOARDING

    def test_case_insensitive_team_highlight(self) -> None:
        """Team highlighting is case-insensitive."""
        ctx = self._make_context()
        embed = build_onboarding_embed(ctx, team_name="rose city thorns")

        standings_field = next(
            (f for f in embed.fields if f.name == "Standings"), None
        )
        assert standings_field is not None
        assert "<-- your team" in standings_field.value


# ===========================================================================
# Tests: /status command and /join integration
# ===========================================================================


def _make_interaction(**overrides: object) -> AsyncMock:
    """Build a fully-configured Discord interaction mock."""
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = overrides.get("user_id", 12345)
    interaction.user.display_name = overrides.get("display_name", "TestGovernor")
    interaction.user.send = AsyncMock()
    interaction.channel = AsyncMock()
    interaction.guild = None
    if "display_avatar_url" in overrides:
        interaction.user.display_avatar = MagicMock()
        interaction.user.display_avatar.url = overrides["display_avatar_url"]
    return interaction


class TestStatusCommand:
    """Tests for the /status slash command."""

    async def test_status_no_engine(self) -> None:
        """Status reports unavailable when no engine is set."""
        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
        )
        event_bus = EventBus()
        bot = PinwheelBot(settings=settings, event_bus=event_bus, engine=None)

        interaction = _make_interaction()
        await bot._handle_status(interaction)

        interaction.response.defer.assert_called_once_with(ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert "unavailable" in str(call_kwargs)

    async def test_status_no_active_season(self, engine: AsyncEngine) -> None:
        """Status reports 'no active season' when no seasons exist."""
        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
        )
        event_bus = EventBus()
        bot = PinwheelBot(settings=settings, event_bus=event_bus, engine=engine)

        interaction = _make_interaction()
        await bot._handle_status(interaction)

        interaction.response.defer.assert_called_once_with(ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert "No active season" in str(call_kwargs)

    async def test_status_with_active_season(self, engine: AsyncEngine) -> None:
        """Status sends an embed when a season exists."""
        # Seed data
        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test League")
            season = await repo.create_season(league.id, "Season ONE")
            season.status = "active"
            await repo.create_team(season.id, "Test Team")
            await session.commit()

        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
        )
        event_bus = EventBus()
        bot = PinwheelBot(settings=settings, event_bus=event_bus, engine=engine)

        interaction = _make_interaction()
        await bot._handle_status(interaction)

        interaction.response.defer.assert_called_once_with(ephemeral=True)
        # Should have sent an embed
        call_args = interaction.followup.send.call_args
        assert call_args is not None
        # Check that an embed was sent
        assert "embed" in call_args.kwargs or (
            len(call_args.args) > 0 and isinstance(call_args.args[0], discord.Embed)
        )

    async def test_status_highlights_enrolled_team(self, engine: AsyncEngine) -> None:
        """Status highlights the user's team when they are enrolled."""
        # Seed data with an enrolled player
        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test League")
            season = await repo.create_season(league.id, "Season ONE")
            season.status = "active"
            team = await repo.create_team(season.id, "My Team")
            player = await repo.get_or_create_player(
                discord_id="12345",
                username="TestGovernor",
            )
            await repo.enroll_player(player.id, team.id, season.id)
            # Add a game so standings aren't empty
            team2 = await repo.create_team(season.id, "Other Team")
            await repo.store_game_result(
                season_id=season.id,
                round_number=1,
                matchup_index=0,
                home_team_id=team.id,
                away_team_id=team2.id,
                home_score=100,
                away_score=90,
                winner_team_id=team.id,
                seed=42,
                total_possessions=100,
            )
            await session.commit()

        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
        )
        event_bus = EventBus()
        bot = PinwheelBot(settings=settings, event_bus=event_bus, engine=engine)

        interaction = _make_interaction(user_id=12345)
        await bot._handle_status(interaction)

        call_args = interaction.followup.send.call_args
        assert call_args is not None
        embed = call_args.kwargs.get("embed")
        assert embed is not None

        # Check that the team is highlighted in standings
        standings_field = next(
            (f for f in embed.fields if f.name == "Standings"), None
        )
        assert standings_field is not None
        assert "<-- your team" in standings_field.value

    async def test_status_no_enrollment(self, engine: AsyncEngine) -> None:
        """Status works for non-enrolled users without team highlight."""
        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test League")
            season = await repo.create_season(league.id, "Season ONE")
            season.status = "active"
            await repo.create_team(season.id, "Some Team")
            await session.commit()

        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
        )
        event_bus = EventBus()
        bot = PinwheelBot(settings=settings, event_bus=event_bus, engine=engine)

        interaction = _make_interaction(user_id=99999)  # Not enrolled
        await bot._handle_status(interaction)

        call_args = interaction.followup.send.call_args
        assert call_args is not None
        embed = call_args.kwargs.get("embed")
        assert embed is not None
        # No team should be highlighted
        standings_field = next(
            (f for f in embed.fields if f.name == "Standings"), None
        )
        if standings_field:
            assert "<-- your team" not in standings_field.value
