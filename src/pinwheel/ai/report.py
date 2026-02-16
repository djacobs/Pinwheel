"""AI report generation — Claude-powered reports on gameplay and governance.

Three report types for Day 3:
- Simulation report: reflects on game results, statistical patterns, emergent behavior
- Governance report: reflects on proposal patterns, voting dynamics, rule evolution
- Private report: reflects on a single governor's behavior (visible only to them)

All reports follow the same constraint: they DESCRIBE patterns, never PRESCRIBE actions.
The AI observes; humans decide.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING

import anthropic

from pinwheel.core.narrative import NarrativeContext, format_narrative_for_prompt
from pinwheel.models.report import Report

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository

logger = logging.getLogger(__name__)


def compute_pairwise_alignment(
    votes: list[dict],
) -> list[dict[str, str | int | float]]:
    """Compute pairwise voting agreement between governors.

    For each pair of governors who voted on at least one common proposal,
    computes the number of shared proposals and the agreement percentage.

    Args:
        votes: List of vote dicts, each with at least "governor_id",
            "proposal_id", and "vote" keys.

    Returns:
        List of dicts sorted by agreement_pct descending, each with:
        - governor_a: str
        - governor_b: str
        - shared_proposals: int (number of proposals both voted on)
        - agreed: int (number they voted the same way)
        - agreement_pct: float (0.0-100.0)
    """
    # Build a mapping: governor_id -> {proposal_id: vote_choice}
    governor_votes: dict[str, dict[str, str]] = defaultdict(dict)
    for v in votes:
        gov_id = v.get("governor_id", "")
        prop_id = v.get("proposal_id", "")
        choice = v.get("vote", "")
        if gov_id and prop_id and choice:
            governor_votes[gov_id][prop_id] = choice

    governor_ids = sorted(governor_votes.keys())
    pairs: list[dict[str, str | int | float]] = []

    for i, gov_a in enumerate(governor_ids):
        for gov_b in governor_ids[i + 1 :]:
            # Find proposals both voted on
            shared = set(governor_votes[gov_a].keys()) & set(
                governor_votes[gov_b].keys()
            )
            if not shared:
                continue
            agreed = sum(
                1
                for pid in shared
                if governor_votes[gov_a][pid] == governor_votes[gov_b][pid]
            )
            pct = (agreed / len(shared)) * 100.0
            pairs.append({
                "governor_a": gov_a,
                "governor_b": gov_b,
                "shared_proposals": len(shared),
                "agreed": agreed,
                "agreement_pct": round(pct, 1),
            })

    # Sort by agreement_pct descending, then shared_proposals descending
    pairs.sort(
        key=lambda p: (-float(p["agreement_pct"]), -int(p["shared_proposals"]))
    )
    return pairs


def compute_proposal_parameter_clustering(
    proposals: list[dict[str, str | int | float | None]],
    history: list[dict[str, str | int | float | None]] | None = None,
) -> list[dict[str, str | int]]:
    """Detect proposal parameter clustering within a round and across history.

    Groups proposals by parameter category prefix (e.g. "three_point_value"
    -> "three_point", "elam_margin" -> "elam") and counts how many proposals
    target each category.

    When ``history`` is provided (proposals from earlier rounds), also checks
    for recurring category focus across rounds.

    Args:
        proposals: Current round's proposals, each with an optional
            "parameter" key.
        history: Optional list of proposals from previous rounds, each with
            an optional "parameter" key and optional "round_number" key.

    Returns:
        List of dicts sorted by count descending, each with:
        - category: str (the parameter category prefix)
        - count: int (number of proposals targeting this category this round)
        - historical_count: int (total from history targeting this category)
    """

    def _extract_category(param: str) -> str:
        """Extract category prefix from a parameter name."""
        parts = param.split("_")
        if len(parts) >= 2:
            # Compound prefixes like "three_point", "shot_clock", "dead_ball", "home_court"
            if parts[0] in ("three", "shot", "dead", "home"):
                return "_".join(parts[:2])
            return parts[0]
        return param

    # Current round categories
    current_categories: dict[str, int] = {}
    for p in proposals:
        param = p.get("parameter")
        if param and isinstance(param, str):
            cat = _extract_category(param)
            current_categories[cat] = current_categories.get(cat, 0) + 1

    # Historical categories
    historical_categories: dict[str, int] = {}
    if history:
        for p in history:
            param = p.get("parameter")
            if param and isinstance(param, str):
                cat = _extract_category(param)
                historical_categories[cat] = historical_categories.get(cat, 0) + 1

    results: list[dict[str, str | int]] = []
    for cat in current_categories:
        results.append({
            "category": cat,
            "count": current_categories[cat],
            "historical_count": historical_categories.get(cat, 0),
        })

    results.sort(key=lambda r: -int(r["count"]))
    return results


def compute_governance_velocity(
    current_round_proposals: int,
    current_round_votes: int,
    season_proposals_by_round: dict[int, int] | None = None,
    season_votes_by_round: dict[int, int] | None = None,
) -> dict[str, str | float | int | bool]:
    """Assess governance velocity -- is this the most/least active window?

    Compares the current round's activity to historical averages.

    Args:
        current_round_proposals: Number of proposals this round.
        current_round_votes: Number of votes this round.
        season_proposals_by_round: Optional dict mapping round_number to
            proposal count across the full season.
        season_votes_by_round: Optional dict mapping round_number to vote
            count across the full season.

    Returns:
        Dict with:
        - velocity_label: str ("peak", "high", "normal", "low", "silent")
        - proposals_this_round: int
        - votes_this_round: int
        - avg_proposals_per_round: float
        - avg_votes_per_round: float
        - is_season_peak: bool (True if this is the most active round so far)
    """
    total_activity = current_round_proposals + current_round_votes

    # Compute historical averages
    avg_proposals = 0.0
    avg_votes = 0.0
    is_peak = False

    if season_proposals_by_round:
        total_proposals = sum(season_proposals_by_round.values())
        avg_proposals = total_proposals / len(season_proposals_by_round)
        max_proposals = max(season_proposals_by_round.values())
        if current_round_proposals > max_proposals:
            is_peak = True

    if season_votes_by_round:
        total_votes = sum(season_votes_by_round.values())
        avg_votes = total_votes / len(season_votes_by_round)
        max_votes = max(season_votes_by_round.values())
        if current_round_votes > max_votes:
            is_peak = True

    # Classify velocity
    avg_total = avg_proposals + avg_votes
    if total_activity == 0:
        label = "silent"
    elif avg_total > 0 and total_activity >= avg_total * 2.0:
        label = "peak"
    elif avg_total > 0 and total_activity >= avg_total * 1.3:
        label = "high"
    elif avg_total > 0 and total_activity <= avg_total * 0.5:
        label = "low"
    else:
        label = "normal"

    return {
        "velocity_label": label,
        "proposals_this_round": current_round_proposals,
        "votes_this_round": current_round_votes,
        "avg_proposals_per_round": round(avg_proposals, 1),
        "avg_votes_per_round": round(avg_votes, 1),
        "is_season_peak": is_peak,
    }


def detect_governance_blind_spots(
    proposals: list[dict[str, str | int | float | None]],
    rules_changed: list[dict[str, str | int | float | None]],
    all_parameter_categories: list[str] | None = None,
) -> list[str]:
    """Identify areas of the game NOT being targeted by governance.

    Compares what categories proposals target against the full parameter
    space. Returns categories that have never been proposed against.

    Args:
        proposals: All proposals from the season (or recent window), each
            with an optional "parameter" key.
        rules_changed: All rule changes enacted, each with an optional
            "parameter" key.
        all_parameter_categories: Optional explicit list of all parameter
            categories in the game. Defaults to a standard set.

    Returns:
        List of parameter category names that have NOT been targeted.
    """
    if all_parameter_categories is None:
        all_parameter_categories = [
            "scoring",
            "defense",
            "pace",
            "three_point",
            "elam",
            "foul",
            "stamina",
            "shot_clock",
            "rebound",
            "turnover",
        ]

    # Collect all categories that have been targeted
    targeted: set[str] = set()
    for p in list(proposals) + list(rules_changed):
        param = p.get("parameter")
        if param and isinstance(param, str):
            param_lower = param.lower()
            for cat in all_parameter_categories:
                if cat in param_lower:
                    targeted.add(cat)

    # Return untargeted categories
    return [cat for cat in all_parameter_categories if cat not in targeted]


# ---------------------------------------------------------------------------
# Parameter categorization — maps governance parameters to human-readable
# gameplay categories for blind-spot analysis in private reports.
# ---------------------------------------------------------------------------

PARAMETER_CATEGORIES: dict[str, str] = {
    # Offense
    "three_point_value": "offense",
    "two_point_value": "offense",
    "free_throw_value": "offense",
    "three_point_distance": "offense",
    "max_shot_share": "offense",
    "min_pass_per_possession": "offense",
    "offensive_rebound_weight": "offense",
    # Defense
    "personal_foul_limit": "defense",
    "team_foul_bonus_threshold": "defense",
    "foul_rate_modifier": "defense",
    "turnover_rate_modifier": "defense",
    "crowd_pressure": "defense",
    # Pace
    "quarter_minutes": "pace",
    "shot_clock_seconds": "pace",
    "stamina_drain_rate": "pace",
    "halftime_stamina_recovery": "pace",
    "quarter_break_stamina_recovery": "pace",
    "dead_ball_time_seconds": "pace",
    "safety_cap_possessions": "pace",
    "substitution_stamina_threshold": "pace",
    # Endgame
    "elam_trigger_quarter": "endgame",
    "elam_margin": "endgame",
    # Environment
    "home_court_enabled": "environment",
    "home_crowd_boost": "environment",
    "away_fatigue_factor": "environment",
    "altitude_stamina_penalty": "environment",
    "travel_fatigue_enabled": "environment",
    "travel_fatigue_per_mile": "environment",
    # Structure
    "teams_count": "structure",
    "round_robins_per_season": "structure",
    "playoff_teams": "structure",
    "playoff_semis_best_of": "structure",
    "playoff_finals_best_of": "structure",
    # Meta-governance
    "proposals_per_window": "meta-governance",
    "vote_threshold": "meta-governance",
}


def categorize_parameter(param: str | None) -> str:
    """Map a governance parameter name to a human-readable category.

    Returns the category name (e.g. "offense", "defense", "pace") or
    "other" if the parameter is unknown or None.
    """
    if param is None:
        return "other"
    return PARAMETER_CATEGORIES.get(param, "other")


def compute_category_distribution(
    proposals: list[dict[str, str | int | None]],
) -> dict[str, int]:
    """Count proposals per gameplay category.

    Args:
        proposals: List of dicts, each with at least a "parameter" key
            (str or None).

    Returns:
        Dict mapping category name to count, sorted by count descending.
    """
    counts: dict[str, int] = defaultdict(int)
    for p in proposals:
        cat = categorize_parameter(p.get("parameter"))  # type: ignore[arg-type]
        counts[cat] += 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


async def compute_private_report_context(
    repo: Repository,
    governor_id: str,
    season_id: str,
    round_number: int,
) -> dict[str, object]:
    """Assemble rich context data for a governor's private report.

    Computes the governor's proposal focus vs league-wide activity, surfaces
    blind spots (categories they haven't touched but the league has changed),
    and connects their voting record to actual outcomes.
    """
    # 1. Governor's own proposals
    gov_submitted = await repo.get_events_by_type_and_governor(
        season_id=season_id,
        governor_id=governor_id,
        event_types=["proposal.submitted"],
    )
    gov_proposal_details: list[dict[str, str | int | None]] = []
    for e in gov_submitted:
        p_data = e.payload
        interp = p_data.get("interpretation")
        parameter = None
        if interp and isinstance(interp, dict):
            parameter = interp.get("parameter")
        gov_proposal_details.append({
            "raw_text": p_data.get("raw_text", ""),
            "parameter": parameter,
            "tier": p_data.get("tier", 1),
        })

    gov_categories = compute_category_distribution(gov_proposal_details)

    # 2. All proposals in the season (league-wide)
    all_submitted = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted"],
    )
    all_proposal_details: list[dict[str, str | int | None]] = []
    for e in all_submitted:
        p_data = e.payload
        interp = p_data.get("interpretation")
        parameter = None
        if interp and isinstance(interp, dict):
            parameter = interp.get("parameter")
        all_proposal_details.append({"parameter": parameter})

    league_categories = compute_category_distribution(all_proposal_details)

    # 3. Rule changes that actually enacted (league-wide)
    rule_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["rule.enacted"],
    )
    rule_change_details: list[dict[str, str | int | None]] = []
    for e in rule_events:
        rule_change_details.append({"parameter": e.payload.get("parameter")})

    rule_change_categories = compute_category_distribution(rule_change_details)

    # 4. Blind spots
    blind_spots: list[str] = []
    for cat in rule_change_categories:
        if cat != "other" and cat not in gov_categories:
            blind_spots.append(cat)

    # 5. Voting outcomes
    gov_votes = await repo.get_events_by_type_and_governor(
        season_id=season_id,
        governor_id=governor_id,
        event_types=["vote.cast"],
    )

    outcome_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.passed", "proposal.failed"],
    )
    outcome_map: dict[str, str] = {}
    for e in outcome_events:
        pid = e.payload.get("proposal_id", e.aggregate_id)
        outcome_map[pid] = "passed" if e.event_type == "proposal.passed" else "failed"

    proposal_text_map: dict[str, str] = {}
    proposal_param_map: dict[str, str | None] = {}
    for e in all_submitted:
        pid = e.payload.get("id", e.aggregate_id)
        proposal_text_map[pid] = str(e.payload.get("raw_text", ""))[:100]
        interp = e.payload.get("interpretation")
        if interp and isinstance(interp, dict):
            proposal_param_map[pid] = interp.get("parameter")
        else:
            proposal_param_map[pid] = None

    voting_outcomes: list[dict[str, str]] = []
    correct = 0
    total_decided = 0
    for v in gov_votes:
        pid = v.payload.get("proposal_id", "")
        vote_choice = v.payload.get("vote", "")
        outcome = outcome_map.get(pid, "pending")
        proposal_text = proposal_text_map.get(pid, "")
        param = proposal_param_map.get(pid)
        category = categorize_parameter(param)
        voting_outcomes.append({
            "proposal_text": proposal_text,
            "vote": vote_choice,
            "outcome": outcome,
            "parameter": param or "unknown",
            "category": category,
        })
        if outcome in ("passed", "failed"):
            total_decided += 1
            voted_yes = vote_choice == "yes"
            passed = outcome == "passed"
            if voted_yes == passed:
                correct += 1

    alignment_rate = correct / total_decided if total_decided > 0 else 0.0

    # 6. Swing vote detection
    all_votes = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["vote.cast"],
    )
    swing_count = 0
    for pid, outcome in outcome_map.items():
        pid_votes = [
            v for v in all_votes if v.payload.get("proposal_id") == pid
        ]
        gov_vote_in_pid = [
            v for v in pid_votes if v.governor_id == governor_id
        ]
        if not gov_vote_in_pid:
            continue

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

        gv = gov_vote_in_pid[0]
        gov_weight = float(gv.payload.get("weight", 1.0))
        gov_voted_yes = gv.payload.get("vote") == "yes"

        if gov_voted_yes:
            new_yes = weighted_yes - gov_weight
            new_no = weighted_no
        else:
            new_yes = weighted_yes
            new_no = weighted_no - gov_weight

        total_without = new_yes + new_no
        if total_without == 0:
            continue

        original_passed = outcome == "passed"
        would_pass_without = new_yes / total_without > 0.5
        if original_passed != would_pass_without:
            swing_count += 1

    # 7. Governor's proposals with outcomes
    gov_activity = await repo.get_governor_activity(governor_id, season_id)

    return {
        "governor_id": governor_id,
        "proposals_submitted": len(gov_submitted),
        "votes_cast": len(gov_votes),
        "governor_proposal_categories": gov_categories,
        "league_proposal_categories": league_categories,
        "league_rule_change_categories": rule_change_categories,
        "blind_spots": blind_spots,
        "voting_outcomes": voting_outcomes,
        "alignment_rate": round(alignment_rate, 3),
        "swing_votes": swing_count,
        "total_league_proposals": len(all_submitted),
        "proposals_voted_on": len(gov_votes),
        "proposal_details": gov_activity.get("proposal_list", []),
    }




SIMULATION_REPORT_PROMPT = """\
You are the editor of The Pinwheel Post. You've watched every game, tracked every rule change, \
and studied the standings since opening day. After each round, you write one report — 3 to 5 \
paragraphs — that tells the story of what just happened and what the system reveals.

Your job is to find the ONE story, then surface what humans can't see from inside.

## Finding the Lede
Every round has one story. Find it. The hierarchy is strict — the first match wins:
1. Champion crowned — everything else is context for that moment.
2. Team eliminated — a sweep, a series loss, a season ending.
3. Upset — the last-place team beat the first-place team. Check the standings: rank matters.
4. Streak lives or dies — five or more straight wins/losses is a story.
5. Blowout or classic — a 15+ point demolition or a game decided by 3 or fewer.
6. Standings shifted — two teams swapped positions, a playoff berth clinched or slipping.
7. Rules changed — governance reshaped the game; this is the first round under new parameters.

Only ONE of these leads the piece. Everything else is supporting detail.

## Leading With What Changed
The report is NEWS. What is different after this round than before?

- Before/after: "The Thorns were 6-4 coming in. They're 7-4 now and alone in first."
- Rule correlation: If a rule changed recently, connect it to outcomes. "Scoring averaged 58 \
per game in the three rounds since three_point_value moved from 3 to 4 — up from 47 before."
- Narrowing margins: "The gap between first and last was 6 games entering the round. It's 4 now."
- Scoring variance: If all games were blowouts, say so. If all were close, say so. Compare to \
the season trend if system_context data is available.

## Surfacing the Invisible
This is what separates The Pinwheel Post from a box score. Governors control the rules but \
can't see the whole system. You can.

- Scoring trends: Is league-wide scoring rising, falling, or steady? Compare this round's \
average total to the season average if system_context provides it.
- Rule impact: If rules changed, look for correlation in the data. Did the parameter change \
produce the expected effect? Did it produce an unexpected one?
- Competitive balance: Are margins narrowing or widening? Is one team pulling away?
- Streak patterns: Which teams are trending up vs. down? A 3-game win streak after a 4-game \
losing streak is a reversal worth naming.

## Composing the Story
- Open with the lede — vivid, specific. Not "Round 8 saw some exciting games." \
Instead: "Rose City Thorns are your champions."
- Name the players — connect stats to their games. "Rosa Vex poured in 27 to close out the Hammers."
- Read the standings — a 10-4 team winning is expected; a 4-10 team winning is an upset.
- Detect the sweep — 3-0 in a series is a sweep, say so.
- Know where you are — regular season and championship are different universes.
- Close with what the round REVEALS about the system — not prescriptions, but patterns \
newly visible. What does this round tell us about where the league is heading? What dynamic \
just became visible that wasn't before?

## Early-Season Awareness
Sample size matters. Do not claim patterns, trends, or "tight races" from insufficient data.
- Round 1: Every team is 1-0 or 0-1. There are no streaks, no trends, no "compressed standings." \
The only story is what happened on the court. Write about the games, not the standings.
- Rounds 2-3: You can note a team's start (2-0, 0-2) but do not call it a trend. \
"Early returns" is honest; "dominant" or "struggling" is premature.
- Round 4+: Now you can start identifying patterns — but qualify them. "Through 4 rounds" \
is better than "this season" when the season is young.
- Never say the league is "tight" or "compressed" unless teams have played enough games \
for separation to be meaningful (at least 4-5 rounds).

## What You Never Do
- Never prescribe — describe only. "The Thorns have won seven straight" not "Teams need to adjust."
- Never be generic — every sentence names a team, a score, a streak, or a player.
- Never contradict the data.
- Never lead with the loser.
- Never pad. If three paragraphs tell the story, stop at three.
- Never open with "Round N saw..." or any template-filling language.
- Never state the obvious — if it's Round 1, don't marvel that teams are separated by 1 game.

The AI observes. Humans decide.

## Current Round Data

{round_data}
"""

GOVERNANCE_REPORT_PROMPT = """\
You are the Governance Mirror for Pinwheel Fates, a 3v3 basketball governance game.

Your job: surface what governors cannot see from inside the system. Individual governors see \
their own votes and proposals. You see ALL of them. Your report reveals the hidden patterns, \
emergent coalitions, and systemic blind spots that no single governor can perceive.

## What to Surface

### 1. Voting Coalitions
The pairwise_voting_alignment data shows which governors vote together and how often. \
This is the most important insight you provide -- governors do not know who their natural \
allies are until you tell them.
- Strong coalitions (80%+ agreement on 3+ proposals): Name both governors, state the \
agreement rate, and note what they tend to agree ON.
- Notable splits: If two governors on the same team vote differently, that is a fracture \
worth naming.
- Bloc formation: If 3+ governors all align above 70%, they are a voting bloc. Say so.
- Isolation: A governor who agrees with nobody above 50% is a lone voice. Note it.

### 2. Proposal Parameter Clustering
The parameter_clustering data shows which game dimensions governors are focused on. \
Surface the concentration:
- "3 of the last 4 proposals targeted scoring parameters -- the Floor is fixated on offense."
- "No proposals have touched defense, fouls, or stamina this season." (blind spots)
- If one team's governors consistently propose in the same category, that is coordinated \
strategy worth naming.

### 3. Governance Velocity
The velocity data tells you whether this is unusually active or quiet governance. \
Use it to set context:
- "This is the most active governance window of the season."
- "The Floor has gone quiet -- the fewest votes in any tally window."
- Compare to season averages when provided.

### 4. The Gap Between Votes and Outcomes
If rules changed, connect them to game impact. State parameter name, old value, new value \
explicitly (e.g., "three_point_value changed from 3 to 4"), then describe the expected \
effect on gameplay.
- If proposals failed, analyze what that reveals. Is it disagreement, or shared priority \
to keep things as they are?

### 5. Blind Spots
Surface what governance is NOT doing. The blind_spots data lists game categories that \
have never been targeted by proposals. If defense stats are declining but no proposals \
target defense, that is a story.

## Early-Season Awareness
Sample size matters. Do not claim patterns from insufficient data.
- Rounds 1-2: There may be zero or very few governance actions. If no proposals have been \
filed, say so plainly and move on — do not manufacture insights from absence.
- Rounds 3-4: You can note early tendencies but qualify them. "Early signs of" is honest; \
"clear pattern" is premature with 2-3 data points.
- Do not claim coalitions exist from fewer than 3 shared votes. Two agreements is coincidence.

## Composition Rules
1. You DESCRIBE. You never PRESCRIBE. Never say "governors should" or "the league needs to."
2. Write 3-5 paragraphs. Lead with the most interesting pattern -- coalitions forming, \
a parameter being targeted repeatedly, or a dramatic silence.
3. Name specific governors when discussing coalitions and voting patterns.
4. Close with governance window status and a "what the Floor is building" summary -- \
the trajectory of governance decisions, not just the count.
5. Every paragraph must contain at least one specific insight that individual governors \
cannot see from their own perspective.

The AI observes. Humans decide.

## Governance Activity

{governance_data}
"""

PRIVATE_REPORT_PROMPT = """\
You are generating a Private Mirror for governor "{governor_id}" in Pinwheel Fates.

A private mirror reflects a governor's OWN behavior back to them. Only they see this.
It helps them understand their patterns and blind spots without telling them what to do.

## Rules
1. You DESCRIBE their behavior patterns. You never PRESCRIBE actions.
2. Write 2-3 paragraphs. Be specific to THIS governor's actions and context.
3. Compare their focus to league-wide patterns:
   - What categories are they proposing changes for vs. what others are focused on?
   - Are they concentrated in one area while missing another?
4. Surface blind spots:
   - "You haven't proposed anything about [category] despite it being the most-changed area."
   - "Your proposals focus on offense, but the league's biggest shifts have been in defense."
5. Show their voting record relative to outcomes:
   - "You voted yes on 3 rules that passed — scoring has risen 15% since."
   - "You've opposed every defensive rule change — all passed anyway."
6. Note their engagement trajectory:
   - Are they increasing participation, stable, or fading?
   - Frame activity relative to opportunity: "You voted on 2 of 5 proposals — selective engagement."
7. Never compare them to other specific governors by name. Reflect, don't rank.
8. If they haven't been active, contextualize what they missed — but without judgment.
9. Early-season awareness: In Rounds 1-2, there is very little data to reflect on. \
If the governor has done nothing yet, say so briefly — do not pad with generic advice \
or claim patterns from a single action.

## Governor Activity

{governor_data}
"""


# --- Variant B prompts for A/B comparison (M.2) ---

SIMULATION_REPORT_PROMPT_B = """\
You are a keen-eyed sports analyst for Pinwheel Fates, a 3v3 basketball governance game.

Reflect on this round's results. Focus on what the numbers reveal about the current meta.

## Constraints
1. OBSERVE only. Never recommend, suggest, or advise.
2. Be terse — one paragraph. Data-driven.
3. If Elam triggered, note the score dynamics it created.
4. Mention specific teams and agents by name when relevant.

## Round Data

{round_data}
"""

GOVERNANCE_REPORT_PROMPT_B = """\
You are a governance analyst for Pinwheel Fates, a 3v3 basketball governance game.

Analyze this round's governance activity. Focus on coalition dynamics and power shifts.

## Constraints
1. OBSERVE only. Never say what governors "should" do.
2. One paragraph. Be precise about vote counts and proposal patterns.
3. If consensus formed, note what that reveals. If it fractured, note the fault lines.

## Governance Activity

{governance_data}
"""

PRIVATE_REPORT_PROMPT_B = """\
You are writing a behavioral snapshot for governor "{governor_id}" in Pinwheel Fates.

This is private — only they see it. Show them their pattern.

## Constraints
1. DESCRIBE only. Zero advice.
2. One paragraph. Specific to their actions.
3. Note: frequency, consistency, token economy, risk appetite.
4. Never mention other governors by name.

## Governor Activity

{governor_data}
"""


def build_system_context(
    round_data: dict,
    narrative: NarrativeContext | None,
) -> dict[str, object]:
    """Compute system-level context for the simulation report.

    Produces before/after comparisons, league-wide stats, rule correlation
    data, and competitive balance metrics that the AI prompt or mock report
    can use to surface invisible patterns.

    Args:
        round_data: Dict with ``games`` list and optional ``rule_changes``.
        narrative: Optional NarrativeContext with standings, streaks, rules.

    Returns:
        Dict with keys: ``round_avg_total``, ``round_avg_margin``,
        ``all_games_close`` (margin <= 5 for every game),
        ``all_games_blowout`` (margin >= 15 for every game),
        ``standings_gap`` (wins diff between first and last),
        ``recent_rule_changes`` (list of {parameter, old_value, new_value, round_enacted}),
        ``leader_team``, ``trailer_team``, ``streaks_summary`` (list of
        {team, streak} for streaks >= 3 in absolute value).
    """
    games = round_data.get("games", [])
    ctx: dict[str, object] = {}

    if not games:
        return ctx

    # --- Per-round scoring stats ---
    totals: list[int] = []
    margins: list[int] = []
    for g in games:
        hs: int = g.get("home_score", 0)
        aws: int = g.get("away_score", 0)
        totals.append(hs + aws)
        margins.append(abs(hs - aws))

    if totals:
        ctx["round_avg_total"] = sum(totals) // len(totals)
        ctx["round_avg_margin"] = sum(margins) // len(margins)
        ctx["all_games_close"] = all(m <= 5 for m in margins)
        ctx["all_games_blowout"] = all(m >= 15 for m in margins)

    # --- Standings gap (first vs last) ---
    if narrative and narrative.standings and len(narrative.standings) >= 2:
        first = narrative.standings[0]
        last = narrative.standings[-1]
        first_wins = int(first.get("wins", 0))
        last_wins = int(last.get("wins", 0))
        ctx["standings_gap"] = first_wins - last_wins
        ctx["leader_team"] = str(first.get("team_name", ""))
        ctx["trailer_team"] = str(last.get("team_name", ""))

    # --- Recent rule changes ---
    if narrative and narrative.active_rule_changes:
        ctx["recent_rule_changes"] = [
            {
                "parameter": str(rc.get("parameter", "")),
                "old_value": rc.get("old_value"),
                "new_value": rc.get("new_value"),
                "round_enacted": rc.get("round_enacted"),
            }
            for rc in narrative.active_rule_changes
        ]

    # Also pick up rule_changes from round_data (governance results this round)
    round_rule_changes = round_data.get("rule_changes", [])
    if round_rule_changes:
        ctx["this_round_rule_changes"] = [
            {
                "parameter": rc.get("parameter", ""),
                "old_value": rc.get("old_value"),
                "new_value": rc.get("new_value"),
            }
            for rc in round_rule_changes
            if rc.get("parameter")
        ]

    # --- Streaks summary (|streak| >= 3) ---
    if narrative and narrative.streaks:
        # Build team_id -> name map from standings
        id_to_name: dict[str, str] = {}
        if narrative.standings:
            for s in narrative.standings:
                tid = str(s.get("team_id", ""))
                if tid:
                    id_to_name[tid] = str(s.get("team_name", tid))

        streaks_summary: list[dict[str, object]] = []
        for tid, streak in narrative.streaks.items():
            if abs(streak) >= 3:
                streaks_summary.append({
                    "team": id_to_name.get(tid, tid),
                    "streak": streak,
                })
        if streaks_summary:
            ctx["streaks_summary"] = streaks_summary

    return ctx


async def generate_report_with_prompt(
    prompt_template: str,
    data: dict,
    format_kwargs: dict,
    report_type: str,
    report_id_prefix: str,
    round_number: int,
    api_key: str,
    governor_id: str = "",
    season_id: str = "",
    db_session: object | None = None,
) -> Report:
    """Generate a report using a specific prompt template (for A/B testing)."""
    formatted = prompt_template.format(**format_kwargs)
    content = await _call_claude(
        system=formatted,
        user_message=f"Generate a {report_type} report for this round.",
        api_key=api_key,
        call_type=f"report.{report_type}.ab",
        season_id=season_id,
        round_number=round_number,
        db_session=db_session,
    )
    return Report(
        id=f"{report_id_prefix}-{round_number}-{uuid.uuid4().hex[:8]}",
        report_type=report_type,
        round_number=round_number,
        governor_id=governor_id,
        content=content,
    )


async def generate_simulation_report(
    round_data: dict,
    season_id: str,
    round_number: int,
    api_key: str,
    narrative: NarrativeContext | None = None,
    db_session: object | None = None,
) -> Report:
    """Generate a simulation report using Claude.

    Enriches the round data with system-level context (scoring trends,
    rule correlations, competitive balance) before passing it to the prompt.
    """
    # Compute system-level context and inject it into the round data
    sys_ctx = build_system_context(round_data, narrative)
    enriched_data = dict(round_data)
    if sys_ctx:
        enriched_data["system_context"] = sys_ctx

    data_str = json.dumps(enriched_data, indent=2)
    if narrative:
        narrative_block = format_narrative_for_prompt(narrative)
        data_str += f"\n\n--- Dramatic Context ---\n{narrative_block}"
    content = await _call_claude(
        system=SIMULATION_REPORT_PROMPT.format(round_data=data_str),
        user_message="Generate a simulation report for this round.",
        api_key=api_key,
        call_type="report.simulation",
        season_id=season_id,
        round_number=round_number,
        db_session=db_session,
    )
    return Report(
        id=f"r-sim-{round_number}-{uuid.uuid4().hex[:8]}",
        report_type="simulation",
        round_number=round_number,
        content=content,
    )


async def generate_governance_report(
    governance_data: dict,
    season_id: str,
    round_number: int,
    api_key: str,
    narrative: NarrativeContext | None = None,
    db_session: object | None = None,
) -> Report:
    """Generate a governance report using Claude.

    Enriches the governance data with computed pairwise voting alignment,
    parameter clustering, governance velocity, and blind spots before
    passing it to the prompt, so Claude can surface coalition patterns
    and system-level insights.
    """
    # Compute pairwise alignment from votes and include in the data for Claude
    enriched_data = dict(governance_data)
    votes = enriched_data.get("votes", [])
    if votes:
        alignment = compute_pairwise_alignment(votes)
        if alignment:
            enriched_data["pairwise_voting_alignment"] = alignment

    # Compute parameter clustering
    proposals = enriched_data.get("proposals", [])
    if proposals:
        clustering = compute_proposal_parameter_clustering(proposals)
        if clustering:
            enriched_data["parameter_clustering"] = clustering

    # Compute governance velocity
    velocity = compute_governance_velocity(
        current_round_proposals=len(proposals),
        current_round_votes=len(votes),
    )
    enriched_data["velocity"] = velocity

    # Compute blind spots
    rules_changed = enriched_data.get("rules_changed", [])
    blind_spots = detect_governance_blind_spots(proposals, rules_changed)
    if blind_spots:
        enriched_data["blind_spots"] = blind_spots

    data_str = json.dumps(enriched_data, indent=2)
    if narrative:
        narrative_block = format_narrative_for_prompt(narrative)
        data_str += f"\n\n--- Dramatic Context ---\n{narrative_block}"
    content = await _call_claude(
        system=GOVERNANCE_REPORT_PROMPT.format(governance_data=data_str),
        user_message="Generate a governance report for this round.",
        api_key=api_key,
        call_type="report.governance",
        season_id=season_id,
        round_number=round_number,
        db_session=db_session,
    )
    return Report(
        id=f"r-gov-{round_number}-{uuid.uuid4().hex[:8]}",
        report_type="governance",
        round_number=round_number,
        content=content,
    )


def _compute_rule_correlations(
    round_data: dict[str, object],
    narrative: NarrativeContext,
) -> list[dict[str, object]]:
    """Compute rule-change correlation data from round games.

    Returns a list of dicts with parameter, old/new values, avg total score
    this round, rounds since change, and a human-readable summary.
    Pre-computed data in round_data["rule_correlations"] takes precedence.
    """
    precomputed = round_data.get("rule_correlations")
    if isinstance(precomputed, list) and precomputed:
        return precomputed

    if not narrative.active_rule_changes:
        return []

    games = round_data.get("games", [])
    if not isinstance(games, list) or not games:
        return []

    # Compute current round average total score
    totals: list[float] = []
    for g in games:
        if isinstance(g, dict):
            hs = g.get("home_score", 0)
            aws = g.get("away_score", 0)
            if isinstance(hs, (int, float)) and isinstance(aws, (int, float)):
                totals.append(float(hs) + float(aws))
    if not totals:
        return []
    avg_total_after = sum(totals) / len(totals)

    correlations: list[dict[str, object]] = []
    for rc in narrative.active_rule_changes:
        enacted = rc.get("round_enacted")
        if not isinstance(enacted, (int, float)) or isinstance(enacted, bool):
            continue
        enacted_int = int(enacted)
        if enacted_int >= narrative.round_number:
            continue  # Future or same-round change

        param = str(rc.get("parameter", "")).replace("_", " ")
        old_val = rc.get("old_value")
        new_val = rc.get("new_value")
        rounds_since = narrative.round_number - enacted_int

        summary = (
            f"Since {param} changed to {new_val} (from {old_val}, "
            f"Round {enacted_int}): this round averaged "
            f"{avg_total_after:.0f} total points per game "
            f"({rounds_since} round{'s' if rounds_since != 1 else ''} "
            f"under new rules)"
        )
        correlations.append({
            "parameter": str(rc.get("parameter", "")),
            "old_value": old_val,
            "new_value": new_val,
            "round_enacted": enacted_int,
            "avg_total_after": round(avg_total_after, 1),
            "summary": summary,
        })

    return correlations


def _compute_rule_correlations_with_history(
    round_data: dict[str, object],
    narrative: NarrativeContext,
    avg_total_before: float,
) -> list[dict[str, object]]:
    """Compute rule correlations with pre-computed before/after comparison.

    Like _compute_rule_correlations but includes percentage change
    when historical avg_total_before is provided.
    """
    base = _compute_rule_correlations(round_data, narrative)
    if not base or avg_total_before <= 0:
        return base

    for corr in base:
        avg_after = float(corr.get("avg_total_after", 0))
        pct = round(((avg_after - avg_total_before) / avg_total_before) * 100)
        param = str(corr.get("parameter", "")).replace("_", " ")
        new_val = corr.get("new_value")
        direction = "up" if pct >= 0 else "down"
        corr["avg_total_before"] = avg_total_before
        corr["pct_change"] = abs(pct)
        corr["summary"] = (
            f"Since {param} changed to {new_val}: "
            f"scoring {direction} {abs(pct)}% "
            f"(avg {avg_total_before:.0f} -> {avg_after:.0f})"
        )

    return base


async def generate_private_report(
    governor_data: dict,
    governor_id: str,
    season_id: str,
    round_number: int,
    api_key: str,
    db_session: object | None = None,
) -> Report:
    """Generate a private report for a specific governor."""
    content = await _call_claude(
        system=PRIVATE_REPORT_PROMPT.format(
            governor_id=governor_id,
            governor_data=json.dumps(governor_data, indent=2),
        ),
        user_message=f"Generate a private report for governor {governor_id}.",
        api_key=api_key,
        call_type="report.private",
        season_id=season_id,
        round_number=round_number,
        db_session=db_session,
    )
    return Report(
        id=f"r-priv-{round_number}-{uuid.uuid4().hex[:8]}",
        report_type="private",
        round_number=round_number,
        governor_id=governor_id,
        content=content,
    )


async def _call_claude(
    system: str,
    user_message: str,
    api_key: str,
    call_type: str = "report",
    season_id: str = "",
    round_number: int | None = None,
    db_session: object | None = None,
) -> str:
    """Make a Claude API call for report generation.

    When ``db_session`` is provided, records token usage to the AI usage log.
    """
    from pinwheel.ai.usage import (
        cacheable_system,
        extract_usage,
        record_ai_usage,
        track_latency,
    )

    model = "claude-opus-4-6"
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        async with track_latency() as timing:
            response = await client.messages.create(
                model=model,
                max_tokens=1500,
                system=cacheable_system(system),
                messages=[{"role": "user", "content": user_message}],
            )
        text = response.content[0].text

        # Record usage if a DB session is available
        if db_session is not None:
            input_tok, output_tok, cache_tok, cache_create_tok = extract_usage(response)
            await record_ai_usage(
                session=db_session,
                call_type=call_type,
                model=model,
                input_tokens=input_tok,
                output_tokens=output_tok,
                cache_read_tokens=cache_tok,
                cache_creation_tokens=cache_create_tok,
                latency_ms=timing["latency_ms"],
                season_id=season_id,
                round_number=round_number,
            )

        return text
    except anthropic.APIError as e:
        logger.error("Report generation API error: %s", e)
        return f"[Report generation failed: {e}]"


# --- Mock implementations for testing ---


def generate_simulation_report_mock(
    round_data: dict,
    season_id: str,
    round_number: int,
    narrative: NarrativeContext | None = None,
) -> Report:
    """Mock simulation report — follows The Pinwheel Post editorial prompt.

    Implements the lede hierarchy:
    1. Champion crowned
    2. Team eliminated/swept
    3. Upset (standings-aware)
    4. Streak lives or dies
    5. Blowout or classic
    6. Standings shifted
    7. Rules changed
    """

    games = round_data.get("games", [])
    if not games:
        return Report(
            id=f"r-sim-{round_number}-mock",
            report_type="simulation",
            round_number=round_number,
            content="Silence from the courts. No games this round.",
        )

    phase = narrative.phase if narrative else "regular"
    phase_label = ""
    if phase in ("finals", "championship"):
        phase_label = "championship"
    elif phase == "semifinal":
        phase_label = "semifinal"
    is_playoff = bool(phase_label)

    # --- Parse and classify every game ---
    entries: list[dict[str, object]] = []
    for g in games:
        home = g.get("home_team", "Home")
        away = g.get("away_team", "Away")
        hs: int = g.get("home_score", 0)
        aws: int = g.get("away_score", 0)
        margin = abs(hs - aws)
        winner = home if hs > aws else away
        loser = away if hs > aws else home
        w_score, l_score = max(hs, aws), min(hs, aws)
        winner_id: str = g.get("winner_team_id", "")
        home_id: str = g.get("home_team_id", "")
        away_id: str = g.get("away_team_id", "")
        loser_id = away_id if winner_id == home_id else home_id
        entries.append({
            "winner": winner, "loser": loser,
            "w_score": w_score, "l_score": l_score,
            "margin": margin, "total": hs + aws,
            "winner_id": winner_id, "loser_id": loser_id,
            "home_id": home_id, "away_id": away_id,
        })

    # Collect team IDs and names
    played_ids: set[str] = set()
    team_id_to_name: dict[str, str] = {}
    for e in entries:
        for fld in ("home_id", "away_id"):
            tid = str(e[fld])
            if tid:
                played_ids.add(tid)
        wid, lid = str(e["winner_id"]), str(e["loser_id"])
        if wid:
            team_id_to_name[wid] = str(e["winner"])
        if lid:
            team_id_to_name[lid] = str(e["loser"])

    # Build standings lookup (rank by team_id)
    standings_by_team: dict[str, dict[str, object]] = {}
    if narrative and narrative.standings:
        for s in narrative.standings:
            tid = str(s.get("team_id", ""))
            if tid:
                standings_by_team[tid] = s

    # --- LEDE HIERARCHY ---
    lede: str = ""
    lede_type: str = ""
    supporting: list[str] = []

    # 1. Champion crowned
    if (
        narrative and
        narrative.season_arc == "championship" and
        phase in ("finals", "championship") and
        entries
    ):
        # Find the champion from the games (winner in finals phase)
            champion = str(entries[0]["winner"])
            lede = f"{champion} are your champions."
            lede_type = "championship"

    # 2. Team eliminated/swept (playoff only)
    if not lede and is_playoff and narrative and narrative.streaks:
        for team_id, streak in narrative.streaks.items():
            if team_id in played_ids and streak <= -3:
                team_name = team_id_to_name.get(team_id, team_id)
                if abs(streak) >= 3:
                    lede = (
                        f"{team_name} were swept — {abs(streak)} straight "
                        f"losses end their season in the {phase_label}."
                    )
                    lede_type = "elimination"
                    break

    # 3. Upset (standings-aware)
    if not lede and narrative and narrative.standings and len(entries) > 0:
        for e in entries:
            winner_id = str(e["winner_id"])
            loser_id = str(e["loser_id"])
            winner_rank = standings_by_team.get(winner_id, {}).get("rank", 99)
            loser_rank = standings_by_team.get(loser_id, {}).get("rank", 99)

            # Upset = lower-ranked team beats higher-ranked team by 2+ positions
            if (
                isinstance(winner_rank, int) and
                isinstance(loser_rank, int) and
                winner_rank - loser_rank >= 2
            ):
                    w = str(e["winner"])
                    lo = str(e["loser"])
                    ws = int(e["w_score"])  # type: ignore[arg-type]
                    ls = int(e["l_score"])  # type: ignore[arg-type]
                    lede = f"{w} shocked {lo} {ws}-{ls}. The standings didn't predict this one."
                    lede_type = "upset"
                    break

    # 4. Streak lives or dies (5+ games)
    if not lede and narrative and narrative.streaks:
        for team_id, streak in narrative.streaks.items():
            if team_id in played_ids and abs(streak) >= 5:
                team_name = team_id_to_name.get(team_id, team_id)
                won = any(str(e["winner_id"]) == team_id for e in entries)
                if streak > 0 and won:
                    lede = f"{team_name} extended their {streak}-game win streak."
                    lede_type = "streak"
                    break
                elif streak < 0 and not won:
                    lede = f"{team_name} have now lost {abs(streak)} straight."
                    lede_type = "streak"
                    break

    # 5. Blowout or classic
    if not lede and entries:
        biggest_blowout = max(entries, key=lambda e: int(e["margin"]))  # type: ignore[arg-type]
        closest_game = min(entries, key=lambda e: int(e["margin"]))  # type: ignore[arg-type]

        if int(biggest_blowout["margin"]) >= 15:  # type: ignore[arg-type]
            w = str(biggest_blowout["winner"])
            lo = str(biggest_blowout["loser"])
            ws = int(biggest_blowout["w_score"])  # type: ignore[arg-type]
            ls = int(biggest_blowout["l_score"])  # type: ignore[arg-type]
            m = int(biggest_blowout["margin"])  # type: ignore[arg-type]
            lede = f"{w} demolished {lo} {ws}-{ls}. The {m}-point margin speaks for itself."
            lede_type = "blowout"
        elif int(closest_game["margin"]) <= 3:  # type: ignore[arg-type]
            w = str(closest_game["winner"])
            lo = str(closest_game["loser"])
            ws = int(closest_game["w_score"])  # type: ignore[arg-type]
            ls = int(closest_game["l_score"])  # type: ignore[arg-type]
            m = int(closest_game["margin"])  # type: ignore[arg-type]
            lede = f"{w} survived {lo} {ws}-{ls} in a thriller — just {m} points separated them."
            lede_type = "classic"

    # 6. Standings shifted (check for rank swaps)
    # (We don't have pre-round standings in mock, so skip this for now)

    # 7. Rules changed
    rule_changes = round_data.get("rule_changes", [])
    if not lede and rule_changes:
        change_notes = [
            rc["parameter"].replace("_", " ")
            for rc in rule_changes
            if rc.get("parameter")
        ]
        if change_notes:
            lede = (
                f"The rules changed. First games under new parameters: "
                f"{', '.join(change_notes)} adjusted."
            )
            lede_type = "rules"

    # Default lede if nothing else hits
    if not lede:
        first_game = entries[0]
        w = str(first_game["winner"])
        lo = str(first_game["loser"])
        ws = int(first_game["w_score"])  # type: ignore[arg-type]
        ls = int(first_game["l_score"])  # type: ignore[arg-type]
        if phase_label:
            lede = f"The {phase_label} continued. {w} beat {lo} {ws}-{ls}."
        else:
            lede = f"Round {round_number}. {w} beat {lo} {ws}-{ls}."
        lede_type = "default"

    # --- SUPPORTING DETAILS ---
    # Include other significant games not covered by the lede
    for e in entries:
        w = str(e["winner"])
        lo = str(e["loser"])
        ws = int(e["w_score"])  # type: ignore[arg-type]
        ls = int(e["l_score"])  # type: ignore[arg-type]
        m = int(e["margin"])  # type: ignore[arg-type]

        # Skip the game that became the lede
        if lede_type in ("blowout", "classic", "upset") and (
            (lede_type == "blowout" and m >= 15 and w in lede) or
            (lede_type == "classic" and m <= 3 and w in lede) or
            (lede_type == "upset" and w in lede)
        ):
            continue

        # Add other notable games
        if phase_label:
            supporting.append(f"{w} beat {lo} {ws}-{ls} in the {phase_label}.")
        elif m >= 10:
            supporting.append(f"{w} rolled past {lo} {ws}-{ls}.")
        elif m <= 4:
            supporting.append(f"{w} edged {lo} {ws}-{ls}.")
        else:
            supporting.append(f"{w} beat {lo} {ws}-{ls}.")

    # --- WHAT CHANGED (system-level patterns) ---
    sys_ctx = build_system_context(round_data, narrative)
    what_changed_lines: list[str] = []

    # Scoring trend — extreme averages are the story
    round_avg = int(sys_ctx.get("round_avg_total", 0))
    if round_avg >= 80:
        what_changed_lines.append(
            f"Scoring surged to {round_avg} per game across the slate."
        )
    elif 0 < round_avg <= 35:
        what_changed_lines.append(
            f"Defense dominated — just {round_avg} points per game."
        )

    # Margin compression — tight or lopsided slate
    round_avg_margin = int(sys_ctx.get("round_avg_margin", 99))
    if sys_ctx.get("all_games_close") and len(entries) > 1:
        what_changed_lines.append(
            f"Every game was decided by 5 or fewer — "
            f"average margin just {round_avg_margin} points."
        )
    elif sys_ctx.get("all_games_blowout") and len(entries) > 1:
        what_changed_lines.append(
            f"No contest was close — every game was decided by "
            f"{round_avg_margin}+ points."
        )
    elif round_avg_margin <= 5 and len(entries) > 1:
        what_changed_lines.append(
            f"The average margin was just {round_avg_margin} points."
        )

    # Competitive balance — standings gap
    standings_gap = sys_ctx.get("standings_gap")
    leader = str(sys_ctx.get("leader_team", ""))
    trailer = str(sys_ctx.get("trailer_team", ""))
    if isinstance(standings_gap, int) and standings_gap > 0 and leader and trailer:
        if standings_gap <= 2:
            what_changed_lines.append(
                f"Just {standings_gap} game{'s' if standings_gap != 1 else ''} "
                f"separate {leader} in first from {trailer} in last — "
                f"the league is compressed."
            )
        elif standings_gap >= 6:
            what_changed_lines.append(
                f"{leader} lead the league by {standings_gap} games over "
                f"{trailer} — the gap is widening."
            )

    # Streaks context — only if nothing better surfaced
    if not what_changed_lines and narrative and narrative.streaks:
        active_streaks = [
            (team_id_to_name.get(tid, tid), s)
            for tid, s in narrative.streaks.items()
            if tid in played_ids and abs(s) >= 3
        ]
        if active_streaks:
            streak_team, streak_val = active_streaks[0]
            if streak_val > 0:
                what_changed_lines.append(
                    f"{streak_team} are riding a {streak_val}-game win streak."
                )
            else:
                what_changed_lines.append(
                    f"{streak_team} have lost {abs(streak_val)} straight."
                )

    # --- RULE CORRELATION DATA ---
    rule_correlation_lines: list[str] = []
    if narrative and narrative.active_rule_changes and entries:
        correlations = _compute_rule_correlations(round_data, narrative)
        for corr in correlations:
            rule_correlation_lines.append(str(corr["summary"]))

    # --- HOT PLAYERS ---
    hot_player_lines: list[str] = []
    if narrative and narrative.hot_players:
        for hp in narrative.hot_players[:2]:
            hp_name = hp.get("name", "?")
            hp_team = hp.get("team_name", "?")
            hp_pts = hp.get("value", 0)

            # Find their game
            player_game: dict[str, object] | None = None
            for e in entries:
                if hp_team in (e["winner"], e["loser"]):
                    player_game = e
                    break

            if player_game:
                if hp_team == player_game["winner"]:
                    hot_player_lines.append(
                        f"{hp_name} poured in {hp_pts} to lead {hp_team}'s win."
                    )
                else:
                    hot_player_lines.append(
                        f"{hp_name} scored {hp_pts} for {hp_team} in a losing effort."
                    )

    # --- WHAT THE ROUND REVEALS (closing — system-level pattern) ---
    closing: str = ""
    if narrative:
        # Rule correlation: governance is showing up in the numbers
        if rule_correlation_lines:
            closing = (
                "The data is starting to speak — governance decisions "
                "are showing up in the box scores."
            )
        # Competitive balance: league is compressed
        elif (
            isinstance(standings_gap, int)
            and standings_gap <= 2
            and leader
            and trailer
        ):
            closing = (
                "The league is as tight as it has ever been. "
                "One bad round changes the standings entirely."
            )
        # Phase: late season or playoffs coming into focus
        elif phase in ("semifinals", "finals") and not is_playoff:
            closing = "Playoff seeding is coming into focus."
        elif narrative.season_arc == "late" and narrative.total_rounds > 0:
            closing = (
                f"Round {narrative.round_number} of {narrative.total_rounds}. "
                f"The regular season is winding down — "
                f"every result carries playoff weight now."
            )
        # Governance pendency: the game may change next round
        elif narrative.pending_proposals > 0:
            plural = "s" if narrative.pending_proposals != 1 else ""
            verb = "awaits" if narrative.pending_proposals == 1 else "await"
            closing = (
                f"{narrative.pending_proposals} proposal{plural} {verb} "
                f"the governors' vote — the game these teams are playing "
                f"may not be the same game next round."
            )

    # --- COMPOSE THE REPORT ---
    lines = [lede]

    # Supporting games (max 2)
    lines.extend(supporting[:2])

    # System-level "what changed" observations (max 2)
    lines.extend(what_changed_lines[:2])

    # Rule correlation data
    lines.extend(rule_correlation_lines)

    # Hot players
    lines.extend(hot_player_lines)

    # Closing — what the round reveals
    if closing:
        lines.append(closing)

    return Report(
        id=f"r-sim-{round_number}-mock",
        report_type="simulation",
        round_number=round_number,
        content=" ".join(lines),
    )



def generate_governance_report_mock(
    governance_data: dict,
    season_id: str,
    round_number: int,
    narrative: NarrativeContext | None = None,
) -> Report:
    """Mock governance report -- surfaces coalitions, patterns, velocity, blind spots."""
    proposals = governance_data.get("proposals", [])
    votes = governance_data.get("votes", [])
    rules_changed = governance_data.get("rules_changed", [])
    proposal_history: list[dict[str, str | int | float | None]] = (
        governance_data.get("proposal_history", [])
    )
    season_proposals_by_round: dict[int, int] | None = governance_data.get(
        "season_proposals_by_round"
    )
    season_votes_by_round: dict[int, int] | None = governance_data.get(
        "season_votes_by_round"
    )

    lines: list[str] = []

    # Playoff phase opener -- governance during playoffs carries different weight
    if narrative and narrative.phase in ("semifinal", "finals", "championship"):
        if narrative.phase == "finals":
            lines.append(
                "CHAMPIONSHIP GOVERNANCE. With the finals underway, "
                "every rule decision now shapes how the title is won."
            )
        elif narrative.phase == "semifinal":
            lines.append(
                "PLAYOFF GOVERNANCE — the stakes are higher. "
                "Rule changes enacted now land on elimination games."
            )

    # Governance velocity (system-level insight)
    velocity = compute_governance_velocity(
        current_round_proposals=len(proposals),
        current_round_votes=len(votes),
        season_proposals_by_round=season_proposals_by_round,
        season_votes_by_round=season_votes_by_round,
    )
    velocity_label = str(velocity["velocity_label"])

    # Proposal activity -- count + velocity analysis
    if proposals:
        velocity_qualifier = ""
        if velocity_label == "peak":
            velocity_qualifier = (
                " This is the most active governance window of the season."
            )
        elif velocity_label == "high":
            velocity_qualifier = " Governance activity is running above average."
        elif velocity_label == "low":
            velocity_qualifier = " Activity is below the season average."

        lines.append(
            f"Round {round_number} saw {len(proposals)} proposal(s) "
            f"enter the governance arena.{velocity_qualifier}"
        )

        # Parameter clustering (system-level insight)
        clustering = compute_proposal_parameter_clustering(
            proposals, history=proposal_history or None
        )
        for cluster in clustering:
            cat = str(cluster["category"])
            count = int(cluster["count"])
            hist_count = int(cluster["historical_count"])
            if count > 1:
                cat_label = cat.replace("_", " ")
                if hist_count > 0:
                    total = count + hist_count
                    lines.append(
                        f"{count} proposals targeted {cat_label} parameters "
                        f"this round, {total} total this season -- "
                        "the Floor is fixated on this dimension of the game."
                    )
                else:
                    lines.append(
                        f"{count} proposals targeted {cat_label} parameters -- "
                        "the Floor is focused on this dimension of the game."
                    )
                break  # Only note the top cluster
    else:
        if velocity_label == "silent" and season_proposals_by_round:
            lines.append(
                f"Round {round_number} was silent on the governance front "
                "-- no proposals filed. The Floor has gone quiet."
            )
        else:
            lines.append(
                f"Round {round_number} was quiet on the governance front "
                "-- no proposals filed."
            )

    # Voting analysis — add alignment patterns
    if votes:
        yes_count = sum(1 for v in votes if v.get("vote") == "yes")
        no_count = sum(1 for v in votes if v.get("vote") == "no")
        lines.append(
            f"Governors cast {len(votes)} votes "
            f"({yes_count} yes, {no_count} no)."
        )
        # Detect voting coalitions
        if yes_count == len(votes):
            lines.append("The vote was unanimous — consensus is forming.")
        elif no_count == len(votes):
            lines.append("The vote was unanimously against — the Floor is aligned in resistance.")
        elif yes_count > 0 and no_count > 0:
            lines.append("The Floor was split on this decision — voting coalitions are emerging.")

        # Pairwise coalition detection (system-level insight)
        alignment = compute_pairwise_alignment(votes)
        for pair in alignment:
            shared = int(pair["shared_proposals"])
            pct = float(pair["agreement_pct"])
            if pct >= 80.0 and shared >= 3:
                gov_a = str(pair["governor_a"])
                gov_b = str(pair["governor_b"])
                agreed = int(pair["agreed"])
                lines.append(
                    f"Governors {gov_a} and {gov_b} have voted together "
                    f"on {agreed} of {shared} proposals "
                    f"({pct:.0f}% agreement)."
                )

        # Detect isolated governors (no agreement > 50% with anyone)
        if len(alignment) > 1:
            all_govs_in_votes: set[str] = set()
            for v in votes:
                gid = v.get("governor_id", "")
                if gid:
                    all_govs_in_votes.add(gid)
            for gid in sorted(all_govs_in_votes):
                max_agr = 0.0
                for pair in alignment:
                    if gid in (pair["governor_a"], pair["governor_b"]):
                        max_agr = max(max_agr, float(pair["agreement_pct"]))
                if max_agr <= 50.0 and len(all_govs_in_votes) >= 3:
                    lines.append(
                        f"Governor {gid} stands alone -- "
                        "no voting alignment above 50% with any other governor."
                    )
                    break  # Only note the most isolated

    # Rule changes -- connect to game impact
    if rules_changed:
        lines.append(f"{len(rules_changed)} rule(s) changed this round:")
        for rc in rules_changed:
            param = rc.get("parameter", "unknown")
            old_val = rc.get("old_value", "?")
            new_val = rc.get("new_value", "?")
            if param != "unknown" and old_val != "?" and new_val != "?":
                param_label = param.replace("_", " ").title()
                lines.append(
                    f"  {param_label} moved from {old_val} to {new_val}."
                )
                # Add expected gameplay impact
                if "three_point" in param.lower():
                    if new_val > old_val:  # type: ignore[operator]
                        lines.append("    Perimeter shooting is now more valuable.")
                    else:
                        lines.append("    Inside scoring gains relative value.")
                elif "elam" in param.lower():
                    lines.append("    Endgame dynamics will shift.")
                elif "steal" in param.lower() or "defense" in param.lower():
                    lines.append("    Defensive intensity should change accordingly.")
            else:
                lines.append(
                    f"  A rule was changed "
                    f"(proposal {rc.get('proposal_id', '?')})."
                )
        lines.append("The next round plays under these new conditions.")

    # Blind spots (system-level insight)
    blind_spots = detect_governance_blind_spots(
        proposals + (proposal_history or []),
        rules_changed,
    )
    if blind_spots and len(blind_spots) <= 5:
        spot_labels = [s.replace("_", " ") for s in blind_spots[:3]]
        lines.append(
            f"Untouched by governance: {', '.join(spot_labels)}. "
            "No proposals have targeted these game dimensions."
        )

    # Narrative context enrichment
    if narrative:
        if narrative.pending_proposals > 0 and not proposals:
            lines.append(
                f"{narrative.pending_proposals} proposal(s) remain "
                "pending from prior rounds."
            )
        if (
            narrative.next_tally_round is not None
            and not narrative.governance_window_open
        ):
            lines.append(
                f"Next governance tally: Round {narrative.next_tally_round}."
            )

    # "What the Floor is building" closing — governance trajectory
    if rules_changed:
        params_changed = [rc.get("parameter", "") for rc in rules_changed]
        if any("three_point" in p for p in params_changed):
            lines.append(
                "The Floor is reshaping the offensive meta — "
                "the next round will reveal what the new parameters unlock."
            )
        elif any("elam" in p for p in params_changed):
            lines.append(
                "The Floor is tuning endgame mechanics — "
                "close games will play differently from here forward."
            )
        else:
            lines.append(
                "The Floor is experimenting with the game's foundational parameters — "
                "governance is active and the ruleset is evolving."
            )
    elif proposals and not rules_changed:
        lines.append(
            "Proposals were submitted but not yet enacted — "
            "the next governance window will show whether they gain traction."
        )

    return Report(
        id=f"r-gov-{round_number}-mock",
        report_type="governance",
        round_number=round_number,
        content=" ".join(lines) if lines else "Governance was silent this round.",
    )


def generate_private_report_mock(
    governor_data: dict,
    governor_id: str,
    season_id: str,
    round_number: int,
) -> Report:
    """Mock private report — shows governor behavior relative to the system.

    When ``governor_data`` contains enriched context from
    ``compute_private_report_context`` (governor_proposal_categories,
    league_rule_change_categories, blind_spots, voting_outcomes,
    alignment_rate, swing_votes), the mock uses real data for blind-spot
    surfacing and voting-outcome analysis.  Falls back to randomized
    context when enriched data is absent (backward compatibility).
    """
    proposals = governor_data.get("proposals_submitted", 0)
    votes = governor_data.get("votes_cast", 0)
    total_league = governor_data.get("total_league_proposals", 0)

    # Enriched data (present when compute_private_report_context ran)
    gov_categories: dict[str, int] = governor_data.get(
        "governor_proposal_categories", {}
    )
    league_rule_cats: dict[str, int] = governor_data.get(
        "league_rule_change_categories", {}
    )
    blind_spots: list[str] = governor_data.get("blind_spots", [])
    voting_outcomes: list[dict[str, str]] = governor_data.get(
        "voting_outcomes", []
    )
    alignment_rate: float = governor_data.get("alignment_rate", 0.0)
    swing_votes: int = governor_data.get("swing_votes", 0)

    lines: list[str] = []

    # --- Activity summary with context ---
    if proposals == 0 and votes == 0:
        if total_league > 0:
            top_league_cat = (
                next(iter(league_rule_cats)) if league_rule_cats else "unknown"
            )
            lines.append(
                f"You were quiet this round. "
                f"The Floor saw {total_league} "
                f"proposals debated — most changes landed in {top_league_cat}. "
                "Your absence is noted, not judged."
            )
        else:
            lines.append(
                "You were quiet this round. "
                "The Floor was quiet too — no proposals filed. "
                "Your absence is noted, not judged."
            )
    else:
        total_activity = proposals + votes
        if total_activity <= 2:
            activity_level = "light"
        elif total_activity <= 4:
            activity_level = "active"
        else:
            activity_level = "busy"

        lines.append(f"This was a {activity_level} round for you. ")

        if proposals > 0:
            if proposals >= 2:
                activity_descriptor = "one of the more active governors"
            else:
                activity_descriptor = "contributing to the debate"
            plural = "s" if proposals != 1 else ""
            lines[-1] += (
                f"You submitted {proposals} proposal{plural} — "
                f"{activity_descriptor}. "
            )

        if votes > 0:
            vote_denominator = total_league if total_league > 0 else votes
            vote_context = (
                "selective" if votes < vote_denominator // 2 else "engaged"
            )
            plural = "s" if votes != 1 else ""
            lines[-1] += (
                f"You cast {votes} vote{plural} out of "
                f"{vote_denominator} proposals — "
                f"{vote_context} participation."
            )

    # --- Blind spot surfacing (real data when available) ---
    if gov_categories and league_rule_cats:
        governor_focus = next(iter(gov_categories), "")
        if blind_spots:
            top_blind = blind_spots[0]
            if governor_focus:
                lines.append(
                    f"Your proposals have focused on {governor_focus}. "
                    f"Meanwhile, the league\'s biggest shifts have been "
                    f"in {top_blind} — an area you haven\'t addressed yet."
                )
            else:
                lines.append(
                    f"You voted but didn\'t propose. "
                    f"The league\'s most-changed area is {top_blind} — "
                    f"a dimension where your voice hasn\'t shaped the agenda."
                )
        elif governor_focus and league_rule_cats:
            top_league_cat = next(iter(league_rule_cats))
            if governor_focus == top_league_cat:
                lines.append(
                    f"Your proposals target {governor_focus} — "
                    "the same area the league has been changing most. "
                    "You\'re in the current."
                )
            else:
                lines.append(
                    f"Your proposals target {governor_focus}. "
                    f"The league has focused more on {top_league_cat}."
                )
    elif proposals > 0:
        import random as _rng

        rng = _rng.Random(hash((governor_id, round_number)))
        areas = ["offense", "defense", "pace", "endgame"]
        gov_focus = rng.choice(areas)
        league_focus = rng.choice([a for a in areas if a != gov_focus])
        lines.append(
            f"Your proposals have focused on {gov_focus}. "
            f"Meanwhile, the league has seen more changes in {league_focus} — "
            f"an area you haven\'t addressed yet."
        )
    elif votes > 0 and not blind_spots:
        import random as _rng

        rng = _rng.Random(hash((governor_id, round_number)))
        areas = ["offense", "defense", "pace", "endgame"]
        league_focus = rng.choice(areas)
        lines.append(
            f"You voted but didn\'t propose. "
            f"The league\'s biggest debates centered on {league_focus} — "
            f"an area where your voice hasn\'t shaped the agenda."
        )

    # --- Voting outcomes relative to results ---
    if voting_outcomes:
        yes_passed = sum(
            1
            for vo in voting_outcomes
            if vo.get("vote") == "yes" and vo.get("outcome") == "passed"
        )
        no_passed = sum(
            1
            for vo in voting_outcomes
            if vo.get("vote") == "no" and vo.get("outcome") == "passed"
        )

        if yes_passed > 0:
            lines.append(
                f"You voted yes on {yes_passed} rule(s) that passed — "
                "those changes are now shaping the game."
            )
        if no_passed > 0:
            lines.append(
                f"You opposed {no_passed} rule(s) that passed anyway."
            )
        if alignment_rate > 0:
            lines.append(
                f"Your votes aligned with outcomes "
                f"{alignment_rate:.0%} of the time."
            )

    # --- Swing vote power ---
    if swing_votes > 0:
        lines.append(
            f"You were the swing vote on {swing_votes} "
            f"proposal{'s' if swing_votes != 1 else ''} — "
            "your vote alone determined the outcome."
        )

    # --- Engagement trajectory (fallback for un-enriched data) ---
    if not voting_outcomes and (proposals + votes > 0):
        import random as _rng

        rng = _rng.Random(hash((governor_id, round_number)))
        trajectory = rng.choice(["increasing", "steady", "declining"])
        if trajectory == "increasing":
            lines.append(
                "Your participation is trending up — "
                "you\'re more engaged than in earlier rounds."
            )
        elif trajectory == "steady":
            lines.append(
                "Your engagement has been consistent across the season."
            )
        else:
            lines.append(
                "Your activity has tapered off from earlier rounds. "
                "The system continues to evolve without you."
            )

    content = " ".join(lines)

    return Report(
        id=f"r-priv-{round_number}-{governor_id[:8]}-mock",
        report_type="private",
        round_number=round_number,
        governor_id=governor_id,
        content=content,
    )


# --- Series Report Generation ---

SERIES_REPORT_PROMPT = """\
You are the Sports Chronicler for Pinwheel Fates, a 3v3 basketball governance game.

Write a 2-3 paragraph recap of a completed playoff series. Cover the full arc:
how the series opened, the turning point, and the clinching game.

## Rules
1. You DESCRIBE. You never PRESCRIBE.
2. Write in vivid sports journalism style — this is the record of the series.
3. Reference team names, game-by-game scores, and the series record.
4. Note momentum shifts, dominant performances, and close calls.
5. Build to the decisive moment of the clinching game.

## Series Data

{series_data}
"""


async def generate_series_report(
    series_data: dict,
    season_id: str,
    api_key: str,
    db_session: object | None = None,
) -> Report:
    """Generate an AI-powered recap of a completed playoff series.

    Args:
        series_data: Dict with team names, game-by-game scores, series record,
            series type (semifinal/finals), winner/loser info.
        season_id: Season ID for usage tracking.
        api_key: Anthropic API key.
        db_session: Optional DB session for usage logging.

    Returns:
        A Report with report_type="series".
    """
    data_str = json.dumps(series_data, indent=2)
    content = await _call_claude(
        system=SERIES_REPORT_PROMPT.format(series_data=data_str),
        user_message="Write a recap of this completed playoff series.",
        api_key=api_key,
        call_type="report.series",
        season_id=season_id,
        db_session=db_session,
    )
    return Report(
        id=f"r-series-{series_data.get('series_type', 'playoff')}-{uuid.uuid4().hex[:8]}",
        report_type="series",
        round_number=0,
        content=content,
    )


def generate_series_report_mock(series_data: dict) -> Report:
    """Generate a mock series recap for testing.

    Args:
        series_data: Dict with team names, game-by-game scores, series record,
            series type, winner/loser info.

    Returns:
        A Report with report_type="series" and deterministic content.
    """
    winner = series_data.get("winner_name", "Winner")
    loser = series_data.get("loser_name", "Loser")
    record = series_data.get("record", "?-?")
    series_type = series_data.get("series_type", "playoff")
    games = series_data.get("games", [])

    lines: list[str] = []

    if series_type == "finals":
        lines.append(
            f"The championship finals are over. {winner} claimed the title "
            f"with a {record} series victory over {loser}."
        )
    else:
        lines.append(
            f"{winner} advanced past {loser} in a {record} semifinal series."
        )

    if games:
        last_game = games[-1]
        lines.append(
            f"The clinching game ended {last_game.get('home_score', 0)}-"
            f"{last_game.get('away_score', 0)}. "
            f"From the opening tip of Game 1 to the final buzzer, "
            f"this series delivered."
        )

    return Report(
        id=f"r-series-{series_type}-mock",
        report_type="series",
        round_number=0,
        content=" ".join(lines),
    )


# --- Season Memorial Generation ---

SEASON_NARRATIVE_PROMPT = """\
You are the chronicler of Pinwheel Fates, a 3v3 basketball governance game.

Write the definitive season narrative: 3-5 paragraphs covering the full arc from
opening round to final whistle. This is the almanac entry for this season.

## Rules
1. You DESCRIBE. You never PRESCRIBE.
2. Write in the style of a sports almanac — vivid, authoritative, specific.
3. Reference specific teams, hoopers, and rule changes by name.
4. Note turning points: when the standings shifted, when a rule change reshaped
   the meta, when a streak defined a team's season.
5. Build to the playoffs and championship as a dramatic conclusion.

## Season Data

{season_data}
"""

CHAMPIONSHIP_RECAP_PROMPT = """\
You are the chronicler of Pinwheel Fates, a 3v3 basketball governance game.

Write a detailed championship recap: the playoff bracket, semifinal drama,
and the championship finals. 2-3 paragraphs of vivid sports writing.

## Rules
1. You DESCRIBE. You never PRESCRIBE.
2. Cover each playoff round — who won, the score, the momentum shifts.
3. If the Elam Ending activated, describe how it shaped the outcome.
4. Build to the championship moment — the final basket, the winning team's
   reaction, the season's capstone.

## Playoff Data

{playoff_data}
"""

CHAMPION_PROFILE_PROMPT = """\
You are the chronicler of Pinwheel Fates, a 3v3 basketball governance game.

Write a champion profile: the winning team's journey from regular season
through playoffs to the title. 1-2 paragraphs.

## Rules
1. You DESCRIBE. You never PRESCRIBE.
2. Highlight the team's regular season record and standout hoopers.
3. Describe their playoff path — close calls, dominant wins, the finals.
4. Note their roster's strengths and how they matched up against opponents.

## Champion Data

{champion_data}
"""

GOVERNANCE_LEGACY_PROMPT = """\
You are the chronicler of Pinwheel Fates, a 3v3 basketball governance game.

Write the governance legacy section: how the rules evolved during this season,
who drove changes, and what the governance record reveals about the community.
2-3 paragraphs.

## Rules
1. You DESCRIBE. You never PRESCRIBE.
2. Note which rules changed, who proposed them, and whether they passed or failed.
3. Identify patterns: were governors bold or conservative? Did consensus form?
4. Reflect on how rule changes affected gameplay outcomes.

## Governance Data

{governance_data}
"""


async def generate_season_memorial(
    memorial_data: dict,
    season_id: str,
    api_key: str,
    db_session: object | None = None,
) -> dict:
    """Generate AI narrative sections for a season memorial.

    Makes 4 concurrent Claude calls for the narrative sections:
    season_narrative, championship_recap, champion_profile, governance_legacy.

    Args:
        memorial_data: Dict from gather_memorial_data() with computed sections.
        season_id: Season being memorialized.
        api_key: Anthropic API key.
        db_session: Optional DB session for usage logging.

    Returns:
        Updated memorial_data dict with AI narratives filled in.
    """
    import asyncio

    # Prepare context for each prompt
    season_context = json.dumps(
        {
            "awards": memorial_data.get("awards", []),
            "statistical_leaders": memorial_data.get("statistical_leaders", {}),
            "key_moments": memorial_data.get("key_moments", []),
            "head_to_head": memorial_data.get("head_to_head", []),
            "rule_timeline": memorial_data.get("rule_timeline", []),
        },
        indent=2,
    )

    playoff_context = json.dumps(
        {
            "key_moments": [
                m for m in memorial_data.get("key_moments", [])
                if m.get("moment_type") == "playoff"
            ],
            "awards": memorial_data.get("awards", []),
        },
        indent=2,
    )

    champion_context = json.dumps(
        {
            "awards": [
                a for a in memorial_data.get("awards", [])
                if a.get("category") == "gameplay"
            ],
            "statistical_leaders": memorial_data.get("statistical_leaders", {}),
        },
        indent=2,
    )

    governance_context = json.dumps(
        {
            "rule_timeline": memorial_data.get("rule_timeline", []),
            "awards": [
                a for a in memorial_data.get("awards", [])
                if a.get("category") == "governance"
            ],
        },
        indent=2,
    )

    # Make 4 concurrent calls
    narrative_task = _call_claude(
        system=SEASON_NARRATIVE_PROMPT.format(season_data=season_context),
        user_message="Write the season narrative.",
        api_key=api_key,
        call_type="memorial.season_narrative",
        season_id=season_id,
        db_session=db_session,
    )
    championship_task = _call_claude(
        system=CHAMPIONSHIP_RECAP_PROMPT.format(playoff_data=playoff_context),
        user_message="Write the championship recap.",
        api_key=api_key,
        call_type="memorial.championship_recap",
        season_id=season_id,
        db_session=db_session,
    )
    champion_task = _call_claude(
        system=CHAMPION_PROFILE_PROMPT.format(champion_data=champion_context),
        user_message="Write the champion profile.",
        api_key=api_key,
        call_type="memorial.champion_profile",
        season_id=season_id,
        db_session=db_session,
    )
    governance_task = _call_claude(
        system=GOVERNANCE_LEGACY_PROMPT.format(governance_data=governance_context),
        user_message="Write the governance legacy.",
        api_key=api_key,
        call_type="memorial.governance_legacy",
        season_id=season_id,
        db_session=db_session,
    )

    results = await asyncio.gather(
        narrative_task,
        championship_task,
        champion_task,
        governance_task,
        return_exceptions=True,
    )

    # Fill in results, using empty string for any failures
    narratives = []
    for r in results:
        if isinstance(r, Exception):
            logger.error("Memorial AI call failed: %s", r)
            narratives.append("")
        else:
            narratives.append(str(r))

    memorial_data["season_narrative"] = narratives[0]
    memorial_data["championship_recap"] = narratives[1]
    memorial_data["champion_profile"] = narratives[2]
    memorial_data["governance_legacy"] = narratives[3]
    memorial_data["model_used"] = "claude-sonnet-4-5-20250929"

    return memorial_data


def generate_season_memorial_mock(memorial_data: dict) -> dict:
    """Generate mock AI narrative sections for testing.

    Fills in reasonable static content for each narrative section
    based on available computed data.

    Args:
        memorial_data: Dict from gather_memorial_data() with computed sections.

    Returns:
        Updated memorial_data dict with mock narratives filled in.
    """
    awards = memorial_data.get("awards", [])
    key_moments = memorial_data.get("key_moments", [])
    rule_timeline = memorial_data.get("rule_timeline", [])
    leaders = memorial_data.get("statistical_leaders", {})

    # Season narrative
    parts = ["Another season in the books for Pinwheel Fates."]
    if key_moments:
        closest = [m for m in key_moments if m.get("moment_type") == "closest_game"]
        if closest:
            m = closest[0]
            parts.append(
                f"The closest game of the season saw {m.get('home_team_name', '?')} "
                f"edge {m.get('away_team_name', '?')} by {m.get('margin', 0)} points."
            )
    ppg_leaders = leaders.get("ppg", [])
    if ppg_leaders:
        top = ppg_leaders[0]
        parts.append(
            f"{top['hooper_name']} ({top['team_name']}) led the league in scoring "
            f"with {top['value']} PPG across {top['games']} games."
        )
    memorial_data["season_narrative"] = " ".join(parts)

    # Championship recap
    playoff_moments = [m for m in key_moments if m.get("moment_type") == "playoff"]
    if playoff_moments:
        pm = playoff_moments[0]
        memorial_data["championship_recap"] = (
            f"The playoffs delivered. {pm.get('winner_name', '?')} "
            f"took down {pm.get('away_team_name', pm.get('home_team_name', '?'))} "
            f"{pm.get('home_score', 0)}-{pm.get('away_score', 0)} "
            f"to advance. Every possession counted under the bright lights."
        )
    else:
        memorial_data["championship_recap"] = (
            "The playoff bracket was set and the best teams battled for the title. "
            "When the final buzzer sounded, a champion was crowned."
        )

    # Champion profile
    gameplay_awards = [a for a in awards if a.get("category") == "gameplay"]
    if gameplay_awards:
        mvp = gameplay_awards[0]
        memorial_data["champion_profile"] = (
            f"The champion's run was anchored by {mvp.get('recipient_name', '?')}, "
            f"who earned {mvp.get('award', 'top honors')} with "
            f"{mvp.get('stat_value', '?')} {mvp.get('stat_label', '')}. "
            f"A roster built for the moment."
        )
    else:
        memorial_data["champion_profile"] = (
            "The champions proved their mettle across the entire season, "
            "building consistency in the regular season and peaking in the playoffs."
        )

    # Governance legacy
    if rule_timeline:
        changes = [f"{r.get('parameter', '?')}" for r in rule_timeline[:3]]
        memorial_data["governance_legacy"] = (
            f"Governors reshaped the game this season with {len(rule_timeline)} "
            f"rule change{'s' if len(rule_timeline) != 1 else ''}. "
            f"Parameters affected: {', '.join(changes)}. "
            f"The community's fingerprints are all over this ruleset."
        )
    else:
        memorial_data["governance_legacy"] = (
            "The governors held steady this season -- no rule changes were enacted. "
            "Whether by consensus or inaction, the default ruleset stood."
        )

    memorial_data["model_used"] = "mock"

    return memorial_data
