"""Governance lifecycle — proposals, votes, window resolution, rule enactment.

All governance state is derived from the append-only event store.
This module contains pure business logic; database access goes through Repository.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING

from pinwheel.models.governance import (
    Amendment,
    GovernanceWindow,
    Proposal,
    RuleInterpretation,
    Vote,
    VoteTally,
)
from pinwheel.models.rules import RuleChange, RuleSet

if TYPE_CHECKING:
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
        "quarter_minutes", "shot_clock_seconds", "three_point_value",
        "two_point_value", "free_throw_value", "personal_foul_limit",
        "team_foul_bonus_threshold", "three_point_distance",
        "elam_trigger_quarter", "elam_margin", "halftime_stamina_recovery",
        "safety_cap_possessions",
    }
    tier2 = {
        "max_shot_share", "min_pass_per_possession", "home_court_enabled",
        "home_crowd_boost", "away_fatigue_factor", "crowd_pressure",
        "altitude_stamina_penalty", "travel_fatigue_enabled", "travel_fatigue_per_mile",
    }
    tier3 = {
        "teams_count", "round_robins_per_season", "playoff_teams",
        "playoff_semis_best_of", "playoff_finals_best_of",
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
) -> Proposal:
    """Submit a proposal. Deducts 1 PROPOSE token via event store."""
    sanitized = sanitize_text(raw_text)
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

    # Append proposal event
    await repo.append_event(
        event_type="proposal.submitted",
        aggregate_id=proposal_id,
        aggregate_type="proposal",
        season_id=season_id,
        governor_id=governor_id,
        team_id=team_id,
        payload=proposal.model_dump(mode="json"),
    )

    # Spend PROPOSE token
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


async def confirm_proposal(repo: Repository, proposal: Proposal) -> Proposal:
    """Governor confirms AI interpretation. Moves to voting."""
    await repo.append_event(
        event_type="proposal.confirmed",
        aggregate_id=proposal.id,
        aggregate_type="proposal",
        season_id=proposal.season_id,
        governor_id=proposal.governor_id,
        payload={"proposal_id": proposal.id},
    )
    proposal.status = "confirmed"
    return proposal


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
    weighted_yes = sum(v.weight for v in votes if v.vote == "yes")
    weighted_no = sum(v.weight for v in votes if v.vote == "no")
    total = weighted_yes + weighted_no

    passed = total > 0 and (weighted_yes / total) > threshold

    return VoteTally(
        proposal_id=votes[0].proposal_id if votes else "",
        weighted_yes=weighted_yes,
        weighted_no=weighted_no,
        total_weight=total,
        passed=passed,
        threshold=threshold,
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


# --- Window Resolution ---


async def tally_governance(
    repo: Repository,
    season_id: str,
    proposals: list[Proposal],
    votes_by_proposal: dict[str, list[Vote]],
    current_ruleset: RuleSet,
    round_number: int,
) -> tuple[RuleSet, list[VoteTally]]:
    """Tally all pending proposals and enact passing rule changes.

    Unlike close_governance_window(), this takes a season_id directly
    and does not emit a window.closed event — there is no window concept
    in interval-based governance.

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


async def close_governance_window(
    repo: Repository,
    window: GovernanceWindow,
    proposals: list[Proposal],
    votes_by_proposal: dict[str, list[Vote]],
    current_ruleset: RuleSet,
    round_number: int,
) -> tuple[RuleSet, list[VoteTally]]:
    """Resolve all proposals in a governance window.

    Delegates to tally_governance() for the actual tallying, then
    emits a window.closed event.

    Returns the updated ruleset and list of vote tallies.
    """
    ruleset, tallies = await tally_governance(
        repo=repo,
        season_id=window.season_id,
        proposals=proposals,
        votes_by_proposal=votes_by_proposal,
        current_ruleset=current_ruleset,
        round_number=round_number,
    )

    # Close window
    await repo.append_event(
        event_type="window.closed",
        aggregate_id=window.id,
        aggregate_type="governance_window",
        season_id=window.season_id,
        payload={"proposals_resolved": len(tallies)},
    )

    return ruleset, tallies
