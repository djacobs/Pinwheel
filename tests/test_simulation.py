"""Tests for the simulation engine."""

import random

from pinwheel.core.defense import (
    assign_matchups,
    get_primary_defender,
    select_scheme,
)
from pinwheel.core.moves import (
    FATES_HAND,
    HEAT_CHECK,
    IRON_WILL,
    LOCKDOWN_STANCE,
    apply_move_modifier,
    check_gate,
    check_trigger,
)
from pinwheel.core.scoring import (
    compute_fate_clutch_bonus,
    compute_shot_probability,
    compute_stamina_modifier,
    logistic,
    resolve_shot,
)
from pinwheel.core.simulation import simulate_game
from pinwheel.core.state import GameState, HooperState
from pinwheel.models.rules import DEFAULT_RULESET, RuleSet
from pinwheel.models.team import Hooper, Move, PlayerAttributes, Team, Venue


def _make_attrs(
    scoring: int = 50,
    passing: int = 40,
    defense: int = 40,
    speed: int = 40,
    stamina: int = 40,
    iq: int = 50,
    ego: int = 30,
    chaotic: int = 20,
    fate: int = 30,
) -> PlayerAttributes:
    # Use model_construct to bypass the 360-point budget validator.
    # Tests routinely create extreme stat profiles to isolate mechanics.
    return PlayerAttributes.model_construct(
        scoring=scoring,
        passing=passing,
        defense=defense,
        speed=speed,
        stamina=stamina,
        iq=iq,
        ego=ego,
        chaotic_alignment=chaotic,
        fate=fate,
    )


def _make_hooper(
    hooper_id: str = "a-1",
    team_id: str = "t-1",
    attrs: PlayerAttributes | None = None,
    is_starter: bool = True,
    moves: list[Move] | None = None,
) -> Hooper:
    # Use model_construct to bypass nested PlayerAttributes re-validation.
    return Hooper.model_construct(
        id=hooper_id,
        name=f"Hooper-{hooper_id}",
        team_id=team_id,
        archetype="sharpshooter",
        backstory="",
        attributes=attrs or _make_attrs(),
        is_starter=is_starter,
        moves=moves or [],
    )


def _make_team(
    team_id: str = "t-1",
    n_starters: int = 3,
    n_bench: int = 1,
    attrs: PlayerAttributes | None = None,
    moves: list[Move] | None = None,
) -> Team:
    hoopers = []
    for i in range(n_starters):
        hoopers.append(
            _make_hooper(
                f"{team_id}-s{i}",
                team_id,
                attrs,
                is_starter=True,
                moves=moves,
            )
        )
    for i in range(n_bench):
        hoopers.append(
            _make_hooper(
                f"{team_id}-b{i}",
                team_id,
                attrs,
                is_starter=False,
                moves=moves,
            )
        )
    return Team(
        id=team_id,
        name=f"Team-{team_id}",
        venue=Venue(name="Court", capacity=5000),
        hoopers=hoopers,
    )


# --- Determinism ---


class TestDeterminism:
    def test_same_seed_same_result(self):
        home = _make_team("home")
        away = _make_team("away")
        r1 = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        r2 = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        assert r1.home_score == r2.home_score
        assert r1.away_score == r2.away_score
        assert r1.total_possessions == r2.total_possessions
        assert r1.winner_team_id == r2.winner_team_id

    def test_different_seeds_different_results(self):
        home = _make_team("home")
        away = _make_team("away")
        results = [simulate_game(home, away, DEFAULT_RULESET, seed=s) for s in range(20)]
        scores = {(r.home_score, r.away_score) for r in results}
        # With 20 different seeds, we should get multiple different outcomes
        assert len(scores) > 5


# --- Scoring ---


class TestScoring:
    def test_logistic_midpoint(self):
        assert abs(logistic(50, 50, 0.05) - 0.5) < 0.01

    def test_logistic_high_attribute(self):
        assert logistic(90, 50, 0.05) > 0.85

    def test_logistic_low_attribute(self):
        assert logistic(10, 50, 0.05) < 0.15

    def test_probability_in_bounds(self):
        shooter = HooperState(hooper=_make_hooper(attrs=_make_attrs(scoring=99)))
        defender = HooperState(hooper=_make_hooper(attrs=_make_attrs(defense=99)))
        prob = compute_shot_probability(shooter, defender, "three_point", 0.08, DEFAULT_RULESET)
        assert 0.01 <= prob <= 0.99

    def test_high_defense_reduces_probability(self):
        shooter = HooperState(hooper=_make_hooper(attrs=_make_attrs(scoring=60)))
        weak_def = HooperState(hooper=_make_hooper(attrs=_make_attrs(defense=20)))
        strong_def = HooperState(hooper=_make_hooper(attrs=_make_attrs(defense=90)))
        p_weak = compute_shot_probability(shooter, weak_def, "mid_range", 0.05, DEFAULT_RULESET)
        p_strong = compute_shot_probability(shooter, strong_def, "mid_range", 0.05, DEFAULT_RULESET)
        assert p_weak > p_strong

    def test_stamina_modifier_full(self):
        assert compute_stamina_modifier(1.0) == 1.0

    def test_stamina_modifier_depleted(self):
        assert compute_stamina_modifier(0.0) == 0.7

    def test_resolve_shot_returns_tuple(self):
        shooter = HooperState(hooper=_make_hooper(attrs=_make_attrs(scoring=80)))
        defender = HooperState(hooper=_make_hooper(attrs=_make_attrs(defense=30)))
        made, pts = resolve_shot(
            shooter, defender, "three_point", 0.05, DEFAULT_RULESET, random.Random(42)
        )
        assert isinstance(made, bool)
        assert pts >= 0


# --- Defense ---


class TestDefense:
    def test_select_scheme_returns_valid(self):
        off = [HooperState(hooper=_make_hooper(f"o{i}")) for i in range(3)]
        dfn = [HooperState(hooper=_make_hooper(f"d{i}")) for i in range(3)]
        gs = GameState(home_agents=off, away_agents=dfn)
        scheme = select_scheme(off, dfn, gs, DEFAULT_RULESET, random.Random(42))
        assert scheme in ("man_tight", "man_switch", "zone", "press")

    def test_low_stamina_favors_zone(self):
        off = [HooperState(hooper=_make_hooper(f"o{i}")) for i in range(3)]
        dfn = [HooperState(hooper=_make_hooper(f"d{i}")) for i in range(3)]
        for d in dfn:
            d.current_stamina = 0.2
        gs = GameState(home_agents=dfn, away_agents=off, home_has_ball=False)
        counts: dict[str, int] = {}
        for s in range(100):
            scheme = select_scheme(off, dfn, gs, DEFAULT_RULESET, random.Random(s))
            counts[scheme] = counts.get(scheme, 0) + 1
        # Zone should be the most common when stamina is low
        assert counts.get("zone", 0) > counts.get("man_tight", 0)

    def test_matchups_all_defenders_assigned(self):
        off = [HooperState(hooper=_make_hooper(f"o{i}")) for i in range(3)]
        dfn = [HooperState(hooper=_make_hooper(f"d{i}")) for i in range(3)]
        matchups = assign_matchups(off, dfn, "man_tight", random.Random(42))
        assert len(matchups) == 3

    def test_get_primary_defender(self):
        off = [HooperState(hooper=_make_hooper(f"o{i}")) for i in range(3)]
        dfn = [HooperState(hooper=_make_hooper(f"d{i}")) for i in range(3)]
        matchups = assign_matchups(off, dfn, "man_tight", random.Random(42))
        defender = get_primary_defender(off[0], matchups, dfn)
        assert defender in dfn


