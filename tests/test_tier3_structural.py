"""Tier-3 structural governance surface (Phase 5).

Covers: violation nodes activated as data (backcourt, three-second paint
dwell, lane, kicked ball, goaltending), FIBA-3x3 structure options
(check-ball restarts, clear-arc rule, target score), transition foul
subtypes (take / clear-path / away-from-play), the possession-level
transition tag, injury events, GameDefinitionPatch.modify_transitions,
and chain-reachability validation.

Everything here is OFF by default — the companion invariant (pinned by
the seed-snapshot test in test_possession_micro.py) is that none of these
features draw RNG until governance activates them.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pinwheel.core.game_def_validation import validate_game_def_patch
from pinwheel.core.narrate import narrate_event, narrate_play
from pinwheel.core.simulation import simulate_game
from pinwheel.models.game import GameResult
from pinwheel.models.game_definition import (
    GameDefinition,
    GameDefinitionPatch,
    basketball_game_definition,
)
from pinwheel.models.rules import DEFAULT_RULESET, RuleSet
from tests.test_possession_micro import (
    _all_event_types,
    _micro_game_def,
    _team,
)

# Tier-3 event types that must NEVER appear on the default path.
TIER3_EVENT_TYPES = {
    "violation.backcourt",
    "violation.three_second",
    "violation.lane",
    "violation.kicked_ball",
    "violation.goaltending",
    "violation.clear_arc",
    "foul.take",
    "foul.clear_path",
    "foul.away_from_play",
    "admin.check_ball",
    "admin.clear_arc",
    "injury",
}


def _run_seeds(
    game_def: GameDefinition,
    rules: RuleSet = DEFAULT_RULESET,
    seeds: range = range(8),
) -> list[GameResult]:
    return [
        simulate_game(_team("h"), _team("a"), rules, seed=s, game_def=game_def)
        for s in seeds
    ]


def _event_types_across(results: list[GameResult]) -> set[str]:
    return {t for r in results for t in _all_event_types(r)}


# --- Default-path inertness --------------------------------------------------


class TestTier3DefaultsInert:
    def test_no_tier3_events_on_default_path(self):
        results = _run_seeds(_micro_game_def(), seeds=range(5))
        assert not (_event_types_across(results) & TIER3_EVENT_TYPES)

    def test_default_game_def_carries_tier3_nodes(self):
        # The governable surface ships in the definition even though it is
        # inert — governance patches reference these nodes by name.
        names = {a.name for a in basketball_game_definition(DEFAULT_RULESET).actions}
        assert {
            "violation_backcourt", "violation_three_second", "violation_lane",
            "violation_kicked_ball", "violation_goaltending",
            "foul_take", "foul_clear_path", "foul_away_from_play",
        } <= names

    def test_tier3_structure_flags_default_off(self):
        game_def = basketball_game_definition(DEFAULT_RULESET)
        assert game_def.check_ball_restarts is False
        assert game_def.clear_arc_required is False
        assert game_def.target_score == 0


# --- Violations as data ------------------------------------------------------


class TestViolationNodes:
    def test_backcourt_activated_by_modify_transitions(self):
        patch = GameDefinitionPatch(
            modify_transitions={"initiate": {"violation_backcourt": 6.0}},
            description="Backcourt violations are live",
        )
        results = _run_seeds(patch.apply(_micro_game_def()))
        assert "violation.backcourt" in _event_types_across(results)

    def test_violation_strictness_zero_silences_activated_violations(self):
        patch = GameDefinitionPatch(
            modify_transitions={"initiate": {"violation_backcourt": 6.0}},
        )
        rules = RuleSet(violation_strictness=0.0)
        results = _run_seeds(
            patch.apply(_micro_game_def(rules)), rules=rules,
        )
        assert "violation.backcourt" not in _event_types_across(results)

    def test_three_second_requires_paint_dwell(self):
        # The paint-dwell edge hangs off pass_entry (which puts the ball in
        # the paint); the streak multiplier only arms it once the offense
        # is actually camped there.
        patch = GameDefinitionPatch(
            modify_transitions={
                "initiate": {"pass_entry": 40.0},
                "pass_entry": {"violation_three_second": 60.0},
            },
            description="Three seconds in the key is a violation",
        )
        results = _run_seeds(patch.apply(_micro_game_def()))
        assert "violation.three_second" in _event_types_across(results)

    def test_three_second_never_fires_from_the_perimeter(self):
        # Same edge weight attached where the ball is NEVER in the paint —
        # the streak multiplier keeps the weight at zero.
        patch = GameDefinitionPatch(
            modify_transitions={"initiate": {"violation_three_second": 60.0}},
        )
        results = _run_seeds(patch.apply(_micro_game_def()))
        assert "violation.three_second" not in _event_types_across(results)

    def test_kicked_ball_offense_retains_possession(self):
        patch = GameDefinitionPatch(
            modify_transitions={"initiate": {"violation_kicked_ball": 8.0}},
        )
        results = _run_seeds(patch.apply(_micro_game_def()))
        continued = False
        for r in results:
            for p in r.possession_log:
                types = [e.event_type for e in p.events]
                if "violation.kicked_ball" in types:
                    idx = types.index("violation.kicked_ball")
                    ev = p.events[idx]
                    assert ev.outcome == "offense_retains"
                    if idx < len(types) - 1:
                        continued = True
        assert continued, "kicked-ball possessions must continue afterward"

    def test_goaltending_awards_the_basket(self):
        patch = GameDefinitionPatch(
            modify_actions={"violation_goaltending": {"selection_weight": 0.5}},
            description="Goaltending is called half the time",
        )
        results = _run_seeds(patch.apply(_micro_game_def()))
        goaltends = 0
        for r in results:
            for p in r.possession_log:
                for i, e in enumerate(p.events):
                    if e.event_type == "violation.goaltending":
                        goaltends += 1
                        assert e.outcome == "basket_awarded"
                        # The immediately preceding shot counts as made.
                        shot = p.events[i - 1]
                        assert shot.event_type.startswith("shot.")
                        assert shot.outcome == "made"
                        assert "goaltended" in shot.tags
        assert goaltends > 0

    def test_goaltending_box_scores_stay_consistent(self):
        patch = GameDefinitionPatch(
            modify_actions={"violation_goaltending": {"selection_weight": 0.5}},
        )
        for r in _run_seeds(patch.apply(_micro_game_def()), seeds=range(4)):
            assert sum(b.points for b in r.box_scores) == (
                r.home_score + r.away_score
            )


# --- FIBA-3x3 structure options ---------------------------------------------


FIBA_PATCH = GameDefinitionPatch(
    modify_structure={
        "check_ball_restarts": True,
        "clear_arc_required": True,
        "target_score": 21,
        "elam_ending_enabled": False,
    },
    description="FIBA 3x3: check-ball, clear-arc, first to 21",
)


class TestFiba3x3Structure:
    def test_fiba_patch_validates(self):
        assert validate_game_def_patch(
            FIBA_PATCH.model_dump(mode="json"), RuleSet(),
        ) == []

    def test_fiba_patch_produces_check_ball_and_clear_arc_events(self):
        rules = RuleSet(shot_clock_seconds=12)
        results = _run_seeds(
            FIBA_PATCH.apply(_micro_game_def(rules)), rules=rules,
        )
        types = _event_types_across(results)
        assert "admin.check_ball" in types
        assert "admin.clear_arc" in types
        # Across 8 seeded games the clearance failure fires at least once.
        assert "violation.clear_arc" in types

    def test_first_to_target_ends_the_game(self):
        results = _run_seeds(FIBA_PATCH.apply(_micro_game_def()))
        for r in results:
            assert not r.elam_activated
            winner = max(r.home_score, r.away_score)
            assert winner >= 21, "someone reaches 21 at these scoring rates"
            # The game stops at the target — a full game runs ~99
            # possessions; first-to-21 is dramatically shorter.
            assert r.total_possessions < 60

    def test_target_score_and_elam_are_mutually_exclusive(self):
        patch = GameDefinitionPatch(
            modify_structure={"target_score": 21},  # Elam left enabled
        )
        violations = validate_game_def_patch(
            patch.model_dump(mode="json"), RuleSet(),
        )
        assert any("mutually exclusive" in v for v in violations)

    def test_target_score_bounds(self):
        patch = GameDefinitionPatch(
            modify_structure={"target_score": 2, "elam_ending_enabled": False},
        )
        violations = validate_game_def_patch(
            patch.model_dump(mode="json"), RuleSet(),
        )
        assert any("target_score" in v for v in violations)


# --- Transition foul subtypes ------------------------------------------------


class TestTransitionFouls:
    FOUL_PATCH = GameDefinitionPatch(
        modify_transitions={
            "initiate": {
                "foul_take": 8.0,
                "foul_clear_path": 5.0,
                "foul_away_from_play": 3.0,
            },
        },
        description="Defenders foul to stop the break",
    )

    def _results(self) -> list[GameResult]:
        return _run_seeds(self.FOUL_PATCH.apply(_micro_game_def()))

    def test_transition_fouls_occur(self):
        types = _event_types_across(self._results())
        assert "foul.take" in types
        assert "foul.clear_path" in types

    def test_transition_fouls_only_on_transition_possessions(self):
        for r in self._results():
            for p in r.possession_log:
                types = [e.event_type for e in p.events]
                if any(t.startswith("foul.take") for t in types):
                    assert "transition" in p.tags, (
                        "take fouls must emerge from transition possessions"
                    )

    def test_clear_path_awards_free_throw_and_ball(self):
        found_ft = False
        for r in self._results():
            for p in r.possession_log:
                types = [e.event_type for e in p.events]
                if "foul.clear_path" not in types:
                    continue
                idx = types.index("foul.clear_path")
                rest = types[idx + 1:]
                if "shot.free_throw" in rest:
                    found_ft = True
                    # The possession continues after the free throw —
                    # the offense keeps the ball.
                    ft_idx = idx + 1 + rest.index("shot.free_throw")
                    assert ft_idx < len(types) - 1
        assert found_ft

    def test_box_score_points_match_final_score_with_chain_fts(self):
        # Clear-path free throws are banked mid-chain — the accounting
        # must survive any possession ending, including turnovers.
        for r in self._results():
            assert sum(b.points for b in r.box_scores) == (
                r.home_score + r.away_score
            )

    def test_take_fouls_count_personal_fouls(self):
        base = _run_seeds(_micro_game_def())
        fouled = self._results()
        base_fouls = sum(b.fouls for r in base for b in r.box_scores)
        patched_fouls = sum(b.fouls for r in fouled for b in r.box_scores)
        assert patched_fouls > base_fouls


# --- Possession-level transition tag ----------------------------------------


class TestTransitionTag:
    def test_transition_possessions_tagged_in_summary_log(self):
        r = simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            game_def=_micro_game_def(),
        )
        tagged = [p for p in r.possession_log if "transition" in p.tags]
        assert tagged, "seeded games open possessions in transition"
        for p in tagged:
            shot_events = [
                e for e in p.events
                if e.event_type.startswith("shot.")
                and e.event_type != "shot.free_throw"
            ]
            if shot_events:
                assert "transition" in shot_events[0].tags

    def test_narration_says_on_the_break(self):
        line = narrate_play(
            player="Ada", defender="Bo", action="at_rim", result="made",
            points=2, seed=1, transition=True,
        )
        assert line.startswith("On the break — ")


# --- Injury events -----------------------------------------------------------


class TestInjuryEvents:
    def test_injuries_off_by_default(self):
        results = _run_seeds(_micro_game_def(), seeds=range(5))
        assert "injury" not in _event_types_across(results)

    def test_injury_rate_produces_injury_events(self):
        rules = RuleSet(injury_rate=0.05)
        results = _run_seeds(_micro_game_def(rules), rules=rules, seeds=range(4))
        types = [t for r in results for t in _all_event_types(r) if t == "injury"]
        assert types, "injury_rate=0.05 across 4 games must produce injuries"

    def test_injury_events_name_a_victim(self):
        rules = RuleSet(injury_rate=0.05)
        for r in _run_seeds(_micro_game_def(rules), rules=rules, seeds=range(4)):
            for p in r.possession_log:
                for e in p.events:
                    if e.event_type == "injury":
                        assert e.actor_id
                        assert e.outcome == "hobbled"

    def test_injury_rate_is_a_tier1_rule_knob(self):
        assert RuleSet().injury_rate == 0.0
        with pytest.raises(ValidationError):
            RuleSet(injury_rate=1.5)


# --- modify_transitions ------------------------------------------------------


class TestModifyTransitions:
    def test_negative_weights_rejected(self):
        with pytest.raises(ValidationError):
            GameDefinitionPatch(
                modify_transitions={"initiate": {"shot": -1.0}},
            )

    def test_merge_preserves_unlisted_edges(self):
        patch = GameDefinitionPatch(
            modify_transitions={"initiate": {"violation_backcourt": 2.0}},
        )
        patched = patch.apply(basketball_game_definition(DEFAULT_RULESET))
        initiate = next(a for a in patched.actions if a.name == "initiate")
        assert initiate.transitions["violation_backcourt"] == 2.0
        assert initiate.transitions["pass_swing"] == 28.0  # untouched

    def test_merge_lands_on_success_and_failure_tables(self):
        patch = GameDefinitionPatch(
            modify_transitions={"pass_swing": {"shot": 99.0}},
        )
        patched = patch.apply(basketball_game_definition(DEFAULT_RULESET))
        node = next(a for a in patched.actions if a.name == "pass_swing")
        assert node.success_transitions["shot"] == 99.0
        assert node.failure_transitions["shot"] == 99.0

    def test_unknown_nodes_silently_ignored(self):
        patch = GameDefinitionPatch(
            modify_transitions={"no_such_node": {"shot": 1.0}},
        )
        patched = patch.apply(basketball_game_definition(DEFAULT_RULESET))
        assert {a.name for a in patched.actions} == {
            a.name for a in basketball_game_definition(DEFAULT_RULESET).actions
        }

    def test_modify_transitions_reshapes_play(self):
        # Zeroing every initiate exit except an immediate shot removes
        # passing from initiate-led chains.
        patch = GameDefinitionPatch(
            modify_transitions={
                "initiate": {
                    "pass_swing": 0.0, "pass_entry": 0.0, "drive": 0.0,
                    "iso": 0.0, "crossover": 0.0, "screen_on_ball": 0.0,
                    "screen_off_ball": 0.0, "steal_attempt": 0.0,
                    "violation_deadball": 0.0, "shot": 100.0,
                },
            },
        )
        base = simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=5,
            game_def=_micro_game_def(),
        )
        patched = simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=5,
            game_def=patch.apply(_micro_game_def()),
        )
        base_passes = sum(b.passes_made for b in base.box_scores)
        patched_passes = sum(b.passes_made for b in patched.box_scores)
        assert patched_passes < base_passes * 0.5

    def test_old_patches_without_modify_transitions_still_validate(self):
        old = {"modify_structure": {"quarters": 5}}
        patch = GameDefinitionPatch(**old)
        assert patch.modify_transitions == {}
        patched = patch.apply(basketball_game_definition(DEFAULT_RULESET))
        assert patched.quarters == 5


# --- Reachability validation -------------------------------------------------


class TestChainReachability:
    def test_bricking_patch_rejected(self):
        # initiate can only reach pass_swing, and pass_swing can only
        # reach itself — no shot, no terminal, every possession dies on
        # the shot clock. The validator must refuse it.
        zero_initiate = {
            "pass_swing": 10.0, "pass_entry": 0.0, "drive": 0.0,
            "iso": 0.0, "crossover": 0.0, "screen_on_ball": 0.0,
            "screen_off_ball": 0.0, "steal_attempt": 0.0, "shot": 0.0,
            "violation_deadball": 0.0,
        }
        zero_swing = {
            "pass_swing": 10.0, "pass_skip": 0.0, "pass_extra": 0.0,
            "drive": 0.0, "iso": 0.0, "crossover": 0.0,
            "screen_on_ball": 0.0, "screen_off_ball": 0.0,
            "steal_attempt": 0.0, "shot": 0.0, "turnover_bad_pass": 0.0,
        }
        patch = {
            "modify_transitions": {
                "initiate": zero_initiate,
                "pass_swing": zero_swing,
            },
            "description": "Remove all pass AND shot exits",
        }
        violations = validate_game_def_patch(patch, RuleSet())
        assert violations
        assert any("bricks possessions" in v for v in violations)

    def test_picks_are_illegal_passes_and_simulates(self):
        patch = {
            "remove_actions": ["screen_on_ball", "screen_off_ball"],
            "description": "Picks are illegal",
        }
        assert validate_game_def_patch(patch, RuleSet()) == []
        game_def = GameDefinitionPatch(**patch).apply(_micro_game_def())
        r = simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=3, game_def=game_def,
        )
        types = set(_all_event_types(r))
        assert not any(t.startswith("screen.") for t in types)
        assert r.home_score != r.away_score

    def test_dangling_edges_do_not_brick(self):
        # Removing a node that other tables point at leaves dangling
        # edges — the engine skips them, and validation must agree.
        patch = {
            "remove_actions": ["drive", "crossover", "iso"],
            "description": "No dribbling",
        }
        assert validate_game_def_patch(patch, RuleSet()) == []


# --- Narration ---------------------------------------------------------------


class TestTier3Narration:
    @pytest.mark.parametrize(
        ("event_type", "fragment"),
        [
            ("violation.backcourt", "half court"),
            ("violation.three_second", "key"),
            ("violation.kicked_ball", "kicks"),
            ("violation.goaltending", "GOALTEND"),
            ("foul.take", "takes the foul"),
            ("foul.clear_path", "Clear-path"),
            ("admin.check_ball", "checked"),
            ("admin.clear_arc", "clears"),
            ("injury", "hobbling"),
        ],
    )
    def test_tier3_events_narrate(self, event_type: str, fragment: str):
        line = narrate_event(
            {"event_type": event_type, "actor_id": "a1", "target_id": "b1"},
            names={"a1": "Ada", "b1": "Bo"},
        )
        assert fragment in line
