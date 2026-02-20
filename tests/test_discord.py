"""Tests for the Discord bot integration.

All Discord objects are mocked — no real Discord connection required.
"""

from __future__ import annotations

import asyncio
import time
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
    build_server_welcome_embed,
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
    """Settings with Discord enabled (production env so the guard passes)."""
    return Settings(
        pinwheel_env="production",
        database_url="sqlite+aiosqlite:///:memory:",
        anthropic_api_key="",
        discord_bot_token="test-token-not-real",
        discord_channel_id="123456789",
        discord_guild_id="987654321",
        discord_enabled=True,
        session_secret_key="test-secret-for-prod",
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
            pinwheel_env="production",
            database_url="sqlite+aiosqlite:///:memory:",
            discord_bot_token="some-token",
            discord_enabled=False,
            session_secret_key="test-secret",
        )
        assert is_discord_enabled(settings) is False

    def test_disabled_when_token_empty(self) -> None:
        settings = Settings(
            pinwheel_env="production",
            database_url="sqlite+aiosqlite:///:memory:",
            discord_bot_token="",
            discord_enabled=True,
            session_secret_key="test-secret",
        )
        assert is_discord_enabled(settings) is False

    def test_disabled_in_development_env(self) -> None:
        """Discord bot must not start in development, even with token + flag."""
        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
            discord_bot_token="some-token",
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
        """Without an engine, roster defers and returns a database unavailable message."""
        interaction = make_interaction()
        await bot._handle_roster(interaction)
        interaction.response.defer.assert_called_once()
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args
        msg = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        assert "unavailable" in str(msg).lower()

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

    async def test_governance_dedup_stale_team_channels(self, bot: PinwheelBot) -> None:
        """Multiple team_* keys mapping to the same channel ID should only send once."""
        main_channel = AsyncMock(spec=discord.TextChannel)
        team_channel = AsyncMock(spec=discord.TextChannel)

        # Simulate stale entries: 3 different team keys all point to channel 5555
        bot.channel_ids = {
            "team_old_season1": 5555,
            "team_old_season2": 5555,
            "team_current": 5555,
        }

        def get_channel_side_effect(cid: int) -> AsyncMock:
            if cid == bot.main_channel_id:
                return main_channel
            if cid == 5555:
                return team_channel
            return None  # type: ignore[return-value]

        bot.get_channel = MagicMock(side_effect=get_channel_side_effect)

        event = {
            "type": "governance.window_closed",
            "data": {"round": 3, "proposals_count": 2, "rules_changed": 1},
        }
        await bot._dispatch_event(event)

        # main channel: 1 send, team channel: 1 send (not 3)
        assert main_channel.send.call_count == 1
        assert team_channel.send.call_count == 1

    async def test_get_unique_team_channels_deduplicates(self, bot: PinwheelBot) -> None:
        """_get_unique_team_channels returns each channel only once."""
        ch_a = MagicMock(spec=discord.TextChannel)
        ch_b = MagicMock(spec=discord.TextChannel)

        bot.channel_ids = {
            "team_1": 100,
            "team_2": 100,  # duplicate
            "team_3": 200,
            "play_by_play": 300,  # not a team channel
        }

        def get_channel_side_effect(cid: int) -> MagicMock | None:
            if cid == 100:
                return ch_a
            if cid == 200:
                return ch_b
            return None

        bot.get_channel = MagicMock(side_effect=get_channel_side_effect)

        result = bot._get_unique_team_channels()
        assert len(result) == 2
        assert ch_a in result
        assert ch_b in result

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
        embed = build_schedule_embed([])
        assert "No games scheduled" in (embed.description or "")

    def test_with_matchups(self) -> None:
        slots = [
            {
                "start_time": "1:00 PM ET",
                "games": [
                    {"home_team_name": "Thorns", "away_team_name": "Breakers"},
                    {"home_team_name": "Hammers", "away_team_name": "Herons"},
                ],
            },
        ]
        embed = build_schedule_embed(slots)
        desc = embed.description or ""
        assert "1:00 PM ET" in desc
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
        guild.roles = []
        guild.me = MagicMock()

        # fetch_channels returns empty list — nothing exists yet
        guild.fetch_channels = AsyncMock(return_value=[])

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

        # fetch_channels returns both the category and existing text channels
        guild.fetch_channels = AsyncMock(return_value=[category, *existing_channels])
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
        """Full propose flow: defer, thinking msg, interpret, edit with view."""
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
        # Should send a thinking message first
        interaction.followup.send.assert_called_once()
        thinking_kwargs = interaction.followup.send.call_args
        assert thinking_kwargs.kwargs.get("ephemeral") is True
        assert "reviewing" in str(thinking_kwargs.args[0]).lower()
        # The thinking message gets edited with the interpretation embed + view
        thinking_msg = interaction.followup.send.return_value
        thinking_msg.edit.assert_called_once()
        edit_kwargs = thinking_msg.edit.call_args
        embed = edit_kwargs.kwargs.get("embed")
        assert embed is not None
        assert "Interpretation" in embed.title
        view = edit_kwargs.kwargs.get("view")
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

    async def test_propose_exceeds_proposals_per_window(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Proposing beyond proposals_per_window limit returns an error."""
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        # Simulate submitting proposals up to the limit (default proposals_per_window=3)
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        async with get_session(engine) as session:
            repo = Repository(session)
            for i in range(3):
                await repo.append_event(
                    event_type="proposal.submitted",
                    aggregate_id=f"prop-{i}",
                    aggregate_type="proposal",
                    season_id=gov_data["season_id"],
                    governor_id=gov_data["player_id"],
                    payload={
                        "id": f"prop-{i}",
                        "raw_text": f"proposal {i}",
                    },
                )
            await session.commit()

        await bot._handle_propose(interaction, "One more proposal")
        interaction.response.defer.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        msg = str(call_kwargs.args[0])
        assert "maximum" in msg.lower() or "reached" in msg.lower()
        assert "3" in msg
        assert call_kwargs.kwargs.get("ephemeral") is True
        await engine.dispose()

    async def test_propose_cooldown_blocks_rapid_submission(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """A second proposal within PROPOSAL_COOLDOWN_SECONDS is rejected."""
        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        # First proposal should succeed (thinking msg + edit with interpret view)
        await bot._handle_propose(
            interaction,
            "Make three-pointers worth 5 points",
        )
        interaction.followup.send.assert_called_once()
        thinking_msg = interaction.followup.send.return_value
        # First call should edit the thinking msg with the interpretation embed
        thinking_msg.edit.assert_called_once()
        edit_kwargs = thinking_msg.edit.call_args
        assert edit_kwargs.kwargs.get("embed") is not None

        # Second proposal immediately should be blocked by cooldown
        interaction2 = make_interaction(
            user_id=interaction.user.id,
            display_name="TestGovernor",
            display_avatar_url="https://example.com/a.png",
        )
        interaction2.response.is_done = MagicMock(return_value=False)
        interaction2.guild = MagicMock(spec=discord.Guild)
        interaction2.guild.roles = []

        await bot._handle_propose(interaction2, "Another proposal")
        interaction2.response.defer.assert_called_once()
        call_kwargs2 = interaction2.followup.send.call_args
        assert call_kwargs2.kwargs.get("ephemeral") is True
        msg = str(call_kwargs2.args[0])
        assert "wait" in msg.lower()
        assert "second" in msg.lower()
        await engine.dispose()

    async def test_propose_cooldown_expires(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """After cooldown expires, a new proposal is accepted."""
        from pinwheel.discord.bot import PROPOSAL_COOLDOWN_SECONDS

        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        # Manually set a cooldown that is already expired
        from pinwheel.discord.helpers import get_governor

        gov = await get_governor(engine, str(interaction.user.id))
        bot._proposal_cooldowns[gov.player_id] = time.monotonic() - PROPOSAL_COOLDOWN_SECONDS - 1

        await bot._handle_propose(
            interaction,
            "Make three-pointers worth 5 points",
        )
        # Should succeed (thinking msg + edit with interpret view, not cooldown error)
        thinking_msg = interaction.followup.send.return_value
        thinking_msg.edit.assert_called_once()
        edit_kwargs = thinking_msg.edit.call_args
        assert edit_kwargs.kwargs.get("embed") is not None
        await engine.dispose()

    async def test_propose_spends_token_at_propose_time(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Token is spent immediately at /propose time, before confirm UI."""
        from pinwheel.core.tokens import get_token_balance
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        await bot._handle_propose(
            interaction,
            "Make three-pointers worth 5 points",
        )

        # Check that the token was already spent (before confirm)
        async with get_session(engine) as session:
            repo = Repository(session)
            balance = await get_token_balance(
                repo,
                gov_data["player_id"],
                gov_data["season_id"],
            )
        # Started with 2, spent 1 at propose-time
        assert balance.propose == 1

        # Verify the view has token_already_spent=True
        thinking_msg = interaction.followup.send.return_value
        edit_kwargs = thinking_msg.edit.call_args
        view = edit_kwargs.kwargs.get("view")
        assert view is not None
        assert view.token_already_spent is True
        await engine.dispose()

    async def test_propose_cancel_refunds_token(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Cancelling after propose refunds the token spent at propose-time."""
        from pinwheel.core.tokens import get_token_balance
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        await bot._handle_propose(
            interaction,
            "Make three-pointers worth 5 points",
        )

        # Get the view from the thinking message edit
        thinking_msg = interaction.followup.send.return_value
        edit_kwargs = thinking_msg.edit.call_args
        view = edit_kwargs.kwargs.get("view")
        assert view is not None

        # Simulate cancel button press via the button callback
        cancel_interaction = make_interaction(
            user_id=interaction.user.id,
            display_name="TestGovernor",
        )
        await view.cancel.callback(cancel_interaction)

        # Token should be refunded
        async with get_session(engine) as session:
            repo = Repository(session)
            balance = await get_token_balance(
                repo,
                gov_data["player_id"],
                gov_data["season_id"],
            )
        assert balance.propose == 2  # Refunded back to original
        await engine.dispose()

    async def test_propose_confirm_does_not_double_spend(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Confirming after propose does not spend the token twice."""
        from pinwheel.core.tokens import get_token_balance
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        bot, interaction, gov_data, engine = await _make_enrolled_bot_and_interaction(
            settings_discord_enabled,
            event_bus,
        )

        # Clear cooldown so we can call propose
        await bot._handle_propose(
            interaction,
            "Make three-pointers worth 5 points",
        )

        thinking_msg = interaction.followup.send.return_value
        edit_kwargs = thinking_msg.edit.call_args
        view = edit_kwargs.kwargs.get("view")
        assert view is not None

        # Simulate confirm button press via the button callback
        confirm_interaction = make_interaction(
            user_id=interaction.user.id,
            display_name="TestGovernor",
        )
        confirm_interaction.channel = AsyncMock()
        await view.confirm.callback(confirm_interaction)

        # Token should have been spent only once (at propose-time)
        async with get_session(engine) as session:
            repo = Repository(session)
            balance = await get_token_balance(
                repo,
                gov_data["player_id"],
                gov_data["season_id"],
            )
        # Started with 2, spent 1 (only once) → 1 remaining
        assert balance.propose == 1
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
                    "season_id": gov_data["season_id"],
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
                    "season_id": gov_data["season_id"],
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
        assert "isn't enrolled" in str(call_kwargs.args[0]).lower()
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


class TestServerWelcomeEmbed:
    """First-touch DM sent when someone joins the Discord server."""

    def test_server_welcome_embed_content(self) -> None:
        embed = build_server_welcome_embed()
        assert embed.title == "Welcome to Pinwheel Fates!"
        desc = embed.description or ""
        assert "/join" in desc
        assert "/propose" in desc
        assert "basketball" in desc
        assert "whatever you want" in desc
        assert embed.footer.text is not None
        assert "Pinwheel Fates" in embed.footer.text

    def test_server_welcome_embed_color(self) -> None:
        embed = build_server_welcome_embed()
        assert embed.color == discord.Color.gold()


class TestOnMemberJoin:
    """Bot sends a server welcome DM when a new member joins."""

    async def test_sends_welcome_dm_to_human(self) -> None:
        settings = Settings(
            pinwheel_env="production",
            database_url="sqlite+aiosqlite:///:memory:",
            discord_bot_token="tok",
            discord_enabled=True,
            session_secret_key="test-secret",
        )
        bot = PinwheelBot(settings=settings, event_bus=EventBus())
        member = MagicMock(spec=discord.Member)
        member.bot = False
        member.display_name = "NewPlayer"
        member.send = AsyncMock()

        await bot.on_member_join(member)

        member.send.assert_called_once()
        embed = member.send.call_args[1].get("embed") or member.send.call_args[0][0]
        assert "Pinwheel Fates" in embed.title

    async def test_skips_bots(self) -> None:
        settings = Settings(
            pinwheel_env="production",
            database_url="sqlite+aiosqlite:///:memory:",
            discord_bot_token="tok",
            discord_enabled=True,
            session_secret_key="test-secret",
        )
        bot = PinwheelBot(settings=settings, event_bus=EventBus())
        member = MagicMock(spec=discord.Member)
        member.bot = True
        member.send = AsyncMock()

        await bot.on_member_join(member)

        member.send.assert_not_called()

    async def test_handles_dm_forbidden_gracefully(self) -> None:
        settings = Settings(
            pinwheel_env="production",
            database_url="sqlite+aiosqlite:///:memory:",
            discord_bot_token="tok",
            discord_enabled=True,
            session_secret_key="test-secret",
        )
        bot = PinwheelBot(settings=settings, event_bus=EventBus())
        member = MagicMock(spec=discord.Member)
        member.bot = False
        member.display_name = "PrivateUser"
        member.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "Cannot send"))

        # Should not raise
        await bot.on_member_join(member)


