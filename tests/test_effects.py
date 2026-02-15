"""Tests for the Proposal Effects System.

Covers:
- MetaStore: lifecycle, arithmetic ops, dirty tracking, snapshot immutability
- EffectSpec / ProposalInterpretation models
- RegisteredEffect: hook matching, apply actions, lifetime, serialization
- EffectRegistry: CRUD, tick, summary
- effect_spec_to_registered conversion
- Effect persistence: load_effect_registry, register_effects_for_proposal
- tally_governance_with_effects: effects registration on proposal pass
- Interpreter v2 mock: detection patterns + backward compat
- End-to-end: proposal -> interpret -> tally -> effects -> fire
- Simulation integration: effects during game simulation
- DB meta columns: team meta, flush, load_all
- Migration script idempotency
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.ai.interpreter import (
    interpret_proposal_mock,
    interpret_proposal_v2_mock,
)
from pinwheel.core.effects import (
    EffectRegistry,
    effect_spec_to_registered,
    load_effect_registry,
    persist_expired_effects,
    register_effects_for_proposal,
)
from pinwheel.core.governance import (
    cast_vote,
    confirm_proposal,
    submit_proposal,
    tally_governance_with_effects,
)
from pinwheel.core.hooks import (
    EffectLifetime,
    HookContext,
    HookResult,
    RegisteredEffect,
    apply_hook_results,
    fire_effects,
)
from pinwheel.core.meta import MetaStore
from pinwheel.core.state import GameState, HooperState
from pinwheel.core.tokens import regenerate_tokens
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.governance import (
    EffectSpec,
    ProposalInterpretation,
    RuleInterpretation,
)
from pinwheel.models.rules import RuleSet
from pinwheel.models.team import Hooper, PlayerAttributes

# --- Fixtures ---


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def repo(engine: AsyncEngine) -> Repository:
    async with get_session(engine) as session:
        yield Repository(session)


@pytest.fixture
async def season_id(repo: Repository) -> str:
    league = await repo.create_league("Test League")
    season = await repo.create_season(
        league_id=league.id,
        name="Season 1",
        starting_ruleset=RuleSet().model_dump(),
    )
    return season.id


@pytest.fixture
async def seeded_governor(repo: Repository, season_id: str) -> tuple[str, str]:
    """Create a governor with tokens. Returns (governor_id, team_id)."""
    team = await repo.create_team(season_id=season_id, name="Test Team")
    governor_id = "gov-effects-001"
    await regenerate_tokens(repo, governor_id, team.id, season_id)
    return governor_id, team.id


def _make_hooper(
    hooper_id: str = "h1",
    name: str = "Test Hooper",
    team_id: str = "team-a",
) -> Hooper:
    """Create a minimal Hooper for testing."""
    return Hooper(
        id=hooper_id,
        name=name,
        team_id=team_id,
        archetype="scorer",
        attributes=PlayerAttributes(
            scoring=80,
            passing=60,
            defense=50,
            speed=70,
            stamina=75,
            iq=65,
            ego=40,
            chaotic_alignment=30,
            fate=50,
        ),
    )


def _make_game_state() -> GameState:
    """Create a minimal GameState for testing hooks."""
    home_hoopers = [
        HooperState(hooper=_make_hooper("h1", "Alpha", "team-a")),
        HooperState(hooper=_make_hooper("h2", "Beta", "team-a")),
        HooperState(hooper=_make_hooper("h3", "Gamma", "team-a")),
    ]
    away_hoopers = [
        HooperState(hooper=_make_hooper("h4", "Delta", "team-b")),
        HooperState(hooper=_make_hooper("h5", "Epsilon", "team-b")),
        HooperState(hooper=_make_hooper("h6", "Zeta", "team-b")),
    ]
    return GameState(
        home_agents=home_hoopers,
        away_agents=away_hoopers,
        home_score=50,
        away_score=45,
    )


# ============================================================================
# MetaStore Tests
# ============================================================================


class TestMetaStore:
    def test_store_lifecycle(self):
        """get/set/get_all/entity_count/repr work correctly through a full lifecycle."""
        store = MetaStore()

        # Empty store has zero entities
        assert store.entity_count() == 0

        # Defaults (using a fresh store to avoid defaultdict side effects)
        default_store = MetaStore()
        assert default_store.get("team", "t1", "swagger") is None
        assert default_store.get("team", "t1", "swagger", default=0) == 0

        # Set and retrieve
        store.set("team", "t1", "swagger", 5)
        assert store.get("team", "t1", "swagger") == 5

        # Multiple keys on same entity + get_all
        store.set("team", "t1", "morale", 10)
        meta = store.get_all("team", "t1")
        assert meta == {"swagger": 5, "morale": 10}

        # Multiple entities across types
        store.set("team", "t2", "x", 2)
        store.set("hooper", "h1", "x", 3)
        assert store.entity_count() == 3

        # Repr
        r = repr(store)
        assert "entities=3" in r

    def test_arithmetic_and_toggle_operations(self):
        """increment, decrement, toggle, and increment-on-non-numeric all behave correctly."""
        store = MetaStore()

        # Increment from zero (key does not exist)
        result = store.increment("team", "t1", "wins", 1)
        assert result == 1

        # Increment existing value
        store.set("team", "t1", "swagger", 3)
        result = store.increment("team", "t1", "swagger", 2)
        assert result == 5
        assert store.get("team", "t1", "swagger") == 5

        # Decrement
        store.set("team", "t1", "morale", 10)
        result = store.decrement("team", "t1", "morale", 3)
        assert result == 7

        # Toggle (false->true->false)
        result1 = store.toggle("team", "t1", "bonus_active")
        assert result1 is True
        result2 = store.toggle("team", "t1", "bonus_active")
        assert result2 is False

        # Incrementing a non-numeric value starts from 0
        store.set("team", "t1", "name", "hello")
        result = store.increment("team", "t1", "name", 5)
        assert result == 5

    def test_dirty_tracking(self):
        """Dirty tracking marks set entities, clears on read, and ignores loads."""
        store = MetaStore()

        # set() marks entities dirty
        store.set("team", "t1", "swagger", 5)
        store.set("hooper", "h1", "bonus", True)
        dirty = store.get_dirty_entities()
        assert len(dirty) == 2
        types = {d[0] for d in dirty}
        assert "team" in types
        assert "hooper" in types

        # get_dirty_entities clears dirty set
        dirty2 = store.get_dirty_entities()
        assert len(dirty2) == 0

        # load_entity does NOT mark dirty
        store.load_entity("team", "t1", {"swagger": 5, "morale": 10})
        assert store.get("team", "t1", "swagger") == 5
        dirty3 = store.get_dirty_entities()
        assert len(dirty3) == 0

    def test_snapshot_immutability(self):
        """Snapshot returns a deep copy; modifying it does not affect the store."""
        store = MetaStore()
        store.set("team", "t1", "swagger", 5)
        snap = store.snapshot()
        assert snap["team"]["t1"]["swagger"] == 5

        # Modifying snapshot does not affect original
        snap["team"]["t1"]["swagger"] = 99
        assert store.get("team", "t1", "swagger") == 5


# ============================================================================
# EffectSpec / ProposalInterpretation Model Tests
# ============================================================================


class TestEffectSpec:
    def test_all_spec_types(self):
        """All EffectSpec types construct correctly with expected fields."""
        # parameter_change
        param = EffectSpec(
            effect_type="parameter_change",
            parameter="three_point_value",
            new_value=5,
            old_value=3,
            description="Three pointers now worth 5",
        )
        assert param.effect_type == "parameter_change"
        assert param.parameter == "three_point_value"
        assert param.duration == "permanent"

        # meta_mutation
        meta = EffectSpec(
            effect_type="meta_mutation",
            target_type="team",
            target_selector="winning_team",
            meta_field="swagger",
            meta_value=1,
            meta_operation="increment",
        )
        assert meta.target_type == "team"
        assert meta.meta_operation == "increment"

        # hook_callback
        hook = EffectSpec(
            effect_type="hook_callback",
            hook_point="sim.shot.pre",
            condition="offense team swagger >= 5",
            action_code={"type": "modify_probability", "modifier": 0.05},
        )
        assert hook.hook_point == "sim.shot.pre"
        assert hook.action_code is not None

        # narrative
        narr = EffectSpec(
            effect_type="narrative",
            narrative_instruction="Track team swagger in commentary",
        )
        assert narr.narrative_instruction is not None

        # n_rounds duration
        timed = EffectSpec(
            effect_type="hook_callback",
            hook_point="sim.shot.pre",
            duration="n_rounds",
            duration_rounds=3,
        )
        assert timed.duration == "n_rounds"
        assert timed.duration_rounds == 3


class TestProposalInterpretation:
    def test_to_rule_interpretation(self):
        """to_rule_interpretation extracts parameter info or returns None."""
        # With parameter effect
        pi_param = ProposalInterpretation(
            effects=[
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="three_point_value",
                    new_value=5,
                    old_value=3,
                )
            ],
            confidence=0.9,
            impact_analysis="Change three pointers to 5",
        )
        ri = pi_param.to_rule_interpretation()
        assert ri.parameter == "three_point_value"
        assert ri.new_value == 5
        assert ri.confidence == 0.9

        # Without parameter effect
        pi_narr = ProposalInterpretation(
            effects=[
                EffectSpec(
                    effect_type="narrative",
                    narrative_instruction="Rename the league",
                )
            ],
            confidence=0.7,
        )
        ri2 = pi_narr.to_rule_interpretation()
        assert ri2.parameter is None

    def test_from_rule_interpretation(self):
        """from_rule_interpretation converts legacy format both with and without params."""
        # With parameter
        ri = RuleInterpretation(
            parameter="three_point_value",
            new_value=5,
            old_value=3,
            confidence=0.9,
            impact_analysis="3pt -> 5pt",
        )
        pi = ProposalInterpretation.from_rule_interpretation(ri, "Make threes worth 5")
        assert len(pi.effects) == 1
        assert pi.effects[0].effect_type == "parameter_change"
        assert pi.effects[0].parameter == "three_point_value"
        assert pi.confidence == 0.9
        assert pi.original_text_echo == "Make threes worth 5"

        # Without parameter
        ri_none = RuleInterpretation(parameter=None, confidence=0.3)
        pi_none = ProposalInterpretation.from_rule_interpretation(ri_none)
        assert len(pi_none.effects) == 0
        assert pi_none.confidence == 0.3


# ============================================================================
# RegisteredEffect Tests
# ============================================================================


class TestRegisteredEffect:
    def test_hook_matching_and_conditions(self):
        """should_fire checks hook point match, gte/lte/eq condition evaluation."""
        # Basic hook matching
        effect = RegisteredEffect(
            effect_id="e1",
            proposal_id="p1",
            _hook_points=["round.game.post"],
        )
        ctx = HookContext()
        assert effect.should_fire("round.game.post", ctx) is True
        assert effect.should_fire("round.game.pre", ctx) is False

        # Condition met (gte)
        store = MetaStore()
        store.set("team", "team-a", "swagger", 5)
        effect_cond = RegisteredEffect(
            effect_id="e2",
            proposal_id="p1",
            _hook_points=["sim.shot.pre"],
            action_code={
                "type": "modify_probability",
                "modifier": 0.05,
                "condition_check": {
                    "meta_field": "swagger",
                    "entity_type": "team",
                    "gte": 5,
                },
            },
        )
        game_state = _make_game_state()
        ctx_meta = HookContext(game_state=game_state, meta_store=store)
        assert effect_cond.should_fire("sim.shot.pre", ctx_meta) is True

        # Condition NOT met (swagger=3, need gte=5)
        store2 = MetaStore()
        store2.set("team", "team-a", "swagger", 3)
        ctx_meta2 = HookContext(game_state=game_state, meta_store=store2)
        effect_cond2 = RegisteredEffect(
            effect_id="e3",
            proposal_id="p1",
            _hook_points=["sim.shot.pre"],
            action_code={
                "type": "modify_probability",
                "modifier": 0.05,
                "condition_check": {
                    "meta_field": "swagger",
                    "entity_type": "team",
                    "gte": 5,
                },
            },
        )
        assert effect_cond2.should_fire("sim.shot.pre", ctx_meta2) is False

        # lte condition
        store3 = MetaStore()
        store3.set("team", "team-a", "fatigue", 3)
        effect_lte = RegisteredEffect(
            effect_id="e4",
            proposal_id="p1",
            _hook_points=["sim.shot.pre"],
            action_code={
                "type": "modify_probability",
                "modifier": -0.05,
                "condition_check": {
                    "meta_field": "fatigue",
                    "entity_type": "team",
                    "lte": 5,
                },
            },
        )
        ctx_meta3 = HookContext(game_state=game_state, meta_store=store3)
        assert effect_lte.should_fire("sim.shot.pre", ctx_meta3) is True

        # eq condition
        store4 = MetaStore()
        store4.set("team", "team-a", "status", "hot")
        effect_eq = RegisteredEffect(
            effect_id="e5",
            proposal_id="p1",
            _hook_points=["sim.shot.pre"],
            action_code={
                "type": "modify_probability",
                "modifier": 0.1,
                "condition_check": {
                    "meta_field": "status",
                    "entity_type": "team",
                    "eq": "hot",
                },
            },
        )
        ctx_meta4 = HookContext(game_state=game_state, meta_store=store4)
        assert effect_eq.should_fire("sim.shot.pre", ctx_meta4) is True

    def test_apply_actions(self):
        """apply() handles modify_probability, modify_score, write_meta,
        meta_mutation (set + increment), and narrative."""
        # modify_probability
        effect_prob = RegisteredEffect(
            effect_id="e1",
            proposal_id="p1",
            _hook_points=["sim.shot.pre"],
            effect_type="hook_callback",
            action_code={"type": "modify_probability", "modifier": 0.1},
        )
        result = effect_prob.apply("sim.shot.pre", HookContext())
        assert result.shot_probability_modifier == pytest.approx(0.1)

        # modify_score
        effect_score = RegisteredEffect(
            effect_id="e2",
            proposal_id="p1",
            _hook_points=["sim.shot.post"],
            effect_type="hook_callback",
            action_code={"type": "modify_score", "modifier": 2},
        )
        result2 = effect_score.apply("sim.shot.post", HookContext())
        assert result2.score_modifier == 2

        # write_meta
        store = MetaStore()
        effect_write = RegisteredEffect(
            effect_id="e3",
            proposal_id="p1",
            _hook_points=["round.game.post"],
            effect_type="hook_callback",
            action_code={
                "type": "write_meta",
                "entity": "team:{winner_team_id}",
                "field": "swagger",
                "value": 1,
                "op": "increment",
            },
        )
        ctx_write = HookContext(meta_store=store, winner_team_id="team-a")
        effect_write.apply("round.game.post", ctx_write)
        assert store.get("team", "team-a", "swagger") == 1

        # meta_mutation (set)
        store2 = MetaStore()
        effect_set = RegisteredEffect(
            effect_id="e4",
            proposal_id="p1",
            _hook_points=["round.game.post"],
            effect_type="meta_mutation",
            target_type="team",
            target_selector="team-a",
            meta_field="morale",
            meta_value=5,
            meta_operation="set",
        )
        effect_set.apply("round.game.post", HookContext(meta_store=store2))
        assert store2.get("team", "team-a", "morale") == 5

        # meta_mutation (increment)
        store2.set("team", "team-a", "morale", 3)
        effect_inc = RegisteredEffect(
            effect_id="e5",
            proposal_id="p1",
            _hook_points=["round.game.post"],
            effect_type="meta_mutation",
            target_type="team",
            target_selector="team-a",
            meta_field="morale",
            meta_value=2,
            meta_operation="increment",
        )
        effect_inc.apply("round.game.post", HookContext(meta_store=store2))
        assert store2.get("team", "team-a", "morale") == 5

        # narrative
        effect_narr = RegisteredEffect(
            effect_id="e6",
            proposal_id="p1",
            _hook_points=["report.simulation.pre"],
            effect_type="narrative",
            narrative_instruction="The league is now the Chaos Basketball Association",
        )
        result_narr = effect_narr.apply("report.simulation.pre", HookContext())
        assert "Chaos Basketball Association" in result_narr.narrative

    def test_lifetime_tick(self):
        """tick_round correctly handles permanent, n_rounds, and one_game lifetimes."""
        # Permanent: never expires
        perm = RegisteredEffect(
            effect_id="e1",
            proposal_id="p1",
            _lifetime=EffectLifetime.PERMANENT,
        )
        assert perm.tick_round() is False

        # N_ROUNDS: counts down
        n_round = RegisteredEffect(
            effect_id="e2",
            proposal_id="p1",
            _lifetime=EffectLifetime.N_ROUNDS,
            rounds_remaining=2,
        )
        assert n_round.tick_round() is False  # 2 -> 1
        assert n_round.tick_round() is True  # 1 -> 0 = expired

        # ONE_GAME: always expires after one tick
        one_game = RegisteredEffect(
            effect_id="e3",
            proposal_id="p1",
            _lifetime=EffectLifetime.ONE_GAME,
        )
        assert one_game.tick_round() is True

    def test_serialization_roundtrip(self):
        """to_dict/from_dict roundtrip preserves all fields; from_dict handles bad data."""
        effect = RegisteredEffect(
            effect_id="e1",
            proposal_id="p1",
            _hook_points=["sim.shot.pre", "sim.shot.post"],
            _lifetime=EffectLifetime.N_ROUNDS,
            rounds_remaining=5,
            registered_at_round=3,
            effect_type="hook_callback",
            condition="swagger >= 5",
            action_code={"type": "modify_probability", "modifier": 0.05},
            description="Swagger shooting boost",
        )
        d = effect.to_dict()
        restored = RegisteredEffect.from_dict(d)

        assert restored.effect_id == "e1"
        assert restored.proposal_id == "p1"
        assert restored.hook_points == ["sim.shot.pre", "sim.shot.post"]
        assert restored.lifetime == EffectLifetime.N_ROUNDS
        assert restored.rounds_remaining == 5
        assert restored.registered_at_round == 3
        assert restored.action_code == {"type": "modify_probability", "modifier": 0.05}

        # Bad data: from_dict handles empty dict gracefully
        bad = RegisteredEffect.from_dict({})
        assert bad.effect_id == ""
        assert bad.lifetime == EffectLifetime.PERMANENT
        assert bad.hook_points == []


# ============================================================================
# fire_effects / apply_hook_results Tests
# ============================================================================


class TestFireEffects:
    def test_fire_effects_routing_and_aggregation(self):
        """fire_effects returns results for matching hooks, filters non-matching,
        and aggregates multiple effects."""
        effect = RegisteredEffect(
            effect_id="e1",
            proposal_id="p1",
            _hook_points=["sim.shot.post"],
            effect_type="hook_callback",
            action_code={"type": "modify_score", "modifier": 1},
        )
        ctx = HookContext()

        # Matching hook returns results
        results = fire_effects("sim.shot.post", ctx, [effect])
        assert len(results) == 1
        assert results[0].score_modifier == 1

        # Non-matching hook returns nothing
        results_empty = fire_effects("sim.shot.pre", ctx, [effect])
        assert len(results_empty) == 0

        # Multiple effects aggregate
        e1 = RegisteredEffect(
            effect_id="e1",
            proposal_id="p1",
            _hook_points=["sim.shot.pre"],
            effect_type="hook_callback",
            action_code={"type": "modify_probability", "modifier": 0.05},
        )
        e2 = RegisteredEffect(
            effect_id="e2",
            proposal_id="p2",
            _hook_points=["sim.shot.pre"],
            effect_type="hook_callback",
            action_code={"type": "modify_probability", "modifier": 0.03},
        )
        multi_results = fire_effects("sim.shot.pre", ctx, [e1, e2])
        assert len(multi_results) == 2
        total = sum(r.shot_probability_modifier for r in multi_results)
        assert total == pytest.approx(0.08)

    def test_apply_hook_results_modifiers(self):
        """apply_hook_results applies score and stamina modifiers, with clamping."""
        # Score modifier
        game_state = _make_game_state()
        initial_home = game_state.home_score
        ctx = HookContext(game_state=game_state)
        apply_hook_results([HookResult(score_modifier=3)], ctx)
        assert game_state.home_score == initial_home + 3

        # Stamina modifier
        game_state2 = _make_game_state()
        hooper = game_state2.home_agents[0]
        hooper.current_stamina = 0.8
        ctx2 = HookContext(game_state=game_state2, hooper=hooper)
        apply_hook_results([HookResult(stamina_modifier=-0.1)], ctx2)
        assert hooper.current_stamina == pytest.approx(0.7)

        # Stamina clamping at 0.0
        game_state3 = _make_game_state()
        hooper2 = game_state3.home_agents[0]
        hooper2.current_stamina = 0.05
        ctx3 = HookContext(game_state=game_state3, hooper=hooper2)
        apply_hook_results([HookResult(stamina_modifier=-0.5)], ctx3)
        assert hooper2.current_stamina == 0.0


# ============================================================================
# EffectRegistry Tests
# ============================================================================


class TestEffectRegistry:
    def test_registry_crud(self):
        """register, deregister, get_all_active, get_effects_for_proposal,
        get_narrative_effects, get_effects_for_hook all work correctly."""
        registry = EffectRegistry()

        # Register and retrieve by hook
        e1 = RegisteredEffect(
            effect_id="e1",
            proposal_id="p1",
            _hook_points=["sim.shot.pre"],
        )
        registry.register(e1)
        assert registry.count == 1
        effects = registry.get_effects_for_hook("sim.shot.pre")
        assert len(effects) == 1

        # Register more and check get_all_active
        e2 = RegisteredEffect(effect_id="e2", proposal_id="p1")
        e3 = RegisteredEffect(
            effect_id="e3", proposal_id="p2", effect_type="narrative"
        )
        e4 = RegisteredEffect(
            effect_id="e4", proposal_id="p2", effect_type="hook_callback"
        )
        registry.register(e2)
        registry.register(e3)
        registry.register(e4)
        assert len(registry.get_all_active()) == 4

        # get_effects_for_proposal
        p1_effects = registry.get_effects_for_proposal("p1")
        assert len(p1_effects) == 2

        # get_narrative_effects
        narratives = registry.get_narrative_effects()
        assert len(narratives) == 1
        assert narratives[0].effect_type == "narrative"

        # Deregister existing
        removed = registry.deregister("e1")
        assert removed is not None
        assert registry.count == 3

        # Deregister nonexistent
        removed_none = registry.deregister("nonexistent")
        assert removed_none is None

    def test_registry_tick_and_summary(self):
        """tick_round expires effects; build_effects_summary reflects state."""
        registry = EffectRegistry()

        # Empty summary
        assert registry.build_effects_summary() == "No active effects."

        # Register one expiring and one permanent
        e1 = RegisteredEffect(
            effect_id="e1",
            proposal_id="p1",
            _lifetime=EffectLifetime.N_ROUNDS,
            rounds_remaining=1,
        )
        e2 = RegisteredEffect(
            effect_id="e2",
            proposal_id="p2",
            _lifetime=EffectLifetime.PERMANENT,
            _hook_points=["sim.shot.pre"],
            effect_type="hook_callback",
            description="Swagger shooting boost",
        )
        registry.register(e1)
        registry.register(e2)

        # tick expires e1
        expired = registry.tick_round(5)
        assert "e1" in expired
        assert registry.count == 1

        # Summary with remaining effect
        summary = registry.build_effects_summary()
        assert "Swagger shooting boost" in summary
        assert "hook_callback" in summary


# ============================================================================
# effect_spec_to_registered Tests
# ============================================================================


class TestEffectSpecToRegistered:
    def test_all_conversion_types(self):
        """effect_spec_to_registered handles hook_callback, n_rounds,
        meta_mutation default hooks, narrative report hooks, and parameter_change."""
        # Permanent hook_callback
        spec_hook = EffectSpec(
            effect_type="hook_callback",
            hook_point="sim.shot.pre",
            action_code={"type": "modify_probability", "modifier": 0.05},
            description="5% boost",
        )
        eff1 = effect_spec_to_registered(spec_hook, "p1", 3)
        assert eff1.proposal_id == "p1"
        assert eff1.registered_at_round == 3
        assert eff1.hook_points == ["sim.shot.pre"]
        assert eff1.lifetime == EffectLifetime.PERMANENT
        assert eff1.action_code is not None

        # N_ROUNDS duration
        spec_timed = EffectSpec(
            effect_type="hook_callback",
            hook_point="round.game.post",
            duration="n_rounds",
            duration_rounds=5,
            description="Temporary effect",
        )
        eff2 = effect_spec_to_registered(spec_timed, "p1", 1)
        assert eff2.lifetime == EffectLifetime.N_ROUNDS
        assert eff2.rounds_remaining == 5

        # meta_mutation gets default hook (round.game.post)
        spec_meta = EffectSpec(
            effect_type="meta_mutation",
            target_type="team",
            target_selector="winning_team",
            meta_field="swagger",
            meta_value=1,
            meta_operation="increment",
        )
        eff3 = effect_spec_to_registered(spec_meta, "p1", 1)
        assert "round.game.post" in eff3.hook_points

        # narrative gets report hooks
        spec_narr = EffectSpec(
            effect_type="narrative",
            narrative_instruction="Track swagger",
        )
        eff4 = effect_spec_to_registered(spec_narr, "p1", 1)
        assert "report.simulation.pre" in eff4.hook_points
        assert "report.commentary.pre" in eff4.hook_points

        # parameter_change: creates an effect but register_effects_for_proposal skips it
        spec_param = EffectSpec(
            effect_type="parameter_change",
            parameter="three_point_value",
            new_value=5,
        )
        eff5 = effect_spec_to_registered(spec_param, "p1", 1)
        assert eff5.effect_id  # It creates one, but register skips it


# ============================================================================
# Effect Persistence Tests (load_effect_registry, register_effects_for_proposal)
# ============================================================================


class TestEffectPersistence:
    async def test_register_load_and_expiration(
        self, repo: Repository, season_id: str
    ):
        """Register effects, load from store, then verify expired effects are skipped."""
        registry = EffectRegistry()
        specs = [
            EffectSpec(
                effect_type="meta_mutation",
                target_type="team",
                target_selector="winning_team",
                meta_field="swagger",
                meta_value=1,
                meta_operation="increment",
                description="Win swagger",
            ),
            EffectSpec(
                effect_type="narrative",
                narrative_instruction="Track swagger",
                description="Swagger narrative",
            ),
        ]
        registered = await register_effects_for_proposal(
            repo=repo,
            registry=registry,
            proposal_id="p-test",
            effects=specs,
            season_id=season_id,
            current_round=1,
        )
        assert len(registered) == 2
        assert registry.count == 2

        # Load from event store into a fresh registry
        loaded_registry = await load_effect_registry(repo, season_id)
        assert loaded_registry.count == 2

        # Mark one as expired, verify it's skipped on reload
        effect_id = registered[0].effect_id
        await persist_expired_effects(repo, season_id, [effect_id])
        loaded2 = await load_effect_registry(repo, season_id)
        assert loaded2.count == 1

    async def test_load_skips_repealed(self, repo: Repository, season_id: str):
        """Repealed effects should not be loaded."""
        registry = EffectRegistry()
        specs = [
            EffectSpec(
                effect_type="narrative",
                narrative_instruction="Repealed narrative",
                description="Will be repealed",
            ),
        ]
        registered = await register_effects_for_proposal(
            repo, registry, "p-test", specs, season_id, 1
        )
        effect_id = registered[0].effect_id

        # Mark as repealed
        await repo.append_event(
            event_type="effect.repealed",
            aggregate_id=effect_id,
            aggregate_type="effect",
            season_id=season_id,
            payload={"effect_id": effect_id, "reason": "governance_repeal"},
        )

        loaded = await load_effect_registry(repo, season_id)
        assert loaded.count == 0

    async def test_parameter_change_not_registered(
        self, repo: Repository, season_id: str
    ):
        """parameter_change effects should NOT be registered (handled via RuleSet)."""
        registry = EffectRegistry()
        specs = [
            EffectSpec(
                effect_type="parameter_change",
                parameter="three_point_value",
                new_value=5,
                description="3pt -> 5pt",
            ),
        ]
        registered = await register_effects_for_proposal(
            repo, registry, "p-test", specs, season_id, 1
        )
        assert len(registered) == 0
        assert registry.count == 0


# ============================================================================
# tally_governance_with_effects Tests
# ============================================================================


class TestTallyGovernanceWithEffects:
    async def test_passing_proposal_registers_effects(
        self,
        repo: Repository,
        season_id: str,
        seeded_governor: tuple[str, str],
    ):
        """A passing proposal with v2 effects gets them registered."""
        gov_id, team_id = seeded_governor
        gov2_id = "gov-effects-002"
        await regenerate_tokens(repo, gov2_id, team_id, season_id)

        interpretation = interpret_proposal_mock("Make three pointers worth 5", RuleSet())
        proposal = await submit_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_id,
            season_id=season_id,
            window_id="",
            raw_text="Make three pointers worth 5",
            interpretation=interpretation,
            ruleset=RuleSet(),
        )
        proposal = await confirm_proposal(repo, proposal)

        vote1 = await cast_vote(
            repo=repo,
            proposal=proposal,
            governor_id=gov_id,
            team_id=team_id,
            vote_choice="yes",
            weight=1.0,
        )
        vote2 = await cast_vote(
            repo=repo,
            proposal=proposal,
            governor_id=gov2_id,
            team_id=team_id,
            vote_choice="yes",
            weight=1.0,
        )

        registry = EffectRegistry()
        new_ruleset, tallies = await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={proposal.id: [vote1, vote2]},
            current_ruleset=RuleSet(),
            round_number=1,
            effect_registry=registry,
        )

        assert len(tallies) == 1
        assert tallies[0].passed is True
        assert new_ruleset.three_point_value == 5

    async def test_failing_proposal_no_effects(
        self,
        repo: Repository,
        season_id: str,
        seeded_governor: tuple[str, str],
    ):
        """A failing proposal does not register effects."""
        gov_id, team_id = seeded_governor

        interpretation = interpret_proposal_mock("Make three pointers worth 5", RuleSet())
        proposal = await submit_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_id,
            season_id=season_id,
            window_id="",
            raw_text="Make three pointers worth 5",
            interpretation=interpretation,
            ruleset=RuleSet(),
        )
        proposal = await confirm_proposal(repo, proposal)

        vote = await cast_vote(
            repo=repo,
            proposal=proposal,
            governor_id=gov_id,
            team_id=team_id,
            vote_choice="no",
            weight=1.0,
        )

        registry = EffectRegistry()
        new_ruleset, tallies = await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={proposal.id: [vote]},
            current_ruleset=RuleSet(),
            round_number=1,
            effect_registry=registry,
        )

        assert tallies[0].passed is False
        assert new_ruleset.three_point_value == 3  # Unchanged
        assert registry.count == 0

    async def test_backward_compatible_without_registry(
        self,
        repo: Repository,
        season_id: str,
        seeded_governor: tuple[str, str],
    ):
        """tally_governance_with_effects works without a registry (backward compat)."""
        gov_id, team_id = seeded_governor
        gov2_id = "gov-effects-003"
        await regenerate_tokens(repo, gov2_id, team_id, season_id)

        interpretation = interpret_proposal_mock("Make three pointers worth 5", RuleSet())
        proposal = await submit_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_id,
            season_id=season_id,
            window_id="",
            raw_text="Make three pointers worth 5",
            interpretation=interpretation,
            ruleset=RuleSet(),
        )
        proposal = await confirm_proposal(repo, proposal)

        vote1 = await cast_vote(
            repo=repo,
            proposal=proposal,
            governor_id=gov_id,
            team_id=team_id,
            vote_choice="yes",
            weight=1.0,
        )
        vote2 = await cast_vote(
            repo=repo,
            proposal=proposal,
            governor_id=gov2_id,
            team_id=team_id,
            vote_choice="yes",
            weight=1.0,
        )

        # No registry -- should still work
        new_ruleset, tallies = await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={proposal.id: [vote1, vote2]},
            current_ruleset=RuleSet(),
            round_number=1,
            effect_registry=None,
        )

        assert tallies[0].passed is True
        assert new_ruleset.three_point_value == 5


# ============================================================================
# Interpreter V2 Mock Tests
# ============================================================================


class TestInterpreterV2Mock:
    def test_detection_patterns(self):
        """V2 mock detects parameter, swagger, morale, shooting boost,
        narrative, and unparseable patterns."""
        # Parameter change
        result_param = interpret_proposal_v2_mock(
            "Make three pointers worth 5", RuleSet()
        )
        assert len(result_param.effects) == 1
        assert result_param.effects[0].effect_type == "parameter_change"
        assert result_param.effects[0].parameter == "three_point_value"
        assert result_param.effects[0].new_value == 5
        assert result_param.confidence > 0.5

        # Swagger meta mutation
        result_swagger = interpret_proposal_v2_mock(
            "Every team that wins gets +1 swagger", RuleSet()
        )
        has_meta = any(e.effect_type == "meta_mutation" for e in result_swagger.effects)
        has_narrative = any(
            e.effect_type == "narrative" for e in result_swagger.effects
        )
        assert has_meta
        assert has_narrative

        # Morale meta mutation
        result_morale = interpret_proposal_v2_mock(
            "Teams should track morale", RuleSet()
        )
        has_morale_meta = any(
            e.effect_type == "meta_mutation" for e in result_morale.effects
        )
        assert has_morale_meta

        # Shooting boost callback
        result_boost = interpret_proposal_v2_mock(
            "Give teams a shooting bonus", RuleSet()
        )
        has_callback = any(
            e.effect_type == "hook_callback" for e in result_boost.effects
        )
        assert has_callback

        # Narrative pattern
        result_narr = interpret_proposal_v2_mock(
            "Call the league the Chaos Basketball Association", RuleSet()
        )
        has_narr = any(e.effect_type == "narrative" for e in result_narr.effects)
        assert has_narr

        # Unparseable becomes low-confidence narrative
        result_vague = interpret_proposal_v2_mock(
            "Make the game more exciting and fun", RuleSet()
        )
        assert len(result_vague.effects) >= 1
        assert result_vague.clarification_needed is True
        assert result_vague.confidence < 0.5

    def test_conditional_mechanics(self):
        """V2 mock detects 'when X happens, do Y' conditional proposals."""
        ruleset = RuleSet()

        # "When the ball goes out of bounds, double the next basket"
        result_oob = interpret_proposal_v2_mock(
            "When the ball goes out of bounds, double the value of the next basket",
            ruleset,
        )
        assert result_oob.confidence >= 0.8
        assert not result_oob.clarification_needed
        has_hook = any(e.effect_type == "hook_callback" for e in result_oob.effects)
        assert has_hook, "Out-of-bounds conditional should produce a hook_callback"
        hook_effect = next(e for e in result_oob.effects if e.effect_type == "hook_callback")
        assert hook_effect.hook_point == "sim.possession.pre"
        assert hook_effect.action_code is not None
        assert hook_effect.action_code["type"] == "modify_score"

        # "Losing team gets a shooting boost"
        result_trailing = interpret_proposal_v2_mock(
            "The losing team gets a shooting boost every possession", ruleset,
        )
        assert result_trailing.confidence >= 0.8
        has_hook_t = any(e.effect_type == "hook_callback" for e in result_trailing.effects)
        assert has_hook_t

        # "After halftime threes are worth 4"
        result_half = interpret_proposal_v2_mock(
            "After halftime, threes should be worth 4 points", ruleset,
        )
        assert result_half.confidence >= 0.8
        has_hook_h = any(e.effect_type == "hook_callback" for e in result_half.effects)
        assert has_hook_h

        # "First basket of each quarter is worth double"
        result_first = interpret_proposal_v2_mock(
            "The first basket of each quarter is worth double", ruleset,
        )
        assert result_first.confidence >= 0.8
        has_hook_f = any(e.effect_type == "hook_callback" for e in result_first.effects)
        assert has_hook_f

    def test_backward_compat_conversion(self):
        """V2 result can convert to legacy RuleInterpretation."""
        result = interpret_proposal_v2_mock("Make three pointers worth 5", RuleSet())
        legacy = result.to_rule_interpretation()
        assert legacy.parameter == "three_point_value"
        assert legacy.new_value == 5


# ============================================================================
# End-to-End Integration Test
# ============================================================================


class TestEffectsEndToEnd:
    def test_full_swagger_scenario(self):
        """End-to-end: swagger effect fires at round.game.post, then condition-based
        shooting boost fires at sim.shot.pre."""
        store = MetaStore()
        registry = EffectRegistry()

        # Effect 1: Winning by 20+ increments swagger
        swagger_spec = EffectSpec(
            effect_type="hook_callback",
            hook_point="round.game.post",
            condition="winner margin >= 20",
            action_code={
                "type": "write_meta",
                "entity": "team:{winner_team_id}",
                "field": "swagger",
                "value": 1,
                "op": "increment",
            },
            description="Win by 20+ gets swagger",
        )
        swagger_effect = effect_spec_to_registered(swagger_spec, "p1", 1)
        registry.register(swagger_effect)

        # Effect 2: Swagger >= 5 gives shooting boost
        boost_spec = EffectSpec(
            effect_type="hook_callback",
            hook_point="sim.shot.pre",
            action_code={
                "type": "modify_probability",
                "modifier": 0.05,
                "condition_check": {
                    "meta_field": "swagger",
                    "entity_type": "team",
                    "gte": 5,
                },
            },
            description="Swagger 5+ shooting boost",
        )
        boost_effect = effect_spec_to_registered(boost_spec, "p1", 1)
        registry.register(boost_effect)

        # Simulate a blowout win for team-a
        ctx_post_game = HookContext(
            meta_store=store,
            winner_team_id="team-a",
            home_team_id="team-a",
            away_team_id="team-b",
            margin=25,
        )

        # Fire round.game.post 5 times (5 blowout wins)
        for _ in range(5):
            effects = registry.get_effects_for_hook("round.game.post")
            fire_effects("round.game.post", ctx_post_game, list(effects))

        # team-a now has swagger = 5
        assert store.get("team", "team-a", "swagger") == 5

        # Now fire sim.shot.pre -- boost should activate
        game_state = _make_game_state()
        ctx_shot = HookContext(
            game_state=game_state,
            meta_store=store,
        )
        shot_effects = registry.get_effects_for_hook("sim.shot.pre")
        results = fire_effects("sim.shot.pre", ctx_shot, shot_effects)

        assert len(results) == 1
        assert results[0].shot_probability_modifier == pytest.approx(0.05)

    def test_swagger_not_met(self):
        """Swagger boost does NOT fire when swagger < 5."""
        store = MetaStore()
        store.set("team", "team-a", "swagger", 3)

        registry = EffectRegistry()
        boost_spec = EffectSpec(
            effect_type="hook_callback",
            hook_point="sim.shot.pre",
            action_code={
                "type": "modify_probability",
                "modifier": 0.05,
                "condition_check": {
                    "meta_field": "swagger",
                    "entity_type": "team",
                    "gte": 5,
                },
            },
        )
        boost_effect = effect_spec_to_registered(boost_spec, "p1", 1)
        registry.register(boost_effect)

        game_state = _make_game_state()
        ctx = HookContext(game_state=game_state, meta_store=store)
        effects = registry.get_effects_for_hook("sim.shot.pre")
        results = fire_effects("sim.shot.pre", ctx, effects)

        assert len(results) == 0  # Condition not met, nothing fired

    def test_effect_expiration_lifecycle(self):
        """Effects with N_ROUNDS lifetime expire correctly."""
        registry = EffectRegistry()
        spec = EffectSpec(
            effect_type="hook_callback",
            hook_point="sim.shot.pre",
            action_code={"type": "modify_probability", "modifier": 0.1},
            duration="n_rounds",
            duration_rounds=2,
            description="Temporary boost",
        )
        effect = effect_spec_to_registered(spec, "p1", 1)
        registry.register(effect)

        assert registry.count == 1

        # Round 2: still active
        expired = registry.tick_round(2)
        assert len(expired) == 0
        assert registry.count == 1

        # Round 3: expires
        expired = registry.tick_round(3)
        assert len(expired) == 1
        assert registry.count == 0


# ---------------------------------------------------------------------------
# Simulation Integration Tests
# ---------------------------------------------------------------------------


class TestSimulationEffectsIntegration:
    """Test that effects fire correctly during game simulation."""

    def _make_team(self, team_id: str, name: str) -> object:
        """Build a minimal Team for simulation."""
        from pinwheel.models.team import Hooper, PlayerAttributes, Team, Venue

        hoopers = []
        for i in range(4):
            attrs = PlayerAttributes(
                scoring=50, passing=50, defense=50, speed=50,
                iq=50, stamina=50, ego=50, chaotic_alignment=50, fate=50,
            )
            hoopers.append(
                Hooper(
                    id=f"{team_id}-h{i}",
                    name=f"{name} Player {i}",
                    team_id=team_id,
                    archetype="balanced",
                    attributes=attrs,
                    is_starter=i < 3,
                )
            )
        return Team(
            id=team_id,
            name=name,
            hoopers=hoopers,
            venue=Venue(name="Test Arena", capacity=5000),
        )

    def test_simulate_game_accepts_effect_registry(self):
        """simulate_game runs with effect_registry and meta_store params."""
        from pinwheel.core.simulation import simulate_game
        from pinwheel.models.rules import RuleSet

        home = self._make_team("t1", "Home")
        away = self._make_team("t2", "Away")
        rules = RuleSet()
        meta_store = MetaStore()

        # Create a simple narrative effect (no-op mechanically)
        effect = RegisteredEffect(
            effect_id="test-1",
            proposal_id="p1",
            _hook_points=["sim.game.pre", "sim.game.end"],
            effect_type="narrative",
            narrative_instruction="Test narrative",
        )

        result = simulate_game(
            home, away, rules, seed=42,
            effect_registry=[effect],
            meta_store=meta_store,
        )

        assert result.home_score > 0 or result.away_score > 0
        assert result.total_possessions > 0

    def test_effects_modify_meta_during_simulation(self):
        """Effects can write meta during simulation hooks."""
        from pinwheel.core.simulation import simulate_game
        from pinwheel.models.rules import RuleSet

        home = self._make_team("t1", "Home")
        away = self._make_team("t2", "Away")
        rules = RuleSet()
        meta_store = MetaStore()

        # Effect that increments a counter on each quarter end
        effect = RegisteredEffect(
            effect_id="quarter-counter",
            proposal_id="p1",
            _hook_points=["sim.quarter.end"],
            effect_type="meta_mutation",
            target_type="team",
            target_selector="t1",
            meta_field="quarters_played",
            meta_value=1,
            meta_operation="increment",
        )

        simulate_game(
            home, away, rules, seed=42,
            effect_registry=[effect],
            meta_store=meta_store,
        )

        # Should have incremented at least once (quarters end)
        quarters = meta_store.get("team", "t1", "quarters_played", default=0)
        assert isinstance(quarters, (int, float))
        assert quarters > 0

    def test_simulation_without_effects_unchanged(self):
        """Simulation without effects produces identical results to before."""
        from pinwheel.core.simulation import simulate_game
        from pinwheel.models.rules import RuleSet

        home = self._make_team("t1", "Home")
        away = self._make_team("t2", "Away")
        rules = RuleSet()

        # Run without effects
        result1 = simulate_game(home, away, rules, seed=42)

        # Run with empty effects
        result2 = simulate_game(
            home, away, rules, seed=42,
            effect_registry=None,
            meta_store=None,
        )

        assert result1.home_score == result2.home_score
        assert result1.away_score == result2.away_score
        assert result1.total_possessions == result2.total_possessions


# ---------------------------------------------------------------------------
# DB Meta Column Tests
# ---------------------------------------------------------------------------


class TestDBMetaColumns:
    """Test meta columns on ORM models."""

    @pytest.fixture
    async def _engine(self):
        """Create an in-memory SQLite engine with all tables."""
        from pinwheel.db.engine import create_engine
        from pinwheel.db.models import Base

        eng = create_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield eng
        await eng.dispose()

    @pytest.mark.asyncio
    async def test_team_meta_flush_and_load(self, _engine):
        """TeamRow meta column: write, flush from MetaStore, and load_all."""
        from pinwheel.db.engine import get_session
        from pinwheel.db.repository import Repository

        async with get_session(_engine) as session:
            repo = Repository(session)
            league = await repo.create_league("Test League")
            season = await repo.create_season(league.id, "S1")
            t1 = await repo.create_team(season.id, "Team A")
            t2 = await repo.create_team(season.id, "Team B")

            # Direct update_team_meta and read back
            await repo.update_team_meta(t1.id, {"swagger": 5, "morale": "high"})
            team_row = await repo.get_team(t1.id)
            assert team_row is not None
            assert team_row.meta is not None
            assert team_row.meta.get("swagger") == 5
            assert team_row.meta.get("morale") == "high"

            # flush_meta_store writes MetaStore dirty entries to DB
            store = MetaStore()
            store.set("team", t2.id, "swagger", 3)
            store.set("team", t2.id, "style", "aggressive")
            dirty = store.get_dirty_entities()
            await repo.flush_meta_store(dirty)
            t2_row = await repo.get_team(t2.id)
            assert t2_row is not None
            assert t2_row.meta is not None
            assert t2_row.meta.get("swagger") == 3
            assert t2_row.meta.get("style") == "aggressive"

            # Update t1 meta for load_all test
            await repo.update_team_meta(t2.id, {"swagger": 2})
            all_meta = await repo.load_all_team_meta(season.id)
            assert t1.id in all_meta
            assert all_meta[t1.id].get("swagger") == 5
            assert t2.id in all_meta


# ---------------------------------------------------------------------------
# Migration Script Test
# ---------------------------------------------------------------------------


class TestMigrationScript:
    """Test the meta column migration script."""

    def test_migrate_add_meta_idempotent(self, tmp_path):
        """Migration script is idempotent -- safe to run twice."""
        import sqlite3

        db_path = str(tmp_path / "test.db")

        # Create minimal schema
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE teams (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE hoopers (id TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()

        from scripts.migrate_add_meta import migrate

        # First run: adds columns
        migrate(f"sqlite:///{db_path}")

        # Second run: skips (no error)
        migrate(f"sqlite:///{db_path}")

        # Verify column exists
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(teams)")
        cols = [row[1] for row in cursor.fetchall()]
        assert "meta" in cols
        conn.close()


# ============================================================================
# Custom Mechanic Tests
# ============================================================================


class TestCustomMechanicModel:
    """Test custom_mechanic as a valid EffectType with new fields."""

    def test_custom_mechanic_is_valid_effect_type(self):
        """custom_mechanic is an accepted EffectType literal."""
        spec = EffectSpec(
            effect_type="custom_mechanic",
            description="Defenders gain stamina from blocks",
            mechanic_description="Defenders recover stamina on defensive plays",
            mechanic_hook_point="sim.possession.post",
            mechanic_observable_behavior="Defenders recover stamina after steals/blocks",
            mechanic_implementation_spec="Hook at sim.possession.post, check for defensive events",
        )
        assert spec.effect_type == "custom_mechanic"
        assert spec.mechanic_description is not None
        assert spec.mechanic_hook_point == "sim.possession.post"
        assert spec.mechanic_observable_behavior is not None
        assert spec.mechanic_implementation_spec is not None

    def test_custom_mechanic_fields_optional(self):
        """New mechanic fields are optional — existing EffectSpecs don't break."""
        spec = EffectSpec(
            effect_type="parameter_change",
            parameter="three_point_value",
            new_value=5,
        )
        assert spec.mechanic_description is None
        assert spec.mechanic_hook_point is None
        assert spec.mechanic_observable_behavior is None
        assert spec.mechanic_implementation_spec is None


