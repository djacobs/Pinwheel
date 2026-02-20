"""Discord bot for Pinwheel Fates.

Runs alongside FastAPI using the same event loop. Subscribes to EventBus
for real-time game updates and posts results, governance outcomes, and
reports to configured channels.

The bot is optional: if DISCORD_BOT_TOKEN is not set, nothing starts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
from discord import Intents, app_commands
from discord.ext import commands
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.discord.embeds import (
    build_amendment_confirm_embed,
    build_game_result_embed,
    build_governor_profile_embed,
    build_history_list_embed,
    build_interpretation_embed,
    build_memorial_embed,
    build_onboarding_embed,
    build_report_embed,
    build_roster_embed,
    build_round_summary_embed,
    build_schedule_embed,
    build_search_result_embed,
    build_standings_embed,
    build_strategy_embed,
    build_token_balance_embed,
    build_trade_offer_embed,
    build_vote_tally_embed,
)

if TYPE_CHECKING:
    from pinwheel.config import Settings
    from pinwheel.core.event_bus import EventBus

logger = logging.getLogger(__name__)

DISCORD_SETUP_LOCK_KEY = "discord_setup_lock"
DISCORD_SETUP_LOCK_TIMEOUT_SECONDS = 60  # 1 minute — setup is fast

# System-level cooldown between proposals per governor (seconds).
# NOT governable — protects against rapid-fire submissions that waste AI interpreter capacity.
PROPOSAL_COOLDOWN_SECONDS = 60

# Cooldown between /ask queries per user (seconds).
ASK_COOLDOWN_SECONDS = 10


class PinwheelBot(commands.Bot):
    """The Pinwheel Fates Discord bot.

    Runs in-process with FastAPI. Subscribes to EventBus events and
    posts updates to the configured Discord channel. Provides slash
    commands for standings, proposals, schedule, and reports.
    """

    def __init__(
        self,
        settings: Settings,
        event_bus: EventBus,
        engine: AsyncEngine | None = None,
    ) -> None:
        intents = Intents.default()
        intents.message_content = True
        intents.members = True  # Required for role-enrollment self-heal on startup

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
        self._team_names_cache: list[str] = []
        self._event_listener_task: asyncio.Task[None] | None = None
        self._setup_done: bool = False
        # Per-governor cooldown: governor_id → last proposal timestamp (monotonic)
        self._proposal_cooldowns: dict[str, float] = {}
        # Per-user cooldown for /ask queries (discord_user_id → monotonic timestamp)
        self._ask_cooldowns: dict[str, float] = {}
        self._setup_commands()

    def _setup_commands(self) -> None:
        """Register slash commands on the bot's command tree."""

        @self.tree.command(name="standings", description="View current league standings")
        async def standings_command(interaction: discord.Interaction) -> None:
            await self._handle_standings(interaction)

        @self.tree.command(name="propose", description="Put a rule change on the Floor")
        @app_commands.describe(text="Your rule change proposal in natural language")
        async def propose_command(interaction: discord.Interaction, text: str) -> None:
            await self._handle_propose(interaction, text)

        @self.tree.command(name="schedule", description="View the upcoming game schedule")
        async def schedule_command(interaction: discord.Interaction) -> None:
            await self._handle_schedule(interaction)

        @self.tree.command(name="reports", description="View the latest AI reports")
        async def reports_command(interaction: discord.Interaction) -> None:
            await self._handle_reports(interaction)

        @self.tree.command(name="join", description="Join a team as a governor for this season")
        @app_commands.describe(team="The team name to join")
        async def join_command(
            interaction: discord.Interaction,
            team: str,
        ) -> None:
            await self._handle_join(interaction, team)

        @join_command.autocomplete("team")
        async def _team_autocomplete(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            return await self._autocomplete_teams(current)

        @self.tree.command(
            name="vote",
            description="Vote on a proposal on the Floor",
        )
        @app_commands.describe(
            choice="Your vote: yes or no",
            boost="Use a BOOST token to double your vote weight",
            proposal="Which proposal to vote on (defaults to latest)",
        )
        @app_commands.choices(
            choice=[
                app_commands.Choice(name="Yes", value="yes"),
                app_commands.Choice(name="No", value="no"),
            ]
        )
        async def vote_command(
            interaction: discord.Interaction,
            choice: app_commands.Choice[str],
            boost: bool = False,
            proposal: str = "",
        ) -> None:
            await self._handle_vote(interaction, choice.value, boost, proposal)

        @vote_command.autocomplete("proposal")
        async def _proposal_autocomplete(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            return await self._autocomplete_proposals(interaction, current)

        @self.tree.command(
            name="amend",
            description="Propose an amendment to an active proposal on the Floor",
        )
        @app_commands.describe(
            proposal="Which proposal to amend",
            text="Your amendment in natural language",
        )
        async def amend_command(
            interaction: discord.Interaction,
            proposal: str,
            text: str,
        ) -> None:
            await self._handle_amend(interaction, proposal, text)

        @amend_command.autocomplete("proposal")
        async def _amend_proposal_autocomplete(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            return await self._autocomplete_proposals(interaction, current)

        @self.tree.command(
            name="tokens",
            description="Check your Floor token balance",
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
                interaction,
                target,
                offer_type.value,
                offer_amount,
                request_type.value,
                request_amount,
            )

        @self.tree.command(
            name="trade-hooper",
            description="Propose trading hoopers between two teams",
        )
        @app_commands.describe(
            offer_hooper="Name of the hooper you're offering",
            request_hooper="Name of the hooper you want in return",
        )
        async def trade_hooper_command(
            interaction: discord.Interaction,
            offer_hooper: str,
            request_hooper: str,
        ) -> None:
            await self._handle_trade_hooper(interaction, offer_hooper, request_hooper)

        @trade_hooper_command.autocomplete("offer_hooper")
        async def _offer_hooper_autocomplete(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            return await self._autocomplete_hoopers(interaction, current, own_team=True)

        @trade_hooper_command.autocomplete("request_hooper")
        async def _request_hooper_autocomplete(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            return await self._autocomplete_hoopers(interaction, current, own_team=False)

        @self.tree.command(
            name="strategy",
            description="Set your team's strategic direction",
        )
        @app_commands.describe(
            text="Your team's strategy in natural language",
        )
        async def strategy_command(
            interaction: discord.Interaction,
            text: str,
        ) -> None:
            await self._handle_strategy(interaction, text)

        @self.tree.command(
            name="bio",
            description="Write a backstory for one of your team's hoopers",
        )
        @app_commands.describe(
            hooper="The hooper to write a bio for",
            text="The backstory text",
        )
        async def bio_command(
            interaction: discord.Interaction,
            hooper: str,
            text: str,
        ) -> None:
            await self._handle_bio(interaction, hooper, text)

        @bio_command.autocomplete("hooper")
        async def _bio_hooper_autocomplete(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            return await self._autocomplete_hoopers(
                interaction,
                current,
                own_team=True,
            )

        @self.tree.command(
            name="profile",
            description="View your governor profile and Floor record",
        )
        async def profile_command(
            interaction: discord.Interaction,
        ) -> None:
            await self._handle_profile(interaction)

        @self.tree.command(
            name="new-season",
            description="Start a new season (admin only)",
        )
        @app_commands.describe(
            name="Name for the new season",
            carry_rules="Carry forward rules from last season (default: yes)",
        )
        async def new_season_command(
            interaction: discord.Interaction,
            name: str,
            carry_rules: bool = True,
        ) -> None:
            await self._handle_new_season(interaction, name, carry_rules)

        @self.tree.command(
            name="proposals",
            description="View all proposals and their status",
        )
        @app_commands.describe(
            season="Which season to show (default: current)",
        )
        @app_commands.choices(
            season=[
                app_commands.Choice(name="Current season", value="current"),
                app_commands.Choice(name="All seasons", value="all"),
            ]
        )
        async def proposals_command(
            interaction: discord.Interaction,
            season: str = "current",
        ) -> None:
            await self._handle_proposals(interaction, season)

        @self.tree.command(
            name="roster",
            description="View all enrolled governors for this season",
        )
        async def roster_command(
            interaction: discord.Interaction,
        ) -> None:
            await self._handle_roster(interaction)

        @self.tree.command(
            name="effects",
            description="View all active game effects for the current season",
        )
        async def effects_command(
            interaction: discord.Interaction,
        ) -> None:
            await self._handle_effects(interaction)

        @self.tree.command(
            name="repeal",
            description="Propose repealing an active game effect",
        )
        @app_commands.describe(
            effect="The effect to repeal (select from active effects)",
        )
        async def repeal_command(
            interaction: discord.Interaction,
            effect: str,
        ) -> None:
            await self._handle_repeal(interaction, effect)

        @repeal_command.autocomplete("effect")
        async def _effect_autocomplete(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            return await self._autocomplete_effects(current)

        @self.tree.command(
            name="status",
            description="Get a briefing on the current state of the league",
        )
        async def status_command(
            interaction: discord.Interaction,
        ) -> None:
            await self._handle_status(interaction)

        @self.tree.command(
            name="history",
            description="View past season memorials",
        )
        @app_commands.describe(
            season="Name of a specific season to view (optional)",
        )
        async def history_command(
            interaction: discord.Interaction,
            season: str = "",
        ) -> None:
            await self._handle_history(interaction, season)

        @self.tree.command(
            name="ask",
            description="Ask anything about the league -- stats, standings, games, rules",
        )
        @app_commands.describe(
            question="Your question in natural language",
        )
        async def ask_command(
            interaction: discord.Interaction,
            question: str,
        ) -> None:
            await self._handle_ask(interaction, question)

        @self.tree.command(
            name="edit-series",
            description="Collaboratively edit a playoff series report",
        )
        @app_commands.describe(
            report="Which series report to edit",
        )
        async def edit_series_command(
            interaction: discord.Interaction,
            report: str,
        ) -> None:
            await self._handle_edit_series(interaction, report)

        @edit_series_command.autocomplete("report")
        async def _series_report_autocomplete(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            return await self._autocomplete_series_reports(interaction, current)

        @self.tree.command(
            name="activate-mechanic",
            description="Activate a pending custom mechanic (admin only)",
        )
        @app_commands.describe(
            effect="The pending mechanic to activate",
            hook_point="Hook point for the real implementation (optional)",
            action_type="Action type: modify_score, modify_probability, modify_stamina (optional)",
            modifier="Numeric modifier for the action (optional)",
        )
        async def activate_mechanic_command(
            interaction: discord.Interaction,
            effect: str,
            hook_point: str = "",
            action_type: str = "",
            modifier: float = 0.0,
        ) -> None:
            await self._handle_activate_mechanic(
                interaction, effect, hook_point, action_type, modifier,
            )

        @activate_mechanic_command.autocomplete("effect")
        async def _mechanic_autocomplete(
            interaction: discord.Interaction,
            current: str,
        ) -> list[app_commands.Choice[str]]:
            return await self._autocomplete_pending_mechanics(interaction, current)

    async def setup_hook(self) -> None:
        """Called when the bot is ready to start. Syncs slash commands."""
        if self.settings.discord_guild_id:
            guild = discord.Object(id=int(self.settings.discord_guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("discord_commands_synced guild_id=%s", self.settings.discord_guild_id)
        else:
            await self.tree.sync()
            logger.info("discord_commands_synced globally")

    async def on_ready(self) -> None:
        """Called when the bot has connected to Discord.

        on_ready fires on every reconnect, not just the first connection.
        Guard setup to run only once to prevent duplicate channel creation.
        """
        user = self.user
        name = user.name if user else "unknown"
        logger.info("discord_bot_ready user=%s", name)
        if not self._setup_done:
            await self._setup_server()
            self._setup_done = True
        if self._event_listener_task is None or self._event_listener_task.done():
            self._event_listener_task = asyncio.create_task(
                self._listen_to_event_bus(), name="discord-event-listener"
            )

    async def on_member_join(self, member: discord.Member) -> None:
        """Send a first-touch welcome DM when someone joins the Discord server."""
        if member.bot:
            return
        try:
            from pinwheel.discord.embeds import build_server_welcome_embed

            embed = build_server_welcome_embed()
            await member.send(embed=embed)
            logger.info("server_welcome_dm_sent user=%s", member.display_name)
        except (discord.Forbidden, discord.HTTPException) as exc:
            # DMs disabled or other issue — non-fatal
            logger.info(
                "server_welcome_dm_failed user=%s err=%s",
                member.display_name,
                exc,
            )

    async def _listen_to_event_bus(self) -> None:
        """Subscribe to EventBus and forward events to Discord channels."""
        async with self.event_bus.subscribe(None) as subscription:
            async for event in subscription:
                try:
                    await self._dispatch_event(event)
                except Exception:  # Last-resort handler — all exceptions logged above
                    logger.exception("discord_event_dispatch_error event=%s", event.get("type"))

    async def _try_acquire_setup_lock(self) -> bool:
        """Try to acquire the Discord setup lock via DB. Returns True if acquired.

        Uses an atomic INSERT OR IGNORE to avoid the TOCTOU race where two
        instances both read "no lock" before either writes. The insert
        succeeds (rowcount=1) for exactly one writer; the other gets
        rowcount=0 and backs off. Expired locks (older than the timeout)
        are deleted first so a crashed instance doesn't hold the lock forever.
        """
        if not self.engine:
            return True  # No DB — single-instance, proceed
        try:
            from sqlalchemy import text as sa_text

            from pinwheel.db.engine import get_session

            async with get_session(self.engine) as session:
                # Expire stale locks (e.g. from a crashed prior instance)
                cutoff = time.time() - DISCORD_SETUP_LOCK_TIMEOUT_SECONDS
                await session.execute(
                    sa_text(
                        "DELETE FROM bot_state "
                        "WHERE key = :key AND json_extract(value, '$.acquired_at') < :cutoff"
                    ),
                    {"key": DISCORD_SETUP_LOCK_KEY, "cutoff": cutoff},
                )
                # Atomic insert — only one writer succeeds.
                # Must include updated_at (NOT NULL, no SQL-level default).
                now = time.time()
                result = await session.execute(
                    sa_text(
                        "INSERT OR IGNORE INTO bot_state (key, value, updated_at) "
                        "VALUES (:key, :value, :updated_at)"
                    ),
                    {
                        "key": DISCORD_SETUP_LOCK_KEY,
                        "value": json.dumps({"acquired_at": now}),
                        "updated_at": datetime.fromtimestamp(now, tz=UTC).isoformat(),
                    },
                )
                # Capture rowcount before commit (cursor may be invalidated after)
                acquired = result.rowcount > 0  # type: ignore[union-attr]
                await session.commit()
                return acquired
        except SQLAlchemyError:
            logger.exception("discord_setup_lock_acquire_failed")
            return True  # On error, proceed (better than deadlocking)

    async def _release_setup_lock(self) -> None:
        """Release the Discord setup lock."""
        if not self.engine:
            return
        try:
            from pinwheel.db.engine import get_session
            from pinwheel.db.models import BotStateRow

            async with get_session(self.engine) as session:
                row = await session.get(BotStateRow, DISCORD_SETUP_LOCK_KEY)
                if row:
                    await session.delete(row)
                    await session.flush()
        except SQLAlchemyError:
            logger.exception("discord_setup_lock_release_failed")

    async def _setup_server(self) -> None:
        """Create channels, roles, and post welcome message on bot startup.

        Idempotent: loads persisted channel IDs from bot_state, validates
        they still exist in the guild, creates anything missing, and
        persists all IDs back. Safe to call on every restart.

        Uses a DB-level lock to prevent multiple Fly.io machines from
        racing to create channels concurrently.

        Uses guild.fetch_channels() (API call) instead of the local cache
        to avoid duplicates when on_ready fires before the cache is fully
        populated.
        """
        if not self.settings.discord_guild_id:
            return

        # Distributed lock: only one bot instance runs setup at a time
        if not await self._try_acquire_setup_lock():
            logger.info("discord_setup_skip: lock held by another instance")
            # Still load persisted channel IDs so this instance can post to them
            await self._load_persisted_channel_ids()
            return

        try:
            guild = self.get_guild(int(self.settings.discord_guild_id))
            if guild is None:
                logger.warning(
                    "discord_setup_guild_not_found guild_id=%s",
                    self.settings.discord_guild_id,
                )
                return

            # Fetch the real channel list from the Discord API.
            # The local cache (guild.text_channels / guild.categories) may be
            # incomplete when on_ready fires — Discord sends guild data in
            # chunks and the cache can lag behind. Fetching from the API
            # guarantees a complete picture and prevents duplicate creation.
            all_channels = await guild.fetch_channels()

            # --- Load persisted channel IDs from DB ---
            await self._load_persisted_channel_ids()

            # --- Get or create category ---
            category_name = "PINWHEEL FATES"
            category = discord.utils.get(
                [c for c in all_channels if isinstance(c, discord.CategoryChannel)],
                name=category_name,
            )
            if category is None:
                try:
                    category = await guild.create_category(category_name)
                    logger.info("discord_setup_created category=%s", category_name)
                except discord.HTTPException:
                    logger.exception("discord_setup_category_failed name=%s", category_name)
                    return

            # Build a list of text channels from the fetched data for lookups
            text_channels = [
                c for c in all_channels if isinstance(c, discord.TextChannel)
            ]

            # --- Get or create shared channels ---
            channel_defs = [
                ("how-to-play", "Learn how to play Pinwheel Fates"),
                ("play-by-play", "Live game updates"),
                ("big-plays", "Highlights -- Elam endings, upsets, blowouts"),
            ]
            for ch_name, ch_topic in channel_defs:
                key = ch_name.replace("-", "_")
                channel = await self._get_or_create_shared_channel(
                    guild,
                    category,
                    ch_name,
                    ch_topic,
                    key,
                    text_channels,
                )
                if channel is not None:
                    self.channel_ids[key] = channel.id
                    await self._persist_bot_state(f"channel_{key}", str(channel.id))

            # --- Get or create team channels + roles ---
            if self.engine:
                try:

                    from pinwheel.db.engine import get_session
                    from pinwheel.db.repository import Repository

                    async with get_session(self.engine) as session:
                        repo = Repository(session)
                        season = await repo.get_active_season()
                        if season:
                            teams = await repo.get_teams_for_season(season.id)
                            self._team_names_cache = [t.name for t in teams]
                            current_team_keys = set()
                            for team in teams:
                                await self._setup_team_channel_and_role(
                                    guild,
                                    category,
                                    team,
                                    text_channels,
                                )
                                current_team_keys.add(f"team_{team.id}")

                            # Prune stale team_* entries from previous seasons
                            stale_keys = [
                                k for k in list(self.channel_ids)
                                if k.startswith("team_") and k not in current_team_keys
                            ]
                            for sk in stale_keys:
                                del self.channel_ids[sk]
                                await self._persist_bot_state_delete(f"channel_{sk}")
                            if stale_keys:
                                logger.info(
                                    "discord_setup_pruned_stale_team_channels count=%d keys=%s",
                                    len(stale_keys),
                                    stale_keys,
                                )
                except (discord.HTTPException, SQLAlchemyError):
                    logger.exception("discord_setup_team_channels_failed")

            # --- Self-heal: re-enroll members with team roles missing from DB ---
            await self._sync_role_enrollments(guild)

            # --- Post welcome message if #how-to-play is empty ---
            await self._post_welcome_message(guild)

            logger.info("discord_setup_complete channels=%s", list(self.channel_ids.keys()))
        finally:
            await self._release_setup_lock()

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
        except SQLAlchemyError:
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
        except SQLAlchemyError:
            logger.exception("discord_setup_persist_state_failed key=%s", key)

    async def _persist_bot_state_delete(self, key: str) -> None:
        """Delete a bot state key from the database."""
        if not self.engine:
            return
        try:
            from pinwheel.db.engine import get_session
            from pinwheel.db.models import BotStateRow

            async with get_session(self.engine) as session:
                row = await session.get(BotStateRow, key)
                if row:
                    await session.delete(row)
                    await session.flush()
        except SQLAlchemyError:
            logger.exception("discord_setup_delete_state_failed key=%s", key)

    async def _get_or_create_shared_channel(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        ch_name: str,
        ch_topic: str,
        key: str,
        text_channels: list[discord.TextChannel],
    ) -> discord.TextChannel | None:
        """Find or create a shared (public) text channel.

        Checks: persisted ID -> API-fetched channel list by name -> create new.
        Shared channels grant @everyone read access.

        Uses the pre-fetched ``text_channels`` list (from guild.fetch_channels)
        instead of the local guild cache, which may be incomplete when on_ready
        fires before Discord has finished populating it.
        """
        # 1. Check if persisted ID still valid in guild
        persisted_id = self.channel_ids.get(key)
        if persisted_id:
            existing = discord.utils.get(text_channels, id=persisted_id)
            if existing is not None:
                logger.info("discord_setup_reused channel=%s id=%d", ch_name, persisted_id)
                return existing

        # 2. Look up by name in fetched channels (prefer same category, fall back to any)
        existing = discord.utils.get(text_channels, name=ch_name, category=category)
        if existing is None:
            existing = discord.utils.get(text_channels, name=ch_name)
        if existing is not None:
            logger.info("discord_setup_found_by_name channel=%s id=%d", ch_name, existing.id)
            return existing

        # 3. Create new channel with @everyone read
        try:
            allow_everyone = discord.PermissionOverwrite(read_messages=True)
            overwrites = {guild.default_role: allow_everyone}
            new_ch = await guild.create_text_channel(
                ch_name,
                category=category,
                topic=ch_topic,
                overwrites=overwrites,
            )
            logger.info("discord_setup_created channel=%s id=%d", ch_name, new_ch.id)
            return new_ch
        except discord.HTTPException:
            logger.exception("discord_setup_create_channel_failed name=%s", ch_name)
            return None

    async def _setup_team_channel_and_role(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        team: object,
        text_channels: list[discord.TextChannel],
    ) -> None:
        """Set up a single team's role and private channel.

        Team channels deny @everyone read and grant the team role
        read + send. Each operation is individually wrapped for
        graceful degradation.

        Uses the pre-fetched ``text_channels`` list (from guild.fetch_channels)
        instead of the local guild cache, which may be incomplete when on_ready
        fires before Discord has finished populating it.
        """
        # Normalize to match Discord's channel name rules: lowercase,
        # spaces→hyphens, strip non-alphanumeric (periods, apostrophes, etc.),
        # collapse runs of hyphens.  Without this, "St. Johns" becomes
        # "st.-johns" locally but Discord stores "st-johns", causing
        # duplicate channel creation on every deploy.
        raw = team.name.lower().replace(" ", "-")  # type: ignore[union-attr]
        slug = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]", "", raw)).strip("-")
        team_key = f"team_{team.id}"  # type: ignore[union-attr]

        # --- Role ---
        role = discord.utils.get(guild.roles, name=team.name)  # type: ignore[union-attr]
        if role is None:
            try:
                color = discord.Color(int(team.color.lstrip("#"), 16))  # type: ignore[union-attr]
                role = await guild.create_role(name=team.name, color=color)  # type: ignore[union-attr]
                logger.info("discord_setup_created role=%s", team.name)  # type: ignore[union-attr]
            except discord.HTTPException:
                logger.exception("discord_setup_create_role_failed name=%s", team.name)  # type: ignore[union-attr]
                return
        else:
            logger.info("discord_setup_reused role=%s", team.name)  # type: ignore[union-attr]

        # --- Team channel ---
        # 1. Check persisted ID
        persisted_id = self.channel_ids.get(team_key)
        team_ch: discord.TextChannel | None = None
        if persisted_id:
            found = discord.utils.get(text_channels, id=persisted_id)
            if found is not None:
                team_ch = found
                logger.info("discord_setup_reused team_channel=%s id=%d", slug, persisted_id)

        # 2. Look up by name in fetched channels (prefer same category, fall back to any)
        if team_ch is None:
            team_ch = discord.utils.get(
                text_channels,
                name=slug,
                category=category,
            )
            if team_ch is None:
                # Channel may exist outside the category (from older setup)
                team_ch = discord.utils.get(text_channels, name=slug)
            if team_ch is not None:
                logger.info("discord_setup_found_by_name team_channel=%s id=%d", slug, team_ch.id)

        # 3. Create new private channel
        if team_ch is None:
            try:
                deny = discord.PermissionOverwrite(read_messages=False)
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
                    topic=f"Team channel for {team.name}",  # type: ignore[union-attr]
                )
                logger.info("discord_setup_created team_channel=%s id=%d", slug, team_ch.id)
            except discord.HTTPException:
                logger.exception("discord_setup_create_team_channel_failed name=%s", slug)
                return

        self.channel_ids[team_key] = team_ch.id
        await self._persist_bot_state(f"channel_{team_key}", str(team_ch.id))

    async def _sync_role_enrollments(self, guild: discord.Guild) -> None:
        """Re-enroll guild members who have a team role but no DB enrollment.

        Heals state after a database reseed: Discord roles persist but
        PlayerRow entries are wiped. Runs on every startup; idempotent.

        Requires the ``members`` privileged intent (enable in the Discord
        Developer Portal → Bot → Privileged Gateway Intents).
        """
        if not self.engine:
            return

        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        try:
            async with get_session(self.engine) as session:
                repo = Repository(session)
                season = await repo.get_active_season()
                if not season:
                    return

                teams = await repo.get_teams_for_season(season.id)
                team_by_role_name: dict[str, object] = {t.name: t for t in teams}
                healed = 0

                for member in guild.members:
                    if member.bot:
                        continue
                    for role in member.roles:
                        if role.name not in team_by_role_name:
                            continue
                        team = team_by_role_name[role.name]
                        discord_id = str(member.id)
                        enrollment = await repo.get_player_enrollment(
                            discord_id, season.id
                        )
                        if enrollment is None:
                            player = await repo.get_or_create_player(
                                discord_id=discord_id,
                                username=member.display_name,
                                avatar_url=(
                                    str(member.display_avatar.url)
                                    if member.display_avatar
                                    else ""
                                ),
                            )
                            await repo.enroll_player(
                                player.id, team.id, season.id  # type: ignore[union-attr]
                            )
                            # Grant tokens for healed governor
                            from pinwheel.core.tokens import regenerate_tokens

                            await regenerate_tokens(
                                repo, player.id, team.id, season.id  # type: ignore[union-attr]
                            )
                            healed += 1
                            logger.info(
                                "sync_role_healed user=%s team=%s",
                                member.display_name,
                                team.name,  # type: ignore[union-attr]
                            )
                        break  # one team role per member

                if healed:
                    await session.commit()
                    logger.info(
                        "sync_role_enrollments_complete healed=%d", healed
                    )
                else:
                    logger.info("sync_role_enrollments_complete all_ok=true")
        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("sync_role_enrollments_failed")

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

                from pinwheel.db.engine import get_session
                from pinwheel.db.repository import Repository

                async with get_session(self.engine) as session:
                    repo = Repository(session)
                    season = await repo.get_active_season()
                    if season:
                        teams = await repo.get_teams_for_season(season.id)
                        lines = []
                        for team in teams:
                            hooper_names = ", ".join(h.name for h in team.hoopers)
                            lines.append(f"**{team.name}** -- {hooper_names}")
                        team_lines = "\n".join(lines)
            except SQLAlchemyError:
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
            "- Put rule changes on the Floor with `/propose` -- "
            "the AI interprets your natural language into game parameters\n"
            "- Check standings with `/standings`, schedule with `/schedule`\n\n"
            f"**The teams:**\n{team_lines}\n\n"
            "Choose wisely. Your team's hoopers are counting on you."
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
        """Return team name choices matching the current input.

        Uses an in-memory cache populated at startup. Never hits the DB here —
        DB queries in autocomplete can congest the event loop and cause the
        subsequent /join command interaction to expire (3s Discord timeout).
        """
        if not self._team_names_cache:
            return []
        lowered = current.lower()
        return [
            app_commands.Choice(name=name, value=name)
            for name in self._team_names_cache
            if lowered in name.lower()
        ][:25]

    async def _autocomplete_proposals(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Return open proposal choices matching the current input."""
        if not self.engine:
            return []
        try:
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository
            from pinwheel.discord.helpers import GovernorNotFound, get_governor

            try:
                gov = await get_governor(self.engine, str(interaction.user.id))
            except GovernorNotFound:
                return []

            async with get_session(self.engine) as session:
                repo = Repository(session)
                confirmed = await repo.get_events_by_type(
                    season_id=gov.season_id,
                    event_types=["proposal.confirmed"],
                )
                resolved = await repo.get_events_by_type(
                    season_id=gov.season_id,
                    event_types=["proposal.passed", "proposal.failed"],
                )
                resolved_ids = {e.aggregate_id for e in resolved}
                pending = [
                    c
                    for c in confirmed
                    if c.payload.get("proposal_id", c.aggregate_id) not in resolved_ids
                ]
                if not pending:
                    return []

                # Get proposal texts from submitted events
                submitted = await repo.get_events_by_type(
                    season_id=gov.season_id,
                    event_types=["proposal.submitted"],
                )
                proposal_texts: dict[str, str] = {}
                for evt in submitted:
                    pid = evt.payload.get("id", evt.aggregate_id)
                    raw = evt.payload.get("raw_text", "")
                    proposal_texts[pid] = raw

                lowered = current.lower()
                choices: list[app_commands.Choice[str]] = []
                for p in pending:
                    pid = p.payload.get("proposal_id", p.aggregate_id)
                    raw_text = proposal_texts.get(pid, pid)
                    display = raw_text[:80] if raw_text else pid
                    if lowered in display.lower() or lowered in pid.lower():
                        choices.append(
                            app_commands.Choice(name=display, value=pid),
                        )
                return choices[:25]
        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("discord_proposal_autocomplete_failed")
            return []

    async def _handle_join(self, interaction: discord.Interaction, team_name: str) -> None:
        """Handle the /join slash command for team enrollment."""
        # Diagnostic: measure interaction age to debug "Unknown interaction" errors.
        interaction_age = (
            datetime.now(UTC) - interaction.created_at
        ).total_seconds()
        logger.info(
            "join_interaction_received user=%s team=%s age_seconds=%.3f "
            "is_expired=%s interaction_id=%s",
            interaction.user.display_name if interaction.user else "unknown",
            team_name,
            interaction_age,
            interaction.is_expired(),
            interaction.id,
        )

        if not self.engine:
            await interaction.response.send_message(
                "The league database is temporarily unavailable. "
                "Try `/join` again in a moment -- if this persists, let an admin know.",
                ephemeral=True,
            )
            return

        # Defer immediately — DB + role ops can exceed 3s interaction timeout.
        # If the interaction already expired (e.g., event loop was congested),
        # Discord returns 404 Unknown Interaction and we can't respond at all.
        try:
            await interaction.response.defer()
        except (discord.NotFound, discord.HTTPException) as defer_err:
            logger.warning(
                "join_defer_expired user=%s team=%s age_seconds=%.3f err=%s",
                interaction.user.display_name if interaction.user else "unknown",
                team_name,
                interaction_age,
                defer_err,
            )
            return

        try:
            from sqlalchemy.exc import OperationalError

            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            # Retry up to 3 times on transient SQLite "database is locked" errors.
            # WAL mode + busy_timeout handle most contention, but this provides
            # defense-in-depth for heavy-load moments (e.g., during tick_round).
            max_retries = 3
            last_error: Exception | None = None

            for attempt in range(max_retries):
                try:
                    async with get_session(self.engine) as session:
                        repo = Repository(session)
                        season = await repo.get_active_season()
                        if not season:
                            await interaction.followup.send(
                                "There's no active season right now. "
                                "Ask an admin to start one with `/new-season`.",
                                ephemeral=True,
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
                                team_data,
                                season.name or "this season",
                            )
                            await interaction.followup.send(
                                embed=embed,
                                ephemeral=True,
                            )
                            return

                        # Check existing enrollment
                        discord_id = str(interaction.user.id)
                        enrollment = await repo.get_player_enrollment(discord_id, season.id)
                        if enrollment is not None:
                            existing_team_id, existing_team_name = enrollment
                            if existing_team_name.lower() == team_name.lower():
                                # Re-assign Discord role in case it was lost
                                if interaction.guild and isinstance(
                                    interaction.user, discord.Member
                                ):
                                    role = discord.utils.get(
                                        interaction.guild.roles,
                                        name=existing_team_name,
                                    )
                                    if role and role not in interaction.user.roles:
                                        await interaction.user.add_roles(role)
                                        logger.info(
                                            "join_role_restored user=%s role=%s",
                                            interaction.user.display_name,
                                            existing_team_name,
                                        )
                                await interaction.followup.send(
                                    f"You're already on **{existing_team_name}**! "
                                    "(Role confirmed.)",
                                    ephemeral=True,
                                )
                            else:
                                season_label = season.name or "Season 1"
                                await interaction.followup.send(
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
                            await interaction.followup.send(
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

                        # Grant initial governance tokens so the governor can propose immediately
                        from pinwheel.core.tokens import regenerate_tokens

                        await regenerate_tokens(repo, player.id, target_team.id, season.id)

                        # Gather season context while the DB session is still open
                        season_context = await _gather_season_context(repo, season)

                        # Gather full league context for the onboarding embed
                        from pinwheel.core.onboarding import build_league_context

                        league_context = await build_league_context(
                            repo,
                            season_id=season.id,
                            season_name=season.name or "",
                            season_status=season.status or "active",
                            governance_interval=self.settings.pinwheel_governance_interval,
                        )

                        await session.commit()

                    # DB session closed — safe to do Discord ops and build embeds

                    # Assign Discord role if in a guild (non-fatal if role ops fail)
                    if interaction.guild:
                        role = discord.utils.get(
                            interaction.guild.roles,
                            name=target_team.name,
                        )
                        if role and isinstance(interaction.user, discord.Member):
                            try:
                                await interaction.user.add_roles(role)
                            except (discord.Forbidden, discord.HTTPException) as role_err:
                                logger.warning(
                                    "join_role_assign_failed user=%s role=%s err=%s",
                                    interaction.user.display_name,
                                    target_team.name,
                                    role_err,
                                )

                    # Build confirmation embed (shown in channel)
                    from pinwheel.discord.embeds import build_welcome_embed

                    hoopers = [
                        {
                            "name": h.name,
                            "archetype": h.archetype or "Hooper",
                            "backstory": h.backstory or "",
                        }
                        for h in target_team.hoopers
                    ]
                    embed = build_welcome_embed(
                        target_team.name,
                        target_team.color or "#000000",
                        hoopers,
                        motto=target_team.motto or "",
                        season_context=season_context,
                    )
                    await interaction.followup.send(embed=embed)

                    # Send welcome DM with quick-start info + onboarding context
                    import contextlib

                    with contextlib.suppress(discord.Forbidden, discord.HTTPException):
                        await interaction.user.send(embed=embed)
                        # Send the State of the League briefing as a second DM
                        onboarding_embed = build_onboarding_embed(
                            league_context,
                            team_name=target_team.name,
                        )
                        await interaction.user.send(embed=onboarding_embed)

                    return  # Success — exit retry loop

                except OperationalError as exc:
                    last_error = exc
                    if attempt < max_retries - 1:
                        logger.warning(
                            "discord_join_retry attempt=%d err=%s",
                            attempt + 1,
                            str(exc),
                        )
                        await asyncio.sleep(1)
                    # else: fall through to final error handler

            # All retries exhausted
            if last_error is not None:
                raise last_error

        except Exception as exc:  # Last-resort handler — DB, Discord, and logic errors
            logger.exception(
                "discord_join_failed user=%s team=%s",
                interaction.user.display_name if interaction.user else "unknown",
                team_name,
            )
            exc_str = str(exc).lower()
            if "locked" in exc_str or "busy" in exc_str:
                msg = (
                    "The league database is busy right now -- "
                    f"try `/join {team_name}` again in a few seconds."
                )
            elif team_name:
                msg = (
                    f"Having trouble joining **{team_name}**. "
                    "This might be a temporary glitch -- try again, or ask an admin for help."
                )
            else:
                msg = (
                    "Something unexpected happened while looking up teams. "
                    "Try `/join` again -- if it keeps failing, let an admin know."
                )
            await interaction.followup.send(
                msg,
                ephemeral=True,
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

    def _get_unique_team_channels(self) -> list[discord.TextChannel]:
        """Return deduplicated team channels.

        Multiple seasons may leave stale ``team_*`` entries in
        ``self.channel_ids`` that all point to the same Discord channel.
        This helper deduplicates by channel ID so each channel receives
        a message at most once.
        """
        seen: set[int] = set()
        channels: list[discord.TextChannel] = []
        for key, chan_id in self.channel_ids.items():
            if key.startswith("team_") and chan_id not in seen:
                seen.add(chan_id)
                ch = self.get_channel(chan_id)
                if isinstance(ch, discord.TextChannel):
                    channels.append(ch)
        return channels

    async def _send_to_team_channel(
        self,
        team_id: str,
        embed: discord.Embed,
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

        if event_type == "presentation.game_finished":
            # Extract playoff context from event data (propagated from game_summaries)
            pc = str(data.get("playoff_context", "")) or None
            embed = build_game_result_embed(data, playoff_context=pc)
            play_channel = self._get_channel_for("play_by_play")
            if play_channel:
                await play_channel.send(embed=embed)

            # Big plays: blowout (>15 diff) or buzzer-beater (margin <= 2)
            home_score = int(data.get("home_score", 0))
            away_score = int(data.get("away_score", 0))
            margin = abs(home_score - away_score)
            is_blowout = margin > 15
            is_buzzer_beater = margin <= 2
            if is_blowout or is_buzzer_beater:
                big_channel = self._get_channel_for("big_plays")
                if big_channel:
                    await big_channel.send(embed=embed)

            # Team-specific results to team channels
            home_id = str(data.get("home_team_id", ""))
            away_id = str(data.get("away_team_id", ""))
            if home_id:
                from pinwheel.discord.embeds import build_team_game_result_embed

                home_embed = build_team_game_result_embed(
                    data, home_id, playoff_context=pc,
                )
                await self._send_to_team_channel(home_id, home_embed)
            if away_id:
                from pinwheel.discord.embeds import build_team_game_result_embed

                away_embed = build_team_game_result_embed(
                    data, away_id, playoff_context=pc,
                )
                await self._send_to_team_channel(away_id, away_embed)

        elif event_type == "presentation.round_finished":
            pc = str(data.get("playoff_context", "")) or None
            embed = build_round_summary_embed(data, playoff_context=pc)
            play_channel = self._get_channel_for("play_by_play")
            if play_channel:
                await play_channel.send(embed=embed)

        elif event_type == "report.generated":
            report_type = data.get("report_type", "")
            excerpt = str(data.get("excerpt", ""))
            if report_type == "private":
                await self._send_private_report(data)
            elif excerpt:
                from pinwheel.models.report import Report

                report = Report(
                    id="",
                    report_type=report_type,  # type: ignore[arg-type]
                    round_number=int(data.get("round", 0)),
                    content=excerpt,
                )
                embed = build_report_embed(report)
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
                title=f"The Floor Has Spoken -- Round {round_num}",
                description=(
                    f"**{proposals_count}** proposals reviewed\n**{rules_changed}** rules changed"
                ),
                color=0x3498DB,
            )
            embed.set_footer(text="Pinwheel Fates")

            # Build per-proposal result embeds from tally data
            from pinwheel.models.governance import VoteTally as VoteTallyModel

            tally_embeds: list[discord.Embed] = []
            tallies_data = data.get("tallies", [])
            if isinstance(tallies_data, list):
                for td in tallies_data:
                    if isinstance(td, dict):
                        tally_obj = VoteTallyModel(
                            **{k: v for k, v in td.items() if k != "proposal_text"}
                        )
                        proposal_text = str(td.get("proposal_text", ""))
                        tally_embed = build_vote_tally_embed(
                            tally_obj,
                            proposal_text,
                        )
                        tally_embeds.append(tally_embed)

            channel = self._get_channel_for("main")
            if channel:
                await channel.send(embed=embed)
                for te in tally_embeds:
                    with contextlib.suppress(
                        discord.Forbidden,
                        discord.HTTPException,
                    ):
                        await channel.send(embed=te)
            # Post to all team channels (deduplicated to avoid stale entries)
            for ch in self._get_unique_team_channels():
                with contextlib.suppress(
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    await ch.send(embed=embed)
                for te in tally_embeds:
                    with contextlib.suppress(
                        discord.Forbidden,
                        discord.HTTPException,
                    ):
                        await ch.send(embed=te)

        elif event_type == "season.championship_started":
            champion_name = str(data.get("champion_team_name", "???"))
            awards = data.get("awards", [])

            embed = discord.Embed(
                title="Championship Ceremony",
                description=(
                    f"**{champion_name}** are your champions!\n\n"
                    "The championship ceremony has begun."
                ),
                color=0xFFD700,  # Gold
            )

            # Add awards as fields
            if isinstance(awards, list):
                for award in awards[:6]:  # Cap at 6 to avoid embed limit
                    if isinstance(award, dict):
                        name = str(award.get("award", ""))
                        recipient = str(award.get("recipient_name", ""))
                        val = award.get("stat_value", "")
                        label = str(award.get("stat_label", ""))
                        embed.add_field(
                            name=name,
                            value=f"{recipient} ({val} {label})",
                            inline=True,
                        )

            embed.set_footer(text="Pinwheel Fates -- Season Awards")

            channel = self._get_channel_for("main")
            if channel:
                await channel.send(embed=embed)

            # Post to all team channels (deduplicated)
            for ch in self._get_unique_team_channels():
                with contextlib.suppress(
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    await ch.send(embed=embed)

        elif event_type == "season.memorial_generated":
            season_name = str(data.get("season_name", ""))
            champion_name = str(data.get("champion_team_name", ""))
            narrative_excerpt = str(data.get("narrative_excerpt", ""))
            total_games = int(data.get("total_games", 0))
            total_proposals = int(data.get("total_proposals", 0))
            total_rule_changes = int(data.get("total_rule_changes", 0))

            embed = build_memorial_embed(
                season_name=season_name,
                champion_team_name=champion_name,
                narrative_excerpt=narrative_excerpt,
                total_games=total_games,
                total_proposals=total_proposals,
                total_rule_changes=total_rule_changes,
            )

            channel = self._get_channel_for("main")
            if channel:
                await channel.send(embed=embed)

            # Post to all team channels (deduplicated)
            for ch in self._get_unique_team_channels():
                with contextlib.suppress(
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    await ch.send(embed=embed)

        elif event_type == "season.phase_changed":
            to_phase = str(data.get("to_phase", ""))
            if to_phase == "complete":
                embed = discord.Embed(
                    title="Season Complete",
                    description="The season has concluded. Thanks for playing!",
                    color=0x95A5A6,
                )
                embed.set_footer(text="Pinwheel Fates")
                channel = self._get_channel_for("main")
                if channel:
                    await channel.send(embed=embed)

    # --- Slash command handlers ---

    async def _handle_standings(self, interaction: discord.Interaction) -> None:
        """Handle the /standings slash command."""
        await interaction.response.defer()
        standings = await self._query_standings()
        embed = build_standings_embed(standings)
        await interaction.followup.send(embed=embed)

    async def _query_standings(self) -> list[dict[str, object]]:
        """Query current standings from the database."""
        if not self.engine:
            return []
        try:

            from pinwheel.core.scheduler import compute_standings
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                season = await repo.get_active_season()
                if not season:
                    return []

                all_games = await repo.get_all_games(season.id)
                all_results: list[dict] = [
                    {
                        "home_team_id": g.home_team_id,
                        "away_team_id": g.away_team_id,
                        "home_score": g.home_score,
                        "away_score": g.away_score,
                        "winner_team_id": g.winner_team_id,
                    }
                    for g in all_games
                ]
                standings = compute_standings(all_results)
                for s in standings:
                    team = await repo.get_team(s["team_id"])
                    if team:
                        s["team_name"] = team.name
                return standings
        except SQLAlchemyError:
            logger.exception("discord_standings_query_failed")
            return []

    async def _handle_ask(self, interaction: discord.Interaction, question: str) -> None:
        """Handle the /ask slash command -- natural language stats queries.

        Available to all server members. Rate-limited per user (10s cooldown).
        Uses a two-step pipeline: parse question -> execute query -> format response.
        Falls back to mock (keyword-based) when ANTHROPIC_API_KEY is not set.
        """
        user_id = str(interaction.user.id)

        # Rate limiting: 10s cooldown per user
        now = time.monotonic()
        last_ask = self._ask_cooldowns.get(user_id, 0.0)
        if now - last_ask < ASK_COOLDOWN_SECONDS:
            remaining = int(ASK_COOLDOWN_SECONDS - (now - last_ask))
            await interaction.response.send_message(
                f"Easy there -- try again in {remaining} seconds.",
                ephemeral=True,
            )
            return

        self._ask_cooldowns[user_id] = now
        await interaction.response.defer()

        if not self.engine:
            await interaction.followup.send(
                "The league database is temporarily unavailable. Try again in a moment.",
                ephemeral=True,
            )
            return

        try:
            from pinwheel.ai.search import (
                NameResolver,
                execute_query,
                format_response_mock,
                parse_query_mock,
            )
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                season = await repo.get_active_season()
                if not season:
                    await interaction.followup.send(
                        "No active season right now. Check back soon!",
                        ephemeral=True,
                    )
                    return

                # Build name resolver
                teams = await repo.get_teams_for_season(season.id)
                all_hoopers = []
                for t in teams:
                    all_hoopers.extend(t.hoopers)
                resolver = NameResolver(teams, all_hoopers)

                # Step 1: Parse the question
                api_key = self.settings.anthropic_api_key
                if api_key:
                    from pinwheel.ai.search import parse_query_ai

                    team_names = [t.name for t in teams]
                    hooper_names = [h.name for h in all_hoopers]
                    plan = await parse_query_ai(
                        question, api_key, team_names, hooper_names
                    )
                else:
                    plan = parse_query_mock(question)

                # Step 2: Execute the query
                result = await execute_query(plan, repo, season.id, resolver)

                # Step 3: Format the response
                if api_key:
                    from pinwheel.ai.search import format_response_ai

                    answer = await format_response_ai(question, result, api_key)
                else:
                    answer = format_response_mock(question, result)

            embed = build_search_result_embed(question, answer, result.query_type)
            await interaction.followup.send(embed=embed)

        except Exception:  # Last-resort handler — DB, AI (Anthropic), and Discord errors
            logger.exception(
                "discord_ask_failed user=%s question=%s",
                interaction.user.display_name if interaction.user else "unknown",
                question[:100],
            )
            await interaction.followup.send(
                "Something went wrong processing your question. Try again!",
                ephemeral=True,
            )

    async def _handle_status(self, interaction: discord.Interaction) -> None:
        """Handle the /status slash command -- show current state of the league.

        Available to all server members, not just enrolled governors.
        Sends an ephemeral embed with standings, active proposals,
        recent rule changes, and governor counts.
        """
        await interaction.response.defer(ephemeral=True)

        if not self.engine:
            await interaction.followup.send(
                "The league database is temporarily unavailable. "
                "Try `/status` again in a moment.",
                ephemeral=True,
            )
            return

        try:
            from pinwheel.core.onboarding import build_league_context
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                season = await repo.get_active_season()
                if not season:
                    await interaction.followup.send(
                        "No active season right now. Check back soon!",
                        ephemeral=True,
                    )
                    return

                league_context = await build_league_context(
                    repo,
                    season_id=season.id,
                    season_name=season.name or "",
                    season_status=season.status or "active",
                    governance_interval=self.settings.pinwheel_governance_interval,
                )

                # Check if the user is enrolled to highlight their team
                discord_id = str(interaction.user.id)
                enrollment = await repo.get_player_enrollment(discord_id, season.id)
                user_team_name: str | None = None
                if enrollment is not None:
                    _team_id, user_team_name = enrollment

            embed = build_onboarding_embed(league_context, team_name=user_team_name)
            await interaction.followup.send(embed=embed, ephemeral=True)

        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("discord_status_failed")
            await interaction.followup.send(
                "Something went wrong fetching the league status. "
                "Try again in a moment.",
                ephemeral=True,
            )

    async def _handle_history(
        self, interaction: discord.Interaction, season: str = ""
    ) -> None:
        """Handle the /history slash command -- show past season memorials.

        With no args, lists all archived seasons. With a season name,
        shows a memorial summary embed.
        """
        await interaction.response.defer()

        if not self.engine:
            await interaction.followup.send(
                "The league database is temporarily unavailable. "
                "Try `/history` again in a moment.",
            )
            return

        try:
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                archives = await repo.get_all_archives()

                if not archives:
                    await interaction.followup.send(
                        "No seasons have been archived yet. "
                        "History is written by those who finish.",
                    )
                    return

                if not season:
                    # List all archived seasons
                    archive_list = [
                        {
                            "season_name": a.season_name,
                            "champion_team_name": a.champion_team_name,
                            "total_games": a.total_games,
                        }
                        for a in archives
                    ]
                    embed = build_history_list_embed(archive_list)
                    await interaction.followup.send(embed=embed)
                    return

                # Find archive matching the season name
                matching = None
                season_lower = season.lower()
                for a in archives:
                    if a.season_name.lower() == season_lower:
                        matching = a
                        break

                if not matching:
                    # Try partial match
                    for a in archives:
                        if season_lower in a.season_name.lower():
                            matching = a
                            break

                if not matching:
                    await interaction.followup.send(
                        f'No archived season found matching "{season}". '
                        "Use `/history` to see all archived seasons.",
                    )
                    return

                # Build memorial summary embed
                memorial = matching.memorial or {}
                narrative = str(memorial.get("season_narrative", ""))

                embed = build_memorial_embed(
                    season_name=matching.season_name,
                    champion_team_name=matching.champion_team_name or "",
                    narrative_excerpt=narrative[:500] if narrative else "",
                    total_games=matching.total_games,
                    total_proposals=matching.total_proposals,
                    total_rule_changes=matching.total_rule_changes,
                )
                await interaction.followup.send(embed=embed)

        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("discord_history_failed")
            await interaction.followup.send(
                "Something went wrong fetching the history. "
                "Try again in a moment.",
            )

    async def _handle_roster(self, interaction: discord.Interaction) -> None:
        """Handle the /roster slash command -- show all enrolled governors."""
        await interaction.response.defer()

        if not self.engine:
            await interaction.followup.send(
                "The league database is temporarily unavailable. "
                "Try `/roster` again in a moment -- if this persists, let an admin know.",
            )
            return

        try:
            from pinwheel.core.tokens import get_token_balance
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                season = await repo.get_active_season()
                if not season:
                    await interaction.followup.send(
                        "There's no active season right now. "
                        "Ask an admin to start one with `/new-season`.",
                    )
                    return

                players = await repo.get_players_for_season(season.id)
                governor_data: list[dict[str, object]] = []

                for player in players:
                    team = await repo.get_team(player.team_id) if player.team_id else None
                    team_name = team.name if team else "Unassigned"

                    balance = await get_token_balance(repo, player.id, season.id)

                    activity = await repo.get_governor_activity(player.id, season.id)

                    governor_data.append(
                        {
                            "username": player.username,
                            "team_name": team_name,
                            "propose": balance.propose,
                            "amend": balance.amend,
                            "boost": balance.boost,
                            "proposals_submitted": activity.get("proposals_submitted", 0),
                            "votes_cast": activity.get("votes_cast", 0),
                        }
                    )

                embed = build_roster_embed(
                    governor_data,
                    season_name=season.name or "this season",
                )
                await interaction.followup.send(embed=embed)
        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("discord_roster_failed")
            await interaction.followup.send(
                "Could not load the governor roster right now. "
                "Try `/roster` again -- if this persists, let an admin know.",
            )

    async def _handle_proposals(
        self,
        interaction: discord.Interaction,
        season_filter: str = "current",
    ) -> None:
        """Handle the /proposals slash command -- show all proposals with status."""
        if not self.engine:
            await interaction.response.send_message(
                "The league database is temporarily unavailable. "
                "Try `/proposals` again in a moment -- if this persists, let an admin know.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository
            from pinwheel.discord.embeds import build_proposals_embed

            async with get_session(self.engine) as session:
                repo = Repository(session)

                seasons_to_query: list[tuple[str, str]] = []
                if season_filter == "all":
                    all_seasons = await repo.get_all_seasons()
                    seasons_to_query = [(s.id, s.name or s.id) for s in all_seasons]
                else:
                    season = await repo.get_active_season()
                    if not season:
                        await interaction.followup.send(
                            "There's no active season right now. "
                            "Ask an admin to start one with `/new-season`.",
                        )
                        return
                    seasons_to_query = [(season.id, season.name or "this season")]

                # Build governor_id -> username lookup
                all_players = await repo.get_all_players()
                governor_names = {p.id: p.username for p in all_players}

                embeds: list[object] = []
                for season_id, season_name in seasons_to_query:
                    proposals = await repo.get_all_proposals(season_id)
                    if proposals or season_filter == "current":
                        embed = build_proposals_embed(
                            proposals,
                            season_name=season_name,
                            governor_names=governor_names,
                        )
                        embeds.append(embed)

                if embeds:
                    await interaction.followup.send(embeds=embeds[:10])
                else:
                    await interaction.followup.send("No proposals found.")
        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("discord_proposals_failed")
            await interaction.followup.send(
                "Could not load proposals right now. "
                "Try `/proposals` again -- if this persists, let an admin know.",
            )

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
                "The league database is temporarily unavailable. "
                "Try `/propose` again in a moment -- if this persists, let an admin know.",
                ephemeral=True,
            )
            return

        # Defer immediately — DB + AI calls can exceed 3s interaction timeout
        await interaction.response.defer(ephemeral=True)

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(self.engine, str(interaction.user.id))
        except GovernorNotFound as exc:
            await interaction.followup.send(
                str(exc) or "You need to `/join` a team first.",
                ephemeral=True,
            )
            return

        # Per-governor cooldown — system-level protection against rapid-fire submissions
        now = time.monotonic()
        last_propose = self._proposal_cooldowns.get(gov.player_id)
        if last_propose is not None:
            elapsed = now - last_propose
            if elapsed < PROPOSAL_COOLDOWN_SECONDS:
                remaining = int(PROPOSAL_COOLDOWN_SECONDS - elapsed)
                await interaction.followup.send(
                    f"Please wait {remaining} second{'s' if remaining != 1 else ''} "
                    "before submitting another proposal.",
                    ephemeral=True,
                )
                return

        try:
            from pinwheel.ai.interpreter import (
                interpret_proposal_v2,
                interpret_proposal_v2_mock,
            )
            from pinwheel.core.governance import (
                detect_tier,
                token_cost_for_tier,
            )
            from pinwheel.core.tokens import get_token_balance, has_token
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository
            from pinwheel.models.rules import RuleSet

            # Guard: check if governor already has a pending interpretation
            async with get_session(self.engine) as guard_session:
                guard_repo = Repository(guard_session)
                from pinwheel.core.deferred_interpreter import (
                    get_pending_interpretations,
                )

                pending = await get_pending_interpretations(
                    guard_repo, gov.season_id,
                )
                governor_pending = [
                    p for p in pending if p.governor_id == gov.player_id
                ]
                if governor_pending:
                    await interaction.followup.send(
                        "You already have a proposal queued for interpretation. "
                        "You'll get a DM when it's ready.",
                        ephemeral=True,
                    )
                    return

            async with get_session(self.engine) as session:
                repo = Repository(session)
                if not await has_token(
                    repo,
                    gov.player_id,
                    gov.season_id,
                    "propose",
                ):
                    await interaction.followup.send(
                        "You don't have any PROPOSE tokens left. "
                        "Use `/tokens` to check your balance. "
                        "Tokens regenerate at the next governance interval.",
                        ephemeral=True,
                    )
                    return

                balance = await get_token_balance(
                    repo,
                    gov.player_id,
                    gov.season_id,
                )
                season = await repo.get_season(gov.season_id)
                rs_data = (season.current_ruleset or {}) if season else {}
                ruleset = RuleSet(**rs_data)

                # Enforce proposals_per_window limit
                submitted_events = await repo.get_events_by_type_and_governor(
                    season_id=gov.season_id,
                    governor_id=gov.player_id,
                    event_types=["proposal.submitted"],
                )
                # Count proposals in the current governance window.
                # A window starts after the most recent token.regenerated event.
                regen_events = await repo.get_events_by_type_and_governor(
                    season_id=gov.season_id,
                    governor_id=gov.player_id,
                    event_types=["token.regenerated"],
                )
                if regen_events:
                    last_regen_seq = max(e.sequence_number for e in regen_events)
                    window_proposals = [
                        e for e in submitted_events
                        if e.sequence_number > last_regen_seq
                    ]
                else:
                    window_proposals = list(submitted_events)

                if len(window_proposals) >= ruleset.proposals_per_window:
                    await interaction.followup.send(
                        f"You've reached the maximum of {ruleset.proposals_per_window} "
                        "proposals for this governance window. "
                        "Your limit resets after the next governance tally.",
                        ephemeral=True,
                    )
                    return

            # Let the player know their proposal was received before the slow AI call
            thinking_msg = await interaction.followup.send(
                "**Received your proposal.** The Constitutional Interpreter "
                "is reviewing it \u2014 this usually takes 15\u201330 seconds...",
                ephemeral=True,
            )

            api_key = self.settings.anthropic_api_key
            interpretation_v2 = None
            if api_key:
                # Fire classifier and interpreter in parallel —
                # total time = max(classifier, interpreter) instead of sum.
                import asyncio

                from pinwheel.ai.classifier import classify_injection
                from pinwheel.evals.injection import store_injection_classification
                from pinwheel.models.governance import (
                    ProposalInterpretation as PI,
                )
                from pinwheel.models.governance import (
                    RuleInterpretation as RI,
                )

                classification, interpretation_v2 = await asyncio.gather(
                    classify_injection(text, api_key),
                    interpret_proposal_v2(text, ruleset, api_key),
                )

                # Store classification result for dashboard visibility
                async with get_session(self.engine) as session:
                    cls_repo = Repository(session)
                    await store_injection_classification(
                        repo=cls_repo,
                        season_id=gov.season_id,
                        proposal_text=text,
                        result=classification,
                        governor_id=gov.player_id,
                        source="discord_bot",
                    )
                    await session.commit()

                if classification.classification == "injection" and classification.confidence > 0.8:
                    # Discard interpreter result — injection detected
                    interpretation = RI(
                        confidence=0.0,
                        injection_flagged=True,
                        rejection_reason=classification.reason,
                        impact_analysis="Proposal flagged as potential prompt injection.",
                    )
                    interpretation_v2 = PI(
                        confidence=0.0,
                        injection_flagged=True,
                        rejection_reason=classification.reason,
                        impact_analysis="Proposal flagged as potential prompt injection.",
                        original_text_echo=text,
                    )
                else:
                    interpretation = interpretation_v2.to_rule_interpretation()
                    if classification.classification == "suspicious":
                        interpretation.impact_analysis = (
                            f"[Suspicious: {classification.reason}] "
                            + interpretation.impact_analysis
                        )
                        interpretation_v2.impact_analysis = (
                            f"[Suspicious: {classification.reason}] "
                            + interpretation_v2.impact_analysis
                        )
            else:
                interpretation_v2 = interpret_proposal_v2_mock(
                    text,
                    ruleset,
                )
                interpretation = interpretation_v2.to_rule_interpretation()

            if interpretation_v2 is not None:
                from pinwheel.core.governance import detect_tier_v2

                tier = detect_tier_v2(interpretation_v2, ruleset)
            else:
                tier = detect_tier(interpretation, ruleset)
            cost = token_cost_for_tier(tier)

            # Spend PROPOSE token NOW (before confirm UI) to prevent race conditions.
            # Two rapid /propose calls can no longer both pass the has_token() check
            # because the token is deducted immediately. If the governor cancels,
            # the token is refunded in ProposalConfirmView.cancel.
            async with get_session(self.engine) as spend_session:
                spend_repo = Repository(spend_session)
                await spend_repo.append_event(
                    event_type="token.spent",
                    aggregate_id=gov.player_id,
                    aggregate_type="token",
                    season_id=gov.season_id,
                    governor_id=gov.player_id,
                    team_id=gov.team_id,
                    payload={
                        "token_type": "propose",
                        "amount": cost,
                        "reason": "proposal:pending_confirm",
                    },
                )
                await spend_session.commit()

            # Record cooldown timestamp
            self._proposal_cooldowns[gov.player_id] = time.monotonic()

            from pinwheel.discord.views import ProposalConfirmView

            view = ProposalConfirmView(
                original_user_id=interaction.user.id,
                raw_text=text,
                interpretation=interpretation,
                tier=tier,
                token_cost=cost,
                tokens_remaining=balance.propose - cost,
                governor_info=gov,
                engine=self.engine,
                settings=self.settings,
                interpretation_v2=interpretation_v2,
                token_already_spent=True,
            )
            embed = build_interpretation_embed(
                raw_text=text,
                interpretation=interpretation,
                tier=tier,
                token_cost=cost,
                tokens_remaining=balance.propose - cost,
                governor_name=interaction.user.display_name,
                interpretation_v2=interpretation_v2,
            )
            await thinking_msg.edit(
                content=None,
                embed=embed,
                view=view,
            )
        except Exception:  # Last-resort handler — DB, AI interpreter, and Discord errors
            logger.exception("discord_propose_failed")
            # If the thinking message was sent, edit it with the error;
            # otherwise fall back to a new followup.
            error_text = (
                "Your proposal could not be interpreted right now. "
                "This might be a temporary issue with the AI interpreter -- "
                "try `/propose` again with the same text. "
                "If it keeps failing, ask an admin for help."
            )
            if "thinking_msg" in locals():
                await thinking_msg.edit(content=error_text)
            else:
                await interaction.followup.send(
                    error_text,
                    ephemeral=True,
                )

    async def _handle_amend(
        self,
        interaction: discord.Interaction,
        proposal_selector: str,
        text: str,
    ) -> None:
        """Handle the /amend slash command with AI re-interpretation."""
        if not text.strip():
            await interaction.response.send_message(
                "You need to describe your amendment. "
                "Example: `/amend [proposal] Change the value to 4 instead of 5`",
                ephemeral=True,
            )
            return

        if not self.engine:
            await interaction.response.send_message(
                "The league database is temporarily unavailable. "
                "Try `/amend` again in a moment -- if this persists, let an admin know.",
                ephemeral=True,
            )
            return

        # Defer immediately -- DB + AI calls can exceed 3s interaction timeout
        await interaction.response.defer(ephemeral=True)

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(self.engine, str(interaction.user.id))
        except GovernorNotFound as exc:
            await interaction.followup.send(
                str(exc) or "You need to `/join` a team first.",
                ephemeral=True,
            )
            return

        try:
            from pinwheel.ai.interpreter import (
                interpret_proposal_v2,
                interpret_proposal_v2_mock,
            )
            from pinwheel.core.governance import (
                MAX_AMENDMENTS_PER_PROPOSAL,
                count_amendments,
            )
            from pinwheel.core.tokens import get_token_balance, has_token
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository
            from pinwheel.models.governance import Proposal
            from pinwheel.models.rules import RuleSet

            async with get_session(self.engine) as session:
                repo = Repository(session)

                # Check AMEND tokens
                if not await has_token(
                    repo, gov.player_id, gov.season_id, "amend"
                ):
                    await interaction.followup.send(
                        "You don't have any AMEND tokens left. "
                        "Use `/tokens` to check your balance. "
                        "Tokens regenerate at the next governance interval.",
                        ephemeral=True,
                    )
                    return

                balance = await get_token_balance(
                    repo, gov.player_id, gov.season_id
                )

                # Find the proposal
                confirmed = await repo.get_events_by_type(
                    season_id=gov.season_id,
                    event_types=["proposal.confirmed"],
                )
                resolved = await repo.get_events_by_type(
                    season_id=gov.season_id,
                    event_types=["proposal.passed", "proposal.failed"],
                )
                resolved_ids = {e.aggregate_id for e in resolved}
                pending = [
                    c
                    for c in confirmed
                    if c.payload.get("proposal_id", c.aggregate_id) not in resolved_ids
                ]

                # Match proposal by ID
                proposal_id: str | None = None
                for p in pending:
                    pid = p.payload.get("proposal_id", p.aggregate_id)
                    if pid == proposal_selector:
                        proposal_id = pid
                        break

                # Try text match if ID didn't match
                if not proposal_id:
                    submitted_for_match = await repo.get_events_by_type(
                        season_id=gov.season_id,
                        event_types=["proposal.submitted"],
                    )
                    for p in pending:
                        pid = p.payload.get("proposal_id", p.aggregate_id)
                        for se in submitted_for_match:
                            if se.aggregate_id == pid:
                                raw = se.payload.get("raw_text", "")
                                if proposal_selector.lower() in raw.lower():
                                    proposal_id = pid
                                    break
                        if proposal_id:
                            break

                if not proposal_id:
                    await interaction.followup.send(
                        "Could not find an open proposal matching your selection. "
                        "Use `/proposals` to see what's currently on the Floor.",
                        ephemeral=True,
                    )
                    return

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
                    await interaction.followup.send(
                        "The proposal data could not be loaded.",
                        ephemeral=True,
                    )
                    return

                proposal = Proposal(**proposal_data)

                # Check amendment cap
                amendment_count = await count_amendments(
                    repo, proposal_id, gov.season_id
                )
                if amendment_count >= MAX_AMENDMENTS_PER_PROPOSAL:
                    await interaction.followup.send(
                        f"This proposal has already been amended "
                        f"{MAX_AMENDMENTS_PER_PROPOSAL} times (the maximum). "
                        "No further amendments are allowed.",
                        ephemeral=True,
                    )
                    return

                # Get the current ruleset for AI interpretation
                season = await repo.get_season(gov.season_id)
                rs_data = (season.current_ruleset or {}) if season else {}
                ruleset = RuleSet(**rs_data)

            # AI interpretation of the amendment text
            api_key = self.settings.anthropic_api_key
            interpretation_v2 = None
            if api_key:
                import asyncio

                from pinwheel.ai.classifier import classify_injection
                from pinwheel.evals.injection import store_injection_classification
                from pinwheel.models.governance import (
                    ProposalInterpretation as PI,
                )
                from pinwheel.models.governance import (
                    RuleInterpretation as RI,
                )

                classification, interpretation_v2 = await asyncio.gather(
                    classify_injection(text, api_key),
                    interpret_proposal_v2(text, ruleset, api_key),
                )

                # Store classification result
                async with get_session(self.engine) as cls_session:
                    cls_repo = Repository(cls_session)
                    await store_injection_classification(
                        repo=cls_repo,
                        season_id=gov.season_id,
                        proposal_text=text,
                        result=classification,
                        governor_id=gov.player_id,
                        source="discord_amend",
                    )
                    await cls_session.commit()

                if classification.classification == "injection" and classification.confidence > 0.8:
                    interpretation = RI(
                        confidence=0.0,
                        injection_flagged=True,
                        rejection_reason=classification.reason,
                        impact_analysis="Amendment flagged as potential prompt injection.",
                    )
                    interpretation_v2 = PI(
                        confidence=0.0,
                        injection_flagged=True,
                        rejection_reason=classification.reason,
                        impact_analysis="Amendment flagged as potential prompt injection.",
                        original_text_echo=text,
                    )
                else:
                    interpretation = interpretation_v2.to_rule_interpretation()
                    if classification.classification == "suspicious":
                        interpretation.impact_analysis = (
                            f"[Suspicious: {classification.reason}] "
                            + interpretation.impact_analysis
                        )
                        interpretation_v2.impact_analysis = (
                            f"[Suspicious: {classification.reason}] "
                            + interpretation_v2.impact_analysis
                        )
            else:
                interpretation_v2 = interpret_proposal_v2_mock(text, ruleset)
                interpretation = interpretation_v2.to_rule_interpretation()

            from pinwheel.discord.views import AmendConfirmView

            new_amendment_number = amendment_count + 1

            view = AmendConfirmView(
                original_user_id=interaction.user.id,
                proposal_id=proposal_id,
                proposal_raw_text=proposal.raw_text,
                amendment_text=text,
                interpretation=interpretation,
                amendment_number=new_amendment_number,
                max_amendments=MAX_AMENDMENTS_PER_PROPOSAL,
                governor_info=gov,
                engine=self.engine,
                interpretation_v2=interpretation_v2,
            )
            embed = build_amendment_confirm_embed(
                original_text=proposal.raw_text,
                amendment_text=text,
                interpretation=interpretation,
                amendment_number=new_amendment_number,
                max_amendments=MAX_AMENDMENTS_PER_PROPOSAL,
                amend_tokens_remaining=balance.amend - 1,
                governor_name=interaction.user.display_name,
                interpretation_v2=interpretation_v2,
            )
            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True,
            )
        except Exception:  # Last-resort handler — DB, AI interpreter, and Discord errors
            logger.exception("discord_amend_failed")
            await interaction.followup.send(
                "Your amendment could not be processed right now. "
                "This might be a temporary issue with the AI interpreter -- "
                "try `/amend` again with the same text. "
                "If it keeps failing, ask an admin for help.",
                ephemeral=True,
            )

    async def _handle_effects(self, interaction: discord.Interaction) -> None:
        """Handle the /effects slash command — show all active effects."""
        await interaction.response.defer()

        if not self.engine:
            await interaction.followup.send(
                "The league database is temporarily unavailable.",
                ephemeral=True,
            )
            return

        try:
            from pinwheel.core.effects import load_effect_registry
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository
            from pinwheel.discord.embeds import build_effects_list_embed

            async with get_session(self.engine) as session:
                repo = Repository(session)
                season = await repo.get_active_season()
                if not season:
                    await interaction.followup.send(
                        "No active season.",
                        ephemeral=True,
                    )
                    return

                registry = await load_effect_registry(repo, season.id)
                active_effects = registry.get_all_active()

                # Look up source proposal text for each effect
                submitted_events = await repo.get_events_by_type(
                    season_id=season.id,
                    event_types=["proposal.submitted"],
                )
                proposal_texts: dict[str, str] = {}
                for se in submitted_events:
                    pid = se.payload.get("id", se.aggregate_id)
                    raw = str(se.payload.get("raw_text", ""))
                    proposal_texts[str(pid)] = raw

                effects_data: list[dict[str, object]] = []
                for effect in active_effects:
                    desc = (
                        effect.description
                        or effect.narrative_instruction
                        or effect.effect_type
                    )
                    effects_data.append({
                        "effect_id": effect.effect_id,
                        "effect_type": effect.effect_type,
                        "description": desc,
                        "lifetime": effect.lifetime.value,
                        "rounds_remaining": effect.rounds_remaining,
                        "proposal_text": proposal_texts.get(
                            effect.proposal_id, ""
                        ),
                    })

            season_name = season.name if season else "this season"
            embed = build_effects_list_embed(effects_data, season_name=season_name)
            await interaction.followup.send(embed=embed)
        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("discord_effects_failed")
            await interaction.followup.send(
                "Could not load active effects right now.",
                ephemeral=True,
            )

    async def _handle_repeal(self, interaction: discord.Interaction, effect_id: str) -> None:
        """Handle the /repeal slash command — propose repealing an active effect."""
        if not self.engine:
            await interaction.response.send_message(
                "The league database is temporarily unavailable.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(self.engine, str(interaction.user.id))
        except GovernorNotFound as exc:
            await interaction.followup.send(
                str(exc) or "You need to `/join` a team first.",
                ephemeral=True,
            )
            return

        try:
            from pinwheel.core.effects import load_effect_registry
            from pinwheel.core.governance import REPEAL_TOKEN_COST
            from pinwheel.core.tokens import get_token_balance, has_token
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository
            from pinwheel.discord.embeds import build_repeal_confirm_embed
            from pinwheel.discord.views import RepealConfirmView

            async with get_session(self.engine) as session:
                repo = Repository(session)

                # Check tokens
                if not await has_token(
                    repo,
                    gov.player_id,
                    gov.season_id,
                    "propose",
                ):
                    await interaction.followup.send(
                        "You don't have any PROPOSE tokens left. "
                        "Use `/tokens` to check your balance.",
                        ephemeral=True,
                    )
                    return

                balance = await get_token_balance(
                    repo, gov.player_id, gov.season_id
                )

                if balance.propose < REPEAL_TOKEN_COST:
                    await interaction.followup.send(
                        f"A repeal proposal costs {REPEAL_TOKEN_COST} PROPOSE tokens, "
                        f"but you only have {balance.propose}.",
                        ephemeral=True,
                    )
                    return

                # Load registry and find the target effect
                registry = await load_effect_registry(repo, gov.season_id)

            target = registry.get_effect(effect_id)
            if target is None:
                await interaction.followup.send(
                    "That effect is no longer active. "
                    "Use `/effects` to see current active effects.",
                    ephemeral=True,
                )
                return

            # Parameter changes cannot be repealed via this mechanism
            if target.effect_type == "parameter_change":
                await interaction.followup.send(
                    "Parameter changes cannot be repealed. "
                    "Submit a new `/propose` to change the parameter to a different value.",
                    ephemeral=True,
                )
                return

            desc = target.description or target.narrative_instruction or target.effect_type

            # Spend PROPOSE token NOW (before confirm UI) to prevent race conditions
            async with get_session(self.engine) as spend_session:
                spend_repo = Repository(spend_session)
                await spend_repo.append_event(
                    event_type="token.spent",
                    aggregate_id=gov.player_id,
                    aggregate_type="token",
                    season_id=gov.season_id,
                    governor_id=gov.player_id,
                    team_id=gov.team_id,
                    payload={
                        "token_type": "propose",
                        "amount": REPEAL_TOKEN_COST,
                        "reason": "repeal:pending_confirm",
                    },
                )
                await spend_session.commit()

            view = RepealConfirmView(
                original_user_id=interaction.user.id,
                target_effect_id=effect_id,
                effect_description=desc,
                effect_type=target.effect_type,
                token_cost=REPEAL_TOKEN_COST,
                governor_info=gov,
                engine=self.engine,
                token_already_spent=True,
            )
            embed = build_repeal_confirm_embed(
                effect_description=desc,
                effect_type=target.effect_type,
                effect_id=effect_id,
                token_cost=REPEAL_TOKEN_COST,
                tokens_remaining=balance.propose - REPEAL_TOKEN_COST,
                governor_name=interaction.user.display_name,
            )
            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True,
            )
        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("discord_repeal_failed")
            await interaction.followup.send(
                "Your repeal proposal could not be processed right now. "
                "Try `/repeal` again. If it keeps failing, ask an admin for help.",
                ephemeral=True,
            )

    async def _autocomplete_effects(
        self, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for the /repeal effect parameter.

        Returns active non-parameter effects with their short IDs and descriptions.
        """
        if not self.engine:
            return []

        try:
            from pinwheel.core.effects import load_effect_registry
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                season = await repo.get_active_season()
                if not season:
                    return []

                registry = await load_effect_registry(repo, season.id)
                active = registry.get_all_active()

            choices: list[app_commands.Choice[str]] = []
            for effect in active:
                # Skip parameter_change effects — cannot be repealed
                if effect.effect_type == "parameter_change":
                    continue

                desc = effect.description or effect.narrative_instruction or effect.effect_type
                short_id = effect.effect_id[-8:]
                label = f"{desc[:80]} ({short_id})"

                if current and current.lower() not in label.lower():
                    continue

                choices.append(
                    app_commands.Choice(name=label[:100], value=effect.effect_id)
                )
                if len(choices) >= 25:
                    break

            return choices
        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("discord_effect_autocomplete_failed")
            return []

    async def _handle_schedule(self, interaction: discord.Interaction) -> None:
        """Handle the /schedule slash command."""
        await interaction.response.defer()
        upcoming_rounds = await self._query_schedule()

        # Group each round's games into time slots (no team plays twice
        # per slot) and compute one cron fire time per slot.
        upcoming_slots: list[dict] = []
        if upcoming_rounds:
            try:
                from pinwheel.core.schedule_times import (
                    compute_round_start_times,
                    format_game_time,
                    group_into_slots,
                )

                all_slots: list[list[dict]] = []
                for rd in upcoming_rounds:
                    all_slots.extend(
                        group_into_slots(
                            rd["games"],
                            home_key="home_team_name",
                            away_key="away_team_name",
                        )
                    )

                effective_cron = self.settings.effective_game_cron()
                start_times: list[str] = []
                if effective_cron:
                    times = compute_round_start_times(
                        effective_cron,
                        len(all_slots),
                    )
                    start_times = [format_game_time(t) for t in times]

                for idx, slot_games in enumerate(all_slots):
                    upcoming_slots.append(
                        {
                            "start_time": (
                                start_times[idx]
                                if idx < len(start_times)
                                else None
                            ),
                            "games": slot_games,
                        }
                    )
            except (ValueError, TypeError, KeyError):
                logger.debug("discord_schedule_slots_failed", exc_info=True)
                # Fallback: show rounds without slot grouping
                for rd in upcoming_rounds:
                    upcoming_slots.append(
                        {"start_time": None, "games": rd["games"]}
                    )

        embed = build_schedule_embed(upcoming_slots)
        await interaction.followup.send(embed=embed)

    async def _query_schedule(self) -> list[dict]:
        """Query all upcoming unplayed rounds' schedules.

        Returns a list of round dicts, each with ``round_number`` and
        ``games`` (list of matchup dicts with team names).
        """
        if not self.engine:
            return []
        try:
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                season = await repo.get_active_season()
                if not season:
                    return []

                # Find the latest played round
                latest_played = await repo.get_latest_round_number(season.id) or 0

                # Get all remaining scheduled rounds
                full_schedule = await repo.get_full_schedule(season.id)
                remaining: dict[int, list] = {}
                for entry in full_schedule:
                    if entry.round_number > latest_played:
                        remaining.setdefault(entry.round_number, []).append(entry)

                result: list[dict] = []
                for rn in sorted(remaining.keys()):
                    games_list: list[dict] = []
                    for m in remaining[rn]:
                        home = await repo.get_team(m.home_team_id)
                        away = await repo.get_team(m.away_team_id)
                        games_list.append(
                            {
                                "home_team_name": home.name if home else m.home_team_id,
                                "away_team_name": away.name if away else m.away_team_id,
                            }
                        )
                    result.append(
                        {
                            "round_number": rn,
                            "games": games_list,
                        }
                    )
                return result
        except SQLAlchemyError:
            logger.exception("discord_schedule_query_failed")
            return []

    async def _handle_reports(self, interaction: discord.Interaction) -> None:
        """Handle the /reports slash command."""
        await interaction.response.defer()
        reports = await self._query_latest_reports()
        if not reports:
            embed = discord.Embed(
                title="Latest Reports",
                description=(
                    "No reports have been generated yet. Reports appear after each round."
                ),
                color=0x9B59B6,
            )
            embed.set_footer(text="Pinwheel Fates")
            await interaction.followup.send(embed=embed)
            return

        # Send the most recent public report
        from pinwheel.models.report import Report

        m = reports[0]
        report = Report(
            id=m["id"],
            report_type=m["report_type"],  # type: ignore[arg-type]
            round_number=m["round_number"],
            content=m["content"],
        )
        embed = build_report_embed(report)
        await interaction.followup.send(embed=embed)

    async def _query_latest_reports(self) -> list[dict]:
        """Query the most recent public reports."""
        if not self.engine:
            return []
        try:

            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                season = await repo.get_active_season()
                if not season:
                    return []

                # Try simulation report first, then governance
                for mtype in ("simulation", "governance"):
                    m = await repo.get_latest_report(season.id, mtype)
                    if m:
                        return [
                            {
                                "id": m.id,
                                "report_type": m.report_type,
                                "round_number": m.round_number,
                                "content": m.content,
                            }
                        ]
                return []
        except SQLAlchemyError:
            logger.exception("discord_reports_query_failed")
            return []

    async def _handle_vote(
        self,
        interaction: discord.Interaction,
        choice: str,
        boost: bool = False,
        proposal_selector: str = "",
    ) -> None:
        """Handle the /vote slash command. Votes are hidden until window closes."""
        if not self.engine:
            await interaction.response.send_message(
                "The league database is temporarily unavailable. "
                "Try `/vote` again in a moment -- if this persists, let an admin know.",
                ephemeral=True,
            )
            return

        # Defer immediately — DB calls can exceed 3s interaction timeout
        await interaction.response.defer(ephemeral=True)

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(self.engine, str(interaction.user.id))
        except GovernorNotFound as exc:
            await interaction.followup.send(
                str(exc) or "You need to `/join` a team first.",
                ephemeral=True,
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

                # Find confirmed proposals that haven't been resolved yet
                confirmed = await repo.get_events_by_type(
                    season_id=gov.season_id,
                    event_types=["proposal.confirmed"],
                )
                resolved = await repo.get_events_by_type(
                    season_id=gov.season_id,
                    event_types=["proposal.passed", "proposal.failed"],
                )
                resolved_ids = {e.aggregate_id for e in resolved}
                pending = [
                    c
                    for c in confirmed
                    if c.payload.get("proposal_id", c.aggregate_id) not in resolved_ids
                ]
                if not pending:
                    await interaction.followup.send(
                        "No proposals are currently open for voting.",
                        ephemeral=True,
                    )
                    return

                # Select proposal: by selector or default to latest
                proposal_id: str | None = None
                if proposal_selector:
                    # Try direct aggregate_id match first
                    for p in pending:
                        pid = p.payload.get("proposal_id", p.aggregate_id)
                        if pid == proposal_selector:
                            proposal_id = pid
                            break
                    # Try text match via submitted events
                    if not proposal_id:
                        submitted_for_match = await repo.get_events_by_type(
                            season_id=gov.season_id,
                            event_types=["proposal.submitted"],
                        )
                        for p in pending:
                            pid = p.payload.get("proposal_id", p.aggregate_id)
                            for se in submitted_for_match:
                                if se.aggregate_id == pid:
                                    raw = se.payload.get("raw_text", "")
                                    if proposal_selector.lower() in raw.lower():
                                        proposal_id = pid
                                        break
                            if proposal_id:
                                break
                    if not proposal_id:
                        await interaction.followup.send(
                            "Could not find an open proposal matching your selection. "
                            "Use `/proposals` to see what's currently on the Floor, "
                            "or try `/vote` without specifying a proposal "
                            "to vote on the latest one.",
                            ephemeral=True,
                        )
                        return
                else:
                    latest = pending[-1]
                    proposal_id = latest.payload.get(
                        "proposal_id",
                        latest.aggregate_id,
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
                    await interaction.followup.send(
                        "The proposal data could not be loaded. "
                        "It may have been removed or there may be a database issue. "
                        "Try `/proposals` to see what's currently on the Floor.",
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
                        await interaction.followup.send(
                            "You've already voted on this proposal.",
                            ephemeral=True,
                        )
                        return

                # Check boost token if requested
                if boost and not await has_token(
                    repo,
                    gov.player_id,
                    gov.season_id,
                    "boost",
                ):
                    await interaction.followup.send(
                        "You don't have any BOOST tokens to double your vote weight. "
                        "Use `/tokens` to check your balance. "
                        "You can still vote without boosting: `/vote yes` or `/vote no`.",
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
                    "Votes are hidden until the Floor "
                    "closes."
                ),
                color=0x3498DB,
            )
            embed.set_footer(text="Pinwheel Fates")
            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
            )
        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("discord_vote_failed")
            await interaction.followup.send(
                "Your vote could not be recorded right now. "
                "This might be a temporary database issue -- "
                "try `/vote` again. If it keeps failing, let an admin know.",
                ephemeral=True,
            )

    async def _handle_tokens(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Handle the /tokens slash command."""
        if not self.engine:
            await interaction.response.send_message(
                "The league database is temporarily unavailable. "
                "Try `/tokens` again in a moment -- if this persists, let an admin know.",
                ephemeral=True,
            )
            return

        # Defer immediately — DB calls can exceed 3s interaction timeout
        await interaction.response.defer(ephemeral=True)

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(self.engine, str(interaction.user.id))
        except GovernorNotFound as exc:
            await interaction.followup.send(
                str(exc) or "You need to `/join` a team first.",
                ephemeral=True,
            )
            return

        try:
            from pinwheel.core.tokens import get_token_balance
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                balance = await get_token_balance(
                    repo,
                    gov.player_id,
                    gov.season_id,
                )

            embed = build_token_balance_embed(
                balance,
                governor_name=interaction.user.display_name,
            )
            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
            )
        except SQLAlchemyError:
            logger.exception("discord_tokens_failed")
            await interaction.followup.send(
                "Could not retrieve your token balance right now. "
                "Try `/tokens` again -- if this persists, let an admin know.",
                ephemeral=True,
            )

    async def _handle_profile(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Handle the /profile slash command -- show governor's governance record."""
        if not self.engine:
            await interaction.response.send_message(
                "The league database is temporarily unavailable. "
                "Try `/profile` again in a moment -- if this persists, let an admin know.",
                ephemeral=True,
            )
            return

        # Defer immediately — DB calls can exceed 3s interaction timeout
        await interaction.response.defer(ephemeral=True)

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(self.engine, str(interaction.user.id))
        except GovernorNotFound as exc:
            await interaction.followup.send(
                str(exc) or "You need to `/join` a team first.",
                ephemeral=True,
            )
            return

        try:
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                activity = await repo.get_governor_activity(
                    gov.player_id,
                    gov.season_id,
                )

            embed = build_governor_profile_embed(
                governor_name=interaction.user.display_name,
                team_name=gov.team_name,
                activity=activity,
            )
            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
            )
        except SQLAlchemyError:
            logger.exception("discord_profile_failed")
            await interaction.followup.send(
                "Could not load your governor profile right now. "
                "Try `/profile` again -- if this persists, let an admin know.",
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
                "The league database is temporarily unavailable. "
                "Try `/trade` again in a moment -- if this persists, let an admin know.",
                ephemeral=True,
            )
            return

        if target.id == interaction.user.id:
            await interaction.response.send_message(
                "You can't trade with yourself.",
                ephemeral=True,
            )
            return

        # Defer immediately — DB calls can exceed 3s interaction timeout
        await interaction.response.defer(ephemeral=True)

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(
                self.engine,
                str(interaction.user.id),
            )
        except GovernorNotFound as exc:
            await interaction.followup.send(
                str(exc) or "You need to `/join` a team first.",
                ephemeral=True,
            )
            return

        try:
            target_gov = await get_governor(
                self.engine,
                str(target.id),
            )
        except GovernorNotFound:
            await interaction.followup.send(
                f"**{target.display_name}** isn't enrolled as a governor this season. "
                "They need to `/join` a team before you can trade with them.",
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
                    repo,
                    gov.player_id,
                    gov.season_id,
                )
                available = getattr(balance, offer_type, 0)
                if available < offer_amount:
                    await interaction.followup.send(
                        f"You only have {available} {offer_type.upper()} tokens.",
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

            await interaction.followup.send(
                f"Trade offer sent to **{target.display_name}**!",
                ephemeral=True,
            )
        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("discord_trade_failed")
            await interaction.followup.send(
                f"Could not send the trade offer to **{target.display_name}** right now. "
                "This might be a temporary issue -- try `/trade` again. "
                "If it keeps failing, let an admin know.",
                ephemeral=True,
            )

    async def _autocomplete_hoopers(
        self,
        interaction: discord.Interaction,
        current: str,
        *,
        own_team: bool,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete hooper names for trade-hooper command."""
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
                    hoopers = await repo.get_hoopers_for_team(gov.team_id)
                else:
                    teams = await repo.get_teams_for_season(gov.season_id)
                    hoopers = []
                    for t in teams:
                        if t.id != gov.team_id:
                            team_hoopers = await repo.get_hoopers_for_team(t.id)
                            hoopers.extend(team_hoopers)
                lowered = current.lower()
                return [
                    app_commands.Choice(name=h.name, value=h.name)
                    for h in hoopers
                    if lowered in h.name.lower()
                ][:25]
        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("hooper_autocomplete_failed")
            return []

    async def _handle_trade_hooper(
        self,
        interaction: discord.Interaction,
        offer_hooper_name: str,
        request_hooper_name: str,
    ) -> None:
        """Handle the /trade-hooper slash command."""
        if not self.engine:
            await interaction.response.send_message(
                "The league database is temporarily unavailable. "
                "Try `/trade-hooper` again in a moment -- if this persists, let an admin know.",
                ephemeral=True,
            )
            return

        # Defer immediately — DB calls can exceed 3s interaction timeout
        await interaction.response.defer(ephemeral=True)

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(self.engine, str(interaction.user.id))
        except GovernorNotFound as exc:
            await interaction.followup.send(
                str(exc) or "You must `/join` a team before trading hoopers.",
                ephemeral=True,
            )
            return

        try:
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)

                # Find the offered hooper (must be on proposer's team)
                my_hoopers = await repo.get_hoopers_for_team(gov.team_id)
                offered = None
                for h in my_hoopers:
                    if h.name.lower() == offer_hooper_name.lower():
                        offered = h
                        break
                if not offered:
                    available = ", ".join(h.name for h in my_hoopers)
                    await interaction.followup.send(
                        f"Hooper not found on your team. Your hoopers: {available}",
                        ephemeral=True,
                    )
                    return

                # Find the requested hooper (must be on a different team)
                teams = await repo.get_teams_for_season(gov.season_id)
                requested = None
                target_team = None
                for t in teams:
                    if t.id == gov.team_id:
                        continue
                    for h in t.hoopers:
                        if h.name.lower() == request_hooper_name.lower():
                            requested = h
                            target_team = t
                            break
                    if requested:
                        break
                if not requested or not target_team:
                    await interaction.followup.send(
                        f"Hooper '{request_hooper_name}' not found on any other team.",
                        ephemeral=True,
                    )
                    return

                # Get all governors on both teams
                from_govs = await repo.get_governors_for_team(
                    gov.team_id,
                    gov.season_id,
                )
                to_govs = await repo.get_governors_for_team(
                    target_team.id,
                    gov.season_id,
                )
                from_voter_ids = [p.discord_id for p in from_govs]
                to_voter_ids = [p.discord_id for p in to_govs]
                all_voters = from_voter_ids + to_voter_ids

                if len(all_voters) < 2:
                    await interaction.followup.send(
                        "Both teams need at least one governor to vote on a trade.",
                        ephemeral=True,
                    )
                    return

                from pinwheel.core.tokens import propose_hooper_trade

                my_team = next(
                    (t for t in teams if t.id == gov.team_id),
                    None,
                )
                trade = await propose_hooper_trade(
                    repo=repo,
                    proposer_id=gov.player_id,
                    from_team_id=gov.team_id,
                    to_team_id=target_team.id,
                    offered_hooper_ids=[offered.id],
                    requested_hooper_ids=[requested.id],
                    offered_hooper_names=[offered.name],
                    requested_hooper_names=[requested.name],
                    from_team_name=my_team.name if my_team else gov.team_id,
                    to_team_name=target_team.name,
                    required_voters=all_voters,
                    from_team_voters=from_voter_ids,
                    to_team_voters=to_voter_ids,
                    season_id=gov.season_id,
                )
                await session.commit()

            # Post trade view to both team channels
            from pinwheel.discord.embeds import build_hooper_trade_embed
            from pinwheel.discord.views import HooperTradeView

            view = HooperTradeView(
                trade=trade,
                season_id=gov.season_id,
                engine=self.engine,
            )
            embed = build_hooper_trade_embed(
                from_team=trade.from_team_name,
                to_team=trade.to_team_name,
                offered_names=trade.offered_hooper_names,
                requested_names=trade.requested_hooper_names,
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
                view2 = HooperTradeView(
                    trade=trade,
                    season_id=gov.season_id,
                    engine=self.engine,
                )
                await to_ch.send(embed=embed, view=view2)

            await interaction.followup.send(
                f"Hooper trade proposed: **{offered.name}** "
                f"for **{requested.name}**. "
                "Both teams' governors must vote in their team channels.",
                ephemeral=True,
            )
        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("discord_trade_hooper_failed")
            await interaction.followup.send(
                "The hooper trade could not be created right now. "
                "This might be a temporary issue -- try `/trade-hooper` again. "
                "If it keeps failing, let an admin know.",
                ephemeral=True,
            )

    async def _handle_strategy(
        self,
        interaction: discord.Interaction,
        text: str,
    ) -> None:
        """Handle the /strategy slash command."""
        if not text.strip():
            await interaction.response.send_message(
                "Describe your team's strategy. Example: `/strategy Focus on three-point shooting`",
                ephemeral=True,
            )
            return

        if not self.engine:
            await interaction.response.send_message(
                "The league database is temporarily unavailable. "
                "Try `/strategy` again in a moment -- if this persists, let an admin know.",
                ephemeral=True,
            )
            return

        # Defer immediately — DB calls can exceed 3s interaction timeout
        await interaction.response.defer(ephemeral=True)

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(
                self.engine,
                str(interaction.user.id),
            )
        except GovernorNotFound as exc:
            await interaction.followup.send(
                str(exc) or "You need to `/join` a team first.",
                ephemeral=True,
            )
            return

        from pinwheel.discord.views import StrategyConfirmView

        view = StrategyConfirmView(
            original_user_id=interaction.user.id,
            raw_text=text,
            team_name=gov.team_name,
            governor_info=gov,
            engine=self.engine,
            api_key=self.settings.anthropic_api_key if self.settings else "",
        )
        embed = build_strategy_embed(text, gov.team_name)
        embed.set_footer(
            text="Pinwheel Fates -- Confirm or Cancel",
        )
        await interaction.followup.send(
            embed=embed,
            view=view,
            ephemeral=True,
        )

    async def _handle_bio(
        self,
        interaction: discord.Interaction,
        hooper_name: str,
        text: str,
    ) -> None:
        """Handle the /bio slash command."""
        if not text.strip():
            await interaction.response.send_message(
                "Provide a backstory for the hooper. "
                "Example: `/bio Briar Ashwood A sharpshooter from Portland...`",
                ephemeral=True,
            )
            return

        if len(text) > 500:
            await interaction.response.send_message(
                f"Bio is too long ({len(text)} chars). Max 500 characters.",
                ephemeral=True,
            )
            return

        if not self.engine:
            await interaction.response.send_message(
                "The league database is temporarily unavailable. "
                "Try `/bio` again in a moment -- if this persists, let an admin know.",
                ephemeral=True,
            )
            return

        # Defer immediately — DB calls can exceed 3s interaction timeout
        await interaction.response.defer(ephemeral=True)

        from pinwheel.discord.helpers import GovernorNotFound, get_governor

        try:
            gov = await get_governor(
                self.engine,
                str(interaction.user.id),
            )
        except GovernorNotFound as exc:
            await interaction.followup.send(
                str(exc) or "You need to `/join` a team first.",
                ephemeral=True,
            )
            return

        try:
            from pinwheel.db.engine import get_session
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)
                team = await repo.get_team(gov.team_id)
                if not team:
                    await interaction.followup.send(
                        "Your team could not be found in the database. "
                        "This is unexpected -- let an admin know so they can investigate.",
                        ephemeral=True,
                    )
                    return

                target_hooper = None
                for h in team.hoopers:
                    if h.name.lower() == hooper_name.lower():
                        target_hooper = h
                        break

                if not target_hooper:
                    available = ", ".join(h.name for h in team.hoopers)
                    await interaction.followup.send(
                        f"Hooper not found on your team. Your hoopers: {available}",
                        ephemeral=True,
                    )
                    return

                await repo.update_hooper_backstory(target_hooper.id, text)
                await session.commit()

            from pinwheel.discord.embeds import build_bio_embed

            embed = build_bio_embed(target_hooper.name, text)
            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
            )
        except SQLAlchemyError:
            logger.exception("discord_bio_failed")
            await interaction.followup.send(
                f"Could not save the bio for **{hooper_name}** right now. "
                "Try `/bio` again -- if this persists, let an admin know.",
                ephemeral=True,
            )

    async def _handle_new_season(
        self,
        interaction: discord.Interaction,
        name: str,
        carry_rules: bool = True,
    ) -> None:
        """Handle the /new-season slash command (admin only)."""
        # Check admin permissions
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "`/new-season` can only be used inside a Discord server, not in DMs.",
                ephemeral=True,
            )
            return

        admin_discord_id = self.settings.pinwheel_admin_discord_id
        if not admin_discord_id or str(interaction.user.id) != admin_discord_id:
            await interaction.response.send_message(
                "`/new-season` is restricted to the configured league administrator. "
                "Ask an admin to start the new season for you.",
                ephemeral=True,
            )
            return

        if not self.engine:
            await interaction.response.send_message(
                "The league database is temporarily unavailable. "
                "Try `/new-season` again in a moment -- if this persists, check the server logs.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            from sqlalchemy import select

            from pinwheel.core.season import start_new_season
            from pinwheel.db.engine import get_session
            from pinwheel.db.models import SeasonRow
            from pinwheel.db.repository import Repository

            async with get_session(self.engine) as session:
                repo = Repository(session)

                # Find the league from the current/latest season
                result = await session.execute(
                    select(SeasonRow).order_by(SeasonRow.created_at.desc()).limit(1),
                )
                latest_season = result.scalar_one_or_none()
                if not latest_season:
                    await interaction.followup.send(
                        "No existing season or league found in the database. "
                        "The league needs to be seeded first -- "
                        "run `demo_seed.py seed` or set up the league through the API.",
                        ephemeral=True,
                    )
                    return

                league_id = latest_season.league_id

                new_season = await start_new_season(
                    repo=repo,
                    league_id=league_id,
                    season_name=name,
                    carry_forward_rules=carry_rules,
                    previous_season_id=latest_season.id,
                )

                teams = await repo.get_teams_for_season(new_season.id)
                await session.commit()

            rules_note = "carried forward" if carry_rules else "default"
            embed = discord.Embed(
                title=f"New Season: {name}",
                description=(
                    f"Season **{name}** has been created!\n\n"
                    f"**Teams:** {len(teams)}\n"
                    f"**Rules:** {rules_note}\n"
                    f"**Status:** {new_season.status}\n\n"
                    "Teams, hoopers, and governor enrollments have been "
                    "carried over. All governors have received fresh tokens.\n\n"
                    "Run `/schedule` to see the matchups."
                ),
                color=0x2ECC71,
            )
            embed.set_footer(text="Pinwheel Fates")
            await interaction.followup.send(embed=embed)

            # Announce in the main channel
            channel = self._get_channel_for("play_by_play")
            if not channel:
                channel = self._get_channel_for("main")
            if channel:
                announce_embed = discord.Embed(
                    title=f"New Season: {name}",
                    description=(
                        f"A new season has begun! **{name}** is now active.\n\n"
                        f"**{len(teams)} teams** are ready to compete.\n"
                        f"Rules: {rules_note}.\n\n"
                        "Fresh tokens have been distributed to all governors.\n\n"
                        "Run `/schedule` to see the matchups."
                    ),
                    color=0x2ECC71,
                )
                announce_embed.set_footer(text="Pinwheel Fates")
                with contextlib.suppress(discord.Forbidden, discord.HTTPException):
                    await channel.send(embed=announce_embed)

        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("discord_new_season_failed")
            await interaction.followup.send(
                f"Could not create season **{name}** right now. "
                "This might be a database issue -- try `/new-season` again. "
                "If it keeps failing, check the server logs for details.",
                ephemeral=True,
            )

    async def _handle_activate_mechanic(
        self,
        interaction: discord.Interaction,
        effect_id: str,
        hook_point: str,
        action_type: str,
        modifier: float,
    ) -> None:
        """Handle /activate-mechanic — admin activates a pending custom mechanic."""
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True,
            )
            return

        # Admin check — use configured admin Discord ID (same model as web auth)
        admin_discord_id = self.settings.pinwheel_admin_discord_id
        if not admin_discord_id or str(interaction.user.id) != admin_discord_id:
            await interaction.response.send_message(
                "`/activate-mechanic` is restricted to the configured league administrator.",
                ephemeral=True,
            )
            return

        if not self.engine:
            await interaction.response.send_message(
                "Database unavailable.", ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        from pinwheel.core.effects import activate_custom_mechanic, load_effect_registry
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        try:
            async with get_session(self.engine) as session:
                repo = Repository(session)
                seasons = await repo.get_all_seasons()
                if not seasons:
                    await interaction.followup.send("No active season.", ephemeral=True)
                    return

                season = seasons[-1]
                registry = await load_effect_registry(repo, season.id)

                action_code = None
                if action_type and modifier != 0.0:
                    action_code = {"type": action_type, "modifier": modifier}

                success = await activate_custom_mechanic(
                    repo=repo,
                    registry=registry,
                    effect_id=effect_id,
                    season_id=season.id,
                    hook_point=hook_point or None,
                    action_code=action_code,
                )
                await session.commit()

            if success:
                effect = registry.get_effect(effect_id)
                desc = effect.description if effect else effect_id
                embed = discord.Embed(
                    title="Mechanic Activated",
                    description=f"**{desc}** is now live.",
                    color=0x2ECC71,
                )
                embed.set_footer(text="Pinwheel Fates")
                await interaction.followup.send(embed=embed)

                # Announce in main channel
                channel = self._get_channel_for("play_by_play")
                if not channel:
                    channel = self._get_channel_for("main")
                if channel:
                    announce = discord.Embed(
                        title="New Mechanic Activated",
                        description=f"A new mechanic is now live: **{desc}**",
                        color=0x2ECC71,
                    )
                    with contextlib.suppress(discord.Forbidden, discord.HTTPException):
                        await channel.send(embed=announce)
            else:
                await interaction.followup.send(
                    "Effect not found or not a pending custom mechanic.",
                    ephemeral=True,
                )
        except (SQLAlchemyError, discord.HTTPException):
            logger.exception("activate_mechanic_failed")
            await interaction.followup.send(
                "Could not activate the mechanic. Check the server logs.",
                ephemeral=True,
            )

    async def _autocomplete_pending_mechanics(
        self,
        interaction: discord.Interaction,  # noqa: ARG002
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for /activate-mechanic — list pending custom_mechanic effects."""
        if not self.engine:
            return []

        from pinwheel.core.effects import load_effect_registry
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        try:
            async with get_session(self.engine) as session:
                repo = Repository(session)
                seasons = await repo.get_all_seasons()
                if not seasons:
                    return []

                season = seasons[-1]
                registry = await load_effect_registry(repo, season.id)

            choices: list[app_commands.Choice[str]] = []
            for effect in registry.get_all_active():
                if effect.effect_type != "custom_mechanic":
                    continue
                label = effect.description[:100] or effect.effect_id[:100]
                if current and current.lower() not in label.lower():
                    continue
                choices.append(app_commands.Choice(name=label, value=effect.effect_id))
                if len(choices) >= 25:
                    break
            return choices
        except SQLAlchemyError:
            logger.debug("mechanic_autocomplete_failed", exc_info=True)
            return []

    async def _send_private_report(self, data: dict) -> None:
        """DM a private report to the governor."""
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

            from pinwheel.models.report import Report

            report = Report(
                id=str(data.get("report_id", "")),
                report_type="private",
                round_number=round_num,
                content=excerpt,
            )
            embed = build_report_embed(report)
            embed.title = f"Private Report -- Round {round_num}"
            await user.send(embed=embed)
        except (SQLAlchemyError, discord.HTTPException, discord.Forbidden):
            logger.exception(
                "private_report_dm_failed governor=%s",
                governor_id,
            )

    async def _handle_edit_series(
        self,
        interaction: discord.Interaction,
        report_id: str,
    ) -> None:
        """Handle /edit-series — open a modal to edit a series report.

        Auth: only governors whose team_id matches winner_id or loser_id
        in the report's metadata_json can edit.
        """
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository
        from pinwheel.discord.helpers import get_governor_info
        from pinwheel.discord.views import EditSeriesModal

        try:
            async with get_session(self.engine) as session:
                repo = Repository(session)

                governor_info = await get_governor_info(repo, str(interaction.user.id))
                if governor_info is None:
                    await interaction.response.send_message(
                        "You need to `/join` a team first.",
                        ephemeral=True,
                    )
                    return

                # Fetch the report
                report_row = await session.get(
                    __import__("pinwheel.db.models", fromlist=["ReportRow"]).ReportRow,
                    report_id,
                )
                if report_row is None or report_row.report_type != "series":
                    await interaction.response.send_message(
                        "Series report not found.",
                        ephemeral=True,
                    )
                    return

                # Auth check: governor's team must be winner or loser
                metadata = report_row.metadata_json or {}
                winner_id = metadata.get("winner_id", "")
                loser_id = metadata.get("loser_id", "")

                if governor_info.team_id not in (winner_id, loser_id):
                    await interaction.response.send_message(
                        "Only governors on the participating teams can edit this report.",
                        ephemeral=True,
                    )
                    return

                # Resolve team names
                winner_team = await repo.get_team(winner_id)
                loser_team = await repo.get_team(loser_id)
                winner_name = winner_team.name if winner_team else winner_id
                loser_name = loser_team.name if loser_team else loser_id
                series_type = metadata.get("series_type", "playoff")
                current_content = report_row.content or ""

            modal = EditSeriesModal(
                report_id=report_id,
                season_id=governor_info.season_id,
                series_type=series_type,
                winner_name=winner_name,
                loser_name=loser_name,
                current_content=current_content,
                engine=self.engine,
            )
            await interaction.response.send_modal(modal)

        except (SQLAlchemyError, AttributeError, discord.HTTPException):
            logger.exception("edit_series_failed")
            await interaction.response.send_message(
                "Could not open the series report editor right now.",
                ephemeral=True,
            )

    async def _autocomplete_series_reports(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for /edit-series — show series reports the governor can edit."""
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository
        from pinwheel.discord.helpers import get_governor_info

        choices: list[app_commands.Choice[str]] = []
        try:
            async with get_session(self.engine) as session:
                repo = Repository(session)
                governor_info = await get_governor_info(repo, str(interaction.user.id))
                if governor_info is None:
                    return choices

                reports = await repo.get_series_reports(governor_info.season_id)
                for r in reports:
                    metadata = r.metadata_json or {}
                    winner_id = metadata.get("winner_id", "")
                    loser_id = metadata.get("loser_id", "")

                    # Only show reports where the governor's team participated
                    if governor_info.team_id not in (winner_id, loser_id):
                        continue

                    series_type = metadata.get("series_type", "series")
                    record = metadata.get("record", "")
                    label = f"{series_type.title()} Series"
                    if record:
                        label += f" ({record})"
                    # Truncate to Discord's 100-char limit
                    label = label[:100]

                    if current.lower() in label.lower() or not current:
                        choices.append(app_commands.Choice(name=label, value=r.id))

                    if len(choices) >= 25:
                        break
        except SQLAlchemyError:
            logger.exception("series_report_autocomplete_failed")

        return choices

    async def close(self) -> None:
        """Clean shutdown: cancel event listener and close bot."""
        if self._event_listener_task and not self._event_listener_task.done():
            self._event_listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._event_listener_task
        await super().close()


async def _gather_season_context(
    repo: object,
    season: object,
) -> dict[str, object]:
    """Gather season context for the welcome embed while the DB session is open.

    Returns a dict with season_name, season_phase, current_round, and
    total_rounds.  All values are plain Python types (not ORM objects) so
    they remain usable after the session closes.

    Args:
        repo: A Repository instance (typed as object to avoid import at module level).
        season: A SeasonRow instance.
    """
    from pinwheel.core.season import normalize_phase

    season_name = getattr(season, "name", "") or ""
    season_status = getattr(season, "status", "active") or "active"
    season_id = getattr(season, "id", "")
    phase = normalize_phase(season_status)

    # Determine the current round (max round with played games) and total rounds
    current_round = 0
    total_rounds = 0
    try:
        games = await repo.get_all_games(season_id)  # type: ignore[union-attr]
        if games:
            current_round = max(g.round_number for g in games)

        schedule = await repo.get_full_schedule(season_id, phase="regular")  # type: ignore[union-attr]
        if schedule:
            total_rounds = max(s.round_number for s in schedule)
    except SQLAlchemyError:
        logger.debug("welcome_season_context_round_lookup_failed", exc_info=True)

    return {
        "season_name": season_name,
        "season_phase": phase.value if hasattr(phase, "value") else str(phase),
        "current_round": current_round,
        "total_rounds": total_rounds,
    }


def is_discord_enabled(settings: Settings) -> bool:
    """Check whether Discord integration should be started.

    Returns True only when discord_enabled is True, a token is set, AND the
    environment is not development.  This prevents a local dev server from
    accidentally connecting to the production Discord guild and creating
    duplicate channels / syncing commands.
    """
    if settings.pinwheel_env == "development":
        logger.info("discord_bot_skipped_in_development")
        return False
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
        except Exception:  # Last-resort handler — bot.start can raise connection and auth errors
            logger.exception("discord_bot_error")
        finally:
            if not bot.is_closed():
                await bot.close()

    asyncio.create_task(_run_bot(), name="discord-bot")
    logger.info("discord_bot_started")
    return bot
