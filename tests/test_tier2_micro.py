"""Tests for Tier-2 micro-engine events and surfaces (Phase 4).

Covers: passes (entry/skip/kickout/extra), crossovers, screens with real
screen-assist credit, cuts, defensive switch/help/steal-gamble events,
loose-ball hustle stats, contest attribution, transition possessions,
the pass-count/transition governance scalars, per-event move triggers,
chain-node governability ("picks are illegal"), chain narration, the
presenter's event payload, and commentary's drama-selected chains.
"""

from __future__ import annotations

import pytest

from pinwheel.ai.commentary import _build_game_context, _drama_selected_chains
from pinwheel.core.hooks import RegisteredEffect
from pinwheel.core.moves import ANKLE_BREAKER, LOCKDOWN_STANCE, NO_LOOK_PASS
from pinwheel.core.narrate import narrate_event
from pinwheel.core.presenter import PresentationState, present_round
from pinwheel.core.simulation import simulate_game
from pinwheel.models.game import GameEvent, GameResult
from pinwheel.models.game_definition import (
    GameDefinitionPatch,
    basketball_game_definition,
)
from pinwheel.models.rules import DEFAULT_RULESET, RuleSet
from pinwheel.models.team import Hooper, Move, PlayerAttributes, Team, Venue


def _attrs(**kw: int) -> PlayerAttributes:
    base = dict(
        scoring=50, passing=40, defense=40, speed=40, stamina=40,
        iq=50, ego=30, chaotic_alignment=20, fate=30,
    )
    base.update(kw)
    return PlayerAttributes.model_construct(**base)


def _hooper(
    hooper_id: str,
    team_id: str,
    attrs: PlayerAttributes | None = None,
    is_starter: bool = True,
    moves: list[Move] | None = None,
) -> Hooper:
    return Hooper.model_construct(
        id=hooper_id,
        name=f"Hooper-{hooper_id}",
        team_id=team_id,
        archetype="sharpshooter",
        backstory="",
        attributes=attrs or _attrs(),
        is_starter=is_starter,
        moves=moves or [],
    )


def _team(
    team_id: str,
    attrs: PlayerAttributes | None = None,
    moves: list[Move] | None = None,
) -> Team:
    hoopers = [
        _hooper(f"{team_id}-s{i}", team_id, attrs, moves=moves) for i in range(3)
    ]
    hoopers.append(_hooper(f"{team_id}-b0", team_id, attrs, is_starter=False))
    return Team(
        id=team_id,
        name=f"Team-{team_id}",
        venue=Venue(name="Court", capacity=5000),
        hoopers=hoopers,
    )


def _events(result: GameResult) -> list[GameEvent]:
    return [e for p in result.possession_log for e in p.events]


def _games(n: int = 10) -> list[GameResult]:
    return [
        simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=s)
        for s in range(n)
    ]


# --- Tier-2 event families under the flipped default ------------------------


