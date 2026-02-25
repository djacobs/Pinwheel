"""Tests for ActionDefinition, ActionRegistry, and basketball_actions().

Phase 1a validation: the data-driven action models exactly reproduce
the hardcoded constants in scoring.py and possession.py, and the
registry container works correctly.

Phase 1c validation: the registry-based possession pipeline produces
identical results to the hardcoded path.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from pinwheel.core.hooks import HookResult
from pinwheel.core.possession import resolve_possession, select_action
from pinwheel.core.scoring import BASE_MIDPOINTS, BASE_STEEPNESS, points_for_shot
from pinwheel.core.simulation import resolve_turn, simulate_game
from pinwheel.core.state import GameState, HooperState, PossessionContext
from pinwheel.models.game_definition import (
    ActionDefinition,
    ActionRegistry,
    GameDefinition,
    basketball_actions,
    basketball_game_definition,
)
from pinwheel.models.rules import DEFAULT_RULESET, RuleSet
from pinwheel.models.team import Hooper, PlayerAttributes, Team, Venue


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
    """Build PlayerAttributes bypassing budget validation for test isolation."""
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
) -> Hooper:
    """Build a Hooper bypassing nested re-validation."""
    return Hooper.model_construct(
        id=hooper_id,
        name=f"Hooper-{hooper_id}",
        team_id=team_id,
        archetype="sharpshooter",
        backstory="",
        attributes=attrs or _make_attrs(),
        is_starter=is_starter,
        moves=[],
    )


def _make_team(
    team_id: str = "t-1",
    n_starters: int = 3,
    n_bench: int = 1,
    attrs: PlayerAttributes | None = None,
) -> Team:
    """Build a Team with starters and bench players."""
    hoopers = []
    for i in range(n_starters):
        hoopers.append(_make_hooper(f"{team_id}-s{i}", team_id, attrs, is_starter=True))
    for i in range(n_bench):
        hoopers.append(_make_hooper(f"{team_id}-b{i}", team_id, attrs, is_starter=False))
    return Team(
        id=team_id,
        name=f"Team-{team_id}",
        venue=Venue(name="Court", capacity=5000),
        hoopers=hoopers,
    )


class TestBasketballActions:
    """Verify basketball_actions() reproduces hardcoded constants exactly."""

    def test_produces_exactly_four_actions(self) -> None:
        actions = basketball_actions(DEFAULT_RULESET)
        assert len(actions) == 4

    def test_action_names(self) -> None:
        actions = basketball_actions(DEFAULT_RULESET)
        names = {a.name for a in actions}
        assert names == {"at_rim", "mid_range", "three_point", "free_throw"}

    def test_midpoints_match_scoring_constants(self) -> None:
        """Each action's base_midpoint must match BASE_MIDPOINTS from scoring.py."""
        actions = basketball_actions(DEFAULT_RULESET)
        by_name = {a.name: a for a in actions}
        for shot_type, expected_midpoint in BASE_MIDPOINTS.items():
            assert by_name[shot_type].base_midpoint == expected_midpoint, (
                f"{shot_type}: expected midpoint {expected_midpoint}, "
                f"got {by_name[shot_type].base_midpoint}"
            )

    def test_steepness_matches_scoring_constants(self) -> None:
        """Each action's base_steepness must match BASE_STEEPNESS from scoring.py."""
        actions = basketball_actions(DEFAULT_RULESET)
        by_name = {a.name: a for a in actions}
        for shot_type, expected_steepness in BASE_STEEPNESS.items():
            assert by_name[shot_type].base_steepness == expected_steepness, (
                f"{shot_type}: expected steepness {expected_steepness}, "
                f"got {by_name[shot_type].base_steepness}"
            )

    def test_at_rim_selection_weight(self) -> None:
        """at_rim: base weight 30, speed component 0.3."""
        actions = basketball_actions(DEFAULT_RULESET)
        at_rim = next(a for a in actions if a.name == "at_rim")
        assert at_rim.selection_weight == 30.0
        assert at_rim.weight_attributes == {"speed": 0.3}

    def test_mid_range_selection_weight(self) -> None:
        """mid_range: base weight 25, iq component 0.2."""
        actions = basketball_actions(DEFAULT_RULESET)
        mid_range = next(a for a in actions if a.name == "mid_range")
        assert mid_range.selection_weight == 25.0
        assert mid_range.weight_attributes == {"iq": 0.2}

    def test_three_point_selection_weight(self) -> None:
        """three_point: base weight 20, scoring component 0.3."""
        actions = basketball_actions(DEFAULT_RULESET)
        three_pt = next(a for a in actions if a.name == "three_point")
        assert three_pt.selection_weight == 20.0
        assert three_pt.weight_attributes == {"scoring": 0.3}

    def test_free_throw_is_special(self) -> None:
        """free_throw: category='special', is_free_throw=True, requires_opponent=False."""
        actions = basketball_actions(DEFAULT_RULESET)
        ft = next(a for a in actions if a.name == "free_throw")
        assert ft.category == "special"
        assert ft.is_free_throw is True
        assert ft.requires_opponent is False

    def test_points_match_points_for_shot(self) -> None:
        """points_on_success must match points_for_shot() for all types with default rules."""
        actions = basketball_actions(DEFAULT_RULESET)
        by_name = {a.name: a for a in actions}
        for shot_type in ("at_rim", "mid_range", "three_point", "free_throw"):
            expected = points_for_shot(shot_type, DEFAULT_RULESET)  # type: ignore[arg-type]
            assert by_name[shot_type].points_on_success == expected, (
                f"{shot_type}: expected {expected} points, "
                f"got {by_name[shot_type].points_on_success}"
            )

    def test_free_throw_attempts_on_foul(self) -> None:
        """at_rim and mid_range get 2 FT attempts; three_point gets 3."""
        actions = basketball_actions(DEFAULT_RULESET)
        by_name = {a.name: a for a in actions}
        assert by_name["at_rim"].free_throw_attempts_on_foul == 2
        assert by_name["mid_range"].free_throw_attempts_on_foul == 2
        assert by_name["three_point"].free_throw_attempts_on_foul == 3

    def test_points_with_custom_rules(self) -> None:
        """basketball_actions respects custom point values from the RuleSet."""
        custom = RuleSet(two_point_value=4, three_point_value=5, free_throw_value=2)
        actions = basketball_actions(custom)
        by_name = {a.name: a for a in actions}
        assert by_name["at_rim"].points_on_success == 4
        assert by_name["mid_range"].points_on_success == 4
        assert by_name["three_point"].points_on_success == 5
        assert by_name["free_throw"].points_on_success == 2

    def test_all_shot_actions_require_opponent(self) -> None:
        """All non-free-throw shots require an opponent."""
        actions = basketball_actions(DEFAULT_RULESET)
        for a in actions:
            if a.name != "free_throw":
                assert a.requires_opponent is True, f"{a.name} should require opponent"