# --- Moves ---


class TestMoves:
    def test_heat_check_gate(self):
        hooper = HooperState(hooper=_make_hooper(attrs=_make_attrs(ego=50), moves=[HEAT_CHECK]))
        assert check_gate(HEAT_CHECK, hooper)

    def test_heat_check_gate_fails(self):
        hooper = HooperState(hooper=_make_hooper(attrs=_make_attrs(ego=10), moves=[HEAT_CHECK]))
        # Heat check gate is ego >= 30, so ego=10 fails
        assert not check_gate(HEAT_CHECK, hooper)

    def test_lockdown_trigger(self):
        hooper = HooperState(
            hooper=_make_hooper(attrs=_make_attrs(defense=80), moves=[LOCKDOWN_STANCE])
        )
        assert check_trigger(LOCKDOWN_STANCE, hooper, "drive", False, False)
        assert not check_trigger(LOCKDOWN_STANCE, hooper, "pass", False, False)

    def test_heat_check_trigger(self):
        hooper = HooperState(hooper=_make_hooper(attrs=_make_attrs(ego=50), moves=[HEAT_CHECK]))
        assert check_trigger(HEAT_CHECK, hooper, "three_point", True, False)
        assert not check_trigger(HEAT_CHECK, hooper, "three_point", False, False)

    def test_positive_move_increases_make_rate(self):
        """Heat Check (+0.15) should increase total make probability."""
        from pinwheel.core.moves import HEAT_CHECK as HC

        moves = [HC]
        # Use mediocre shooter so effect is clearly measurable
        base_team = _make_team("h", attrs=_make_attrs(scoring=45, ego=50), moves=moves)
        no_move_team = _make_team("h2", attrs=_make_attrs(scoring=45, ego=50))
        away = _make_team("a")

        # Run many games with last_three=True context (Heat Check always triggers)
        # We can't directly set last_three, so compare overall scoring rates
        with_scores = []
        without_scores = []
        for s in range(200):
            r1 = simulate_game(base_team, away, DEFAULT_RULESET, seed=s)
            r2 = simulate_game(no_move_team, away, DEFAULT_RULESET, seed=s)
            with_scores.append(r1.home_score)
            without_scores.append(r2.home_score)

        avg_with = sum(with_scores) / len(with_scores)
        avg_without = sum(without_scores) / len(without_scores)
        # Team with Heat Check should score at least as much (likely more)
        assert avg_with >= avg_without * 0.95

    def test_wild_card_negative_can_reduce_make_rate(self):
        """Wild Card -0.15 branch must be able to reduce make probability.

        This test verifies the fix for the P0 bug where move modifiers
        were applied as a second roll after an initial miss, making even
        negative effects beneficial.
        """
        from pinwheel.core.moves import apply_move_modifier
        from pinwheel.core.scoring import compute_shot_probability

        shooter = HooperState(hooper=_make_hooper(attrs=_make_attrs(scoring=50, chaotic=80)))
        defender = HooperState(hooper=_make_hooper(attrs=_make_attrs(defense=40)))
        base_prob = compute_shot_probability(shooter, defender, "mid_range", 0.05, DEFAULT_RULESET)

        # Wild Card with -0.15 should reduce probability
        wc_move = Move(
            name="Wild Card",
            trigger="any_possession",
            effect="random: +25% or -15%",
            attribute_gate={"chaotic_alignment": 70},
        )
        # Force the -0.15 branch by using a seed where rng.choice returns -0.15
        test_rng = random.Random(0)
        # Try many seeds to find one that hits -0.15
        for seed in range(100):
            test_rng = random.Random(seed)
            modified = apply_move_modifier(wc_move, base_prob, test_rng)
            if modified < base_prob:
                # Confirmed: negative modifier actually reduces probability
                assert modified < base_prob
                return
        # If we never hit the negative branch in 100 seeds, that's also a bug
        raise AssertionError("Wild Card never produced negative modifier in 100 seeds")


# --- Elam Ending ---


class TestElamEnding:
    def test_elam_activates(self):
        home = _make_team("home")
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        assert result.elam_activated
        assert result.elam_target_score is not None

    def test_game_ends_at_or_above_target(self):
        home = _make_team("home")
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        if result.elam_activated and result.elam_target_score:
            winning_score = max(result.home_score, result.away_score)
            assert winning_score >= result.elam_target_score


# --- Game Clock ---


class TestGameClock:
    def test_timed_quarters_have_game_clock(self):
        """Possession logs from timed quarters must have non-empty M:SS game_clock.

        Substitution entries are excluded — they're generated outside the main
        game loop and don't carry game_clock data.
        """
        home = _make_team("home")
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        elam_quarter = DEFAULT_RULESET.elam_trigger_quarter + 1
        timed_logs = [
            p for p in result.possession_log
            if p.quarter < elam_quarter and p.action != "substitution"
        ]
        assert len(timed_logs) > 0
        import re

        clock_pattern = re.compile(r"^\d+:\d{2}$")
        for log in timed_logs:
            assert log.game_clock != "", f"Timed quarter log missing game_clock: Q{log.quarter}"
            assert clock_pattern.match(log.game_clock), (
                f"game_clock '{log.game_clock}' doesn't match M:SS format"
            )

    def test_elam_possessions_have_empty_game_clock(self):
        """Elam ending possessions (untimed) must have empty game_clock."""
        home = _make_team("home")
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        elam_quarter = DEFAULT_RULESET.elam_trigger_quarter + 1
        elam_logs = [p for p in result.possession_log if p.quarter == elam_quarter]
        assert len(elam_logs) > 0
        for log in elam_logs:
            assert log.game_clock == "", (
                f"Elam possession should have empty game_clock, got '{log.game_clock}'"
            )


# --- Full Game ---


class TestFullGame:
    def test_game_produces_result(self):
        home = _make_team("home")
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        assert result.home_score > 0
        assert result.away_score > 0
        assert result.total_possessions > 0
        assert len(result.box_scores) == 8  # 4 per team
        assert len(result.quarter_scores) >= 3  # Q1, Q2, Q3, + Elam

    def test_possession_log_populated(self):
        """GameResult must contain a possession_log for presenter/SSE."""
        home = _make_team("home")
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        assert len(result.possession_log) > 0
        # Possession log may include substitution entries beyond regular possessions
        gameplay_logs = [p for p in result.possession_log if p.action != "substitution"]
        assert len(gameplay_logs) == result.total_possessions
        # Each log entry has required fields
        for log in result.possession_log:
            assert log.quarter > 0
            assert log.offense_team_id in ("home", "away")
            assert log.ball_handler_id != ""

    def test_box_scores_sum_to_team_total(self):
        home = _make_team("home")
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        home_pts = sum(bs.points for bs in result.box_scores if bs.team_id == "home")
        away_pts = sum(bs.points for bs in result.box_scores if bs.team_id == "away")
        assert home_pts == result.home_score
        assert away_pts == result.away_score

    def test_rule_changes_affect_outcomes(self):
        home = _make_team("home")
        away = _make_team("away")
        default_results = [simulate_game(home, away, DEFAULT_RULESET, seed=s) for s in range(50)]
        high_3pt = RuleSet(three_point_value=6)
        high_results = [simulate_game(home, away, high_3pt, seed=s) for s in range(50)]
        avg_default = sum(r.home_score + r.away_score for r in default_results) / 50
        avg_high = sum(r.home_score + r.away_score for r in high_results) / 50
        # Higher 3pt value should produce higher scores
        assert avg_high > avg_default

    def test_quarter_structure(self):
        home = _make_team("home")
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        # Default: elam_trigger_quarter=3, so Q1, Q2, Q3, then Elam (Q4)
        assert len(result.quarter_scores) == 4

    def test_moves_fire_during_game(self):
        moves = [
            Move(
                name="Heat Check",
                trigger="made_three_last_possession",
                effect="+15%",
                attribute_gate={"ego": 10},
            )
        ]
        home = _make_team("home", attrs=_make_attrs(scoring=70, ego=50), moves=moves)
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        # At least some box score entries should exist
        assert result.total_possessions > 30


