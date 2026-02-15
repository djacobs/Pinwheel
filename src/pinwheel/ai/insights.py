"""AI intelligence layer — insight reports beyond per-round reflections.

Four insight types:
- Impact validation: Did governance predictions match gameplay reality?
- Leverage detection: Hidden influence patterns for individual governors (private)
- Behavioral profile: Longitudinal governance arc for individual governors (private)
- Newspaper headlines: Punchy headline + subhead for the Pinwheel Post page

All follow the report.py pattern: prompt template → API call (or mock) → Report model.
The AI observes; humans decide.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

from pinwheel.models.report import Report

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

IMPACT_VALIDATION_PROMPT = """\
You are validating a governance prediction in Pinwheel Fates.

A governor proposed a rule change. The AI predicted consequences. Now we have data.

## Rules
1. Grade the prediction honestly but without judgment of the proposer.
2. Note what was predicted correctly and what surprised everyone.
3. If the rule had unintended consequences, describe them vividly.
4. Be specific — use the actual numbers. "Three-point shooting rose 12%" not "shooting increased."
5. End with what this reveals about the league's understanding of its own rules.

## Proposal & Prediction
{proposal_data}

## Gameplay Before Rule Change
{stats_before}

## Gameplay After Rule Change
{stats_after}
"""

LEVERAGE_DETECTION_PROMPT = """\
You are generating a private influence analysis for governor "{governor_id}" in Pinwheel Fates.

This report shows a governor how their votes and proposals actually shaped outcomes.
Only they see this. Be honest and specific.

## Rules
1. DESCRIBE their influence patterns. Never PRESCRIBE what they should do.
2. If they're a swing voter, tell them — that's powerful information.
3. If their proposals always pass, note what that means about their read of the league.
4. If they vote against their team often, note the pattern without judgment.
5. Compare to league averages where relevant, but never name other governors.

## Governor Influence Data
{leverage_data}
"""

BEHAVIORAL_PROFILE_PROMPT = """\
You are generating a longitudinal behavioral profile for governor "{governor_id}" \
in Pinwheel Fates.

Unlike the per-round private report, this looks at their ENTIRE season arc.
What patterns emerge over time? Only they see this.

## Rules
1. DESCRIBE patterns across time. Never PRESCRIBE future actions.
2. Note trajectory: are they getting bolder? More conservative? More engaged?
3. If their proposal focus shifted, name the shift specifically.
4. Note coalition patterns without naming other governors ("one other governor" is fine).
5. Be reflective and insightful — this should feel like a coaching session, not a report card.

## Governor Season Profile
{profile_data}
"""

NEWSPAPER_HEADLINE_PROMPT = """\
You are writing the headline and subhead for The Pinwheel Post, \
the newspaper of Pinwheel Fates.

Return ONLY valid JSON: {{"headline": "...", "subhead": "..."}}

The headline should be punchy and vivid (1 short sentence, all caps style).
The subhead should provide governance context (1 sentence, normal case).