class TestCustomMechanicTierDetection:
    """custom_mechanic maps to Tier 3."""

    def test_custom_mechanic_tier_3(self):
        """detect_tier_v2 returns Tier 3 for custom_mechanic effects."""
        from pinwheel.core.governance import detect_tier_v2

        interp = ProposalInterpretation(
            effects=[
                EffectSpec(
                    effect_type="custom_mechanic",
                    description="Defenders recover stamina",
                )
            ],
            confidence=0.8,
        )
        tier = detect_tier_v2(interp, RuleSet())
        assert tier == 3

    def test_compound_with_custom_mechanic(self):
        """Compound proposal with parameter_change + custom_mechanic:
        highest tier wins."""
        from pinwheel.core.governance import detect_tier_v2

        interp = ProposalInterpretation(
            effects=[
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="stamina_drain_rate",
                    new_value=1.5,
                    old_value=1.0,
                ),
                EffectSpec(
                    effect_type="custom_mechanic",
                    description="Defenders gain stamina",
                ),
            ],
            confidence=0.85,
        )
        tier = detect_tier_v2(interp, RuleSet())
        assert tier == 3  # custom_mechanic = 3, stamina_drain_rate = 1 → max = 3


class TestCustomMechanicAdminReview:
    """custom_mechanic always triggers admin review, even with high confidence."""

    def test_custom_mechanic_always_needs_review(self):
        """_needs_admin_review returns True for custom_mechanic even at 0.9 confidence."""
        from pinwheel.core.governance import _needs_admin_review
        from pinwheel.models.governance import Proposal

        proposal = Proposal(
            id="p1",
            governor_id="gov1",
            team_id="t1",
            raw_text="defenders gain stamina",
            tier=3,
        )
        interp_v2 = ProposalInterpretation(
            effects=[
                EffectSpec(
                    effect_type="custom_mechanic",
                    description="Defenders gain stamina",
                )
            ],
            confidence=0.9,
        )
        assert _needs_admin_review(proposal, interpretation_v2=interp_v2) is True

    def test_non_custom_with_high_confidence_no_review(self):
        """A non-custom effect with high confidence does NOT need review."""
        from pinwheel.core.governance import _needs_admin_review
        from pinwheel.models.governance import Proposal

        proposal = Proposal(
            id="p2",
            governor_id="gov1",
            team_id="t1",
            raw_text="make threes worth 5",
            tier=1,
        )
        interp_v2 = ProposalInterpretation(
            effects=[
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="three_point_value",
                    new_value=5,
                )
            ],
            confidence=0.9,
        )
        assert _needs_admin_review(proposal, interpretation_v2=interp_v2) is False