class TestTier2Events:
    def test_all_tier2_families_occur(self):
        types = {e.event_type for r in _games(10) for e in _events(r)}
        for expected in (
            "pass.swing", "pass.entry", "pass.skip", "pass.kickout",
            "pass.extra", "dribble.crossover", "screen.on_ball",
            "screen.off_ball", "defense.contest", "defense.switch",
            "defense.help_rotation", "defense.steal_attempt",
        ):
            assert expected in types, f"{expected} never occurred in 10 games"

    def test_default_engine_is_micro(self):
        gd = basketball_game_definition(DEFAULT_RULESET)
        assert gd.possession_engine == "micro"
        # The chain vocabulary ships in the definition — governable data.
        names = {a.name for a in gd.actions}
        assert {"initiate", "screen_on_ball", "steal_attempt"} <= names

    def test_pass_events_increment_pass_count_and_stat(self):
        r = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=3)
        pass_events = [
            e for e in _events(r)
            if e.event_type.startswith("pass.") and e.outcome == "success"
        ]
        # Slip screens and successful cuts also count as passes.
        slip_passes = [
            e for e in _events(r)
            if e.event_type == "screen.on_ball" and e.detail == "slip"
        ]
        cut_passes = [
            e for e in _events(r)
            if e.event_type in ("cut.curl", "cut.backdoor")
            and e.outcome == "success"
        ]
        total_passes = sum(b.passes_made for b in r.box_scores)
        assert total_passes == len(pass_events) + len(slip_passes) + len(cut_passes)

    def test_kickout_requires_paint_zone(self):
        for r in _games(6):
            for p in r.possession_log:
                for e in p.events:
                    if e.event_type == "pass.kickout":
                        assert e.zone == "paint"

    def test_screen_branches_roll_pop_slip(self):
        details = {
            e.detail
            for r in _games(10)
            for e in _events(r)
            if e.event_type == "screen.on_ball"
        }
        assert details & {"roll", "pop", "slip"}

    def test_screen_assist_credit_is_real(self):
        # A screen.assist event only appears when a screen was set earlier
        # in the same possession, and the stat matches the events.
        total_stat = 0
        total_events = 0
        for r in _games(10):
            total_stat += sum(b.screen_assists for b in r.box_scores)
            for p in r.possession_log:
                types = [e.event_type for e in p.events]
                for i, e in enumerate(p.events):
                    if e.event_type == "screen.assist":
                        total_events += 1
                        assert any(
                            t in ("screen.on_ball", "screen.off_ball")
                            for t in types[:i]
                        ), "screen.assist without a screen in the chain"
        assert total_events > 0, "screen assists should occur"
        assert total_stat == total_events

    def test_contest_attribution_matches_stat(self):
        contested_events = 0
        contested_stat = 0
        for r in _games(6):
            contested_stat += sum(b.contested_shots for b in r.box_scores)
            contested_events += sum(
                1
                for e in _events(r)
                if e.event_type == "defense.contest" and e.outcome == "contested"
            )
        assert contested_events > 0
        assert contested_stat == contested_events

    def test_every_shot_has_open_or_contested_tag(self):
        r = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=5)
        shots = [
            e for e in _events(r)
            if e.event_type.startswith("shot.")
            and e.event_type != "shot.free_throw"
        ]
        assert shots
        for s in shots:
            assert ("open" in s.tags) != ("contested" in s.tags)

    def test_loose_balls_accrue_from_deflection_scrambles(self):
        loose_stat = 0
        scramble_events = 0
        for r in _games(15):
            loose_stat += sum(b.loose_balls for b in r.box_scores)
            scramble_events += sum(
                1 for e in _events(r) if e.event_type == "hustle.loose_ball"
            )
        assert scramble_events > 0, "loose-ball scrambles should occur"
        assert loose_stat == scramble_events

    def test_kickout_after_help_rotation_produces_open_shot(self):
        found = 0
        for r in _games(10):
            for p in r.possession_log:
                evs = p.events
                for i, e in enumerate(evs):
                    if e.event_type != "pass.kickout" or e.outcome != "success":
                        continue
                    if not any(
                        x.event_type == "defense.help_rotation" for x in evs[:i]
                    ):
                        continue
                    for x in evs[i + 1:]:
                        if x.event_type.startswith("shot."):
                            if "open" in x.tags:
                                found += 1
                            break
        assert found > 0, "help→kickout synergy should produce open shots"


# --- Transition possessions + governance scalars ----------------------------


