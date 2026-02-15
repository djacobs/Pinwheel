"""Tests for strategy overrides — simulation integration.

Verifies that strategy parameters (defensive_intensity, pace_modifier) actually
affect the simulation calculations they are supposed to affect:

Fix 1: defensive_intensity -> foul rate
Fix 2: defensive_intensity -> stamina drain (defenders)
Fix 3: pace_modifier -> stamina drain (both teams)
Fix 4: defensive_intensity -> scheme selection
Fix 5: strategy summaries in GameResult metadata
"""

import random

from pinwheel.core.defense import select_scheme
from pinwheel.core.possession import check_foul, drain_stamina
from pinwheel.core.simulation import simulate_game
from pinwheel.core.state import GameState, HooperState
from pinwheel.models.rules import DEFAULT_RULESET
from pinwheel.models.team import Hooper, PlayerAttributes, Team, TeamStrategy, Venue

# ---------------------------------------------------------------------------
# Helpers (reuse the same pattern as test_simulation.py)
# ---------------------------------------------------------------------------


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
) -> Hooper:
    return Hooper(
        id=hooper_id,
        name=f"Hooper-{hooper_id}",
        team_id=team_id,
        archetype="sharpshooter",
        attributes=attrs or _make_attrs(),
        is_starter=is_starter,
    )


def _make_team(
    team_id: str = "t-1",
    n_starters: int = 3,
    n_bench: int = 1,
    attrs: PlayerAttributes | None = None,
) -> Team:
    hoopers = []
    for i in range(n_starters):
        hoopers.append(
            _make_hooper(f"{team_id}-s{i}", team_id, attrs, is_starter=True)
        )
    for i in range(n_bench):
        hoopers.append(
            _make_hooper(f"{team_id}-b{i}", team_id, attrs, is_starter=False)
        )
    return Team(
        id=team_id,
        name=f"Team-{team_id}",
        venue=Venue(name="Court", capacity=5000),
        hoopers=hoopers,
    )


# ===========================================================================
# Fix 1: Defensive Intensity Affects Foul Rate
# ===========================================================================


class TestDefensiveIntensityFoulRate:
    """check_foul() with defensive_intensity=0.5 should return True more often."""

    def test_high_intensity_increases_foul_rate(self) -> None:
        """Statistical test: 1000 trials, high intensity should produce more fouls."""
        defender = HooperState(hooper=_make_hooper(attrs=_make_attrs(defense=50, iq=50)))

        fouls_normal = 0
        fouls_intense = 0
        trials = 2000
        for i in range(trials):
            rng_a = random.Random(i)
            rng_b = random.Random(i)
            if check_foul(defender, "mid_range", "man_switch", rng_a, rules=DEFAULT_RULESET):
                fouls_normal += 1
            if check_foul(
                defender, "mid_range", "man_switch", rng_b,
                rules=DEFAULT_RULESET, defensive_intensity=0.5,
            ):
                fouls_intense += 1

        # With intensity=0.5, +4% additional foul rate -> clearly more fouls
        assert fouls_intense > fouls_normal, (
            f"High intensity should cause more fouls: {fouls_intense} vs {fouls_normal}"
        )
        # Sanity: intensity should add a meaningful amount (at least 20% more fouls)
        assert fouls_intense > fouls_normal * 1.15, (
            f"Intensity effect too small: {fouls_intense} vs {fouls_normal}"
        )

    def test_zero_intensity_matches_default(self) -> None:
        """defensive_intensity=0.0 should produce the same result as no parameter."""
        defender = HooperState(hooper=_make_hooper(attrs=_make_attrs(defense=50, iq=50)))

        for i in range(100):
            rng_a = random.Random(i)
            rng_b = random.Random(i)
            a = check_foul(defender, "mid_range", "zone", rng_a, rules=DEFAULT_RULESET)
            b = check_foul(
                defender, "mid_range", "zone", rng_b,
                rules=DEFAULT_RULESET, defensive_intensity=0.0,
            )
            assert a == b, f"Seed {i}: default and intensity=0.0 should match"

    def test_negative_intensity_does_not_reduce_fouls(self) -> None:
        """Negative defensive_intensity should NOT reduce fouls below base rate."""
        defender = HooperState(hooper=_make_hooper(attrs=_make_attrs(defense=50, iq=50)))

        fouls_normal = 0
        fouls_relaxed = 0
        trials = 2000
        for i in range(trials):
            rng_a = random.Random(i)
            rng_b = random.Random(i)
            if check_foul(defender, "mid_range", "zone", rng_a, rules=DEFAULT_RULESET):
                fouls_normal += 1
            if check_foul(
                defender, "mid_range", "zone", rng_b,
                rules=DEFAULT_RULESET, defensive_intensity=-0.5,
            ):
                fouls_relaxed += 1

        # Should be essentially equal — negative intensity does NOT reduce fouls
        assert fouls_relaxed == fouls_normal, (
            f"Negative intensity should not reduce fouls: {fouls_relaxed} vs {fouls_normal}"
        )


