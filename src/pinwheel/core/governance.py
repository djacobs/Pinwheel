"""Governance lifecycle — proposals, votes, tallying, rule enactment.

All governance state is derived from the append-only event store.
This module contains pure business logic; database access goes through Repository.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING

from pinwheel.models.governance import (
    Amendment,
    EffectSpec,
    Proposal,
    ProposalInterpretation,
    RuleInterpretation,
    Vote,
    VoteTally,
)
from pinwheel.models.rules import RuleChange, RuleSet

# Token cost for repeal proposals (same as Tier 5 — game effect).
REPEAL_TOKEN_COST = 2
# Repeal proposals are Tier 5 (game effect, not a parameter change).
REPEAL_TIER = 5

if TYPE_CHECKING:
    from pinwheel.core.effects import EffectRegistry
    from pinwheel.db.repository import Repository


# --- Input Sanitization ---


def sanitize_text(raw: str, max_length: int = 500) -> str:
    """Strip dangerous content from governor-submitted text.

    Removes: invisible chars, HTML/markdown markup, prompt injection markers.
    Enforces max length.
    """
    # Strip invisible Unicode
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202f\ufeff]", "", raw)
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Strip common prompt injection markers
    for marker in ["<system>", "</system>", "<human>", "</human>", "<assistant>", "</assistant>"]:
        text = text.replace(marker, "")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Enforce length
    return text[:max_length]


# --- Vote Weight ---


def compute_vote_weight(active_governors_on_team: int) -> float:
    """Each team's total weight = 1.0, divided equally among active governors."""
    if active_governors_on_team <= 0:
        return 0.0
    return 1.0 / active_governors_on_team


# --- Tier Detection ---


def detect_tier(interpretation: RuleInterpretation, ruleset: RuleSet) -> int:
    """Determine the governance tier of an interpreted proposal.

    Tiers 1-4 are parameter changes. Higher tiers need higher vote thresholds.
    """
    if interpretation.parameter is None:
        return 5  # Game Effect or uninterpretable
    param = interpretation.parameter
    tier1 = {
        "quarter_minutes",
        "shot_clock_seconds",
        "three_point_value",
        "two_point_value",
        "free_throw_value",
        "personal_foul_limit",
        "team_foul_bonus_threshold",
        "three_point_distance",
        "elam_trigger_quarter",
        "elam_margin",
        "halftime_stamina_recovery",
        "safety_cap_possessions",
        "turnover_rate_modifier",
        "foul_rate_modifier",
        "offensive_rebound_weight",
        "stamina_drain_rate",
        "dead_ball_time_seconds",
    }
    tier2 = {
        "max_shot_share",
        "min_pass_per_possession",
        "home_court_enabled",
        "home_crowd_boost",
        "away_fatigue_factor",
        "crowd_pressure",
        "altitude_stamina_penalty",
        "travel_fatigue_enabled",
        "travel_fatigue_per_mile",
    }
    tier3 = {
        "teams_count",
        "round_robins_per_season",
        "playoff_teams",
        "playoff_semis_best_of",
        "playoff_finals_best_of",
    }
    tier4 = {"proposals_per_window", "vote_threshold"}
    if param in tier1:
        return 1
    if param in tier2:
        return 2
    if param in tier3:
        return 3
    if param in tier4:
        return 4
    return 5