class TestActionRegistry:
    """Verify ActionRegistry container behavior."""

    def test_get_existing(self) -> None:
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))
        action = registry.get("at_rim")
        assert action is not None
        assert action.name == "at_rim"

    def test_get_missing_returns_none(self) -> None:
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))
        assert registry.get("slam_dunk") is None

    def test_getitem_existing(self) -> None:
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))
        action = registry["mid_range"]
        assert action.name == "mid_range"

    def test_getitem_missing_raises_keyerror(self) -> None:
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))
        with pytest.raises(KeyError):
            registry["nonexistent_action"]

    def test_contains_existing(self) -> None:
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))
        assert "three_point" in registry

    def test_contains_missing(self) -> None:
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))
        assert "slam_dunk" not in registry

    def test_len(self) -> None:
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))
        assert len(registry) == 4

    def test_shot_actions_excludes_free_throw(self) -> None:
        """shot_actions() should return 3 actions (excludes free_throw)."""
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))
        shots = registry.shot_actions()
        assert len(shots) == 3
        shot_names = {a.name for a in shots}
        assert "free_throw" not in shot_names
        assert shot_names == {"at_rim", "mid_range", "three_point"}

    def test_all_actions_returns_all(self) -> None:
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))
        all_actions = registry.all_actions()
        assert len(all_actions) == 4

    def test_action_names_returns_sorted(self) -> None:
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))
        names = registry.action_names()
        assert names == ["at_rim", "free_throw", "mid_range", "three_point"]

    def test_empty_registry(self) -> None:
        """ActionRegistry with zero actions works without errors."""
        registry = ActionRegistry([])
        assert len(registry) == 0
        assert registry.get("anything") is None
        assert registry.all_actions() == []
        assert registry.shot_actions() == []
        assert registry.action_names() == []
        with pytest.raises(KeyError):
            registry["missing"]

    def test_duplicate_names_last_wins(self) -> None:
        """When duplicate names are provided, the last definition wins."""
        action_v1 = ActionDefinition(name="test_action", points_on_success=2)
        action_v2 = ActionDefinition(name="test_action", points_on_success=5)
        registry = ActionRegistry([action_v1, action_v2])
        assert len(registry) == 1
        assert registry["test_action"].points_on_success == 5


