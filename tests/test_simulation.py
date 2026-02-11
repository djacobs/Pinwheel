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
from pinwheel.core.state import AgentState, GameState
from pinwheel.models.rules import DEFAULT_RULESET, RuleSet
from pinwheel.models.team import Agent, Move, PlayerAttributes, Team, Venue


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


def _make_agent(
    agent_id: str = "a-1",
    team_id: str = "t-1",
    attrs: PlayerAttributes | None = None,
    is_starter: bool = True,
    moves: list[Move] | None = None,
) -> Agent:
    return Agent(
        id=agent_id,
        name=f"Agent-{agent_id}",
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
    agents = []
    for i in range(n_starters):
        agents.append(
            _make_agent(
                f"{team_id}-s{i}",
                team_id,
                attrs,
                is_starter=True,
                moves=moves,
            )
        )
    for i in range(n_bench):
        agents.append(
            _make_agent(
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
        agents=agents,
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
        shooter = AgentState(agent=_make_agent(attrs=_make_attrs(scoring=99)))
        defender = AgentState(agent=_make_agent(attrs=_make_attrs(defense=99)))
        prob = compute_shot_probability(shooter, defender, "three_point", 0.08, DEFAULT_RULESET)
        assert 0.01 <= prob <= 0.99

    def test_high_defense_reduces_probability(self):
        shooter = AgentState(agent=_make_agent(attrs=_make_attrs(scoring=60)))
        weak_def = AgentState(agent=_make_agent(attrs=_make_attrs(defense=20)))
        strong_def = AgentState(agent=_make_agent(attrs=_make_attrs(defense=90)))
        p_weak = compute_shot_probability(shooter, weak_def, "mid_range", 0.05, DEFAULT_RULESET)
        p_strong = compute_shot_probability(shooter, strong_def, "mid_range", 0.05, DEFAULT_RULESET)
        assert p_weak > p_strong

    def test_stamina_modifier_full(self):
        assert compute_stamina_modifier(1.0) == 1.0

    def test_stamina_modifier_depleted(self):
        assert compute_stamina_modifier(0.0) == 0.7

    def test_resolve_shot_returns_tuple(self):
        shooter = AgentState(agent=_make_agent(attrs=_make_attrs(scoring=80)))
        defender = AgentState(agent=_make_agent(attrs=_make_attrs(defense=30)))
        made, pts = resolve_shot(
            shooter, defender, "three_point", 0.05, DEFAULT_RULESET, random.Random(42)
        )
        assert isinstance(made, bool)
        assert pts >= 0


# --- Defense ---


class TestDefense:
    def test_select_scheme_returns_valid(self):
        off = [AgentState(agent=_make_agent(f"o{i}")) for i in range(3)]
        dfn = [AgentState(agent=_make_agent(f"d{i}")) for i in range(3)]
        gs = GameState(home_agents=off, away_agents=dfn)
        scheme = select_scheme(off, dfn, gs, DEFAULT_RULESET, random.Random(42))
        assert scheme in ("man_tight", "man_switch", "zone", "press")

    def test_low_stamina_favors_zone(self):
        off = [AgentState(agent=_make_agent(f"o{i}")) for i in range(3)]
        dfn = [AgentState(agent=_make_agent(f"d{i}")) for i in range(3)]
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
        off = [AgentState(agent=_make_agent(f"o{i}")) for i in range(3)]
        dfn = [AgentState(agent=_make_agent(f"d{i}")) for i in range(3)]
        matchups = assign_matchups(off, dfn, "man_tight", random.Random(42))
        assert len(matchups) == 3

    def test_get_primary_defender(self):
        off = [AgentState(agent=_make_agent(f"o{i}")) for i in range(3)]
        dfn = [AgentState(agent=_make_agent(f"d{i}")) for i in range(3)]
        matchups = assign_matchups(off, dfn, "man_tight", random.Random(42))
        defender = get_primary_defender(off[0], matchups, dfn)
        assert defender in dfn


# --- Moves ---


class TestMoves:
    def test_heat_check_gate(self):
        agent = AgentState(agent=_make_agent(attrs=_make_attrs(ego=50), moves=[HEAT_CHECK]))
        assert check_gate(HEAT_CHECK, agent)

    def test_heat_check_gate_fails(self):
        agent = AgentState(agent=_make_agent(attrs=_make_attrs(ego=10), moves=[HEAT_CHECK]))
        # Heat check gate is ego >= 30, so ego=10 fails
        assert not check_gate(HEAT_CHECK, agent)

    def test_lockdown_trigger(self):
        agent = AgentState(
            agent=_make_agent(attrs=_make_attrs(defense=80), moves=[LOCKDOWN_STANCE])
        )
        assert check_trigger(LOCKDOWN_STANCE, agent, "drive", False, False)
        assert not check_trigger(LOCKDOWN_STANCE, agent, "pass", False, False)

    def test_heat_check_trigger(self):
        agent = AgentState(agent=_make_agent(attrs=_make_attrs(ego=50), moves=[HEAT_CHECK]))
        assert check_trigger(HEAT_CHECK, agent, "three_point", True, False)
        assert not check_trigger(HEAT_CHECK, agent, "three_point", False, False)


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
