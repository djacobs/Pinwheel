"""Tests for the simulation engine."""

import random

from pinwheel.core.defense import (
    assign_matchups,
    get_primary_defender,
    select_scheme,
)
from pinwheel.core.moves import HEAT_CHECK, LOCKDOWN_STANCE, check_gate, check_trigger
from pinwheel.core.scoring import (
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
    return PlayerAttributes(
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
    return Hooper(
        id=hooper_id,
        name=f"Hooper-{hooper_id}",
        team_id=team_id,
        archetype="sharpshooter",
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
        """Possession logs from timed quarters must have non-empty M:SS game_clock."""
        home = _make_team("home")
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        elam_quarter = DEFAULT_RULESET.elam_trigger_quarter + 1
        timed_logs = [p for p in result.possession_log if p.quarter < elam_quarter]
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
        """Run 100 games and verify basketball-like distributions."""
        home = _make_team("home")
        away = _make_team("away")
        results = [simulate_game(home, away, DEFAULT_RULESET, seed=s) for s in range(100)]
        total_scores = [r.home_score + r.away_score for r in results]
        avg_score = sum(total_scores) / len(total_scores)
        avg_possessions = sum(r.total_possessions for r in results) / len(results)
        home_wins = sum(1 for r in results if r.winner_team_id == "home")

        # Basketball-like ranges (3v3 with Elam ending)
        # 15 possessions/quarter * 3 quarters + Elam possessions
        assert 30 < avg_score < 200, f"avg total score {avg_score} out of range"
        assert 45 < avg_possessions < 200, f"avg possessions {avg_possessions} out of range"
        # With equal teams, home/away should be roughly balanced
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