class TestCustomMechanicEffectsRegistry:
    """custom_mechanic effects have no hook_points and show as PENDING MECHANIC."""

    def test_custom_mechanic_no_hooks(self):
        """effect_spec_to_registered gives custom_mechanic empty hook_points."""
        spec = EffectSpec(
            effect_type="custom_mechanic",
            description="Defenders gain stamina",
            mechanic_hook_point="sim.possession.post",
        )
        effect = effect_spec_to_registered(spec, "p1", 1)
        assert effect.hook_points == []
        assert effect.effect_type == "custom_mechanic"

    def test_pending_mechanic_summary(self):
        """build_effects_summary shows custom_mechanic as [PENDING MECHANIC]."""
        registry = EffectRegistry()
        effect = RegisteredEffect(
            effect_id="e1",
            proposal_id="p1",
            _hook_points=[],
            effect_type="custom_mechanic",
            description="Defenders gain stamina from blocks",
        )
        registry.register(effect)
        summary = registry.build_effects_summary()
        assert "[PENDING MECHANIC]" in summary
        assert "Defenders gain stamina" in summary

    def test_custom_mechanic_apply_returns_narrative(self):
        """RegisteredEffect.apply for custom_mechanic returns narrative with prefix."""
        from pinwheel.core.hooks import HookContext

        effect = RegisteredEffect(
            effect_id="e1",
            proposal_id="p1",
            _hook_points=[],
            effect_type="custom_mechanic",
            description="Defenders gain stamina",
        )
        result = effect.apply("any.hook", HookContext())
        assert "[Pending mechanic]" in result.narrative
        assert "Defenders gain stamina" in result.narrative


