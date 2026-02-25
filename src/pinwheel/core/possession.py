"""Possession model — the atomic unit of gameplay.

Ball handler -> action selection -> shot resolution -> rebounds -> fouls -> stamina.
See SIMULATION.md "Possession Model".
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from pinwheel.core.defense import (
    SCHEME_CONTEST_MODIFIER,
    SCHEME_STAMINA_COST,
    SCHEME_TURNOVER_BONUS,
    DefensiveScheme,
    assign_matchups,
    get_primary_defender,
    select_scheme,
)
from pinwheel.core.moves import apply_move_modifier, get_triggered_moves
from pinwheel.core.scoring import ShotType, resolve_shot
from pinwheel.core.state import GameState, HooperState, PossessionContext
from pinwheel.models.game import PossessionLog
from pinwheel.models.game_definition import ActionRegistry
from pinwheel.models.rules import RuleSet

# Legacy constant — now governed by rules.dead_ball_time_seconds
_DEFAULT_DEAD_TIME_SECONDS = 9.0


@dataclass
class SurfaceModifiers:
    """Modifiers derived from the home venue's playing surface.

    Applied alongside home-court mechanics in resolve_possession().
    See SIMULATION.md "Surface modifier".
    """

    at_rim_weight_modifier: float = 0.0
    """Additive modifier to at_rim shot selection weight (percentage of base)."""

    mid_range_weight_modifier: float = 0.0
    """Additive modifier to mid_range shot selection weight (percentage of base)."""

    three_point_weight_modifier: float = 0.0
    """Additive modifier to three_point shot selection weight (percentage of base)."""

    stamina_drain_multiplier: float = 1.0
    """Multiplicative factor on stamina drain (1.0 = normal)."""

    turnover_rate_modifier: float = 0.0
    """Additive modifier to turnover probability."""

    shot_probability_modifier: float = 0.0
    """Additive modifier to all shot probabilities."""

    speed_at_rim_modifier: float = 0.0
    """Modifier to the speed component of at_rim weight (fraction, e.g. -0.10 = -10%)."""


# Surface effects table — each surface maps to its modifiers.
# "hardwood" is the default with no modifications.
SURFACE_EFFECTS: dict[str, SurfaceModifiers] = {
    "hardwood": SurfaceModifiers(),
    "grass": SurfaceModifiers(
        speed_at_rim_modifier=-0.10,    # -10% at_rim weight (speed penalty)
        stamina_drain_multiplier=1.20,  # +20% stamina drain
        turnover_rate_modifier=0.05,    # +5% turnover rate
    ),
    "sand": SurfaceModifiers(
        speed_at_rim_modifier=-0.20,    # -20% at_rim weight (heavy speed penalty)
        stamina_drain_multiplier=1.40,  # +40% stamina drain
        three_point_weight_modifier=0.10,  # +10% three-point weight (shoot from outside)
    ),
    "ice": SurfaceModifiers(
        turnover_rate_modifier=0.15,    # +15% turnover rate (slippery)
        shot_probability_modifier=-0.05,  # -5% all shot probabilities
        speed_at_rim_modifier=0.10,     # +10% at_rim weight (sliding momentum)
    ),
    "clay": SurfaceModifiers(
        mid_range_weight_modifier=0.10,  # +10% mid_range weight
        stamina_drain_multiplier=1.10,  # +10% stamina drain
    ),
}


def get_surface_modifiers(surface: str) -> SurfaceModifiers:
    """Look up surface modifiers for a venue surface type.

    Unknown surfaces are treated as hardwood (no modifiers).
    """
    return SURFACE_EFFECTS.get(surface, SurfaceModifiers())


@dataclass
class PossessionResult:
    """Result of resolving one possession."""

    points_scored: int = 0
    scoring_team_home: bool = True
    turnover: bool = False
    foul_on_defender: bool = False
    fouling_agent_id: str = ""
    shooter_id: str = ""
    assist_id: str = ""
    rebound_id: str = ""
    is_offensive_rebound: bool = False
    shot_type: str = ""
    shot_made: bool = False
    move_activated: str = ""
    defensive_scheme: str = ""
    time_used: float = 0.0
    log: PossessionLog | None = None


def select_ball_handler(
    offense: list[HooperState],
    rng: random.Random,
    rules: RuleSet | None = None,
    total_team_fga: int = 0,
) -> HooperState:
    """Pick who handles the ball. Weighted by passing + IQ.

    When rules.max_shot_share < 1.0, players who have exceeded their share
    of the team's field goal attempts get a reduced selection weight.
    """
    if not offense:
        raise ValueError("No offensive players available")
    max_share = rules.max_shot_share if rules else 1.0
    weights = []
    for a in offense:
        base = max(1, a.current_attributes.passing + a.current_attributes.iq)
        # Reduce weight for players exceeding their max shot share
        if max_share < 1.0 and total_team_fga > 0:
            player_share = a.field_goals_attempted / total_team_fga
            if player_share > max_share:
                # Scale down proportionally — the more over the cap, the less likely
                overshoot = player_share - max_share
                penalty = max(0.1, 1.0 - overshoot * 5.0)
                base = max(1, int(base * penalty))
        weights.append(base)
    return rng.choices(offense, weights=weights, k=1)[0]


def select_action(
    handler: HooperState,
    game_state: GameState,
    rules: RuleSet,
    rng: random.Random,
    effect_biases: PossessionContext | None = None,
    surface: SurfaceModifiers | None = None,
    action_registry: ActionRegistry | None = None,
) -> ShotType:
    """Select shot type based on handler attributes and game state.

    When ``action_registry`` is provided, weights are built from the
    registry's ``shot_actions()`` instead of hardcoded constants. The
    registry path produces identical results for the standard basketball
    actions because ``basketball_actions()`` encodes the same constants.

    Surface modifiers adjust shot selection weights: speed_at_rim_modifier
    scales the speed component of at_rim, while the per-type weight modifiers
    are additive percentages of the pre-surface weight.
    """
    if action_registry is not None:
        return _select_action_registry(
            handler, game_state, rules, rng,
            effect_biases, surface, action_registry,
        )

    scoring = handler.current_attributes.scoring
    speed = handler.current_attributes.speed
    iq = handler.current_attributes.iq

    # three_point_distance: default 22.15 ft (NBA 3PT distance).
    # Farther distance reduces three-point selection weight; closer increases it.
    # Scale: each foot from default shifts weight by ~2 points.
    distance_from_default = rules.three_point_distance - 22.15
    three_distance_penalty = distance_from_default * 2.0

    # Surface modifier on the speed component of at_rim weight.
    # e.g. grass (-0.10) reduces the speed*0.3 term by 10%.
    speed_at_rim = speed * 0.3
    if surface:
        speed_at_rim *= 1.0 + surface.speed_at_rim_modifier

    weights = {
        "at_rim": 30.0 + speed_at_rim,
        "mid_range": 25.0 + iq * 0.2,
        "three_point": 20.0 + scoring * 0.3 - three_distance_penalty,
    }

    # Apply surface weight modifiers (additive percentage of pre-surface weight)
    if surface:
        weights["at_rim"] += weights["at_rim"] * surface.at_rim_weight_modifier
        weights["mid_range"] += weights["mid_range"] * surface.mid_range_weight_modifier
        weights["three_point"] += weights["three_point"] * surface.three_point_weight_modifier

    # Apply team strategy biases
    strategy = game_state.offense_strategy
    if strategy:
        weights["at_rim"] += strategy.at_rim_bias
        weights["mid_range"] += strategy.mid_range_bias
        weights["three_point"] += strategy.three_point_bias

    # Apply effect-derived biases
    if effect_biases:
        weights["at_rim"] += effect_biases.at_rim_bias
        weights["mid_range"] += effect_biases.mid_range_bias
        weights["three_point"] += effect_biases.three_point_bias

    # Elam: trailing team takes more threes
    if game_state.elam_activated and game_state.elam_target_score:
        my_score = game_state.home_score if game_state.home_has_ball else game_state.away_score
        gap = game_state.elam_target_score - my_score
        if gap > 5:
            weights["three_point"] += 15.0

    types = list(weights.keys())
    w = [max(1.0, weights[t]) for t in types]  # floor at 1.0 to avoid negative weights
    chosen: ShotType = rng.choices(types, weights=w, k=1)[0]
    return chosen


def _select_action_registry(
    handler: HooperState,
    game_state: GameState,
    rules: RuleSet,
    rng: random.Random,
    effect_biases: PossessionContext | None,
    surface: SurfaceModifiers | None,
    action_registry: ActionRegistry,
) -> ShotType:
    """Registry-based action selection — data-driven weights.

    Builds selection weights from ``registry.shot_actions()`` instead of
    hardcoded constants. For the standard basketball registry, this produces
    identical RNG draws because the weights are algebraically equivalent.
    """
    # three_point_distance: default 22.15 ft (NBA 3PT distance).
    distance_from_default = rules.three_point_distance - 22.15
    three_distance_penalty = distance_from_default * 2.0

    # Build weights from registry shot actions, sorted by name for determinism
    shot_actions = sorted(action_registry.shot_actions(), key=lambda a: a.name)
    weights: dict[str, float] = {}

    for action_def in shot_actions:
        name = action_def.name
        # Base weight from action definition
        w = action_def.selection_weight

        # Attribute contributions from weight_attributes
        for attr, factor in sorted(action_def.weight_attributes.items()):
            attr_val = getattr(handler.current_attributes, attr, 0)
            # Special handling: speed_at_rim_modifier applies to the speed
            # component of at_rim weight.
            if name == "at_rim" and attr == "speed" and surface:
                w += attr_val * factor * (1.0 + surface.speed_at_rim_modifier)
            else:
                w += attr_val * factor

        # three_point_distance penalty (only for three_point action)
        if name == "three_point":
            w -= three_distance_penalty

        weights[name] = w

    # Apply surface weight modifiers (additive percentage of pre-surface weight)
    if surface:
        for name in weights:
            if name == "at_rim":
                weights[name] += weights[name] * surface.at_rim_weight_modifier
            elif name == "mid_range":
                weights[name] += weights[name] * surface.mid_range_weight_modifier
            elif name == "three_point":
                weights[name] += weights[name] * surface.three_point_weight_modifier

    # Apply team strategy biases
    strategy = game_state.offense_strategy
    if strategy:
        for name in weights:
            if name == "at_rim":
                weights[name] += strategy.at_rim_bias
            elif name == "mid_range":
                weights[name] += strategy.mid_range_bias
            elif name == "three_point":
                weights[name] += strategy.three_point_bias

    # Apply effect-derived biases from action_biases dict
    if effect_biases:
        for name in weights:
            bias = effect_biases.action_biases.get(name, 0.0)
            if bias != 0.0:
                weights[name] += bias

    # Elam: trailing team takes more threes
    if game_state.elam_activated and game_state.elam_target_score:
        my_score = game_state.home_score if game_state.home_has_ball else game_state.away_score
        gap = game_state.elam_target_score - my_score
        if gap > 5 and "three_point" in weights:
            weights["three_point"] += 15.0

    # Sort by name for deterministic RNG draws (at_rim < mid_range < three_point)
    types = sorted(weights.keys())
    w = [max(1.0, weights[t]) for t in types]  # floor at 1.0 to avoid negative weights
    chosen: ShotType = rng.choices(types, weights=w, k=1)[0]
    return chosen


def check_turnover(
    handler: HooperState,
    scheme: DefensiveScheme,
    rng: random.Random,
    rules: RuleSet | None = None,
    effect_turnover_modifier: float = 0.0,
    crowd_pressure_modifier: float = 0.0,
) -> bool:
    """Check if the offense turns the ball over.

    The base turnover rate is scaled by rules.turnover_rate_modifier (default 1.0).
    effect_turnover_modifier is additive from PossessionContext.
    crowd_pressure_modifier is additive from home court advantage (applied to away offense).
    """
    base_to_rate = 0.08
    modifier = rules.turnover_rate_modifier if rules else 1.0
    iq_reduction = handler.current_attributes.iq / 1000.0
    scheme_bonus = SCHEME_TURNOVER_BONUS[scheme]
    stamina_penalty = (1.0 - handler.current_stamina) * 0.05
    to_prob = (
        (base_to_rate * modifier)
        - iq_reduction
        + scheme_bonus
        + stamina_penalty
        + effect_turnover_modifier
        + crowd_pressure_modifier
    )
    return rng.random() < max(0.01, min(0.25, to_prob))


def check_foul(
    defender: HooperState,
    shot_type: ShotType,
    scheme: DefensiveScheme,
    rng: random.Random,
    rules: RuleSet | None = None,
    defensive_intensity: float = 0.0,
) -> bool:
    """Check if the defender commits a foul.

    The base foul rate is scaled by rules.foul_rate_modifier (default 1.0).
    Higher defensive_intensity (positive) increases foul rate --- tighter defense
    means more contact. Only positive intensity adds fouls; relaxed defense
    (negative intensity) does not reduce fouls below the base rate.
    """
    base_foul_rate = 0.08
    modifier = rules.foul_rate_modifier if rules else 1.0
    # Aggressive schemes foul more
    scheme_add = {"man_tight": 0.03, "press": 0.04, "man_switch": 0.01, "zone": 0.0}
    # Low-IQ defenders foul more
    iq_penalty = max(0, (50 - defender.current_attributes.iq)) / 500.0
    # High defensive intensity causes more fouls: +0.5 intensity -> +4% foul rate
    intensity_add = max(0.0, defensive_intensity) * 0.08
    foul_prob = (base_foul_rate * modifier) + scheme_add[scheme] + iq_penalty + intensity_add
    return rng.random() < min(0.25, foul_prob)


def attempt_rebound(
    offense: list[HooperState],
    defense: list[HooperState],
    rng: random.Random,
    rules: RuleSet | None = None,
) -> tuple[HooperState, bool]:
    """Resolve a rebound after a missed shot. Returns (rebounder, is_offensive).

    The offensive rebound base weight is governed by rules.offensive_rebound_weight
    (default 5.0) while defensive is fixed at 10.0.

    **Fate --- lucky bounces:** High-Fate offensive players get a bonus to
    offensive rebound weight, representing fortunate ball bounces.
    ``fate_bonus = (fate / 100.0) * 3.0`` --- a Fate-90 player adds +2.7
    to their offensive rebound weight.
    """
    all_players = [(a, True) for a in offense] + [(a, False) for a in defense]
    if not all_players:
        return offense[0], True

    off_reb_weight = rules.offensive_rebound_weight if rules else 5.0

    # Weight by a combination of attributes
    weights = []
    for agent, is_off in all_players:
        # Defense gets natural rebound advantage
        base = off_reb_weight if is_off else 10.0
        # Physical attributes matter
        base += agent.current_attributes.defense * 0.2
        base += agent.current_attributes.speed * 0.1
        base += agent.current_attributes.stamina * 50 * 0.1
        # Fate --- lucky bounces: high-Fate offensive players get lucky bounces
        if is_off:
            fate = agent.hooper.attributes.fate
            base += (fate / 100.0) * 3.0
        weights.append(max(1, base))

    idx = rng.choices(range(len(all_players)), weights=weights, k=1)[0]
    rebounder, is_offensive = all_players[idx]
    return rebounder, is_offensive


def check_shot_clock_violation(
    handler: HooperState,
    scheme: DefensiveScheme,
    rng: random.Random,
) -> bool:
    """Check if the offense commits a shot clock violation.

    Strong defense + low IQ + fatigue -> higher chance of not getting a shot off.
    """
    base_rate = 0.02
    # Aggressive schemes make it harder to get a shot off
    scheme_factor: dict[DefensiveScheme, float] = {
        "press": 2.0,
        "man_tight": 1.5,
        "man_switch": 1.0,
        "zone": 0.5,
    }
    # Fatigue makes it harder to create a shot
    fatigue_factor = 1.0 + (1.0 - handler.current_stamina) * 0.8
    # High IQ handlers manage the clock better
    iq_factor = 1.0 - (handler.current_attributes.iq / 200.0)

    prob = base_rate * scheme_factor[scheme] * fatigue_factor * max(0.3, iq_factor)
    return rng.random() < max(0.005, min(0.12, prob))


def drain_stamina(
    agents: list[HooperState],
    scheme: DefensiveScheme,
    is_defense: bool,
    rules: RuleSet | None = None,
    defensive_intensity: float = 0.0,
    pace_modifier: float = 1.0,
    is_away: bool = False,
    altitude_ft: int = 0,
    surface_stamina_multiplier: float = 1.0,
) -> None:
    """Drain stamina for all agents after a possession.

    Base drain rate is governed by rules.stamina_drain_rate (default 0.007).

    Higher defensive_intensity increases stamina drain for defenders --- playing
    tighter defense is more physically demanding.

    Faster pace (pace_modifier < 1.0) increases stamina drain for both teams ---
    faster possessions mean more physical effort per unit of game time.

    When home_court_enabled, away teams drain extra stamina (away_fatigue_factor).
    High-altitude venues (altitude_ft) add stamina drain scaled by
    rules.altitude_stamina_penalty.

    surface_stamina_multiplier scales the total drain by the venue surface factor
    (e.g. 1.2 for grass = +20% drain, 1.4 for sand = +40% drain).
    """
    base_drain = rules.stamina_drain_rate if rules else 0.007
    scheme_drain = SCHEME_STAMINA_COST[scheme] if is_defense else 0.003
    # Defensive intensity adds drain for defenders only
    intensity_drain = (max(0.0, defensive_intensity) * 0.005) if is_defense else 0.0
    # Faster pace (< 1.0) increases drain; slower pace (> 1.0) decreases
    pace_drain = (1.0 - pace_modifier) * 0.003
    # Away fatigue: extra drain for the visiting team when home court is enabled
    away_drain = 0.0
    if rules and rules.home_court_enabled and is_away:
        away_drain = rules.away_fatigue_factor
    # Altitude drain: scales with venue altitude (normalized to 5000 ft baseline).
    # Higher altitude = harder to breathe = more stamina drain for everyone.
    altitude_drain = 0.0
    if rules and rules.home_court_enabled and altitude_ft > 0:
        altitude_drain = rules.altitude_stamina_penalty * (altitude_ft / 5000.0)
    for agent in agents:
        recovery = agent.hooper.attributes.stamina / 3000.0
        drain = (
            base_drain + scheme_drain + intensity_drain + pace_drain
            + away_drain + altitude_drain - recovery
        )
        # Apply surface stamina multiplier to positive drain only
        drain = max(0, drain) * surface_stamina_multiplier
        agent.current_stamina = max(0.15, agent.current_stamina - drain)


def compute_possession_duration(
    rules: RuleSet,
    rng: random.Random,
    pace_modifier: float = 1.0,
) -> float:
    """Compute clock time consumed by one possession (seconds).

    Dead-ball time between possessions is governed by rules.dead_ball_time_seconds.
    """
    play_time = rules.shot_clock_seconds * rng.uniform(0.4, 1.0)
    dead_time = rules.dead_ball_time_seconds
    return (play_time * pace_modifier) + dead_time


def resolve_possession(
    game_state: GameState,
    rules: RuleSet,
    rng: random.Random,
    last_possession_three: bool = False,
    context: PossessionContext | None = None,
    action_registry: ActionRegistry | None = None,
) -> PossessionResult:
    """Resolve one complete possession.

    Args:
        context: Effect-derived modifiers for this possession. Built by
            _fire_sim_effects from accumulated HookResults.
        action_registry: Data-driven action definitions (Phase 1c). When
            provided, uses registry-based action selection and shot resolution.
            When ``None`` (default), the hardcoded path is used unchanged.
    """
    ctx = context or PossessionContext()

    # Extract strategy parameters once for use throughout the possession
    off_strategy = game_state.offense_strategy
    def_strategy = game_state.defense_strategy
    pace = off_strategy.pace_modifier if off_strategy else 1.0
    def_intensity = def_strategy.defensive_intensity if def_strategy else 0.0

    # Home court advantage modifiers --- computed once per possession
    offense_is_away = not game_state.home_has_ball
    hc_enabled = rules.home_court_enabled
    # Shot probability boost for the home team only (away team is unmodified)
    home_crowd_shot_mod = 0.0
    if hc_enabled and not offense_is_away:
        home_crowd_shot_mod = rules.home_crowd_boost
    # Crowd pressure: increased turnover rate for away offense
    crowd_pressure_mod = rules.crowd_pressure if (hc_enabled and offense_is_away) else 0.0
    # Altitude and away fatigue data for drain_stamina
    altitude = game_state.home_venue_altitude_ft

    # Surface modifiers --- derived from home venue's playing surface.
    # Affects both teams equally (it's the court everyone plays on).
    surface_mods = get_surface_modifiers(game_state.home_venue_surface)

    # Consume clock time first (consistent RNG position)
    time_used = compute_possession_duration(rules, rng, pace_modifier=pace)

    offense = game_state.offense
    defense = game_state.defense

    if not offense or not defense:
        return PossessionResult(time_used=time_used)

    # 0. Random ejection check (effect-driven: "ball is red hot")
    if ctx.random_ejection_probability > 0.0 and rng.random() < ctx.random_ejection_probability:
        active_all = offense + defense
        if active_all:
            victim = rng.choice(active_all)
            victim.ejected = True
            team_id = (
                game_state.home_agents[0].hooper.team_id
                if game_state.home_has_ball
                else game_state.away_agents[0].hooper.team_id
            )
            log = PossessionLog(
                quarter=game_state.quarter,
                possession_number=game_state.possession_number,
                offense_team_id=team_id,
                ball_handler_id=victim.hooper.id,
                action="ejection",
                result=f"effect_ejection:{victim.hooper.name}",
                defensive_scheme="",
                home_score=game_state.home_score,
                away_score=game_state.away_score,
            )
            # Refresh offense/defense lists after ejection
            offense = game_state.offense
            defense = game_state.defense
            if not offense or not defense:
                return PossessionResult(time_used=time_used, log=log)

    # 1. Select scheme and matchups (strategy influences scheme selection)
    scheme = select_scheme(offense, defense, game_state, rules, rng, strategy=def_strategy)
    matchups = assign_matchups(offense, defense, scheme, rng)
    scheme_mod = SCHEME_CONTEST_MODIFIER[scheme]

    # Apply defensive strategy intensity to shot contest
    if def_strategy:
        scheme_mod += def_strategy.defensive_intensity

    # 2. Select ball handler (max_shot_share reduces weight for overused shooters)
    total_team_fga = sum(a.field_goals_attempted for a in offense)
    handler = select_ball_handler(offense, rng, rules=rules, total_team_fga=total_team_fga)

    # 2b. min_pass_per_possession: if the rule requires passes before shooting,
    # model it as a forced turnover chance. Each required pass has a small
    # probability of going wrong (fumble/bad pass), increasing with the number
    # of required passes.
    if rules.min_pass_per_possession > 0:
        pass_turnover_rate = 0.03  # ~3% chance per required pass
        for _ in range(rules.min_pass_per_possession):
            if rng.random() < pass_turnover_rate:
                stealer = rng.choice(defense)
                stealer.steals += 1
                handler.turnovers += 1
                drain_stamina(
                    offense, scheme, is_defense=False, rules=rules,
                    pace_modifier=pace,
                    is_away=offense_is_away, altitude_ft=altitude,
                    surface_stamina_multiplier=surface_mods.stamina_drain_multiplier,
                )
                drain_stamina(
                    defense, scheme, is_defense=True, rules=rules,
                    defensive_intensity=def_intensity, pace_modifier=pace,
                    is_away=not offense_is_away, altitude_ft=altitude,
                    surface_stamina_multiplier=surface_mods.stamina_drain_multiplier,
                )
                game_state.last_action = "turnover"
                game_state.last_result = "turnover"
                game_state.consecutive_makes = 0
                game_state.consecutive_misses += 1
                team_id = (
                    game_state.home_agents[0].hooper.team_id
                    if game_state.home_has_ball
                    else game_state.away_agents[0].hooper.team_id
                )
                log = PossessionLog(
                    quarter=game_state.quarter,
                    possession_number=game_state.possession_number,
                    offense_team_id=team_id,
                    ball_handler_id=handler.hooper.id,
                    defender_id=stealer.hooper.id,
                    action="turnover",
                    result="turnover",
                    defensive_scheme=scheme,
                    home_score=game_state.home_score,
                    away_score=game_state.away_score,
                )
                return PossessionResult(
                    turnover=True,
                    scoring_team_home=game_state.home_has_ball,
                    defensive_scheme=scheme,
                    time_used=time_used,
                    log=log,
                )

    # 3. Check turnover (live-ball: steal) --- with effect + crowd + surface modifiers
    if check_turnover(
        handler, scheme, rng, rules=rules,
        effect_turnover_modifier=ctx.turnover_modifier + surface_mods.turnover_rate_modifier,
        crowd_pressure_modifier=crowd_pressure_mod,
    ):
        stealer = rng.choice(defense)
        stealer.steals += 1
        handler.turnovers += 1
        drain_stamina(
            offense, scheme, is_defense=False, rules=rules,
            pace_modifier=pace,
            is_away=offense_is_away, altitude_ft=altitude,
            surface_stamina_multiplier=surface_mods.stamina_drain_multiplier,
        )
        drain_stamina(
            defense, scheme, is_defense=True, rules=rules,
            defensive_intensity=def_intensity, pace_modifier=pace,
            is_away=not offense_is_away, altitude_ft=altitude,
            surface_stamina_multiplier=surface_mods.stamina_drain_multiplier,
        )

        # Update cross-possession tracking
        game_state.last_action = "turnover"
        game_state.last_result = "turnover"
        game_state.consecutive_makes = 0
        game_state.consecutive_misses += 1

        log = PossessionLog(
            quarter=game_state.quarter,
            possession_number=game_state.possession_number,
            offense_team_id=(
                game_state.home_agents[0].hooper.team_id
                if game_state.home_has_ball
                else game_state.away_agents[0].hooper.team_id
            ),
            ball_handler_id=handler.hooper.id,
            defender_id=stealer.hooper.id,
            action="turnover",
            result="turnover",
            defensive_scheme=scheme,
            home_score=game_state.home_score,
            away_score=game_state.away_score,
        )
        return PossessionResult(
            turnover=True,
            scoring_team_home=game_state.home_has_ball,
            defensive_scheme=scheme,
            time_used=time_used,
            log=log,
        )

    # 3b. Check shot clock violation (defense shuts down the offense)
    if check_shot_clock_violation(handler, scheme, rng):
        handler.turnovers += 1
        drain_stamina(
            offense, scheme, is_defense=False, rules=rules,
            pace_modifier=pace,
            is_away=offense_is_away, altitude_ft=altitude,
            surface_stamina_multiplier=surface_mods.stamina_drain_multiplier,
        )
        drain_stamina(
            defense, scheme, is_defense=True, rules=rules,
            defensive_intensity=def_intensity, pace_modifier=pace,
            is_away=not offense_is_away, altitude_ft=altitude,
            surface_stamina_multiplier=surface_mods.stamina_drain_multiplier,
        )

        # Update cross-possession tracking
        game_state.last_action = "shot_clock_violation"
        game_state.last_result = "turnover"
        game_state.consecutive_makes = 0
        game_state.consecutive_misses += 1

        log = PossessionLog(
            quarter=game_state.quarter,
            possession_number=game_state.possession_number,
            offense_team_id=(
                game_state.home_agents[0].hooper.team_id
                if game_state.home_has_ball
                else game_state.away_agents[0].hooper.team_id
            ),
            ball_handler_id=handler.hooper.id,
            action="shot_clock_violation",
            result="turnover",
            defensive_scheme=scheme,
            home_score=game_state.home_score,
            away_score=game_state.away_score,
        )
        return PossessionResult(
            turnover=True,
            scoring_team_home=game_state.home_has_ball,
            defensive_scheme=scheme,
            time_used=time_used,
            log=log,
        )

    # 4. Select action --- with effect biases + surface modifiers
    shot_type = select_action(
        handler, game_state, rules, rng, effect_biases=ctx, surface=surface_mods,
        action_registry=action_registry,
    )

    # 4b. Flow control from governance effects
    if ctx.block_action:
        game_state.last_action = "blocked_by_effect"
        game_state.last_result = "turnover"
        game_state.consecutive_makes = 0
        game_state.consecutive_misses += 1
        drain_stamina(
            offense, scheme, is_defense=False, rules=rules, pace_modifier=pace,
            is_away=offense_is_away, altitude_ft=altitude,
            surface_stamina_multiplier=surface_mods.stamina_drain_multiplier,
        )
        drain_stamina(
            defense, scheme, is_defense=True, rules=rules,
            defensive_intensity=def_intensity, pace_modifier=pace,
            is_away=not offense_is_away, altitude_ft=altitude,
            surface_stamina_multiplier=surface_mods.stamina_drain_multiplier,
        )
        team_id = (
            game_state.home_agents[0].hooper.team_id
            if game_state.home_has_ball
            else game_state.away_agents[0].hooper.team_id
        )
        log = PossessionLog(
            quarter=game_state.quarter,
            possession_number=game_state.possession_number,
            offense_team_id=team_id,
            ball_handler_id=handler.hooper.id,
            action="blocked_by_effect",
            result="turnover",
            defensive_scheme=scheme,
            home_score=game_state.home_score,
            away_score=game_state.away_score,
        )
        return PossessionResult(
            turnover=True,
            scoring_team_home=game_state.home_has_ball,
            defensive_scheme=scheme,
            time_used=time_used,
            log=log,
        )
    if ctx.substitute_action and ctx.substitute_action in (
        "at_rim",
        "mid_range",
        "three_point",
    ):
        shot_type = ctx.substitute_action  # type: ignore[assignment]

    # 5. Check offensive moves (ball handler)
    triggered = get_triggered_moves(
        handler,
        shot_type,
        last_possession_three,
        game_state.elam_activated,
        rng,
    )

    # 6. Find primary defender
    primary_defender = get_primary_defender(handler, matchups, defense)

    # 6b. Check defensive moves (primary defender)
    def_triggered = get_triggered_moves(
        primary_defender,
        shot_type,
        last_possession_three,
        game_state.elam_activated,
        rng,
    )

    # 7. Compute base shot probability and apply all modifiers
    from pinwheel.core.scoring import (
        compute_shot_probability,
        compute_shot_probability_v2,
        points_for_action,
        points_for_shot,
    )

    move_name = ""
    score_diff = abs(game_state.home_score - game_state.away_score)

    # Look up the ActionDefinition when registry is available
    action_def = action_registry.get(shot_type) if action_registry is not None else None

    if action_def is not None:
        base_prob = compute_shot_probability_v2(
            handler, primary_defender, action_def, scheme_mod, rules,
            score_differential=score_diff,
        )
    else:
        base_prob = compute_shot_probability(
            handler, primary_defender, shot_type, scheme_mod, rules,
            score_differential=score_diff,
        )
    # Apply effect-driven shot probability modifier + home crowd boost + surface
    prob = max(
        0.01,
        min(
            0.99,
            base_prob
            + ctx.shot_probability_modifier
            + home_crowd_shot_mod
            + surface_mods.shot_probability_modifier,
        ),
    )

    # 7a. Apply offensive move modifier (first triggered move wins)
    if triggered:
        move = triggered[0]
        move_name = move.name
        handler.moves_activated.append(move_name)
        prob = apply_move_modifier(move, prob, rng)

    # 7b. Apply defensive move modifiers --- these stack on top of offensive moves
    for def_move in def_triggered:
        primary_defender.moves_activated.append(def_move.name)
        prob = apply_move_modifier(def_move, prob, rng)
        if not move_name:
            move_name = def_move.name

    # 7c. Resolve the shot
    made = rng.random() < prob
    if action_def is not None:
        pts = points_for_action(action_def, rules) if made else 0
    else:
        pts = points_for_shot(shot_type, rules) if made else 0

    # Apply effect-driven shot value modifier and pass bonus
    if made:
        pts += ctx.shot_value_modifier + ctx.bonus_pass_count

    # 8. Update shooter stats
    handler.field_goals_attempted += 1
    if shot_type == "three_point":
        handler.three_pointers_attempted += 1
    if made:
        handler.field_goals_made += 1
        handler.points += pts
        if shot_type == "three_point":
            handler.three_pointers_made += 1

    # 9. Check foul
    foul_on_defender = False
    fouling_id = ""
    if check_foul(
        primary_defender, shot_type, scheme, rng,
        rules=rules, defensive_intensity=def_intensity,
    ):
        foul_on_defender = True
        primary_defender.fouls += 1
        fouling_id = primary_defender.hooper.id

        # Track team fouls for bonus threshold
        if game_state.home_has_ball:
            # Defender is on the away team
            game_state.away_team_fouls += 1
            defense_team_fouls = game_state.away_team_fouls
        else:
            # Defender is on the home team
            game_state.home_team_fouls += 1
            defense_team_fouls = game_state.home_team_fouls

        if primary_defender.fouls >= rules.personal_foul_limit:
            primary_defender.ejected = True

        # Free throws on foul
        if not made:
            if action_def is not None:
                ft_attempts = action_def.free_throw_attempts_on_foul
            else:
                ft_attempts = 2 if shot_type != "three_point" else 3
            # team_foul_bonus_threshold: when team fouls exceed threshold,
            # award bonus free throws (1 extra FT regardless of shot type)
            if defense_team_fouls >= rules.team_foul_bonus_threshold:
                ft_attempts += 1
            # Use v2 free throw resolution when registry is available
            ft_action_def = (
                action_registry.get("free_throw")
                if action_registry is not None
                else None
            )
            for _ in range(ft_attempts):
                if ft_action_def is not None:
                    from pinwheel.core.scoring import resolve_shot_v2

                    ft_made, ft_pts = resolve_shot_v2(
                        handler, primary_defender, ft_action_def, 0.0, rules, rng
                    )
                else:
                    ft_made, ft_pts = resolve_shot(
                        handler, primary_defender, "free_throw", 0.0, rules, rng
                    )
                handler.free_throws_attempted += 1
                if ft_made:
                    handler.free_throws_made += 1
                    handler.points += ft_pts
                    pts += ft_pts

    # 10. Rebound on miss
    rebound_id = ""
    is_offensive_rebound = False
    assist_id = ""
    if not made and not foul_on_defender:
        rebounder, is_offensive_rebound = attempt_rebound(offense, defense, rng, rules=rules)
        rebounder.rebounds += 1
        rebound_id = rebounder.hooper.id

    # 11. Assist credit (simplified: random teammate if made)
    if made and len(offense) > 1:
        teammates = [a for a in offense if a.hooper.id != handler.hooper.id]
        if teammates:
            assister = rng.choice(teammates)
            assister.assists += 1
            assist_id = assister.hooper.id

    # 12. Update score
    if pts > 0:
        if game_state.home_has_ball:
            game_state.home_score += pts
        else:
            game_state.away_score += pts

    # 13. Drain stamina --- with extra drain from effects + home court + surface modifiers
    drain_stamina(
        offense, scheme, is_defense=False, rules=rules,
        pace_modifier=pace,
        is_away=offense_is_away, altitude_ft=altitude,
        surface_stamina_multiplier=surface_mods.stamina_drain_multiplier,
    )
    drain_stamina(
        defense, scheme, is_defense=True, rules=rules,
        defensive_intensity=def_intensity, pace_modifier=pace,
        is_away=not offense_is_away, altitude_ft=altitude,
        surface_stamina_multiplier=surface_mods.stamina_drain_multiplier,
    )
    # Extra stamina drain on ball handler from effects
    if ctx.extra_stamina_drain > 0.0:
        handler.current_stamina = max(
            0.15, handler.current_stamina - ctx.extra_stamina_drain
        )

    # 14. Update cross-possession tracking
    game_state.last_action = shot_type
    if made:
        game_state.last_result = "made"
        game_state.consecutive_makes += 1
        game_state.consecutive_misses = 0
    else:
        game_state.last_result = "missed"
        game_state.consecutive_makes = 0
        game_state.consecutive_misses += 1

    # Build log
    team_id = (
        game_state.home_agents[0].hooper.team_id
        if game_state.home_has_ball
        else game_state.away_agents[0].hooper.team_id
    )
    log = PossessionLog(
        quarter=game_state.quarter,
        possession_number=game_state.possession_number,
        offense_team_id=team_id,
        ball_handler_id=handler.hooper.id,
        action=shot_type,
        result="made" if made else ("foul" if foul_on_defender else "missed"),
        points_scored=pts,
        defender_id=primary_defender.hooper.id,
        assist_id=assist_id,
        rebound_id=rebound_id,
        is_offensive_rebound=is_offensive_rebound,
        move_activated=move_name,
        defensive_scheme=scheme,
        home_score=game_state.home_score,
        away_score=game_state.away_score,
    )

    return PossessionResult(
        points_scored=pts,
        scoring_team_home=game_state.home_has_ball,
        turnover=False,
        foul_on_defender=foul_on_defender,
        fouling_agent_id=fouling_id,
        shooter_id=handler.hooper.id,
        assist_id=assist_id,
        rebound_id=rebound_id,
        is_offensive_rebound=is_offensive_rebound,
        shot_type=shot_type,
        shot_made=made,
        move_activated=move_name,
        defensive_scheme=scheme,
        time_used=time_used,
        log=log,
    )