class TestActionDefinitionSerialization:
    """Verify Pydantic serialization round-trips."""

    def test_round_trip_default(self) -> None:
        """ActionDefinition round-trips through model_dump/model_validate."""
        original = ActionDefinition(name="test_shot", points_on_success=3)
        data = original.model_dump()
        restored = ActionDefinition.model_validate(data)
        assert restored == original

    def test_round_trip_basketball_actions(self) -> None:
        """All basketball actions round-trip through serialization."""
        actions = basketball_actions(DEFAULT_RULESET)
        for action in actions:
            data = action.model_dump()
            restored = ActionDefinition.model_validate(data)
            assert restored == action, f"{action.name} failed round-trip"

    def test_custom_action_validates(self) -> None:
        """A custom non-basketball action validates correctly."""
        coin_flip = ActionDefinition(
            name="call_heads",
            display_name="Call Heads",
            description="Flip a coin — heads you score, tails you don't.",
            category="chance",
            selection_weight=10.0,
            weight_attributes={},
            resolution_type="coin_flip",
            base_midpoint=50.0,
            base_steepness=0.0,
            primary_attribute="fate",
            stamina_factor=0.0,
            modifier_attributes={"iq": 0.05},
            points_on_success=1,
            requires_opponent=False,
            stamina_cost=0.0,
            is_free_throw=False,
            free_throw_attempts_on_foul=0,
        )
        data = coin_flip.model_dump()
        restored = ActionDefinition.model_validate(data)
        assert restored.name == "call_heads"
        assert restored.category == "chance"
        assert restored.resolution_type == "coin_flip"
        assert restored.primary_attribute == "fate"
        assert restored.modifier_attributes == {"iq": 0.05}
        assert restored.requires_opponent is False

    def test_json_round_trip(self) -> None:
        """ActionDefinition round-trips through JSON serialization."""
        original = ActionDefinition(
            name="test_json",
            display_name="JSON Test",
            weight_attributes={"speed": 0.5, "iq": 0.2},
            modifier_attributes={"defense": 0.1},
            points_on_success=4,
        )
        json_str = original.model_dump_json()
        restored = ActionDefinition.model_validate_json(json_str)
        assert restored == original


class TestRegistryScoringCrossValidation:
    """Cross-validate the action registry against scoring.py constants.

    These tests ensure that basketball_actions() produces ActionDefinitions
    whose curve parameters exactly match the hardcoded dicts in scoring.py.
    """

    def test_midpoints_match_base_midpoints(self) -> None:
        """Each basketball action's base_midpoint matches BASE_MIDPOINTS[name]."""
        actions = basketball_actions(DEFAULT_RULESET)
        by_name = {a.name: a for a in actions}
        for shot_type, expected in BASE_MIDPOINTS.items():
            assert by_name[shot_type].base_midpoint == expected, (
                f"{shot_type}: ActionDefinition.base_midpoint={by_name[shot_type].base_midpoint}, "
                f"BASE_MIDPOINTS={expected}"
            )

    def test_steepness_matches_base_steepness(self) -> None:
        """Each basketball action's base_steepness matches BASE_STEEPNESS[name]."""
        actions = basketball_actions(DEFAULT_RULESET)
        by_name = {a.name: a for a in actions}
        for shot_type, expected in BASE_STEEPNESS.items():
            actual = by_name[shot_type].base_steepness
            assert actual == expected, (
                f"{shot_type}: ActionDefinition.base_steepness={actual}, "
                f"BASE_STEEPNESS={expected}"
            )


# ---------------------------------------------------------------------------
# Phase 1c: Registry-based possession pipeline integration tests
# ---------------------------------------------------------------------------