class TestWelcomeEmbedExtended:
    """Welcome embed: schema, sentinels, and season context behavior."""

    def test_welcome_embed_core_content(self) -> None:
        """Welcome embed includes team, hoopers, commands, tokens, and play link."""
        hoopers = [
            {"name": "Briar Ashwood", "archetype": "Sharpshooter"},
            {"name": "Kai Rivers", "archetype": "Playmaker"},
        ]
        embed = build_welcome_embed(
            "Rose City Thorns",
            "#E94560",
            hoopers,
            motto="Bloom or bust",
        )
        desc = embed.description or ""
        # Team and color
        assert "Rose City Thorns" in desc
        assert embed.color == discord.Color(0xE94560)
        # Motto
        assert "Bloom or bust" in desc
        # Hooper names and archetypes
        assert "Briar Ashwood" in desc
        assert "Sharpshooter" in desc
        assert "Kai Rivers" in desc
        assert "Playmaker" in desc
        # Key commands
        for cmd in ("/propose", "/vote", "/strategy", "/tokens", "/standings", "/play"):
            assert cmd in desc
        # Starter tokens
        assert "2 PROPOSE" in desc
        assert "2 AMEND" in desc
        assert "2 BOOST" in desc

    def test_welcome_embed_motto_omitted_when_empty(self) -> None:
        """Welcome embed omits motto line when empty."""
        hoopers = [{"name": "Star", "archetype": "Sharpshooter"}]
        embed = build_welcome_embed("Thorns", "#E74C3C", hoopers, motto="")
        assert '*"' not in (embed.description or "")

    def test_welcome_embed_backstory_truncation(self) -> None:
        """Backstory longer than 100 chars is truncated with ellipsis."""
        long_bio = "A" * 150
        hoopers = [
            {"name": "Star", "archetype": "Sharpshooter", "backstory": long_bio},
        ]
        embed = build_welcome_embed("Thorns", "#E74C3C", hoopers)
        desc = embed.description or ""
        assert "..." in desc
        assert long_bio not in desc

    @pytest.mark.parametrize(
        "phase, current_round, total_rounds, expected_labels, absent_labels",
        [
            ("active", 2, 9, ["Season X", "Regular season", "Round 2 of 9"], []),
            ("playoffs", 7, 6, ["Playoffs"], []),
            ("active", 0, 9, ["Season X", "Regular season"], ["Round 0"]),
        ],
        ids=["active-mid-season", "playoffs", "active-no-games"],
    )
    def test_welcome_embed_season_context_phases(
        self,
        phase: str,
        current_round: int,
        total_rounds: int,
        expected_labels: list[str],
        absent_labels: list[str],
    ) -> None:
        """Season context renders correct phase labels and round info."""
        hoopers = [{"name": "Briar", "archetype": "Sharpshooter"}]
        ctx = {
            "season_name": "Season X",
            "season_phase": phase,
            "current_round": current_round,
            "total_rounds": total_rounds,
        }
        embed = build_welcome_embed(
            "Rose City Thorns",
            "#E74C3C",
            hoopers,
            season_context=ctx,
        )
        desc = embed.description or ""
        for label in expected_labels:
            assert label in desc
        for label in absent_labels:
            assert label not in desc

    def test_welcome_embed_without_season_context(self) -> None:
        """Welcome embed works without season context (backward compat)."""
        hoopers = [{"name": "Briar", "archetype": "Sharpshooter"}]
        embed = build_welcome_embed("Rose City Thorns", "#E74C3C", hoopers)
        desc = embed.description or ""
        assert "Rose City Thorns" in desc
        assert "/propose" in desc
        assert "Regular season" not in desc

    def test_welcome_embed_season_context_all_phases(self) -> None:
        """Each season phase produces an appropriate label."""
        from pinwheel.discord.embeds import _PHASE_LABELS

        hoopers = [{"name": "Star", "archetype": "Sharpshooter"}]
        for phase_value, expected_label in _PHASE_LABELS.items():
            ctx = {
                "season_name": "Test",
                "season_phase": phase_value,
                "current_round": 1,
                "total_rounds": 9,
            }
            embed = build_welcome_embed(
                "Thorns",
                "#E74C3C",
                hoopers,
                season_context=ctx,
            )
            desc = embed.description or ""
            assert expected_label in desc, (
                f"Phase '{phase_value}' should produce label '{expected_label}'"
            )


