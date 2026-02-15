"""Dramatic pacing modulation — classify possessions for variable-speed replay.

The simulation engine runs instantly and produces a full GameResult. Before the
presenter streams possessions, this module classifies each possession's dramatic
weight. The presenter uses these annotations to:

1. Vary the delay between possessions (dramatic moments get more time).
2. Enrich SSE events with ``drama_level`` and ``drama_tags`` so the frontend
   can apply visual treatments (CSS classes, animations).

The total wall-clock time per quarter is preserved — dramatic moments steal time
from routine moments. The ``normalize_delays`` function redistributes the time
budget so the quarter finishes on schedule.

This is a pure computation module — no AI calls, no DB access, no async.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pinwheel.models.game import GameResult

DramaLevel = Literal["routine", "elevated", "high", "peak"]


@dataclass
class DramaAnnotation:
    """Dramatic weight for a single possession."""

    possession_index: int
    level: DramaLevel
    tags: list[str] = field(default_factory=list)
    delay_multiplier: float = 1.0


def annotate_drama(game_result: GameResult) -> list[DramaAnnotation]:
    """Pre-classify every possession in a game for dramatic pacing.

    Because the presenter has the full GameResult, this runs once before
    streaming begins. The annotations drive both pacing and visual treatment.

    Detection rules (in order of evaluation):
        - Lead changes and tie-breaking
        - Scoring runs (momentum detection)
        - Move activations (signature plays)
        - Elam Ending approach (target score proximity)
        - Game-winning shot (final scoring possession)
        - Elam period transition
        - Close game in late quarters

    Returns a list of DramaAnnotation, one per possession, in order.
    """
    annotations: list[DramaAnnotation] = []
    possessions = game_result.possession_log
    if not possessions:
        return annotations

    # Pre-compute game-level context
    elam_target = game_result.elam_target_score
    total_possessions = len(possessions)
    last_possession_index = total_possessions - 1

    # Track running state
    prev_leader: str | None = None  # "home" | "away" | "tied"
    run_team: str | None = None  # Team on a scoring run
    run_points: int = 0

    for idx, poss in enumerate(possessions):
        tags: list[str] = []
        multiplier = 1.0

        # --- Detect lead changes ---
        if poss.home_score > poss.away_score:
            leader = "home"
        elif poss.away_score > poss.home_score:
            leader = "away"
        else:
            leader = "tied"

        if prev_leader is not None and leader != prev_leader and leader != "tied":
            if prev_leader != "tied":
                tags.append("lead_change")
                multiplier = max(multiplier, 1.8)
            else:
                tags.append("tie_broken")
                multiplier = max(multiplier, 1.4)

        if leader == "tied" and prev_leader != "tied" and prev_leader is not None:
            tags.append("game_tied")
            multiplier = max(multiplier, 1.5)

        prev_leader = leader

        # --- Detect scoring runs ---
        if poss.points_scored > 0:
            scoring_team = poss.offense_team_id
            if scoring_team == run_team:
                run_points += poss.points_scored
            else:
                run_team = scoring_team
                run_points = poss.points_scored

            if run_points >= 8:
                tags.append("big_run")
                multiplier = max(multiplier, 0.75)  # Faster — momentum
            elif run_points >= 5:
                tags.append("run")
                multiplier = max(multiplier, 0.85)  # Slightly faster

        # --- Detect move activations ---
        if poss.move_activated:
            tags.append("move")
            tags.append(f"move:{poss.move_activated}")
            multiplier = max(multiplier, 1.3)

        # --- Detect Elam approach ---
        if elam_target and poss.quarter >= 4:  # Elam period
            tags.append("elam")
            home_to_go = elam_target - poss.home_score
            away_to_go = elam_target - poss.away_score
            closest = min(home_to_go, away_to_go)

            if closest <= 3:
                tags.append("elam_climax")
                multiplier = max(multiplier, 2.5)  # Very slow — savor it
            elif closest <= 7:
                tags.append("elam_tension")
                multiplier = max(multiplier, 1.8)
            else:
                multiplier = max(multiplier, 1.2)  # Elam is always slightly slower

        # --- Detect game-winning shot ---
        if idx == last_possession_index and poss.points_scored > 0:
            tags.append("game_winner")
            multiplier = max(multiplier, 3.0)  # Long pause — the big moment

        # --- Detect entering Elam (transition possession) ---
        if idx > 0:
            prev_poss = possessions[idx - 1]
            if prev_poss.quarter < 4 and poss.quarter >= 4:
                tags.append("elam_start")
                multiplier = max(multiplier, 2.0)  # Scene-setting pause

        # --- Detect close game in late quarters ---
        if poss.quarter == 3:  # Q3 is the last regulation quarter before Elam
            score_diff = abs(poss.home_score - poss.away_score)
            if score_diff <= 3:
                tags.append("close_late")
                multiplier = max(multiplier, 1.3)

        # --- Classify drama level from multiplier ---
        if multiplier >= 2.0:
            level: DramaLevel = "peak"
        elif multiplier >= 1.4:
            level = "high"
        elif multiplier < 1.0:
            level = "elevated"  # Fast-paced excitement (runs)
        else:
            level = "routine"

        annotations.append(
            DramaAnnotation(
                possession_index=idx,
                level=level,
                tags=tags,
                delay_multiplier=multiplier,
            )
        )

    return annotations


def normalize_delays(
    annotations: list[DramaAnnotation],
    quarter_seconds: float,
) -> list[float]:
    """Convert drama annotations into actual delay values (seconds).

    Normalizes so total delay across the quarter equals ``quarter_seconds``.
    Dramatic moments get more time, routine moments get less, but the total
    quarter duration stays the same.

    Args:
        annotations: DramaAnnotations for possessions in a single quarter.
        quarter_seconds: Wall-clock budget for the quarter.

    Returns:
        A list of delay values (seconds), one per annotation, summing to
        approximately ``quarter_seconds``.
    """
    if not annotations:
        return []

    raw_multipliers = [a.delay_multiplier for a in annotations]
    total_raw = sum(raw_multipliers)
    if total_raw == 0:
        base_delay = quarter_seconds / max(len(annotations), 1)
        return [base_delay] * len(annotations)

    base_delay = quarter_seconds / total_raw
    return [m * base_delay for m in raw_multipliers]


def get_drama_summary(annotations: list[DramaAnnotation]) -> dict[str, int]:
    """Summarize drama level counts for logging/debugging.

    Returns a dict like {"routine": 40, "elevated": 5, "high": 8, "peak": 2}.
    """
    counts: dict[str, int] = {"routine": 0, "elevated": 0, "high": 0, "peak": 0}
    for a in annotations:
        counts[a.level] = counts.get(a.level, 0) + 1
    return counts