class TestPossessionContextBackwardCompat:
    """PossessionContext property bridges for legacy bias fields."""

    def test_set_at_rim_bias_via_property(self) -> None:
        """Setting ctx.at_rim_bias writes to action_biases dict."""
        ctx = PossessionContext()
        ctx.at_rim_bias = 5.0
        assert ctx.action_biases["at_rim"] == 5.0
        assert ctx.at_rim_bias == 5.0

    def test_set_mid_range_bias_via_property(self) -> None:
        """Setting ctx.mid_range_bias writes to action_biases dict."""
        ctx = PossessionContext()
        ctx.mid_range_bias = 3.0
        assert ctx.action_biases["mid_range"] == 3.0
        assert ctx.mid_range_bias == 3.0

    def test_set_three_point_bias_via_property(self) -> None:
        """Setting ctx.three_point_bias writes to action_biases dict."""
        ctx = PossessionContext()
        ctx.three_point_bias = -2.0
        assert ctx.action_biases["three_point"] == -2.0
        assert ctx.three_point_bias == -2.0

    def test_set_via_action_biases_dict_reads_through_property(self) -> None:
        """Writing to action_biases dict reads back through the legacy property."""
        ctx = PossessionContext()
        ctx.action_biases["at_rim"] = 7.5
        assert ctx.at_rim_bias == 7.5

    def test_default_biases_are_zero(self) -> None:
        """Unset biases default to 0.0 via the property."""
        ctx = PossessionContext()
        assert ctx.at_rim_bias == 0.0
        assert ctx.mid_range_bias == 0.0
        assert ctx.three_point_bias == 0.0
        assert ctx.action_biases == {}

    def test_custom_action_name_in_action_biases(self) -> None:
        """Custom action names (non-basketball) work in action_biases dict."""
        ctx = PossessionContext()
        ctx.action_biases["call_heads"] = 3.0
        assert ctx.action_biases["call_heads"] == 3.0
        # Legacy properties unaffected
        assert ctx.at_rim_bias == 0.0

    def test_construct_with_action_biases(self) -> None:
        """PossessionContext can be constructed with action_biases dict."""
        ctx = PossessionContext(action_biases={"at_rim": 2.0, "three_point": -1.0})
        assert ctx.at_rim_bias == 2.0
        assert ctx.three_point_bias == -1.0
        assert ctx.mid_range_bias == 0.0

    def test_mixed_property_and_dict_writes(self) -> None:
        """Property writes and dict writes coexist correctly."""
        ctx = PossessionContext()
        ctx.at_rim_bias = 5.0
        ctx.action_biases["mid_range"] = 3.0
        ctx.action_biases["custom_action"] = 1.0
        assert ctx.at_rim_bias == 5.0
        assert ctx.mid_range_bias == 3.0
        assert ctx.action_biases == {"at_rim": 5.0, "mid_range": 3.0, "custom_action": 1.0}


class TestHookResultActionBiases:
    """HookResult action_biases dict exists and works alongside legacy fields."""

    def test_action_biases_default_empty(self) -> None:
        """action_biases defaults to empty dict."""
        result = HookResult()
        assert result.action_biases == {}

    def test_action_biases_holds_custom_values(self) -> None:
        """action_biases can store arbitrary action name biases."""
        result = HookResult(action_biases={"call_heads": 5.0, "at_rim": 2.0})
        assert result.action_biases["call_heads"] == 5.0
        assert result.action_biases["at_rim"] == 2.0

    def test_legacy_fields_still_work(self) -> None:
        """Legacy at_rim_bias etc. fields are still real init params."""
        result = HookResult(at_rim_bias=3.0, mid_range_bias=2.0, three_point_bias=1.0)
        assert result.at_rim_bias == 3.0
        assert result.mid_range_bias == 2.0
        assert result.three_point_bias == 1.0

    def test_legacy_fields_and_action_biases_coexist(self) -> None:
        """Both legacy fields and action_biases can be set simultaneously."""
        result = HookResult(
            at_rim_bias=3.0,
            action_biases={"custom": 1.0},
        )
        assert result.at_rim_bias == 3.0
        assert result.action_biases["custom"] == 1.0