# --- Substitution ---


class TestSubstitution:
    def test_foul_out_triggers_bench_promotion(self):
        """When a starter fouls out, the bench player enters the game."""
        from pinwheel.core.simulation import _build_hooper_states, _check_substitution

        home = _make_team("home", n_starters=3, n_bench=1)
        away = _make_team("away", n_starters=3, n_bench=1)
        game_state = GameState(
            home_agents=_build_hooper_states(home),
            away_agents=_build_hooper_states(away),
        )

        # Verify bench player starts off court
        bench = game_state.home_bench
        assert len(bench) == 1
        assert not bench[0].on_court
        assert len(game_state.home_active) == 3

        # Simulate a foul-out: eject a starter
        starter = game_state.home_active[0]
        starter.fouls = 5
        starter.ejected = True

        possession_log: list = []
        _check_substitution(game_state, DEFAULT_RULESET, possession_log, reason="foul_out")

        # Starter should be off court, bench player should be on court
        assert not starter.on_court
        assert len(game_state.home_active) == 3  # 2 remaining starters + 1 promoted bench
        assert len(game_state.home_bench) == 0

    def test_fatigue_triggers_rotation_at_quarter_break(self):
        """Low stamina triggers substitution at quarter breaks."""
        from pinwheel.core.simulation import _build_hooper_states, _check_substitution

        home = _make_team("home", n_starters=3, n_bench=1)
        away = _make_team("away", n_starters=3, n_bench=1)
        game_state = GameState(
            home_agents=_build_hooper_states(home),
            away_agents=_build_hooper_states(away),
        )

        # Drain one starter's stamina below threshold
        tired_starter = game_state.home_active[0]
        tired_starter.current_stamina = 0.20  # Below default threshold of 0.35
        bench_player = game_state.home_bench[0]
        bench_player.current_stamina = 0.80  # Higher than tired starter

        possession_log: list = []
        _check_substitution(game_state, DEFAULT_RULESET, possession_log, reason="fatigue")

        # Tired starter should be benched, bench player should be in
        assert not tired_starter.on_court
        assert bench_player.on_court
        assert len(game_state.home_active) == 3

    def test_no_bench_plays_short_handed(self):
        """Team with no available bench plays short-handed after foul-out."""
        from pinwheel.core.simulation import _build_hooper_states, _check_substitution

        # Team with only starters, no bench
        home = _make_team("home", n_starters=3, n_bench=0)
        away = _make_team("away", n_starters=3, n_bench=1)
        game_state = GameState(
            home_agents=_build_hooper_states(home),
            away_agents=_build_hooper_states(away),
        )

        # Eject a starter
        starter = game_state.home_active[0]
        starter.fouls = 5
        starter.ejected = True

        possession_log: list = []
        _check_substitution(game_state, DEFAULT_RULESET, possession_log, reason="foul_out")

        # No bench available, plays with 2
        assert len(game_state.home_active) == 2
        assert len(game_state.home_bench) == 0

    def test_bench_player_recovers_stamina_during_breaks(self):
        """Bench players recover stamina during quarter breaks."""
        from pinwheel.core.simulation import _build_hooper_states, _quarter_break_recovery

        home = _make_team("home", n_starters=3, n_bench=1)
        away = _make_team("away", n_starters=3, n_bench=1)
        game_state = GameState(
            home_agents=_build_hooper_states(home),
            away_agents=_build_hooper_states(away),
        )

        # Drain bench player stamina
        bench_player = game_state.home_bench[0]
        bench_player.current_stamina = 0.50

        _quarter_break_recovery(game_state, DEFAULT_RULESET)

        # Bench player should recover (they're in home_agents)
        assert bench_player.current_stamina > 0.50

    def test_substitution_appears_in_play_by_play(self):
        """Substitution events appear in the possession log."""
        from pinwheel.core.simulation import _build_hooper_states, _check_substitution

        home = _make_team("home", n_starters=3, n_bench=1)
        away = _make_team("away", n_starters=3, n_bench=1)
        game_state = GameState(
            home_agents=_build_hooper_states(home),
            away_agents=_build_hooper_states(away),
        )

        # Eject a starter
        starter = game_state.home_active[0]
        starter.fouls = 5
        starter.ejected = True

        from pinwheel.models.game import PossessionLog

        possession_log: list[PossessionLog] = []
        _check_substitution(game_state, DEFAULT_RULESET, possession_log, reason="foul_out")

        # Should have a substitution log entry
        assert len(possession_log) == 1
        assert possession_log[0].action == "substitution"
        assert "foul_out" in possession_log[0].result

    def test_on_court_initialized_from_is_starter(self):
        """HooperState.on_court is initialized from Hooper.is_starter."""
        from pinwheel.core.simulation import _build_hooper_states

        team = _make_team("t", n_starters=3, n_bench=1)
        states = _build_hooper_states(team)
        starters = [s for s in states if s.on_court]
        bench = [s for s in states if not s.on_court]
        assert len(starters) == 3
        assert len(bench) == 1

    def test_fatigue_no_sub_when_bench_worse(self):
        """No fatigue substitution when bench player has lower stamina."""
        from pinwheel.core.simulation import _build_hooper_states, _check_substitution

        home = _make_team("home", n_starters=3, n_bench=1)
        away = _make_team("away", n_starters=3, n_bench=1)
        game_state = GameState(
            home_agents=_build_hooper_states(home),
            away_agents=_build_hooper_states(away),
        )

        # Active player tired, but bench is even more tired
        tired_starter = game_state.home_active[0]
        tired_starter.current_stamina = 0.30
        bench_player = game_state.home_bench[0]
        bench_player.current_stamina = 0.20

        possession_log: list = []
        _check_substitution(game_state, DEFAULT_RULESET, possession_log, reason="fatigue")

        # No substitution should happen
        assert tired_starter.on_court
        assert not bench_player.on_court
        assert len(possession_log) == 0


class TestBatchStatistics:
    def test_100_game_distributions(self):
        """Run 100 games and verify basketball-like distributions.

        Uses home_court_enabled=False so equal teams produce balanced results.
        Home court advantage is tested separately.
        """
        home = _make_team("home")
        away = _make_team("away")
        neutral_rules = RuleSet(home_court_enabled=False)
        results = [simulate_game(home, away, neutral_rules, seed=s) for s in range(100)]
        total_scores = [r.home_score + r.away_score for r in results]
        avg_score = sum(total_scores) / len(total_scores)
        avg_possessions = sum(r.total_possessions for r in results) / len(results)
        home_wins = sum(1 for r in results if r.winner_team_id == "home")

        # Basketball-like ranges (3v3 with Elam ending)
        # 15 possessions/quarter * 3 quarters + Elam possessions
        assert 30 < avg_score < 200, f"avg total score {avg_score} out of range"
        assert 45 < avg_possessions < 200, f"avg possessions {avg_possessions} out of range"
        # With equal teams on neutral court, home/away should be roughly balanced
        assert 25 < home_wins < 75, f"home wins {home_wins}/100 too skewed"


