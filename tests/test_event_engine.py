"""Tests for the granular event engine (Phases 1-2).

Covers: GameEvent substrate, Tier-1 subtype enrichment, turnover/steal
attribution, violations (violation_strictness), block conversion,
assist-before-shot, and-one flow, contested rebounds, box-out pre-roll,
category hooks + hook index, and the event_detail=False legacy path.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from pinwheel.core.governance import detect_tier
from pinwheel.core.hooks import HookContext, HookResult, RegisteredEffect
from pinwheel.core.meta import MetaStore
from pinwheel.core.possession import (
    CATEGORY_HOOKS,
    attempt_rebound,
    derive_shot_subtype,
    zone_for_action,
)
from pinwheel.core.simulation import simulate_game
from pinwheel.core.state import HooperState
from pinwheel.models.game import GameEvent, PossessionLog
from pinwheel.models.game_definition import basketball_game_definition
from pinwheel.models.governance import RuleInterpretation
from pinwheel.models.rules import DEFAULT_RULESET, RuleSet
from pinwheel.models.team import Hooper, PlayerAttributes, Team, Venue


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
    # model_construct bypasses the attribute budget validator — tests
    # use extreme profiles to isolate mechanics.
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


def _team(
    team_id: str,
    attrs: PlayerAttributes | None = None,
    per_hooper_attrs: list[PlayerAttributes] | None = None,
) -> Team:
    hoopers = []
    for i in range(3):
        a = per_hooper_attrs[i] if per_hooper_attrs else attrs
        hoopers.append(_hooper(f"{team_id}-s{i}", team_id, a, is_starter=True))
    hoopers.append(_hooper(f"{team_id}-b0", team_id, attrs, is_starter=False))
    return Team(
        id=team_id,
        name=f"Team-{team_id}",
        venue=Venue(name="Court", capacity=5000),
        hoopers=hoopers,
    )


def _all_events(result) -> list[GameEvent]:
    return [e for p in result.possession_log for e in p.events]


# --- Event substrate -------------------------------------------------------


class TestEventSubstrate:
    def test_possessions_emit_events_by_default(self):
        result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        events = _all_events(result)
        assert events, "event_detail defaults to True — events must be emitted"
        # Every shot possession carries a namespaced shot event
        assert any(e.event_type.startswith("shot.") for e in events)

    def test_event_seq_is_per_possession_ordinal(self):
        result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        for log in result.possession_log:
            assert [e.seq for e in log.events] == list(range(len(log.events)))

    def test_old_possession_rows_deserialize_without_events(self):
        # Pre-enrichment rows have no `events` key — must default to []
        old_row = {
            "quarter": 1,
            "possession_number": 3,
            "offense_team_id": "t-1",
            "ball_handler_id": "h-1",
            "action": "three_point",
            "result": "made",
        }
        log = PossessionLog.model_validate(old_row)
        assert log.events == []

    def test_events_round_trip_through_json(self):
        result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        log = next(p for p in result.possession_log if p.events)
        restored = PossessionLog.model_validate(log.model_dump(mode="json"))
        assert restored.events == log.events

    def test_zone_for_action(self):
        assert zone_for_action("at_rim") == "paint"
        assert zone_for_action("three_point") == "perimeter"
        assert zone_for_action("unknown_action") == ""


# --- Shot subtypes ---------------------------------------------------------


class TestShotSubtypes:
    def test_shot_events_carry_subtypes(self):
        result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        shot_events = [
            e for e in _all_events(result)
            if e.event_type.startswith("shot.") and e.event_type != "shot.free_throw"
        ]
        assert shot_events
        assert all(e.detail for e in shot_events), "every shot gets a subtype"

    def test_dunks_correlate_with_scoring_and_speed(self):
        highfliers = _team("high", attrs=_attrs(scoring=85, speed=85))
        plodders = _team("low", attrs=_attrs(scoring=30, speed=30))
        dunks = {"high": 0, "low": 0}
        for seed in range(10):
            result = simulate_game(highfliers, plodders, DEFAULT_RULESET, seed=seed)
            for e in _all_events(result):
                if e.event_type == "shot.at_rim" and e.detail == "dunk":
                    dunks["high" if e.team_id == "high" else "low"] += 1
        assert dunks["high"] > 0, "high scoring+speed hoopers should dunk"
        assert dunks["high"] > dunks["low"]

    def test_derive_shot_subtype_is_deterministic(self):
        h = HooperState(hooper=_hooper("x", "t", _attrs(scoring=80, speed=80)))
        for shot_type in ("at_rim", "mid_range", "three_point"):
            a = derive_shot_subtype(shot_type, h, 0.5, False, False)
            b = derive_shot_subtype(shot_type, h, 0.5, False, False)
            assert a == b != ""

    def test_putback_subtype_and_unassisted(self):
        h = HooperState(hooper=_hooper("x", "t", _attrs()))
        assert derive_shot_subtype("at_rim", h, 0.9, True, False) == "putback"
        assert derive_shot_subtype("at_rim", h, 0.1, True, False) == "tip"
        # End-to-end: made putbacks never carry an assist. (Under the micro
        # engine a possession row can hold several shots — a missed putback
        # may be followed by a later assisted make — so the unassisted
        # invariant is asserted on MADE putbacks, which end the possession.)
        found_putback = False
        for seed in range(20):
            result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=seed)
            for log in result.possession_log:
                for e in log.events:
                    if e.event_type == "shot.at_rim" and e.detail in ("putback", "tip"):
                        found_putback = True
                        if e.outcome == "made":
                            assert log.assist_id == "", "putbacks are unassisted"
        assert found_putback, "putbacks should occur across 20 seeded games"

    def test_catch_and_shoot_requires_assister(self):
        h = HooperState(hooper=_hooper("x", "t", _attrs()))
        assert derive_shot_subtype("mid_range", h, 0.99, False, True) == "catch_and_shoot"
        assert derive_shot_subtype("three_point", h, 0.5, False, True) == "spot_up"
        assert derive_shot_subtype("three_point", h, 0.1, False, True) == "corner"


# --- Turnover subtypes & steal attribution ---------------------------------


class TestTurnoverAttribution:
    def test_turnover_events_have_subtypes_and_stealers(self):
        result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        to_events = [
            e for e in _all_events(result)
            if e.event_type in ("turnover.bad_pass", "turnover.lost_ball")
        ]
        assert to_events, "live-ball turnovers should occur"
        for e in to_events:
            assert e.target_id, "steal must be attributed"
            assert e.outcome == "stolen"

    def test_steal_attribution_matches_log_defender(self):
        for seed in range(5):
            result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=seed)
            for log in result.possession_log:
                for e in log.events:
                    if e.event_type in ("turnover.bad_pass", "turnover.lost_ball"):
                        assert log.defender_id == e.target_id

    def test_deflections_credited_on_bad_pass_and_steal_gambles(self):
        # Deflections come from exactly two sources: picked-off bad passes
        # and Tier-2 steal-gamble deflections (defense.steal_attempt with
        # outcome "deflection") — nothing else inflates the stat.
        totals_expected = 0
        totals_deflections = 0
        for seed in range(10):
            result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=seed)
            totals_expected += sum(
                1 for e in _all_events(result) if e.event_type == "turnover.bad_pass"
            )
            totals_expected += sum(
                1
                for e in _all_events(result)
                if e.event_type == "defense.steal_attempt"
                and e.outcome == "deflection"
            )
            totals_deflections += sum(b.deflections for b in result.box_scores)
        assert totals_deflections == totals_expected

    def test_turnover_subtype_mix_tracks_skill_deficits(self):
        # Micro-engine semantics (Phase 4): turnover subtypes EMERGE from
        # failed chain events rather than being attributed by usage — a
        # low-passing team fails its passes (bad_pass), a slow team gets
        # stripped on its dribble attempts (lost_ball). So the bad-pass
        # SHARE of a team's live-ball turnovers tracks its passing deficit.
        passers = _team("p", attrs=_attrs(passing=90, speed=10))
        dribblers = _team("d", attrs=_attrs(passing=10, speed=90))
        counts = {"p": {"bad_pass": 0, "lost_ball": 0}, "d": {"bad_pass": 0, "lost_ball": 0}}
        for seed in range(15):
            result = simulate_game(passers, dribblers, DEFAULT_RULESET, seed=seed)
            for e in _all_events(result):
                if e.event_type.startswith("turnover.") and e.team_id in ("p", "d"):
                    sub = e.event_type.split(".", 1)[1]
                    if sub in ("bad_pass", "lost_ball"):
                        counts[e.team_id][sub] += 1
        p_total = counts["p"]["bad_pass"] + counts["p"]["lost_ball"]
        d_total = counts["d"]["bad_pass"] + counts["d"]["lost_ball"]
        assert p_total > 0 and d_total > 0
        bad_pass_share_p = counts["p"]["bad_pass"] / p_total
        bad_pass_share_d = counts["d"]["bad_pass"] / d_total
        assert bad_pass_share_d > bad_pass_share_p


# --- Violations ------------------------------------------------------------


class TestViolations:
    def _violation_count(self, strictness: float, n_games: int = 20) -> tuple[int, int]:
        rules = RuleSet(violation_strictness=strictness)
        violations = 0
        possessions = 0
        for seed in range(n_games):
            result = simulate_game(_team("h"), _team("a"), rules, seed=seed)
            possessions += result.total_possessions
            violations += sum(
                1 for e in _all_events(result)
                if e.event_type in ("turnover.travel", "turnover.double_dribble")
            )
        return violations, possessions

    def test_zero_strictness_means_no_violations(self):
        violations, _ = self._violation_count(0.0)
        assert violations == 0

    def test_higher_strictness_means_more_violations(self):
        v1, _ = self._violation_count(1.0)
        v5, _ = self._violation_count(5.0)
        assert v5 > v1

    def test_default_rate_is_small(self):
        violations, possessions = self._violation_count(1.0, n_games=30)
        rate = violations / possessions
        assert 0.0 < rate < 0.03, f"~1% of possessions expected, got {rate:.3f}"

    def test_violations_are_dead_ball_turnovers(self):
        rules = RuleSet(violation_strictness=5.0)
        for seed in range(10):
            result = simulate_game(_team("h"), _team("a"), rules, seed=seed)
            for log in result.possession_log:
                if log.action in ("travel", "double_dribble"):
                    assert log.result == "turnover"
                    assert log.defender_id == "", "no steal on a dead-ball violation"

    def test_violation_strictness_is_tier_1(self):
        tier = detect_tier(
            RuleInterpretation(parameter="violation_strictness"), DEFAULT_RULESET
        )
        assert tier == 1

    def test_violation_strictness_bounds(self):
        assert RuleSet(violation_strictness=0.0).violation_strictness == 0.0
        assert RuleSet(violation_strictness=5.0).violation_strictness == 5.0


# --- Blocks ----------------------------------------------------------------


class TestBlocks:
    def test_blocks_appear_in_box_scores(self):
        stoppers = _team("d", attrs=_attrs(defense=90, speed=80))
        shooters = _team("o", attrs=_attrs(scoring=60))
        total_blocks = 0
        total_block_events = 0
        for seed in range(10):
            result = simulate_game(shooters, stoppers, DEFAULT_RULESET, seed=seed)
            total_blocks += sum(b.blocks for b in result.box_scores)
            total_block_events += sum(
                1 for e in _all_events(result) if e.event_type == "block"
            )
        assert total_blocks > 0, "phantom blocks stat must be incremented now"
        assert total_blocks == total_block_events

    def test_blocked_shot_event_outcome(self):
        # A block flips the outcome of the shot it rejected — the nearest
        # shot event BEFORE the block. (Micro possessions can hold several
        # shots, so "the first shot in the log" is no longer the target.)
        stoppers = _team("d", attrs=_attrs(defense=90, speed=80))
        found_block = False
        for seed in range(10):
            result = simulate_game(_team("o"), stoppers, DEFAULT_RULESET, seed=seed)
            for log in result.possession_log:
                for i, e in enumerate(log.events):
                    if e.event_type != "block":
                        continue
                    found_block = True
                    shot = next(
                        ev
                        for ev in reversed(log.events[:i])
                        if ev.event_type.startswith("shot.")
                    )
                    assert shot.outcome == "blocked"
        assert found_block, "blocks should occur against elite stoppers"


# --- Assist-before-shot ----------------------------------------------------


class TestAssistBeforeShot:
    def test_assists_are_passing_weighted_not_uniform(self):
        # s0 passes 90, s1 passes 10, s2 passes 50 — assists should
        # concentrate on the high-passing hooper, unlike the old
        # uniform-random-teammate credit.
        per = [
            _attrs(passing=90, iq=50),
            _attrs(passing=10, iq=50),
            _attrs(passing=50, iq=50),
        ]
        home = _team("h", per_hooper_attrs=per)
        away = _team("a")
        assists: dict[str, int] = {}
        for seed in range(25):
            result = simulate_game(home, away, DEFAULT_RULESET, seed=seed)
            for b in result.box_scores:
                if b.team_id == "h":
                    assists[b.hooper_id] = assists.get(b.hooper_id, 0) + b.assists
        # Under the micro engine, assist credit goes to the actual last
        # passer in the chain, so the skew comes from passing-weighted
        # ball-handling plus pass completion — real but softer than the
        # macro engine's direct passing-weighted pick (was a 2x margin).
        assert assists.get("h-s0", 0) > 1.2 * assists.get("h-s1", 0)

    def test_potential_assists_tracked(self):
        result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        total_potential = sum(b.potential_assists for b in result.box_scores)
        total_assists = sum(b.assists for b in result.box_scores)
        assert total_potential > 0
        assert total_potential >= total_assists, (
            "every credited assist starts as a potential assist"
        )

    def test_assist_event_matches_log(self):
        result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        for log in result.possession_log:
            assist = next((e for e in log.events if e.event_type == "assist"), None)
            if assist is not None:
                assert log.assist_id == assist.actor_id
                assert log.result == "made"


# --- And-one flow ----------------------------------------------------------


class TestAndOne:
    def test_and_one_produces_free_throw(self):
        rules = RuleSet(foul_rate_modifier=3.0)  # more fouls -> more and-ones
        found = False
        for seed in range(20):
            result = simulate_game(_team("h"), _team("a"), rules, seed=seed)
            for log in result.possession_log:
                shot = next(
                    (
                        e for e in log.events
                        if e.event_type.startswith("shot.") and "and_one" in e.tags
                    ),
                    None,
                )
                if shot is not None:
                    found = True
                    assert log.result == "made"
                    fts = [
                        e for e in log.events
                        if e.event_type == "shot.free_throw" and e.detail == "and_one"
                    ]
                    assert len(fts) == 1, "and-one awards exactly one FT"
            if found:
                break
        assert found, "and-ones should occur with a high foul rate"


# --- Rebounds: contested + box-out -----------------------------------------


class TestRebounds:
    def test_rebound_events_flag_contested(self):
        result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        reb_events = [
            e for e in _all_events(result)
            if e.event_type in ("rebound.offensive", "rebound.defensive")
        ]
        assert reb_events
        assert all(e.outcome in ("contested", "uncontested") for e in reb_events)
        # Offensive boards are always contested
        for e in reb_events:
            if e.event_type == "rebound.offensive":
                assert e.outcome == "contested"

    def test_box_out_shifts_rebound_distribution(self):
        offense = [HooperState(hooper=_hooper(f"o{i}", "o")) for i in range(3)]
        defense = [HooperState(hooper=_hooper(f"d{i}", "d")) for i in range(3)]
        rng_a = random.Random(7)
        rng_b = random.Random(7)
        drb_plain = sum(
            1 for _ in range(2000)
            if not attempt_rebound(offense, defense, rng_a, DEFAULT_RULESET)[1]
        )
        drb_boxed = sum(
            1 for _ in range(2000)
            if not attempt_rebound(
                offense, defense, rng_b, DEFAULT_RULESET, defensive_bonus=8.0
            )[1]
        )
        assert drb_boxed > drb_plain

    def test_box_outs_counted_in_box_scores(self):
        result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        assert sum(b.box_outs for b in result.box_scores) > 0


# --- Category hooks + hook index -------------------------------------------


@dataclass
class _CountingEffect(RegisteredEffect):
    """Records every category-hook invocation with its context shape."""

    calls: list = field(default_factory=list)

    def apply(self, hook: str, context: HookContext) -> HookResult:
        self.calls.append(
            (hook, context.game_state is not None, context.hooper is not None)
        )
        return HookResult()


class TestCategoryHooks:
    def test_hooks_fire_with_correct_context(self):
        effect = _CountingEffect(
            effect_id="counter",
            proposal_id="p-1",
            _hook_points=sorted(CATEGORY_HOOKS),
        )
        simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            effect_registry=[effect],
        )
        by_hook: dict[str, int] = {}
        for hook, has_gs, _ in effect.calls:
            assert has_gs, "category hooks always carry game_state"
            by_hook[hook] = by_hook.get(hook, 0) + 1
        assert by_hook.get("sim.shot.pre", 0) > 0
        assert by_hook.get("sim.shot.post", 0) > 0
        assert by_hook.get("sim.rebound.pre", 0) > 0
        assert by_hook.get("sim.rebound.post", 0) > 0
        assert by_hook.get("sim.turnover.post", 0) > 0
        assert by_hook.get("sim.foul.post", 0) > 0
        # Shot hooks are anchored to the shooter
        assert all(
            has_hooper for hook, _, has_hooper in effect.calls
            if hook in ("sim.shot.pre", "sim.shot.post")
        )

    def test_shot_pre_probability_modifier_shapes_shots(self):
        effect = RegisteredEffect(
            effect_id="bricks",
            proposal_id="p-1",
            _hook_points=["sim.shot.pre"],
            effect_type="hook_callback",
            action_code={"type": "modify_probability", "modifier": -0.9},
        )
        base = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        cursed = simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            effect_registry=[effect],
        )
        base_fgm = sum(b.field_goals_made for b in base.box_scores)
        cursed_fgm = sum(b.field_goals_made for b in cursed.box_scores)
        assert cursed_fgm < base_fgm

    def test_hook_index_skips_unsubscribed_hooks(self):
        # An effect on a non-category hook must never be invoked by the
        # possession engine — the per-game hook index filters it out.
        effect = _CountingEffect(
            effect_id="dormant",
            proposal_id="p-1",
            _hook_points=["round.game.post"],
        )
        simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            effect_registry=[effect],
        )
        assert effect.calls == []

    def test_meta_store_reaches_category_hooks(self):
        effect = RegisteredEffect(
            effect_id="meta-counter",
            proposal_id="p-1",
            _hook_points=["sim.turnover.post"],
            effect_type="meta_mutation",
            target_type="team",
            target_selector="h",
            meta_field="turnovers_seen",
            meta_value=1,
            meta_operation="increment",
        )
        store = MetaStore()
        simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            effect_registry=[effect], meta_store=store,
        )
        assert store.get("team", "h", "turnovers_seen", default=0) > 0


# --- Legacy path: event_detail=False ---------------------------------------


class TestEventDetailOff:
    def test_legacy_path_preserves_pre_change_seeded_outcomes(self):
        # Pinned outputs from the pre-enrichment engine (commit a4e8712),
        # generate_league(4, seed=7) teams[0] vs teams[1]. The legacy RNG
        # stream must reproduce these exactly with event_detail=False.
        from pinwheel.core.seeding import generate_league

        teams = generate_league(4, seed=7).teams
        game_def = basketball_game_definition(DEFAULT_RULESET)
        game_def.event_detail = False
        pinned = {
            42: (83, 42, 114),
            99: (62, 47, 113),
            1234: (58, 25, 114),
        }
        for seed, (h, a, poss) in pinned.items():
            r = simulate_game(
                teams[0], teams[1], DEFAULT_RULESET, seed=seed, game_def=game_def,
            )
            assert (r.home_score, r.away_score, r.total_possessions) == (h, a, poss)

    def test_legacy_path_emits_no_events(self):
        game_def = basketball_game_definition(DEFAULT_RULESET)
        game_def.event_detail = False
        result = simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42, game_def=game_def,
        )
        assert all(not p.events for p in result.possession_log)
        assert sum(b.blocks for b in result.box_scores) == 0
        assert sum(b.potential_assists for b in result.box_scores) == 0

    def test_enriched_path_is_deterministic(self):
        r1 = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        r2 = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        assert r1.home_score == r2.home_score
        assert r1.away_score == r2.away_score
        assert [
            (e.event_type, e.detail, e.actor_id)
            for p in r1.possession_log for e in p.events
        ] == [
            (e.event_type, e.detail, e.actor_id)
            for p in r2.possession_log for e in p.events
        ]


# --- Hustle stat plumbing --------------------------------------------------


class TestHustleStats:
    def test_hustle_stats_flow_to_box_scores(self):
        result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        assert sum(b.drives for b in result.box_scores) > 0
        assert sum(b.passes_made for b in result.box_scores) > 0
        # contested_shots accrue under tight schemes — check across seeds
        contested = 0
        for seed in range(5):
            r = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=seed)
            contested += sum(b.contested_shots for b in r.box_scores)
        assert contested > 0