def detect_tier_v2(interpretation: ProposalInterpretation, ruleset: RuleSet) -> int:
    """Determine governance tier from a V2 ProposalInterpretation.

    Examines the effects list directly instead of relying on the legacy
    ``to_rule_interpretation()`` conversion (which loses non-parameter effects).

    Tier rules:
    - ``parameter_change`` → reuse per-parameter tier logic (1-4)
    - ``hook_callback`` / ``meta_mutation`` / ``move_grant`` → Tier 3
    - Only ``narrative`` effects → Tier 2
    - No effects / ``injection_flagged`` / ``rejection_reason`` → Tier 5
    - Compound proposals: highest tier wins
    """
    if interpretation.injection_flagged or interpretation.rejection_reason:
        return 5

    effects = interpretation.effects
    if not effects:
        return 5

    tiers: list[int] = []
    for effect in effects:
        if effect.effect_type == "parameter_change" and effect.parameter:
            # Reuse the legacy per-parameter tier lookup
            legacy = RuleInterpretation(parameter=effect.parameter)
            tiers.append(detect_tier(legacy, ruleset))
        elif effect.effect_type in (
            "hook_callback", "meta_mutation", "move_grant", "custom_mechanic",
        ):
            tiers.append(3)
        elif effect.effect_type == "narrative":
            tiers.append(2)
        # composite effects: recurse into sub-effects if needed, but for now
        # treat as Tier 3 (structural change)
        elif effect.effect_type == "composite":
            tiers.append(3)

    if not tiers:
        return 5

    return max(tiers)


def token_cost_for_tier(tier: int) -> int:
    """Higher tiers cost more PROPOSE tokens."""
    if tier <= 4:
        return 1
    if tier <= 6:
        return 2
    return 3


def vote_threshold_for_tier(tier: int, base_threshold: float = 0.5) -> float:
    """Higher tiers need supermajority."""
    if tier <= 2:
        return base_threshold
    if tier <= 4:
        return max(base_threshold, 0.6)
    if tier <= 6:
        return 0.67
    return 0.75


# --- Proposal Lifecycle ---


async def submit_proposal(
    repo: Repository,
    governor_id: str,
    team_id: str,
    season_id: str,
    window_id: str,
    raw_text: str,
    interpretation: RuleInterpretation,
    ruleset: RuleSet,
    *,
    token_already_spent: bool = False,
    interpretation_v2: ProposalInterpretation | None = None,
) -> Proposal:
    """Submit a proposal. Deducts PROPOSE token(s) via event store.

    If ``token_already_spent`` is True, the token was deducted at propose-time
    (before the confirm UI) to prevent race conditions, so the token.spent
    event is skipped here.

    When ``interpretation_v2`` is provided, tier detection uses the V2 effects
    list instead of the legacy parameter-based tier lookup.
    """
    sanitized = sanitize_text(raw_text)
    if interpretation_v2 is not None:
        tier = detect_tier_v2(interpretation_v2, ruleset)
    else:
        tier = detect_tier(interpretation, ruleset)
    cost = token_cost_for_tier(tier)
    proposal_id = str(uuid.uuid4())

    proposal = Proposal(
        id=proposal_id,
        season_id=season_id,
        governor_id=governor_id,
        team_id=team_id,
        window_id=window_id,
        raw_text=raw_text,
        sanitized_text=sanitized,
        interpretation=interpretation,
        tier=tier,
        token_cost=cost,
        status="submitted",
    )

    # Append proposal event — include v2 effects in payload so the game
    # loop can extract them during tally without re-interpreting.
    payload = proposal.model_dump(mode="json")
    if interpretation_v2 is not None:
        payload["effects_v2"] = [e.model_dump(mode="json") for e in interpretation_v2.effects]
        payload["interpretation_v2_confidence"] = interpretation_v2.confidence
        payload["interpretation_v2_impact"] = interpretation_v2.impact_analysis

    await repo.append_event(
        event_type="proposal.submitted",
        aggregate_id=proposal_id,
        aggregate_type="proposal",
        season_id=season_id,
        governor_id=governor_id,
        team_id=team_id,
        payload=payload,
    )

    # Spend PROPOSE token (unless already spent at propose-time)
    if not token_already_spent:
        await repo.append_event(
            event_type="token.spent",
            aggregate_id=governor_id,
            aggregate_type="token",
            season_id=season_id,
            governor_id=governor_id,
            team_id=team_id,
            payload={"token_type": "propose", "amount": cost, "reason": f"proposal:{proposal_id}"},
        )

    return proposal