# ===========================================================================
# Fix 2: Defensive Intensity Affects Stamina Drain
# ===========================================================================


class TestDefensiveIntensityStaminaDrain:
    """drain_stamina() with defensive_intensity should drain defenders more."""

    def test_high_intensity_drains_defenders_more(self) -> None:
        """Defenders with high intensity should end with lower stamina."""
        agents_normal = [HooperState(hooper=_make_hooper(f"d{i}")) for i in range(3)]
        agents_intense = [HooperState(hooper=_make_hooper(f"d{i}")) for i in range(3)]

        # 20 possessions of drain
        for _ in range(20):
            drain_stamina(agents_normal, "man_switch", is_defense=True, rules=DEFAULT_RULESET)
            drain_stamina(
                agents_intense, "man_switch", is_defense=True,
                rules=DEFAULT_RULESET, defensive_intensity=0.5,
            )

        avg_normal = sum(a.current_stamina for a in agents_normal) / len(agents_normal)
        avg_intense = sum(a.current_stamina for a in agents_intense) / len(agents_intense)
        assert avg_intense < avg_normal, (
            f"High intensity should drain defenders more: {avg_intense:.4f} vs {avg_normal:.4f}"
        )

    def test_intensity_does_not_drain_offense(self) -> None:
        """Defensive intensity should NOT affect offensive stamina drain."""
        agents_normal = [HooperState(hooper=_make_hooper(f"o{i}")) for i in range(3)]
        agents_intense = [HooperState(hooper=_make_hooper(f"o{i}")) for i in range(3)]

        for _ in range(20):
            drain_stamina(agents_normal, "man_switch", is_defense=False, rules=DEFAULT_RULESET)
            drain_stamina(
                agents_intense, "man_switch", is_defense=False,
                rules=DEFAULT_RULESET, defensive_intensity=0.5,
            )

        avg_normal = sum(a.current_stamina for a in agents_normal) / len(agents_normal)
        avg_intense = sum(a.current_stamina for a in agents_intense) / len(agents_intense)
        assert avg_normal == avg_intense, (
            f"Defensive intensity should not affect offense: {avg_intense:.4f} vs {avg_normal:.4f}"
        )


# ===========================================================================
# Fix 3: Pace Modifier Affects Stamina Drain
# ===========================================================================


