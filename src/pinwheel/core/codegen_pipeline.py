"""Codegen pipeline — routes beyond-primitive proposals to the code council.

When the v2 interpreter emits a ``custom_mechanic`` effect, it is signaling
that the primitive vocabulary can't express the proposal. With
``PINWHEEL_CODEGEN_ENABLED`` on, those proposals additionally escalate to the
codegen council (generate → AST validate → 3-reviewer consensus) as a
background task after the player confirms. The council's output is an
ADDITIONAL codegen EffectSpec attached to the proposal — the interpreted
approximation remains what voters vote on, and stays live until an admin
approves the generated code (the Phase 2 pre-execution gate).

Crash resilience mirrors ``core/deferred_interpreter.py``: the confirm
handler appends ``proposal.codegen_requested`` before spawning the task, and
``tick_codegen_pipeline`` (60s scheduler tick) re-drives any request without
a terminal event, consumes ``/rerun-council`` requests, and DMs the admin
about pending effects registered outside an interactive context.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from pinwheel.models.governance import EffectSpec

if TYPE_CHECKING:
    import discord
    from sqlalchemy.ext.asyncio import AsyncEngine

    from pinwheel.config import Settings
    from pinwheel.db.repository import Repository
    from pinwheel.models.governance import ProposalInterpretation

logger = logging.getLogger(__name__)

# Hooks the simulation actually fires for effects. Council output targeting
# anything else would register but never execute — reject it instead.
CODEGEN_SIM_HOOKS = frozenset({
    "sim.game.pre",
    "sim.quarter.pre",
    "sim.possession.pre",
    "sim.possession.post",
    "sim.quarter.end",
    "sim.halftime",
    "sim.elam.start",
    "sim.game.end",
})

# Give up re-driving a codegen request after this many failed attempts.
MAX_CODEGEN_RETRIES = 3

# Backstop on council runs per tick — the token economy already bounds
# proposal volume, this guards against pathological event-store states.
MAX_COUNCIL_RUNS_PER_TICK = 10


def should_escalate_to_codegen(
    interpretation: ProposalInterpretation | None,
    settings: Settings,
) -> bool:
    """Whether a confirmed proposal should go to the codegen council.

    Escalate iff: the feature flag is on, the interpreter emitted a
    ``custom_mechanic`` effect (its own signal that primitives don't
    suffice), the proposal wasn't injection-flagged, and the interpreter
    was reasonably confident it understood the intent.
    """
    if not settings.pinwheel_codegen_enabled:
        return False
    if interpretation is None:
        return False
    if interpretation.injection_flagged or interpretation.rejection_reason:
        return False
    if interpretation.confidence < 0.5:
        return False
    return any(
        e.effect_type == "custom_mechanic" for e in interpretation.effects
    )


async def _current_round(repo: Repository, season_id: str) -> int:
    """The round a newly registered effect should be stamped with."""
    games = await repo.get_all_games(season_id)
    if not games:
        return 1
    return max(g.round_number for g in games) + 1


async def _proposal_has_passed(repo: Repository, season_id: str, proposal_id: str) -> bool:
    passed_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.passed"],
    )
    return any(ev.aggregate_id == proposal_id for ev in passed_events)


async def _codegen_already_registered(
    repo: Repository, season_id: str, proposal_id: str, code_hash: str,
) -> bool:
    """Idempotency: has this exact code already been registered for the proposal?"""
    registered_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["effect.registered"],
    )
    return any(
        ev.payload.get("proposal_id") == proposal_id
        and ev.payload.get("codegen_code_hash") == code_hash
        for ev in registered_events
    )


async def run_codegen_for_proposal(
    engine: AsyncEngine,
    settings: Settings,
    *,
    proposal_id: str,
    season_id: str,
    raw_text: str,
    proposer_discord_id: int | None = None,
    bot: discord.Client | None = None,
) -> bool:
    """Run the council for a confirmed proposal and persist the outcome.

    Appends ``proposal.codegen_ready`` (with the serialized EffectSpec) on
    consensus, or ``proposal.codegen_rejected`` with the council's reasons.
    If the proposal has ALREADY passed by the time the council finishes,
    the effect is registered immediately (pending admin approval) — the
    tally-time merge in ``tally_governance_with_effects`` covers the other
    ordering. Returns True when a codegen spec became ready/registered.
    """
    from pinwheel.ai.codegen_council import (
        generate_codegen_effect_mock,
        run_council_review,
    )
    from pinwheel.db.engine import get_session
    from pinwheel.db.repository import Repository

    api_key = settings.anthropic_api_key

    # The council is the slow part (multiple Opus calls) — run it OUTSIDE
    # any DB session.
    if api_key:
        spec, review = await run_council_review(
            proposal_id=proposal_id,
            proposal_text=raw_text,
            api_key=api_key,
        )
        flag_reasons = list(review.flag_reasons)
    elif settings.pinwheel_env != "production":
        # Mock path for dev/demo without an API key
        spec = generate_codegen_effect_mock(raw_text)
        flag_reasons = []
    else:
        spec = None
        flag_reasons = ["No API key configured in production"]

    # Validate hook points against what the simulation actually fires
    if spec is not None:
        bad_hooks = [hp for hp in spec.hook_points if hp not in CODEGEN_SIM_HOOKS]
        if bad_hooks:
            flag_reasons = [
                f"Unknown hook points (never fired by the sim): {bad_hooks}"
            ]
            spec = None

    async with get_session(engine) as session:
        repo = Repository(session)

        if spec is None:
            await repo.append_event(
                event_type="proposal.codegen_rejected",
                aggregate_id=proposal_id,
                aggregate_type="proposal",
                season_id=season_id,
                payload={"reasons": flag_reasons, "raw_text": raw_text},
            )
            await session.commit()
            logger.info(
                "codegen_rejected proposal=%s reasons=%s",
                proposal_id,
                flag_reasons,
            )
            return False

        if await _codegen_already_registered(
            repo, season_id, proposal_id, spec.code_hash,
        ):
            logger.info(
                "codegen_already_registered proposal=%s hash=%s",
                proposal_id,
                spec.code_hash,
            )
            return True

        effect_spec = EffectSpec(
            effect_type="codegen",
            codegen=spec,
            hook_point=spec.hook_points[0] if spec.hook_points else None,
            description=spec.description,
        )

        await repo.append_event(
            event_type="proposal.codegen_ready",
            aggregate_id=proposal_id,
            aggregate_type="proposal",
            season_id=season_id,
            payload={
                "code_hash": spec.code_hash,
                "effect_spec": effect_spec.model_dump(mode="json"),
                "proposer_discord_id": proposer_discord_id,
            },
        )

        # If the vote already resolved (fast pace), register now — pending
        # the admin gate. Otherwise tally merges the ready event at pass time.
        registered_effect = None
        if await _proposal_has_passed(repo, season_id, proposal_id):
            from pinwheel.core.effects import (
                load_effect_registry,
                register_effects_for_proposal,
            )

            registry = await load_effect_registry(repo, season_id)
            round_number = await _current_round(repo, season_id)
            registered = await register_effects_for_proposal(
                repo,
                registry,
                proposal_id,
                [effect_spec],
                season_id,
                current_round=round_number,
                codegen_auto_approve=settings.pinwheel_codegen_auto_approve,
            )
            registered_effect = registered[0] if registered else None

        await session.commit()

    logger.info(
        "codegen_ready proposal=%s hash=%s registered=%s",
        proposal_id,
        spec.code_hash,
        registered_effect is not None,
    )

    # Admin gate DM (outside the DB session)
    if (
        registered_effect is not None
        and registered_effect.codegen_approval_status == "pending"
        and bot is not None
    ):
        await _notify_and_mark(
            engine, settings, bot,
            effect=registered_effect,
            season_id=season_id,
            proposer_discord_id=proposer_discord_id,
        )

    return True


async def _notify_and_mark(
    engine: AsyncEngine,
    settings: Settings,
    bot: discord.Client,
    *,
    effect: object,
    season_id: str,
    proposer_discord_id: int | None = None,
) -> None:
    """DM the admin about a pending effect and persist the notified marker."""
    import contextlib

    from pinwheel.db.engine import get_session
    from pinwheel.db.repository import Repository
    from pinwheel.discord.views import notify_admin_codegen_pending

    with contextlib.suppress(Exception):
        await notify_admin_codegen_pending(
            bot,
            settings,
            effect=effect,
            season_id=season_id,
            proposer_discord_id=proposer_discord_id,
        )
    try:
        async with get_session(engine) as session:
            repo = Repository(session)
            await repo.append_event(
                event_type="effect.codegen_admin_notified",
                aggregate_id=str(getattr(effect, "effect_id", "")),
                aggregate_type="effect",
                season_id=season_id,
                payload={"effect_id": str(getattr(effect, "effect_id", ""))},
            )
            await session.commit()
    except SQLAlchemyError:
        logger.exception("codegen_notify_marker_failed")


async def _pending_codegen_requests(
    repo: Repository,
    season_id: str,
) -> list[object]:
    """codegen_requested events without a terminal outcome, under the retry cap."""
    requested = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.codegen_requested"],
    )
    terminal = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.codegen_ready", "proposal.codegen_rejected"],
    )
    failed = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.codegen_failed"],
    )
    resolved_ids = {ev.aggregate_id for ev in terminal}
    failure_counts: dict[str, int] = {}
    for ev in failed:
        failure_counts[ev.aggregate_id] = failure_counts.get(ev.aggregate_id, 0) + 1

    return [
        ev
        for ev in requested
        if ev.aggregate_id not in resolved_ids
        and failure_counts.get(ev.aggregate_id, 0) < MAX_CODEGEN_RETRIES
    ]


async def _consume_rerun_requests(
    engine: AsyncEngine,
    settings: Settings,
    season_id: str,
) -> int:
    """Consume /rerun-council requests: re-review the STORED code.

    On a rejecting re-review, the effect is disabled persistently and the
    completed event records the verdict. Returns the number consumed.
    """
    from pinwheel.ai.codegen_council import review_existing_code
    from pinwheel.core.effects import load_effect_registry
    from pinwheel.db.engine import get_session
    from pinwheel.db.repository import Repository

    api_key = settings.anthropic_api_key
    consumed = 0

    async with get_session(engine) as session:
        repo = Repository(session)
        requested = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["effect.council_rerun_requested"],
        )
        completed = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["effect.council_rerun_completed"],
        )
        done_ids = {ev.aggregate_id for ev in completed}
        open_requests = [ev for ev in requested if ev.aggregate_id not in done_ids]
        if not open_requests:
            return 0
        registry = await load_effect_registry(repo, season_id)

    for ev in open_requests:
        effect_id = str(ev.payload.get("effect_id", ev.aggregate_id))
        effect = registry.get_effect(effect_id)
        if effect is None or not effect.codegen_code:
            verdict = "effect_missing"
            disable = False
        elif not api_key:
            verdict = "skipped_no_api_key"
            disable = False
        else:
            review = await review_existing_code(
                effect.codegen_code,
                effect.description or "",
                api_key,
                proposal_id=effect.proposal_id,
            )
            verdict = "approved" if review.consensus else "rejected"
            disable = not review.consensus

        try:
            async with get_session(engine) as session:
                repo = Repository(session)
                await repo.append_event(
                    event_type="effect.council_rerun_completed",
                    aggregate_id=ev.aggregate_id,
                    aggregate_type="effect",
                    season_id=season_id,
                    payload={"effect_id": effect_id, "verdict": verdict},
                )
                if disable:
                    await repo.append_event(
                        event_type="effect.codegen_disabled",
                        aggregate_id=effect_id,
                        aggregate_type="effect",
                        season_id=season_id,
                        payload={
                            "effect_id": effect_id,
                            "reason": "council_rerun_rejected",
                        },
                    )
                await session.commit()
            consumed += 1
            logger.info(
                "council_rerun_completed effect=%s verdict=%s",
                effect_id,
                verdict,
            )
        except SQLAlchemyError:
            logger.exception("council_rerun_persist_failed effect=%s", effect_id)

    return consumed


async def _notify_unannounced_pending(
    engine: AsyncEngine,
    settings: Settings,
    bot: discord.Client,
    season_id: str,
) -> None:
    """DM the admin about pending effects that never got their gate DM
    (e.g. registered at tally time, where no bot reference exists)."""
    from pinwheel.core.effects import load_effect_registry
    from pinwheel.db.engine import get_session
    from pinwheel.db.repository import Repository

    async with get_session(engine) as session:
        repo = Repository(session)
        registry = await load_effect_registry(repo, season_id)
        notified_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["effect.codegen_admin_notified"],
        )
        notified_ids = {
            str(ev.payload.get("effect_id", ev.aggregate_id))
            for ev in notified_events
        }
        ready_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.codegen_ready"],
        )
        proposer_by_proposal = {
            ev.aggregate_id: ev.payload.get("proposer_discord_id")
            for ev in ready_events
        }

    for effect in registry.get_all_active():
        if effect.effect_type != "codegen":
            continue
        if effect.codegen_approval_status != "pending":
            continue
        if effect.effect_id in notified_ids:
            continue
        proposer = proposer_by_proposal.get(effect.proposal_id)
        await _notify_and_mark(
            engine, settings, bot,
            effect=effect,
            season_id=season_id,
            proposer_discord_id=int(proposer) if proposer else None,
        )


async def tick_codegen_pipeline(
    engine: AsyncEngine,
    settings: Settings,
    bot: discord.Client | None = None,
) -> None:
    """60s scheduler tick: re-drive crashed council runs, consume rerun
    requests, and DM the admin about unannounced pending effects.

    Never raises — scheduler ticks must not take down the app.
    """
    from pinwheel.db.engine import get_session
    from pinwheel.db.repository import Repository

    try:
        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_active_season()
            if season is None:
                return
            season_id = season.id
            pending = await _pending_codegen_requests(repo, season_id)

        for ev in pending[:MAX_COUNCIL_RUNS_PER_TICK]:
            proposal_id = ev.aggregate_id
            raw_text = str(ev.payload.get("raw_text", ""))
            proposer = ev.payload.get("proposer_discord_id")
            if not raw_text:
                continue
            try:
                await run_codegen_for_proposal(
                    engine,
                    settings,
                    proposal_id=proposal_id,
                    season_id=season_id,
                    raw_text=raw_text,
                    proposer_discord_id=int(proposer) if proposer else None,
                    bot=bot,
                )
            except Exception:  # Last-resort: AI/network errors must not kill the tick
                logger.exception("codegen_run_failed proposal=%s", proposal_id)
                try:
                    async with get_session(engine) as session:
                        repo = Repository(session)
                        await repo.append_event(
                            event_type="proposal.codegen_failed",
                            aggregate_id=proposal_id,
                            aggregate_type="proposal",
                            season_id=season_id,
                            payload={"reason": "exception"},
                        )
                        await session.commit()
                except SQLAlchemyError:
                    logger.exception("codegen_failed_marker_failed")

        await _consume_rerun_requests(engine, settings, season_id)

        if bot is not None:
            await _notify_unannounced_pending(engine, settings, bot, season_id)
    except Exception:  # Last-resort handler — scheduler ticks must never raise
        logger.exception("tick_codegen_pipeline_failed")