def _needs_admin_review(
    proposal: Proposal,
    interpretation_v2: ProposalInterpretation | None = None,
) -> bool:
    """Check if a proposal is "wild" and should be flagged for admin review.

    Tier 5+ proposals (uninterpretable, parameter=None, or unknown params)
    and proposals with low AI confidence (< 0.5) are flagged for admin veto.
    Wild proposals still go to vote immediately — the admin can veto before tally.

    When ``interpretation_v2`` is provided, the V2 interpretation is checked:
    - If V2 has real effects and is not injection-flagged → NOT wild
    - If V2 has no effects or is injection-flagged → wild
    - Low confidence (< 0.5) is still flagged regardless of V2
    """
    # custom_mechanic effects always need admin review — they require code
    if interpretation_v2 is not None:
        has_custom = any(
            e.effect_type == "custom_mechanic" for e in interpretation_v2.effects
        )
        if has_custom:
            return True

    # Low confidence always triggers review, regardless of V2
    if interpretation_v2 is not None and interpretation_v2.confidence < 0.5:
        return True
    if (
        interpretation_v2 is None
        and proposal.interpretation
        and proposal.interpretation.confidence < 0.5
    ):
        return True

    # V2 path: real effects + not injection-flagged = not wild
    if interpretation_v2 is not None:
        if interpretation_v2.injection_flagged:
            return True
        return not interpretation_v2.effects

    # Legacy path: tier 5+ = wild
    return proposal.tier >= 5


async def confirm_proposal(
    repo: Repository,
    proposal: Proposal,
    interpretation_v2: ProposalInterpretation | None = None,
) -> Proposal:
    """Governor confirms AI interpretation. Always moves to confirmed (voting open).

    All proposals go to vote immediately. Wild proposals (Tier 5+ or
    confidence < 0.5) are also flagged for admin review — the admin can
    veto before tally, but the democratic process proceeds by default.

    When ``interpretation_v2`` is provided, V2-aware admin review logic is used.
    """
    # Always confirm — opens voting
    await repo.append_event(
        event_type="proposal.confirmed",
        aggregate_id=proposal.id,
        aggregate_type="proposal",
        season_id=proposal.season_id,
        governor_id=proposal.governor_id,
        payload={"proposal_id": proposal.id},
    )
    proposal.status = "confirmed"

    # Wild proposals also get flagged for admin review (audit trail)
    if _needs_admin_review(proposal, interpretation_v2=interpretation_v2):
        flag_payload = proposal.model_dump(mode="json")
        if interpretation_v2 is not None:
            flag_payload["effects_v2"] = [
                e.model_dump(mode="json") for e in interpretation_v2.effects
            ]
        await repo.append_event(
            event_type="proposal.flagged_for_review",
            aggregate_id=proposal.id,
            aggregate_type="proposal",
            season_id=proposal.season_id,
            governor_id=proposal.governor_id,
            payload=flag_payload,
        )

    return proposal


async def admin_clear_proposal(repo: Repository, proposal: Proposal) -> Proposal:
    """Admin clears a flagged proposal. No-op since proposal is already confirmed.

    Emits a review_cleared event for audit trail and notifies the proposer.
    """
    await repo.append_event(
        event_type="proposal.review_cleared",
        aggregate_id=proposal.id,
        aggregate_type="proposal",
        season_id=proposal.season_id,
        governor_id=proposal.governor_id,
        payload={"proposal_id": proposal.id},
    )
    return proposal


# Backward-compatible alias
admin_approve_proposal = admin_clear_proposal


async def admin_veto_proposal(
    repo: Repository,
    proposal: Proposal,
    reason: str = "",
) -> Proposal:
    """Admin vetoes a wild proposal. Refunds the PROPOSE token.

    If the proposal has already passed or been enacted, veto is a no-op
    (too late — the democratic process completed).
    """
    if proposal.status in ("passed", "enacted"):
        return proposal

    proposal.status = "vetoed"
    await repo.append_event(
        event_type="proposal.vetoed",
        aggregate_id=proposal.id,
        aggregate_type="proposal",
        season_id=proposal.season_id,
        governor_id=proposal.governor_id,
        payload={**proposal.model_dump(mode="json"), "veto_reason": reason},
    )
    # Refund PROPOSE token
    await repo.append_event(
        event_type="token.regenerated",
        aggregate_id=proposal.governor_id,
        aggregate_type="token",
        season_id=proposal.season_id,
        governor_id=proposal.governor_id,
        payload={
            "token_type": "propose",
            "amount": proposal.token_cost,
            "reason": "admin_veto_refund",
        },
    )
    return proposal