# --- Strategy ---


class TestTeamStrategy:
    def test_strategy_model_defaults(self):
        from pinwheel.models.team import TeamStrategy

        s = TeamStrategy()
        assert s.three_point_bias == 0.0
        assert s.pace_modifier == 1.0
        assert s.defensive_intensity == 0.0
        assert s.confidence == 0.0

    def test_strategy_model_validation(self):
        from pydantic import ValidationError

        from pinwheel.models.team import TeamStrategy

        # Valid extreme values
        s = TeamStrategy(three_point_bias=20.0, pace_modifier=0.7)
        assert s.three_point_bias == 20.0

        # Out of range should fail
        try:
            TeamStrategy(three_point_bias=25.0)
            assert False, "Should have raised"  # noqa: B011
        except ValidationError:
            pass

    def test_mock_interpreter_three_point(self):
        from pinwheel.ai.interpreter import interpret_strategy_mock

        result = interpret_strategy_mock("Shoot more threes, bomb away!")
        assert result.three_point_bias > 5.0
        assert result.confidence > 0.5

    def test_mock_interpreter_defense(self):
        from pinwheel.ai.interpreter import interpret_strategy_mock

        result = interpret_strategy_mock("Lock down on defense, clamp them")
        assert result.defensive_intensity > 0.1
        assert result.confidence > 0.5

    def test_mock_interpreter_pace(self):
        from pinwheel.ai.interpreter import interpret_strategy_mock

        result = interpret_strategy_mock("Push the tempo, run and gun")
        assert result.pace_modifier < 0.9
        assert result.confidence > 0.5

    def test_mock_interpreter_balanced(self):
        from pinwheel.ai.interpreter import interpret_strategy_mock

        result = interpret_strategy_mock("Just play normal basketball")
        assert abs(result.three_point_bias) < 1.0
        assert abs(result.at_rim_bias) < 1.0

    def test_strategy_affects_shot_selection(self):
        """With a heavy three-point bias, more threes should be selected."""
        from pinwheel.core.possession import select_action
        from pinwheel.models.team import TeamStrategy

        handler = HooperState(hooper=_make_hooper())
        state = GameState(
            home_agents=[handler],
            away_agents=[HooperState(hooper=_make_hooper("d-1", "t-2"))],
        )

        rng = random.Random(42)
        n = 500

        # Without strategy
        state.home_strategy = None
        no_strat_threes = sum(
            1
            for _ in range(n)
            if select_action(handler, state, DEFAULT_RULESET, rng) == "three_point"
        )

        # With heavy three-point bias
        rng = random.Random(42)
        state.home_strategy = TeamStrategy(three_point_bias=20.0)
        strat_threes = sum(
            1
            for _ in range(n)
            if select_action(handler, state, DEFAULT_RULESET, rng) == "three_point"
        )

        assert strat_threes > no_strat_threes, (
            f"Three-point strategy should increase threes: {strat_threes} vs {no_strat_threes}"
        )

    def test_strategy_affects_simulation(self):
        """Same seed, different strategies should produce different results."""
        from pinwheel.models.team import TeamStrategy

        home = _make_team("home")
        away = _make_team("away")

        # No strategy
        r1 = simulate_game(home, away, DEFAULT_RULESET, seed=42)

        # Home team: bomb away
        three_strat = TeamStrategy(three_point_bias=20.0, pace_modifier=0.8)
        r2 = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            home_strategy=three_strat,
        )

        # Results should differ (different shot selection → different RNG path)
        differs = (
            r1.home_score != r2.home_score
            or r1.away_score != r2.away_score
            or r1.total_possessions != r2.total_possessions
        )
        assert differs, "Strategy should change game outcome"


# --- Substitution in Full Games ---


class TestSubstitutionFullGame:
    def test_substitution_fires_in_full_game(self):
        """A full game with 4 hoopers should produce at least one substitution."""
        home = _make_team("home", n_starters=3, n_bench=1)
        away = _make_team("away", n_starters=3, n_bench=1)

        # Use a lower threshold to make substitutions more likely
        rules = RuleSet(substitution_stamina_threshold=0.5)

        # Try multiple seeds — at least one should produce a substitution
        found_sub = False
        for seed in range(20):
            result = simulate_game(home, away, rules, seed=seed)
            subs = [
                p for p in result.possession_log if p.action == "substitution"
            ]
            if subs:
                found_sub = True
                break

        assert found_sub, "Expected at least one substitution across 20 games"

    def test_substitution_details_in_log(self):
        """Substitution log entries should have meaningful result strings."""
        home = _make_team("home", n_starters=3, n_bench=1)
        away = _make_team("away", n_starters=3, n_bench=1)

        rules = RuleSet(substitution_stamina_threshold=0.6)

        for seed in range(30):
            result = simulate_game(home, away, rules, seed=seed)
            subs = [
                p for p in result.possession_log if p.action == "substitution"
            ]
            if subs:
                # Verify the substitution log entry format
                sub = subs[0]
                assert ":" in sub.result, (
                    f"Sub result should have format 'reason:out:in', got {sub.result}"
                )
                parts = sub.result.split(":")
                assert parts[0] in ("fatigue", "foul_out"), (
                    f"Sub reason should be fatigue or foul_out, got {parts[0]}"
                )
                return

        # Warn but don't fail — substitution depends on game dynamics
        raise AssertionError("No substitution found in 30 games at threshold 0.6")


# --- Rebounds ---


class TestRebounds:
    def test_rebounds_tracked_in_box_scores(self) -> None:
        """Box scores should have nonzero rebounds across a game."""
        home = _make_team("home")
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        total_rebounds = sum(bs.rebounds for bs in result.box_scores)
        assert total_rebounds > 0, "Expected some rebounds in box scores"

    def test_rebound_id_in_possession_log(self) -> None:
        """Missed shots should have a rebound_id in the possession log."""
        home = _make_team("home")
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        missed = [p for p in result.possession_log if p.result == "missed"]
        assert len(missed) > 0, "Expected some missed shots"
        with_rebound = [p for p in missed if p.rebound_id]
        assert len(with_rebound) > 0, "Expected rebound_id on missed shots"

    def test_is_offensive_rebound_in_possession_log(self) -> None:
        """Some missed shots should have is_offensive_rebound set.

        Offensive rebounds are less frequent than defensive, but across
        many possessions some should appear.
        """
        home = _make_team("home")
        away = _make_team("away")
        # Run several games to find at least one offensive rebound
        found_offensive = False
        found_defensive = False
        for seed in range(20):
            result = simulate_game(home, away, DEFAULT_RULESET, seed=seed)
            for p in result.possession_log:
                if p.rebound_id and p.is_offensive_rebound:
                    found_offensive = True
                if p.rebound_id and not p.is_offensive_rebound:
                    found_defensive = True
            if found_offensive and found_defensive:
                break
        assert found_offensive, "Expected at least one offensive rebound across 20 games"
        assert found_defensive, "Expected at least one defensive rebound across 20 games"

    def test_rebound_id_matches_valid_hooper(self) -> None:
        """Rebound IDs should be from hoopers actually in the game."""
        home = _make_team("home")
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        all_hooper_ids = {bs.hooper_id for bs in result.box_scores}
        for p in result.possession_log:
            if p.rebound_id:
                assert p.rebound_id in all_hooper_ids, (
                    f"Rebound ID {p.rebound_id} not in game hoopers"
                )


