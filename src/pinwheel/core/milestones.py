"""Milestone system — stat-based move acquisition for hoopers.

Hoopers earn new moves by hitting cumulative stat thresholds across a season.
See SIMULATION.md (Decision #13): moves can be seeded, earned, or governed.

Career stats are aggregated from box scores across all games in a season.
Milestones fire once — if a hooper already has the move, it is not granted again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pinwheel.models.team import Move

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MilestoneDefinition:
    """A stat threshold that unlocks a new move for a hooper.

    Attributes:
        stat: Box score stat column name (e.g., "points", "three_pointers_made").
        threshold: Cumulative season total required to unlock.
        move_name: Name of the move to grant.
        move_trigger: Trigger condition for the move.
        move_effect: Description of the move's effect.
        move_type: Category hint for the move (informational).
        attribute_gate: Minimum attribute values required to earn the move.
        description: Human-readable unlock condition.
    """

    stat: str
    threshold: int
    move_name: str
    move_trigger: str
    move_effect: str
    move_type: str
    attribute_gate: dict[str, int]
    description: str

    def to_move(self) -> Move:
        """Create the Move model for this milestone."""
        return Move(
            name=self.move_name,
            trigger=self.move_trigger,
            effect=self.move_effect,
            attribute_gate=self.attribute_gate,
            source="earned",
        )


# Default milestone definitions.
# Thresholds are calibrated for a 21-game regular season.
DEFAULT_MILESTONES: list[MilestoneDefinition] = [
    MilestoneDefinition(
        stat="points",
        threshold=50,
        move_name="Fadeaway",
        move_trigger="half_court_setup",
        move_effect="+12% mid-range shot probability",
        move_type="mid_range",
        attribute_gate={"scoring": 40},
        description="Score 50 career points in a season",
    ),
    MilestoneDefinition(
        stat="assists",
        threshold=20,
        move_name="No-Look Pass",
        move_trigger="half_court_setup",
        move_effect="assist window doubled, +10% teammate shot",
        move_type="passing",
        attribute_gate={"passing": 40, "iq": 30},
        description="Record 20 career assists in a season",
    ),
    MilestoneDefinition(
        stat="steals",
        threshold=15,
        move_name="Strip Steal",
        move_trigger="opponent_iso",
        move_effect="+15% steal probability on ball handler",
        move_type="defensive",
        attribute_gate={"defense": 40},
        description="Record 15 career steals in a season",
    ),
    MilestoneDefinition(
        stat="three_pointers_made",
        threshold=10,
        move_name="Deep Three",
        move_trigger="made_three_last_possession",
        move_effect="+18% three-point from beyond normal range",
        move_type="three_point",
        attribute_gate={"scoring": 35},
        description="Hit 10 career three-pointers in a season",
    ),
]


def check_milestones(
    season_stats: dict[str, int],
    existing_move_names: set[str],
    milestones: list[MilestoneDefinition] | None = None,
) -> list[Move]:
    """Check which milestones a hooper has reached and return newly earned moves.

    Args:
        season_stats: Aggregated box score stats for the hooper this season.
            Keys are stat column names, values are cumulative totals.
        existing_move_names: Set of move names the hooper already has.
            Used to prevent re-granting the same move.
        milestones: Optional override for milestone definitions.
            Defaults to DEFAULT_MILESTONES.

    Returns:
        List of Move objects for newly earned milestones. Empty if none qualify.
    """
    if milestones is None:
        milestones = DEFAULT_MILESTONES

    earned: list[Move] = []
    for milestone in milestones:
        # Skip if hooper already has this move
        if milestone.move_name in existing_move_names:
            continue

        # Check if stat threshold is met
        stat_value = season_stats.get(milestone.stat, 0)
        if stat_value >= milestone.threshold:
            earned.append(milestone.to_move())
            logger.info(
                "milestone_reached move=%s stat=%s value=%d threshold=%d",
                milestone.move_name,
                milestone.stat,
                stat_value,
                milestone.threshold,
            )

    return earned