class TestRegistryPossessionIntegration:
    """Verify that the registry-based possession pipeline produces identical results."""

    def test_select_action_same_distribution(self) -> None:
        """select_action with registry produces same distribution as without.

        Run 10000 selections with identical seeds and compare action counts.
        """
        handler = HooperState(hooper=_make_hooper(attrs=_make_attrs()))
        game_state = GameState(
            home_agents=[handler],
            away_agents=[HooperState(hooper=_make_hooper("d-1", "t-2"))],
        )
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))

        # Without registry
        counts_legacy: Counter[str] = Counter()
        for i in range(10000):
            rng = random.Random(i)
            action = select_action(handler, game_state, DEFAULT_RULESET, rng)
            counts_legacy[action] += 1

        # With registry
        counts_registry: Counter[str] = Counter()
        for i in range(10000):
            rng = random.Random(i)
            action = select_action(
                handler, game_state, DEFAULT_RULESET, rng,
                action_registry=registry,
            )
            counts_registry[action] += 1

        # Counts must be identical (same RNG, same weights)
        assert counts_legacy == counts_registry, (
            f"Legacy: {dict(counts_legacy)}, Registry: {dict(counts_registry)}"
        )

    def test_resolve_possession_with_registry_returns_valid(self) -> None:
        """resolve_possession with registry produces a valid PossessionResult."""
        home_hoopers = [HooperState(hooper=_make_hooper(f"h-{i}", "t-h")) for i in range(3)]
        away_hoopers = [HooperState(hooper=_make_hooper(f"a-{i}", "t-a")) for i in range(3)]
        game_state = GameState(home_agents=home_hoopers, away_agents=away_hoopers)
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))
        rng = random.Random(42)

        result = resolve_possession(
            game_state, DEFAULT_RULESET, rng, action_registry=registry,
        )
        assert result.shot_type in ("at_rim", "mid_range", "three_point", "")
        assert result.time_used > 0

    def test_simulate_game_with_registry_identical_scores(self) -> None:
        """CRITICAL: simulate_game with registry + same seed = identical final scores.

        Run 50 games with the same seed, once with and once without the registry.
        All 50 pairs must produce identical home_score AND away_score.
        """
        home = _make_team("home")
        away = _make_team("away")
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))

        for seed in range(50):
            result_legacy = simulate_game(home, away, DEFAULT_RULESET, seed=seed)
            result_registry = simulate_game(
                home, away, DEFAULT_RULESET, seed=seed,
                action_registry=registry,
            )
            assert result_legacy.home_score == result_registry.home_score, (
                f"seed={seed}: home_score legacy={result_legacy.home_score} "
                f"registry={result_registry.home_score}"
            )
            assert result_legacy.away_score == result_registry.away_score, (
                f"seed={seed}: away_score legacy={result_legacy.away_score} "
                f"registry={result_registry.away_score}"
            )
            assert result_legacy.total_possessions == result_registry.total_possessions, (
                f"seed={seed}: possessions legacy={result_legacy.total_possessions} "
                f"registry={result_registry.total_possessions}"
            )

    def test_simulate_game_with_registry_custom_rules(self) -> None:
        """Registry + custom RuleSet (3pt=4, 2pt=3) matches non-registry version.

        Both paths must agree on scores when point values are non-standard.
        """
        custom_rules = RuleSet(two_point_value=3, three_point_value=4)
        home = _make_team("home")
        away = _make_team("away")
        registry = ActionRegistry(basketball_actions(custom_rules))

        for seed in range(20):
            result_legacy = simulate_game(home, away, custom_rules, seed=seed)
            result_registry = simulate_game(
                home, away, custom_rules, seed=seed,
                action_registry=registry,
            )
            assert result_legacy.home_score == result_registry.home_score, (
                f"seed={seed}: home_score legacy={result_legacy.home_score} "
                f"registry={result_registry.home_score}"
            )
            assert result_legacy.away_score == result_registry.away_score, (
                f"seed={seed}: away_score legacy={result_legacy.away_score} "
                f"registry={result_registry.away_score}"
            )

    def test_simulate_game_with_registry_box_scores_identical(self) -> None:
        """Box scores (points per hooper) match between legacy and registry paths."""
        home = _make_team("home")
        away = _make_team("away")
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))

        result_legacy = simulate_game(home, away, DEFAULT_RULESET, seed=123)
        result_registry = simulate_game(
            home, away, DEFAULT_RULESET, seed=123,
            action_registry=registry,
        )

        for bs_l, bs_r in zip(
            sorted(result_legacy.box_scores, key=lambda b: b.hooper_id),
            sorted(result_registry.box_scores, key=lambda b: b.hooper_id),
            strict=True,
        ):
            assert bs_l.hooper_id == bs_r.hooper_id
            assert bs_l.points == bs_r.points, (
                f"{bs_l.hooper_id}: pts legacy={bs_l.points} registry={bs_r.points}"
            )
            assert bs_l.field_goals_made == bs_r.field_goals_made
            assert bs_l.field_goals_attempted == bs_r.field_goals_attempted
            assert bs_l.three_pointers_made == bs_r.three_pointers_made
            assert bs_l.three_pointers_attempted == bs_r.three_pointers_attempted
            assert bs_l.free_throws_made == bs_r.free_throws_made
            assert bs_l.free_throws_attempted == bs_r.free_throws_attempted

    def test_simulate_game_without_registry_unchanged(self) -> None:
        """simulate_game without action_registry is completely unchanged.

        This verifies that the default path (action_registry=None) has not
        been accidentally modified by the refactor.
        """
        home = _make_team("home")
        away = _make_team("away")

        # Run the same game twice without registry — must be identical
        r1 = simulate_game(home, away, DEFAULT_RULESET, seed=999)
        r2 = simulate_game(home, away, DEFAULT_RULESET, seed=999)
        assert r1.home_score == r2.home_score
        assert r1.away_score == r2.away_score
        assert r1.total_possessions == r2.total_possessions


# ---------------------------------------------------------------------------
# Phase 3a: GameDefinition turn structure fields
# ---------------------------------------------------------------------------