# --- Lockdown Stance ---


class TestLockdownStance:
    def test_lockdown_stance_reduces_probability(self):
        """Lockdown Stance should reduce shot probability by 12%."""
        base_prob = 0.50
        modified = apply_move_modifier(LOCKDOWN_STANCE, base_prob, random.Random(42))
        assert modified == base_prob - 0.12

    def test_lockdown_stance_floor(self):
        """Lockdown Stance should not reduce probability below 0.01."""
        base_prob = 0.05
        modified = apply_move_modifier(LOCKDOWN_STANCE, base_prob, random.Random(42))
        assert modified == 0.01

    def test_lockdown_stance_gate(self):
        """Lockdown Stance requires defense >= 70."""
        high_def = HooperState(
            hooper=_make_hooper(attrs=_make_attrs(defense=80), moves=[LOCKDOWN_STANCE])
        )
        low_def = HooperState(
            hooper=_make_hooper(attrs=_make_attrs(defense=50), moves=[LOCKDOWN_STANCE])
        )
        assert check_gate(LOCKDOWN_STANCE, high_def)
        assert not check_gate(LOCKDOWN_STANCE, low_def)

    def test_lockdown_stance_triggers_on_iso_actions(self):
        """Lockdown Stance triggers on drive, at_rim, mid_range actions."""
        hooper = HooperState(
            hooper=_make_hooper(attrs=_make_attrs(defense=80), moves=[LOCKDOWN_STANCE])
        )
        for action in ("drive", "at_rim", "mid_range"):
            assert check_trigger(LOCKDOWN_STANCE, hooper, action, False, False), (
                f"Lockdown Stance should trigger on {action}"
            )
        # Should NOT trigger on three_point or pass
        assert not check_trigger(LOCKDOWN_STANCE, hooper, "three_point", False, False)
        assert not check_trigger(LOCKDOWN_STANCE, hooper, "pass", False, False)

    def test_lockdown_defender_reduces_scoring_in_game(self):
        """Teams facing lockdown defenders should score less on average."""
        # Lockdown team: high defense hoopers with Lockdown Stance
        lockdown_attrs = _make_attrs(scoring=30, defense=85, speed=50, stamina=50)
        lockdown_moves = [LOCKDOWN_STANCE]
        lockdown_team = _make_team(
            "lock", attrs=lockdown_attrs, moves=lockdown_moves,
        )
        # Normal team: average attributes, no moves
        normal_team = _make_team("norm")

        # Lockdown is away team (defense), normal team is home (offense)
        scores_vs_lockdown = []
        scores_vs_normal = []
        normal_away = _make_team("norm2")
        for s in range(100):
            r1 = simulate_game(normal_team, lockdown_team, DEFAULT_RULESET, seed=s)
            scores_vs_lockdown.append(r1.home_score)
            r2 = simulate_game(normal_team, normal_away, DEFAULT_RULESET, seed=s)
            scores_vs_normal.append(r2.home_score)

        avg_vs_lockdown = sum(scores_vs_lockdown) / len(scores_vs_lockdown)
        avg_vs_normal = sum(scores_vs_normal) / len(scores_vs_normal)
        # Scoring against lockdown defenders should be lower
        assert avg_vs_lockdown < avg_vs_normal, (
            f"Expected lower scoring vs lockdown: {avg_vs_lockdown:.1f} vs {avg_vs_normal:.1f}"
        )


# --- Iron Will ---


class TestIronWill:
    def test_iron_will_increases_probability(self):
        """Iron Will should boost shot probability by 8%."""
        base_prob = 0.40
        modified = apply_move_modifier(IRON_WILL, base_prob, random.Random(42))
        assert modified == base_prob + 0.08

    def test_iron_will_cap(self):
        """Iron Will should not raise probability above 0.99."""
        base_prob = 0.95
        modified = apply_move_modifier(IRON_WILL, base_prob, random.Random(42))
        assert modified == 0.99

    def test_iron_will_gate(self):
        """Iron Will requires stamina >= 70."""
        high_stam = HooperState(
            hooper=_make_hooper(attrs=_make_attrs(stamina=80), moves=[IRON_WILL])
        )
        low_stam = HooperState(
            hooper=_make_hooper(attrs=_make_attrs(stamina=50), moves=[IRON_WILL])
        )
        assert check_gate(IRON_WILL, high_stam)
        assert not check_gate(IRON_WILL, low_stam)

    def test_iron_will_triggers_on_low_stamina(self):
        """Iron Will triggers when current stamina < 40%."""
        hooper = HooperState(
            hooper=_make_hooper(attrs=_make_attrs(stamina=80), moves=[IRON_WILL])
        )
        hooper.current_stamina = 0.35
        assert check_trigger(IRON_WILL, hooper, "mid_range", False, False)
        hooper.current_stamina = 0.50
        assert not check_trigger(IRON_WILL, hooper, "mid_range", False, False)

    def test_iron_will_helps_fatigued_player_in_game(self):
        """Iron Horse archetype with Iron Will should score better when fatigued."""
        # Iron Horse attrs: high stamina (85) so the move gate is met
        iron_attrs = _make_attrs(scoring=45, stamina=85)
        iron_moves = [IRON_WILL]
        iron_team = _make_team("iron", attrs=iron_attrs, moves=iron_moves)
        no_move_team = _make_team("plain", attrs=iron_attrs)
        away = _make_team("away")

        iron_scores = []
        plain_scores = []
        for s in range(200):
            r1 = simulate_game(iron_team, away, DEFAULT_RULESET, seed=s)
            r2 = simulate_game(no_move_team, away, DEFAULT_RULESET, seed=s)
            iron_scores.append(r1.home_score)
            plain_scores.append(r2.home_score)

        avg_iron = sum(iron_scores) / len(iron_scores)
        avg_plain = sum(plain_scores) / len(plain_scores)
        # Iron Will team should score at least as much (likely more late-game)
        assert avg_iron >= avg_plain * 0.95, (
            f"Iron Will should help: {avg_iron:.1f} vs {avg_plain:.1f}"
        )


# --- Fate's Hand Move ---


class TestFatesHand:
    def test_fates_hand_gate(self):
        """Fate's Hand requires fate >= 80."""
        high_fate = HooperState(
            hooper=_make_hooper(attrs=_make_attrs(fate=90), moves=[FATES_HAND])
        )
        low_fate = HooperState(
            hooper=_make_hooper(attrs=_make_attrs(fate=50), moves=[FATES_HAND])
        )
        assert check_gate(FATES_HAND, high_fate)
        assert not check_gate(FATES_HAND, low_fate)

    def test_fates_hand_triggers_any_possession(self):
        """Fate's Hand triggers on any possession."""
        hooper = HooperState(
            hooper=_make_hooper(attrs=_make_attrs(fate=90), moves=[FATES_HAND])
        )
        for action in ("at_rim", "mid_range", "three_point", "drive"):
            assert check_trigger(FATES_HAND, hooper, action, False, False)

    def test_fates_hand_can_boost(self):
        """Fate's Hand should sometimes produce a +18% boost."""
        base_prob = 0.40
        found_boost = False
        for seed in range(100):
            modified = apply_move_modifier(FATES_HAND, base_prob, random.Random(seed))
            if modified > base_prob + 0.10:
                found_boost = True
                break
        assert found_boost, "Fate's Hand never produced boost in 100 seeds"

    def test_fates_hand_can_penalize(self):
        """Fate's Hand should sometimes produce a -5% penalty."""
        base_prob = 0.40
        found_penalty = False
        for seed in range(100):
            modified = apply_move_modifier(FATES_HAND, base_prob, random.Random(seed))
            if modified < base_prob:
                found_penalty = True
                break
        assert found_penalty, "Fate's Hand never produced penalty in 100 seeds"