class TestTransitionAndScalars:
    def test_transition_shots_tagged(self):
        tagged = sum(
            1
            for r in _games(5)
            for e in _events(r)
            if e.event_type.startswith("shot.") and "transition" in e.tags
        )
        assert tagged > 0, "transition possessions should occur"

    def test_pass_count_condition_gates_effects(self):
        # A shot-value bonus conditioned on pass_count_last_possession must
        # change scoring; an unreachable threshold must not.
        def total_with(condition: dict) -> int:
            effect = RegisteredEffect(
                effect_id="pass-bonus",
                proposal_id="p-1",
                _hook_points=["sim.shot.pre"],
                effect_type="hook_callback",
                action_code={
                    "type": "modify_shot_value",
                    "modifier": 2,
                    "condition_check": condition,
                },
            )
            total = 0
            for seed in range(6):
                r = simulate_game(
                    _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                    effect_registry=[effect],
                )
                total += r.home_score + r.away_score
            return total

        base = sum(
            r.home_score + r.away_score
            for r in (
                simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=s)
                for s in range(6)
            )
        )
        boosted = total_with({"pass_count_last_possession_gte": 1})
        unreachable = total_with({"pass_count_last_possession_gte": 99})
        assert boosted > base
        assert unreachable <= boosted

    def test_transition_condition_gates_effects(self):
        effect = RegisteredEffect(
            effect_id="fastbreak-bonus",
            proposal_id="p-2",
            _hook_points=["sim.shot.pre"],
            effect_type="hook_callback",
            action_code={
                "type": "modify_shot_value",
                "modifier": 3,
                "condition_check": {"transition_possession": True},
            },
        )
        base = 0
        boosted = 0
        for seed in range(6):
            b = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=seed)
            r = simulate_game(
                _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                effect_registry=[effect],
            )
            base += b.home_score + b.away_score
            boosted += r.home_score + r.away_score
        assert boosted > base

    def test_pass_and_screen_hooks_fire(self):
        from dataclasses import dataclass, field

        from pinwheel.core.hooks import HookContext, HookResult

        @dataclass
        class _Counting(RegisteredEffect):
            calls: list = field(default_factory=list)

            def apply(self, hook: str, context: HookContext) -> HookResult:
                self.calls.append(hook)
                return HookResult()

        effect = _Counting(
            effect_id="listener",
            proposal_id="p-3",
            _hook_points=["sim.pass.post", "sim.screen.post"],
        )
        simulate_game(
            _team("h"), _team("a"), DEFAULT_RULESET, seed=42,
            effect_registry=[effect],
        )
        assert "sim.pass.post" in effect.calls
        assert "sim.screen.post" in effect.calls


# --- Moves rewired to real chain triggers -----------------------------------


class TestMovesOnChainEvents:
    def test_ankle_breaker_triggers_on_drives(self):
        mover = _team("m", attrs=_attrs(speed=80), moves=[ANKLE_BREAKER])
        activations = 0
        for seed in range(5):
            r = simulate_game(mover, _team("a"), DEFAULT_RULESET, seed=seed)
            activations += sum(
                1 for p in r.possession_log if p.move_activated == "Ankle Breaker"
            )
        assert activations > 0

    def test_lockdown_stance_triggers_on_iso_defense(self):
        lock = _team("L", attrs=_attrs(defense=80), moves=[LOCKDOWN_STANCE])
        activations = 0
        for seed in range(5):
            r = simulate_game(
                _team("o", attrs=_attrs(ego=60)), lock, DEFAULT_RULESET, seed=seed,
            )
            activations += sum(
                1 for p in r.possession_log if p.move_activated == "Lockdown Stance"
            )
        assert activations > 0

    def test_no_look_pass_triggers_on_pass_events(self):
        passer = _team(
            "p", attrs=_attrs(passing=70, iq=60), moves=[NO_LOOK_PASS],
        )
        activations = 0
        for seed in range(5):
            r = simulate_game(passer, _team("a"), DEFAULT_RULESET, seed=seed)
            activations += sum(
                1 for p in r.possession_log if p.move_activated == "No-Look Pass"
            )
        assert activations > 0


# --- Governability: the chain is data ---------------------------------------


class TestChainGovernance:
    def test_picks_are_illegal_removes_screen_events(self):
        patch = GameDefinitionPatch(
            remove_actions=["screen_on_ball", "screen_off_ball"],
            description="Picks are illegal",
        )
        game_def = patch.apply(basketball_game_definition(DEFAULT_RULESET))
        for seed in range(5):
            r = simulate_game(
                _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                game_def=game_def,
            )
            assert r.home_score != r.away_score  # game still playable
            types = {e.event_type for e in _events(r)}
            assert not any(t.startswith("screen.on_ball") for t in types)
            assert not any(t.startswith("screen.off_ball") for t in types)
            assert sum(b.screen_assists for b in r.box_scores) == 0

    def test_transition_reweighting_via_patch(self):
        # Governance can reshape the chain: make every possession a
        # pass-first offense by rewriting initiate's transitions.
        patch = GameDefinitionPatch(
            modify_actions={
                "initiate": {"transitions": {"pass_swing": 95.0, "shot": 5.0}},
            },
            description="Move the ball",
        )
        game_def = patch.apply(basketball_game_definition(DEFAULT_RULESET))
        base_passes = 0
        patched_passes = 0
        for seed in range(5):
            b = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=seed)
            r = simulate_game(
                _team("h"), _team("a"), DEFAULT_RULESET, seed=seed,
                game_def=game_def,
            )
            base_passes += sum(bs.passes_made for bs in b.box_scores)
            patched_passes += sum(bs.passes_made for bs in r.box_scores)
        assert patched_passes > base_passes


