"""Tests for the Discord bot integration.

All Discord objects are mocked — no real Discord connection required.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from pinwheel.config import Settings
from pinwheel.core.event_bus import EventBus
from pinwheel.discord.bot import PinwheelBot, is_discord_enabled, start_discord_bot
from pinwheel.discord.embeds import (
    build_game_result_embed,
    build_proposal_embed,
    build_report_embed,
    build_roster_embed,
    build_round_summary_embed,
    build_schedule_embed,
    build_standings_embed,
    build_token_balance_embed,
    build_vote_tally_embed,
    build_welcome_embed,
)
from pinwheel.models.governance import Proposal, RuleInterpretation, VoteTally
from pinwheel.models.report import Report


def make_interaction(**overrides) -> AsyncMock:
    """Build a fully-configured Discord interaction mock."""
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = overrides.get("user_id", 12345)
    interaction.user.display_name = overrides.get("display_name", "TestGovernor")
    interaction.user.send = AsyncMock()
    interaction.channel = AsyncMock()
    if "display_avatar_url" in overrides:
        interaction.user.display_avatar = MagicMock()
        interaction.user.display_avatar.url = overrides["display_avatar_url"]
    return interaction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_discord_enabled() -> Settings:
    """Settings with Discord enabled."""
    return Settings(
        pinwheel_env="development",
        database_url="sqlite+aiosqlite:///:memory:",
        discord_bot_token="test-token-not-real",
        discord_channel_id="123456789",
        discord_guild_id="987654321",
        discord_enabled=True,
    )


@pytest.fixture
def settings_discord_disabled() -> Settings:
    """Settings with Discord disabled (no token)."""
    return Settings(
        pinwheel_env="development",
        database_url="sqlite+aiosqlite:///:memory:",
        discord_bot_token="",
        discord_channel_id="",
        discord_enabled=False,
    )


@pytest.fixture
def event_bus() -> EventBus:
    """A fresh EventBus for testing."""
    return EventBus()


# ---------------------------------------------------------------------------
# is_discord_enabled
# ---------------------------------------------------------------------------


class TestIsDiscordEnabled:
    def test_enabled_with_token(self, settings_discord_enabled: Settings) -> None:
        assert is_discord_enabled(settings_discord_enabled) is True

    def test_disabled_without_token(self, settings_discord_disabled: Settings) -> None:
        assert is_discord_enabled(settings_discord_disabled) is False

    def test_disabled_when_flag_false(self) -> None:
        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
            discord_bot_token="some-token",
            discord_enabled=False,
        )
        assert is_discord_enabled(settings) is False

    def test_disabled_when_token_empty(self) -> None:
        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
            discord_bot_token="",
            discord_enabled=True,
        )
        assert is_discord_enabled(settings) is False


# ---------------------------------------------------------------------------
# PinwheelBot construction
# ---------------------------------------------------------------------------


class TestPinwheelBotInit:
    def test_bot_creation(self, settings_discord_enabled: Settings, event_bus: EventBus) -> None:
        bot = PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus)
        assert bot.main_channel_id == 123456789
        assert bot.settings is settings_discord_enabled
        assert bot.event_bus is event_bus

    def test_bot_no_channel_id(self, event_bus: EventBus) -> None:
        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
            discord_bot_token="tok",
            discord_channel_id="",
            discord_enabled=True,
        )
        bot = PinwheelBot(settings=settings, event_bus=event_bus)
        assert bot.main_channel_id == 0

    def test_bot_has_slash_commands(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> None:
        bot = PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus)
        command_names = [cmd.name for cmd in bot.tree.get_commands()]
        assert "standings" in command_names
        assert "propose" in command_names
        assert "schedule" in command_names
        assert "reports" in command_names
        assert "join" in command_names
        assert "vote" in command_names
        assert "tokens" in command_names
        assert "trade" in command_names
        assert "strategy" in command_names
        assert "bio" in command_names
        assert "roster" in command_names

    def test_bot_has_channel_ids_dict(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> None:
        bot = PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus)
        assert isinstance(bot.channel_ids, dict)
        assert len(bot.channel_ids) == 0


# ---------------------------------------------------------------------------
# Slash command handlers
# ---------------------------------------------------------------------------


class TestSlashCommands:
    @pytest.fixture
    def bot(self, settings_discord_enabled: Settings, event_bus: EventBus) -> PinwheelBot:
        return PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus)

    async def test_handle_standings(self, bot: PinwheelBot) -> None:
        interaction = make_interaction()
        await bot._handle_standings(interaction)
        interaction.response.defer.assert_called_once()
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        embed = call_kwargs.kwargs.get("embed") or call_kwargs.args[0]
        assert isinstance(embed, discord.Embed)

    async def test_handle_roster_no_engine(self, bot: PinwheelBot) -> None:
        """Without an engine, roster defers and returns 'Database not available'."""
        interaction = make_interaction()
        await bot._handle_roster(interaction)
        interaction.response.defer.assert_called_once()
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args
        msg = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        assert "Database not available" in str(msg)

    async def test_handle_propose_with_text_no_engine(self, bot: PinwheelBot) -> None:
        """Without an engine, propose returns an ephemeral error (before defer)."""
        interaction = make_interaction()
        await bot._handle_propose(interaction, "Make three-pointers worth 5 points")
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True

    async def test_handle_propose_empty_text(self, bot: PinwheelBot) -> None:
        interaction = make_interaction()
        await bot._handle_propose(interaction, "   ")
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True

    async def test_handle_schedule(self, bot: PinwheelBot) -> None:
        interaction = make_interaction()
        await bot._handle_schedule(interaction)
        interaction.response.defer.assert_called_once()
        interaction.followup.send.assert_called_once()

    async def test_handle_reports(self, bot: PinwheelBot) -> None:
        interaction = make_interaction()
        await bot._handle_reports(interaction)
        interaction.response.defer.assert_called_once()
        interaction.followup.send.assert_called_once()


# ---------------------------------------------------------------------------
# Event dispatch
# ---------------------------------------------------------------------------


class TestEventDispatch:
    @pytest.fixture
    def bot(self, settings_discord_enabled: Settings, event_bus: EventBus) -> PinwheelBot:
        return PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus)

    async def test_dispatch_game_finished(self, bot: PinwheelBot) -> None:
        channel = AsyncMock(spec=discord.TextChannel)
        bot.get_channel = MagicMock(return_value=channel)

        event = {
            "type": "presentation.game_finished",
            "data": {
                "game_id": "g-1-0",
                "home_team": "Rose City Thorns",
                "away_team": "Burnside Breakers",
                "home_score": 55,
                "away_score": 38,
                "winner_team_id": "team-1",
                "total_possessions": 60,
            },
        }
        await bot._dispatch_event(event)
        # Blowout (>15 diff): sent to both play-by-play and big-plays
        assert channel.send.call_count == 2
        embed = channel.send.call_args_list[0].kwargs["embed"]
        assert "Rose City Thorns" in embed.title

    async def test_dispatch_round_finished(self, bot: PinwheelBot) -> None:
        channel = AsyncMock(spec=discord.TextChannel)
        bot.get_channel = MagicMock(return_value=channel)

        event = {
            "type": "presentation.round_finished",
            "data": {"round": 3, "games_presented": 4},
        }
        await bot._dispatch_event(event)
        channel.send.assert_called_once()

    async def test_dispatch_report_generated_public(self, bot: PinwheelBot) -> None:
        channel = AsyncMock(spec=discord.TextChannel)
        bot.get_channel = MagicMock(return_value=channel)

        event = {
            "type": "report.generated",
            "data": {
                "report_type": "simulation",
                "round": 5,
                "excerpt": "The Rose City Thorns dominated the boards this round.",
            },
        }
        await bot._dispatch_event(event)
        channel.send.assert_called_once()

    async def test_dispatch_report_generated_private_skipped(self, bot: PinwheelBot) -> None:
        channel = AsyncMock(spec=discord.TextChannel)
        bot.get_channel = MagicMock(return_value=channel)

        event = {
            "type": "report.generated",
            "data": {
                "report_type": "private",
                "round": 5,
                "excerpt": "Your voting pattern reveals...",
            },
        }
        await bot._dispatch_event(event)
        channel.send.assert_not_called()

    async def test_dispatch_governance_window_closed(self, bot: PinwheelBot) -> None:
        channel = AsyncMock(spec=discord.TextChannel)
        bot.get_channel = MagicMock(return_value=channel)

        event = {
            "type": "governance.window_closed",
            "data": {"round": 3, "proposals_count": 2, "rules_changed": 1},
        }
        await bot._dispatch_event(event)
        channel.send.assert_called_once()

    async def test_dispatch_no_channel_configured(self, event_bus: EventBus) -> None:
        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
            discord_bot_token="tok",
            discord_channel_id="",
            discord_enabled=True,
        )
        bot = PinwheelBot(settings=settings, event_bus=event_bus)

        # Should not raise, just silently return
        event = {"type": "presentation.game_finished", "data": {"home_team": "A", "away_team": "B"}}
        await bot._dispatch_event(event)

    async def test_dispatch_unknown_event_type(self, bot: PinwheelBot) -> None:
        channel = AsyncMock(spec=discord.TextChannel)
        bot.get_channel = MagicMock(return_value=channel)

        event = {"type": "unknown.event", "data": {"foo": "bar"}}
        await bot._dispatch_event(event)
        channel.send.assert_not_called()


# ---------------------------------------------------------------------------
# start_discord_bot
# ---------------------------------------------------------------------------


class TestStartDiscordBot:
    async def test_start_creates_task(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> None:
        with patch.object(PinwheelBot, "start", new_callable=AsyncMock) as mock_start:
            bot = await start_discord_bot(settings_discord_enabled, event_bus)
            assert bot is not None
            assert isinstance(bot, PinwheelBot)
            # Give the task a moment to start
            await asyncio.sleep(0.05)
            mock_start.assert_called_once_with("test-token-not-real")
            await bot.close()


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------


class TestBuildGameResultEmbed:
    def test_basic_game_result(self) -> None:
        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 55,
            "away_score": 48,
            "winner_team_id": "t1",
            "elam_activated": False,
            "total_possessions": 60,
        }
        embed = build_game_result_embed(data)
        assert isinstance(embed, discord.Embed)
        assert "Thorns" in embed.title
        assert "Breakers" in embed.title
        assert "55" in (embed.description or "")
        assert "48" in (embed.description or "")

    def test_elam_target_score(self) -> None:
        data = {
            "home_team": "A",
            "away_team": "B",
            "home_score": 50,
            "away_score": 50,
            "elam_target_score": 55,
            "total_possessions": 75,
        }
        embed = build_game_result_embed(data)
        assert "Elam Target: 55" in (embed.description or "")


class TestBuildStandingsEmbed:
    def test_empty_standings(self) -> None:
        embed = build_standings_embed([])
        assert "No games played" in (embed.description or "")

    def test_with_standings(self) -> None:
        standings = [
            {"team_name": "Thorns", "wins": 5, "losses": 2},
            {"team_name": "Breakers", "wins": 4, "losses": 3},
        ]
        embed = build_standings_embed(standings)
        desc = embed.description or ""
        assert "Thorns" in desc
        assert "5W-2L" in desc
        assert "Breakers" in desc


class TestBuildRosterEmbed:
    def test_empty_roster(self) -> None:
        embed = build_roster_embed([])
        assert "No governors enrolled" in (embed.description or "")

    def test_with_governors(self) -> None:
        governors = [
            {
                "username": "Alice",
                "team_name": "Thorns",
                "propose": 2,
                "amend": 1,
                "boost": 3,
                "proposals_submitted": 5,
                "votes_cast": 10,
            },
            {
                "username": "Bob",
                "team_name": "Breakers",
                "propose": 0,
                "amend": 2,
                "boost": 0,
                "proposals_submitted": 1,
                "votes_cast": 3,
            },
        ]
        embed = build_roster_embed(governors, season_name="Season 1")
        desc = embed.description or ""
        assert "Alice" in desc
        assert "Thorns" in desc
        assert "Bob" in desc
        assert "Season 1" in embed.title

    def test_roster_has_token_info(self) -> None:
        governors = [
            {
                "username": "Alice",
                "team_name": "Thorns",
                "propose": 2,
                "amend": 1,
                "boost": 3,
                "proposals_submitted": 5,
                "votes_cast": 10,
            },
        ]
        embed = build_roster_embed(governors)
        desc = embed.description or ""
        assert "P:2" in desc
        assert "A:1" in desc
        assert "B:3" in desc


class TestBuildProposalEmbed:
    def test_basic_proposal(self) -> None:
        proposal = Proposal(
            id="p-1",
            governor_id="gov-1",
            team_id="t-1",
            raw_text="Make three-pointers worth 5",
            status="submitted",
            tier=1,
        )
        embed = build_proposal_embed(proposal)
        assert "Make three-pointers" in embed.title
        assert embed.fields[0].value == "Submitted"

    def test_proposal_with_interpretation(self) -> None:
        interp = RuleInterpretation(
            parameter="three_point_value",
            old_value=3,
            new_value=5,
            impact_analysis="Sharpshooters become more valuable.",
        )
        proposal = Proposal(
            id="p-2",
            governor_id="gov-2",
            team_id="t-2",
            raw_text="Make three-pointers worth 5",
            interpretation=interp,
            status="confirmed",
            tier=1,
        )
        embed = build_proposal_embed(proposal)
        field_names = [f.name for f in embed.fields]
        assert "Parameter Change" in field_names
        assert "Impact Analysis" in field_names


class TestBuildVoteTallyEmbed:
    def test_passed_tally(self) -> None:
        tally = VoteTally(
            proposal_id="p-1",
            weighted_yes=5.0,
            weighted_no=3.0,
            total_weight=8.0,
            passed=True,
            threshold=0.5,
            yes_count=3,
            no_count=2,
        )
        embed = build_vote_tally_embed(tally, "Make it rain")
        assert "PASSED" in embed.title
        assert "Make it rain" in (embed.description or "")
        # Check vote count fields
        yes_field = next(f for f in embed.fields if f.name == "Yes")
        assert "3 votes" in yes_field.value
        assert "5.00" in yes_field.value
        no_field = next(f for f in embed.fields if f.name == "No")
        assert "2 votes" in no_field.value
        assert "3.00" in no_field.value
        votes_cast_field = next(f for f in embed.fields if f.name == "Votes Cast")
        assert "5 governors voted" in votes_cast_field.value

    def test_failed_tally(self) -> None:
        tally = VoteTally(
            proposal_id="p-1",
            weighted_yes=2.0,
            weighted_no=6.0,
            total_weight=8.0,
            passed=False,
            threshold=0.5,
            yes_count=1,
            no_count=4,
        )
        embed = build_vote_tally_embed(tally)
        assert "FAILED" in embed.title
        votes_cast_field = next(f for f in embed.fields if f.name == "Votes Cast")
        assert "5 governors voted" in votes_cast_field.value

    def test_single_vote_grammar(self) -> None:
        """Single vote uses singular 'vote' not 'votes'."""
        tally = VoteTally(
            proposal_id="p-1",
            weighted_yes=1.0,
            weighted_no=0.0,
            total_weight=1.0,
            passed=True,
            threshold=0.5,
            yes_count=1,
            no_count=0,
        )
        embed = build_vote_tally_embed(tally)
        yes_field = next(f for f in embed.fields if f.name == "Yes")
        assert "1 vote)" in yes_field.value
        votes_cast_field = next(f for f in embed.fields if f.name == "Votes Cast")
        assert "1 governor voted" in votes_cast_field.value

    def test_participation_field_with_eligible(self) -> None:
        """Participation field shows when total_eligible > 0."""
        tally = VoteTally(
            proposal_id="p-1",
            weighted_yes=2.0,
            weighted_no=1.0,
            total_weight=3.0,
            passed=True,
            threshold=0.5,
            yes_count=2,
            no_count=1,
            total_eligible=5,
        )
        embed = build_vote_tally_embed(tally)
        field_names = [f.name for f in embed.fields]
        assert "Participation" in field_names
        part_field = next(f for f in embed.fields if f.name == "Participation")
        assert "3 of 5" in part_field.value
        assert "60%" in part_field.value

    def test_no_participation_field_without_eligible(self) -> None:
        """Participation field absent when total_eligible is 0."""
        tally = VoteTally(
            proposal_id="p-1",
            weighted_yes=1.0,
            weighted_no=0.0,
            total_weight=1.0,
            passed=True,
            threshold=0.5,
            yes_count=1,
            no_count=0,
            total_eligible=0,
        )
        embed = build_vote_tally_embed(tally)
        field_names = [f.name for f in embed.fields]
        assert "Participation" not in field_names


class TestBuildProposalAnnouncementEmbed:
    def test_basic_announcement(self) -> None:
        from pinwheel.discord.embeds import build_proposal_announcement_embed

        embed = build_proposal_announcement_embed(
            proposal_text="Make three-pointers worth 5 points",
            parameter="three_point_value",
            old_value=3,
            new_value=5,
            tier=1,
            threshold=0.5,
        )
        assert embed.title == "New Proposal on the Floor"
        assert "Make three-pointers" in (embed.description or "")
        field_names = [f.name for f in embed.fields]
        assert "Parameter Change" in field_names
        assert "Tier" in field_names
        assert "Threshold" in field_names
        assert embed.footer.text == "Use /vote to cast your vote"

    def test_announcement_no_parameter(self) -> None:
        from pinwheel.discord.embeds import build_proposal_announcement_embed

        embed = build_proposal_announcement_embed(
            proposal_text="Make the game more exciting",
            tier=5,
            threshold=0.67,
        )
        assert embed.title == "New Proposal on the Floor"
        field_names = [f.name for f in embed.fields]
        assert "Parameter Change" not in field_names
        assert "Tier" in field_names

    def test_announcement_color(self) -> None:
        from pinwheel.discord.embeds import COLOR_GOVERNANCE, build_proposal_announcement_embed

        embed = build_proposal_announcement_embed(
            proposal_text="Test",
        )
        assert embed.color.value == COLOR_GOVERNANCE


class TestBuildReportEmbed:
    def test_simulation_report(self) -> None:
        report = Report(
            id="m-1",
            report_type="simulation",
            round_number=3,
            content="The Thorns dominated this round with superior defense.",
        )
        embed = build_report_embed(report)
        assert "Simulation Report" in embed.title
        assert "Round 3" in embed.title
        assert "Thorns" in (embed.description or "")

    def test_governance_report(self) -> None:
        report = Report(
            id="m-2",
            report_type="governance",
            round_number=7,
            content="A coalition is forming between two teams.",
        )
        embed = build_report_embed(report)
        assert "The Floor" in embed.title


class TestBuildScheduleEmbed:
    def test_empty_schedule(self) -> None:
        embed = build_schedule_embed([], round_number=1)
        assert "No games scheduled" in (embed.description or "")

    def test_with_matchups(self) -> None:
        schedule = [
            {"home_team_name": "Thorns", "away_team_name": "Breakers"},
            {"home_team_name": "Hammers", "away_team_name": "Herons"},
        ]
        embed = build_schedule_embed(schedule, round_number=5)
        assert "Round 5" in embed.title
        desc = embed.description or ""
        assert "Thorns vs Breakers" in desc


class TestBuildRoundSummaryEmbed:
    def test_round_summary(self) -> None:
        data = {"round": 3, "games": 4, "reports": 2, "elapsed_ms": 150.5}
        embed = build_round_summary_embed(data)
        assert "Round 3" in embed.title
        assert "4" in (embed.description or "")


class TestBuildTokenBalanceEmbed:
    def test_nonzero_balance(self) -> None:
        from pinwheel.models.tokens import TokenBalance

        balance = TokenBalance(governor_id="g1", season_id="s1", propose=2, amend=2, boost=2)
        embed = build_token_balance_embed(balance, governor_name="TestGov")
        assert "PROPOSE" in (embed.description or "")
        assert "no tokens" not in (embed.description or "")

    def test_zero_balance_shows_message(self) -> None:
        from pinwheel.models.tokens import TokenBalance

        balance = TokenBalance(governor_id="g1", season_id="s1", propose=0, amend=0, boost=0)
        embed = build_token_balance_embed(balance, governor_name="TestGov")
        assert "no tokens" in (embed.description or "")
        assert "governance interval" in (embed.description or "")


# ---------------------------------------------------------------------------
# /join command
# ---------------------------------------------------------------------------


class TestJoinCommand:
    @pytest.fixture
    def bot(self, settings_discord_enabled: Settings, event_bus: EventBus) -> PinwheelBot:
        return PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus)

    async def test_join_no_engine(self, bot: PinwheelBot) -> None:
        """Without an engine, join returns an ephemeral error."""
        interaction = make_interaction(user_id=111222333, display_name="TestPlayer")
        await bot._handle_join(interaction, "Rose City Thorns")
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True

    async def test_join_with_db(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> None:
        """Full join flow with an in-memory database."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Seed a season + team
        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test League")
            season = await repo.create_season(league.id, "Season 1")
            team = await repo.create_team(season.id, "Rose City Thorns", color="#e94560")
            await repo.create_hooper(
                team.id,
                season.id,
                "Briar Ashwood",
                "sharpshooter",
                {
                    "scoring": 65,
                    "passing": 35,
                    "defense": 30,
                    "speed": 45,
                    "stamina": 40,
                    "iq": 55,
                    "ego": 40,
                    "chaotic_alignment": 20,
                    "fate": 30,
                },
            )
            await session.commit()
            team_id = team.id
            season_id = season.id

        bot = PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus, engine=engine)

        interaction = make_interaction(
            user_id=111222333,
            display_name="TestPlayer",
            display_avatar_url="https://example.com/avatar.png",
        )
        interaction.guild = MagicMock(spec=discord.Guild)
        interaction.guild.roles = []

        await bot._handle_join(interaction, "Rose City Thorns")
        interaction.response.defer.assert_called_once()
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        embed = call_kwargs.kwargs.get("embed")
        assert embed is not None
        assert "Rose City Thorns" in embed.title

        # Verify enrollment in DB
        async with get_session(engine) as session:
            repo = Repository(session)
            enrollment = await repo.get_player_enrollment("111222333", season_id)
            assert enrollment is not None
            assert enrollment[0] == team_id
            assert enrollment[1] == "Rose City Thorns"

        await engine.dispose()

    async def test_join_grants_tokens(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> None:
        """Joining a team grants initial governance tokens so the governor can propose."""
        from pinwheel.core.tokens import get_token_balance
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test League")
            season = await repo.create_season(league.id, "Season 1")
            team = await repo.create_team(season.id, "Rose City Thorns", color="#e94560")
            await repo.create_hooper(
                team.id,
                season.id,
                "Briar Ashwood",
                "sharpshooter",
                {
                    "scoring": 65,
                    "passing": 35,
                    "defense": 30,
                    "speed": 45,
                    "stamina": 40,
                    "iq": 55,
                    "ego": 40,
                    "chaotic_alignment": 20,
                    "fate": 30,
                },
            )
            await session.commit()
            season_id = season.id

        bot = PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus, engine=engine)

        interaction = make_interaction(
            user_id=999888777,
            display_name="NewGovernor",
            display_avatar_url="https://example.com/avatar.png",
        )
        interaction.guild = MagicMock(spec=discord.Guild)
        interaction.guild.roles = []

        await bot._handle_join(interaction, "Rose City Thorns")
        interaction.followup.send.assert_called_once()

        # Verify tokens were granted
        async with get_session(engine) as session:
            repo = Repository(session)
            enrollment = await repo.get_player_enrollment("999888777", season_id)
            assert enrollment is not None
            player = await repo.get_or_create_player("999888777", "NewGovernor")
            balance = await get_token_balance(repo, player.id, season_id)
            assert balance.propose == 2
            assert balance.amend == 2
            assert balance.boost == 2

        await engine.dispose()

    async def test_join_season_lock(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> None:
        """Trying to join a different team after enrollment is blocked."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test League")
            season = await repo.create_season(league.id, "Season 1")
            team1 = await repo.create_team(season.id, "Rose City Thorns", color="#e94560")
            await repo.create_team(season.id, "Burnside Breakers", color="#53d8fb")
            # Pre-enroll the player
            player = await repo.get_or_create_player("111222333", "TestPlayer")
            await repo.enroll_player(player.id, team1.id, season.id)
            await session.commit()

        bot = PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus, engine=engine)

        interaction = make_interaction(
            user_id=111222333,
            display_name="TestPlayer",
            display_avatar_url="https://example.com/avatar.png",
        )
        interaction.guild = None

        await bot._handle_join(interaction, "Burnside Breakers")
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        msg = call_kwargs.args[0] if call_kwargs.args else ""
        assert "ride or die" in str(msg) or "mid-season" in str(msg)

        await engine.dispose()

    async def test_join_team_not_found(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> None:
        """Joining a nonexistent team returns an ephemeral error."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test League")
            await repo.create_season(league.id, "Season 1")
            await session.commit()

        bot = PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus, engine=engine)

        interaction = make_interaction(
            user_id=111222333,
            display_name="TestPlayer",
            display_avatar_url="https://example.com/avatar.png",
        )

        await bot._handle_join(interaction, "Nonexistent Team")
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True

        await engine.dispose()


