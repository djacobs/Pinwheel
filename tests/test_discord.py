"""Tests for the Discord bot integration.

All Discord objects are mocked — no real Discord connection required.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from pinwheel.config import Settings
from pinwheel.core.event_bus import EventBus
from pinwheel.discord.bot import PinwheelBot, is_discord_enabled, start_discord_bot
from pinwheel.discord.embeds import (
    build_game_result_embed,
    build_mirror_embed,
    build_proposal_embed,
    build_round_summary_embed,
    build_schedule_embed,
    build_standings_embed,
    build_vote_tally_embed,
)
from pinwheel.models.governance import Proposal, RuleInterpretation, VoteTally
from pinwheel.models.mirror import Mirror

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
    def test_bot_creation(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> None:
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
        assert "mirrors" in command_names


# ---------------------------------------------------------------------------
# Slash command handlers
# ---------------------------------------------------------------------------


class TestSlashCommands:
    @pytest.fixture
    def bot(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> PinwheelBot:
        return PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus)

    async def test_handle_standings(self, bot: PinwheelBot) -> None:
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await bot._handle_standings(interaction)
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        embed = call_kwargs.kwargs.get("embed") or call_kwargs.args[0]
        assert isinstance(embed, discord.Embed)

    async def test_handle_propose_with_text(self, bot: PinwheelBot) -> None:
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.user = MagicMock()
        interaction.user.display_name = "TestGovernor"
        await bot._handle_propose(interaction, "Make three-pointers worth 5 points")
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        embed = call_kwargs.kwargs.get("embed") or call_kwargs.args[0]
        assert "Proposal Received" in embed.title

    async def test_handle_propose_empty_text(self, bot: PinwheelBot) -> None:
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await bot._handle_propose(interaction, "   ")
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True

    async def test_handle_schedule(self, bot: PinwheelBot) -> None:
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await bot._handle_schedule(interaction)
        interaction.response.send_message.assert_called_once()

    async def test_handle_mirrors(self, bot: PinwheelBot) -> None:
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        await bot._handle_mirrors(interaction)
        interaction.response.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# Event dispatch
# ---------------------------------------------------------------------------


class TestEventDispatch:
    @pytest.fixture
    def bot(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> PinwheelBot:
        return PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus)

    async def test_dispatch_game_completed(self, bot: PinwheelBot) -> None:
        channel = AsyncMock(spec=discord.TextChannel)
        bot.get_channel = MagicMock(return_value=channel)

        event = {
            "type": "game.completed",
            "data": {
                "game_id": "g-1-0",
                "home_team": "Rose City Thorns",
                "away_team": "Burnside Breakers",
                "home_score": 45,
                "away_score": 38,
                "winner_team_id": "team-1",
                "elam_activated": True,
                "total_possessions": 60,
            },
        }
        await bot._dispatch_event(event)
        channel.send.assert_called_once()
        embed = channel.send.call_args.kwargs["embed"]
        assert "Rose City Thorns" in embed.title

    async def test_dispatch_round_completed(self, bot: PinwheelBot) -> None:
        channel = AsyncMock(spec=discord.TextChannel)
        bot.get_channel = MagicMock(return_value=channel)

        event = {
            "type": "round.completed",
            "data": {"round": 3, "games": 4, "mirrors": 2, "elapsed_ms": 150.5},
        }
        await bot._dispatch_event(event)
        channel.send.assert_called_once()

    async def test_dispatch_mirror_generated_public(self, bot: PinwheelBot) -> None:
        channel = AsyncMock(spec=discord.TextChannel)
        bot.get_channel = MagicMock(return_value=channel)

        event = {
            "type": "mirror.generated",
            "data": {
                "mirror_type": "simulation",
                "round": 5,
                "excerpt": "The Rose City Thorns dominated the boards this round.",
            },
        }
        await bot._dispatch_event(event)
        channel.send.assert_called_once()

    async def test_dispatch_mirror_generated_private_skipped(
        self, bot: PinwheelBot
    ) -> None:
        channel = AsyncMock(spec=discord.TextChannel)
        bot.get_channel = MagicMock(return_value=channel)

        event = {
            "type": "mirror.generated",
            "data": {
                "mirror_type": "private",
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

    async def test_dispatch_no_channel_configured(
        self, event_bus: EventBus
    ) -> None:
        settings = Settings(
            pinwheel_env="development",
            database_url="sqlite+aiosqlite:///:memory:",
            discord_bot_token="tok",
            discord_channel_id="",
            discord_enabled=True,
        )
        bot = PinwheelBot(settings=settings, event_bus=event_bus)

        # Should not raise, just silently return
        event = {"type": "game.completed", "data": {"home_team": "A", "away_team": "B"}}
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

    def test_elam_ending(self) -> None:
        data = {
            "home_team": "A",
            "away_team": "B",
            "home_score": 50,
            "away_score": 50,
            "elam_activated": True,
            "total_possessions": 75,
        }
        embed = build_game_result_embed(data)
        assert "Elam" in (embed.description or "")


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
        )
        embed = build_vote_tally_embed(tally, "Make it rain")
        assert "PASSED" in embed.title
        assert "Make it rain" in (embed.description or "")

    def test_failed_tally(self) -> None:
        tally = VoteTally(
            proposal_id="p-1",
            weighted_yes=2.0,
            weighted_no=6.0,
            total_weight=8.0,
            passed=False,
            threshold=0.5,
        )
        embed = build_vote_tally_embed(tally)
        assert "FAILED" in embed.title


class TestBuildMirrorEmbed:
    def test_simulation_mirror(self) -> None:
        mirror = Mirror(
            id="m-1",
            mirror_type="simulation",
            round_number=3,
            content="The Thorns dominated this round with superior defense.",
        )
        embed = build_mirror_embed(mirror)
        assert "Simulation Mirror" in embed.title
        assert "Round 3" in embed.title
        assert "Thorns" in (embed.description or "")

    def test_governance_mirror(self) -> None:
        mirror = Mirror(
            id="m-2",
            mirror_type="governance",
            round_number=7,
            content="A coalition is forming between two teams.",
        )
        embed = build_mirror_embed(mirror)
        assert "Governance Mirror" in embed.title


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
        data = {"round": 3, "games": 4, "mirrors": 2, "elapsed_ms": 150.5}
        embed = build_round_summary_embed(data)
        assert "Round 3" in embed.title
        assert "4" in (embed.description or "")