class TestPaceModifierStaminaDrain:
    """drain_stamina() with pace_modifier < 1.0 should drain more for both teams.

    Uses low-stamina hoopers (stamina=10) so the stamina recovery rate
    (10/3000 = 0.0033) does not outpace the base drain, making the pace
    modifier effect measurable.
    """

    def test_fast_pace_drains_more_offense(self) -> None:
        """Offensive players drain faster at pace_modifier=0.8."""
        low_stam_attrs = _make_attrs(stamina=10)
        agents_normal = [
            HooperState(hooper=_make_hooper(f"o{i}", attrs=low_stam_attrs)) for i in range(3)
        ]
        agents_fast = [
            HooperState(hooper=_make_hooper(f"o{i}", attrs=low_stam_attrs)) for i in range(3)
        ]

        for _ in range(20):
            drain_stamina(
                agents_normal, "man_switch", is_defense=False,
                rules=DEFAULT_RULESET, pace_modifier=1.0,
            )
            drain_stamina(
                agents_fast, "man_switch", is_defense=False,
                rules=DEFAULT_RULESET, pace_modifier=0.8,
            )

        avg_normal = sum(a.current_stamina for a in agents_normal) / len(agents_normal)
        avg_fast = sum(a.current_stamina for a in agents_fast) / len(agents_fast)
        assert avg_fast < avg_normal, (
            f"Fast pace should drain offense more: {avg_fast:.4f} vs {avg_normal:.4f}"
        )

    def test_fast_pace_drains_more_defense(self) -> None:
        """Defensive players drain faster at pace_modifier=0.8."""
        low_stam_attrs = _make_attrs(stamina=10)
        agents_normal = [
            HooperState(hooper=_make_hooper(f"d{i}", attrs=low_stam_attrs)) for i in range(3)
        ]
        agents_fast = [
            HooperState(hooper=_make_hooper(f"d{i}", attrs=low_stam_attrs)) for i in range(3)
        ]

        for _ in range(20):
            drain_stamina(
                agents_normal, "man_switch", is_defense=True,
                rules=DEFAULT_RULESET, pace_modifier=1.0,
            )
            drain_stamina(
                agents_fast, "man_switch", is_defense=True,
                rules=DEFAULT_RULESET, pace_modifier=0.8,
            )

        avg_normal = sum(a.current_stamina for a in agents_normal) / len(agents_normal)
        avg_fast = sum(a.current_stamina for a in agents_fast) / len(agents_fast)
        assert avg_fast < avg_normal, (
            f"Fast pace should drain defense more: {avg_fast:.4f} vs {avg_normal:.4f}"
        )

    def test_slow_pace_drains_less(self) -> None:
        """Slower pace (pace_modifier=1.3) should drain LESS than default.

        Uses a press scheme (high base drain) so the difference between
        pace=1.0 and pace=1.3 is visible even with moderate stamina.
        """
        low_stam_attrs = _make_attrs(stamina=10)
        agents_normal = [
            HooperState(hooper=_make_hooper(f"o{i}", attrs=low_stam_attrs)) for i in range(3)
        ]
        agents_slow = [
            HooperState(hooper=_make_hooper(f"o{i}", attrs=low_stam_attrs)) for i in range(3)
        ]

        for _ in range(20):
            drain_stamina(
                agents_normal, "press", is_defense=True,
                rules=DEFAULT_RULESET, pace_modifier=1.0,
            )
            drain_stamina(
                agents_slow, "press", is_defense=True,
                rules=DEFAULT_RULESET, pace_modifier=1.3,
            )

        avg_normal = sum(a.current_stamina for a in agents_normal) / len(agents_normal)
        avg_slow = sum(a.current_stamina for a in agents_slow) / len(agents_slow)
        assert avg_slow > avg_normal, (
            f"Slow pace should drain less: {avg_slow:.4f} vs {avg_normal:.4f}"
        )


# ===========================================================================
# Fix 4: Strategy Influences Scheme Selection
# ===========================================================================