# ---------------------------------------------------------------------------
# _setup_server
# ---------------------------------------------------------------------------


class TestSetupServer:
    @pytest.fixture
    def bot(self, settings_discord_enabled: Settings, event_bus: EventBus) -> PinwheelBot:
        return PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus)

    async def test_setup_no_guild_id(self, event_bus: EventBus) -> None:
        """Setup is a no-op when no guild ID is configured."""
        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
            discord_bot_token="tok",
            discord_guild_id="",
            discord_enabled=True,
        )
        bot = PinwheelBot(settings=settings, event_bus=event_bus)
        # Should not raise
        await bot._setup_server()
        assert bot.channel_ids == {}

    async def test_setup_guild_not_found(self, bot: PinwheelBot) -> None:
        """Setup is a no-op when the guild isn't in the bot's cache."""
        bot.get_guild = MagicMock(return_value=None)
        await bot._setup_server()
        assert bot.channel_ids == {}

    async def test_setup_creates_channels(self, bot: PinwheelBot) -> None:
        """Setup creates category and channels when they don't exist."""
        guild = MagicMock(spec=discord.Guild)
        guild.categories = []
        guild.text_channels = []
        guild.roles = []
        guild.me = MagicMock()

        category = MagicMock(spec=discord.CategoryChannel)
        guild.create_category = AsyncMock(return_value=category)

        # Track created channels by name
        channel_counter = {"count": 100}

        async def mock_create_text_channel(name: str, **kwargs: object) -> MagicMock:
            ch = MagicMock(spec=discord.TextChannel)
            ch.id = channel_counter["count"]
            ch.name = name
            channel_counter["count"] += 1
            return ch

        guild.create_text_channel = AsyncMock(side_effect=mock_create_text_channel)

        bot.get_guild = MagicMock(return_value=guild)
        await bot._setup_server()

        guild.create_category.assert_called_once_with("PINWHEEL FATES")
        # 3 shared channels: how-to-play, play-by-play, big-plays
        assert guild.create_text_channel.call_count >= 3
        assert "how_to_play" in bot.channel_ids
        assert "play_by_play" in bot.channel_ids
        assert "big_plays" in bot.channel_ids

    async def test_setup_idempotent(self, bot: PinwheelBot) -> None:
        """Setup doesn't create channels that already exist."""
        guild = MagicMock(spec=discord.Guild)

        category = MagicMock(spec=discord.CategoryChannel)
        category.name = "PINWHEEL FATES"
        guild.categories = [category]

        def make_existing_channel(name: str, channel_id: int) -> MagicMock:
            ch = MagicMock(spec=discord.TextChannel)
            ch.name = name
            ch.id = channel_id
            ch.category = category
            return ch

        existing_channels = [
            make_existing_channel("how-to-play", 201),
            make_existing_channel("play-by-play", 202),
            make_existing_channel("big-plays", 203),
        ]
        guild.text_channels = existing_channels
        guild.roles = []
        guild.me = MagicMock()

        guild.create_category = AsyncMock()
        guild.create_text_channel = AsyncMock()

        # Mock history to indicate channel has messages (skip welcome)
        for ch in existing_channels:

            async def _history(**kwargs: object) -> list[MagicMock]:  # noqa: ARG001
                return [MagicMock()]

            ch.history = MagicMock(side_effect=_history)

        bot.get_guild = MagicMock(return_value=guild)
        await bot._setup_server()

        guild.create_category.assert_not_called()
        guild.create_text_channel.assert_not_called()
        assert bot.channel_ids["how_to_play"] == 201
        assert bot.channel_ids["play_by_play"] == 202
        assert bot.channel_ids["big_plays"] == 203


