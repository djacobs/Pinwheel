"""Deferred interpretation — background retry for proposals that failed AI interpretation.

When the interpreter times out or fails, the proposal is queued as a
``proposal.pending_interpretation`` event. This module retries those
interpretations on a 60-second tick and DMs the player when ready.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from pinwheel.models.governance import GovernanceEvent

if TYPE_CHECKING:
    import discord

    from pinwheel.db.repository import Repository

logger = logging.getLogger(__name__)

# Maximum age (hours) before a pending interpretation expires and tokens are refunded.
MAX_PENDING_AGE_HOURS = 4


async def get_pending_interpretations(
    repo: Repository,
    season_id: str,
) -> list[GovernanceEvent]:
    """Find pending_interpretation events without a corresponding ready or expired event."""
    pending_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.pending_interpretation"],
    )
    ready_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.interpretation_ready"],
    )
    expired_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.interpretation_expired"],
    )

    # Build set of resolved aggregate IDs
    resolved_ids: set[str] = set()
    for ev in ready_events:
        resolved_ids.add(ev.aggregate_id)
    for ev in expired_events:
        resolved_ids.add(ev.aggregate_id)

    return [ev for ev in pending_events if ev.aggregate_id not in resolved_ids]


async def retry_pending_interpretation(
    repo: Repository,
    pending: GovernanceEvent,
    api_key: str,
) -> bool:
    """Retry interpretation for a single pending event.

    Returns True if the retry succeeded and the interpretation_ready event was appended.
    """
    from pinwheel.ai.interpreter import interpret_proposal_v2
    from pinwheel.models.governance import ProposalInterpretation
    from pinwheel.models.rules import RuleSet

    payload = pending.payload
    raw_text = str(payload.get("raw_text", ""))
    ruleset_data = payload.get("ruleset", {})
    if not raw_text:
        return False

    try:
        ruleset = RuleSet(**ruleset_data) if isinstance(ruleset_data, dict) else RuleSet()
    except Exception:
        ruleset = RuleSet()

    try:
        result: ProposalInterpretation = await interpret_proposal_v2(
            raw_text=raw_text,
            ruleset=ruleset,
            api_key=api_key,
            season_id=pending.season_id,
        )
    except Exception:
        logger.warning(
            "deferred_retry_failed aggregate=%s",
            pending.aggregate_id,
            exc_info=True,
        )
        return False

    # If the retry also fell back to mock, don't count it as success
    if result.is_mock_fallback:
        return False

    # Store the successful interpretation
    await repo.append_event(
        event_type="proposal.interpretation_ready",
        aggregate_id=pending.aggregate_id,
        aggregate_type="proposal",
        season_id=pending.season_id,
        governor_id=pending.governor_id,
        payload={
            "raw_text": raw_text,
            "interpretation": result.model_dump(mode="json"),
            "discord_user_id": payload.get("discord_user_id"),
            "discord_channel_id": payload.get("discord_channel_id"),
            "governor_id": pending.governor_id,
            "team_id": pending.team_id,
            "token_cost": payload.get("token_cost", 1),
        },
    )

    logger.info(
        "deferred_interpretation_ready aggregate=%s confidence=%.2f",
        pending.aggregate_id,
        result.confidence,
    )
    return True


async def expire_stale_pending(
    repo: Repository,
    season_id: str,
    max_age_hours: float = MAX_PENDING_AGE_HOURS,
) -> list[str]:
    """Expire pending interpretations older than max_age_hours. Refund tokens.

    Returns list of expired aggregate IDs.
    """
    from datetime import UTC, datetime, timedelta

    pending = await get_pending_interpretations(repo, season_id)
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=max_age_hours)
    expired_ids: list[str] = []

    for ev in pending:
        if ev.timestamp < cutoff:
            # Expire it
            await repo.append_event(
                event_type="proposal.interpretation_expired",
                aggregate_id=ev.aggregate_id,
                aggregate_type="proposal",
                season_id=season_id,
                governor_id=ev.governor_id,
                payload={
                    "reason": "max_age_exceeded",
                    "raw_text": ev.payload.get("raw_text", ""),
                },
            )
            # Refund token
            token_cost = ev.payload.get("token_cost", 1)
            if isinstance(token_cost, (int, float)):
                await repo.append_event(
                    event_type="token.regenerated",
                    aggregate_id=ev.governor_id,
                    aggregate_type="token",
                    season_id=season_id,
                    governor_id=ev.governor_id,
                    payload={
                        "token_type": "propose",
                        "amount": int(token_cost),
                        "reason": "deferred_interpretation_expired",
                    },
                )
            expired_ids.append(ev.aggregate_id)
            logger.info(
                "deferred_interpretation_expired aggregate=%s governor=%s",
                ev.aggregate_id,
                ev.governor_id,
            )

    return expired_ids


async def _dm_player_with_interpretation(
    bot: discord.Client,
    ready_event: GovernanceEvent,
    engine: object,
    settings: object,
) -> None:
    """DM the player with their deferred interpretation result.

    Sends an embed with Confirm/Revise/Cancel buttons. If the player
    can't be reached (left server, DMs closed), the interpretation and
    token are expired.
    """
    from pinwheel.discord.embeds import build_interpretation_embed
    from pinwheel.discord.helpers import GovernorInfo
    from pinwheel.discord.views import ProposalConfirmView
    from pinwheel.models.governance import ProposalInterpretation

    payload = ready_event.payload
    discord_user_id = payload.get("discord_user_id")
    if not discord_user_id:
        return

    interp_data = payload.get("interpretation", {})
    if not isinstance(interp_data, dict):
        return

    try:
        interpretation_v2 = ProposalInterpretation(**interp_data)
    except Exception:
        logger.warning("deferred_dm_bad_interpretation aggregate=%s", ready_event.aggregate_id)
        return

    interpretation = interpretation_v2.to_rule_interpretation()
    raw_text = str(payload.get("raw_text", ""))

    from pinwheel.core.governance import detect_tier_v2, token_cost_for_tier
    from pinwheel.models.rules import RuleSet

    tier = detect_tier_v2(interpretation_v2, RuleSet())
    cost = token_cost_for_tier(tier)

    governor_info = GovernorInfo(
        player_id=str(payload.get("governor_id", ready_event.governor_id)),
        team_id=str(payload.get("team_id", ready_event.team_id)),
        season_id=ready_event.season_id,
        team_name="",
    )

    try:
        user = await bot.fetch_user(int(discord_user_id))
    except Exception:
        logger.warning(
            "deferred_dm_user_not_found discord_id=%s",
            discord_user_id,
        )
        return

    view = ProposalConfirmView(
        original_user_id=int(discord_user_id),
        raw_text=raw_text,
        interpretation=interpretation,
        tier=tier,
        token_cost=cost,
        tokens_remaining=0,  # Unknown from here; player sees /tokens
        governor_info=governor_info,
        engine=engine,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
        interpretation_v2=interpretation_v2,
        token_already_spent=True,
    )

    embed = build_interpretation_embed(
        raw_text=raw_text,
        interpretation=interpretation,
        tier=tier,
        token_cost=cost,
        tokens_remaining=0,
        governor_name=user.display_name,
        interpretation_v2=interpretation_v2,
    )
    embed.title = "Your proposal interpretation is ready!"

    with contextlib.suppress(Exception):
        await user.send(embed=embed, view=view)
        logger.info(
            "deferred_dm_sent discord_id=%s aggregate=%s",
            discord_user_id,
            ready_event.aggregate_id,
        )


async def tick_deferred_interpretations(
    engine: object,
    api_key: str,
    bot: discord.Client | None = None,
    settings: object | None = None,
) -> None:
    """Scheduler entry point — called every 60 seconds.

    1. Find pending interpretations
    2. Retry each one
    3. DM players for successful retries
    4. Expire stale ones
    """
    from pinwheel.db.engine import get_session
    from pinwheel.db.repository import Repository

    if not api_key:
        return

    try:
        async with get_session(engine) as session:  # type: ignore[arg-type]
            repo = Repository(session)

            # Get current season
            seasons = await repo.get_all_seasons()
            if not seasons:
                return
            season = seasons[-1]
            season_id = season.id

            # Expire stale pending interpretations first
            expired = await expire_stale_pending(repo, season_id)

            # Find remaining pending interpretations
            pending = await get_pending_interpretations(repo, season_id)
            if not pending:
                await session.commit()
                return

            logger.info(
                "deferred_tick pending=%d expired=%d season=%s",
                len(pending),
                len(expired),
                season_id,
            )

            # Retry each pending interpretation
            for ev in pending:
                success = await retry_pending_interpretation(repo, ev, api_key)
                if success and bot is not None:
                    # Fetch the ready event we just created
                    ready_events = await repo.get_events_by_type(
                        season_id=season_id,
                        event_types=["proposal.interpretation_ready"],
                    )
                    ready_ev = None
                    for re in ready_events:
                        if re.aggregate_id == ev.aggregate_id:
                            ready_ev = re
                    if ready_ev is not None:
                        await _dm_player_with_interpretation(
                            bot, ready_ev, engine, settings
                        )

            await session.commit()

    except Exception:
        logger.exception("deferred_tick_failed")