# Backward-compatible alias
admin_reject_proposal = admin_veto_proposal


async def cancel_proposal(repo: Repository, proposal: Proposal) -> Proposal:
    """Cancel a proposal. Refunds PROPOSE token if pre-vote."""
    await repo.append_event(
        event_type="proposal.cancelled",
        aggregate_id=proposal.id,
        aggregate_type="proposal",
        season_id=proposal.season_id,
        governor_id=proposal.governor_id,
        payload={"proposal_id": proposal.id},
    )

    if proposal.status in ("draft", "submitted"):
        # Refund token
        await repo.append_event(
            event_type="token.regenerated",
            aggregate_id=proposal.governor_id,
            aggregate_type="token",
            season_id=proposal.season_id,
            governor_id=proposal.governor_id,
            payload={"token_type": "propose", "amount": proposal.token_cost, "reason": "refund"},
        )

    proposal.status = "cancelled"
    return proposal


async def submit_repeal_proposal(
    repo: Repository,
    governor_id: str,
    team_id: str,
    season_id: str,
    target_effect_id: str,
    effect_description: str,
    *,
    token_already_spent: bool = False,
) -> Proposal:
    """Submit a repeal proposal targeting an active effect.

    Creates a Tier 5 proposal with a special raw_text and stores the
    target effect ID in the proposal event payload. The proposal goes
    through the normal voting process; if it passes, the effect is
    removed during tally.
    """
    raw_text = f"Repeal: {effect_description}"
    sanitized = sanitize_text(raw_text)
    proposal_id = str(uuid.uuid4())

    interpretation = RuleInterpretation(
        parameter=None,
        impact_analysis=f"Repeal of active effect: {effect_description}",
        confidence=1.0,
    )

    proposal = Proposal(
        id=proposal_id,
        season_id=season_id,
        governor_id=governor_id,
        team_id=team_id,
        window_id="",
        raw_text=raw_text,
        sanitized_text=sanitized,
        interpretation=interpretation,
        tier=REPEAL_TIER,
        token_cost=REPEAL_TOKEN_COST,
        status="submitted",
    )

    # Append proposal event with repeal metadata
    payload = proposal.model_dump(mode="json")
    payload["repeal_target_effect_id"] = target_effect_id
    payload["proposal_type"] = "repeal"

    await repo.append_event(
        event_type="proposal.submitted",
        aggregate_id=proposal_id,
        aggregate_type="proposal",
        season_id=season_id,
        governor_id=governor_id,
        team_id=team_id,
        payload=payload,
    )

    # Spend PROPOSE token (unless already spent at propose-time)
    if not token_already_spent:
        await repo.append_event(
            event_type="token.spent",
            aggregate_id=governor_id,
            aggregate_type="token",
            season_id=season_id,
            governor_id=governor_id,
            team_id=team_id,
            payload={
                "token_type": "propose",
                "amount": REPEAL_TOKEN_COST,
                "reason": f"repeal_proposal:{proposal_id}",
            },
        )

    return proposal


async def count_amendments(repo: Repository, proposal_id: str, season_id: str) -> int:
    """Count how many times a proposal has been amended.

    Derived from the event store — counts ``proposal.amended`` events
    for the given proposal.
    """
    events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.amended"],
    )
    return sum(1 for e in events if e.aggregate_id == proposal_id)


# Maximum number of amendments allowed per proposal.
MAX_AMENDMENTS_PER_PROPOSAL = 2