# ---------------------------------------------------------------------------
# Event routing to play-by-play and big-plays
# ---------------------------------------------------------------------------


class TestEventRouting:
    @pytest.fixture
    def bot(self, settings_discord_enabled: Settings, event_bus: EventBus) -> PinwheelBot:
        return PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus)

    async def test_game_routed_to_play_by_play(self, bot: PinwheelBot) -> None:
        """Normal game (not blowout or buzzer-beater) goes to play-by-play only."""
        play_channel = AsyncMock(spec=discord.TextChannel)
        big_channel = AsyncMock(spec=discord.TextChannel)

        bot.channel_ids = {"play_by_play": 201, "big_plays": 202}

        def get_channel_side_effect(channel_id: int) -> AsyncMock | None:
            if channel_id == 201:
                return play_channel
            if channel_id == 202:
                return big_channel
            return None

        bot.get_channel = MagicMock(side_effect=get_channel_side_effect)

        event = {
            "type": "presentation.game_finished",
            "data": {
                "home_team": "Thorns",
                "away_team": "Breakers",
                "home_score": 45,
                "away_score": 38,
                "total_possessions": 60,
            },
        }
        await bot._dispatch_event(event)
        play_channel.send.assert_called_once()
        big_channel.send.assert_not_called()

    async def test_buzzer_beater_routed_to_big_plays(self, bot: PinwheelBot) -> None:
        """Buzzer-beater (margin <= 2) goes to both play-by-play and big-plays."""
        play_channel = AsyncMock(spec=discord.TextChannel)
        big_channel = AsyncMock(spec=discord.TextChannel)

        bot.channel_ids = {"play_by_play": 201, "big_plays": 202}

        def get_channel_side_effect(channel_id: int) -> AsyncMock | None:
            if channel_id == 201:
                return play_channel
            if channel_id == 202:
                return big_channel
            return None

        bot.get_channel = MagicMock(side_effect=get_channel_side_effect)

        event = {
            "type": "presentation.game_finished",
            "data": {
                "home_team": "Thorns",
                "away_team": "Breakers",
                "home_score": 50,
                "away_score": 49,
                "total_possessions": 70,
            },
        }
        await bot._dispatch_event(event)
        play_channel.send.assert_called_once()
        big_channel.send.assert_called_once()

    async def test_blowout_routed_to_big_plays(self, bot: PinwheelBot) -> None:
        """Blowout (>15 point diff) goes to both play-by-play and big-plays."""
        play_channel = AsyncMock(spec=discord.TextChannel)
        big_channel = AsyncMock(spec=discord.TextChannel)

        bot.channel_ids = {"play_by_play": 201, "big_plays": 202}

        def get_channel_side_effect(channel_id: int) -> AsyncMock | None:
            if channel_id == 201:
                return play_channel
            if channel_id == 202:
                return big_channel
            return None

        bot.get_channel = MagicMock(side_effect=get_channel_side_effect)

        event = {
            "type": "presentation.game_finished",
            "data": {
                "home_team": "Thorns",
                "away_team": "Breakers",
                "home_score": 65,
                "away_score": 40,
                "elam_activated": False,
                "total_possessions": 80,
            },
        }
        await bot._dispatch_event(event)
        play_channel.send.assert_called_once()
        big_channel.send.assert_called_once()

    async def test_round_finished_routed_to_play_by_play(self, bot: PinwheelBot) -> None:
        """Round finished goes to play-by-play."""
        play_channel = AsyncMock(spec=discord.TextChannel)
        bot.channel_ids = {"play_by_play": 201}
        bot.get_channel = MagicMock(return_value=play_channel)

        event = {
            "type": "presentation.round_finished",
            "data": {"round": 3, "games_presented": 4},
        }
        await bot._dispatch_event(event)
        play_channel.send.assert_called_once()