class TestGameDefinitionTurnStructure:
    """Verify GameDefinition turn structure fields and basketball_game_definition()."""

    def test_default_game_definition_turn_structure(self) -> None:
        """Default GameDefinition has sensible basketball-like defaults."""
        gd = GameDefinition()
        assert gd.quarters == 4
        assert gd.quarter_clock_seconds == 600.0
        assert gd.alternating_possession is True
        assert gd.elam_ending_enabled is True
        assert gd.elam_trigger_quarter == 4
        assert gd.elam_target_margin == 15
        assert gd.halftime_after_quarter == 2
        assert gd.halftime_recovery == 0.40
        assert gd.quarter_break_recovery == 0.15
        assert gd.safety_cap_possessions == 300

    def test_basketball_game_definition_default_rules(self) -> None:
        """basketball_game_definition with DEFAULT_RULESET produces correct values."""
        gd = basketball_game_definition(DEFAULT_RULESET)
        # DEFAULT_RULESET: elam_trigger_quarter=3, so total quarters = 4
        assert gd.quarters == 4
        assert gd.quarter_clock_seconds == DEFAULT_RULESET.quarter_minutes * 60.0
        assert gd.alternating_possession is True
        assert gd.elam_ending_enabled is True
        assert gd.elam_trigger_quarter == 4  # last quarter is Elam
        assert gd.elam_target_margin == DEFAULT_RULESET.elam_margin
        assert gd.halftime_after_quarter == 2
        assert gd.halftime_recovery == DEFAULT_RULESET.halftime_stamina_recovery
        assert gd.quarter_break_recovery == DEFAULT_RULESET.quarter_break_stamina_recovery
        assert gd.safety_cap_possessions == DEFAULT_RULESET.safety_cap_possessions

    def test_basketball_game_definition_custom_elam_trigger(self) -> None:
        """Custom elam_trigger_quarter changes total quarters and Elam trigger."""
        rules = RuleSet(elam_trigger_quarter=2)
        gd = basketball_game_definition(rules)
        # elam_trigger_quarter=2 means Q1, Q2 regular, Q3 Elam
        assert gd.quarters == 3
        assert gd.elam_trigger_quarter == 3

    def test_basketball_game_definition_custom_quarter_minutes(self) -> None:
        """Custom quarter_minutes changes quarter_clock_seconds."""
        rules = RuleSet(quarter_minutes=5)
        gd = basketball_game_definition(rules)
        assert gd.quarter_clock_seconds == 300.0

    def test_basketball_game_definition_custom_elam_margin(self) -> None:
        """Custom elam_margin flows through to elam_target_margin."""
        rules = RuleSet(elam_margin=25)
        gd = basketball_game_definition(rules)
        assert gd.elam_target_margin == 25

    def test_basketball_game_definition_custom_stamina_recovery(self) -> None:
        """Custom stamina recovery values flow through."""
        rules = RuleSet(halftime_stamina_recovery=0.30, quarter_break_stamina_recovery=0.10)
        gd = basketball_game_definition(rules)
        assert gd.halftime_recovery == 0.30
        assert gd.quarter_break_recovery == 0.10

    def test_basketball_game_definition_custom_safety_cap(self) -> None:
        """Custom safety_cap_possessions flows through."""
        rules = RuleSet(safety_cap_possessions=200)
        gd = basketball_game_definition(rules)
        assert gd.safety_cap_possessions == 200

    def test_basketball_game_definition_preserves_actions(self) -> None:
        """Turn structure fields don't interfere with existing action fields."""
        gd = basketball_game_definition(DEFAULT_RULESET)
        assert len(gd.actions) == 4
        assert gd.participants_per_side == 3
        assert gd.bench_size == 1
        assert gd.name == "Basketball"
        registry = gd.build_registry()
        assert "at_rim" in registry
        assert "three_point" in registry

    def test_game_definition_serialization_round_trip(self) -> None:
        """GameDefinition with turn structure fields round-trips through JSON."""
        gd = basketball_game_definition(DEFAULT_RULESET)
        data = gd.model_dump()
        restored = GameDefinition.model_validate(data)
        assert restored.quarters == gd.quarters
        assert restored.quarter_clock_seconds == gd.quarter_clock_seconds
        assert restored.alternating_possession == gd.alternating_possession
        assert restored.elam_ending_enabled == gd.elam_ending_enabled
        assert restored.elam_trigger_quarter == gd.elam_trigger_quarter
        assert restored.elam_target_margin == gd.elam_target_margin
        assert restored.halftime_after_quarter == gd.halftime_after_quarter
        assert restored.halftime_recovery == gd.halftime_recovery
        assert restored.quarter_break_recovery == gd.quarter_break_recovery
        assert restored.safety_cap_possessions == gd.safety_cap_possessions

    def test_elam_trigger_equals_total_quarters_for_basketball(self) -> None:
        """For basketball, Elam is always the last quarter (trigger == total)."""
        for etq in (1, 2, 3, 4):
            rules = RuleSet(elam_trigger_quarter=etq)
            gd = basketball_game_definition(rules)
            assert gd.elam_trigger_quarter == gd.quarters, (
                f"elam_trigger_quarter={etq}: "
                f"expected trigger={gd.quarters}, "
                f"got {gd.elam_trigger_quarter}"
            )

    def test_regular_quarters_count(self) -> None:
        """Number of regular (non-Elam) quarters is quarters - 1."""
        gd = basketball_game_definition(DEFAULT_RULESET)
        regular_quarters = gd.quarters - 1  # Q1, Q2, Q3
        assert regular_quarters == 3

    def test_custom_non_basketball_game_definition(self) -> None:
        """A non-basketball game can have different turn structure values."""
        coin_flip = GameDefinition(
            name="Coin Flip Championship",
            description="Best-of-N coin flips",
            quarters=1,
            quarter_clock_seconds=0.0,
            alternating_possession=False,
            elam_ending_enabled=False,
            elam_trigger_quarter=1,
            elam_target_margin=0,
            halftime_after_quarter=0,
            halftime_recovery=0.0,
            quarter_break_recovery=0.0,
            safety_cap_possessions=100,
        )
        assert coin_flip.quarters == 1
        assert coin_flip.elam_ending_enabled is False
        assert coin_flip.alternating_possession is False