async def amend_proposal(
    repo: Repository,
    proposal: Proposal,
    governor_id: str,
    team_id: str,
    amendment_text: str,
    new_interpretation: RuleInterpretation,
) -> Amendment:
    """Submit an amendment. Costs 1 AMEND token. Replaces interpretation."""
    amendment_id = str(uuid.uuid4())
    amendment = Amendment(
        id=amendment_id,
        proposal_id=proposal.id,
        governor_id=governor_id,
        amendment_text=amendment_text,
        new_interpretation=new_interpretation,
    )

    await repo.append_event(
        event_type="proposal.amended",
        aggregate_id=proposal.id,
        aggregate_type="proposal",
        season_id=proposal.season_id,
        governor_id=governor_id,
        team_id=team_id,
        payload=amendment.model_dump(mode="json"),
    )

    # Spend AMEND token
    await repo.append_event(
        event_type="token.spent",
        aggregate_id=governor_id,
        aggregate_type="token",
        season_id=proposal.season_id,
        governor_id=governor_id,
        team_id=team_id,
        payload={"token_type": "amend", "amount": 1, "reason": f"amendment:{amendment_id}"},
    )

    # Update proposal interpretation
    proposal.interpretation = new_interpretation
    proposal.status = "amended"

    return amendment


# --- Voting ---


async def cast_vote(
    repo: Repository,
    proposal: Proposal,
    governor_id: str,
    team_id: str,
    vote_choice: str,
    weight: float,
    boost_used: bool = False,
) -> Vote:
    """Cast a vote on a proposal."""
    vote_id = str(uuid.uuid4())
    effective_weight = weight * 2.0 if boost_used else weight

    vote = Vote(
        id=vote_id,
        proposal_id=proposal.id,
        governor_id=governor_id,
        team_id=team_id,
        vote=vote_choice,  # type: ignore[arg-type]
        weight=effective_weight,
        boost_used=boost_used,
    )

    await repo.append_event(
        event_type="vote.cast",
        aggregate_id=proposal.id,
        aggregate_type="proposal",
        season_id=proposal.season_id,
        governor_id=governor_id,
        team_id=team_id,
        payload=vote.model_dump(mode="json"),
    )

    if boost_used:
        await repo.append_event(
            event_type="token.spent",
            aggregate_id=governor_id,
            aggregate_type="token",
            season_id=proposal.season_id,
            governor_id=governor_id,
            team_id=team_id,
            payload={"token_type": "boost", "amount": 1, "reason": f"boost:{vote_id}"},
        )

    return vote


def tally_votes(votes: list[Vote], threshold: float) -> VoteTally:
    """Tally weighted votes and determine if proposal passes.

    Strictly greater-than: ties fail.
    """
    yes_votes = [v for v in votes if v.vote == "yes"]
    no_votes = [v for v in votes if v.vote == "no"]
    weighted_yes = sum(v.weight for v in yes_votes)
    weighted_no = sum(v.weight for v in no_votes)
    total = weighted_yes + weighted_no

    passed = total > 0 and (weighted_yes / total) > threshold

    return VoteTally(
        proposal_id=votes[0].proposal_id if votes else "",
        weighted_yes=weighted_yes,
        weighted_no=weighted_no,
        total_weight=total,
        passed=passed,
        threshold=threshold,
        yes_count=len(yes_votes),
        no_count=len(no_votes),
    )


# --- Rule Application ---


def apply_rule_change(
    ruleset: RuleSet,
    interpretation: RuleInterpretation,
    proposal_id: str,
    round_enacted: int,
) -> tuple[RuleSet, RuleChange]:
    """Apply a passed proposal's interpretation to the ruleset.

    Returns the new ruleset and the change record. Raises ValueError if invalid.
    """
    if interpretation.parameter is None:
        raise ValueError("Cannot apply rule change: no parameter specified")

    param = interpretation.parameter
    if not hasattr(ruleset, param):
        raise ValueError(f"Unknown rule parameter: {param}")

    old_value = getattr(ruleset, param)
    new_value = interpretation.new_value

    # Build new ruleset with the change — Pydantic validates ranges
    new_data = ruleset.model_dump()
    new_data[param] = new_value
    new_ruleset = RuleSet(**new_data)

    change = RuleChange(
        parameter=param,
        old_value=old_value,
        new_value=new_value,  # type: ignore[arg-type]
        source_proposal_id=proposal_id,
        round_enacted=round_enacted,
    )

    return new_ruleset, change