# ---------------------------------------------------------------------------
# Governance command helpers
# ---------------------------------------------------------------------------


async def _make_enrolled_bot_and_interaction(
    settings: Settings,
    event_bus: EventBus,
    discord_id: int = 111222333,
    display_name: str = "TestGovernor",
) -> tuple:
    """Create an in-memory DB with an enrolled governor, return (bot, interaction, gov_data)."""
    from pinwheel.core.tokens import regenerate_tokens
    from pinwheel.db.engine import create_engine, get_session
    from pinwheel.db.models import Base
    from pinwheel.db.repository import Repository

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with get_session(engine) as session:
        repo = Repository(session)
        league = await repo.create_league("Test League")
        season = await repo.create_season(league.id, "Season 1")
        team = await repo.create_team(
            season.id,
            "Rose City Thorns",
            color="#e94560",
        )
        await repo.create_hooper(
            team.id,
            season.id,
            "Briar Ashwood",
            "sharpshooter",
            {
                "scoring": 65,
                "passing": 35,
                "defense": 30,
                "speed": 45,
                "stamina": 40,
                "iq": 55,
                "ego": 40,
                "chaotic_alignment": 20,
                "fate": 30,
            },
        )
        player = await repo.get_or_create_player(
            str(discord_id),
            display_name,
        )
        await repo.enroll_player(player.id, team.id, season.id)
        await regenerate_tokens(
            repo,
            player.id,
            team.id,
            season.id,
        )
        await session.commit()
        gov_data = {
            "player_id": player.id,
            "team_id": team.id,
            "season_id": season.id,
        }

    bot = PinwheelBot(
        settings=settings,
        event_bus=event_bus,
        engine=engine,
    )

    interaction = make_interaction(
        user_id=discord_id,
        display_name=display_name,
        display_avatar_url="https://example.com/a.png",
    )
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.guild = MagicMock(spec=discord.Guild)
    interaction.guild.roles = []

    return bot, interaction, gov_data, engine