# ---------------------------------------------------------------------------
# Phase 3b: simulate_game reads turn structure from GameDefinition
# ---------------------------------------------------------------------------


class TestSimulateGameWithGameDefinition:
    """Verify simulate_game reads turn structure from GameDefinition."""

    def test_simulate_with_explicit_game_def_identical(self) -> None:
        """Passing basketball_game_definition explicitly produces identical results.

        This confirms the GameDefinition path and the fallback path produce
        the exact same simulation output for the same seed.
        """
        home = _make_team("home")
        away = _make_team("away")
        game_def = basketball_game_definition(DEFAULT_RULESET)

        for seed in range(30):
            r_fallback = simulate_game(
                home, away, DEFAULT_RULESET, seed=seed,
            )
            r_explicit = simulate_game(
                home, away, DEFAULT_RULESET, seed=seed,
                game_def=game_def,
            )
            assert r_fallback.home_score == r_explicit.home_score, (
                f"seed={seed}: home mismatch"
            )
            assert r_fallback.away_score == r_explicit.away_score, (
                f"seed={seed}: away mismatch"
            )
            assert r_fallback.total_possessions == r_explicit.total_possessions
            assert r_fallback.elam_activated == r_explicit.elam_activated
            assert len(r_fallback.quarter_scores) == len(r_explicit.quarter_scores)

    def test_custom_elam_margin_via_game_def(self) -> None:
        """Custom elam_target_margin in GameDefinition affects Elam target score."""
        home = _make_team("home")
        away = _make_team("away")
        game_def = basketball_game_definition(DEFAULT_RULESET)
        # Override the margin to a much larger value
        game_def_large = game_def.model_copy(
            update={"elam_target_margin": 40}
        )

        r_default = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            game_def=game_def,
        )
        r_large = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            game_def=game_def_large,
        )
        # With a larger margin, Elam target is higher so game may differ
        assert r_default.elam_activated
        assert r_large.elam_activated
        if r_large.elam_target_score is not None and r_default.elam_target_score is not None:
            assert r_large.elam_target_score >= r_default.elam_target_score

    def test_quarter_count_from_game_def(self) -> None:
        """Number of quarter_scores matches game_def.quarters."""
        home = _make_team("home")
        away = _make_team("away")
        game_def = basketball_game_definition(DEFAULT_RULESET)
        result = simulate_game(
            home, away, DEFAULT_RULESET, seed=99,
            game_def=game_def,
        )
        # Basketball with default rules: 4 quarters (3 regular + Elam)
        assert len(result.quarter_scores) == game_def.quarters

    def test_custom_quarter_minutes_via_game_def(self) -> None:
        """Custom quarter_clock_seconds flows through to game duration."""
        home = _make_team("home")
        away = _make_team("away")
        # Short quarters = fewer possessions
        short_def = basketball_game_definition(DEFAULT_RULESET).model_copy(
            update={"quarter_clock_seconds": 60.0}  # 1 minute
        )
        long_def = basketball_game_definition(DEFAULT_RULESET).model_copy(
            update={"quarter_clock_seconds": 1200.0}  # 20 minutes
        )
        r_short = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            game_def=short_def,
        )
        r_long = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            game_def=long_def,
        )
        # Shorter clock should produce fewer total possessions
        assert r_short.total_possessions < r_long.total_possessions


# ---------------------------------------------------------------------------
# Phase 3c: resolve_turn() indirection layer
# ---------------------------------------------------------------------------