# --- Governance Tallying ---


async def tally_governance(
    repo: Repository,
    season_id: str,
    proposals: list[Proposal],
    votes_by_proposal: dict[str, list[Vote]],
    current_ruleset: RuleSet,
    round_number: int,
) -> tuple[RuleSet, list[VoteTally]]:
    """Tally all pending proposals and enact passing rule changes.

    Returns the updated ruleset and list of vote tallies.
    """
    tallies: list[VoteTally] = []
    ruleset = current_ruleset

    for proposal in proposals:
        if proposal.status not in ("confirmed", "amended", "submitted"):
            continue

        votes = votes_by_proposal.get(proposal.id, [])
        threshold = vote_threshold_for_tier(proposal.tier, current_ruleset.vote_threshold)
        tally = tally_votes(votes, threshold)
        tally.proposal_id = proposal.id
        tallies.append(tally)

        if tally.passed and proposal.interpretation and proposal.interpretation.parameter:
            # Enact rule
            try:
                ruleset, change = apply_rule_change(
                    ruleset, proposal.interpretation, proposal.id, round_number
                )
                await repo.append_event(
                    event_type="rule.enacted",
                    aggregate_id=proposal.id,
                    aggregate_type="rule_change",
                    season_id=season_id,
                    payload=change.model_dump(mode="json"),
                )
            except (ValueError, Exception):
                await repo.append_event(
                    event_type="rule.rolled_back",
                    aggregate_id=proposal.id,
                    aggregate_type="rule_change",
                    season_id=season_id,
                    payload={"reason": "validation_error", "proposal_id": proposal.id},
                )

        # Record pass/fail
        event_type = "proposal.passed" if tally.passed else "proposal.failed"
        await repo.append_event(
            event_type=event_type,
            aggregate_id=proposal.id,
            aggregate_type="proposal",
            season_id=season_id,
            payload=tally.model_dump(mode="json"),
        )

    return ruleset, tallies