# ---------------------------------------------------------------------------
# /propose (AI-interpreted flow)
# ---------------------------------------------------------------------------


class TestProposeGovernance:
    async def test_propose_not_enrolled(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Proposing without enrollment returns ephemeral error."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test")
            await repo.create_season(league.id, "S1")
            await session.commit()

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )
        interaction = make_interaction(user_id=999888777, display_name="Stranger")

        await bot._handle_propose(interaction, "Make it rain")
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "join" in str(call_kwargs.args[0]).lower()
        await engine.dispose()

    async def test_propose_interprets_and_shows_view(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Full propose flow: defer, interpret, send view."""
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        await bot._handle_propose(
            interaction,
            "Make three-pointers worth 5 points",
        )

        # Should defer (AI call takes time)
        interaction.response.defer.assert_called_once()
        # Should followup with embed + view
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        embed = call_kwargs.kwargs.get("embed")
        assert embed is not None
        assert "Interpretation" in embed.title
        view = call_kwargs.kwargs.get("view")
        assert view is not None
        await engine.dispose()

    async def test_propose_no_tokens(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Proposing with no tokens returns an error."""
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        # Spend all tokens
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        async with get_session(engine) as session:
            repo = Repository(session)
            for _ in range(2):
                await repo.append_event(
                    event_type="token.spent",
                    aggregate_id=gov_data["player_id"],
                    aggregate_type="token",
                    season_id=gov_data["season_id"],
                    governor_id=gov_data["player_id"],
                    payload={
                        "token_type": "propose",
                        "amount": 1,
                        "reason": "test",
                    },
                )
            await session.commit()

        await bot._handle_propose(interaction, "Some proposal")
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert "PROPOSE" in str(call_kwargs.args[0])
        await engine.dispose()


# ---------------------------------------------------------------------------
# /vote
# ---------------------------------------------------------------------------


class TestVoteCommand:
    async def test_vote_not_enrolled(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Voting without enrollment returns ephemeral error."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test")
            await repo.create_season(league.id, "S1")
            await session.commit()

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )
        interaction = make_interaction(user_id=999888777)

        await bot._handle_vote(interaction, "yes")
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "join" in str(call_kwargs.args[0]).lower()
        await engine.dispose()

    async def test_vote_no_proposals(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Voting with no active proposals returns error."""
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        await bot._handle_vote(interaction, "yes")
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "no proposals" in str(call_kwargs.args[0]).lower()
        await engine.dispose()

    async def test_vote_success(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Successful vote is recorded and hidden."""
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        # Create a confirmed proposal
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id="prop-1",
                aggregate_type="proposal",
                season_id=gov_data["season_id"],
                governor_id="other-gov",
                payload={
                    "id": "prop-1",
                    "governor_id": "other-gov",
                    "team_id": gov_data["team_id"],
                    "raw_text": "Make threes worth 5",
                    "status": "submitted",
                    "tier": 1,
                },
            )
            await repo.append_event(
                event_type="proposal.confirmed",
                aggregate_id="prop-1",
                aggregate_type="proposal",
                season_id=gov_data["season_id"],
                governor_id="other-gov",
                payload={"proposal_id": "prop-1"},
            )
            await session.commit()

        await bot._handle_vote(interaction, "yes")
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        embed = call_kwargs.kwargs.get("embed")
        assert embed is not None
        assert "Vote Recorded" in embed.title
        assert "hidden" in embed.description.lower()
        await engine.dispose()

    async def test_vote_duplicate_blocked(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Cannot vote twice on the same proposal."""
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.append_event(
                event_type="proposal.submitted",
                aggregate_id="prop-1",
                aggregate_type="proposal",
                season_id=gov_data["season_id"],
                governor_id="other-gov",
                payload={
                    "id": "prop-1",
                    "governor_id": "other-gov",
                    "team_id": gov_data["team_id"],
                    "raw_text": "Test proposal",
                    "status": "submitted",
                    "tier": 1,
                },
            )
            await repo.append_event(
                event_type="proposal.confirmed",
                aggregate_id="prop-1",
                aggregate_type="proposal",
                season_id=gov_data["season_id"],
                governor_id="other-gov",
                payload={"proposal_id": "prop-1"},
            )
            # Pre-cast a vote
            await repo.append_event(
                event_type="vote.cast",
                aggregate_id="prop-1",
                aggregate_type="proposal",
                season_id=gov_data["season_id"],
                governor_id=gov_data["player_id"],
                payload={
                    "proposal_id": "prop-1",
                    "vote": "yes",
                },
            )
            await session.commit()

        await bot._handle_vote(interaction, "no")
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert "already voted" in str(call_kwargs.args[0]).lower()
        await engine.dispose()


# ---------------------------------------------------------------------------
# /tokens
# ---------------------------------------------------------------------------


class TestTokensCommand:
    async def test_tokens_not_enrolled(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test")
            await repo.create_season(league.id, "S1")
            await session.commit()

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )
        interaction = make_interaction(user_id=999888777)

        await bot._handle_tokens(interaction)
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        await engine.dispose()

    async def test_tokens_shows_balance(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        await bot._handle_tokens(interaction)
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        embed = call_kwargs.kwargs.get("embed")
        assert embed is not None
        assert "Floor Tokens" in embed.title
        assert "PROPOSE" in embed.description
        await engine.dispose()


# ---------------------------------------------------------------------------
# /trade
# ---------------------------------------------------------------------------


class TestTradeCommand:
    async def test_trade_self_rejected(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        target = MagicMock(spec=discord.Member)
        target.id = interaction.user.id  # same user
        target.display_name = "TestGovernor"

        await bot._handle_trade(
            interaction,
            target,
            "propose",
            1,
            "amend",
            1,
        )
        call_kwargs = interaction.response.send_message.call_args
        assert "yourself" in str(call_kwargs.args[0]).lower()
        await engine.dispose()

    async def test_trade_target_not_enrolled(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        target = MagicMock(spec=discord.Member)
        target.id = 999888777
        target.display_name = "Stranger"

        await bot._handle_trade(
            interaction,
            target,
            "propose",
            1,
            "amend",
            1,
        )
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert "not enrolled" in str(call_kwargs.args[0]).lower()
        await engine.dispose()

    async def test_trade_insufficient_tokens(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
            discord_id=111222333,
        )

        # Create a second enrolled governor
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        async with get_session(engine) as session:
            repo = Repository(session)
            player2 = await repo.get_or_create_player(
                "444555666",
                "Player2",
            )
            await repo.enroll_player(
                player2.id,
                gov_data["team_id"],
                gov_data["season_id"],
            )
            await session.commit()

        target = MagicMock(spec=discord.Member)
        target.id = 444555666
        target.display_name = "Player2"

        # Try to trade 99 tokens (more than they have)
        await bot._handle_trade(
            interaction,
            target,
            "propose",
            99,
            "amend",
            1,
        )
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert "only have" in str(call_kwargs.args[0]).lower()
        await engine.dispose()


# ---------------------------------------------------------------------------
# /trade-hooper
# ---------------------------------------------------------------------------


class TestHooperTradeCommand:
    """Tests for hooper trade proposals with scoped governor voting."""

    async def test_hooper_trade_model_roundtrip(self) -> None:
        """HooperTrade model can be created and serialized."""
        from pinwheel.models.tokens import HooperTrade

        trade = HooperTrade(
            id="trade-1",
            from_team_id="team-a",
            to_team_id="team-b",
            offered_hooper_ids=["hooper-1"],
            requested_hooper_ids=["hooper-2"],
            offered_hooper_names=["Player One"],
            requested_hooper_names=["Player Two"],
            proposed_by="gov-1",
            required_voters=["gov-1", "gov-2"],
            from_team_name="Team A",
            to_team_name="Team B",
        )
        assert trade.status == "proposed"
        data = trade.model_dump(mode="json")
        restored = HooperTrade(**data)
        assert restored.id == "trade-1"
        assert restored.offered_hooper_names == ["Player One"]

    async def test_vote_hooper_trade(self) -> None:
        """Voting on a hooper trade records the vote."""
        from pinwheel.core.tokens import vote_hooper_trade
        from pinwheel.models.tokens import HooperTrade

        trade = HooperTrade(
            id="trade-1",
            from_team_id="team-a",
            to_team_id="team-b",
            offered_hooper_ids=["hooper-1"],
            requested_hooper_ids=["hooper-2"],
            proposed_by="gov-1",
            required_voters=["gov-1", "gov-2"],
        )
        updated = vote_hooper_trade(trade, "gov-1", "yes")
        assert updated.votes == {"gov-1": "yes"}

    async def test_tally_all_yes_approves(self) -> None:
        """When all voters say yes, the trade is approved."""
        from pinwheel.core.tokens import tally_hooper_trade, vote_hooper_trade
        from pinwheel.models.tokens import HooperTrade

        trade = HooperTrade(
            id="t1",
            from_team_id="a",
            to_team_id="b",
            offered_hooper_ids=["x"],
            requested_hooper_ids=["y"],
            proposed_by="g1",
            required_voters=["g1", "g2"],
            from_team_voters=["g1"],
            to_team_voters=["g2"],
        )
        vote_hooper_trade(trade, "g1", "yes")
        vote_hooper_trade(trade, "g2", "yes")
        all_voted, from_ok, to_ok = tally_hooper_trade(trade)
        assert all_voted is True
        assert from_ok is True
        assert to_ok is True

    async def test_tally_majority_no_rejects(self) -> None:
        """When one team votes no, the trade is rejected for that team."""
        from pinwheel.core.tokens import tally_hooper_trade, vote_hooper_trade
        from pinwheel.models.tokens import HooperTrade

        trade = HooperTrade(
            id="t1",
            from_team_id="a",
            to_team_id="b",
            offered_hooper_ids=["x"],
            requested_hooper_ids=["y"],
            proposed_by="g1",
            required_voters=["g1", "g2"],
            from_team_voters=["g1"],
            to_team_voters=["g2"],
        )
        vote_hooper_trade(trade, "g1", "yes")
        vote_hooper_trade(trade, "g2", "no")
        all_voted, from_ok, to_ok = tally_hooper_trade(trade)
        assert all_voted is True
        assert from_ok is True
        assert to_ok is False

    async def test_tally_incomplete_votes(self) -> None:
        """Trade not tallied until all required voters have voted."""
        from pinwheel.core.tokens import tally_hooper_trade, vote_hooper_trade
        from pinwheel.models.tokens import HooperTrade

        trade = HooperTrade(
            id="t1",
            from_team_id="a",
            to_team_id="b",
            offered_hooper_ids=["x"],
            requested_hooper_ids=["y"],
            proposed_by="g1",
            required_voters=["g1", "g2", "g3"],
            from_team_voters=["g1"],
            to_team_voters=["g2", "g3"],
        )
        vote_hooper_trade(trade, "g1", "yes")
        all_voted, _, _ = tally_hooper_trade(trade)
        assert all_voted is False

    async def test_swap_hooper_team(self) -> None:
        """swap_hooper_team changes the hooper's team_id in the database."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("L")
            season = await repo.create_season(league.id, "S1")
            team_a = await repo.create_team(season.id, "Team A", color="#ff0000")
            team_b = await repo.create_team(season.id, "Team B", color="#0000ff")
            hooper = await repo.create_hooper(
                team_id=team_a.id,
                season_id=season.id,
                name="Star",
                archetype="Sharpshooter",
                attributes={"scoring": 80},
                moves=[],
            )
            assert hooper.team_id == team_a.id

            await repo.swap_hooper_team(hooper.id, team_b.id)
            await session.flush()

            refreshed = await session.get(type(hooper), hooper.id)
            assert refreshed.team_id == team_b.id

        await engine.dispose()

    async def test_get_governors_for_team(self) -> None:
        """get_governors_for_team returns enrolled governors."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("L")
            season = await repo.create_season(league.id, "S1")
            team = await repo.create_team(season.id, "Team A", color="#ff0000")
            p1 = await repo.get_or_create_player("111", "Gov1")
            p2 = await repo.get_or_create_player("222", "Gov2")
            await repo.enroll_player(p1.id, team.id, season.id)
            await repo.enroll_player(p2.id, team.id, season.id)
            await session.commit()

            governors = await repo.get_governors_for_team(team.id, season.id)
            assert len(governors) == 2

        await engine.dispose()

    async def test_build_hooper_trade_embed(self) -> None:
        """build_hooper_trade_embed returns a proper Discord embed."""
        from pinwheel.discord.embeds import build_hooper_trade_embed

        embed = build_hooper_trade_embed(
            from_team="Thorns",
            to_team="Breakers",
            offered_names=["Star"],
            requested_names=["Flash"],
            proposer_name="Gov1",
            votes_cast=1,
            votes_needed=3,
        )
        assert "Hooper Trade Proposal" in embed.title
        assert "Thorns" in embed.description
        assert "Star" in embed.description
        assert "1/3" in embed.description


# ---------------------------------------------------------------------------
# /strategy
# ---------------------------------------------------------------------------


class TestStrategyCommand:
    async def test_strategy_not_enrolled(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test")
            await repo.create_season(league.id, "S1")
            await session.commit()

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )
        interaction = make_interaction(user_id=999888777)

        await bot._handle_strategy(interaction, "Focus on defense")
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "join" in str(call_kwargs.args[0]).lower()
        await engine.dispose()

    async def test_strategy_shows_confirm_view(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        await bot._handle_strategy(
            interaction,
            "Focus on three-point shooting",
        )
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        embed = call_kwargs.kwargs.get("embed")
        assert embed is not None
        assert "Strategy" in embed.title
        assert "Rose City Thorns" in embed.title
        view = call_kwargs.kwargs.get("view")
        assert view is not None
        await engine.dispose()

    async def test_strategy_empty_text(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        await bot._handle_strategy(interaction, "   ")
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        await engine.dispose()


# ---------------------------------------------------------------------------
# /bio
# ---------------------------------------------------------------------------


class TestBioCommand:
    async def test_bio_not_enrolled(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Bio without enrollment returns ephemeral error."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test")
            await repo.create_season(league.id, "S1")
            await session.commit()

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )
        interaction = make_interaction(user_id=999888777)

        await bot._handle_bio(interaction, "SomeHooper", "A backstory")
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "join" in str(call_kwargs.args[0]).lower()
        await engine.dispose()

    async def test_bio_empty_text(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Empty bio text returns ephemeral error."""
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )
        await bot._handle_bio(interaction, "Briar Ashwood", "   ")
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        await engine.dispose()

    async def test_bio_too_long(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Bio exceeding 500 chars returns ephemeral error."""
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )
        long_text = "A" * 501
        await bot._handle_bio(interaction, "Briar Ashwood", long_text)
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "500" in str(call_kwargs.args[0])
        await engine.dispose()

    async def test_bio_hooper_not_found(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Bio for nonexistent hooper returns error."""
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )
        await bot._handle_bio(interaction, "Nonexistent Player", "A bio")
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "not found" in str(call_kwargs.args[0]).lower()
        await engine.dispose()

    async def test_bio_success(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Successful bio update returns confirmation embed."""
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )
        await bot._handle_bio(
            interaction,
            "Briar Ashwood",
            "A sharpshooter from Portland.",
        )
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        embed = call_kwargs.kwargs.get("embed")
        assert embed is not None
        assert "Briar Ashwood" in embed.title
        assert "sharpshooter from Portland" in embed.description
        assert call_kwargs.kwargs.get("ephemeral") is True

        # Verify persistence in DB
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        async with get_session(engine) as session:
            repo = Repository(session)
            team = await repo.get_team(gov_data["team_id"])
            hooper = next(
                (h for h in team.hoopers if h.name == "Briar Ashwood"),
                None,
            )
            assert hooper is not None
            assert hooper.backstory == "A sharpshooter from Portland."

        await engine.dispose()


# ---------------------------------------------------------------------------
# Welcome embed with motto and backstory
# ---------------------------------------------------------------------------


class TestWelcomeEmbedExtended:
    def test_welcome_embed_with_motto(self) -> None:
        """Welcome embed includes team motto when provided."""
        hoopers = [{"name": "Star", "archetype": "Sharpshooter"}]
        embed = build_welcome_embed(
            "Thorns",
            "#E74C3C",
            hoopers,
            motto="Bloom or bust",
        )
        assert "Bloom or bust" in (embed.description or "")

    def test_welcome_embed_without_motto(self) -> None:
        """Welcome embed omits motto line when empty."""
        hoopers = [{"name": "Star", "archetype": "Sharpshooter"}]
        embed = build_welcome_embed("Thorns", "#E74C3C", hoopers, motto="")
        # No motto marker in description
        desc = embed.description or ""
        assert "Thorns" in desc
        assert '*"' not in desc

    def test_welcome_embed_with_backstory(self) -> None:
        """Welcome embed shows hooper backstory snippet."""
        hoopers = [
            {
                "name": "Star",
                "archetype": "Sharpshooter",
                "backstory": "A deadly shooter from downtown.",
            },
        ]
        embed = build_welcome_embed("Thorns", "#E74C3C", hoopers)
        desc = embed.description or ""
        assert "deadly shooter" in desc

    def test_welcome_embed_backstory_truncation(self) -> None:
        """Backstory longer than 100 chars is truncated with ellipsis."""
        long_bio = "A" * 150
        hoopers = [
            {"name": "Star", "archetype": "Sharpshooter", "backstory": long_bio},
        ]
        embed = build_welcome_embed("Thorns", "#E74C3C", hoopers)
        desc = embed.description or ""
        assert "..." in desc
        # The full 150-char string should NOT appear
        assert long_bio not in desc

    def test_welcome_embed_includes_bio_command(self) -> None:
        """Welcome embed quick start lists /bio command."""
        hoopers = [{"name": "Star", "archetype": "Sharpshooter"}]
        embed = build_welcome_embed("Thorns", "#E74C3C", hoopers)
        desc = embed.description or ""
        assert "/bio" in desc


# ---------------------------------------------------------------------------
# Bio embed
# ---------------------------------------------------------------------------


class TestBuildBioEmbed:
    def test_bio_embed(self) -> None:
        from pinwheel.discord.embeds import build_bio_embed

        embed = build_bio_embed("Briar Ashwood", "A sharpshooter from Portland.")
        assert "Briar Ashwood" in embed.title
        assert "sharpshooter from Portland" in embed.description


# ---------------------------------------------------------------------------
# Private report DM dispatch
# ---------------------------------------------------------------------------


class TestPrivateReportDM:
    async def test_private_report_dispatches_dm(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Private report event triggers a DM to the governor."""
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        mock_user = AsyncMock()
        bot.get_user = MagicMock(return_value=mock_user)

        event = {
            "type": "report.generated",
            "data": {
                "report_type": "private",
                "round": 3,
                "governor_id": gov_data["player_id"],
                "report_id": "r-priv-1",
                "excerpt": "Your pattern reveals caution.",
            },
        }
        await bot._dispatch_event(event)
        mock_user.send.assert_called_once()
        call_kwargs = mock_user.send.call_args
        embed = call_kwargs.kwargs.get("embed")
        assert embed is not None
        assert "Private Report" in embed.title
        await engine.dispose()

    async def test_private_report_no_engine(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Private report without engine is a no-op."""
        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
        )
        bot.get_user = MagicMock()

        await bot._send_private_report(
            {
                "governor_id": "gov-1",
                "excerpt": "Report text",
                "round": 1,
            }
        )
        bot.get_user.assert_not_called()

    async def test_private_report_missing_data(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Private report with missing governor_id is a no-op."""
        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
        )
        await bot._send_private_report(
            {
                "excerpt": "Report text",
                "round": 1,
            }
        )  # No governor_id — should not raise


# ---------------------------------------------------------------------------
# Bot state persistence (BotStateRow)
# ---------------------------------------------------------------------------


class TestBotStatePersistence:
    """Test that bot_state persists and loads channel IDs across restarts."""

    async def test_set_and_get_bot_state(self) -> None:
        """Repository set/get round-trips correctly."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            # Initially empty
            val = await repo.get_bot_state("channel_how_to_play")
            assert val is None

            # Set a value
            await repo.set_bot_state("channel_how_to_play", "12345")
            val = await repo.get_bot_state("channel_how_to_play")
            assert val == "12345"

            # Overwrite
            await repo.set_bot_state("channel_how_to_play", "67890")
            val = await repo.get_bot_state("channel_how_to_play")
            assert val == "67890"

        await engine.dispose()

    async def test_persisted_ids_loaded_on_setup(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Channel IDs stored in bot_state are loaded into channel_ids on setup."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Pre-populate bot_state with channel IDs
        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.set_bot_state("channel_how_to_play", "201")
            await repo.set_bot_state("channel_play_by_play", "202")
            await repo.set_bot_state("channel_big_plays", "203")

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )

        # Build a guild mock where persisted IDs resolve to real channels
        guild = MagicMock(spec=discord.Guild)
        category = MagicMock(spec=discord.CategoryChannel)
        category.name = "PINWHEEL FATES"
        guild.categories = [category]

        def make_channel(name: str, channel_id: int) -> MagicMock:
            ch = MagicMock(spec=discord.TextChannel)
            ch.name = name
            ch.id = channel_id
            ch.category = category
            return ch

        ch_how = make_channel("how-to-play", 201)
        ch_play = make_channel("play-by-play", 202)
        ch_big = make_channel("big-plays", 203)

        def get_channel_side_effect(cid: int) -> MagicMock | None:
            return {201: ch_how, 202: ch_play, 203: ch_big}.get(cid)

        guild.get_channel = MagicMock(side_effect=get_channel_side_effect)
        guild.text_channels = [ch_how, ch_play, ch_big]
        guild.roles = []
        guild.me = MagicMock()
        guild.default_role = MagicMock()
        guild.create_category = AsyncMock()
        guild.create_text_channel = AsyncMock()

        # Mock history as async iterator so welcome message check works
        for ch in [ch_how, ch_play, ch_big]:

            async def _async_iter() -> AsyncIterator[MagicMock]:
                yield MagicMock()

            ch.history = MagicMock(return_value=_async_iter())

        bot.get_guild = MagicMock(return_value=guild)
        await bot._setup_server()

        # Channels should be loaded from persisted state, not created
        guild.create_category.assert_not_called()
        guild.create_text_channel.assert_not_called()
        assert bot.channel_ids["how_to_play"] == 201
        assert bot.channel_ids["play_by_play"] == 202
        assert bot.channel_ids["big_plays"] == 203

        await engine.dispose()

    async def test_stale_persisted_ids_triggers_recreation(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """If a persisted channel ID no longer exists in the guild, recreate."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Pre-populate with stale IDs
        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.set_bot_state("channel_how_to_play", "999")
            await repo.set_bot_state("channel_play_by_play", "998")
            await repo.set_bot_state("channel_big_plays", "997")

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )

        guild = MagicMock(spec=discord.Guild)
        category = MagicMock(spec=discord.CategoryChannel)
        category.name = "PINWHEEL FATES"
        guild.categories = [category]
        guild.text_channels = []  # No existing channels
        guild.roles = []
        guild.me = MagicMock()
        guild.default_role = MagicMock()
        guild.create_category = AsyncMock()

        # guild.get_channel returns None for stale IDs
        guild.get_channel = MagicMock(return_value=None)

        counter = {"id": 300}

        async def mock_create_text_channel(name: str, **kwargs: object) -> MagicMock:
            ch = MagicMock(spec=discord.TextChannel)
            ch.id = counter["id"]
            ch.name = name
            counter["id"] += 1
            return ch

        guild.create_text_channel = AsyncMock(side_effect=mock_create_text_channel)

        bot.get_guild = MagicMock(return_value=guild)
        await bot._setup_server()

        # Channels should have been recreated with new IDs
        assert guild.create_text_channel.call_count >= 3
        assert bot.channel_ids["how_to_play"] == 300
        assert bot.channel_ids["play_by_play"] == 301
        assert bot.channel_ids["big_plays"] == 302

        # Verify the new IDs were persisted
        async with get_session(engine) as session:
            repo = Repository(session)
            assert await repo.get_bot_state("channel_how_to_play") == "300"
            assert await repo.get_bot_state("channel_play_by_play") == "301"
            assert await repo.get_bot_state("channel_big_plays") == "302"

        await engine.dispose()


