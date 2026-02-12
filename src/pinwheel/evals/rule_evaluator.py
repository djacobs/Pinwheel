"""AI Rule Evaluator (M.7) — admin-facing Opus analysis.

After each round, Opus reviews the current state and generates admin-facing
analysis. This is the "expansive" AI — where mirrors constrain themselves to
observation, the evaluator explores freely.

Runs only when ANTHROPIC_API_KEY is set. Mock version for tests.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from pinwheel.evals.models import RuleEvaluation

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository

logger = logging.getLogger(__name__)

RULE_EVALUATOR_PROMPT = """\
You are the Rule Evaluator for Pinwheel Fates, a 3v3 basketball governance game.

Unlike the Social Mirror (which DESCRIBES and never PRESCRIBES), you are the admin-facing \
analyst. You DO prescribe. Your job is to review the current state of the game and suggest \
rule experiments, identify stale parameters, and flag degenerate equilibria.

Your audience is the game admin, not the players. Be expansive. Explore freely.

## Current Ruleset
{ruleset}

## Recent Game Statistics
{game_stats}

## Governance Trends
{governance_trends}

## Active Scenario Flags
{flags}

## Parameter Staleness (rounds since last change)
{staleness}

## Your Task
Provide:
1. **Suggested Experiments**: 2-3 rule changes worth trying. Explain why.
2. **Stale Parameters**: Parameters that haven't been touched and might benefit from attention.
3. **Equilibrium Notes**: Is the current meta healthy? Any degenerate patterns?
4. **Flagged Concerns**: Anything from the scenario flags that warrants admin intervention.

Be specific. Reference actual parameter names and values.
"""


async def evaluate_rules(
    repo: Repository,
    season_id: str,
    round_number: int,
    api_key: str = "",
) -> RuleEvaluation:
    """Run the AI rule evaluator. Uses Opus for deeper reasoning."""
    if not api_key:
        return _mock_evaluation(season_id, round_number)

    # Gather context
    season = await repo.get_season(season_id)
    ruleset = season.current_ruleset if season else {}

    # Recent game stats
    all_games = await repo.get_all_game_results_for_season(season_id)
    recent_games = [g for g in all_games if g.round_number >= max(1, round_number - 3)]
    game_stats = {
        "total_games": len(recent_games),
        "avg_score": (
            sum(g.home_score + g.away_score for g in recent_games) / max(len(recent_games), 1) / 2
        ),
        "avg_possessions": (
            sum(g.total_possessions for g in recent_games) / max(len(recent_games), 1)
        ),
        "elam_rate": (
            sum(1 for g in recent_games if g.elam_target) / max(len(recent_games), 1)
        ),
    }

    # Governance trends
    gov_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted", "vote.cast", "rule.enacted"],
    )
    proposals_count = sum(1 for e in gov_events if e.event_type == "proposal.submitted")
    votes_count = sum(1 for e in gov_events if e.event_type == "vote.cast")
    enacted_count = sum(1 for e in gov_events if e.event_type == "rule.enacted")
    governance_trends = {
        "total_proposals": proposals_count,
        "total_votes": votes_count,
        "rules_enacted": enacted_count,
    }

    # Active flags
    flag_results = await repo.get_eval_results(
        season_id, eval_type="flag", round_number=round_number,
    )
    flags = [r.details_json for r in flag_results if r.details_json]

    # Parameter staleness
    staleness = _compute_staleness(gov_events, ruleset or {}, round_number)

    # Call Opus
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=api_key)
        prompt = RULE_EVALUATOR_PROMPT.format(
            ruleset=json.dumps(ruleset, indent=2),
            game_stats=json.dumps(game_stats, indent=2),
            governance_trends=json.dumps(governance_trends, indent=2),
            flags=json.dumps(flags, indent=2),
            staleness=json.dumps(staleness, indent=2),
        )
        response = await client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1500,
            system=prompt,
            messages=[{"role": "user", "content": "Evaluate the current ruleset and game state."}],
        )
        analysis_text = response.content[0].text

        # Parse structured output from free-form analysis
        return _parse_evaluation(analysis_text, season_id, round_number)
    except Exception:
        logger.exception("rule_evaluator_failed season=%s round=%d", season_id, round_number)
        return _mock_evaluation(season_id, round_number)


def _compute_staleness(
    gov_events: list,
    ruleset: dict,
    current_round: int,
) -> dict[str, int]:
    """Compute rounds since last change per parameter."""
    last_changed: dict[str, int] = {}
    for e in gov_events:
        if e.event_type == "rule.enacted":
            param = (e.payload or {}).get("parameter", "")
            rn = (e.payload or {}).get("round_enacted", 0)
            if param:
                last_changed[param] = max(last_changed.get(param, 0), rn)

    staleness = {}
    for param in ruleset:
        if param in last_changed:
            staleness[param] = current_round - last_changed[param]
        else:
            staleness[param] = current_round  # Never changed
    return staleness


def _parse_evaluation(
    text: str,
    season_id: str,
    round_number: int,
) -> RuleEvaluation:
    """Parse Opus's free-form analysis into structured fields."""
    # Simple section extraction
    experiments = []
    stale = []
    equilibrium = ""
    concerns = []

    lines = text.split("\n")
    current_section = ""
    for line in lines:
        lower = line.lower().strip()
        if "experiment" in lower and ("suggest" in lower or "#" in lower or "**" in lower):
            current_section = "experiments"
            continue
        elif "stale" in lower and ("param" in lower or "#" in lower or "**" in lower):
            current_section = "stale"
            continue
        elif "equilibrium" in lower and ("#" in lower or "**" in lower):
            current_section = "equilibrium"
            continue
        elif "concern" in lower or "flag" in lower and ("#" in lower or "**" in lower):
            current_section = "concerns"
            continue

        stripped = line.strip()
        if not stripped:
            continue

        is_list_item = (
            stripped.startswith("-")
            or stripped.startswith("*")
            or stripped[0].isdigit()
        )
        clean = stripped.lstrip("-*0123456789. ")

        if current_section == "experiments" and is_list_item:
            experiments.append(clean)
        elif current_section == "stale" and is_list_item:
            stale.append(clean)
        elif current_section == "equilibrium":
            equilibrium += stripped + " "
        elif current_section == "concerns" and is_list_item:
            concerns.append(clean)

    return RuleEvaluation(
        season_id=season_id,
        round_number=round_number,
        suggested_experiments=experiments or ["No experiments suggested"],
        stale_parameters=stale,
        equilibrium_notes=equilibrium.strip() or "No equilibrium analysis available",
        flagged_concerns=concerns,
    )


def _mock_evaluation(season_id: str, round_number: int) -> RuleEvaluation:
    """Mock evaluation for tests (no API key needed)."""
    return RuleEvaluation(
        season_id=season_id,
        round_number=round_number,
        suggested_experiments=[
            "Try increasing elam_margin to 15 to extend close games",
            "Consider reducing quarter_possessions to 12 for faster rounds",
        ],
        stale_parameters=["three_point_distance", "altitude_stamina_penalty"],
        equilibrium_notes="The current meta appears balanced with no dominant strategy.",
        flagged_concerns=[],
    )


async def store_rule_evaluation(
    repo: Repository,
    season_id: str,
    round_number: int,
    evaluation: RuleEvaluation,
) -> None:
    """Store rule evaluation result."""
    await repo.store_eval_result(
        season_id=season_id,
        round_number=round_number,
        eval_type="rule_evaluation",
        score=0.0,
        details_json=evaluation.model_dump(mode="json"),
    )
