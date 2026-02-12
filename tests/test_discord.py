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
        assert "join" in command_names
        assert "vote" in command_names
        assert "tokens" in command_names
        assert "trade" in command_names
        assert "strategy" in command_names

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

    async def test_handle_propose_with_text_no_engine(self, bot: PinwheelBot) -> None:
        """Without an engine, propose returns an ephemeral error."""
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.user = MagicMock()
        interaction.user.display_name = "TestGovernor"
        await bot._handle_propose(interaction, "Make three-pointers worth 5 points")
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True

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
        # Elam game: sent to both play-by-play and big-plays (both resolve to same mock)
        assert channel.send.call_count == 2
        embed = channel.send.call_args_list[0].kwargs["embed"]
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


# ---------------------------------------------------------------------------
# /join command
# ---------------------------------------------------------------------------


class TestJoinCommand:
    @pytest.fixture
    def bot(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> PinwheelBot:
        return PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus)

    async def test_join_no_engine(self, bot: PinwheelBot) -> None:
        """Without an engine, join returns an ephemeral error."""
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.user = MagicMock()
        interaction.user.id = 111222333
        interaction.user.display_name = "TestPlayer"
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
            await repo.create_agent(
                team.id, season.id, "Briar Ashwood", "sharpshooter",
                {
                    "scoring": 65, "passing": 35, "defense": 30,
                    "speed": 45, "stamina": 40, "iq": 55,
                    "ego": 40, "chaotic_alignment": 20, "fate": 30,
                },
            )
            await session.commit()
            team_id = team.id
            season_id = season.id

        bot = PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus, engine=engine)

        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.id = 111222333
        interaction.user.display_name = "TestPlayer"
        interaction.user.display_avatar = MagicMock()
        interaction.user.display_avatar.url = "https://example.com/avatar.png"
        interaction.guild = MagicMock(spec=discord.Guild)
        interaction.guild.roles = []

        await bot._handle_join(interaction, "Rose City Thorns")
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        embed = call_kwargs.kwargs.get("embed")
        assert embed is not None
        assert "Rose City Thorns" in embed.title
        assert not call_kwargs.kwargs.get("ephemeral")

        # Verify enrollment in DB
        async with get_session(engine) as session:
            repo = Repository(session)
            enrollment = await repo.get_player_enrollment("111222333", season_id)
            assert enrollment is not None
            assert enrollment[0] == team_id
            assert enrollment[1] == "Rose City Thorns"

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

        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.user = MagicMock()
        interaction.user.id = 111222333
        interaction.user.display_name = "TestPlayer"
        interaction.user.display_avatar = MagicMock()
        interaction.user.display_avatar.url = "https://example.com/avatar.png"
        interaction.guild = None

        await bot._handle_join(interaction, "Burnside Breakers")
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        msg = call_kwargs.args[0] if call_kwargs.args else ""
        assert "locked in" in str(msg)

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

        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.user = MagicMock()
        interaction.user.id = 111222333
        interaction.user.display_name = "TestPlayer"
        interaction.user.display_avatar = MagicMock()
        interaction.user.display_avatar.url = "https://example.com/avatar.png"

        await bot._handle_join(interaction, "Nonexistent Team")
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True

        await engine.dispose()


# ---------------------------------------------------------------------------
# _setup_server
# ---------------------------------------------------------------------------