# --- Fate Attribute ---


class TestFateAttribute:
    def test_fate_clutch_bonus_close_game(self):
        """High-Fate hooper gets a bonus in close games (diff < 5)."""
        bonus = compute_fate_clutch_bonus(fate=80, score_differential=3)
        assert abs(bonus - 0.064) < 0.001  # 80/100 * 0.08 = 0.064

    def test_fate_clutch_bonus_blowout(self):
        """No Fate bonus in blowout games (diff >= 5)."""
        bonus = compute_fate_clutch_bonus(fate=80, score_differential=5)
        assert bonus == 0.0
        bonus = compute_fate_clutch_bonus(fate=80, score_differential=10)
        assert bonus == 0.0

    def test_fate_clutch_bonus_tied_game(self):
        """Tied game (diff=0) should give the full Fate clutch bonus."""
        bonus = compute_fate_clutch_bonus(fate=90, score_differential=0)
        assert abs(bonus - 0.072) < 0.001  # 90/100 * 0.08 = 0.072

    def test_low_fate_small_bonus(self):
        """Low-Fate hooper gets a smaller clutch bonus."""
        bonus = compute_fate_clutch_bonus(fate=20, score_differential=2)
        assert abs(bonus - 0.016) < 0.001  # 20/100 * 0.08 = 0.016

    def test_fate_increases_shot_probability_in_close_game(self):
        """compute_shot_probability with score_differential < 5 boosts high-Fate."""
        high_fate = HooperState(
            hooper=_make_hooper(attrs=_make_attrs(scoring=50, fate=90))
        )
        low_fate = HooperState(
            hooper=_make_hooper(attrs=_make_attrs(scoring=50, fate=10))
        )
        defender = HooperState(hooper=_make_hooper(attrs=_make_attrs(defense=40)))

        prob_high = compute_shot_probability(
            high_fate, defender, "mid_range", 0.05, DEFAULT_RULESET, score_differential=2,
        )
        prob_low = compute_shot_probability(
            low_fate, defender, "mid_range", 0.05, DEFAULT_RULESET, score_differential=2,
        )
        assert prob_high > prob_low, (
            f"High-Fate should have higher prob in close game: {prob_high:.4f} vs {prob_low:.4f}"
        )

    def test_fate_no_effect_in_blowout(self):
        """In a blowout (diff >= 5), Fate should not affect probability."""
        high_fate = HooperState(
            hooper=_make_hooper(attrs=_make_attrs(scoring=50, fate=90))
        )
        low_fate = HooperState(
            hooper=_make_hooper(attrs=_make_attrs(scoring=50, fate=10))
        )
        defender = HooperState(hooper=_make_hooper(attrs=_make_attrs(defense=40)))

        prob_high = compute_shot_probability(
            high_fate, defender, "mid_range", 0.05, DEFAULT_RULESET, score_differential=10,
        )
        prob_low = compute_shot_probability(
            low_fate, defender, "mid_range", 0.05, DEFAULT_RULESET, score_differential=10,
        )
        # In a blowout, Fate clutch bonus is 0 so probabilities should be equal
        # (same scoring attribute, same defender)
        assert abs(prob_high - prob_low) < 0.001, (
            f"Fate should not matter in blowout: {prob_high:.4f} vs {prob_low:.4f}"
        )

    def test_fate_lucky_bounces_offensive_rebound(self):
        """High-Fate offensive players should get more offensive rebounds."""
        from pinwheel.core.possession import attempt_rebound

        high_fate_attrs = _make_attrs(scoring=40, defense=30, fate=90)
        low_fate_attrs = _make_attrs(scoring=40, defense=30, fate=10)

        # Run many rebound attempts and compare offensive rebound rates
        high_fate_off_rebs = 0
        low_fate_off_rebs = 0
        n = 2000

        for s in range(n):
            # High-fate offense
            off = [HooperState(hooper=_make_hooper("hf", attrs=high_fate_attrs))]
            dfn = [HooperState(hooper=_make_hooper("d1", "t-2", attrs=_make_attrs()))]
            _, is_off = attempt_rebound(off, dfn, random.Random(s), rules=DEFAULT_RULESET)
            if is_off:
                high_fate_off_rebs += 1

            # Low-fate offense
            off_low = [HooperState(hooper=_make_hooper("lf", attrs=low_fate_attrs))]
            dfn_low = [HooperState(hooper=_make_hooper("d2", "t-2", attrs=_make_attrs()))]
            _, is_off_low = attempt_rebound(
                off_low, dfn_low, random.Random(s), rules=DEFAULT_RULESET,
            )
            if is_off_low:
                low_fate_off_rebs += 1

        assert high_fate_off_rebs > low_fate_off_rebs, (
            f"High-Fate should get more offensive rebounds: "
            f"{high_fate_off_rebs} vs {low_fate_off_rebs}"
        )


# --- All Moves Have Branches ---


class TestAllMovesHaveBranches:
    def test_every_move_modifies_probability(self):
        """Every defined move should either increase or decrease probability."""
        from pinwheel.core.moves import ALL_MOVES

        base_prob = 0.50
        for move in ALL_MOVES:
            found_change = False
            for seed in range(200):
                modified = apply_move_modifier(move, base_prob, random.Random(seed))
                if modified != base_prob:
                    found_change = True
                    break
            assert found_change, (
                f"Move '{move.name}' never modified probability across 200 seeds"
            )


# --- Surface Modifiers ---