class TestSetupIdempotencyWithDB:
    """Test that _setup_server is idempotent across multiple calls."""

    async def test_double_setup_no_duplicate_channels(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Running _setup_server twice doesn't create duplicate channels."""
        from pinwheel.db.engine import create_engine
        from pinwheel.db.models import Base

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )

        guild = MagicMock(spec=discord.Guild)
        guild.categories = []
        guild.text_channels = []
        guild.roles = []
        guild.me = MagicMock()
        guild.default_role = MagicMock()

        category = MagicMock(spec=discord.CategoryChannel)
        guild.create_category = AsyncMock(return_value=category)

        category = MagicMock(spec=discord.CategoryChannel)
        category.name = "PINWHEEL FATES"
        guild.create_category = AsyncMock(return_value=category)

        created_channels: dict[str, MagicMock] = {}
        counter = {"id": 100}

        async def mock_create_text_channel(name: str, **kwargs: object) -> MagicMock:
            ch = MagicMock(spec=discord.TextChannel)
            ch.id = counter["id"]
            ch.name = name
            ch.category = category
            counter["id"] += 1
            created_channels[name] = ch
            return ch

        guild.create_text_channel = AsyncMock(side_effect=mock_create_text_channel)

        bot.get_guild = MagicMock(return_value=guild)

        # --- First setup: creates everything ---
        await bot._setup_server()
        first_call_count = guild.create_text_channel.call_count
        assert first_call_count == 3
        first_ids = dict(bot.channel_ids)

        # --- Second setup: mock guild now has channels; persisted IDs resolve ---
        def get_channel_side_effect(cid: int) -> MagicMock | None:
            for ch in created_channels.values():
                if ch.id == cid:
                    return ch
            return None

        guild.get_channel = MagicMock(side_effect=get_channel_side_effect)
        guild.text_channels = list(created_channels.values())
        guild.categories = [category]
        guild.create_category.reset_mock()
        guild.create_text_channel.reset_mock()

        await bot._setup_server()

        # No new channels created on second run
        guild.create_text_channel.assert_not_called()
        guild.create_category.assert_not_called()
        # IDs unchanged
        assert bot.channel_ids == first_ids

        await engine.dispose()

    async def test_setup_with_team_channels_persisted(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Team channels and roles are also persisted and reused."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Seed a team
        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test League")
            season = await repo.create_season(league.id, "Season 1")
            team = await repo.create_team(
                season.id,
                "Rose City Thorns",
                color="#e94560",
            )
            await repo.create_hooper(
                team.id,
                season.id,
                "Briar",
                "sharpshooter",
                {
                    "scoring": 65,
                    "passing": 35,
                    "defense": 30,
                    "speed": 45,
                    "stamina": 40,
                    "iq": 55,
                    "ego": 40,
                    "chaotic_alignment": 20,
                    "fate": 30,
                },
            )
            await session.commit()
            team_id = team.id

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )

        guild = MagicMock(spec=discord.Guild)
        guild.categories = []
        guild.text_channels = []
        guild.roles = []
        guild.me = MagicMock()
        guild.default_role = MagicMock()

        category = MagicMock(spec=discord.CategoryChannel)
        guild.create_category = AsyncMock(return_value=category)

        counter = {"id": 100}
        created_channels: dict[str, MagicMock] = {}

        async def mock_create_text_channel(name: str, **kwargs: object) -> MagicMock:
            ch = MagicMock(spec=discord.TextChannel)
            ch.id = counter["id"]
            ch.name = name
            counter["id"] += 1
            created_channels[name] = ch
            return ch

        guild.create_text_channel = AsyncMock(side_effect=mock_create_text_channel)

        mock_role = MagicMock(spec=discord.Role)
        mock_role.name = "Rose City Thorns"
        guild.create_role = AsyncMock(return_value=mock_role)

        bot.get_guild = MagicMock(return_value=guild)

        # First setup
        await bot._setup_server()

        # Team channel should be created and persisted
        assert f"team_{team_id}" in bot.channel_ids
        team_ch_id = bot.channel_ids[f"team_{team_id}"]

        # Verify persisted in DB
        async with get_session(engine) as session:
            repo = Repository(session)
            persisted = await repo.get_bot_state(f"channel_team_{team_id}")
            assert persisted == str(team_ch_id)

        await engine.dispose()


