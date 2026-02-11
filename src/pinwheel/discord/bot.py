"""Discord bot for Pinwheel Fates.

Runs alongside FastAPI using the same event loop. Subscribes to EventBus
for real-time game updates and posts results, governance outcomes, and
mirrors to configured channels.

The bot is optional: if DISCORD_BOT_TOKEN is not set, nothing starts.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

import discord
from discord import Intents, app_commands
from discord.ext import commands

from pinwheel.discord.embeds import (
    build_game_result_embed,
    build_mirror_embed,
    build_round_summary_embed,
    build_schedule_embed,
    build_standings_embed,
)

if TYPE_CHECKING:
    from pinwheel.config import Settings
    from pinwheel.core.event_bus import EventBus

logger = logging.getLogger(__name__)


class PinwheelBot(commands.Bot):
    """The Pinwheel Fates Discord bot.

    Runs in-process with FastAPI. Subscribes to EventBus events and
    posts updates to the configured Discord channel. Provides slash
    commands for standings, proposals, schedule, and mirrors.
    """

    def __init__(self, settings: Settings, event_bus: EventBus) -> None:
        intents = Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            description="Pinwheel Fates -- league commissioner, rules interpreter, and town crier.",
        )
        self.settings = settings
        self.event_bus = event_bus
        self.main_channel_id: int = (
            int(settings.discord_channel_id) if settings.discord_channel_id else 0
        )
        self._event_listener_task: asyncio.Task[None] | None = None
        self._setup_commands()

    def _setup_commands(self) -> None:
        """Register slash commands on the bot's command tree."""

        @self.tree.command(name="standings", description="View current league standings")
        async def standings_command(interaction: discord.Interaction) -> None:
            await self._handle_standings(interaction)

        @self.tree.command(name="propose", description="Submit a rule change proposal")
        @app_commands.describe(text="Your rule change proposal in natural language")
        async def propose_command(interaction: discord.Interaction, text: str) -> None:
            await self._handle_propose(interaction, text)

        @self.tree.command(name="schedule", description="View the upcoming game schedule")
        async def schedule_command(interaction: discord.Interaction) -> None:
            await self._handle_schedule(interaction)

        @self.tree.command(name="mirrors", description="View the latest AI mirror reflections")
        async def mirrors_command(interaction: discord.Interaction) -> None:
            await self._handle_mirrors(interaction)

    async def setup_hook(self) -> None:
        """Called when the bot is ready to start. Syncs slash commands."""
        if self.settings.discord_guild_id:
            guild = discord.Object(id=int(self.settings.discord_guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info(
                "discord_commands_synced guild_id=%s", self.settings.discord_guild_id
            )
        else:
            await self.tree.sync()
            logger.info("discord_commands_synced globally")

    async def on_ready(self) -> None:
        """Called when the bot has connected to Discord."""
        user = self.user
        name = user.name if user else "unknown"
        logger.info("discord_bot_ready user=%s", name)
        self._event_listener_task = asyncio.create_task(
            self._listen_to_event_bus(), name="discord-event-listener"
        )

    async def _listen_to_event_bus(self) -> None:
        """Subscribe to EventBus and forward events to Discord channels."""
        async with self.event_bus.subscribe(None) as subscription:
            async for event in subscription:
                try:
                    await self._dispatch_event(event)
                except Exception:
                    logger.exception("discord_event_dispatch_error event=%s", event.get("type"))

    async def _dispatch_event(self, event: dict[str, object]) -> None:
        """Route an EventBus event to the appropriate Discord handler."""
        if not self.main_channel_id:
            return

        channel = self.get_channel(self.main_channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(self.main_channel_id)
            except discord.NotFound:
                logger.warning(
                    "discord_channel_not_found channel_id=%d", self.main_channel_id
                )
                return

        if not isinstance(channel, discord.TextChannel):
            return

        event_type = str(event.get("type", ""))
        data = event.get("data", {})
        if not isinstance(data, dict):
            return

        if event_type == "game.completed":
            embed = build_game_result_embed(data)
            await channel.send(embed=embed)

        elif event_type == "round.completed":
            embed = build_round_summary_embed(data)
            await channel.send(embed=embed)

        elif event_type == "mirror.generated":
            mirror_type = data.get("mirror_type", "")
            excerpt = str(data.get("excerpt", ""))
            if mirror_type != "private" and excerpt:
                from pinwheel.models.mirror import Mirror

                mirror = Mirror(
                    id="",
                    mirror_type=mirror_type,  # type: ignore[arg-type]
                    round_number=int(data.get("round", 0)),
                    content=excerpt,
                )
                embed = build_mirror_embed(mirror)
                await channel.send(embed=embed)

        elif event_type == "governance.window_closed":
            proposals_count = data.get("proposals_count", 0)
            rules_changed = data.get("rules_changed", 0)
            round_num = data.get("round", "?")
            embed = discord.Embed(
                title=f"Governance Window Closed -- Round {round_num}",
                description=(
                    f"**{proposals_count}** proposals reviewed\n"
                    f"**{rules_changed}** rules changed"
                ),
                color=0x3498DB,
            )
            embed.set_footer(text="Pinwheel Fates")
            await channel.send(embed=embed)

    # --- Slash command handlers ---

    async def _handle_standings(self, interaction: discord.Interaction) -> None:
        """Handle the /standings slash command."""
        # Respond with placeholder standings.
        # In production this would query the repository via the FastAPI app state.
        embed = build_standings_embed([])
        await interaction.response.send_message(embed=embed)

    async def _handle_propose(self, interaction: discord.Interaction, text: str) -> None:
        """Handle the /propose slash command."""
        if not text.strip():
            await interaction.response.send_message(
                "You need to describe your rule change proposal. "
                "Example: `/propose Make three-pointers worth 5 points`",
                ephemeral=True,
            )
            return

        # Acknowledge receipt. In production, this sends text to the AI interpreter.
        embed = discord.Embed(
            title="Proposal Received",
            description=f'"{text}"\n\nYour proposal has been received and is being interpreted.',
            color=0x3498DB,
        )
        embed.add_field(name="Governor", value=interaction.user.display_name, inline=True)
        embed.set_footer(text="Pinwheel Fates")
        await interaction.response.send_message(embed=embed)

    async def _handle_schedule(self, interaction: discord.Interaction) -> None:
        """Handle the /schedule slash command."""
        # Respond with placeholder schedule.
        embed = build_schedule_embed([], round_number=0)
        await interaction.response.send_message(embed=embed)

    async def _handle_mirrors(self, interaction: discord.Interaction) -> None:
        """Handle the /mirrors slash command."""
        embed = discord.Embed(
            title="Latest Mirrors",
            description="No mirrors have been generated yet. Mirrors appear after each round.",
            color=0x9B59B6,
        )
        embed.set_footer(text="Pinwheel Fates")
        await interaction.response.send_message(embed=embed)

    async def close(self) -> None:
        """Clean shutdown: cancel event listener and close bot."""
        if self._event_listener_task and not self._event_listener_task.done():
            self._event_listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._event_listener_task
        await super().close()


def is_discord_enabled(settings: Settings) -> bool:
    """Check whether Discord integration should be started.

    Returns True only when both discord_enabled is True and a token is set.
    """
    return bool(settings.discord_enabled and settings.discord_bot_token)


async def start_discord_bot(settings: Settings, event_bus: EventBus) -> PinwheelBot:
    """Create and start the Discord bot in the current event loop.

    Returns the bot instance so the caller can stop it during shutdown.
    The bot runs as a background task; this function returns immediately
    after starting it.
    """
    bot = PinwheelBot(settings=settings, event_bus=event_bus)

    async def _run_bot() -> None:
        try:
            await bot.start(settings.discord_bot_token)
        except asyncio.CancelledError:
            logger.info("discord_bot_cancelled")
        except Exception:
            logger.exception("discord_bot_error")
        finally:
            if not bot.is_closed():
                await bot.close()

    asyncio.create_task(_run_bot(), name="discord-bot")
    logger.info("discord_bot_started")
    return bot
