"""Validation for governance-produced GameDefinitionPatches (Phase 5).

Structural change is the most powerful lever governance has — a patch can
add/remove/reshape actions and change the turn structure. Before a patch
can register as an effect it must: construct cleanly, leave the cumulative
game definition playable (invariants), and survive a seeded smoke
simulation. Degenerate definitions (no scorable actions, zero-length
quarters, unreachable Elam) can never reach a live round.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError

from pinwheel.models.game_definition import (
    GameDefinition,
    GameDefinitionPatch,
    basketball_game_definition,
)
from pinwheel.models.rules import RuleSet

if TYPE_CHECKING:
    from pinwheel.models.team import Team

logger = logging.getLogger(__name__)

# Invariant bounds for a playable game definition
MAX_POINTS_ON_SUCCESS = 25
MAX_QUARTERS = 12
MIN_QUARTER_CLOCK_SECONDS = 30.0
MAX_QUARTER_CLOCK_SECONDS = 3600.0
MIN_SAFETY_CAP = 50
MAX_SAFETY_CAP = 1000
MAX_PARTICIPANTS_PER_SIDE = 5
SMOKE_SIM_MAX_TOTAL_SCORE = 500
# Tier-3 target score ("first to N wins"): 0 = disabled; when set it must
# be reachable in a real game.
MIN_TARGET_SCORE = 5
MAX_TARGET_SCORE = 200


def _check_invariants(game_def: GameDefinition) -> list[str]:
    """Invariants a patched definition must satisfy to be playable."""
    violations: list[str] = []

    # Mirror ActionRegistry.shot_actions(): only category == "shot" actions
    # participate in shot selection — chain nodes (pass/dribble/...) don't
    # keep a definition scorable.
    shot_actions = [
        a for a in game_def.actions if a.category == "shot" and not a.is_free_throw
    ]
    if not shot_actions:
        violations.append("No non-free-throw actions survive the patch")
    elif not any(a.selection_weight > 0 for a in shot_actions):
        violations.append(
            "No selectable shot action (all selection weights are 0)"
        )

    for action in game_def.actions:
        if action.selection_weight < 0:
            violations.append(
                f"Action '{action.name}' has negative selection_weight"
            )
        if not 0 <= action.points_on_success <= MAX_POINTS_ON_SUCCESS:
            violations.append(
                f"Action '{action.name}' points_on_success "
                f"{action.points_on_success} outside [0, {MAX_POINTS_ON_SUCCESS}]"
            )

    if not 1 <= game_def.quarters <= MAX_QUARTERS:
        violations.append(
            f"quarters {game_def.quarters} outside [1, {MAX_QUARTERS}]"
        )
    if game_def.elam_ending_enabled and not (
        1 <= game_def.elam_trigger_quarter <= game_def.quarters
    ):
        violations.append(
            f"elam_trigger_quarter {game_def.elam_trigger_quarter} outside "
            f"[1, quarters={game_def.quarters}]"
        )
    if not (
        MIN_QUARTER_CLOCK_SECONDS
        <= game_def.quarter_clock_seconds
        <= MAX_QUARTER_CLOCK_SECONDS
    ):
        violations.append(
            f"quarter_clock_seconds {game_def.quarter_clock_seconds} outside "
            f"[{MIN_QUARTER_CLOCK_SECONDS}, {MAX_QUARTER_CLOCK_SECONDS}]"
        )
    if not MIN_SAFETY_CAP <= game_def.safety_cap_possessions <= MAX_SAFETY_CAP:
        violations.append(
            f"safety_cap_possessions {game_def.safety_cap_possessions} "
            f"outside [{MIN_SAFETY_CAP}, {MAX_SAFETY_CAP}]"
        )
    if not 1 <= game_def.participants_per_side <= MAX_PARTICIPANTS_PER_SIDE:
        violations.append(
            f"participants_per_side {game_def.participants_per_side} outside "
            f"[1, {MAX_PARTICIPANTS_PER_SIDE}]"
        )

    # Tier-3 target score: bounded, and mutually exclusive with Elam.
    if game_def.target_score != 0 and not (
        MIN_TARGET_SCORE <= game_def.target_score <= MAX_TARGET_SCORE
    ):
        violations.append(
            f"target_score {game_def.target_score} outside "
            f"[{MIN_TARGET_SCORE}, {MAX_TARGET_SCORE}] (0 disables)"
        )
    if game_def.target_score > 0 and game_def.elam_ending_enabled:
        violations.append(
            "target_score and the Elam Ending are mutually exclusive — "
            "disable elam_ending_enabled when setting a target score"
        )

    return violations


def _check_chain_reachability(game_def: GameDefinition) -> list[str]:
    """Every chain start node must reach a shot or terminal event.

    Governance can reshape the possession chain's Markov edges
    (``modify_transitions``, ``remove_actions``); a patch that leaves a
    start node in a positive-weight cycle with no exit would turn every
    possession into a shot-clock violation — a bricked game. This walks
    the edge graph the same way the micro engine does:

    - Only positive-weight edges are traversable.
    - Edges to removed/unknown nodes are dangling — the engine skips
      them, and a node with NO live candidates falls through to the
      ``"shot"`` pseudo-target (so it terminates).
    - Reaching ``"shot"`` or a turnover/violation-category node counts
      as terminating.

    If the definition has no ``initiate`` node the engine's fallback
    chain vocabulary engages, which is known-good — no check needed.
    """
    from collections import deque

    from pinwheel.core.possession_micro import MAX_CHAIN_STEPS

    chain_nodes = {
        a.name: a
        for a in game_def.actions
        if a.category not in ("shot", "special")
    }
    if "initiate" not in chain_nodes:
        return []

    terminal_categories = {"turnover", "violation"}
    violations: list[str] = []

    for start in ("initiate", "second_chance"):
        if start not in chain_nodes:
            continue
        reached_terminal = False
        seen = {start}
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        while queue and not reached_terminal:
            name, depth = queue.popleft()
            node = chain_nodes[name]
            if node.category in terminal_categories:
                reached_terminal = True
                break
            targets: set[str] = set()
            for table in (
                node.transitions,
                node.success_transitions,
                node.failure_transitions,
            ):
                for target, weight in table.items():
                    if weight > 0:
                        targets.add(target)
            live = [t for t in targets if t == "shot" or t in chain_nodes]
            if not live:
                # No live candidates — the engine forces "shot".
                reached_terminal = True
                break
            if "shot" in live:
                reached_terminal = True
                break
            if depth + 1 >= MAX_CHAIN_STEPS:
                continue  # deeper nodes can't terminate within the cap
            for t in live:
                if t not in seen:
                    seen.add(t)
                    queue.append((t, depth + 1))
        if not reached_terminal:
            violations.append(
                f"Patch bricks possessions: chain node '{start}' cannot "
                f"reach a shot or terminal event within {MAX_CHAIN_STEPS} "
                "steps — every possession would die on the shot clock"
            )

    return violations


def _make_smoke_team(prefix: str) -> Team:
    """Minimal synthetic team for the smoke simulation."""
    from pinwheel.models.team import (
        Hooper,
        PlayerAttributes,
        Team,
        Venue,
        suppress_budget_check,
    )

    with suppress_budget_check():
        attrs = PlayerAttributes(
            scoring=50, passing=40, defense=40, speed=40, stamina=40,
            iq=50, ego=30, chaotic_alignment=20, fate=30,
        )
    return Team(
        id=f"smoke-{prefix}",
        name=f"Smoke {prefix}",
        venue=Venue(name=f"Smoke Arena {prefix}", capacity=5000),
        hoopers=[
            Hooper.model_construct(
                id=f"smoke-{prefix}-{i}",
                name=f"Smoke-{prefix}-{i}",
                team_id=f"smoke-{prefix}",
                archetype="sharpshooter",
                backstory="",
                attributes=attrs,
                moves=[],
                is_starter=True,
            )
            for i in range(3)
        ],
    )


def validate_game_def_patch(
    patch_dict: dict[str, object],
    current_rules: RuleSet | None = None,
    existing_patches: list[dict[str, object]] | None = None,
) -> list[str]:
    """Validate a structural patch. Returns violations (empty = valid).

    Three layers:
    1. Pydantic construction of the patch itself.
    2. Invariants on the CUMULATIVELY patched definition — existing active
       patches are applied first, since patches compound in registration
       order.
    3. A seeded smoke simulation: the patched game must terminate with a
       sane total score. Milliseconds of cost; catches degenerate
       definitions the static invariants miss.
    """
    rules = current_rules or RuleSet()

    try:
        patch = GameDefinitionPatch(**patch_dict)  # type: ignore[arg-type]
    except (ValidationError, TypeError) as e:
        return [f"Patch failed to construct: {e}"]

    game_def = basketball_game_definition(rules)
    for prior_dict in existing_patches or []:
        try:
            prior = GameDefinitionPatch(**prior_dict)  # type: ignore[arg-type]
            game_def = prior.apply(game_def)
        except (ValidationError, TypeError, ValueError, KeyError):
            # A broken prior patch shouldn't block validating this one —
            # simulate_game skips broken patches the same way.
            logger.warning("prior_patch_skipped_during_validation")

    try:
        patched = patch.apply(game_def)
    except (ValueError, KeyError, TypeError) as e:
        return [f"Patch failed to apply: {e}"]

    violations = _check_invariants(patched)
    if violations:
        return violations

    # Reachability: the possession chain must be able to terminate.
    violations = _check_chain_reachability(patched)
    if violations:
        return violations

    # Smoke sim — the definition must produce a finishable game
    from pinwheel.core.simulation import simulate_game

    try:
        result = simulate_game(
            _make_smoke_team("home"),
            _make_smoke_team("away"),
            rules,
            seed=42,
            game_def=patched,
        )
    except Exception as e:  # noqa: BLE001 — any crash is a validation failure
        return [f"Smoke simulation crashed: {type(e).__name__}: {e}"]

    total = result.home_score + result.away_score
    if total > SMOKE_SIM_MAX_TOTAL_SCORE:
        return [
            f"Smoke simulation produced a degenerate score "
            f"({result.home_score}-{result.away_score})"
        ]

    return []
