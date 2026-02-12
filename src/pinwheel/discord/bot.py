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
        @app_commands.describe(team="The team name to join")
        async def join_command(interaction: discord.Interaction, team: str) -> None:
            await self._handle_join(interaction, team)

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

        Idempotent: looks up existing channels/roles by name before creating.
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

        # --- Get or create category ---
        category_name = "PINWHEEL FATES"
        category = discord.utils.get(guild.categories, name=category_name)
        if category is None:
            category = await guild.create_category(category_name)
            logger.info("discord_setup_created_category name=%s", category_name)

        # --- Get or create shared channels ---
        channel_defs = [
            ("how-to-play", "Learn how to play Pinwheel Fates"),
            ("play-by-play", "Live game updates"),
            ("big-plays", "Highlights -- Elam endings, upsets, blowouts"),
        ]
        for ch_name, ch_topic in channel_defs:
            existing = discord.utils.get(guild.text_channels, name=ch_name, category=category)
            if existing is None:
                existing = await guild.create_text_channel(
                    ch_name, category=category, topic=ch_topic,
                )
                logger.info("discord_setup_created_channel name=%s", ch_name)
            key = ch_name.replace("-", "_")
            self.channel_ids[key] = existing.id

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
                            slug = team.name.lower().replace(" ", "-")

                            # Role
                            role = discord.utils.get(guild.roles, name=team.name)
                            if role is None:
                                color = discord.Color(int(team.color.lstrip("#"), 16))
                                role = await guild.create_role(name=team.name, color=color)
                                logger.info("discord_setup_created_role name=%s", team.name)

                            # Team channel (private: only team role + bot can see)
                            team_ch = discord.utils.get(
                                guild.text_channels, name=slug, category=category,
                            )
                            if team_ch is None:
                                deny = discord.PermissionOverwrite(
                                    read_messages=False,
                                )
                                allow = discord.PermissionOverwrite(
                                    read_messages=True,
                                    send_messages=True,
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
                                    topic=f"Team channel for {team.name}",
                                )
                                logger.info("discord_setup_created_team_channel name=%s", slug)
                            self.channel_ids[f"team_{team.id}"] = team_ch.id
            except Exception:
                logger.exception("discord_setup_team_channels_failed")

        # --- Post welcome message if #how-to-play is empty ---
        await self._post_welcome_message(guild)

        logger.info("discord_setup_complete channels=%s", list(self.channel_ids.keys()))

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

                # Check existing enrollment
                discord_id = str(interaction.user.id)
                enrollment = await repo.get_player_enrollment(discord_id, season.id)
                if enrollment is not None:
                    existing_team_id, existing_team_name = enrollment
                    # Already enrolled -- check if same team
                    if existing_team_name.lower() == team_name.lower():
                        await interaction.response.send_message(
                            f"You're already on **{existing_team_name}**!",
                            ephemeral=True,
                        )
                    else:
                        await interaction.response.send_message(
                            f"You're locked in with **{existing_team_name}** for this season.",
                            ephemeral=True,
                        )
                    return

                # Find the requested team
                teams = await repo.get_teams_for_season(season.id)
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
                role = discord.utils.get(interaction.guild.roles, name=target_team.name)
                if role and isinstance(interaction.user, discord.Member):
                    await interaction.user.add_roles(role)

            # Build confirmation embed
            agent_names = ", ".join(a.name for a in target_team.agents)
            embed = discord.Embed(
                title=f"Welcome to {target_team.name}!",
                description=(
                    f"You are now a governor of **{target_team.name}**.\n\n"
                    f"**Agents:** {agent_names}\n\n"
                    "You're locked in for this season. Lead wisely."
                ),
                color=discord.Color(int(target_team.color.lstrip("#"), 16)),
            )
            embed.set_footer(text="Pinwheel Fates")
            await interaction.response.send_message(embed=embed)

        except Exception:
            logger.exception("discord_join_failed")
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
                interpretation = await interpret_proposal(
                    text, ruleset, api_key,
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
