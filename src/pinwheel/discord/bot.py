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
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.discord.embeds import (
    build_game_result_embed,
    build_interpretation_embed,
    build_mirror_embed,
    build_round_summary_embed,
    build_schedule_embed,
    build_standings_embed,
    build_strategy_embed,
    build_token_balance_embed,
    build_trade_offer_embed,
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

    def __init__(
        self,
        settings: Settings,
        event_bus: EventBus,
        engine: AsyncEngine | None = None,
    ) -> None:
        intents = Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            description="Pinwheel Fates -- league commissioner, rules interpreter, and town crier.",
        )
        self.settings = settings
        self.event_bus = event_bus
        self.engine = engine
        self.main_channel_id: int = (
            int(settings.discord_channel_id) if settings.discord_channel_id else 0
        )
        self.channel_ids: dict[str, int] = {}
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

        @self.tree.command(name="join", description="Join a team as a governor for this season")
        @app_commands.describe(team="The team name to join (leave blank to see all teams)")
        async def join_command(
            interaction: discord.Interaction, team: str = "",
        ) -> None:
            await self._handle_join(interaction, team)

        @join_command.autocomplete("team")
        async def _team_autocomplete(
            interaction: discord.Interaction, current: str,
        ) -> list[app_commands.Choice[str]]:
            return await self._autocomplete_teams(current)

        @self.tree.command(
            name="vote",
            description="Vote on the current active proposal",
        )
        @app_commands.describe(
            choice="Your vote: yes or no",
            boost="Use a BOOST token to double your vote weight",
        )
        @app_commands.choices(choice=[
            app_commands.Choice(name="Yes", value="yes"),
            app_commands.Choice(name="No", value="no"),
        ])
        async def vote_command(
            interaction: discord.Interaction,
            choice: app_commands.Choice[str],
            boost: bool = False,
        ) -> None:
            await self._handle_vote(interaction, choice.value, boost)

        @self.tree.command(
            name="tokens",
            description="Check your governance token balance",
        )
        async def tokens_command(
            interaction: discord.Interaction,
        ) -> None:
            await self._handle_tokens(interaction)

        @self.tree.command(
            name="trade",
            description="Offer a token trade to another governor",
        )
        @app_commands.describe(
            target="The governor to trade with",
            offer_type="Token type you're offering",
            offer_amount="How many tokens to offer",
            request_type="Token type you want in return",
            request_amount="How many tokens you want",
        )
        @app_commands.choices(
            offer_type=[
                app_commands.Choice(name="PROPOSE", value="propose"),
                app_commands.Choice(name="AMEND", value="amend"),
                app_commands.Choice(name="BOOST", value="boost"),
            ],
            request_type=[
                app_commands.Choice(name="PROPOSE", value="propose"),
                app_commands.Choice(name="AMEND", value="amend"),
                app_commands.Choice(name="BOOST", value="boost"),
            ],
        )
        async def trade_command(
            interaction: discord.Interaction,
            target: discord.Member,
            offer_type: app_commands.Choice[str],
            offer_amount: int,
            request_type: app_commands.Choice[str],
            request_amount: int,
        ) -> None:
            await self._handle_trade(
                interaction, target,
                offer_type.value, offer_amount,
                request_type.value, request_amount,
            )

        @self.tree.command(
            name="trade-agent",
            description="Propose trading agents between two teams",
        )
        @app_commands.describe(
            offer_agent="Name of the agent you're offering",
            request_agent="Name of the agent you want in return",
        )
        async def trade_agent_command(
            interaction: discord.Interaction,
            offer_agent: str,
            request_agent: str,
        ) -> None:
            await self._handle_trade_agent(interaction, offer_agent, request_agent)

        @trade_agent_command.autocomplete("offer_agent")
        async def _offer_agent_autocomplete(
            interaction: discord.Interaction, current: str,
        ) -> list[app_commands.Choice[str]]:
            return await self._autocomplete_agents(interaction, current, own_team=True)

        @trade_agent_command.autocomplete("request_agent")
        async def _request_agent_autocomplete(
            interaction: discord.Interaction, current: str,
        ) -> list[app_commands.Choice[str]]:
            return await self._autocomplete_agents(interaction, current, own_team=False)

        @self.tree.command(
            name="strategy",
            description="Set your team's strategic direction",
        )
        @app_commands.describe(
            text="Your team's strategy in natural language",
        )
        async def strategy_command(
            interaction: discord.Interaction, text: str,
        ) -> None:
            await self._handle_strategy(interaction, text)

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
        await self._setup_server()
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

    async def _setup_server(self) -> None:
        """Create channels, roles, and post welcome message on bot startup.

        Idempotent: loads persisted channel IDs from bot_state, validates
        they still exist in the guild, creates anything missing, and
        persists all IDs back. Safe to call on every restart.
        """
        if not self.settings.discord_guild_id:
            return

        guild = self.get_guild(int(self.settings.discord_guild_id))
        if guild is None:
            logger.warning(
                "discord_setup_guild_not_found guild_id=%s",
                self.settings.discord_guild_id,
            )
            return

        # --- Load persisted channel IDs from DB ---
        await self._load_persisted_channel_ids()

        # --- Get or create category ---
        category_name = "PINWHEEL FATES"
        category = discord.utils.get(guild.categories, name=category_name)
        if category is None:
            try:
                category = await guild.create_category(category_name)
                logger.info("discord_setup_created category=%s", category_name)
            except Exception:
                logger.exception("discord_setup_category_failed name=%s", category_name)
                return

        # --- Get or create shared channels ---
        channel_defs = [
            ("how-to-play", "Learn how to play Pinwheel Fates"),
            ("play-by-play", "Live game updates"),
            ("big-plays", "Highlights -- Elam endings, upsets, blowouts"),
        ]
        for ch_name, ch_topic in channel_defs:
            key = ch_name.replace("-", "_")
            channel = await self._get_or_create_shared_channel(
                guild, category, ch_name, ch_topic, key,
            )
            if channel is not None:
                self.channel_ids[key] = channel.id
                await self._persist_bot_state(f"channel_{key}", str(channel.id))

        # --- Get or create team channels + roles ---
        if self.engine:
            try:
                from sqlalchemy import select

                from pinwheel.db.engine import get_session
                from pinwheel.db.models import SeasonRow
                from pinwheel.db.repository import Repository

                async with get_session(self.engine) as session:
                    repo = Repository(session)
                    result = await session.execute(select(SeasonRow).limit(1))
                    season = result.scalar_one_or_none()
                    if season:
                        teams = await repo.get_teams_for_season(season.id)
                        for team in teams:
                            await self._setup_team_channel_and_role(
                                guild, category, team,
                            )
            except Exception:
                logger.exception("discord_setup_team_channels_failed")

        # --- Post welcome message if #how-to-play is empty ---
        await self._post_welcome_message(guild)

        logger.info("discord_setup_complete channels=%s", list(self.channel_ids.keys()))

    async def _load_persisted_channel_ids(self) -> None:
        """Load channel IDs from bot_state table into self.channel_ids."""
        if not self.engine:
            return
        try:
            from pinwheel.db.engine import get_session

            async with get_session(self.engine) as session:
                # Load all channel_* keys
                from sqlalchemy import select

                from pinwheel.db.models import BotStateRow

                stmt = select(BotStateRow).where(BotStateRow.key.like("channel_%"))
                result = await session.execute(stmt)
                rows = result.scalars().all()
                for row in rows:
                    # channel_how_to_play -> how_to_play
                    channel_key = row.key.removeprefix("channel_")
                    self.channel_ids[channel_key] = int(row.value)
                if rows:
                    logger.info(
                        "discord_setup_loaded_persisted_ids count=%d keys=%s",
                        len(list(rows)),
                        [r.key for r in rows],
                    )
        except Exception:
            logger.exception("discord_setup_load_persisted_ids_failed")

    async def _persist_bot_state(self, key: str, value: str) -> None:
        """Persist a single bot state key-value pair to the database."""
        if not self.engine:
            return
        try:
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                await repo.set_bot_state(key, value)
        except Exception:
            logger.exception("discord_setup_persist_state_failed key=%s", key)

    async def _get_or_create_shared_channel(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        ch_name: str,
        ch_topic: str,
        key: str,
    ) -> discord.TextChannel | None:
        """Find or create a shared (public) text channel.

        Checks: persisted ID -> guild lookup by name -> create new.
        Shared channels grant @everyone read access.
        """
        # 1. Check if persisted ID still valid in guild
        persisted_id = self.channel_ids.get(key)
        if persisted_id:
            existing = guild.get_channel(persisted_id)
            if isinstance(existing, discord.TextChannel):
                logger.info("discord_setup_reused channel=%s id=%d", ch_name, persisted_id)
                return existing

        # 2. Look up by name in guild
        existing = discord.utils.get(guild.text_channels, name=ch_name, category=category)
        if existing is not None:
            logger.info("discord_setup_found_by_name channel=%s id=%d", ch_name, existing.id)
            return existing

        # 3. Create new channel with @everyone read
        try:
            allow_everyone = discord.PermissionOverwrite(read_messages=True)
            overwrites = {guild.default_role: allow_everyone}
            new_ch = await guild.create_text_channel(
                ch_name, category=category, topic=ch_topic, overwrites=overwrites,
            )
            logger.info("discord_setup_created channel=%s id=%d", ch_name, new_ch.id)
            return new_ch
        except Exception:
            logger.exception("discord_setup_create_channel_failed name=%s", ch_name)
            return None

    async def _setup_team_channel_and_role(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        team: object,
    ) -> None:
        """Set up a single team's role and private channel.

        Team channels deny @everyone read and grant the team role
        read + send. Each operation is individually wrapped for
        graceful degradation.
        """
        slug = team.name.lower().replace(" ", "-")  # type: ignore[union-attr]
        team_key = f"team_{team.id}"  # type: ignore[union-attr]

        # --- Role ---
        role = discord.utils.get(guild.roles, name=team.name)  # type: ignore[union-attr]
        if role is None:
            try:
                color = discord.Color(int(team.color.lstrip("#"), 16))  # type: ignore[union-attr]
                role = await guild.create_role(name=team.name, color=color)  # type: ignore[union-attr]
                logger.info("discord_setup_created role=%s", team.name)  # type: ignore[union-attr]
            except Exception:
                logger.exception("discord_setup_create_role_failed name=%s", team.name)  # type: ignore[union-attr]
                return
        else:
            logger.info("discord_setup_reused role=%s", team.name)  # type: ignore[union-attr]

        # --- Team channel ---
        # 1. Check persisted ID
        persisted_id = self.channel_ids.get(team_key)
        team_ch: discord.TextChannel | None = None
        if persisted_id:
            found = guild.get_channel(persisted_id)
            if isinstance(found, discord.TextChannel):
                team_ch = found
                logger.info("discord_setup_reused team_channel=%s id=%d", slug, persisted_id)

        # 2. Look up by name
        if team_ch is None:
            team_ch = discord.utils.get(
                guild.text_channels, name=slug, category=category,
            )
            if team_ch is not None:
                logger.info("discord_setup_found_by_name team_channel=%s id=%d", slug, team_ch.id)

        # 3. Create new private channel
        if team_ch is None:
            try:
                deny = discord.PermissionOverwrite(read_messages=False)
                allow = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True,
                )
                overwrites = {
                    guild.default_role: deny,
                    guild.me: allow,
                    role: allow,
                }
                team_ch = await guild.create_text_channel(
                    slug,
                    category=category,
                    overwrites=overwrites,
                    topic=f"Team channel for {team.name}",  # type: ignore[union-attr]
                )
                logger.info("discord_setup_created team_channel=%s id=%d", slug, team_ch.id)
            except Exception:
                logger.exception("discord_setup_create_team_channel_failed name=%s", slug)
                return

        self.channel_ids[team_key] = team_ch.id
        await self._persist_bot_state(f"channel_{team_key}", str(team_ch.id))

    async def _post_welcome_message(self, guild: discord.Guild) -> None:
        """Post the welcome message to #how-to-play if the channel is empty."""
        how_to_play_id = self.channel_ids.get("how_to_play")
        if not how_to_play_id:
            return

        channel = guild.get_channel(how_to_play_id)
        if not isinstance(channel, discord.TextChannel):
            return

        # Only post if the channel has no messages
        has_messages = False
        async for _msg in channel.history(limit=1):
            has_messages = True
        if has_messages:
            return

        # Build team listing from DB
        team_lines = ""
        if self.engine:
            try:
                from sqlalchemy import select

                from pinwheel.db.engine import get_session
                from pinwheel.db.models import SeasonRow
                from pinwheel.db.repository import Repository

                async with get_session(self.engine) as session:
                    repo = Repository(session)
                    result = await session.execute(select(SeasonRow).limit(1))
                    season = result.scalar_one_or_none()
                    if season:
                        teams = await repo.get_teams_for_season(season.id)
                        lines = []
                        for team in teams:
                            agent_names = ", ".join(a.name for a in team.agents)
                            lines.append(f"**{team.name}** -- {agent_names}")
                        team_lines = "\n".join(lines)
            except Exception:
                logger.exception("discord_welcome_team_query_failed")

        if not team_lines:
            team_lines = "*(Teams will appear once the season is seeded)*"

        welcome_text = (
            "**Welcome to Pinwheel Fates!**\n\n"
            "You're about to become a governor of a 3v3 basketball league "
            "where YOU make the rules.\n\n"
            "**How it works:**\n"
            "- Pick a team with `/join [team name]` -- "
            "you'll govern with them for the whole season\n"
            "- Watch games unfold in #play-by-play and see highlights in #big-plays\n"
            "- Propose rule changes with `/propose` -- the AI interprets your natural language "
            "into game parameters\n"
            "- Check standings with `/standings`, schedule with `/schedule`\n\n"
            f"**The teams:**\n{team_lines}\n\n"
            "Choose wisely. Your team's agents are counting on you."
        )

        embed = discord.Embed(
            title="How to Play",
            description=welcome_text,
            color=0x3498DB,
        )
        embed.set_footer(text="Pinwheel Fates")
        await channel.send(embed=embed)
        logger.info("discord_welcome_message_posted")

    async def _autocomplete_teams(self, current: str) -> list[app_commands.Choice[str]]:
        """Return team name choices matching the current input."""
        if not self.engine:
            return []
        try:
            from sqlalchemy import select

            from pinwheel.db.engine import get_session
            from pinwheel.db.models import SeasonRow
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                result = await session.execute(select(SeasonRow).limit(1))
                season = result.scalar_one_or_none()
                if not season:
                    return []
                teams = await repo.get_teams_for_season(season.id)
                lowered = current.lower()
                return [
                    app_commands.Choice(name=t.name, value=t.name)
                    for t in teams
                    if lowered in t.name.lower()
                ][:25]
        except Exception:
            logger.exception("discord_team_autocomplete_failed")
            return []

    async def _handle_join(self, interaction: discord.Interaction, team_name: str) -> None:
        """Handle the /join slash command for team enrollment."""
        if not self.engine:
            await interaction.response.send_message(
                "Database not available. Try again later.", ephemeral=True,
            )
            return

        try:
            from sqlalchemy import select

            from pinwheel.db.engine import get_session
            from pinwheel.db.models import SeasonRow
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                result = await session.execute(select(SeasonRow).limit(1))
                season = result.scalar_one_or_none()
                if not season:
                    await interaction.response.send_message(
                        "No active season.", ephemeral=True,
                    )
                    return

                teams = await repo.get_teams_for_season(season.id)

                # No team specified → show team list with governor counts
                if not team_name.strip():
                    from pinwheel.discord.embeds import build_team_list_embed

                    counts = await repo.get_governor_counts_by_team(season.id)
                    team_data = [
                        {
                            "name": t.name,
                            "color": t.color,
                            "governor_count": counts.get(t.id, 0),
                        }
                        for t in teams
                    ]
                    embed = build_team_list_embed(
                        team_data, season.name or "this season",
                    )
                    await interaction.response.send_message(
                        embed=embed, ephemeral=True,
                    )
                    return

                # Check existing enrollment
                discord_id = str(interaction.user.id)
                enrollment = await repo.get_player_enrollment(discord_id, season.id)
                if enrollment is not None:
                    _existing_team_id, existing_team_name = enrollment
                    if existing_team_name.lower() == team_name.lower():
                        await interaction.response.send_message(
                            f"You're already on **{existing_team_name}**!",
                            ephemeral=True,
                        )
                    else:
                        season_label = season.name or "Season 1"
                        await interaction.response.send_message(
                            f"You joined **{existing_team_name}** for {season_label}. "
                            "Team switches aren't allowed mid-season -- ride or die!",
                            ephemeral=True,
                        )
                    return

                # Find the requested team
                target_team = None
                for t in teams:
                    if t.name.lower() == team_name.lower():
                        target_team = t
                        break

                if target_team is None:
                    available = ", ".join(t.name for t in teams)
                    await interaction.response.send_message(
                        f"Team not found. Available teams: {available}",
                        ephemeral=True,
                    )
                    return

                # Create or get player, then enroll
                player = await repo.get_or_create_player(
                    discord_id=discord_id,
                    username=interaction.user.display_name,
                    avatar_url=(
                        str(interaction.user.display_avatar.url)
                        if interaction.user.display_avatar
                        else ""
                    ),
                )
                await repo.enroll_player(player.id, target_team.id, season.id)
                await session.commit()

            # Assign Discord role if in a guild
            if interaction.guild:
                role = discord.utils.get(
                    interaction.guild.roles, name=target_team.name,
                )
                if role and isinstance(interaction.user, discord.Member):
                    await interaction.user.add_roles(role)

            # Build confirmation embed (shown in channel)
            from pinwheel.discord.embeds import build_welcome_embed

            agents = [
                {"name": a.name, "archetype": a.archetype or "Agent"}
                for a in target_team.agents
            ]
            embed = build_welcome_embed(target_team.name, target_team.color, agents)
            await interaction.response.send_message(embed=embed)

            # Send welcome DM with quick-start info
            import contextlib

            with contextlib.suppress(discord.Forbidden, discord.HTTPException):
                await interaction.user.send(embed=embed)

        except Exception:
            logger.exception("discord_join_failed")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong joining the team.", ephemeral=True,
                )

    def _get_channel_for(self, key: str) -> discord.TextChannel | None:
        """Resolve a channel by key from channel_ids, falling back to main_channel_id."""
        channel_id = self.channel_ids.get(key) or self.main_channel_id
        if not channel_id:
            return None
        ch = self.get_channel(channel_id)
        if isinstance(ch, discord.TextChannel):
            return ch
        return None

    def _get_team_channel(self, team_id: str) -> discord.TextChannel | None:
        """Resolve a team's private channel from channel_ids."""
        channel_id = self.channel_ids.get(f"team_{team_id}")
        if not channel_id:
            return None
        ch = self.get_channel(channel_id)
        if isinstance(ch, discord.TextChannel):
            return ch
        return None

    async def _send_to_team_channel(
        self, team_id: str, embed: discord.Embed,
    ) -> None:
        """Send an embed to a team's private channel (if available)."""
        ch = self._get_team_channel(team_id)
        if ch:
            import contextlib

            with contextlib.suppress(discord.Forbidden, discord.HTTPException):
                await ch.send(embed=embed)

    async def _dispatch_event(self, event: dict[str, object]) -> None:
        """Route an EventBus event to the appropriate Discord handler."""
        if not self.main_channel_id and not self.channel_ids:
            return

        event_type = str(event.get("type", ""))
        data = event.get("data", {})
        if not isinstance(data, dict):
            return

        if event_type == "game.completed":
            embed = build_game_result_embed(data)
            play_channel = self._get_channel_for("play_by_play")
            if play_channel:
                await play_channel.send(embed=embed)

            # Big plays: Elam activated, blowout (>15 diff), or upset
            is_elam = bool(data.get("elam_activated"))
            home_score = int(data.get("home_score", 0))
            away_score = int(data.get("away_score", 0))
            is_blowout = abs(home_score - away_score) > 15
            if is_elam or is_blowout:
                big_channel = self._get_channel_for("big_plays")
                if big_channel:
                    await big_channel.send(embed=embed)

            # Team-specific results to team channels
            home_id = str(data.get("home_team_id", ""))
            away_id = str(data.get("away_team_id", ""))
            if home_id:
                from pinwheel.discord.embeds import build_team_game_result_embed

                home_embed = build_team_game_result_embed(data, home_id)
                await self._send_to_team_channel(home_id, home_embed)
            if away_id:
                from pinwheel.discord.embeds import build_team_game_result_embed

                away_embed = build_team_game_result_embed(data, away_id)
                await self._send_to_team_channel(away_id, away_embed)

        elif event_type == "round.completed":
            embed = build_round_summary_embed(data)
            play_channel = self._get_channel_for("play_by_play")
            if play_channel:
                await play_channel.send(embed=embed)

        elif event_type == "mirror.generated":
            mirror_type = data.get("mirror_type", "")
            excerpt = str(data.get("excerpt", ""))
            if mirror_type == "private":
                await self._send_private_mirror(data)
            elif excerpt:
                from pinwheel.models.mirror import Mirror

                mirror = Mirror(
                    id="",
                    mirror_type=mirror_type,  # type: ignore[arg-type]
                    round_number=int(data.get("round", 0)),
                    content=excerpt,
                )
                embed = build_mirror_embed(mirror)
                channel = self._get_channel_for("play_by_play")
                if not channel:
                    channel = self._get_channel_for("main")
                if channel:
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
            channel = self._get_channel_for("main")
            if channel:
                await channel.send(embed=embed)
            # Post to all team channels too
            for key, chan_id in self.channel_ids.items():
                if key.startswith("team_"):
                    ch = self.get_channel(chan_id)
                    if isinstance(ch, discord.TextChannel):
                        import contextlib

                        with contextlib.suppress(
                            discord.Forbidden, discord.HTTPException,
                        ):
                            await ch.send(embed=embed)

    # --- Slash command handlers ---

    async def _handle_standings(self, interaction: discord.Interaction) -> None:
        """Handle the /standings slash command."""
        standings = await self._query_standings()
        embed = build_standings_embed(standings)
        await interaction.response.send_message(embed=embed)

    async def _query_standings(self) -> list[dict[str, object]]:
        """Query current standings from the database."""
        if not self.engine:
            return []
        try:
            from sqlalchemy import select

            from pinwheel.core.scheduler import compute_standings
            from pinwheel.db.engine import get_session
            from pinwheel.db.models import SeasonRow
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                result = await session.execute(select(SeasonRow).limit(1))
                season = result.scalar_one_or_none()
                if not season:
                    return []

                all_results: list[dict] = []
                for rn in range(1, 100):
                    games = await repo.get_games_for_round(season.id, rn)
                    if not games:
                        break
                    for g in games:
                        all_results.append({
                            "home_team_id": g.home_team_id,
                            "away_team_id": g.away_team_id,
                            "home_score": g.home_score,
                            "away_score": g.away_score,
                            "winner_team_id": g.winner_team_id,
                        })
                standings = compute_standings(all_results)
                for s in standings:
                    team = await repo.get_team(s["team_id"])
                    if team:
                        s["team_name"] = team.name
                return standings
        except Exception:
            logger.exception("discord_standings_query_failed")
            return []

    async def _handle_propose(self, interaction: discord.Interaction, text: str) -> None:
        """Handle the /propose slash command with AI interpretation."""
        if not text.strip():
            await interaction.response.send_message(
                "You need to describe your rule change proposal. "
                "Example: `/propose Make three-pointers worth 5 points`",
                ephemeral=True,
            )
            return

        if not self.engine:
            await interaction.response.send_message(
                "Database not available. Try again later.", ephemeral=True,
            )
            return

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(self.engine, str(interaction.user.id))
        except GovernorNotFound:
            await interaction.response.send_message(
                "You need to `/join` a team first.", ephemeral=True,
            )
            return

        # Defer — AI interpretation may take a few seconds
        await interaction.response.defer(ephemeral=True)

        try:
            from pinwheel.ai.interpreter import (
                interpret_proposal,
                interpret_proposal_mock,
            )
            from pinwheel.core.governance import (
                detect_tier,
                token_cost_for_tier,
            )
            from pinwheel.core.tokens import get_token_balance, has_token
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository
            from pinwheel.models.rules import RuleSet

            async with get_session(self.engine) as session:
                repo = Repository(session)
                if not await has_token(
                    repo, gov.player_id, gov.season_id, "propose",
                ):
                    await interaction.followup.send(
                        "You don't have any PROPOSE tokens left.",
                        ephemeral=True,
                    )
                    return

                balance = await get_token_balance(
                    repo, gov.player_id, gov.season_id,
                )
                season = await repo.get_season(gov.season_id)
                rs_data = (season.current_ruleset or {}) if season else {}
                ruleset = RuleSet(**rs_data)

            api_key = self.settings.anthropic_api_key
            if api_key:
                from pinwheel.ai.classifier import classify_injection

                classification = await classify_injection(text, api_key)
                if (
                    classification.classification == "injection"
                    and classification.confidence > 0.8
                ):
                    from pinwheel.models.governance import (
                        RuleInterpretation as RI,
                    )

                    interpretation = RI(
                        confidence=0.0,
                        injection_flagged=True,
                        rejection_reason=classification.reason,
                        impact_analysis="Proposal flagged as potential prompt injection.",
                    )
                else:
                    interpretation = await interpret_proposal(
                        text, ruleset, api_key,
                    )
                    if classification.classification == "suspicious":
                        interpretation.impact_analysis = (
                            f"[Suspicious: {classification.reason}] "
                            + interpretation.impact_analysis
                        )
            else:
                interpretation = interpret_proposal_mock(
                    text, ruleset,
                )

            tier = detect_tier(interpretation, ruleset)
            cost = token_cost_for_tier(tier)

            from pinwheel.discord.views import ProposalConfirmView

            view = ProposalConfirmView(
                original_user_id=interaction.user.id,
                raw_text=text,
                interpretation=interpretation,
                tier=tier,
                token_cost=cost,
                tokens_remaining=balance.propose,
                governor_info=gov,
                engine=self.engine,
                settings=self.settings,
            )
            embed = build_interpretation_embed(
                raw_text=text,
                interpretation=interpretation,
                tier=tier,
                token_cost=cost,
                tokens_remaining=balance.propose,
                governor_name=interaction.user.display_name,
            )
            await interaction.followup.send(
                embed=embed, view=view, ephemeral=True,
            )
        except Exception:
            logger.exception("discord_propose_failed")
            await interaction.followup.send(
                "Something went wrong interpreting your proposal.",
                ephemeral=True,
            )

    async def _handle_schedule(self, interaction: discord.Interaction) -> None:
        """Handle the /schedule slash command."""
        schedule, round_number = await self._query_schedule()
        embed = build_schedule_embed(schedule, round_number=round_number)
        await interaction.response.send_message(embed=embed)

    async def _query_schedule(self) -> tuple[list[dict[str, object]], int]:
        """Query the next unplayed round's schedule."""
        if not self.engine:
            return [], 0
        try:
            from sqlalchemy import select

            from pinwheel.db.engine import get_session
            from pinwheel.db.models import SeasonRow
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                result = await session.execute(select(SeasonRow).limit(1))
                season = result.scalar_one_or_none()
                if not season:
                    return [], 0

                # Find the next round that has schedule but no results
                next_round = 1
                for rn in range(1, 100):
                    games = await repo.get_games_for_round(season.id, rn)
                    if games:
                        next_round = rn + 1
                    else:
                        break

                matchups = await repo.get_schedule_for_round(
                    season.id, next_round,
                )
                schedule = []
                for m in matchups:
                    home = await repo.get_team(m.home_team_id)
                    away = await repo.get_team(m.away_team_id)
                    schedule.append({
                        "home_team_name": home.name if home else m.home_team_id,
                        "away_team_name": away.name if away else m.away_team_id,
                    })
                return schedule, next_round
        except Exception:
            logger.exception("discord_schedule_query_failed")
            return [], 0

    async def _handle_mirrors(self, interaction: discord.Interaction) -> None:
        """Handle the /mirrors slash command."""
        mirrors = await self._query_latest_mirrors()
        if not mirrors:
            embed = discord.Embed(
                title="Latest Mirrors",
                description=(
                    "No mirrors have been generated yet. "
                    "Mirrors appear after each round."
                ),
                color=0x9B59B6,
            )
            embed.set_footer(text="Pinwheel Fates")
            await interaction.response.send_message(embed=embed)
            return

        # Send the most recent public mirror
        from pinwheel.models.mirror import Mirror

        m = mirrors[0]
        mirror = Mirror(
            id=m["id"],
            mirror_type=m["mirror_type"],  # type: ignore[arg-type]
            round_number=m["round_number"],
            content=m["content"],
        )
        embed = build_mirror_embed(mirror)
        await interaction.response.send_message(embed=embed)

    async def _query_latest_mirrors(self) -> list[dict]:
        """Query the most recent public mirrors."""
        if not self.engine:
            return []
        try:
            from sqlalchemy import select

            from pinwheel.db.engine import get_session
            from pinwheel.db.models import SeasonRow
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                result = await session.execute(select(SeasonRow).limit(1))
                season = result.scalar_one_or_none()
                if not season:
                    return []

                # Try simulation mirror first, then governance
                for mtype in ("simulation", "governance"):
                    m = await repo.get_latest_mirror(season.id, mtype)
                    if m:
                        return [{
                            "id": m.id,
                            "mirror_type": m.mirror_type,
                            "round_number": m.round_number,
                            "content": m.content,
                        }]
                return []
        except Exception:
            logger.exception("discord_mirrors_query_failed")
            return []

    async def _handle_vote(
        self,
        interaction: discord.Interaction,
        choice: str,
        boost: bool = False,
    ) -> None:
        """Handle the /vote slash command. Votes are hidden until window closes."""
        if not self.engine:
            await interaction.response.send_message(
                "Database not available.", ephemeral=True,
            )
            return

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(self.engine, str(interaction.user.id))
        except GovernorNotFound:
            await interaction.response.send_message(
                "You need to `/join` a team first.", ephemeral=True,
            )
            return

        try:
            from pinwheel.core.governance import (
                cast_vote,
                compute_vote_weight,
            )
            from pinwheel.core.tokens import has_token
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository
            from pinwheel.models.governance import Proposal

            async with get_session(self.engine) as session:
                repo = Repository(session)

                # Find confirmed proposals (open for voting)
                confirmed = await repo.get_events_by_type(
                    season_id=gov.season_id,
                    event_types=["proposal.confirmed"],
                )
                if not confirmed:
                    await interaction.response.send_message(
                        "No proposals are currently open for voting.",
                        ephemeral=True,
                    )
                    return

                latest = confirmed[-1]
                proposal_id = latest.payload.get(
                    "proposal_id", latest.aggregate_id,
                )

                # Reconstruct proposal from submitted event
                submitted = await repo.get_events_by_type(
                    season_id=gov.season_id,
                    event_types=["proposal.submitted"],
                )
                proposal_data = None
                for evt in submitted:
                    if evt.aggregate_id == proposal_id:
                        proposal_data = evt.payload
                        break

                if not proposal_data:
                    await interaction.response.send_message(
                        "Could not find the proposal.",
                        ephemeral=True,
                    )
                    return

                proposal = Proposal(**proposal_data)

                # Check if already voted on this proposal
                my_votes = await repo.get_events_by_type_and_governor(
                    season_id=gov.season_id,
                    governor_id=gov.player_id,
                    event_types=["vote.cast"],
                )
                for v in my_votes:
                    if v.payload.get("proposal_id") == proposal_id:
                        await interaction.response.send_message(
                            "You've already voted on this proposal.",
                            ephemeral=True,
                        )
                        return

                # Check boost token if requested
                if boost and not await has_token(
                    repo, gov.player_id, gov.season_id, "boost",
                ):
                    await interaction.response.send_message(
                        "You don't have any BOOST tokens.",
                        ephemeral=True,
                    )
                    return

                # Compute vote weight
                team_players = await repo.get_players_for_team(
                    gov.team_id,
                )
                active_count = len(team_players) or 1
                weight = compute_vote_weight(active_count)

                await cast_vote(
                    repo=repo,
                    proposal=proposal,
                    governor_id=gov.player_id,
                    team_id=gov.team_id,
                    vote_choice=choice,
                    weight=weight,
                    boost_used=boost,
                )
                await session.commit()

            boost_note = " (BOOSTED)" if boost else ""
            embed = discord.Embed(
                title="Vote Recorded",
                description=(
                    f"Your **{choice.upper()}**{boost_note} vote on "
                    f'"{proposal.raw_text[:80]}" has been recorded.\n\n'
                    "Votes are hidden until the governance "
                    "window closes."
                ),
                color=0x3498DB,
            )
            embed.set_footer(text="Pinwheel Fates")
            await interaction.response.send_message(
                embed=embed, ephemeral=True,
            )
        except Exception:
            logger.exception("discord_vote_failed")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong recording your vote.",
                    ephemeral=True,
                )

    async def _handle_tokens(
        self, interaction: discord.Interaction,
    ) -> None:
        """Handle the /tokens slash command."""
        if not self.engine:
            await interaction.response.send_message(
                "Database not available.", ephemeral=True,
            )
            return

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(self.engine, str(interaction.user.id))
        except GovernorNotFound:
            await interaction.response.send_message(
                "You need to `/join` a team first.", ephemeral=True,
            )
            return

        try:
            from pinwheel.core.tokens import get_token_balance
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                balance = await get_token_balance(
                    repo, gov.player_id, gov.season_id,
                )

            embed = build_token_balance_embed(
                balance,
                governor_name=interaction.user.display_name,
            )
            await interaction.response.send_message(
                embed=embed, ephemeral=True,
            )
        except Exception:
            logger.exception("discord_tokens_failed")
            await interaction.response.send_message(
                "Something went wrong checking your tokens.",
                ephemeral=True,
            )

    async def _handle_trade(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        offer_type: str,
        offer_amount: int,
        request_type: str,
        request_amount: int,
    ) -> None:
        """Handle the /trade slash command."""
        if not self.engine:
            await interaction.response.send_message(
                "Database not available.", ephemeral=True,
            )
            return

        if target.id == interaction.user.id:
            await interaction.response.send_message(
                "You can't trade with yourself.", ephemeral=True,
            )
            return

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(
                self.engine, str(interaction.user.id),
            )
        except GovernorNotFound:
            await interaction.response.send_message(
                "You need to `/join` a team first.", ephemeral=True,
            )
            return

        try:
            target_gov = await get_governor(
                self.engine, str(target.id),
            )
        except GovernorNotFound:
            await interaction.response.send_message(
                f"{target.display_name} is not enrolled.",
                ephemeral=True,
            )
            return

        try:
            from pinwheel.core.tokens import get_token_balance, offer_trade
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                balance = await get_token_balance(
                    repo, gov.player_id, gov.season_id,
                )
                available = getattr(balance, offer_type, 0)
                if available < offer_amount:
                    await interaction.response.send_message(
                        f"You only have {available} "
                        f"{offer_type.upper()} tokens.",
                        ephemeral=True,
                    )
                    return

                trade = await offer_trade(
                    repo=repo,
                    from_governor=gov.player_id,
                    from_team_id=gov.team_id,
                    to_governor=target_gov.player_id,
                    to_team_id=target_gov.team_id,
                    season_id=gov.season_id,
                    offered_type=offer_type,
                    offered_amount=offer_amount,
                    requested_type=request_type,
                    requested_amount=request_amount,
                )
                await session.commit()

            from pinwheel.discord.views import TradeOfferView

            view = TradeOfferView(
                trade=trade,
                target_user_id=target.id,
                from_name=interaction.user.display_name,
                to_name=target.display_name,
                season_id=gov.season_id,
                engine=self.engine,
            )
            embed = build_trade_offer_embed(
                trade,
                interaction.user.display_name,
                target.display_name,
            )

            with contextlib.suppress(discord.Forbidden):
                await target.send(embed=embed, view=view)

            await interaction.response.send_message(
                f"Trade offer sent to "
                f"**{target.display_name}**!",
                ephemeral=True,
            )
        except Exception:
            logger.exception("discord_trade_failed")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong with the trade.",
                    ephemeral=True,
                )

    async def _autocomplete_agents(
        self, interaction: discord.Interaction, current: str, *, own_team: bool,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete agent names for trade-agent command."""
        if not self.engine:
            return []
        try:
            from pinwheel.discord.helpers import get_governor

            gov = await get_governor(self.engine, str(interaction.user.id))

            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                if own_team:
                    agents = await repo.get_agents_for_team(gov.team_id)
                else:
                    teams = await repo.get_teams_for_season(gov.season_id)
                    agents = []
                    for t in teams:
                        if t.id != gov.team_id:
                            team_agents = await repo.get_agents_for_team(t.id)
                            agents.extend(team_agents)
                lowered = current.lower()
                return [
                    app_commands.Choice(name=a.name, value=a.name)
                    for a in agents
                    if lowered in a.name.lower()
                ][:25]
        except Exception:
            logger.exception("agent_autocomplete_failed")
            return []

    async def _handle_trade_agent(
        self,
        interaction: discord.Interaction,
        offer_agent_name: str,
        request_agent_name: str,
    ) -> None:
        """Handle the /trade-agent slash command."""
        if not self.engine:
            await interaction.response.send_message(
                "Database not available.", ephemeral=True,
            )
            return

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(self.engine, str(interaction.user.id))
        except GovernorNotFound:
            await interaction.response.send_message(
                "You must `/join` a team before trading agents.",
                ephemeral=True,
            )
            return

        try:
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)

                # Find the offered agent (must be on proposer's team)
                my_agents = await repo.get_agents_for_team(gov.team_id)
                offered = None
                for a in my_agents:
                    if a.name.lower() == offer_agent_name.lower():
                        offered = a
                        break
                if not offered:
                    available = ", ".join(a.name for a in my_agents)
                    await interaction.response.send_message(
                        f"Agent not found on your team. Your agents: {available}",
                        ephemeral=True,
                    )
                    return

                # Find the requested agent (must be on a different team)
                teams = await repo.get_teams_for_season(gov.season_id)
                requested = None
                target_team = None
                for t in teams:
                    if t.id == gov.team_id:
                        continue
                    for a in t.agents:
                        if a.name.lower() == request_agent_name.lower():
                            requested = a
                            target_team = t
                            break
                    if requested:
                        break
                if not requested or not target_team:
                    await interaction.response.send_message(
                        f"Agent '{request_agent_name}' not found on any other team.",
                        ephemeral=True,
                    )
                    return

                # Get all governors on both teams
                from_govs = await repo.get_governors_for_team(
                    gov.team_id, gov.season_id,
                )
                to_govs = await repo.get_governors_for_team(
                    target_team.id, gov.season_id,
                )
                all_voters = [p.discord_id for p in from_govs] + [
                    p.discord_id for p in to_govs
                ]

                if len(all_voters) < 2:
                    await interaction.response.send_message(
                        "Both teams need at least one governor to vote on a trade.",
                        ephemeral=True,
                    )
                    return

                from pinwheel.core.tokens import propose_agent_trade

                my_team = next(
                    (t for t in teams if t.id == gov.team_id), None,
                )
                trade = await propose_agent_trade(
                    repo=repo,
                    proposer_id=str(interaction.user.id),
                    from_team_id=gov.team_id,
                    to_team_id=target_team.id,
                    offered_agent_ids=[offered.id],
                    requested_agent_ids=[requested.id],
                    offered_agent_names=[offered.name],
                    requested_agent_names=[requested.name],
                    from_team_name=my_team.name if my_team else gov.team_id,
                    to_team_name=target_team.name,
                    required_voters=all_voters,
                    season_id=gov.season_id,
                )
                await session.commit()

            # Post trade view to both team channels
            from pinwheel.discord.embeds import build_agent_trade_embed
            from pinwheel.discord.views import AgentTradeView

            view = AgentTradeView(
                trade=trade, season_id=gov.season_id, engine=self.engine,
            )
            embed = build_agent_trade_embed(
                from_team=trade.from_team_name,
                to_team=trade.to_team_name,
                offered_names=trade.offered_agent_names,
                requested_names=trade.requested_agent_names,
                proposer_name=interaction.user.display_name,
                votes_cast=0,
                votes_needed=len(all_voters),
            )

            # Send to both team channels
            from_ch = self._get_team_channel(gov.team_id)
            to_ch = self._get_team_channel(target_team.id)
            if from_ch:
                await from_ch.send(embed=embed, view=view)
            if to_ch:
                # New view instance for second channel (views can't be reused)
                view2 = AgentTradeView(
                    trade=trade, season_id=gov.season_id, engine=self.engine,
                )
                await to_ch.send(embed=embed, view=view2)

            await interaction.response.send_message(
                f"Agent trade proposed: **{offered.name}** "
                f"for **{requested.name}**. "
                "Both teams' governors must vote in their team channels.",
                ephemeral=True,
            )
        except Exception:
            logger.exception("discord_trade_agent_failed")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong with the trade.", ephemeral=True,
                )

    async def _handle_strategy(
        self, interaction: discord.Interaction, text: str,
    ) -> None:
        """Handle the /strategy slash command."""
        if not text.strip():
            await interaction.response.send_message(
                "Describe your team's strategy. "
                "Example: `/strategy Focus on three-point shooting`",
                ephemeral=True,
            )
            return

        if not self.engine:
            await interaction.response.send_message(
                "Database not available.", ephemeral=True,
            )
            return

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(
                self.engine, str(interaction.user.id),
            )
        except GovernorNotFound:
            await interaction.response.send_message(
                "You need to `/join` a team first.", ephemeral=True,
            )
            return

        from pinwheel.discord.views import StrategyConfirmView

        view = StrategyConfirmView(
            original_user_id=interaction.user.id,
            raw_text=text,
            team_name=gov.team_name,
            governor_info=gov,
            engine=self.engine,
        )
        embed = build_strategy_embed(text, gov.team_name)
        embed.set_footer(
            text="Pinwheel Fates -- Confirm or Cancel",
        )
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True,
        )

    async def _send_private_mirror(self, data: dict) -> None:
        """DM a private mirror to the governor."""
        governor_id = str(data.get("governor_id", ""))
        excerpt = str(data.get("excerpt", ""))
        round_num = int(data.get("round", 0))
        if not governor_id or not excerpt or not self.engine:
            return

        try:
            from pinwheel.db.engine import get_session
            from pinwheel.db.models import PlayerRow

            async with get_session(self.engine) as session:
                player = await session.get(PlayerRow, governor_id)
                if not player:
                    return
                discord_id = player.discord_id

            user = self.get_user(int(discord_id))
            if user is None:
                user = await self.fetch_user(int(discord_id))

            from pinwheel.models.mirror import Mirror

            mirror = Mirror(
                id=str(data.get("mirror_id", "")),
                mirror_type="private",
                round_number=round_num,
                content=excerpt,
            )
            embed = build_mirror_embed(mirror)
            embed.title = f"Private Mirror -- Round {round_num}"
            await user.send(embed=embed)
        except Exception:
            logger.exception(
                "private_mirror_dm_failed governor=%s",
                governor_id,
            )

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


async def start_discord_bot(
    settings: Settings,
    event_bus: EventBus,
    engine: AsyncEngine | None = None,
) -> PinwheelBot:
    """Create and start the Discord bot in the current event loop.

    Returns the bot instance so the caller can stop it during shutdown.
    The bot runs as a background task; this function returns immediately
    after starting it.
    """
    bot = PinwheelBot(settings=settings, event_bus=event_bus, engine=engine)

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