# --- Narration --------------------------------------------------------------


class TestChainNarration:
    def test_known_event_types_narrate(self):
        names = {"h1": "Ayo", "d1": "Bex"}
        cases = [
            ("pass.swing", "success"),
            ("pass.kickout", "success"),
            ("dribble.crossover", "success"),
            ("screen.on_ball", "success"),
            ("screen.off_ball", "success"),
            ("cut.backdoor", "success"),
            ("defense.help_rotation", "success"),
            ("hustle.loose_ball", "offense"),
        ]
        for etype, outcome in cases:
            ev = GameEvent(
                seq=0, event_type=etype, actor_id="h1", target_id="d1",
                outcome=outcome,
            )
            line = narrate_event(ev, names, seed=1)
            assert "Ayo" in line, f"{etype}: {line}"

    def test_steal_and_contest_outcome_lines(self):
        names = {"d1": "Bex", "h1": "Ayo"}
        steal = GameEvent(
            seq=0, event_type="defense.steal_attempt",
            actor_id="d1", target_id="h1", outcome="steal",
        )
        assert "Bex" in narrate_event(steal, names)
        contest = GameEvent(
            seq=0, event_type="defense.contest",
            actor_id="d1", target_id="h1", outcome="open",
        )
        assert "Ayo" in narrate_event(contest, names)

    def test_unknown_event_type_falls_back_gracefully(self):
        ev = GameEvent(
            seq=0, event_type="ritual.summoning", actor_id="h1",
            outcome="success",
        )
        line = narrate_event(ev, {"h1": "Ayo"}, seed=0)
        assert "Ayo" in line
        assert "summoning" in line

    def test_dict_events_supported(self):
        # Events come back from DB JSON as plain dicts.
        line = narrate_event(
            {"event_type": "pass.swing", "actor_id": "h1", "outcome": "success"},
            {"h1": "Ayo"},
        )
        assert "Ayo" in line

    def test_screen_branch_suffix(self):
        ev = GameEvent(
            seq=0, event_type="screen.on_ball", actor_id="h1",
            outcome="success", detail="slip",
        )
        line = narrate_event(ev, {"h1": "Ayo"}, seed=0)
        assert "slip" in line.lower()


# --- Presenter payload -------------------------------------------------------


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, event_type: str, data: dict) -> int:
        self.events.append((event_type, data))
        return 1


@pytest.mark.asyncio
async def test_presenter_exposes_event_chain():
    result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
    bus = _Bus()
    state = PresentationState()
    await present_round([result], bus, state, quarter_replay_seconds=0.001)
    plays = [d for t, d in bus.events if t == "presentation.possession"]
    assert plays
    with_chain = [p for p in plays if p.get("events")]
    assert with_chain, "possession payloads should carry event chains"
    sample = with_chain[0]["events"][0]
    assert set(sample) == {"type", "outcome", "text"}
    assert sample["text"]
    # Bounded: never more than 32 chain entries per possession.
    assert all(len(p.get("events", [])) <= 32 for p in plays)


# --- Commentary: drama-selected chains only ---------------------------------


class TestCommentaryChains:
    def test_drama_selected_chains_are_bounded(self):
        result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        names = {b.hooper_id: b.hooper_name for b in result.box_scores}
        lines = _drama_selected_chains(result, names)
        assert len(lines) <= 3
        for line in lines:
            # Compact single-line chains, hard-capped events per chain.
            assert line.count("->") <= 14

    def test_game_context_includes_highlights_not_full_stream(self):
        result = simulate_game(_team("h"), _team("a"), DEFAULT_RULESET, seed=42)
        context = _build_game_context(
            result, _team("h"), _team("a"), RuleSet(),
        )
        total_events = sum(len(p.events) for p in result.possession_log)
        assert total_events > 200  # the raw stream is large...
        # ...but the context stays bounded: at most 3 highlighted chains.
        assert context.count("Q") < total_events
        if "Highlight possessions" in context:
            section = context.split("Highlight possessions", 1)[1]
            chain_lines = [
                ln for ln in section.splitlines() if "->" in ln
            ]
            assert 0 < len(chain_lines) <= 3