async def tally_governance_with_effects(
    repo: Repository,
    season_id: str,
    proposals: list[Proposal],
    votes_by_proposal: dict[str, list[Vote]],
    current_ruleset: RuleSet,
    round_number: int,
    effect_registry: EffectRegistry | None = None,
    effects_v2_by_proposal: dict[str, list[EffectSpec]] | None = None,
) -> tuple[RuleSet, list[VoteTally]]:
    """Tally proposals and register effects for passing proposals.

    Extension of tally_governance that also handles ProposalInterpretation
    effects beyond parameter changes. Backward compatible: proposals with
    only RuleInterpretation still work through the existing path.

    Supports compound proposals: when effects_v2_by_proposal contains
    multiple parameter_change effects for a proposal, all are applied
    to the RuleSet.

    Returns the updated ruleset and list of vote tallies.
    """
    from pinwheel.core.effects import register_effects_for_proposal, repeal_effect

    tallies: list[VoteTally] = []
    ruleset = current_ruleset
    _effects_v2_map = effects_v2_by_proposal or {}

    # Build a lookup of repeal target IDs from proposal submitted events.
    # The repeal_target_effect_id is stored in the proposal event payload
    # (not in the Proposal model), so we need to check the original payload.
    repeal_targets: dict[str, str] = {}
    if effect_registry is not None:
        submitted_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.submitted"],
        )
        for se in submitted_events:
            pid = se.payload.get("id", se.aggregate_id)
            target_eid = se.payload.get("repeal_target_effect_id")
            if target_eid:
                repeal_targets[str(pid)] = str(target_eid)

    for proposal in proposals:
        if proposal.status not in ("confirmed", "amended", "submitted"):
            continue

        votes = votes_by_proposal.get(proposal.id, [])
        threshold = vote_threshold_for_tier(proposal.tier, current_ruleset.vote_threshold)
        tally = tally_votes(votes, threshold)
        tally.proposal_id = proposal.id
        tallies.append(tally)

        if tally.passed:
            # Check for v2 effects first (compound proposals)
            v2_effects = _effects_v2_map.get(proposal.id, [])
            v2_param_effects = [
                e for e in v2_effects if e.effect_type == "parameter_change" and e.parameter
            ]

            if v2_param_effects:
                # Apply all parameter_change effects from v2 interpretation
                for effect in v2_param_effects:
                    interp = RuleInterpretation(
                        parameter=effect.parameter,
                        new_value=effect.new_value,
                        old_value=effect.old_value,
                    )
                    try:
                        ruleset, change = apply_rule_change(
                            ruleset, interp, proposal.id, round_number
                        )
                        await repo.append_event(
                            event_type="rule.enacted",
                            aggregate_id=proposal.id,
                            aggregate_type="rule_change",
                            season_id=season_id,
                            payload=change.model_dump(mode="json"),
                        )
                    except (ValueError, Exception):
                        await repo.append_event(
                            event_type="rule.rolled_back",
                            aggregate_id=proposal.id,
                            aggregate_type="rule_change",
                            season_id=season_id,
                            payload={
                                "reason": "validation_error",
                                "proposal_id": proposal.id,
                                "parameter": effect.parameter,
                            },
                        )
            elif proposal.interpretation and proposal.interpretation.parameter:
                # 1. Fallback: handle single parameter change via existing path
                try:
                    ruleset, change = apply_rule_change(
                        ruleset, proposal.interpretation, proposal.id, round_number
                    )
                    await repo.append_event(
                        event_type="rule.enacted",
                        aggregate_id=proposal.id,
                        aggregate_type="rule_change",
                        season_id=season_id,
                        payload=change.model_dump(mode="json"),
                    )
                except (ValueError, Exception):
                    await repo.append_event(
                        event_type="rule.rolled_back",
                        aggregate_id=proposal.id,
                        aggregate_type="rule_change",
                        season_id=season_id,
                        payload={"reason": "validation_error", "proposal_id": proposal.id},
                    )

            # 2. Handle non-parameter v2 effects (meta, hook, narrative)
            if effect_registry is not None:
                non_param_effects = [
                    e
                    for e in v2_effects
                    if e.effect_type not in ("parameter_change", "move_grant")
                ]
                # Also check the legacy extraction path
                if not non_param_effects:
                    non_param_effects = _extract_effects_from_proposal(proposal)
                if non_param_effects:
                    await register_effects_for_proposal(
                        repo=repo,
                        registry=effect_registry,
                        proposal_id=proposal.id,
                        effects=non_param_effects,
                        season_id=season_id,
                        current_round=round_number,
                    )

            # 2b. Handle move_grant effects
            move_grant_effects = [
                e for e in v2_effects if e.effect_type == "move_grant"
            ]
            for mg in move_grant_effects:
                await _enact_move_grant(repo, season_id, mg)

            # 2c. Notify admin when custom_mechanic effects are enacted
            custom_effects = [
                e for e in v2_effects if e.effect_type == "custom_mechanic"
            ]
            for ce in custom_effects:
                await repo.append_event(
                    event_type="effect.implementation_requested",
                    aggregate_id=proposal.id,
                    aggregate_type="effect",
                    season_id=season_id,
                    governor_id=proposal.governor_id,
                    payload={
                        "proposal_id": proposal.id,
                        "mechanic_description": ce.mechanic_description or "",
                        "mechanic_hook_point": ce.mechanic_hook_point or "",
                        "mechanic_observable_behavior": ce.mechanic_observable_behavior or "",
                        "mechanic_implementation_spec": ce.mechanic_implementation_spec or "",
                        "description": ce.description,
                    },
                )

            # 3. Handle repeal proposals — remove the target effect
            repeal_target_id = repeal_targets.get(proposal.id)
            if repeal_target_id and effect_registry is not None:
                target_effect = effect_registry.get_effect(repeal_target_id)
                if target_effect and target_effect.effect_type == "parameter_change":
                    # Parameter changes cannot be repealed via this mechanism.
                    # Governors should submit a new /propose to change the parameter.
                    pass
                else:
                    await repeal_effect(
                        repo=repo,
                        registry=effect_registry,
                        effect_id=repeal_target_id,
                        season_id=season_id,
                        proposal_id=proposal.id,
                    )

        # Record pass/fail
        event_type = "proposal.passed" if tally.passed else "proposal.failed"
        await repo.append_event(
            event_type=event_type,
            aggregate_id=proposal.id,
            aggregate_type="proposal",
            season_id=season_id,
            payload=tally.model_dump(mode="json"),
        )

    return ruleset, tallies