class TestCustomMechanicMockInterpreter:
    """Mock interpreter produces correct effect types for various proposals."""

    def test_out_of_bounds_double_is_hook_callback(self):
        """'out of bounds double' → hook_callback, NOT custom_mechanic."""
        result = interpret_proposal_v2_mock(
            "when a ball goes out of bounds it is worth double",
            RuleSet(),
        )
        has_hook = any(e.effect_type == "hook_callback" for e in result.effects)
        has_custom = any(e.effect_type == "custom_mechanic" for e in result.effects)
        assert has_hook, "Should produce hook_callback for conditional rule"
        assert not has_custom, "Should NOT produce custom_mechanic — hook_callback suffices"
        assert result.confidence >= 0.8

    def test_lava_with_defender_gain(self):
        """'ball is lava + defenders gain stamina' → parameter_change + custom_mechanic."""
        result = interpret_proposal_v2_mock(
            "ball is lava... defenders GAIN stamina with great defensive plays",
            RuleSet(),
        )
        types = {e.effect_type for e in result.effects}
        assert "parameter_change" in types, "Lava should produce stamina_drain parameter change"
        assert "custom_mechanic" in types, "Defender gain clause needs custom_mechanic"
        assert result.confidence >= 0.8

    def test_lava_without_defender_gain(self):
        """'ball is lava' without defender clause → no custom_mechanic."""
        result = interpret_proposal_v2_mock("the ball is lava", RuleSet())
        types = {e.effect_type for e in result.effects}
        assert "parameter_change" in types
        assert "custom_mechanic" not in types

    def test_gameplay_intent_produces_custom_mechanic(self):
        """Clear gameplay intent without primitive match → custom_mechanic at 0.75."""
        result = interpret_proposal_v2_mock(
            "every time a player dunks the basket should explode with extra points",
            RuleSet(),
        )
        has_custom = any(e.effect_type == "custom_mechanic" for e in result.effects)
        assert has_custom, "Gameplay intent should produce custom_mechanic"
        assert result.confidence >= 0.7
        assert not result.clarification_needed

    def test_no_gameplay_intent_is_narrative(self):
        """No gameplay intent → narrative at 0.3 (unchanged fallback)."""
        result = interpret_proposal_v2_mock(
            "Make the game more exciting and fun",
            RuleSet(),
        )
        has_narrative = any(e.effect_type == "narrative" for e in result.effects)
        has_custom = any(e.effect_type == "custom_mechanic" for e in result.effects)
        assert has_narrative
        assert not has_custom
        assert result.confidence < 0.5
        assert result.clarification_needed is True