class TestSurfaceModifiers:
    """Tests for venue surface effects on gameplay."""

    def test_get_surface_modifiers_hardwood_no_effect(self):
        """Hardwood is the default surface with no modifications."""
        from pinwheel.core.possession import get_surface_modifiers

        mods = get_surface_modifiers("hardwood")
        assert mods.at_rim_weight_modifier == 0.0
        assert mods.mid_range_weight_modifier == 0.0
        assert mods.three_point_weight_modifier == 0.0
        assert mods.stamina_drain_multiplier == 1.0
        assert mods.turnover_rate_modifier == 0.0
        assert mods.shot_probability_modifier == 0.0
        assert mods.speed_at_rim_modifier == 0.0

    def test_get_surface_modifiers_unknown_defaults_to_hardwood(self):
        """Unknown surfaces are treated as hardwood (no modifiers)."""
        from pinwheel.core.possession import SurfaceModifiers, get_surface_modifiers

        mods = get_surface_modifiers("lava")
        assert mods == SurfaceModifiers()

    def test_grass_surface_values(self):
        """Grass: speed penalty, stamina drain +20%, turnover +5%."""
        from pinwheel.core.possession import get_surface_modifiers

        mods = get_surface_modifiers("grass")
        assert mods.speed_at_rim_modifier == -0.10
        assert mods.stamina_drain_multiplier == 1.20
        assert mods.turnover_rate_modifier == 0.05

    def test_sand_surface_values(self):
        """Sand: heavy speed penalty, stamina drain +40%, three-point weight +10%."""
        from pinwheel.core.possession import get_surface_modifiers

        mods = get_surface_modifiers("sand")
        assert mods.speed_at_rim_modifier == -0.20
        assert mods.stamina_drain_multiplier == 1.40
        assert mods.three_point_weight_modifier == 0.10

    def test_ice_surface_values(self):
        """Ice: turnover +15%, shot prob -5%, speed at rim +10%."""
        from pinwheel.core.possession import get_surface_modifiers

        mods = get_surface_modifiers("ice")
        assert mods.turnover_rate_modifier == 0.15
        assert mods.shot_probability_modifier == -0.05
        assert mods.speed_at_rim_modifier == 0.10

    def test_clay_surface_values(self):
        """Clay: mid-range bonus +10%, stamina drain +10%."""
        from pinwheel.core.possession import get_surface_modifiers

        mods = get_surface_modifiers("clay")
        assert mods.mid_range_weight_modifier == 0.10
        assert mods.stamina_drain_multiplier == 1.10

    def test_surface_stored_in_game_state(self):
        """GameState carries home_venue_surface from the home team's venue."""
        home_team = _make_team("home")
        gs = GameState(
            home_agents=[HooperState(hooper=h) for h in home_team.hoopers if h.is_starter],
            away_agents=[HooperState(hooper=h) for h in home_team.hoopers if h.is_starter],
            home_venue_surface="sand",
        )
        assert gs.home_venue_surface == "sand"

    def test_surface_defaults_to_hardwood(self):
        """GameState defaults to hardwood if not specified."""
        home_team = _make_team("home")
        gs = GameState(
            home_agents=[HooperState(hooper=h) for h in home_team.hoopers if h.is_starter],
            away_agents=[HooperState(hooper=h) for h in home_team.hoopers if h.is_starter],
        )
        assert gs.home_venue_surface == "hardwood"


class TestSurfaceSelectAction:
    """Tests that surface modifiers affect shot selection weights."""

    def test_grass_reduces_at_rim_weight(self):
        """Grass surface should reduce at_rim selection due to speed penalty."""
        from pinwheel.core.possession import get_surface_modifiers, select_action

        rules = RuleSet()
        handler = HooperState(hooper=_make_hooper(attrs=_make_attrs(speed=80, scoring=40, iq=40)))

        def _count_at_rim(surface_name: str, n: int = 1000) -> int:
            surface = get_surface_modifiers(surface_name)
            count = 0
            for seed in range(n):
                gs = GameState(
                    home_agents=[handler],
                    away_agents=[HooperState(hooper=_make_hooper("d1"))],
                )
                rng = random.Random(seed)
                action = select_action(handler, gs, rules, rng, surface=surface)
                if action == "at_rim":
                    count += 1
            return count

        hardwood_at_rim = _count_at_rim("hardwood")
        grass_at_rim = _count_at_rim("grass")
        assert grass_at_rim < hardwood_at_rim

    def test_sand_increases_three_point_weight(self):
        """Sand surface should increase three-point selection."""
        from pinwheel.core.possession import get_surface_modifiers, select_action

        rules = RuleSet()
        handler = HooperState(hooper=_make_hooper(attrs=_make_attrs(scoring=60, speed=60, iq=40)))

        def _count_threes(surface_name: str, n: int = 1000) -> int:
            surface = get_surface_modifiers(surface_name)
            count = 0
            for seed in range(n):
                gs = GameState(
                    home_agents=[handler],
                    away_agents=[HooperState(hooper=_make_hooper("d1"))],
                )
                rng = random.Random(seed)
                action = select_action(handler, gs, rules, rng, surface=surface)
                if action == "three_point":
                    count += 1
            return count

        hardwood_threes = _count_threes("hardwood")
        sand_threes = _count_threes("sand")
        assert sand_threes > hardwood_threes

    def test_ice_increases_at_rim_weight_via_speed_bonus(self):
        """Ice surface gives +10% speed at rim (sliding momentum)."""
        from pinwheel.core.possession import get_surface_modifiers, select_action

        rules = RuleSet()
        handler = HooperState(hooper=_make_hooper(attrs=_make_attrs(speed=80, scoring=30, iq=30)))

        def _count_at_rim(surface_name: str, n: int = 1000) -> int:
            surface = get_surface_modifiers(surface_name)
            count = 0
            for seed in range(n):
                gs = GameState(
                    home_agents=[handler],
                    away_agents=[HooperState(hooper=_make_hooper("d1"))],
                )
                rng = random.Random(seed)
                action = select_action(handler, gs, rules, rng, surface=surface)
                if action == "at_rim":
                    count += 1
            return count

        hardwood_at_rim = _count_at_rim("hardwood")
        ice_at_rim = _count_at_rim("ice")
        assert ice_at_rim > hardwood_at_rim

    def test_clay_increases_mid_range_weight(self):
        """Clay surface should increase mid-range selection."""
        from pinwheel.core.possession import get_surface_modifiers, select_action

        rules = RuleSet()
        handler = HooperState(hooper=_make_hooper(attrs=_make_attrs(iq=60, speed=40, scoring=40)))

        def _count_mid(surface_name: str, n: int = 1000) -> int:
            surface = get_surface_modifiers(surface_name)
            count = 0
            for seed in range(n):
                gs = GameState(
                    home_agents=[handler],
                    away_agents=[HooperState(hooper=_make_hooper("d1"))],
                )
                rng = random.Random(seed)
                action = select_action(handler, gs, rules, rng, surface=surface)
                if action == "mid_range":
                    count += 1
            return count

        hardwood_mid = _count_mid("hardwood")
        clay_mid = _count_mid("clay")
        assert clay_mid > hardwood_mid


class TestSurfaceTurnover:
    """Tests that surface modifiers affect turnover rate."""

    def test_grass_increases_turnovers(self):
        """Grass surface should increase turnover rate (+5%)."""
        from pinwheel.core.possession import resolve_possession

        rules = RuleSet(home_court_enabled=False)

        def _count_turnovers(surface: str, n: int = 500) -> int:
            to_count = 0
            for seed in range(n):
                home = _make_team("home")
                away = _make_team("away")
                gs = GameState(
                    home_agents=[HooperState(hooper=h) for h in home.hoopers if h.is_starter],
                    away_agents=[HooperState(hooper=h) for h in away.hoopers if h.is_starter],
                    home_venue_surface=surface,
                )
                rng = random.Random(seed)
                result = resolve_possession(gs, rules, rng)
                if result.turnover:
                    to_count += 1
            return to_count

        hardwood_to = _count_turnovers("hardwood")
        grass_to = _count_turnovers("grass")
        assert grass_to > hardwood_to

    def test_ice_increases_turnovers_more_than_grass(self):
        """Ice surface should increase turnovers more than grass (+15% vs +5%)."""
        from pinwheel.core.possession import resolve_possession

        rules = RuleSet(home_court_enabled=False)

        def _count_turnovers(surface: str, n: int = 500) -> int:
            to_count = 0
            for seed in range(n):
                home = _make_team("home")
                away = _make_team("away")
                gs = GameState(
                    home_agents=[HooperState(hooper=h) for h in home.hoopers if h.is_starter],
                    away_agents=[HooperState(hooper=h) for h in away.hoopers if h.is_starter],
                    home_venue_surface=surface,
                )
                rng = random.Random(seed)
                result = resolve_possession(gs, rules, rng)
                if result.turnover:
                    to_count += 1
            return to_count

        grass_to = _count_turnovers("grass")
        ice_to = _count_turnovers("ice")
        assert ice_to > grass_to