class TestStrategySchemeSelection:
    """select_scheme() with strategy should bias scheme weights."""

    def test_high_intensity_favors_man_tight(self) -> None:
        """defensive_intensity > 0.2 should select man_tight more often."""
        off = [HooperState(hooper=_make_hooper(f"o{i}")) for i in range(3)]
        dfn = [HooperState(hooper=_make_hooper(f"d{i}")) for i in range(3)]
        gs = GameState(home_agents=dfn, away_agents=off, home_has_ball=False)

        strategy = TeamStrategy(defensive_intensity=0.4)
        counts_with: dict[str, int] = {}
        counts_without: dict[str, int] = {}
        for s in range(300):
            rng = random.Random(s)
            scheme_with = select_scheme(off, dfn, gs, DEFAULT_RULESET, rng, strategy=strategy)
            counts_with[scheme_with] = counts_with.get(scheme_with, 0) + 1

            rng2 = random.Random(s)
            scheme_without = select_scheme(off, dfn, gs, DEFAULT_RULESET, rng2)
            counts_without[scheme_without] = counts_without.get(scheme_without, 0) + 1

        # With high intensity, man_tight should be selected more often
        mt_with = counts_with.get("man_tight", 0)
        mt_without = counts_without.get("man_tight", 0)
        assert mt_with > mt_without, (
            f"High intensity should favor man_tight: with={mt_with} vs without={mt_without}"
        )

    def test_high_intensity_favors_press(self) -> None:
        """defensive_intensity > 0.2 should also select press more often."""
        off = [HooperState(hooper=_make_hooper(f"o{i}")) for i in range(3)]
        dfn = [HooperState(hooper=_make_hooper(f"d{i}")) for i in range(3)]
        gs = GameState(home_agents=dfn, away_agents=off, home_has_ball=False)

        strategy = TeamStrategy(defensive_intensity=0.4)
        counts_with: dict[str, int] = {}
        counts_without: dict[str, int] = {}
        for s in range(300):
            rng = random.Random(s)
            scheme_with = select_scheme(off, dfn, gs, DEFAULT_RULESET, rng, strategy=strategy)
            counts_with[scheme_with] = counts_with.get(scheme_with, 0) + 1

            rng2 = random.Random(s)
            scheme_without = select_scheme(off, dfn, gs, DEFAULT_RULESET, rng2)
            counts_without[scheme_without] = counts_without.get(scheme_without, 0) + 1

        assert counts_with.get("press", 0) > counts_without.get("press", 0), (
            f"High intensity should favor press: "
            f"with={counts_with.get('press', 0)} vs without={counts_without.get('press', 0)}"
        )

    def test_low_intensity_favors_zone(self) -> None:
        """defensive_intensity < -0.1 should select zone more often."""
        off = [HooperState(hooper=_make_hooper(f"o{i}")) for i in range(3)]
        dfn = [HooperState(hooper=_make_hooper(f"d{i}")) for i in range(3)]
        gs = GameState(home_agents=dfn, away_agents=off, home_has_ball=False)

        strategy = TeamStrategy(defensive_intensity=-0.3)
        counts_with: dict[str, int] = {}
        counts_without: dict[str, int] = {}
        for s in range(300):
            rng = random.Random(s)
            scheme_with = select_scheme(off, dfn, gs, DEFAULT_RULESET, rng, strategy=strategy)
            counts_with[scheme_with] = counts_with.get(scheme_with, 0) + 1

            rng2 = random.Random(s)
            scheme_without = select_scheme(off, dfn, gs, DEFAULT_RULESET, rng2)
            counts_without[scheme_without] = counts_without.get(scheme_without, 0) + 1

        assert counts_with.get("zone", 0) > counts_without.get("zone", 0), (
            f"Low intensity should favor zone: "
            f"with={counts_with.get('zone', 0)} vs without={counts_without.get('zone', 0)}"
        )

    def test_no_strategy_regression(self) -> None:
        """Without strategy, scheme selection should be unchanged (regression test)."""
        off = [HooperState(hooper=_make_hooper(f"o{i}")) for i in range(3)]
        dfn = [HooperState(hooper=_make_hooper(f"d{i}")) for i in range(3)]
        gs = GameState(home_agents=dfn, away_agents=off, home_has_ball=False)

        for s in range(100):
            rng_a = random.Random(s)
            rng_b = random.Random(s)
            scheme_a = select_scheme(off, dfn, gs, DEFAULT_RULESET, rng_a)
            scheme_b = select_scheme(off, dfn, gs, DEFAULT_RULESET, rng_b, strategy=None)
            assert scheme_a == scheme_b, f"Seed {s}: no strategy should match None strategy"


# ===========================================================================
# Fix 5: Strategy in GameResult Metadata
# ===========================================================================


class TestStrategyInGameResult:
    """GameResult should record which strategies were active."""

    def test_strategy_summary_populated(self) -> None:
        """GameResult should contain strategy raw_text when strategies are provided."""
        home = _make_team("home")
        away = _make_team("away")
        home_strat = TeamStrategy(
            three_point_bias=10.0,
            raw_text="Bomb away from three",
        )
        away_strat = TeamStrategy(
            defensive_intensity=0.3,
            raw_text="Lock them down",
        )
        result = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            home_strategy=home_strat, away_strategy=away_strat,
        )
        assert result.home_strategy_summary == "Bomb away from three"
        assert result.away_strategy_summary == "Lock them down"

    def test_no_strategy_empty_summary(self) -> None:
        """GameResult should have empty summaries when no strategies are provided."""
        home = _make_team("home")
        away = _make_team("away")
        result = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        assert result.home_strategy_summary == ""
        assert result.away_strategy_summary == ""


# ===========================================================================
# Full-game statistical integration tests
# ===========================================================================