def _extract_effects_from_proposal(proposal: Proposal) -> list[EffectSpec]:
    """Extract EffectSpec list from a proposal's payload.

    Looks for effects_v2 in the proposal's interpretation or raw payload.
    Returns empty list if no v2 effects are found.
    """
    # Check if proposal has a ProposalInterpretation stored in its payload
    # The proposal model stores interpretation as RuleInterpretation,
    # but the event store payload may contain effects_v2 data
    if not proposal.interpretation:
        return []

    # Base case — effects come through the explicit v2 path
    # (stored in event payload as effects_v2, not in RuleInterpretation)
    return []


def get_proposal_effects_v2(
    proposal_payload: dict[str, object],
) -> list[EffectSpec]:
    """Extract v2 effects from a proposal's event store payload.

    The v2 interpreter stores effects in proposal_payload["effects_v2"].
    """
    effects_data = proposal_payload.get("effects_v2")
    if not effects_data or not isinstance(effects_data, list):
        return []

    effects: list[EffectSpec] = []
    for item in effects_data:
        if isinstance(item, dict):
            try:
                effects.append(EffectSpec(**item))
            except Exception:
                continue
    return effects


async def _enact_move_grant(
    repo: Repository,
    season_id: str,
    effect: EffectSpec,
) -> list[str]:
    """Enact a move_grant effect — grant a governed move to targeted hoopers.

    Targets are resolved from the effect's target_hooper_id, target_team_id,
    or target_selector ("all"). Returns list of hooper IDs that received the move.

    Deduplication: if a hooper already has a move with the same name, skip.
    """
    import logging

    from pinwheel.models.team import Move

    logger = logging.getLogger(__name__)

    if not effect.move_name:
        return []

    move = Move(
        name=effect.move_name,
        trigger=effect.move_trigger or "any_possession",
        effect=effect.move_effect or "",
        attribute_gate=effect.move_attribute_gate or {},
        source="governed",
    )
    move_dict = move.model_dump()

    target_hooper_ids: list[str] = []

    if effect.target_hooper_id:
        target_hooper_ids = [effect.target_hooper_id]
    elif effect.target_team_id:
        hoopers = await repo.get_hoopers_for_team(effect.target_team_id)
        target_hooper_ids = [h.id for h in hoopers]
    elif effect.target_selector == "all":
        teams = await repo.get_teams_for_season(season_id)
        for team in teams:
            hoopers = await repo.get_hoopers_for_team(team.id)
            target_hooper_ids.extend(h.id for h in hoopers)

    granted: list[str] = []
    for hooper_id in target_hooper_ids:
        hooper = await repo.get_hooper(hooper_id)
        if hooper is None:
            continue
        existing_names = {
            m.get("name") if isinstance(m, dict) else getattr(m, "name", "")
            for m in (hooper.moves or [])
        }
        if effect.move_name in existing_names:
            continue
        await repo.add_hooper_move(hooper_id, move_dict)
        granted.append(hooper_id)

    if granted:
        logger.info(
            "move_grant_enacted move=%s hoopers=%d season=%s",
            effect.move_name,
            len(granted),
            season_id,
        )

    return granted