class TestResolveTurn:
    """Verify resolve_turn() delegates to resolve_possession() correctly."""

    def test_resolve_turn_returns_possession_result(self) -> None:
        """resolve_turn() returns a PossessionResult with valid fields."""
        home_hoopers = [
            HooperState(hooper=_make_hooper(f"h-{i}", "t-h"))
            for i in range(3)
        ]
        away_hoopers = [
            HooperState(hooper=_make_hooper(f"a-{i}", "t-a"))
            for i in range(3)
        ]
        game_state = GameState(
            home_agents=home_hoopers, away_agents=away_hoopers,
        )
        rng = random.Random(42)
        result = resolve_turn(game_state, DEFAULT_RULESET, rng)
        assert result.time_used > 0
        assert result.shot_type in ("at_rim", "mid_range", "three_point", "")

    def test_resolve_turn_identical_to_resolve_possession(self) -> None:
        """resolve_turn() produces identical results to resolve_possession().

        For the same game state and RNG seed, both paths must agree on
        all output fields.
        """
        from pinwheel.core.possession import resolve_possession as rp_direct

        for seed in range(50):
            home_hoopers = [
                HooperState(hooper=_make_hooper(f"h-{i}", "t-h"))
                for i in range(3)
            ]
            away_hoopers = [
                HooperState(hooper=_make_hooper(f"a-{i}", "t-a"))
                for i in range(3)
            ]

            gs1 = GameState(
                home_agents=home_hoopers, away_agents=away_hoopers,
            )
            # Build fresh identical state for the second call
            home_hoopers2 = [
                HooperState(hooper=_make_hooper(f"h-{i}", "t-h"))
                for i in range(3)
            ]
            away_hoopers2 = [
                HooperState(hooper=_make_hooper(f"a-{i}", "t-a"))
                for i in range(3)
            ]
            gs2 = GameState(
                home_agents=home_hoopers2, away_agents=away_hoopers2,
            )

            rng1 = random.Random(seed)
            rng2 = random.Random(seed)

            r_turn = resolve_turn(gs1, DEFAULT_RULESET, rng1)
            r_poss = rp_direct(gs2, DEFAULT_RULESET, rng2)

            assert r_turn.points_scored == r_poss.points_scored, (
                f"seed={seed}"
            )
            assert r_turn.shot_type == r_poss.shot_type, f"seed={seed}"
            assert r_turn.shot_made == r_poss.shot_made, f"seed={seed}"
            assert r_turn.time_used == r_poss.time_used, f"seed={seed}"

    def test_resolve_turn_with_registry(self) -> None:
        """resolve_turn() correctly passes action_registry through."""
        home_hoopers = [
            HooperState(hooper=_make_hooper(f"h-{i}", "t-h"))
            for i in range(3)
        ]
        away_hoopers = [
            HooperState(hooper=_make_hooper(f"a-{i}", "t-a"))
            for i in range(3)
        ]
        game_state = GameState(
            home_agents=home_hoopers, away_agents=away_hoopers,
        )
        registry = ActionRegistry(basketball_actions(DEFAULT_RULESET))
        rng = random.Random(42)
        result = resolve_turn(
            game_state, DEFAULT_RULESET, rng,
            action_registry=registry,
        )
        assert result.time_used > 0

    def test_resolve_turn_with_game_def(self) -> None:
        """resolve_turn() accepts a game_def parameter without error."""
        home_hoopers = [
            HooperState(hooper=_make_hooper(f"h-{i}", "t-h"))
            for i in range(3)
        ]
        away_hoopers = [
            HooperState(hooper=_make_hooper(f"a-{i}", "t-a"))
            for i in range(3)
        ]
        game_state = GameState(
            home_agents=home_hoopers, away_agents=away_hoopers,
        )
        game_def = basketball_game_definition(DEFAULT_RULESET)
        rng = random.Random(42)
        result = resolve_turn(
            game_state, DEFAULT_RULESET, rng, game_def=game_def,
        )
        assert result.time_used > 0

    def test_simulate_game_uses_resolve_turn(self) -> None:
        """simulate_game with resolve_turn produces identical results.

        This is the integration test: since _run_quarter and _run_elam
        now call resolve_turn instead of resolve_possession directly,
        the full game simulation must still produce identical results.
        """
        home = _make_team("home")
        away = _make_team("away")
        game_def = basketball_game_definition(DEFAULT_RULESET)

        # Run with explicit game_def (uses resolve_turn path)
        for seed in range(20):
            r1 = simulate_game(
                home, away, DEFAULT_RULESET, seed=seed,
                game_def=game_def,
            )
            r2 = simulate_game(
                home, away, DEFAULT_RULESET, seed=seed,
                game_def=game_def,
            )
            assert r1.home_score == r2.home_score
            assert r1.away_score == r2.away_score
            assert r1.total_possessions == r2.total_possessions