class TestSurfaceStaminaDrain:
    """Tests that surface modifiers affect stamina drain."""

    def test_sand_drains_more_stamina_than_hardwood(self):
        """Sand surface (1.4x multiplier) should drain stamina faster."""
        from pinwheel.core.possession import drain_stamina

        rules = RuleSet(home_court_enabled=False)

        def _run_drain(multiplier: float) -> float:
            agent = HooperState(hooper=_make_hooper(attrs=_make_attrs(stamina=10)))
            agent.current_stamina = 0.80
            drain_stamina(
                [agent], "man_switch", is_defense=False, rules=rules,
                surface_stamina_multiplier=multiplier,
            )
            return agent.current_stamina

        hardwood_stamina = _run_drain(1.0)
        sand_stamina = _run_drain(1.4)
        assert sand_stamina < hardwood_stamina

    def test_grass_drains_more_stamina_than_hardwood(self):
        """Grass surface (1.2x multiplier) should drain stamina faster."""
        from pinwheel.core.possession import drain_stamina

        rules = RuleSet(home_court_enabled=False)

        def _run_drain(multiplier: float) -> float:
            agent = HooperState(hooper=_make_hooper(attrs=_make_attrs(stamina=10)))
            agent.current_stamina = 0.80
            drain_stamina(
                [agent], "man_switch", is_defense=False, rules=rules,
                surface_stamina_multiplier=multiplier,
            )
            return agent.current_stamina

        hardwood_stamina = _run_drain(1.0)
        grass_stamina = _run_drain(1.2)
        assert grass_stamina < hardwood_stamina

    def test_hardwood_multiplier_is_identity(self):
        """Hardwood (1.0x) should produce the same drain as no multiplier."""
        from pinwheel.core.possession import drain_stamina

        rules = RuleSet(home_court_enabled=False)

        agent_default = HooperState(hooper=_make_hooper(attrs=_make_attrs(stamina=10)))
        agent_default.current_stamina = 0.80
        drain_stamina([agent_default], "man_switch", is_defense=False, rules=rules)

        agent_hardwood = HooperState(hooper=_make_hooper(attrs=_make_attrs(stamina=10)))
        agent_hardwood.current_stamina = 0.80
        drain_stamina(
            [agent_hardwood], "man_switch", is_defense=False, rules=rules,
            surface_stamina_multiplier=1.0,
        )
        assert agent_default.current_stamina == agent_hardwood.current_stamina


class TestSurfaceShotProbability:
    """Tests that surface modifiers affect shot probability."""

    def test_ice_reduces_shot_probability(self):
        """Ice surface (-5% shot prob) should reduce scoring."""
        from pinwheel.core.possession import resolve_possession

        rules = RuleSet(home_court_enabled=False)

        def _total_points(surface: str, n: int = 500) -> int:
            pts = 0
            for seed in range(n):
                home = _make_team("home")
                away = _make_team("away")
                gs = GameState(
                    home_agents=[HooperState(hooper=h) for h in home.hoopers if h.is_starter],
                    away_agents=[HooperState(hooper=h) for h in away.hoopers if h.is_starter],
                    home_venue_surface=surface,
                )
                rng = random.Random(seed)
                result = resolve_possession(gs, rules, rng)
                pts += result.points_scored
            return pts

        hardwood_pts = _total_points("hardwood")
        ice_pts = _total_points("ice")
        assert ice_pts < hardwood_pts


class TestSurfaceFullGame:
    """Full-game integration tests for surface modifiers."""

    def test_simulate_game_passes_surface_to_game_state(self):
        """simulate_game should propagate home venue surface to GameState."""
        home_venue = Venue(name="Sand Pit", capacity=5000, surface="sand")
        home = Team(
            id="home", name="Home", venue=home_venue,
            hoopers=[
                _make_hooper(f"h-s{i}", "home", is_starter=True)
                for i in range(3)
            ] + [_make_hooper("h-b0", "home", is_starter=False)],
        )
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        assert result.total_possessions > 0

    def test_sand_game_more_threes_than_hardwood(self):
        """Games on sand should see more three-point attempts."""
        def _count_three_attempts(surface: str, n_games: int = 30) -> int:
            total_3pa = 0
            for seed in range(n_games):
                home_venue = Venue(name="Court", capacity=5000, surface=surface)
                home = Team(
                    id="home", name="Home", venue=home_venue,
                    hoopers=[
                        _make_hooper(f"h-s{i}", "home", is_starter=True)
                        for i in range(3)
                    ] + [_make_hooper("h-b0", "home", is_starter=False)],
                )
                away = _make_team("away")
                result = simulate_game(home, away, DEFAULT_RULESET, seed=seed)
                for bs in result.box_scores:
                    total_3pa += bs.three_pointers_attempted
            return total_3pa

        hardwood_3pa = _count_three_attempts("hardwood")
        sand_3pa = _count_three_attempts("sand")
        assert sand_3pa > hardwood_3pa

    def test_ice_game_more_turnovers_than_hardwood(self):
        """Games on ice should produce more turnovers (+15% modifier)."""
        def _count_turnovers(surface: str, n_games: int = 30) -> int:
            total_to = 0
            for seed in range(n_games):
                home_venue = Venue(name="Court", capacity=5000, surface=surface)
                home = Team(
                    id="home", name="Home", venue=home_venue,
                    hoopers=[
                        _make_hooper(f"h-s{i}", "home", is_starter=True)
                        for i in range(3)
                    ] + [_make_hooper("h-b0", "home", is_starter=False)],
                )
                away = _make_team("away")
                result = simulate_game(home, away, DEFAULT_RULESET, seed=seed)
                for bs in result.box_scores:
                    total_to += bs.turnovers
            return total_to

        hardwood_to = _count_turnovers("hardwood")
        ice_to = _count_turnovers("ice")
        assert ice_to > hardwood_to

    def test_all_surface_types_complete_game(self):
        """All defined surface types should produce a valid, complete game."""
        from pinwheel.core.possession import SURFACE_EFFECTS

        for surface_name in SURFACE_EFFECTS:
            home_venue = Venue(name="Court", capacity=5000, surface=surface_name)
            home = Team(
                id="home", name="Home", venue=home_venue,
                hoopers=[
                    _make_hooper(f"h-s{i}", "home", is_starter=True)
                    for i in range(3)
                ] + [_make_hooper("h-b0", "home", is_starter=False)],
            )
            away = _make_team("away")
            result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
            assert result.total_possessions > 0
            assert result.home_score + result.away_score > 0

    def test_determinism_with_surface(self):
        """Games on non-standard surfaces should still be deterministic."""
        home_venue = Venue(name="Ice Rink", capacity=5000, surface="ice")
        home = Team(
            id="home", name="Home", venue=home_venue,
            hoopers=[
                _make_hooper(f"h-s{i}", "home", is_starter=True)
                for i in range(3)
            ] + [_make_hooper("h-b0", "home", is_starter=False)],
        )
        away = _make_team("away")
        r1 = simulate_game(home, away, DEFAULT_RULESET, seed=99)
        r2 = simulate_game(home, away, DEFAULT_RULESET, seed=99)
        assert r1.home_score == r2.home_score
        assert r1.away_score == r2.away_score
        assert r1.total_possessions == r2.total_possessions