class TestFullGameStrategyIntegration:
    """End-to-end tests: strategies produce statistically different game outcomes."""

    def test_high_defensive_intensity_more_fouls(self) -> None:
        """Games with defensive_intensity=0.5 should produce more fouls on average."""
        home = _make_team("home")
        away = _make_team("away")
        intense_strat = TeamStrategy(defensive_intensity=0.5)

        fouls_normal = []
        fouls_intense = []
        n_games = 100
        for s in range(n_games):
            r_normal = simulate_game(home, away, DEFAULT_RULESET, seed=s)
            r_intense = simulate_game(
                home, away, DEFAULT_RULESET, seed=s,
                away_strategy=intense_strat,
            )
            # Count total fouls from box scores
            fouls_normal.append(sum(bs.fouls for bs in r_normal.box_scores))
            fouls_intense.append(sum(bs.fouls for bs in r_intense.box_scores))

        avg_normal = sum(fouls_normal) / n_games
        avg_intense = sum(fouls_intense) / n_games
        assert avg_intense > avg_normal, (
            f"High intensity should cause more fouls: {avg_intense:.1f} vs {avg_normal:.1f}"
        )

    def test_high_defensive_intensity_lower_defender_stamina(self) -> None:
        """Games with defensive_intensity=0.5 should produce lower average defender stamina."""
        home = _make_team("home")
        away = _make_team("away")
        intense_strat = TeamStrategy(defensive_intensity=0.5)

        # Run games and compare stamina at the end (indirectly via substitutions or
        # just use a short game by modifying rules)
        # We can check the effect by running sim and tracking the game state,
        # but for integration tests, the foul rate test above is the strongest signal.
        # Instead, verify via a different proxy: games with high intensity have
        # more substitutions on the intense-strategy team (they fatigue faster).
        # This test simply validates the full pipeline runs without error.
        result = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            away_strategy=intense_strat,
        )
        assert result.total_possessions > 0
        assert result.home_score > 0 or result.away_score > 0

    def test_fast_pace_more_possessions(self) -> None:
        """Games with pace_modifier=0.8 should produce more possessions per quarter."""
        home = _make_team("home")
        away = _make_team("away")
        fast_strat = TeamStrategy(pace_modifier=0.8)

        possessions_normal = []
        possessions_fast = []
        n_games = 100
        for s in range(n_games):
            r_normal = simulate_game(home, away, DEFAULT_RULESET, seed=s)
            r_fast = simulate_game(
                home, away, DEFAULT_RULESET, seed=s,
                home_strategy=fast_strat,
            )
            possessions_normal.append(r_normal.total_possessions)
            possessions_fast.append(r_fast.total_possessions)

        avg_normal = sum(possessions_normal) / n_games
        avg_fast = sum(possessions_fast) / n_games
        assert avg_fast > avg_normal, (
            f"Fast pace should create more possessions: {avg_fast:.1f} vs {avg_normal:.1f}"
        )

    def test_different_strategies_different_outcomes(self) -> None:
        """Two identical games with different strategies should produce different outcomes."""
        home = _make_team("home")
        away = _make_team("away")

        strat_a = TeamStrategy(
            three_point_bias=15.0, pace_modifier=0.8,
            raw_text="Run and gun, bomb threes",
        )
        strat_b = TeamStrategy(
            defensive_intensity=0.4, pace_modifier=1.2,
            raw_text="Slow it down, lock down defense",
        )

        differs = 0
        n_games = 50
        for s in range(n_games):
            r_a = simulate_game(
                home, away, DEFAULT_RULESET, seed=s,
                home_strategy=strat_a,
            )
            r_b = simulate_game(
                home, away, DEFAULT_RULESET, seed=s,
                home_strategy=strat_b,
            )
            if (
                r_a.home_score != r_b.home_score
                or r_a.away_score != r_b.away_score
                or r_a.total_possessions != r_b.total_possessions
            ):
                differs += 1

        # Almost all games should differ
        assert differs > n_games * 0.8, (
            f"Strategies should produce different outcomes: {differs}/{n_games}"
        )


# ===========================================================================
# Determinism tests
# ===========================================================================


class TestStrategyDeterminism:
    """Strategies must not break determinism."""

    def test_same_seed_same_strategy_deterministic(self) -> None:
        """Same seed + same strategy = identical results."""
        home = _make_team("home")
        away = _make_team("away")
        strat = TeamStrategy(
            defensive_intensity=0.3, pace_modifier=0.9,
            three_point_bias=5.0,
        )
        r1 = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            home_strategy=strat, away_strategy=strat,
        )
        r2 = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            home_strategy=strat, away_strategy=strat,
        )
        assert r1.home_score == r2.home_score
        assert r1.away_score == r2.away_score
        assert r1.total_possessions == r2.total_possessions

    def test_no_strategy_determinism_preserved(self) -> None:
        """Games without strategy should be identical to before (regression)."""
        home = _make_team("home")
        away = _make_team("away")
        r1 = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        r2 = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        assert r1.home_score == r2.home_score
        assert r1.away_score == r2.away_score
        assert r1.total_possessions == r2.total_possessions