class TestSetupServer:
    @pytest.fixture
    def bot(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> PinwheelBot:
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
    def bot(
        self, settings_discord_enabled: Settings, event_bus: EventBus
    ) -> PinwheelBot:
        return PinwheelBot(settings=settings_discord_enabled, event_bus=event_bus)

    async def test_game_routed_to_play_by_play(self, bot: PinwheelBot) -> None:
        """Non-elam game goes to play-by-play only."""
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
            "type": "game.completed",
            "data": {
                "home_team": "Thorns",
                "away_team": "Breakers",
                "home_score": 45,
                "away_score": 38,
                "elam_activated": False,
                "total_possessions": 60,
            },
        }
        await bot._dispatch_event(event)
        play_channel.send.assert_called_once()
        big_channel.send.assert_not_called()

    async def test_elam_game_routed_to_big_plays(self, bot: PinwheelBot) -> None:
        """Elam game goes to both play-by-play and big-plays."""
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
            "type": "game.completed",
            "data": {
                "home_team": "Thorns",
                "away_team": "Breakers",
                "home_score": 50,
                "away_score": 48,
                "elam_activated": True,
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
            "type": "game.completed",
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

    async def test_round_completed_routed_to_play_by_play(self, bot: PinwheelBot) -> None:
        """Round completed goes to play-by-play."""
        play_channel = AsyncMock(spec=discord.TextChannel)
        bot.channel_ids = {"play_by_play": 201}
        bot.get_channel = MagicMock(return_value=play_channel)

        event = {
            "type": "round.completed",
            "data": {"round": 3, "games": 4, "mirrors": 2, "elapsed_ms": 150.5},
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
            season.id, "Rose City Thorns", color="#e94560",
        )
        await repo.create_agent(
            team.id, season.id, "Briar Ashwood", "sharpshooter",
            {
                "scoring": 65, "passing": 35, "defense": 30,
                "speed": 45, "stamina": 40, "iq": 55,
                "ego": 40, "chaotic_alignment": 20, "fate": 30,
            },
        )
        player = await repo.get_or_create_player(
            str(discord_id), display_name,
        )
        await repo.enroll_player(player.id, team.id, season.id)
        await regenerate_tokens(
            repo, player.id, team.id, season.id,
        )
        await session.commit()
        gov_data = {
            "player_id": player.id,
            "team_id": team.id,
            "season_id": season.id,
        }

    bot = PinwheelBot(
        settings=settings, event_bus=event_bus, engine=engine,
    )

    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = AsyncMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = discord_id
    interaction.user.display_name = display_name
    interaction.user.display_avatar = MagicMock()
    interaction.user.display_avatar.url = "https://example.com/a.png"
    interaction.guild = MagicMock(spec=discord.Guild)
    interaction.guild.roles = []
    interaction.channel = AsyncMock(spec=discord.TextChannel)

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
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.user = MagicMock()
        interaction.user.id = 999888777
        interaction.user.display_name = "Stranger"

        await bot._handle_propose(interaction, "Make it rain")
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "join" in str(call_kwargs.args[0]).lower()
        await engine.dispose()

    async def test_propose_interprets_and_shows_view(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Full propose flow: defer, interpret, send view."""
        bot, interaction, gov_data, engine = (
            await _make_enrolled_bot_and_interaction(
                settings_discord_enabled, event_bus,
            )
        )

        await bot._handle_propose(
            interaction, "Make three-pointers worth 5 points",
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
        bot, interaction, gov_data, engine = (
            await _make_enrolled_bot_and_interaction(
                settings_discord_enabled, event_bus,
            )
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
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.user = MagicMock()
        interaction.user.id = 999888777

        await bot._handle_vote(interaction, "yes")
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "join" in str(call_kwargs.args[0]).lower()
        await engine.dispose()

    async def test_vote_no_proposals(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Voting with no active proposals returns error."""
        bot, interaction, gov_data, engine = (
            await _make_enrolled_bot_and_interaction(
                settings_discord_enabled, event_bus,
            )
        )

        await bot._handle_vote(interaction, "yes")
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "no proposals" in str(call_kwargs.args[0]).lower()
        await engine.dispose()

    async def test_vote_success(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Successful vote is recorded and hidden."""
        bot, interaction, gov_data, engine = (
            await _make_enrolled_bot_and_interaction(
                settings_discord_enabled, event_bus,
            )
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
        call_kwargs = interaction.response.send_message.call_args
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
        bot, interaction, gov_data, engine = (
            await _make_enrolled_bot_and_interaction(
                settings_discord_enabled, event_bus,
            )
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
        call_kwargs = interaction.response.send_message.call_args
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
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.user = MagicMock()
        interaction.user.id = 999888777

        await bot._handle_tokens(interaction)
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        await engine.dispose()

    async def test_tokens_shows_balance(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        bot, interaction, gov_data, engine = (
            await _make_enrolled_bot_and_interaction(
                settings_discord_enabled, event_bus,
            )
        )

        await bot._handle_tokens(interaction)
        call_kwargs = interaction.response.send_message.call_args
        embed = call_kwargs.kwargs.get("embed")
        assert embed is not None
        assert "Governance Tokens" in embed.title
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
        bot, interaction, gov_data, engine = (
            await _make_enrolled_bot_and_interaction(
                settings_discord_enabled, event_bus,
            )
        )

        target = MagicMock(spec=discord.Member)
        target.id = interaction.user.id  # same user
        target.display_name = "TestGovernor"

        await bot._handle_trade(
            interaction, target, "propose", 1, "amend", 1,
        )
        call_kwargs = interaction.response.send_message.call_args
        assert "yourself" in str(call_kwargs.args[0]).lower()
        await engine.dispose()

    async def test_trade_target_not_enrolled(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        bot, interaction, gov_data, engine = (
            await _make_enrolled_bot_and_interaction(
                settings_discord_enabled, event_bus,
            )
        )

        target = MagicMock(spec=discord.Member)
        target.id = 999888777
        target.display_name = "Stranger"

        await bot._handle_trade(
            interaction, target, "propose", 1, "amend", 1,
        )
        call_kwargs = interaction.response.send_message.call_args
        assert "not enrolled" in str(call_kwargs.args[0]).lower()
        await engine.dispose()

    async def test_trade_insufficient_tokens(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        bot, interaction, gov_data, engine = (
            await _make_enrolled_bot_and_interaction(
                settings_discord_enabled,
                event_bus,
                discord_id=111222333,
            )
        )

        # Create a second enrolled governor
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        async with get_session(engine) as session:
            repo = Repository(session)
            player2 = await repo.get_or_create_player(
                "444555666", "Player2",
            )
            await repo.enroll_player(
                player2.id, gov_data["team_id"],
                gov_data["season_id"],
            )
            await session.commit()

        target = MagicMock(spec=discord.Member)
        target.id = 444555666
        target.display_name = "Player2"

        # Try to trade 99 tokens (more than they have)
        await bot._handle_trade(
            interaction, target, "propose", 99, "amend", 1,
        )
        call_kwargs = interaction.response.send_message.call_args
        assert "only have" in str(call_kwargs.args[0]).lower()
        await engine.dispose()


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
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.user = MagicMock()
        interaction.user.id = 999888777

        await bot._handle_strategy(interaction, "Focus on defense")
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "join" in str(call_kwargs.args[0]).lower()
        await engine.dispose()

    async def test_strategy_shows_confirm_view(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        bot, interaction, gov_data, engine = (
            await _make_enrolled_bot_and_interaction(
                settings_discord_enabled, event_bus,
            )
        )

        await bot._handle_strategy(
            interaction, "Focus on three-point shooting",
        )
        call_kwargs = interaction.response.send_message.call_args
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
        bot, interaction, gov_data, engine = (
            await _make_enrolled_bot_and_interaction(
                settings_discord_enabled, event_bus,
            )
        )

        await bot._handle_strategy(interaction, "   ")
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        await engine.dispose()


# ---------------------------------------------------------------------------
# Private mirror DM dispatch
# ---------------------------------------------------------------------------


class TestPrivateMirrorDM:
    async def test_private_mirror_dispatches_dm(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Private mirror event triggers a DM to the governor."""
        bot, interaction, gov_data, engine = (
            await _make_enrolled_bot_and_interaction(
                settings_discord_enabled, event_bus,
            )
        )

        mock_user = AsyncMock()
        bot.get_user = MagicMock(return_value=mock_user)

        event = {
            "type": "mirror.generated",
            "data": {
                "mirror_type": "private",
                "round": 3,
                "governor_id": gov_data["player_id"],
                "mirror_id": "m-priv-1",
                "excerpt": "Your pattern reveals caution.",
            },
        }
        await bot._dispatch_event(event)
        mock_user.send.assert_called_once()
        call_kwargs = mock_user.send.call_args
        embed = call_kwargs.kwargs.get("embed")
        assert embed is not None
        assert "Private Mirror" in embed.title
        await engine.dispose()

    async def test_private_mirror_no_engine(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Private mirror without engine is a no-op."""
        bot = PinwheelBot(
            settings=settings_discord_enabled, event_bus=event_bus,
        )
        bot.get_user = MagicMock()

        await bot._send_private_mirror({
            "governor_id": "gov-1",
            "excerpt": "Mirror text",
            "round": 1,
        })
        bot.get_user.assert_not_called()

    async def test_private_mirror_missing_data(
        self,
        settings_discord_enabled: Settings,
        event_bus: EventBus,
    ) -> None:
        """Private mirror with missing governor_id is a no-op."""
        bot = PinwheelBot(
            settings=settings_discord_enabled, event_bus=event_bus,
        )
        await bot._send_private_mirror({
            "excerpt": "Mirror text",
            "round": 1,
        })  # No governor_id — should not raise
