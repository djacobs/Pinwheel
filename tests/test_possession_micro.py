"""Tests for the micro possession engine (Phase 3).

Covers: distribution calibration against the macro engine (200 seeded
games per engine), the pinned seed-snapshot event stream, determinism,
governance reach (RuleSet params, action_biases, transition_biases,
GameDefinitionPatch), the shot-selection wall, hook wiring, summary-shape
invariants, the chain-node fallback, and the performance budget.

The macro engine is the calibration target: micro aggregates must sit
within a relative tolerance band of macro's. Bands are wide enough to be
robust (both harnesses are fully seeded, so results are deterministic)
but tight enough to catch gross miscalibration.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

import pytest

from pinwheel.core.hooks import HookContext, HookResult, RegisteredEffect
from pinwheel.core.seeding import generate_league
from pinwheel.core.simulation import simulate_game
from pinwheel.models.game import GameResult
from pinwheel.models.game_definition import (
    EXAMPLE_ACTIONS,
    ActionDefinition,
    ActionRegistry,
    GameDefinition,
    GameDefinitionPatch,
    basketball_game_definition,
    basketball_micro_actions,
    basketball_micro_chain_nodes,
)
from pinwheel.models.rules import DEFAULT_RULESET, RuleSet
from pinwheel.models.team import Hooper, PlayerAttributes, Team, Venue

# Relative tolerance band for the distribution-regression suite. Both
# engines run the same fixed seeds, so the comparison is deterministic —
# the band exists to catch gross miscalibration from future changes.
CALIBRATION_TOLERANCE = 0.20

N_CALIBRATION_GAMES = 200

# Micro engine per-game time budget (ms). Plan estimate: 15-40.
MICRO_MS_PER_GAME_BUDGET = 40.0

CHAIN_NODE_NAMES = {a.name for a in basketball_micro_chain_nodes()}


def _attrs(
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


def _hooper(
    hooper_id: str,
    team_id: str,
    attrs: PlayerAttributes | None = None,
    is_starter: bool = True,
) -> Hooper:
    return Hooper.model_construct(
        id=hooper_id,
        name=f"Hooper-{hooper_id}",
        team_id=team_id,
        archetype="sharpshooter",
        backstory="",
        attributes=attrs or _attrs(),
        is_starter=is_starter,
        moves=[],
    )


def _team(team_id: str, attrs: PlayerAttributes | None = None) -> Team:
    hoopers = [
        _hooper(f"{team_id}-s{i}", team_id, attrs, is_starter=True) for i in range(3)
    ]
    hoopers.append(_hooper(f"{team_id}-b0", team_id, attrs, is_starter=False))
    return Team(
        id=team_id,
        name=f"Team-{team_id}",
        venue=Venue(name="Court", capacity=5000),
        hoopers=hoopers,
    )


def _micro_game_def(rules: RuleSet | None = None) -> GameDefinition:
    rules = rules or DEFAULT_RULESET
    game_def = basketball_game_definition(rules)
    game_def.possession_engine = "micro"
    game_def.actions = basketball_micro_actions(rules)
    return game_def


def _all_event_types(result: GameResult) -> list[str]:
    return [e.event_type for p in result.possession_log for e in p.events]


# --- Distribution calibration ----------------------------------------------


def _aggregate(engine: str) -> dict[str, float]:
    """Run N_CALIBRATION_GAMES fixed-seed games and aggregate distributions."""
    teams = generate_league(4, seed=7).teams
    game_def = basketball_game_definition(DEFAULT_RULESET)
    if engine == "micro":
        game_def.possession_engine = "micro"
        game_def.actions = basketball_micro_actions(DEFAULT_RULESET)

    games = 0
    points = 0
    turnovers = 0
    fouls = 0
    orb = 0
    drb = 0
    shots: dict[str, list[int]] = {}
    start = time.perf_counter()
    for seed in range(N_CALIBRATION_GAMES):
        home, away = teams[seed % 2], teams[2 + seed % 2]
        r = simulate_game(home, away, DEFAULT_RULESET, seed=seed, game_def=game_def)
        games += 1
        points += r.home_score + r.away_score
        for b in r.box_scores:
            turnovers += b.turnovers
            fouls += b.fouls
        for p in r.possession_log:
            for e in p.events:
                if e.event_type.startswith("shot.") and e.event_type != "shot.free_throw":
                    cls = e.event_type.split(".", 1)[1]
                    s = shots.setdefault(cls, [0, 0])
                    s[0] += 1
                    if e.outcome == "made":
                        s[1] += 1
                elif e.event_type == "rebound.offensive":
                    orb += 1
                elif e.event_type == "rebound.defensive":
                    drb += 1
    elapsed_ms = (time.perf_counter() - start) * 1000

    agg: dict[str, float] = {
        "ppg": points / games,
        "to_per_game": turnovers / games,
        "fouls_per_game": fouls / games,
        "orb_rate": orb / max(1, orb + drb),
        "ms_per_game": elapsed_ms / games,
    }
    total_att = sum(v[0] for v in shots.values())
    for cls, (att, made) in shots.items():
        agg[f"fg_pct_{cls}"] = made / max(1, att)
        agg[f"share_{cls}"] = att / max(1, total_att)
    return agg


@pytest.fixture(scope="module")
def macro_stats() -> dict[str, float]:
    return _aggregate("macro")


@pytest.fixture(scope="module")
def micro_stats() -> dict[str, float]:
    return _aggregate("micro")


def _assert_within(
    metric: str, macro: float, micro: float, tolerance: float = CALIBRATION_TOLERANCE
) -> None:
    assert macro > 0, f"{metric}: macro baseline is zero"
    rel = abs(micro - macro) / macro
    assert rel <= tolerance, (
        f"{metric}: micro {micro:.3f} vs macro {macro:.3f} "
        f"({rel * 100:+.1f}% — band ±{tolerance * 100:.0f}%)"
    )


class TestDistributionCalibration:
    """Micro aggregates must track macro within the tolerance band."""

    def test_points_per_game(self, macro_stats, micro_stats):
        _assert_within("PPG", macro_stats["ppg"], micro_stats["ppg"])

    def test_turnover_rate(self, macro_stats, micro_stats):
        _assert_within(
            "TO/game", macro_stats["to_per_game"], micro_stats["to_per_game"]
        )

    def test_foul_rate(self, macro_stats, micro_stats):
        _assert_within(
            "fouls/game", macro_stats["fouls_per_game"], micro_stats["fouls_per_game"]
        )

    def test_offensive_rebound_rate(self, macro_stats, micro_stats):
        _assert_within("ORB rate", macro_stats["orb_rate"], micro_stats["orb_rate"])

    @pytest.mark.parametrize("cls", ["at_rim", "mid_range", "three_point"])
    def test_fg_pct_by_shot_class(self, macro_stats, micro_stats, cls):
        _assert_within(
            f"FG% {cls}",
            macro_stats[f"fg_pct_{cls}"],
            micro_stats[f"fg_pct_{cls}"],
        )

    @pytest.mark.parametrize("cls", ["at_rim", "mid_range", "three_point"])
    def test_attempt_share_by_shot_class(self, macro_stats, micro_stats, cls):
        _assert_within(
            f"attempt share {cls}",
            macro_stats[f"share_{cls}"],
            micro_stats[f"share_{cls}"],
        )

    def test_micro_performance_budget(self, micro_stats):
        # Plan estimate 15-40 ms/game; the budget is the hard wall.
        assert micro_stats["ms_per_game"] < MICRO_MS_PER_GAME_BUDGET, (
            f"micro engine {micro_stats['ms_per_game']:.1f} ms/game exceeds "
            f"{MICRO_MS_PER_GAME_BUDGET} ms budget"
        )


# --- Seed snapshot ----------------------------------------------------------


class TestSeedSnapshot:
    """Pin one full micro game's event stream — unplanned RNG changes to
    the micro engine must fail loudly and land as explicit seed-migration
    commits (plan invariant)."""

    PINNED_SEED = 42
    PINNED_SCORE = (79, 32)
    PINNED_POSSESSIONS = 93
    PINNED_EVENT_COUNT = 538
    PINNED_STREAM_SHA256 = (
        "3ec8441e3e8d44087598f1064a27439b57c4809d55039b05a5de6e8aff70aa87"
    )
    PINNED_FIRST_30 = [
        "admin.initiate", "dribble.iso", "shot.mid_range",
        "admin.initiate", "pass.swing", "shot.three_point",
        "rebound.box_out", "rebound.defensive",
        "admin.initiate", "shot.mid_range",
        "admin.initiate", "pass.swing", "pass.swing", "pass.swing",
        "pass.swing", "turnover.shot_clock",
        "admin.initiate", "dribble.drive", "shot.three_point",
        "rebound.box_out", "rebound.offensive", "rebound.second_chance",
        "shot.at_rim",
        "admin.initiate", "dribble.iso", "shot.mid_range",
        "rebound.box_out", "rebound.offensive", "rebound.second_chance",
        "pass.swing",
    ]

    def _run(self) -> GameResult:
        teams = generate_league(4, seed=7).teams
        return simulate_game(
            teams[0], teams[1], DEFAULT_RULESET,
            seed=self.PINNED_SEED, game_def=_micro_game_def(),
        )

    def test_pinned_score_and_possessions(self):
        r = self._run()
        assert (r.home_score, r.away_score) == self.PINNED_SCORE
        assert r.total_possessions == self.PINNED_POSSESSIONS

    def test_pinned_event_stream(self):
        r = self._run()
        seq = _all_event_types(r)
        assert len(seq) == self.PINNED_EVENT_COUNT
        assert seq[:30] == self.PINNED_FIRST_30
        digest = hashlib.sha256("|".join(seq).encode()).hexdigest()
        assert digest == self.PINNED_STREAM_SHA256


# --- Determinism ------------------------------------------------------------


class TestMicroDeterminism:
    def test_same_seed_identical_results(self):
        game_def = _micro_game_def()
        r1 = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=99,
                           game_def=game_def)
        r2 = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=99,
                           game_def=game_def)
        assert r1.home_score == r2.home_score
        assert r1.away_score == r2.away_score
        assert r1.total_possessions == r2.total_possessions
        assert [
            (e.event_type, e.actor_id, e.outcome, e.detail, e.points)
            for p in r1.possession_log for e in p.events
        ] == [
            (e.event_type, e.actor_id, e.outcome, e.detail, e.points)
            for p in r2.possession_log for e in p.events
        ]

    def test_different_seeds_diverge(self):
        game_def = _micro_game_def()
        r1 = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=1,
                           game_def=game_def)
        r2 = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=2,
                           game_def=game_def)
        assert _all_event_types(r1) != _all_event_types(r2)


# --- Governance reaches the micro engine ------------------------------------


@dataclass
class _TransitionBiasEffect(RegisteredEffect):
    """Test effect: pushes chain transitions toward immediate shots."""

    bias: dict[str, float] = field(default_factory=dict)

    def apply(self, hook: str, context: HookContext) -> HookResult:
        return HookResult(transition_biases=dict(self.bias))


class TestGovernanceUnderMicro:
    def test_three_point_value_changes_scores(self):
        rules5 = RuleSet(three_point_value=5)
        base_total = 0
        boosted_total = 0
        for seed in range(8):
            base = simulate_game(
                _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                game_def=_micro_game_def(),
            )
            boosted = simulate_game(
                _team("h"), _team("a"), rules5, seed=seed,
                game_def=_micro_game_def(rules5),
            )
            base_total += base.home_score + base.away_score
            boosted_total += boosted.home_score + boosted.away_score
        assert boosted_total > base_total

    def test_action_biases_effect_shifts_shot_mix(self):
        # modify_shot_selection wires through PossessionContext.action_biases
        # — the micro engine's shot-class selection must honor it just like
        # the macro engine does.
        effect = RegisteredEffect(
            effect_id="threes-please",
            proposal_id="p-1",
            _hook_points=["sim.possession.pre"],
            effect_type="hook_callback",
            action_code={"type": "modify_shot_selection", "three_point_bias": 80.0},
        )
        base_threes = 0
        biased_threes = 0
        for seed in range(5):
            base = simulate_game(
                _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                game_def=_micro_game_def(),
            )
            biased = simulate_game(
                _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                game_def=_micro_game_def(), effect_registry=[effect],
            )
            base_threes += sum(b.three_pointers_attempted for b in base.box_scores)
            biased_threes += sum(b.three_pointers_attempted for b in biased.box_scores)
        assert biased_threes > base_threes * 1.3

    def test_transition_biases_shift_chain_shape(self):
        # Biasing the "shot" pseudo-target makes chains shoot earlier —
        # fewer passes per game — without touching shot-class selection.
        effect = _TransitionBiasEffect(
            effect_id="quick-triggers",
            proposal_id="p-2",
            _hook_points=["sim.possession.pre"],
            bias={"shot": 300.0},
        )
        base_passes = 0
        biased_passes = 0
        for seed in range(5):
            base = simulate_game(
                _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                game_def=_micro_game_def(),
            )
            biased = simulate_game(
                _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                game_def=_micro_game_def(), effect_registry=[effect],
            )
            base_passes += sum(b.passes_made for b in base.box_scores)
            biased_passes += sum(b.passes_made for b in biased.box_scores)
        assert biased_passes < base_passes

    def test_violation_strictness_reaches_micro(self):
        def violations(strictness: float) -> int:
            rules = RuleSet(violation_strictness=strictness)
            count = 0
            for seed in range(10):
                r = simulate_game(
                    _team("h"), _team("a"), rules, seed=seed,
                    game_def=_micro_game_def(rules),
                )
                count += sum(
                    1 for t in _all_event_types(r)
                    if t in ("turnover.travel", "turnover.double_dribble")
                )
            return count

        assert violations(0.0) == 0
        assert violations(5.0) > violations(1.0)

    def test_game_def_patch_custom_shot_action_under_micro(self):
        patch = GameDefinitionPatch(
            add_actions=[EXAMPLE_ACTIONS["half_court_heave"]],
            description="Add the half-court heave",
        )
        game_def = patch.apply(_micro_game_def())
        assert game_def.possession_engine == "micro"
        heaves = 0
        for seed in range(10):
            r = simulate_game(
                _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                game_def=game_def,
            )
            heaves += sum(
                1 for t in _all_event_types(r) if t == "shot.half_court_heave"
            )
        assert heaves > 0, "patched-in shot action must be selectable under micro"

    def test_game_def_patch_can_modify_transitions(self):
        # Governance reshapes the chain itself: initiate always goes
        # straight to a shot — passes vanish from initiate-led chains.
        patch = GameDefinitionPatch(
            modify_actions={"initiate": {"transitions": {"shot": 100.0}}},
            description="No more ball movement",
        )
        game_def = patch.apply(_micro_game_def())
        base_passes = 0
        patched_passes = 0
        for seed in range(5):
            base = simulate_game(
                _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                game_def=_micro_game_def(),
            )
            patched = simulate_game(
                _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                game_def=game_def,
            )
            base_passes += sum(b.passes_made for b in base.box_scores)
            patched_passes += sum(b.passes_made for b in patched.box_scores)
        assert patched_passes < base_passes * 0.5

    def test_old_patches_still_validate(self):
        # A stored pre-Phase-3 patch dict (no chain fields) must construct.
        old_patch_dict = {
            "add_actions": [
                {
                    "name": "the_prayer",
                    "display_name": "The Prayer",
                    "category": "shot",
                    "points_on_success": 4,
                }
            ],
            "modify_structure": {"quarters": 5},
        }
        patch = GameDefinitionPatch(**old_patch_dict)
        patched = patch.apply(basketball_game_definition(DEFAULT_RULESET))
        assert patched.quarters == 5
        assert any(a.name == "the_prayer" for a in patched.actions)


# --- The shot-selection wall ------------------------------------------------


class TestShotSelectionWall:
    def test_chain_nodes_never_in_shot_actions(self):
        registry = ActionRegistry(basketball_micro_actions(DEFAULT_RULESET))
        shot_names = {a.name for a in registry.shot_actions()}
        assert shot_names == {"at_rim", "mid_range", "three_point"}
        assert not (shot_names & CHAIN_NODE_NAMES)

    def test_non_shot_categories_excluded(self):
        registry = ActionRegistry(
            [
                ActionDefinition(name="jumper", category="shot"),
                ActionDefinition(name="pick", category="screen"),
                ActionDefinition(name="poke", category="defense"),
                ActionDefinition(name="ft", category="shot", is_free_throw=True),
            ]
        )
        assert [a.name for a in registry.shot_actions()] == ["jumper"]

    def test_macro_ignores_chain_nodes_bit_exactly(self):
        # A macro game whose registry carries the chain nodes must produce
        # the exact same result as one without them — proof the nodes
        # cannot leak into macro selection or resolution.
        rules = DEFAULT_RULESET
        plain_def = basketball_game_definition(rules)
        loaded_def = basketball_game_definition(rules)
        loaded_def.actions = basketball_micro_actions(rules)
        for seed in (3, 77):
            plain = simulate_game(_team("h"), _team("a"), rules, seed=seed,
                                  game_def=plain_def)
            loaded = simulate_game(_team("h"), _team("a"), rules, seed=seed,
                                   game_def=loaded_def)
            assert (plain.home_score, plain.away_score) == (
                loaded.home_score, loaded.away_score,
            )
            assert _all_event_types(plain) == _all_event_types(loaded)

    def test_micro_log_actions_are_shot_or_turnover_vocabulary(self):
        r = simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            game_def=_micro_game_def(),
        )
        allowed = {
            "at_rim", "mid_range", "three_point",
            "turnover", "travel", "double_dribble", "shot_clock_violation",
            "substitution", "ejection",
        }
        for log in r.possession_log:
            assert log.action in allowed, f"unexpected log action {log.action}"


# --- Hooks ------------------------------------------------------------------


@dataclass
class _CountingEffect(RegisteredEffect):
    """Records category-hook invocations."""

    calls: list = field(default_factory=list)

    def apply(self, hook: str, context: HookContext) -> HookResult:
        self.calls.append(hook)
        return HookResult()


class TestMicroHooks:
    def test_event_hooks_fire_under_micro(self):
        effect = _CountingEffect(
            effect_id="listener",
            proposal_id="p-1",
            _hook_points=["sim.event.pre", "sim.event.post"],
        )
        simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            game_def=_micro_game_def(), effect_registry=[effect],
        )
        assert effect.calls.count("sim.event.pre") > 100
        assert effect.calls.count("sim.event.post") > 100

    def test_event_hooks_never_fire_under_macro(self):
        effect = _CountingEffect(
            effect_id="listener",
            proposal_id="p-1",
            _hook_points=["sim.event.pre", "sim.event.post"],
        )
        simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            effect_registry=[effect],
        )
        assert effect.calls == []

    def test_category_hooks_fire_under_micro(self):
        effect = _CountingEffect(
            effect_id="listener",
            proposal_id="p-1",
            _hook_points=[
                "sim.shot.pre", "sim.shot.post",
                "sim.rebound.pre", "sim.rebound.post",
                "sim.foul.post", "sim.turnover.post",
            ],
        )
        simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            game_def=_micro_game_def(), effect_registry=[effect],
        )
        for hook in (
            "sim.shot.pre", "sim.shot.post", "sim.rebound.pre",
            "sim.rebound.post", "sim.foul.post", "sim.turnover.post",
        ):
            assert hook in effect.calls, f"{hook} never fired under micro"

    def test_shot_pre_probability_modifier_shapes_micro_shots(self):
        effect = RegisteredEffect(
            effect_id="bricks",
            proposal_id="p-1",
            _hook_points=["sim.shot.pre"],
            effect_type="hook_callback",
            action_code={"type": "modify_probability", "modifier": -0.9},
        )
        base = simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            game_def=_micro_game_def(),
        )
        cursed = simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            game_def=_micro_game_def(), effect_registry=[effect],
        )
        base_fgm = sum(b.field_goals_made for b in base.box_scores)
        cursed_fgm = sum(b.field_goals_made for b in cursed.box_scores)
        assert cursed_fgm < base_fgm


# --- Summary shape & bookkeeping invariants ---------------------------------


class TestMicroSummaryShape:
    def _result(self) -> GameResult:
        return simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            game_def=_micro_game_def(),
        )

    def test_one_summary_row_per_possession_with_events(self):
        r = self._result()
        rows = [
            p for p in r.possession_log
            if p.action not in ("substitution", "ejection")
        ]
        assert rows
        for log in rows:
            assert log.events, "micro possessions always carry an event chain"
            assert [e.seq for e in log.events] == list(range(len(log.events)))
            assert log.result in ("made", "missed", "foul", "turnover")

    def test_box_score_points_match_final_score(self):
        r = self._result()
        assert sum(b.points for b in r.box_scores) == r.home_score + r.away_score

    def test_possession_results_never_offensive_rebound(self):
        # Micro folds second chances into the possession — callers must
        # keep alternating possession normally.
        r = self._result()
        for log in r.possession_log:
            assert log.is_offensive_rebound is False

    def test_potential_assists_cover_assists(self):
        r = self._result()
        total_potential = sum(b.potential_assists for b in r.box_scores)
        total_assists = sum(b.assists for b in r.box_scores)
        assert total_potential >= total_assists > 0

    def test_hustle_stats_accrue(self):
        r = self._result()
        assert sum(b.passes_made for b in r.box_scores) > 0
        assert sum(b.drives for b in r.box_scores) > 0
        assert sum(b.box_outs for b in r.box_scores) > 0

    def test_second_chance_shots_tagged(self):
        found = False
        for seed in range(5):
            r = simulate_game(
                _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                game_def=_micro_game_def(),
            )
            for p in r.possession_log:
                types = [e.event_type for e in p.events]
                if "rebound.offensive" in types:
                    orb_idx = types.index("rebound.offensive")
                    later_shots = [
                        e for e in p.events[orb_idx:]
                        if e.event_type.startswith("shot.")
                        and e.event_type != "shot.free_throw"
                    ]
                    if later_shots:
                        found = True
                        assert "second_chance" in later_shots[0].tags
        assert found, "offensive rebounds should produce second-chance shots"

    def test_putback_subtypes_occur(self):
        details = set()
        for seed in range(10):
            r = simulate_game(
                _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                game_def=_micro_game_def(),
            )
            for p in r.possession_log:
                for e in p.events:
                    if e.event_type == "shot.at_rim" and "second_chance" in e.tags:
                        details.add(e.detail)
        assert details & {"putback", "tip"}, (
            f"second-chance at-rim shots should include putbacks/tips, got {details}"
        )


# --- Fallback: flag flipped without chain nodes ------------------------------


class TestChainNodeFallback:
    def test_micro_without_chain_nodes_uses_fallback(self):
        # Governance can flip possession_engine via modify_structure without
        # adding chain nodes — the engine must not brick the possession.
        game_def = basketball_game_definition(DEFAULT_RULESET)
        game_def.possession_engine = "micro"
        assert "initiate" not in {a.name for a in game_def.actions}
        r = simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42, game_def=game_def,
        )
        assert r.home_score != r.away_score
        types = set(_all_event_types(r))
        assert "admin.initiate" in types, "fallback chain nodes must engage"

    def test_flag_via_modify_structure_patch(self):
        patch = GameDefinitionPatch(
            modify_structure={"possession_engine": "micro"},
            description="Switch to the micro engine",
        )
        game_def = patch.apply(basketball_game_definition(DEFAULT_RULESET))
        assert game_def.possession_engine == "micro"
        r = simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=7, game_def=game_def,
        )
        assert "admin.initiate" in set(_all_event_types(r))


# --- Chain mechanics --------------------------------------------------------


class TestChainMechanics:
    def test_min_pass_per_possession_gates_shots(self):
        rules = RuleSet(min_pass_per_possession=2)
        base = simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=11,
            game_def=_micro_game_def(),
        )
        gated = simulate_game(
            _team("h"), _team("a"), rules, seed=11,
            game_def=_micro_game_def(rules),
        )
        base_passes = sum(b.passes_made for b in base.box_scores)
        gated_passes = sum(b.passes_made for b in gated.box_scores)
        assert gated_passes > base_passes

    def test_chains_respect_step_cap(self):
        r = simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            game_def=_micro_game_def(),
        )
        for p in r.possession_log:
            # Chain steps emit at most ~2 events each (node + attribution),
            # so a generous ceiling proves the cap holds.
            assert len(p.events) <= 40

    def test_turnover_attribution(self):
        r = simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            game_def=_micro_game_def(),
        )
        for p in r.possession_log:
            for e in p.events:
                if e.event_type in ("turnover.bad_pass", "turnover.lost_ball"):
                    assert e.target_id, "live-ball turnovers credit a stealer"
                    assert e.outcome == "stolen"
                    assert p.defender_id == e.target_id

    def test_drive_wins_produce_open_paint_shots(self):
        seen_open = False
        for seed in range(5):
            r = simulate_game(
                _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                game_def=_micro_game_def(),
            )
            for p in r.possession_log:
                types = [e.event_type for e in p.events]
                for i, e in enumerate(p.events):
                    if (
                        e.event_type == "dribble.drive"
                        and e.outcome == "success"
                        and i + 1 < len(p.events)
                        and types[i + 1].startswith("shot.")
                    ):
                        seen_open = True
                        assert "open" in p.events[i + 1].tags
        assert seen_open, "successful drives followed by shots should be open looks"
