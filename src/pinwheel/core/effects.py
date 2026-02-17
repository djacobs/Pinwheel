"""Effect Registry — manages active effects from passed proposals.

Effects are stored as append-only governance events:
- effect.registered — when a proposal passes and creates effects
- effect.expired — when an effect's lifetime ends
- effect.repealed — when an effect is manually repealed via governance

The registry is rebuilt from the event store at round start.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from pinwheel.core.hooks import EffectLifetime, RegisteredEffect
from pinwheel.models.governance import EffectSpec

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository

logger = logging.getLogger(__name__)


class EffectRegistry:
    """Registry of all active effects in the current season.

    Loaded from event store at round start. Effects fire at hook points
    during simulation and governance. Expired effects are pruned at
    round end.
    """

    def __init__(self) -> None:
        self._effects: dict[str, RegisteredEffect] = {}

    def register(self, effect: RegisteredEffect) -> None:
        """Register a new effect."""
        self._effects[effect.effect_id] = effect
        logger.info(
            "effect_registered id=%s type=%s hooks=%s lifetime=%s",
            effect.effect_id,
            effect.effect_type,
            effect.hook_points,
            effect.lifetime.value,
        )

    def deregister(self, effect_id: str) -> RegisteredEffect | None:
        """Remove an effect from the registry. Returns the removed effect or None."""
        effect = self._effects.pop(effect_id, None)
        if effect:
            logger.info("effect_deregistered id=%s", effect_id)
        return effect

    def get_effects_for_hook(self, hook: str) -> list[RegisteredEffect]:
        """Get all active effects that listen on a specific hook point."""
        return [
            e for e in self._effects.values()
            if hook in e.hook_points
        ]

    def get_all_active(self) -> list[RegisteredEffect]:
        """Get all active effects."""
        return list(self._effects.values())

    def get_narrative_effects(self) -> list[RegisteredEffect]:
        """Get all active narrative effects."""
        return [
            e for e in self._effects.values()
            if e.effect_type == "narrative"
        ]

    def tick_round(self, current_round: int) -> list[str]:
        """Advance round counters for all effects.

        Returns list of effect IDs that have expired.
        """
        expired_ids: list[str] = []
        for effect in list(self._effects.values()):
            if effect.tick_round():
                expired_ids.append(effect.effect_id)
                self._effects.pop(effect.effect_id, None)
                logger.info(
                    "effect_expired id=%s round=%d",
                    effect.effect_id,
                    current_round,
                )
        return expired_ids

    def get_effect(self, effect_id: str) -> RegisteredEffect | None:
        """Get a single effect by ID. Returns None if not found."""
        return self._effects.get(effect_id)

    def get_effects_for_proposal(self, proposal_id: str) -> list[RegisteredEffect]:
        """Get all effects from a specific proposal."""
        return [
            e for e in self._effects.values()
            if e.proposal_id == proposal_id
        ]

    def remove_effect(self, effect_id: str) -> bool:
        """Remove an effect by ID. Returns True if removed, False if not found."""
        effect = self._effects.pop(effect_id, None)
        if effect:
            logger.info("effect_removed id=%s type=%s", effect_id, effect.effect_type)
            return True
        return False

    @property
    def count(self) -> int:
        """Number of active effects."""
        return len(self._effects)

    def build_effects_summary(self) -> str:
        """Build a human-readable summary of active effects for report context."""
        if not self._effects:
            return "No active effects."

        lines: list[str] = []
        for effect in self._effects.values():
            desc = effect.description or effect.narrative_instruction or effect.effect_type
            lifetime_str = effect.lifetime.value
            if effect.rounds_remaining is not None:
                lifetime_str = f"{effect.rounds_remaining} rounds remaining"
            type_label = (
                "PENDING MECHANIC"
                if effect.effect_type == "custom_mechanic"
                else effect.effect_type
            )
            lines.append(f"- [{type_label}] {desc} ({lifetime_str})")

        return "\n".join(lines)


def effect_spec_to_registered(
    spec: EffectSpec,
    proposal_id: str,
    current_round: int,
) -> RegisteredEffect:
    """Convert an EffectSpec from AI interpretation into a RegisteredEffect.

    Maps the structured spec into the runtime effect object that the
    registry manages.
    """
    effect_id = str(uuid.uuid4())

    # Determine lifetime
    if spec.duration == "permanent":
        lifetime = EffectLifetime.PERMANENT
    elif spec.duration == "n_rounds":
        lifetime = EffectLifetime.N_ROUNDS
    elif spec.duration == "one_game":
        lifetime = EffectLifetime.ONE_GAME
    elif spec.duration == "until_repealed":
        lifetime = EffectLifetime.UNTIL_REPEALED
    else:
        lifetime = EffectLifetime.PERMANENT

    # Determine hook points based on effect type
    hook_points: list[str] = []
    if spec.effect_type == "custom_mechanic":
        # Custom mechanics fire at report hooks so their observable behavior
        # appears in commentary even before full implementation.
        hook_points = ["report.simulation.pre", "report.commentary.pre"]
    elif spec.hook_point:
        hook_points = [spec.hook_point]
    elif spec.effect_type == "meta_mutation":
        # Meta mutations fire at round.game.post by default
        hook_points = ["round.game.post"]
    elif spec.effect_type == "narrative":
        # Narratives fire at report hooks
        hook_points = ["report.simulation.pre", "report.commentary.pre"]

    # Build action_code from spec if it's a hook_callback
    action_code = spec.action_code
    if spec.effect_type == "meta_mutation" and not action_code:
        # Wrap meta mutation as action_code for uniform handling
        action_code = None  # meta_mutation effects use their own fields

    # For custom_mechanic, use mechanic_observable_behavior as narrative
    narrative = spec.narrative_instruction or ""
    if spec.effect_type == "custom_mechanic" and spec.mechanic_observable_behavior:
        narrative = spec.mechanic_observable_behavior

    return RegisteredEffect(
        effect_id=effect_id,
        proposal_id=proposal_id,
        _hook_points=hook_points,
        _lifetime=lifetime,
        rounds_remaining=spec.duration_rounds,
        registered_at_round=current_round,
        effect_type=spec.effect_type,
        condition=spec.condition or "",
        action_code=action_code,
        narrative_instruction=narrative,
        description=spec.description,
        target_type=spec.target_type or "",
        target_selector=spec.target_selector or "",
        meta_field=spec.meta_field or "",
        meta_value=spec.meta_value,
        meta_operation=spec.meta_operation,
    )


async def register_effects_for_proposal(
    repo: Repository,
    registry: EffectRegistry,
    proposal_id: str,
    effects: list[EffectSpec],
    season_id: str,
    current_round: int,
) -> list[RegisteredEffect]:
    """Register effects for a passing proposal.

    Creates RegisteredEffect objects, adds them to the registry,
    and persists them via effect.registered events.
    """
    registered: list[RegisteredEffect] = []

    for spec in effects:
        # Skip parameter_change effects — those go through the existing RuleSet path
        if spec.effect_type == "parameter_change":
            continue

        effect = effect_spec_to_registered(spec, proposal_id, current_round)
        registry.register(effect)
        registered.append(effect)

        # Persist to event store
        await repo.append_event(
            event_type="effect.registered",
            aggregate_id=effect.effect_id,
            aggregate_type="effect",
            season_id=season_id,
            payload=effect.to_dict(),
        )

    return registered


async def load_effect_registry(
    repo: Repository,
    season_id: str,
) -> EffectRegistry:
    """Rebuild the effect registry from the event store.

    Replays effect.registered events and removes any effects that
    have been expired or repealed.
    """
    registry = EffectRegistry()

    # Load all effect events
    registered_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["effect.registered"],
    )
    expired_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["effect.expired"],
    )
    repealed_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["effect.repealed"],
    )

    # Build set of dead effect IDs
    dead_ids: set[str] = set()
    for ev in expired_events:
        dead_ids.add(str(ev.payload.get("effect_id", ev.aggregate_id)))
    for ev in repealed_events:
        dead_ids.add(str(ev.payload.get("effect_id", ev.aggregate_id)))

    # Register active effects
    for ev in registered_events:
        effect_id = str(ev.payload.get("effect_id", ev.aggregate_id))
        if effect_id in dead_ids:
            continue
        try:
            effect = RegisteredEffect.from_dict(ev.payload)
            registry.register(effect)
        except Exception:
            logger.exception("failed_to_load_effect id=%s", effect_id)

    logger.info(
        "effect_registry_loaded season=%s active_effects=%d",
        season_id,
        registry.count,
    )
    return registry


async def persist_expired_effects(
    repo: Repository,
    season_id: str,
    expired_ids: list[str],
) -> None:
    """Persist effect expiration events."""
    for effect_id in expired_ids:
        await repo.append_event(
            event_type="effect.expired",
            aggregate_id=effect_id,
            aggregate_type="effect",
            season_id=season_id,
            payload={"effect_id": effect_id, "reason": "lifetime_expired"},
        )


async def activate_custom_mechanic(
    repo: Repository,
    registry: EffectRegistry,
    effect_id: str,
    season_id: str,
    hook_point: str | None = None,
    action_code: dict[str, object] | None = None,
) -> bool:
    """Activate a pending custom_mechanic effect with real hook/action implementation.

    Called by admin via /activate-mechanic. If hook_point and action_code
    are provided, the effect becomes a real hook_callback. If not, the
    approximation (already live) is confirmed as good enough.

    Returns True if the effect was found and activated.
    """
    effect = registry.get_effect(effect_id)
    if effect is None or effect.effect_type != "custom_mechanic":
        return False

    if hook_point and action_code:
        # Upgrade to a real hook_callback
        effect.effect_type = "hook_callback"
        effect._hook_points = [hook_point]
        effect.action_code = action_code

    # Persist activation event
    await repo.append_event(
        event_type="effect.activated",
        aggregate_id=effect_id,
        aggregate_type="effect",
        season_id=season_id,
        payload={
            "effect_id": effect_id,
            "hook_point": hook_point,
            "action_code": action_code,
            "description": effect.description,
        },
    )

    logger.info(
        "custom_mechanic_activated id=%s hook=%s",
        effect_id,
        hook_point or "approximation_confirmed",
    )
    return True


async def repeal_effect(
    repo: Repository,
    registry: EffectRegistry,
    effect_id: str,
    season_id: str,
    proposal_id: str,
) -> bool:
    """Repeal an active effect via governance.

    Writes an effect.repealed event to the event store and removes the
    effect from the in-memory registry. Returns True if the effect was
    found and removed, False if it was not in the registry.
    """
    removed = registry.remove_effect(effect_id)

    # Always write the repeal event — even if the effect was already
    # expired in-memory, the event store needs the record so that
    # load_effect_registry() excludes it on future reloads.
    await repo.append_event(
        event_type="effect.repealed",
        aggregate_id=effect_id,
        aggregate_type="effect",
        season_id=season_id,
        payload={
            "effect_id": effect_id,
            "reason": "governance_repeal",
            "proposal_id": proposal_id,
        },
    )

    logger.info(
        "effect_repealed id=%s proposal=%s removed_from_registry=%s",
        effect_id,
        proposal_id,
        removed,
    )
    return removed