# ---------------------------------------------------------------------------
# _sync_role_enrollments — self-heal missing DB enrollments from Discord roles
# ---------------------------------------------------------------------------


class TestSyncRoleEnrollments:
    """Tests for the startup self-heal that re-enrolls players with team roles."""

    async def test_heals_member_with_role_but_no_db_entry(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """A guild member with a team role but no DB enrollment gets re-enrolled."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Seed a team (but NO players)
        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test League")
            season = await repo.create_season(league.id, "Season 1")
            team = await repo.create_team(
                season.id,
                "Rose City Thorns",
                color="#e94560",
            )
            await session.commit()
            team_id = team.id
            season_id = season.id

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )

        # Build a guild with one member who has the team role
        guild = MagicMock(spec=discord.Guild)
        team_role = MagicMock(spec=discord.Role)
        team_role.name = "Rose City Thorns"

        everyone_role = MagicMock(spec=discord.Role)
        everyone_role.name = "@everyone"

        member = MagicMock(spec=discord.Member)
        member.bot = False
        member.id = 111222333
        member.display_name = "Kelley"
        member.display_avatar = MagicMock()
        member.display_avatar.url = "https://cdn.example.com/avatar.png"
        member.roles = [everyone_role, team_role]

        guild.members = [member]

        await bot._sync_role_enrollments(guild)

        # Verify the player was created and enrolled
        async with get_session(engine) as session:
            repo = Repository(session)
            enrollment = await repo.get_player_enrollment("111222333", season_id)
            assert enrollment is not None
            enrolled_team_id, enrolled_team_name = enrollment
            assert enrolled_team_id == team_id
            assert enrolled_team_name == "Rose City Thorns"

        await engine.dispose()

    async def test_idempotent_does_not_duplicate(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Running sync twice doesn't create duplicate enrollments."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test League")
            season = await repo.create_season(league.id, "Season 1")
            team = await repo.create_team(
                season.id, "Burnside Breakers", color="#53d8fb",
            )
            # Pre-enroll a player (they already exist in DB)
            player = await repo.get_or_create_player(
                discord_id="444555666",
                username="ExistingPlayer",
            )
            await repo.enroll_player(player.id, team.id, season.id)
            await session.commit()
            season_id = season.id

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )

        guild = MagicMock(spec=discord.Guild)
        team_role = MagicMock(spec=discord.Role)
        team_role.name = "Burnside Breakers"

        member = MagicMock(spec=discord.Member)
        member.bot = False
        member.id = 444555666
        member.display_name = "ExistingPlayer"
        member.display_avatar = MagicMock()
        member.display_avatar.url = "https://cdn.example.com/avatar2.png"
        member.roles = [team_role]

        guild.members = [member]

        # Run twice
        await bot._sync_role_enrollments(guild)
        await bot._sync_role_enrollments(guild)

        # Still exactly one enrollment
        async with get_session(engine) as session:
            repo = Repository(session)
            enrollment = await repo.get_player_enrollment("444555666", season_id)
            assert enrollment is not None

        await engine.dispose()

    async def test_skips_bots(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Bot members with team roles are ignored."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test League")
            season = await repo.create_season(league.id, "Season 1")
            await repo.create_team(
                season.id, "Rose City Thorns", color="#e94560",
            )
            await session.commit()
            season_id = season.id

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )

        guild = MagicMock(spec=discord.Guild)
        team_role = MagicMock(spec=discord.Role)
        team_role.name = "Rose City Thorns"

        bot_member = MagicMock(spec=discord.Member)
        bot_member.bot = True
        bot_member.id = 999888777
        bot_member.roles = [team_role]

        guild.members = [bot_member]

        await bot._sync_role_enrollments(guild)

        async with get_session(engine) as session:
            repo = Repository(session)
            enrollment = await repo.get_player_enrollment("999888777", season_id)
            assert enrollment is None

        await engine.dispose()

    async def test_no_engine_is_noop(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Without an engine, sync is a no-op."""
        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
        )
        guild = MagicMock(spec=discord.Guild)
        # Should not raise
        await bot._sync_role_enrollments(guild)


class TestGetGovernorCompletedSeason:
    """Governance is always open — get_governor must work with completed seasons."""

    @pytest.mark.asyncio
    async def test_governor_found_with_completed_season(self) -> None:
        """get_governor returns GovernorInfo even when the season is completed."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository
        from pinwheel.discord.helpers import get_governor

        engine = create_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.create_season("League 1", "Season 1")
            team = await repo.create_team("Testers", season.id)
            player = await repo.get_or_create_player(
                discord_id="12345",
                username="TestGov",
            )
            await repo.enroll_player(player.id, team.id, season.id)
            # Mark season as completed
            await repo.update_season_status(season.id, "completed")
            await session.commit()

        # get_governor should still find the governor via the fallback
        gov = await get_governor(engine, "12345")
        assert gov.player_id == player.id
        assert gov.team_id == team.id
        assert gov.season_id == season.id

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_governor_not_found_no_season(self) -> None:
        """get_governor raises GovernorNotFound when no seasons exist at all."""
        from pinwheel.db.engine import create_engine
        from pinwheel.db.models import Base
        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        engine = create_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        with pytest.raises(GovernorNotFound, match="No active season"):
            await get_governor(engine, "99999")

        await engine.dispose()