## Round Data
{round_data}
"""


# ---------------------------------------------------------------------------
# Phase 1: Impact Validation
# ---------------------------------------------------------------------------

async def compute_impact_validation(
    repo: Repository,
    season_id: str,
    round_number: int,
    governance_data: dict,
) -> list[dict]:
    """Assemble data for each rule change enacted this round.

    Returns a list of dicts, one per enacted rule, each with proposal context,
    before/after gameplay stats, and computed deltas.
    """
    rules_changed = governance_data.get("rules_changed", [])
    if not rules_changed:
        return []

    validations: list[dict] = []
    for rc in rules_changed:
        parameter = rc.get("parameter", "unknown")
        old_value = rc.get("old_value")
        new_value = rc.get("new_value")
        proposal_text = rc.get("proposal_text", "")
        impact_prediction = rc.get("impact_analysis", "No prediction recorded.")
        enacted_round = rc.get("round_number", round_number)

        # Stats before the rule change
        stats_before = await repo.get_game_stats_for_rounds(
            season_id, 1, max(1, enacted_round - 1),
        )

        # Stats after the rule change (including current round)
        stats_after = await repo.get_game_stats_for_rounds(
            season_id, enacted_round, round_number,
        )

        # Compute deltas
        deltas: dict[str, float] = {}
        for key in ("avg_score", "avg_margin", "three_point_pct", "field_goal_pct",
                     "avg_possessions", "elam_activation_rate"):
            before_val = stats_before.get(key, 0)
            after_val = stats_after.get(key, 0)
            if isinstance(before_val, (int, float)) and isinstance(after_val, (int, float)):
                deltas[key] = round(after_val - before_val, 2)

        validations.append({
            "proposal_text": proposal_text,
            "impact_prediction": impact_prediction,
            "parameter": parameter,
            "old_value": old_value,
            "new_value": new_value,
            "rounds_under_rule": max(0, round_number - enacted_round + 1),
            "stats_before": stats_before,
            "stats_after": stats_after,
            "deltas": deltas,
        })

    return validations


async def generate_impact_validation(
    validation_data: list[dict],
    season_id: str,
    round_number: int,
    api_key: str,
) -> Report:
    """Generate an impact validation report using Claude."""
    from pinwheel.ai.report import _call_claude

    sections: list[str] = []
    for v in validation_data:
        proposal_str = json.dumps({
            "proposal_text": v["proposal_text"],
            "prediction": v["impact_prediction"],
            "parameter": v["parameter"],
            "old_value": v["old_value"],
            "new_value": v["new_value"],
            "rounds_under_rule": v["rounds_under_rule"],
        }, indent=2)
        before_str = json.dumps(v["stats_before"], indent=2)
        after_str = json.dumps(v["stats_after"], indent=2)

        content = await _call_claude(
            system=IMPACT_VALIDATION_PROMPT.format(
                proposal_data=proposal_str,
                stats_before=before_str,
                stats_after=after_str,
            ),
            user_message="Validate this governance prediction against gameplay reality.",
            api_key=api_key,
            call_type="report.impact_validation",
            season_id=season_id,
            round_number=round_number,
        )
        sections.append(content)

    return Report(
        id=f"r-impact-{round_number}-{uuid.uuid4().hex[:8]}",
        report_type="impact_validation",
        round_number=round_number,
        content="\n\n---\n\n".join(sections),
    )


def generate_impact_validation_mock(
    validation_data: list[dict],
    season_id: str,
    round_number: int,
) -> Report:
    """Mock impact validation — deterministic, uses real data."""
    if not validation_data:
        return Report(
            id=f"r-impact-{round_number}-mock",
            report_type="impact_validation",
            round_number=round_number,
            content="No rule changes to validate this round.",
        )

    paragraphs: list[str] = []
    for v in validation_data:
        param = v["parameter"]
        old = v["old_value"]
        new = v["new_value"]
        deltas = v.get("deltas", {})
        rounds = v["rounds_under_rule"]

        lines = [
            f"**Rule Change: `{param}` from {old} to {new}**",
            f"Prediction: {v['impact_prediction'][:200]}",
            f"After {rounds} round(s) under the new rule:",
        ]

        for key, delta in deltas.items():
            direction = "increased" if delta > 0 else "decreased" if delta < 0 else "unchanged"
            label = key.replace("_", " ").title()
            lines.append(f"- {label}: {direction} by {abs(delta):.1f}")

        before_games = v["stats_before"].get("game_count", 0)
        after_games = v["stats_after"].get("game_count", 0)
        lines.append(f"(Based on {before_games} games before, {after_games} games after)")

        paragraphs.append("\n".join(lines))

    return Report(
        id=f"r-impact-{round_number}-mock",
        report_type="impact_validation",
        round_number=round_number,
        content="\n\n".join(paragraphs),
    )


# ---------------------------------------------------------------------------
# Phase 2: Hidden Leverage Detection
# ---------------------------------------------------------------------------

async def compute_governor_leverage(
    repo: Repository,
    governor_id: str,
    season_id: str,
) -> dict:
    """Compute influence metrics for a single governor.

    Returns a dict with: vote_alignment_rate, swing_count, swing_rate,
    proposal_success_rate, cross_team_vote_rate, proposals_submitted,
    proposals_passed, votes_cast, total_proposals_decided.
    """
    # All votes in the season
    all_votes = await repo.get_events_by_type(season_id, ["vote.cast"])
    # All outcomes
    all_outcomes = await repo.get_events_by_type(
        season_id, ["proposal.passed", "proposal.failed"],
    )

    # Index outcomes by proposal_id
    outcome_map: dict[str, bool] = {}  # proposal_id -> passed?
    for e in all_outcomes:
        pid = e.payload.get("proposal_id", e.aggregate_id)
        outcome_map[pid] = e.event_type == "proposal.passed"

    # This governor's votes
    gov_votes = [v for v in all_votes if v.governor_id == governor_id]

    # Vote alignment: did their vote match the outcome?
    correct = 0
    total_decided = 0
    for v in gov_votes:
        pid = v.payload.get("proposal_id", "")
        if pid not in outcome_map:
            continue
        total_decided += 1
        voted_yes = v.payload.get("vote") == "yes"
        if voted_yes == outcome_map[pid]:
            correct += 1

    alignment_rate = correct / total_decided if total_decided > 0 else 0.0

    # Swing vote detection: for each proposal with an outcome,
    # compute weighted margin. If removing this governor's vote flips it,
    # they were a swing voter.
    swing_count = 0
    proposals_with_outcome = set(outcome_map.keys())

    for pid in proposals_with_outcome:
        # Collect all votes for this proposal
        pid_votes = [v for v in all_votes if v.payload.get("proposal_id") == pid]
        weighted_yes = sum(
            float(v.payload.get("weight", 1.0))
            for v in pid_votes
            if v.payload.get("vote") == "yes"
        )
        weighted_no = sum(
            float(v.payload.get("weight", 1.0))
            for v in pid_votes
            if v.payload.get("vote") == "no"
        )

        # Find this governor's vote in this proposal
        gov_vote_in_pid = [v for v in pid_votes if v.governor_id == governor_id]
        if not gov_vote_in_pid:
            continue

        gv = gov_vote_in_pid[0]
        gov_weight = float(gv.payload.get("weight", 1.0))
        gov_voted_yes = gv.payload.get("vote") == "yes"

        # Remove their vote and check if outcome flips
        if gov_voted_yes:
            new_yes = weighted_yes - gov_weight
            new_no = weighted_no
        else:
            new_yes = weighted_yes
            new_no = weighted_no - gov_weight

        total_without = new_yes + new_no
        if total_without == 0:
            continue

        original_passed = outcome_map[pid]
        # Default threshold is majority
        would_pass_without = new_yes / total_without > 0.5
        if original_passed != would_pass_without:
            swing_count += 1

    swing_rate = swing_count / total_decided if total_decided > 0 else 0.0

    # Proposal success rate
    activity = await repo.get_governor_activity(governor_id, season_id)
    proposals_submitted = activity.get("proposals_submitted", 0)
    proposals_passed = activity.get("proposals_passed", 0)
    proposal_success_rate = (
        proposals_passed / proposals_submitted if proposals_submitted > 0 else 0.0
    )

    # Cross-team voting: how often does this governor vote differently
    # from majority of their team?
    # For simplicity: count votes on proposals from other teams
    gov_team_proposals: set[str] = set()
    player = await repo.get_player(governor_id)
    if player and player.team_id:
        team_proposals = [
            e for e in await repo.get_events_by_type(season_id, ["proposal.submitted"])
            if e.team_id == player.team_id
        ]
        gov_team_proposals = {
            e.payload.get("id", e.aggregate_id) for e in team_proposals
        }

    cross_team_votes = 0
    for v in gov_votes:
        pid = v.payload.get("proposal_id", "")
        if pid and pid not in gov_team_proposals:
            cross_team_votes += 1

    cross_team_rate = cross_team_votes / len(gov_votes) if gov_votes else 0.0

    return {
        "governor_id": governor_id,
        "votes_cast": len(gov_votes),
        "total_proposals_decided": total_decided,
        "vote_alignment_rate": round(alignment_rate, 3),
        "swing_count": swing_count,
        "swing_rate": round(swing_rate, 3),
        "proposals_submitted": proposals_submitted,
        "proposals_passed": proposals_passed,
        "proposal_success_rate": round(proposal_success_rate, 3),
        "cross_team_vote_rate": round(cross_team_rate, 3),
    }


async def generate_leverage_report(
    leverage_data: dict,
    governor_id: str,
    season_id: str,
    round_number: int,
    api_key: str,
) -> Report:
    """Generate a private leverage report using Claude."""
    from pinwheel.ai.report import _call_claude

    content = await _call_claude(
        system=LEVERAGE_DETECTION_PROMPT.format(
            governor_id=governor_id,
            leverage_data=json.dumps(leverage_data, indent=2),
        ),
        user_message=f"Generate an influence analysis for governor {governor_id}.",
        api_key=api_key,
        call_type="report.leverage",
        season_id=season_id,
        round_number=round_number,
    )
    return Report(
        id=f"r-lev-{round_number}-{uuid.uuid4().hex[:8]}",
        report_type="leverage",
        round_number=round_number,
        governor_id=governor_id,
        content=content,
    )


def generate_leverage_report_mock(
    leverage_data: dict,
    governor_id: str,
    season_id: str,
    round_number: int,
) -> Report:
    """Mock leverage report — deterministic, uses real data."""
    gov = leverage_data.get("governor_id", governor_id)
    votes = leverage_data.get("votes_cast", 0)
    alignment = leverage_data.get("vote_alignment_rate", 0)
    swings = leverage_data.get("swing_count", 0)
    success = leverage_data.get("proposal_success_rate", 0)
    cross = leverage_data.get("cross_team_vote_rate", 0)

    lines: list[str] = [f"**Influence Analysis for {gov}**\n"]

    if votes == 0:
        lines.append("You haven't cast any votes yet this season. "
                      "Your influence is entirely potential — untested, unmeasured.")
    else:
        lines.append(
            f"You've voted {votes} times this season. "
            f"Your votes aligned with the final outcome {alignment:.0%} of the time."
        )

        if swings > 0:
            lines.append(
                f"\nYou were a swing voter on {swings} proposal(s) — "
                "your vote alone determined the outcome. That's real power."
            )
        else:
            lines.append(
                "\nYou haven't been the deciding vote on any proposals yet. "
                "Your influence flows through consensus, not margins."
            )

        if success > 0:
            lines.append(
                f"\nYour proposals pass at a {success:.0%} rate."
            )

        if cross > 0.3:
            lines.append(
                f"\n{cross:.0%} of your votes were on proposals from outside your team — "
                "you're an active cross-team participant."
            )

    return Report(
        id=f"r-lev-{round_number}-mock",
        report_type="leverage",
        round_number=round_number,
        governor_id=governor_id,
        content="\n".join(lines),
    )


# ---------------------------------------------------------------------------
# Phase 3: Behavioral Pattern Detection (Longitudinal)
# ---------------------------------------------------------------------------

async def compute_behavioral_profile(
    repo: Repository,
    governor_id: str,
    season_id: str,
) -> dict:
    """Compute longitudinal behavioral profile for a governor.

    Returns a dict with: proposal_timeline, tier_trend, engagement_arc,
    coalition_signal, total_actions, proposals_count.
    """
    # All proposals by this governor
    gov_proposals = await repo.get_events_by_type_and_governor(
        season_id, governor_id, ["proposal.submitted"],
    )

    # Track proposal parameters over time
    proposal_timeline: list[dict] = []
    tier_values: list[int] = []
    for e in gov_proposals:
        p_data = e.payload
        interp = p_data.get("interpretation", {})
        param = interp.get("parameter", "unknown") if isinstance(interp, dict) else "unknown"
        tier = p_data.get("tier", 1)
        proposal_timeline.append({
            "round": e.round_number or 0,
            "parameter": param,
            "tier": tier,
            "text": p_data.get("raw_text", "")[:100],
        })
        tier_values.append(tier)

    # Tier trend: compare first half to second half
    tier_trend = "stable"
    if len(tier_values) >= 4:
        mid = len(tier_values) // 2
        first_avg = sum(tier_values[:mid]) / mid
        second_avg = sum(tier_values[mid:]) / (len(tier_values) - mid)
        if second_avg - first_avg > 0.5:
            tier_trend = "increasing"
        elif first_avg - second_avg > 0.5:
            tier_trend = "decreasing"

    # Engagement arc: actions per round
    gov_votes = await repo.get_events_by_type_and_governor(
        season_id, governor_id, ["vote.cast"],
    )
    all_gov_events = list(gov_proposals) + list(gov_votes)

    actions_by_round: dict[int, int] = {}
    for e in all_gov_events:
        rn = e.round_number or 0
        actions_by_round[rn] = actions_by_round.get(rn, 0) + 1

    rounds_active = sorted(actions_by_round.keys())
    engagement_arc = "stable"
    if len(rounds_active) >= 4:
        mid = len(rounds_active) // 2
        first_rates = [actions_by_round[r] for r in rounds_active[:mid]]
        second_rates = [actions_by_round[r] for r in rounds_active[mid:]]
        first_avg = sum(first_rates) / len(first_rates)
        second_avg = sum(second_rates) / len(second_rates)
        if second_avg > first_avg * 1.3:
            engagement_arc = "warming_up"
        elif second_avg < first_avg * 0.7:
            engagement_arc = "fading"

    # Coalition signal: pairwise vote correlation (anonymized)
    all_votes = await repo.get_events_by_type(season_id, ["vote.cast"])
    # Build vote map: proposal_id -> {gov_id: "yes"/"no"}
    vote_map: dict[str, dict[str, str]] = {}
    for v in all_votes:
        pid = v.payload.get("proposal_id", "")
        if pid:
            vote_map.setdefault(pid, {})[v.governor_id or ""] = v.payload.get("vote", "")

    # Compute pairwise agreement with other governors
    other_governors: set[str] = set()
    for votes_dict in vote_map.values():
        other_governors.update(votes_dict.keys())
    other_governors.discard(governor_id)

    max_agreement = 0.0
    for other_id in other_governors:
        shared = 0
        agree = 0
        for _pid, votes_dict in vote_map.items():
            if governor_id in votes_dict and other_id in votes_dict:
                shared += 1
                if votes_dict[governor_id] == votes_dict[other_id]:
                    agree += 1
        if shared >= 2:
            rate = agree / shared
            if rate > max_agreement:
                max_agreement = rate

    coalition_signal = None
    if max_agreement >= 0.7 and len(other_governors) > 0:
        coalition_signal = f"Your votes align {max_agreement:.0%} with one other governor."

    return {
        "governor_id": governor_id,
        "proposals_count": len(gov_proposals),
        "proposal_timeline": proposal_timeline,
        "tier_trend": tier_trend,
        "engagement_arc": engagement_arc,
        "actions_by_round": actions_by_round,
        "total_actions": len(all_gov_events),
        "votes_cast": len(gov_votes),
        "coalition_signal": coalition_signal,
    }


async def generate_behavioral_report(
    profile_data: dict,
    governor_id: str,
    season_id: str,
    round_number: int,
    api_key: str,
) -> Report:
    """Generate a private behavioral profile report using Claude."""
    from pinwheel.ai.report import _call_claude

    content = await _call_claude(
        system=BEHAVIORAL_PROFILE_PROMPT.format(
            governor_id=governor_id,
            profile_data=json.dumps(profile_data, indent=2),
        ),
        user_message=f"Generate a longitudinal behavioral profile for governor {governor_id}.",
        api_key=api_key,
        call_type="report.behavioral",
        season_id=season_id,
        round_number=round_number,
    )
    return Report(
        id=f"r-beh-{round_number}-{uuid.uuid4().hex[:8]}",
        report_type="behavioral",
        round_number=round_number,
        governor_id=governor_id,
        content=content,
    )


def generate_behavioral_report_mock(
    profile_data: dict,
    governor_id: str,
    season_id: str,
    round_number: int,
) -> Report:
    """Mock behavioral report — deterministic, uses real data."""
    proposals = profile_data.get("proposals_count", 0)
    tier_trend = profile_data.get("tier_trend", "stable")
    engagement = profile_data.get("engagement_arc", "stable")
    coalition = profile_data.get("coalition_signal")
    total = profile_data.get("total_actions", 0)

    lines = [f"**Season Arc for {governor_id}**\n"]

    if total == 0:
        lines.append("You've been quiet this season. No proposals, no votes recorded yet.")
    else:
        # Engagement description
        arc_desc = {
            "warming_up": "Your engagement has been growing — more active in recent rounds.",
            "fading": "Your activity has tapered off in recent rounds.",
            "stable": "Your engagement has been consistent throughout the season.",
        }
        lines.append(arc_desc.get(engagement, "Your engagement pattern is unique."))

        if proposals > 0:
            # Tier trend
            trend_desc = {
                "increasing": f"Your {proposals} proposals show an arc toward bolder changes — "
                              "tier values are climbing.",
                "decreasing": f"Your {proposals} proposals started ambitious and have become "
                              "more conservative over time.",
                "stable": f"Your {proposals} proposals have maintained a consistent "
                          "ambition level.",
            }
            lines.append(trend_desc.get(tier_trend, f"You've submitted {proposals} proposals."))

        if coalition:
            lines.append(f"\n{coalition}")

    return Report(
        id=f"r-beh-{round_number}-mock",
        report_type="behavioral",
        round_number=round_number,
        governor_id=governor_id,
        content="\n".join(lines),
    )


# ---------------------------------------------------------------------------
# Phase 4: Newspaper Headlines
# ---------------------------------------------------------------------------

async def generate_newspaper_headlines(
    round_data: dict,
    season_id: str,
    round_number: int,
    api_key: str,
) -> dict[str, str]:
    """Generate headline + subhead for the Pinwheel Post."""
    from pinwheel.ai.report import _call_claude

    data_str = json.dumps(round_data, indent=2)
    content = await _call_claude(
        system=NEWSPAPER_HEADLINE_PROMPT.format(round_data=data_str),
        user_message="Generate a newspaper headline and subhead for this round.",
        api_key=api_key,
        call_type="report.newspaper",
        season_id=season_id,
        round_number=round_number,
    )

    # Parse JSON response
    try:
        result = json.loads(content)
        return {
            "headline": str(result.get("headline", "ROUND COMPLETE")),
            "subhead": str(result.get("subhead", "")),
        }
    except (json.JSONDecodeError, AttributeError):
        # Fallback: use the raw text as headline
        return {"headline": content[:100], "subhead": ""}


def generate_newspaper_headlines_mock(
    round_data: dict,
    round_number: int,
    *,
    playoff_phase: str = "",
    total_games_played: int = 0,
) -> dict[str, str]:
    """Mock newspaper headlines — reads the context to figure out the story.

    The data tells us everything: a championship game has a winner (that's
    the champion), a blowout tells us who dominated, a close game tells us
    about drama. We don't need external flags — just look at what happened.
    """
    games = round_data.get("games", [])
    if not games:
        return {
            "headline": f"SILENCE ON THE COURTS — ROUND {round_number}",
            "subhead": "No games played this round.",
        }

    # Analyze every game
    closest_margin = 999
    biggest_margin = 0
    closest_game: dict = {}
    biggest_game: dict = {}

    for g in games:
        margin = abs(g.get("home_score", 0) - g.get("away_score", 0))
        if margin < closest_margin:
            closest_margin = margin
            closest_game = g
        if margin > biggest_margin:
            biggest_margin = margin
            biggest_game = g

    # Pick the feature game — close games are more dramatic than blowouts
    feature = closest_game if closest_margin <= 5 else biggest_game
    winner = feature.get(
        "winner_team_name", feature.get("winner_team_id", "???")
    )
    w_score = feature.get("home_score", 0)
    l_score = feature.get("away_score", 0)
    if feature.get("winner_team_name") != feature.get("home_team_name"):
        w_score, l_score = l_score, w_score
    margin = abs(w_score - l_score)

    # Figure out the loser
    loser = ""
    if feature.get("winner_team_name") == feature.get("home_team_name"):
        loser = feature.get("away_team_name", "")
    else:
        loser = feature.get("home_team_name", "")

    # Championship game — the winner IS the champion. Season is over.
    is_championship = playoff_phase in ("finals", "championship")
    is_playoff = is_championship or playoff_phase == "semifinal"

    if is_championship:
        headline = (
            f"{winner.upper()} ARE YOUR CHAMPIONS"
        )
        subhead = f"Defeated {loser} {w_score}-{l_score} to claim the title."
    elif closest_margin <= 3 and closest_game:
        headline = f"DOWN TO THE WIRE! {winner} survives by {margin}"
        subhead = _round_subhead(
            round_data, is_playoff, total_games_played, len(games),
        )
    elif biggest_margin >= 10 and biggest_game:
        headline = f"DOMINANT! {winner} rolls {w_score}-{l_score}"
        subhead = _round_subhead(
            round_data, is_playoff, total_games_played, len(games),
        )
    elif is_playoff:
        headline = f"{winner.upper()} TAKES THE SERIES LEAD"
        subhead = f"Beat {loser} {w_score}-{l_score}."
    else:
        headline = f"{winner} wins Round {round_number}"
        subhead = _round_subhead(
            round_data, is_playoff, total_games_played, len(games),
        )

    return {"headline": headline, "subhead": subhead}


def _round_subhead(
    round_data: dict,
    is_playoff: bool,
    total_games_played: int,
    games_this_round: int,
) -> str:
    """Build a contextual subhead for non-championship rounds."""
    governance_data = round_data.get("governance", {})
    rules_changed = governance_data.get("rules_changed", [])
    if rules_changed:
        param = rules_changed[0].get("parameter", "a rule")
        return f"The Floor rewrites {param} as governance reshapes the game."
    if is_playoff:
        return "The postseason rolls on."
    if total_games_played > 0:
        return f"{total_games_played} games played this season."
    n = games_this_round
    return f"{n} {'game' if n == 1 else 'games'} this round."