# ---------------------------------------------------------------------------
# Welcome embed -- _gather_season_context integration
# ---------------------------------------------------------------------------


class TestGatherSeasonContext:
    """Test the _gather_season_context helper used by _handle_join."""

    async def test_gather_season_context_with_games(self) -> None:
        """Context includes current round and total rounds from DB."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository
        from pinwheel.discord.bot import _gather_season_context

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test")
            season = await repo.create_season(league.id, "Season TWO")
            season.status = "active"
            await session.flush()
            team_a = await repo.create_team(season.id, "Thorns", color="#e94560")
            team_b = await repo.create_team(season.id, "Breakers", color="#53d8fb")

            # Create schedule (3 rounds)
            for rn in range(1, 4):
                await repo.create_schedule_entry(
                    season.id,
                    rn,
                    0,
                    team_a.id,
                    team_b.id,
                    phase="regular",
                )

            # Create game results for 2 rounds
            for rn in range(1, 3):
                await repo.store_game_result(
                    season_id=season.id,
                    round_number=rn,
                    matchup_index=0,
                    home_team_id=team_a.id,
                    away_team_id=team_b.id,
                    home_score=50,
                    away_score=45,
                    winner_team_id=team_a.id,
                    seed=42,
                    total_possessions=80,
                )

            await session.commit()

            # Re-fetch season to pick up updated status
            season = await repo.get_active_season()
            assert season is not None
            ctx = await _gather_season_context(repo, season)

        assert ctx["season_name"] == "Season TWO"
        assert ctx["season_phase"] == "active"
        assert ctx["current_round"] == 2
        assert ctx["total_rounds"] == 3

        await engine.dispose()

    async def test_gather_season_context_no_games(self) -> None:
        """Context handles season with no games yet."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository
        from pinwheel.discord.bot import _gather_season_context

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test")
            season = await repo.create_season(league.id, "Season ONE")
            await repo.update_season_status(season.id, "active")
            team_a = await repo.create_team(season.id, "Thorns", color="#e94560")
            team_b = await repo.create_team(season.id, "Breakers", color="#53d8fb")

            # Create schedule but no game results
            for rn in range(1, 10):
                await repo.create_schedule_entry(
                    season.id,
                    rn,
                    0,
                    team_a.id,
                    team_b.id,
                    phase="regular",
                )
            await session.commit()

            # Re-fetch season to pick up updated status
            season = await repo.get_active_season()
            assert season is not None
            ctx = await _gather_season_context(repo, season)

        assert ctx["season_name"] == "Season ONE"
        assert ctx["current_round"] == 0
        assert ctx["total_rounds"] == 9

        await engine.dispose()

    async def test_gather_season_context_playoff_phase(self) -> None:
        """Context correctly reports the season phase."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository
        from pinwheel.discord.bot import _gather_season_context

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test")
            season = await repo.create_season(league.id, "Season TWO")
            await repo.update_season_status(season.id, "playoffs")
            await session.commit()

            # Refresh to pick up updated status
            season = await repo.get_active_season()
            assert season is not None
            ctx = await _gather_season_context(repo, season)

        assert ctx["season_phase"] == "playoffs"

        await engine.dispose()


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
        guild.fetch_channels = AsyncMock(return_value=[category, ch_how, ch_play, ch_big])
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
        # fetch_channels returns category only — no text channels exist
        guild.fetch_channels = AsyncMock(return_value=[category])
        guild.roles = []
        guild.me = MagicMock()
        guild.default_role = MagicMock()
        guild.create_category = AsyncMock()

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
        guild.roles = []
        guild.me = MagicMock()
        guild.default_role = MagicMock()

        category = MagicMock(spec=discord.CategoryChannel)
        category.name = "PINWHEEL FATES"
        guild.create_category = AsyncMock(return_value=category)

        # First setup: nothing exists yet
        guild.fetch_channels = AsyncMock(return_value=[])

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

        # --- Second setup: fetch_channels now returns existing channels ---
        guild.fetch_channels = AsyncMock(return_value=[category, *created_channels.values()])
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
        guild.fetch_channels = AsyncMock(return_value=[])
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
                season.id,
                "Burnside Breakers",
                color="#53d8fb",
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
                season.id,
                "Rose City Thorns",
                color="#e94560",
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
            league = await repo.create_league("League 1")
            season = await repo.create_season(league.id, "Season 1")
            team = await repo.create_team(season.id, "Testers")
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

        with pytest.raises(GovernorNotFound, match="(?i)no active season"):
            await get_governor(engine, "99999")

        await engine.dispose()


class TestProposalsEmbed:
    """Tests for proposal listing and profile embed enhancements."""

    def test_proposals_embed_empty(self) -> None:
        from pinwheel.discord.embeds import build_proposals_embed

        embed = build_proposals_embed([], season_name="Season 1")
        assert "No proposals" in embed.description

    def test_proposals_embed_with_data(self) -> None:
        from pinwheel.discord.embeds import build_proposals_embed

        proposals = [
            {
                "id": "p1",
                "raw_text": "Make three-pointers worth 5 points",
                "status": "confirmed",
                "governor_id": "gov1",
                "team_id": "t1",
                "parameter": "three_point_value",
                "tier": 1,
                "round_number": 3,
            },
            {
                "id": "p2",
                "raw_text": "The first team to 69 wins",
                "status": "pending_review",
                "governor_id": "gov2",
                "team_id": "t2",
                "parameter": None,
                "tier": 5,
                "round_number": 5,
            },
        ]
        governor_names = {"gov1": "Alice", "gov2": "Bob"}
        embed = build_proposals_embed(
            proposals,
            season_name="Season 1",
            governor_names=governor_names,
        )
        assert embed.title == "Proposals -- Season 1"
        assert len(embed.fields) == 2
        assert "On the Floor" in embed.fields[0].value
        assert "Awaiting Admin Review" in embed.fields[1].value
        assert "Alice" in embed.fields[0].value
        assert "Bob" in embed.fields[1].value

    def test_profile_embed_shows_proposal_details(self) -> None:
        from pinwheel.discord.embeds import build_governor_profile_embed
        from pinwheel.models.tokens import TokenBalance

        activity = {
            "proposals_submitted": 1,
            "proposals_passed": 0,
            "proposals_failed": 0,
            "votes_cast": 2,
            "token_balance": TokenBalance(governor_id="g1", propose=1, amend=1, boost=1),
            "proposal_list": [
                {
                    "id": "p1",
                    "raw_text": "Test proposal text",
                    "status": "pending_review",
                    "parameter": None,
                    "tier": 5,
                }
            ],
        }
        embed = build_governor_profile_embed(
            governor_name="JudgeJedd",
            team_name="Thorns",
            activity=activity,
        )
        # Should have proposal detail field
        field_values = [f.value for f in embed.fields]
        assert any("Awaiting Admin Review" in v for v in field_values)


# ---------------------------------------------------------------------------
# /edit-series command
# ---------------------------------------------------------------------------


class TestEditSeriesCommand:
    """Tests for /edit-series auth and flow."""

    @pytest.fixture
    def bot(self, settings_discord_enabled: Settings, event_bus: EventBus) -> PinwheelBot:
        return PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus)

    async def test_edit_series_registered(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> None:
        bot = PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus)
        command_names = [cmd.name for cmd in bot.tree.get_commands()]
        assert "edit-series" in command_names

    async def test_edit_series_no_engine(self, bot: PinwheelBot) -> None:
        """Without an engine, edit-series fails gracefully."""
        interaction = make_interaction(user_id=111222333)
        await bot._handle_edit_series(interaction, "some-report-id")
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True

    async def test_edit_series_not_enrolled(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> None:
        """A user not enrolled as a governor gets rejected."""
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
            team = await repo.create_team(season.id, "Thorns", color="#e94560")
            # Store a series report
            report = await repo.store_report(
                season_id=season.id,
                report_type="series",
                round_number=0,
                content="The Thorns advanced.",
                team_id=team.id,
                metadata_json={
                    "series_type": "semifinal",
                    "winner_id": team.id,
                    "loser_id": "other-team",
                    "record": "2-1",
                },
            )
            report_id = report.id
            await session.commit()

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )
        interaction = make_interaction(user_id=999888777)
        await bot._handle_edit_series(interaction, report_id)
        # Not enrolled → ephemeral rejection
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "join" in call_kwargs.args[0].lower()

        await engine.dispose()

    async def test_edit_series_non_participating_governor_rejected(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> None:
        """A governor on a non-participating team is rejected."""
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
            await repo.update_season_status(season.id, "active")
            team_winner = await repo.create_team(season.id, "Thorns")
            team_loser = await repo.create_team(season.id, "Breakers")
            team_other = await repo.create_team(season.id, "Outsiders")
            # Enroll governor on the non-participating team
            player = await repo.get_or_create_player(
                discord_id="444555666",
                username="OutsiderGov",
            )
            await repo.enroll_player(player.id, team_other.id, season.id)
            # Store a series report between winner and loser
            report = await repo.store_report(
                season_id=season.id,
                report_type="series",
                round_number=0,
                content="Thorns beat Breakers.",
                team_id=team_winner.id,
                metadata_json={
                    "series_type": "semifinal",
                    "winner_id": team_winner.id,
                    "loser_id": team_loser.id,
                    "record": "2-0",
                },
            )
            report_id = report.id
            await session.commit()

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )
        interaction = make_interaction(user_id=444555666)
        await bot._handle_edit_series(interaction, report_id)
        # Non-participating → ephemeral rejection
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "participating" in call_kwargs.args[0].lower()

        await engine.dispose()

    async def test_edit_series_participating_governor_opens_modal(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> None:
        """A governor on the winning team can open the edit modal."""
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
            await repo.update_season_status(season.id, "active")
            team_winner = await repo.create_team(season.id, "Thorns")
            team_loser = await repo.create_team(season.id, "Breakers")
            player = await repo.get_or_create_player(
                discord_id="111222333",
                username="ThornsGov",
            )
            await repo.enroll_player(player.id, team_winner.id, season.id)
            report = await repo.store_report(
                season_id=season.id,
                report_type="series",
                round_number=0,
                content="Thorns beat Breakers in a tight series.",
                team_id=team_winner.id,
                metadata_json={
                    "series_type": "semifinal",
                    "winner_id": team_winner.id,
                    "loser_id": team_loser.id,
                    "record": "2-1",
                },
            )
            report_id = report.id
            await session.commit()

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )
        interaction = make_interaction(user_id=111222333)
        await bot._handle_edit_series(interaction, report_id)
        # Should open a modal
        interaction.response.send_modal.assert_called_once()
        modal = interaction.response.send_modal.call_args.args[0]
        assert modal.report_id == report_id
        assert modal.winner_name == "Thorns"
        assert modal.loser_name == "Breakers"

        await engine.dispose()

    async def test_edit_series_loser_governor_can_also_edit(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> None:
        """A governor on the losing team can also open the edit modal."""
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
            await repo.update_season_status(season.id, "active")
            team_winner = await repo.create_team(season.id, "Thorns")
            team_loser = await repo.create_team(season.id, "Breakers")
            player = await repo.get_or_create_player(
                discord_id="777888999",
                username="BreakersGov",
            )
            await repo.enroll_player(player.id, team_loser.id, season.id)
            report = await repo.store_report(
                season_id=season.id,
                report_type="series",
                round_number=0,
                content="Thorns won the series.",
                team_id=team_winner.id,
                metadata_json={
                    "series_type": "finals",
                    "winner_id": team_winner.id,
                    "loser_id": team_loser.id,
                    "record": "3-2",
                },
            )
            report_id = report.id
            await session.commit()

        bot = PinwheelBot(
            settings=settings_discord_enabled,
            event_bus=event_bus,
            engine=engine,
        )
        interaction = make_interaction(user_id=777888999)
        await bot._handle_edit_series(interaction, report_id)
        # Loser-team governor should also get the modal
        interaction.response.send_modal.assert_called_once()

        await engine.dispose()


# ---------------------------------------------------------------------------
# EditSeriesModal
# ---------------------------------------------------------------------------


class TestEditSeriesModal:
    """Tests for the EditSeriesModal view."""

    async def test_modal_saves_content_and_appends_event(self) -> None:
        """on_submit saves updated content and appends report.edited event."""
        from pinwheel.db.engine import create_engine, get_session
        from pinwheel.db.models import Base
        from pinwheel.db.repository import Repository
        from pinwheel.discord.views import EditSeriesModal

        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with get_session(engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test League")
            season = await repo.create_season(league.id, "Season 1")
            await repo.update_season_status(season.id, "active")
            team = await repo.create_team(season.id, "Thorns")
            report = await repo.store_report(
                season_id=season.id,
                report_type="series",
                round_number=0,
                content="Original content.",
                team_id=team.id,
                metadata_json={
                    "series_type": "semifinal",
                    "winner_id": team.id,
                    "loser_id": "other-team",
                    "record": "2-0",
                },
            )
            report_id = report.id
            season_id = season.id
            await session.commit()

        modal = EditSeriesModal(
            report_id=report_id,
            season_id=season_id,
            series_type="semifinal",
            winner_name="Thorns",
            loser_name="Breakers",
            current_content="Original content.",
            engine=engine,
        )

        # Simulate user typing new content
        modal.report_content._value = "Revised series report with more drama."

        interaction = make_interaction(user_id=111222333, display_name="TestEditor")
        await modal.on_submit(interaction)

        # Verify content was saved
        async with get_session(engine) as session:
            repo = Repository(session)
            updated = await session.get(
                __import__("pinwheel.db.models", fromlist=["ReportRow"]).ReportRow,
                report_id,
            )
            assert updated.content == "Revised series report with more drama."

            # Verify report.edited event was appended
            events = await repo.get_events_for_aggregate("report", report_id)
            edited_events = [e for e in events if e.event_type == "report.edited"]
            assert len(edited_events) == 1
            assert edited_events[0].payload["editor_discord_id"] == "111222333"
            assert edited_events[0].payload["series_type"] == "semifinal"

        # Verify interaction response was sent
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True

        await engine.dispose()


# ---------------------------------------------------------------------------
# Smart Game Result Embeds — Feature 4
# ---------------------------------------------------------------------------


class TestTeamGameContext:
    """Tests for TeamGameContext and GameContext dataclasses."""

    def test_defaults(self) -> None:
        from pinwheel.discord.embeds import GameContext

        ctx = GameContext()
        assert ctx.home.streak == 0
        assert ctx.away.streak == 0
        assert ctx.home.standing_position is None
        assert ctx.home.standing_movement is None
        assert ctx.margin_label == ""
        assert ctx.new_rules == []

    def test_team_game_context_frozen(self) -> None:
        from pinwheel.discord.embeds import TeamGameContext

        tc = TeamGameContext(streak=5, standing_position=1, standing_movement=2)
        assert tc.streak == 5
        assert tc.standing_position == 1
        assert tc.standing_movement == 2

    def test_game_context_with_values(self) -> None:
        from pinwheel.discord.embeds import GameContext, TeamGameContext

        ctx = GameContext(
            home=TeamGameContext(streak=7, standing_position=1, standing_movement=2),
            away=TeamGameContext(streak=-3, standing_position=4, standing_movement=-1),
            margin_label="Closest game of the season",
            new_rules=["3-point range extended"],
        )
        assert ctx.home.streak == 7
        assert ctx.away.streak == -3
        assert ctx.margin_label == "Closest game of the season"
        assert len(ctx.new_rules) == 1


class TestFormatStreak:
    """Tests for the _format_streak helper."""

    def test_win_streak(self) -> None:
        from pinwheel.discord.embeds import _format_streak

        assert _format_streak(3) == "W3"
        assert _format_streak(7) == "W7"
        assert _format_streak(1) == "W1"

    def test_loss_streak(self) -> None:
        from pinwheel.discord.embeds import _format_streak

        assert _format_streak(-2) == "L2"
        assert _format_streak(-5) == "L5"

    def test_no_streak(self) -> None:
        from pinwheel.discord.embeds import _format_streak

        assert _format_streak(0) == ""


class TestOrdinalSuffix:
    """Tests for the _ordinal_suffix helper."""

    def test_ordinals(self) -> None:
        from pinwheel.discord.embeds import _ordinal_suffix

        assert _ordinal_suffix(1) == "st"
        assert _ordinal_suffix(2) == "nd"
        assert _ordinal_suffix(3) == "rd"
        assert _ordinal_suffix(4) == "th"
        assert _ordinal_suffix(11) == "th"
        assert _ordinal_suffix(12) == "th"
        assert _ordinal_suffix(13) == "th"
        assert _ordinal_suffix(21) == "st"
        assert _ordinal_suffix(22) == "nd"
        assert _ordinal_suffix(23) == "rd"


class TestFormatStandingMovement:
    """Tests for the _format_standing_movement helper."""

    def test_moved_up(self) -> None:
        from pinwheel.discord.embeds import _format_standing_movement

        assert _format_standing_movement(1, 2) == "moved to 1st"
        assert _format_standing_movement(2, 1) == "moved to 2nd"

    def test_dropped(self) -> None:
        from pinwheel.discord.embeds import _format_standing_movement

        assert _format_standing_movement(4, -2) == "dropped to 4th"
        assert _format_standing_movement(3, -1) == "dropped to 3rd"

    def test_no_movement(self) -> None:
        from pinwheel.discord.embeds import _format_standing_movement

        assert _format_standing_movement(2, 0) == ""

    def test_none_position(self) -> None:
        from pinwheel.discord.embeds import _format_standing_movement

        assert _format_standing_movement(None, 2) == ""

    def test_none_movement(self) -> None:
        from pinwheel.discord.embeds import _format_standing_movement

        assert _format_standing_movement(1, None) == ""


class TestComputeTeamStreak:
    """Tests for the _compute_team_streak helper."""

    def test_win_streak(self) -> None:
        from pinwheel.discord.embeds import _compute_team_streak

        games = [
            {
                "home_team_id": "t1",
                "away_team_id": "t2",
                "winner_team_id": "t1",
                "home_score": 50,
                "away_score": 40,
                "round_number": 1,
                "matchup_index": 0,
            },
            {
                "home_team_id": "t1",
                "away_team_id": "t3",
                "winner_team_id": "t1",
                "home_score": 55,
                "away_score": 45,
                "round_number": 2,
                "matchup_index": 0,
            },
            {
                "home_team_id": "t1",
                "away_team_id": "t4",
                "winner_team_id": "t1",
                "home_score": 60,
                "away_score": 50,
                "round_number": 3,
                "matchup_index": 0,
            },
        ]
        assert _compute_team_streak("t1", games) == 3

    def test_loss_streak(self) -> None:
        from pinwheel.discord.embeds import _compute_team_streak

        games = [
            {
                "home_team_id": "t1",
                "away_team_id": "t2",
                "winner_team_id": "t2",
                "home_score": 40,
                "away_score": 50,
                "round_number": 1,
                "matchup_index": 0,
            },
            {
                "home_team_id": "t1",
                "away_team_id": "t3",
                "winner_team_id": "t3",
                "home_score": 35,
                "away_score": 55,
                "round_number": 2,
                "matchup_index": 0,
            },
        ]
        assert _compute_team_streak("t1", games) == -2

    def test_streak_resets_on_reversal(self) -> None:
        from pinwheel.discord.embeds import _compute_team_streak

        games = [
            {
                "home_team_id": "t1",
                "away_team_id": "t2",
                "winner_team_id": "t1",
                "home_score": 50,
                "away_score": 40,
                "round_number": 1,
                "matchup_index": 0,
            },
            {
                "home_team_id": "t1",
                "away_team_id": "t3",
                "winner_team_id": "t3",
                "home_score": 40,
                "away_score": 50,
                "round_number": 2,
                "matchup_index": 0,
            },
            {
                "home_team_id": "t1",
                "away_team_id": "t4",
                "winner_team_id": "t1",
                "home_score": 55,
                "away_score": 45,
                "round_number": 3,
                "matchup_index": 0,
            },
        ]
        assert _compute_team_streak("t1", games) == 1

    def test_empty_games(self) -> None:
        from pinwheel.discord.embeds import _compute_team_streak

        assert _compute_team_streak("t1", []) == 0

    def test_no_matching_team(self) -> None:
        from pinwheel.discord.embeds import _compute_team_streak

        games = [
            {
                "home_team_id": "t2",
                "away_team_id": "t3",
                "winner_team_id": "t2",
                "home_score": 50,
                "away_score": 40,
                "round_number": 1,
                "matchup_index": 0,
            },
        ]
        assert _compute_team_streak("t1", games) == 0

    def test_away_team_streak(self) -> None:
        from pinwheel.discord.embeds import _compute_team_streak

        games = [
            {
                "home_team_id": "t2",
                "away_team_id": "t1",
                "winner_team_id": "t1",
                "home_score": 40,
                "away_score": 55,
                "round_number": 1,
                "matchup_index": 0,
            },
            {
                "home_team_id": "t3",
                "away_team_id": "t1",
                "winner_team_id": "t1",
                "home_score": 35,
                "away_score": 60,
                "round_number": 2,
                "matchup_index": 0,
            },
        ]
        assert _compute_team_streak("t1", games) == 2


class TestFindStandingPosition:
    """Tests for _find_standing_position helper."""

    def test_finds_position(self) -> None:
        from pinwheel.discord.embeds import _find_standing_position

        standings = [
            {"team_id": "t1", "wins": 5},
            {"team_id": "t2", "wins": 3},
            {"team_id": "t3", "wins": 1},
        ]
        assert _find_standing_position("t1", standings) == 1
        assert _find_standing_position("t2", standings) == 2
        assert _find_standing_position("t3", standings) == 3

    def test_not_found(self) -> None:
        from pinwheel.discord.embeds import _find_standing_position

        standings = [{"team_id": "t1", "wins": 5}]
        assert _find_standing_position("t999", standings) is None

    def test_none_standings(self) -> None:
        from pinwheel.discord.embeds import _find_standing_position

        assert _find_standing_position("t1", None) is None

    def test_empty_standings(self) -> None:
        from pinwheel.discord.embeds import _find_standing_position

        assert _find_standing_position("t1", []) is None


class TestComputeMarginLabel:
    """Tests for _compute_margin_label helper."""

    def test_closest_game(self) -> None:
        from pinwheel.discord.embeds import _compute_margin_label

        game = {"home_score": 50, "away_score": 49}
        all_games = [
            {"home_score": 60, "away_score": 40},
            {"home_score": 55, "away_score": 45},
            {"home_score": 50, "away_score": 49},  # margin=1, the smallest
        ]
        assert _compute_margin_label(game, all_games) == "Closest game of the season"

    def test_biggest_blowout(self) -> None:
        from pinwheel.discord.embeds import _compute_margin_label

        game = {"home_score": 80, "away_score": 40}
        all_games = [
            {"home_score": 50, "away_score": 48},
            {"home_score": 55, "away_score": 50},
            {"home_score": 80, "away_score": 40},  # margin=40, the biggest
        ]
        assert _compute_margin_label(game, all_games) == "Biggest blowout of the season"

    def test_unremarkable_margin(self) -> None:
        from pinwheel.discord.embeds import _compute_margin_label

        game = {"home_score": 55, "away_score": 50}
        all_games = [
            {"home_score": 50, "away_score": 48},
            {"home_score": 55, "away_score": 50},
            {"home_score": 60, "away_score": 40},
        ]
        assert _compute_margin_label(game, all_games) == ""

    def test_single_game_no_label(self) -> None:
        from pinwheel.discord.embeds import _compute_margin_label

        game = {"home_score": 55, "away_score": 50}
        assert _compute_margin_label(game, [game]) == ""

    def test_all_same_margin_no_label(self) -> None:
        from pinwheel.discord.embeds import _compute_margin_label

        game = {"home_score": 55, "away_score": 50}
        all_games = [
            {"home_score": 55, "away_score": 50},
            {"home_score": 60, "away_score": 55},
            {"home_score": 45, "away_score": 40},
        ]
        # All margins are 5 — min == max, so no label
        assert _compute_margin_label(game, all_games) == ""


class TestComputeGameContext:
    """Tests for the compute_game_context function."""

    def test_full_context(self) -> None:
        from pinwheel.discord.embeds import compute_game_context

        game = {
            "home_team_id": "t1",
            "away_team_id": "t2",
            "home_score": 55,
            "away_score": 48,
            "winner_team_id": "t1",
            "round_number": 3,
            "matchup_index": 0,
        }
        all_games = [
            {
                "home_team_id": "t1",
                "away_team_id": "t2",
                "winner_team_id": "t1",
                "home_score": 50,
                "away_score": 40,
                "round_number": 1,
                "matchup_index": 0,
            },
            {
                "home_team_id": "t1",
                "away_team_id": "t3",
                "winner_team_id": "t1",
                "home_score": 55,
                "away_score": 45,
                "round_number": 2,
                "matchup_index": 0,
            },
            game,
        ]
        standings_before = [{"team_id": "t2"}, {"team_id": "t1"}, {"team_id": "t3"}]
        standings_after = [{"team_id": "t1"}, {"team_id": "t2"}, {"team_id": "t3"}]

        ctx = compute_game_context(
            game,
            all_games,
            standings_before=standings_before,
            standings_after=standings_after,
            new_rules=["Shot clock reduced to 20s"],
        )

        assert ctx.home.streak == 3  # t1 has 3 wins in a row
        assert ctx.home.standing_position == 1
        assert ctx.home.standing_movement == 1  # moved up from 2nd to 1st
        assert ctx.away.standing_position == 2
        assert ctx.away.standing_movement == -1  # dropped from 1st to 2nd
        assert len(ctx.new_rules) == 1
        assert ctx.new_rules[0] == "Shot clock reduced to 20s"

    def test_no_optional_data(self) -> None:
        from pinwheel.discord.embeds import compute_game_context

        game = {
            "home_team_id": "t1",
            "away_team_id": "t2",
            "home_score": 55,
            "away_score": 48,
            "winner_team_id": "t1",
            "round_number": 1,
            "matchup_index": 0,
        }
        ctx = compute_game_context(game, [game])
        assert ctx.home.streak == 1
        assert ctx.away.streak == -1
        assert ctx.home.standing_position is None
        assert ctx.margin_label == ""
        assert ctx.new_rules == []


class TestBuildGameResultEmbedWithContext:
    """Tests for enriched game result embeds with GameContext."""

    def test_streak_shown_in_description(self) -> None:
        from pinwheel.discord.embeds import GameContext, TeamGameContext

        data = {
            "home_team": "Thorns",
            "away_team": "Hammers",
            "home_score": 56,
            "away_score": 45,
            "total_possessions": 60,
        }
        ctx = GameContext(
            home=TeamGameContext(streak=7),
            away=TeamGameContext(streak=-3),
        )
        embed = build_game_result_embed(data, game_context=ctx)
        desc = embed.description or ""
        assert "Thorns W7" in desc
        assert "Hammers L3" in desc

    def test_standings_movement_shown(self) -> None:
        from pinwheel.discord.embeds import GameContext, TeamGameContext

        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 55,
            "away_score": 48,
            "total_possessions": 60,
        }
        ctx = GameContext(
            home=TeamGameContext(standing_position=1, standing_movement=2),
            away=TeamGameContext(standing_position=4, standing_movement=-1),
        )
        embed = build_game_result_embed(data, game_context=ctx)
        desc = embed.description or ""
        assert "Thorns moved to 1st" in desc
        assert "Breakers dropped to 4th" in desc

    def test_margin_label_shown(self) -> None:
        from pinwheel.discord.embeds import GameContext

        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 50,
            "away_score": 49,
            "total_possessions": 60,
        }
        ctx = GameContext(margin_label="Closest game of the season")
        embed = build_game_result_embed(data, game_context=ctx)
        desc = embed.description or ""
        assert "Closest game of the season" in desc

    def test_new_rules_shown(self) -> None:
        from pinwheel.discord.embeds import GameContext

        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 55,
            "away_score": 48,
            "total_possessions": 60,
        }
        ctx = GameContext(new_rules=["3-point range extended", "Shot clock 18s"])
        embed = build_game_result_embed(data, game_context=ctx)
        desc = embed.description or ""
        assert "First game under new rules" in desc
        assert "3-point range extended" in desc
        assert "Shot clock 18s" in desc

    def test_no_context_backward_compat(self) -> None:
        """Without game_context, the embed is identical to the old behavior."""
        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 55,
            "away_score": 48,
            "total_possessions": 60,
        }
        embed = build_game_result_embed(data)
        desc = embed.description or ""
        assert "**Thorns** 55 - 48 **Breakers**" in desc
        # No enrichment lines
        assert "W" not in desc
        assert "moved to" not in desc
        assert "Closest" not in desc
        assert "First game under" not in desc

    def test_zero_streaks_not_shown(self) -> None:
        from pinwheel.discord.embeds import GameContext, TeamGameContext

        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 55,
            "away_score": 48,
            "total_possessions": 60,
        }
        ctx = GameContext(
            home=TeamGameContext(streak=0),
            away=TeamGameContext(streak=0),
        )
        embed = build_game_result_embed(data, game_context=ctx)
        desc = embed.description or ""
        # No streak line should be added
        lines = desc.strip().split("\n")
        assert len(lines) == 1  # just the score line

    def test_playoff_context_plus_game_context(self) -> None:
        """Playoff context and game context both work together."""
        from pinwheel.discord.embeds import GameContext, TeamGameContext

        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_score": 55,
            "away_score": 48,
            "total_possessions": 60,
        }
        ctx = GameContext(
            home=TeamGameContext(streak=5),
            margin_label="Biggest blowout of the season",
        )
        embed = build_game_result_embed(data, playoff_context="finals", game_context=ctx)
        assert "CHAMPIONSHIP FINALS" in embed.title
        desc = embed.description or ""
        assert "Thorns W5" in desc
        assert "Biggest blowout of the season" in desc


class TestBuildTeamGameResultEmbedWithContext:
    """Tests for enriched team-specific game result embeds."""

    def test_streak_in_title(self) -> None:
        from pinwheel.discord.embeds import (
            GameContext,
            TeamGameContext,
            build_team_game_result_embed,
        )

        data = {
            "home_team": "Thorns",
            "away_team": "Hammers",
            "home_team_id": "t1",
            "away_team_id": "t2",
            "home_score": 56,
            "away_score": 45,
            "winner_team_id": "t1",
        }
        ctx = GameContext(
            home=TeamGameContext(streak=7, standing_position=1, standing_movement=2),
            away=TeamGameContext(streak=-3, standing_position=4, standing_movement=-1),
        )
        # Home team embed
        embed = build_team_game_result_embed(data, "t1", game_context=ctx)
        assert "(W7)" in embed.title
        assert "Victory" in embed.title

        # Away team embed
        away_embed = build_team_game_result_embed(data, "t2", game_context=ctx)
        assert "(L3)" in away_embed.title
        assert "Defeat" in away_embed.title

    def test_standings_movement_for_team(self) -> None:
        from pinwheel.discord.embeds import (
            GameContext,
            TeamGameContext,
            build_team_game_result_embed,
        )

        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_team_id": "t1",
            "away_team_id": "t2",
            "home_score": 55,
            "away_score": 48,
            "winner_team_id": "t1",
        }
        ctx = GameContext(
            home=TeamGameContext(standing_position=1, standing_movement=3),
            away=TeamGameContext(standing_position=4, standing_movement=-2),
        )
        home_embed = build_team_game_result_embed(data, "t1", game_context=ctx)
        desc = home_embed.description or ""
        assert "Thorns moved to 1st" in desc

        away_embed = build_team_game_result_embed(data, "t2", game_context=ctx)
        desc = away_embed.description or ""
        assert "Breakers dropped to 4th" in desc

    def test_margin_label_in_team_embed(self) -> None:
        from pinwheel.discord.embeds import (
            GameContext,
            build_team_game_result_embed,
        )

        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_team_id": "t1",
            "away_team_id": "t2",
            "home_score": 50,
            "away_score": 49,
            "winner_team_id": "t1",
        }
        ctx = GameContext(margin_label="Closest game of the season")
        embed = build_team_game_result_embed(data, "t1", game_context=ctx)
        desc = embed.description or ""
        assert "Closest game of the season" in desc

    def test_new_rules_in_team_embed(self) -> None:
        from pinwheel.discord.embeds import (
            GameContext,
            build_team_game_result_embed,
        )

        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_team_id": "t1",
            "away_team_id": "t2",
            "home_score": 55,
            "away_score": 48,
            "winner_team_id": "t1",
        }
        ctx = GameContext(new_rules=["Elam ending enabled"])
        embed = build_team_game_result_embed(data, "t1", game_context=ctx)
        desc = embed.description or ""
        assert "First game under new rules" in desc
        assert "Elam ending enabled" in desc

    def test_playoff_with_streak(self) -> None:
        from pinwheel.discord.embeds import (
            GameContext,
            TeamGameContext,
            build_team_game_result_embed,
        )

        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_team_id": "t1",
            "away_team_id": "t2",
            "home_score": 70,
            "away_score": 55,
            "winner_team_id": "t1",
        }
        ctx = GameContext(
            home=TeamGameContext(streak=4),
        )
        embed = build_team_game_result_embed(data, "t1", playoff_context="finals", game_context=ctx)
        assert "CHAMPIONS" in embed.title
        assert "(W4)" in embed.title

    def test_backward_compat_no_context(self) -> None:
        from pinwheel.discord.embeds import build_team_game_result_embed

        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_team_id": "t1",
            "away_team_id": "t2",
            "home_score": 55,
            "away_score": 48,
            "winner_team_id": "t1",
        }
        embed = build_team_game_result_embed(data, "t1")
        assert "Victory" in embed.title
        desc = embed.description or ""
        assert "**Thorns** 55 - 48 Breakers" in desc
        # No enrichment
        assert "moved to" not in desc
        assert "Closest" not in desc

    def test_loss_streak_in_semifinal_title(self) -> None:
        from pinwheel.discord.embeds import (
            GameContext,
            TeamGameContext,
            build_team_game_result_embed,
        )

        data = {
            "home_team": "Thorns",
            "away_team": "Breakers",
            "home_team_id": "t1",
            "away_team_id": "t2",
            "home_score": 45,
            "away_score": 60,
            "winner_team_id": "t2",
        }
        ctx = GameContext(
            home=TeamGameContext(streak=-2),
            away=TeamGameContext(streak=2),
        )
        embed = build_team_game_result_embed(
            data, "t1", playoff_context="semifinal", game_context=ctx
        )
        assert "Eliminated" in embed.title
        assert "(L2)" in embed.title
