"""Tests for expanded RuleSet parameters, compound proposals, and simulation integration.

Session 64: Multi-parameter interpretation and expanded RuleSet.
Tests cover:
- New RuleSet parameter defaults and validation
- Simulation consumption of new parameters
- Mock interpreter detection of new parameter keywords
- Compound proposal interpretation (multiple effects)
- Compound proposal application via tally_governance_with_effects
"""

import random

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.ai.interpreter import (
    _split_compound_clauses,
    interpret_proposal_mock,
    interpret_proposal_v2_mock,
)
from pinwheel.core.governance import (
    apply_rule_change,
    detect_tier,
    tally_governance_with_effects,
)
from pinwheel.core.possession import (
    attempt_rebound,
    check_foul,
    check_turnover,
    compute_possession_duration,
    drain_stamina,
)
from pinwheel.core.simulation import simulate_game
from pinwheel.core.state import HooperState
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.governance import (
    EffectSpec,
    Proposal,
    RuleInterpretation,
    Vote,
)
from pinwheel.models.rules import DEFAULT_RULESET, RuleSet
from pinwheel.models.team import Hooper, PlayerAttributes, Team, Venue

# --- Helpers ---


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
        hoopers.append(_make_hooper(f"{team_id}-s{i}", team_id, attrs, is_starter=True))
    for i in range(n_bench):
        hoopers.append(_make_hooper(f"{team_id}-b{i}", team_id, attrs, is_starter=False))
    return Team(
        id=team_id,
        name=f"Team-{team_id}",
        venue=Venue(name="Court", capacity=5000),
        hoopers=hoopers,
    )


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


# ============================================================================
# 1. New RuleSet parameter defaults and validation
# ============================================================================


class TestNewRuleSetDefaults:
    """Every new parameter has a sensible default matching current behavior."""

    def test_turnover_rate_modifier_default(self):
        r = RuleSet()
        assert r.turnover_rate_modifier == 1.0

    def test_foul_rate_modifier_default(self):
        r = RuleSet()
        assert r.foul_rate_modifier == 1.0

    def test_offensive_rebound_weight_default(self):
        r = RuleSet()
        assert r.offensive_rebound_weight == 5.0

    def test_stamina_drain_rate_default(self):
        r = RuleSet()
        assert r.stamina_drain_rate == 0.007

    def test_dead_ball_time_seconds_default(self):
        r = RuleSet()
        assert r.dead_ball_time_seconds == 9.0

    def test_backward_compatible_construction(self):
        """RuleSet() with no arguments still works (all new fields have defaults)."""
        r = RuleSet()
        assert r.three_point_value == 3
        assert r.turnover_rate_modifier == 1.0

    def test_default_ruleset_unchanged(self):
        """DEFAULT_RULESET singleton has the correct defaults."""
        assert DEFAULT_RULESET.turnover_rate_modifier == 1.0
        assert DEFAULT_RULESET.foul_rate_modifier == 1.0
        assert DEFAULT_RULESET.offensive_rebound_weight == 5.0
        assert DEFAULT_RULESET.stamina_drain_rate == 0.007
        assert DEFAULT_RULESET.dead_ball_time_seconds == 9.0


class TestNewRuleSetValidation:
    """New parameters respect their defined ranges."""

    def test_turnover_rate_modifier_range(self):
        RuleSet(turnover_rate_modifier=0.2)
        RuleSet(turnover_rate_modifier=3.0)
        with pytest.raises(ValidationError):
            RuleSet(turnover_rate_modifier=0.1)
        with pytest.raises(ValidationError):
            RuleSet(turnover_rate_modifier=3.1)

    def test_foul_rate_modifier_range(self):
        RuleSet(foul_rate_modifier=0.2)
        RuleSet(foul_rate_modifier=3.0)
        with pytest.raises(ValidationError):
            RuleSet(foul_rate_modifier=0.1)
        with pytest.raises(ValidationError):
            RuleSet(foul_rate_modifier=3.1)

    def test_offensive_rebound_weight_range(self):
        RuleSet(offensive_rebound_weight=1.0)
        RuleSet(offensive_rebound_weight=15.0)
        with pytest.raises(ValidationError):
            RuleSet(offensive_rebound_weight=0.5)
        with pytest.raises(ValidationError):
            RuleSet(offensive_rebound_weight=16.0)

    def test_stamina_drain_rate_range(self):
        RuleSet(stamina_drain_rate=0.001)
        RuleSet(stamina_drain_rate=0.03)
        with pytest.raises(ValidationError):
            RuleSet(stamina_drain_rate=0.0005)
        with pytest.raises(ValidationError):
            RuleSet(stamina_drain_rate=0.031)

    def test_dead_ball_time_seconds_range(self):
        RuleSet(dead_ball_time_seconds=2.0)
        RuleSet(dead_ball_time_seconds=20.0)
        with pytest.raises(ValidationError):
            RuleSet(dead_ball_time_seconds=1.9)
        with pytest.raises(ValidationError):
            RuleSet(dead_ball_time_seconds=20.1)


# ============================================================================
# 2. Simulation uses new parameters
# ============================================================================


class TestSimulationUsesNewParams:
    """Verify the simulation actually consumes the new parameters."""

    def test_high_turnover_rate_increases_turnovers(self):
        """turnover_rate_modifier > 1 should increase turnover frequency."""
        home = _make_team("home")
        away = _make_team("away")

        normal = [simulate_game(home, away, DEFAULT_RULESET, seed=s) for s in range(100)]
        high_to = RuleSet(turnover_rate_modifier=2.5)
        high = [simulate_game(home, away, high_to, seed=s) for s in range(100)]

        avg_normal_to = sum(
            sum(bs.turnovers for bs in r.box_scores) for r in normal
        ) / 100
        avg_high_to = sum(
            sum(bs.turnovers for bs in r.box_scores) for r in high
        ) / 100
        assert avg_high_to > avg_normal_to, (
            f"High turnover rate ({avg_high_to}) should exceed normal ({avg_normal_to})"
        )

    def test_low_turnover_rate_decreases_turnovers(self):
        """turnover_rate_modifier < 1 should decrease turnover frequency."""
        home = _make_team("home")
        away = _make_team("away")

        normal = [simulate_game(home, away, DEFAULT_RULESET, seed=s) for s in range(100)]
        low_to = RuleSet(turnover_rate_modifier=0.3)
        low = [simulate_game(home, away, low_to, seed=s) for s in range(100)]

        avg_normal_to = sum(
            sum(bs.turnovers for bs in r.box_scores) for r in normal
        ) / 100
        avg_low_to = sum(
            sum(bs.turnovers for bs in r.box_scores) for r in low
        ) / 100
        assert avg_low_to < avg_normal_to, (
            f"Low turnover rate ({avg_low_to}) should be below normal ({avg_normal_to})"
        )

    def test_high_foul_rate_increases_fouls(self):
        """foul_rate_modifier > 1 should increase foul frequency."""
        home = _make_team("home")
        away = _make_team("away")

        normal = [simulate_game(home, away, DEFAULT_RULESET, seed=s) for s in range(80)]
        high_foul = RuleSet(foul_rate_modifier=2.5)
        high = [simulate_game(home, away, high_foul, seed=s) for s in range(80)]

        avg_normal_fouls = sum(
            sum(bs.fouls for bs in r.box_scores) for r in normal
        ) / 80
        avg_high_fouls = sum(
            sum(bs.fouls for bs in r.box_scores) for r in high
        ) / 80
        assert avg_high_fouls > avg_normal_fouls, (
            f"High foul rate ({avg_high_fouls}) should exceed normal ({avg_normal_fouls})"
        )

    def test_high_offensive_rebound_weight_increases_oreb(self):
        """Higher offensive_rebound_weight should increase offensive rebounds."""
        home = _make_team("home")
        away = _make_team("away")

        normal = [simulate_game(home, away, DEFAULT_RULESET, seed=s) for s in range(100)]
        high_oreb = RuleSet(offensive_rebound_weight=14.0)
        high = [simulate_game(home, away, high_oreb, seed=s) for s in range(100)]

        def count_oreb(results: list) -> int:
            total = 0
            for r in results:
                for p in r.possession_log:
                    if p.is_offensive_rebound:
                        total += 1
            return total

        normal_oreb = count_oreb(normal)
        high_oreb_count = count_oreb(high)
        assert high_oreb_count > normal_oreb, (
            f"High oreb weight ({high_oreb_count}) should exceed normal ({normal_oreb})"
        )

    def test_high_stamina_drain_rate_reduces_scores(self):
        """Higher stamina_drain_rate should fatigue players faster, reducing scores."""
        home = _make_team("home")
        away = _make_team("away")

        normal = [simulate_game(home, away, DEFAULT_RULESET, seed=s) for s in range(80)]
        high_drain = RuleSet(stamina_drain_rate=0.025)
        drained = [simulate_game(home, away, high_drain, seed=s) for s in range(80)]

        avg_normal = sum(r.home_score + r.away_score for r in normal) / 80
        avg_drained = sum(r.home_score + r.away_score for r in drained) / 80
        # More fatigue → worse shooting → lower scores
        assert avg_drained < avg_normal, (
            f"High drain ({avg_drained}) should produce lower scores than normal ({avg_normal})"
        )

    def test_dead_ball_time_affects_possession_count(self):
        """Higher dead_ball_time_seconds means fewer possessions per quarter."""
        home = _make_team("home")
        away = _make_team("away")

        short_dead = RuleSet(dead_ball_time_seconds=2.0)
        long_dead = RuleSet(dead_ball_time_seconds=18.0)

        short_results = [simulate_game(home, away, short_dead, seed=s) for s in range(50)]
        long_results = [simulate_game(home, away, long_dead, seed=s) for s in range(50)]

        avg_short = sum(r.total_possessions for r in short_results) / 50
        avg_long = sum(r.total_possessions for r in long_results) / 50
        assert avg_short > avg_long, (
            f"Short dead time ({avg_short} poss) should exceed long ({avg_long} poss)"
        )


class TestNewParamUnitLevel:
    """Unit-level tests that the parameterized functions accept and use rules."""

    def test_check_turnover_uses_modifier(self):
        """check_turnover with high modifier should produce more turnovers."""
        handler = HooperState(hooper=_make_hooper())
        high_rules = RuleSet(turnover_rate_modifier=3.0)
        low_rules = RuleSet(turnover_rate_modifier=0.2)

        high_to = sum(
            1 for s in range(500)
            if check_turnover(handler, "man_tight", random.Random(s), rules=high_rules)
        )
        low_to = sum(
            1 for s in range(500)
            if check_turnover(handler, "man_tight", random.Random(s), rules=low_rules)
        )
        assert high_to > low_to

    def test_check_foul_uses_modifier(self):
        """check_foul with high modifier should produce more fouls."""
        defender = HooperState(hooper=_make_hooper())
        high_rules = RuleSet(foul_rate_modifier=3.0)
        low_rules = RuleSet(foul_rate_modifier=0.2)

        high_fouls = sum(
            1 for s in range(500)
            if check_foul(defender, "mid_range", "man_tight", random.Random(s), rules=high_rules)
        )
        low_fouls = sum(
            1 for s in range(500)
            if check_foul(defender, "mid_range", "man_tight", random.Random(s), rules=low_rules)
        )
        assert high_fouls > low_fouls

    def test_attempt_rebound_uses_oreb_weight(self):
        """Higher offensive_rebound_weight should increase offensive rebound rate."""
        offense = [HooperState(hooper=_make_hooper(f"o{i}")) for i in range(3)]
        defense = [HooperState(hooper=_make_hooper(f"d{i}", "t-2")) for i in range(3)]

        high_rules = RuleSet(offensive_rebound_weight=14.0)
        low_rules = RuleSet(offensive_rebound_weight=1.0)

        high_oreb = sum(
            1 for s in range(500)
            if attempt_rebound(offense, defense, random.Random(s), rules=high_rules)[1]
        )
        low_oreb = sum(
            1 for s in range(500)
            if attempt_rebound(offense, defense, random.Random(s), rules=low_rules)[1]
        )
        assert high_oreb > low_oreb

    def test_drain_stamina_uses_rate(self):
        """Higher stamina_drain_rate should drain more stamina per possession."""
        agents_high = [HooperState(hooper=_make_hooper(f"h{i}")) for i in range(3)]
        agents_low = [HooperState(hooper=_make_hooper(f"l{i}")) for i in range(3)]

        high_drain_rules = RuleSet(stamina_drain_rate=0.025)
        low_drain_rules = RuleSet(stamina_drain_rate=0.002)

        drain_stamina(agents_high, "man_tight", is_defense=False, rules=high_drain_rules)
        drain_stamina(agents_low, "man_tight", is_defense=False, rules=low_drain_rules)

        avg_high_stamina = sum(a.current_stamina for a in agents_high) / 3
        avg_low_stamina = sum(a.current_stamina for a in agents_low) / 3
        assert avg_low_stamina > avg_high_stamina

    def test_compute_possession_duration_uses_dead_ball_time(self):
        """Dead ball time should affect possession duration."""
        rng = random.Random(42)
        short_rules = RuleSet(dead_ball_time_seconds=2.0)
        duration_short = compute_possession_duration(short_rules, rng)

        rng = random.Random(42)
        long_rules = RuleSet(dead_ball_time_seconds=18.0)
        duration_long = compute_possession_duration(long_rules, rng)

        assert duration_long > duration_short

    def test_backward_compat_no_rules_arg(self):
        """Functions still work when rules=None (backward compat for unit tests)."""
        handler = HooperState(hooper=_make_hooper())
        offense = [handler]
        defense = [HooperState(hooper=_make_hooper("d-1", "t-2"))]

        # All should work without rules
        check_turnover(handler, "zone", random.Random(42))
        check_foul(handler, "at_rim", "zone", random.Random(42))
        attempt_rebound(offense, defense, random.Random(42))
        drain_stamina(offense, "zone", is_defense=False)


# ============================================================================
# 3. Tier detection for new parameters
# ============================================================================


class TestNewParamTierDetection:
    """New parameters should be detected as Tier 1 (game mechanics)."""

    def test_turnover_rate_modifier_is_tier1(self):
        interp = RuleInterpretation(parameter="turnover_rate_modifier", new_value=1.5)
        assert detect_tier(interp, RuleSet()) == 1

    def test_foul_rate_modifier_is_tier1(self):
        interp = RuleInterpretation(parameter="foul_rate_modifier", new_value=0.5)
        assert detect_tier(interp, RuleSet()) == 1

    def test_offensive_rebound_weight_is_tier1(self):
        interp = RuleInterpretation(parameter="offensive_rebound_weight", new_value=8.0)
        assert detect_tier(interp, RuleSet()) == 1

    def test_stamina_drain_rate_is_tier1(self):
        interp = RuleInterpretation(parameter="stamina_drain_rate", new_value=0.01)
        assert detect_tier(interp, RuleSet()) == 1

    def test_dead_ball_time_seconds_is_tier1(self):
        interp = RuleInterpretation(parameter="dead_ball_time_seconds", new_value=12.0)
        assert detect_tier(interp, RuleSet()) == 1


# ============================================================================
# 4. Mock interpreter detects new parameter keywords
# ============================================================================


class TestMockInterpreterNewParams:
    """interpret_proposal_mock detects keywords for new parameters."""

    def test_turnover_rate(self):
        result = interpret_proposal_mock("Set the turnover rate to 1.5", RuleSet())
        assert result.parameter == "turnover_rate_modifier"
        assert result.new_value == 1.5
        assert result.confidence > 0.5

    def test_turnover_modifier(self):
        result = interpret_proposal_mock("Change turnover modifier to 2.0", RuleSet())
        assert result.parameter == "turnover_rate_modifier"
        assert result.new_value == 2.0

    def test_foul_rate(self):
        result = interpret_proposal_mock("Set foul rate to 0.5", RuleSet())
        assert result.parameter == "foul_rate_modifier"
        assert result.new_value == 0.5

    def test_offensive_rebound(self):
        result = interpret_proposal_mock("Increase offensive rebound weight to 10", RuleSet())
        assert result.parameter == "offensive_rebound_weight"
        assert result.new_value == 10.0

    def test_stamina_drain(self):
        result = interpret_proposal_mock("Lower stamina drain to 0.004", RuleSet())
        assert result.parameter == "stamina_drain_rate"
        assert result.new_value == 0.004

    def test_dead_ball_time(self):
        result = interpret_proposal_mock("Set dead ball time to 5 seconds", RuleSet())
        assert result.parameter == "dead_ball_time_seconds"
        assert result.new_value == 5.0

    def test_dead_time(self):
        result = interpret_proposal_mock("Change dead time to 12", RuleSet())
        assert result.parameter == "dead_ball_time_seconds"
        assert result.new_value == 12.0

    def test_substitution_threshold(self):
        result = interpret_proposal_mock("Set the substitution threshold to 0.5", RuleSet())
        assert result.parameter == "substitution_stamina_threshold"
        assert result.new_value == 0.5


# ============================================================================
# 5. Compound proposal interpretation
# ============================================================================


class TestCompoundProposalSplitting:
    """_split_compound_clauses correctly splits compound proposals."""

    def test_split_on_and(self):
        clauses = _split_compound_clauses(
            "Make threes worth 4 and shorten the shot clock to 20"
        )
        assert len(clauses) == 2
        assert "threes worth 4" in clauses[0].lower()
        assert "shot clock" in clauses[1].lower()

    def test_split_on_comma(self):
        clauses = _split_compound_clauses(
            "Set the foul rate to 2, make free throws worth 2"
        )
        assert len(clauses) == 2

    def test_no_split_single_clause(self):
        clauses = _split_compound_clauses("Make three pointers worth 5")
        assert len(clauses) == 1

    def test_three_clauses(self):
        clauses = _split_compound_clauses(
            "Make threes worth 5, set shot clock to 20, and change the foul limit to 3"
        )
        assert len(clauses) == 3


class TestCompoundProposalInterpretation:
    """interpret_proposal_v2_mock produces multiple effects for compound proposals."""

    def test_two_parameter_changes(self):
        result = interpret_proposal_v2_mock(
            "Make three pointers worth 5 and set the shot clock to 20",
            RuleSet(),
        )
        param_effects = [e for e in result.effects if e.effect_type == "parameter_change"]
        assert len(param_effects) == 2
        params = {e.parameter for e in param_effects}
        assert "three_point_value" in params
        assert "shot_clock_seconds" in params
        assert result.confidence > 0.5

    def test_compound_with_comma(self):
        result = interpret_proposal_v2_mock(
            "Set the foul rate to 2, make free throws worth 3",
            RuleSet(),
        )
        param_effects = [e for e in result.effects if e.effect_type == "parameter_change"]
        assert len(param_effects) == 2
        params = {e.parameter for e in param_effects}
        assert "foul_rate_modifier" in params
        assert "free_throw_value" in params

    def test_compound_preserves_values(self):
        result = interpret_proposal_v2_mock(
            "Make three pointers worth 4 and set the turnover rate to 1.5",
            RuleSet(),
        )
        effects_by_param = {e.parameter: e for e in result.effects}
        assert effects_by_param["three_point_value"].new_value == 4
        assert effects_by_param["turnover_rate_modifier"].new_value == 1.5

    def test_single_param_still_works(self):
        """A single-parameter proposal should still produce a single effect."""
        result = interpret_proposal_v2_mock(
            "Make three pointers worth 5",
            RuleSet(),
        )
        assert len(result.effects) == 1
        assert result.effects[0].effect_type == "parameter_change"
        assert result.effects[0].parameter == "three_point_value"

    def test_compound_with_one_unparseable_clause(self):
        """If one clause is unparseable, compound falls through to legacy."""
        result = interpret_proposal_v2_mock(
            "Make three point shots worth 5 and make the game more exciting",
            RuleSet(),
        )
        # The compound path requires >= 2 parsed effects; with only 1 it falls through
        # to the legacy single-param path which should parse "three point ... worth 5"
        param_effects = [e for e in result.effects if e.effect_type == "parameter_change"]
        assert len(param_effects) >= 1

    def test_three_parameter_compound(self):
        """Three parameter changes in one proposal."""
        result = interpret_proposal_v2_mock(
            "Make three pointers worth 5, set the shot clock to 25, and change foul limit to 8",
            RuleSet(),
        )
        param_effects = [e for e in result.effects if e.effect_type == "parameter_change"]
        assert len(param_effects) == 3
        params = {e.parameter for e in param_effects}
        assert "three_point_value" in params
        assert "shot_clock_seconds" in params
        assert "personal_foul_limit" in params

    def test_compound_new_params(self):
        """Compound proposal with new parameters."""
        result = interpret_proposal_v2_mock(
            "Set the turnover rate to 2.0 and increase foul rate to 1.5",
            RuleSet(),
        )
        param_effects = [e for e in result.effects if e.effect_type == "parameter_change"]
        assert len(param_effects) == 2
        params = {e.parameter for e in param_effects}
        assert "turnover_rate_modifier" in params
        assert "foul_rate_modifier" in params


# ============================================================================
# 6. Compound proposals apply via tally_governance_with_effects
# ============================================================================


class TestCompoundProposalTally:
    """tally_governance_with_effects applies multiple parameter changes."""

    async def test_compound_proposal_applies_all_changes(
        self,
        repo: Repository,
        season_id: str,
    ):
        """A compound proposal with two param changes should modify both parameters."""
        proposal = Proposal(
            id="compound-1",
            season_id=season_id,
            governor_id="gov-1",
            team_id="team-1",
            raw_text="Threes worth 5 and shot clock to 20",
            status="confirmed",
            tier=1,
        )

        votes = [
            Vote(proposal_id="compound-1", governor_id="g1", vote="yes", weight=1.0),
            Vote(proposal_id="compound-1", governor_id="g2", vote="yes", weight=1.0),
        ]

        effects_v2 = {
            "compound-1": [
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="three_point_value",
                    new_value=5,
                    old_value=3,
                    description="Threes worth 5",
                ),
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="shot_clock_seconds",
                    new_value=20,
                    old_value=15,
                    description="Shot clock to 20",
                ),
            ],
        }

        new_ruleset, tallies = await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={"compound-1": votes},
            current_ruleset=RuleSet(),
            round_number=1,
            effects_v2_by_proposal=effects_v2,
        )

        assert len(tallies) == 1
        assert tallies[0].passed is True
        assert new_ruleset.three_point_value == 5
        assert new_ruleset.shot_clock_seconds == 20

    async def test_compound_with_new_params(
        self,
        repo: Repository,
        season_id: str,
    ):
        """Compound proposal with new parameters applies correctly."""
        proposal = Proposal(
            id="compound-2",
            season_id=season_id,
            governor_id="gov-1",
            team_id="team-1",
            raw_text="Turnover rate 2.0 and foul rate 0.5",
            status="confirmed",
            tier=1,
        )

        votes = [
            Vote(proposal_id="compound-2", governor_id="g1", vote="yes", weight=1.0),
            Vote(proposal_id="compound-2", governor_id="g2", vote="yes", weight=1.0),
        ]

        effects_v2 = {
            "compound-2": [
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="turnover_rate_modifier",
                    new_value=2.0,
                    old_value=1.0,
                    description="Turnover rate to 2.0",
                ),
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="foul_rate_modifier",
                    new_value=0.5,
                    old_value=1.0,
                    description="Foul rate to 0.5",
                ),
            ],
        }

        new_ruleset, tallies = await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={"compound-2": votes},
            current_ruleset=RuleSet(),
            round_number=1,
            effects_v2_by_proposal=effects_v2,
        )

        assert tallies[0].passed is True
        assert new_ruleset.turnover_rate_modifier == 2.0
        assert new_ruleset.foul_rate_modifier == 0.5

    async def test_failing_compound_proposal_no_change(
        self,
        repo: Repository,
        season_id: str,
    ):
        """A failed compound proposal should not change any parameters."""
        proposal = Proposal(
            id="compound-fail",
            season_id=season_id,
            governor_id="gov-1",
            team_id="team-1",
            raw_text="Threes worth 5 and shot clock to 20",
            status="confirmed",
            tier=1,
        )

        votes = [
            Vote(proposal_id="compound-fail", governor_id="g1", vote="no", weight=1.0),
        ]

        effects_v2 = {
            "compound-fail": [
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="three_point_value",
                    new_value=5,
                    old_value=3,
                ),
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="shot_clock_seconds",
                    new_value=20,
                    old_value=15,
                ),
            ],
        }

        new_ruleset, tallies = await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={"compound-fail": votes},
            current_ruleset=RuleSet(),
            round_number=1,
            effects_v2_by_proposal=effects_v2,
        )

        assert tallies[0].passed is False
        assert new_ruleset.three_point_value == 3  # Unchanged
        assert new_ruleset.shot_clock_seconds == 15  # Unchanged

    async def test_compound_with_invalid_value_partial_apply(
        self,
        repo: Repository,
        season_id: str,
    ):
        """If one effect has an invalid value, the other still applies."""
        proposal = Proposal(
            id="compound-partial",
            season_id=season_id,
            governor_id="gov-1",
            team_id="team-1",
            raw_text="Shot clock to 20 and threes worth 99",
            status="confirmed",
            tier=1,
        )

        votes = [
            Vote(proposal_id="compound-partial", governor_id="g1", vote="yes", weight=1.0),
            Vote(proposal_id="compound-partial", governor_id="g2", vote="yes", weight=1.0),
        ]

        effects_v2 = {
            "compound-partial": [
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="shot_clock_seconds",
                    new_value=20,
                    old_value=15,
                ),
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="three_point_value",
                    new_value=99,  # Out of range (max 10)
                    old_value=3,
                ),
            ],
        }

        new_ruleset, tallies = await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={"compound-partial": votes},
            current_ruleset=RuleSet(),
            round_number=1,
            effects_v2_by_proposal=effects_v2,
        )

        assert tallies[0].passed is True
        assert new_ruleset.shot_clock_seconds == 20  # Valid effect applied
        assert new_ruleset.three_point_value == 3  # Invalid effect rolled back

    async def test_backward_compat_without_v2_effects(
        self,
        repo: Repository,
        season_id: str,
    ):
        """Without effects_v2_by_proposal, falls back to legacy single-param path."""
        interp = RuleInterpretation(parameter="three_point_value", new_value=5, old_value=3)
        proposal = Proposal(
            id="legacy-1",
            season_id=season_id,
            governor_id="gov-1",
            team_id="team-1",
            raw_text="Threes worth 5",
            interpretation=interp,
            status="confirmed",
            tier=1,
        )

        votes = [
            Vote(proposal_id="legacy-1", governor_id="g1", vote="yes", weight=1.0),
            Vote(proposal_id="legacy-1", governor_id="g2", vote="yes", weight=1.0),
        ]

        new_ruleset, tallies = await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={"legacy-1": votes},
            current_ruleset=RuleSet(),
            round_number=1,
            # No effects_v2_by_proposal
        )

        assert tallies[0].passed is True
        assert new_ruleset.three_point_value == 5

    async def test_compound_three_params(
        self,
        repo: Repository,
        season_id: str,
    ):
        """Three parameter changes in a single compound proposal."""
        proposal = Proposal(
            id="compound-3",
            season_id=season_id,
            governor_id="gov-1",
            team_id="team-1",
            raw_text="Triple compound",
            status="confirmed",
            tier=1,
        )

        votes = [
            Vote(proposal_id="compound-3", governor_id="g1", vote="yes", weight=1.0),
            Vote(proposal_id="compound-3", governor_id="g2", vote="yes", weight=1.0),
        ]

        effects_v2 = {
            "compound-3": [
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="three_point_value",
                    new_value=4,
                    old_value=3,
                ),
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="turnover_rate_modifier",
                    new_value=1.5,
                    old_value=1.0,
                ),
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="dead_ball_time_seconds",
                    new_value=5.0,
                    old_value=9.0,
                ),
            ],
        }

        new_ruleset, tallies = await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={"compound-3": votes},
            current_ruleset=RuleSet(),
            round_number=1,
            effects_v2_by_proposal=effects_v2,
        )

        assert tallies[0].passed is True
        assert new_ruleset.three_point_value == 4
        assert new_ruleset.turnover_rate_modifier == 1.5
        assert new_ruleset.dead_ball_time_seconds == 5.0


# ============================================================================
# 7. Rule application for new parameters
# ============================================================================


class TestRuleApplicationNewParams:
    """apply_rule_change works with the new parameters."""

    def test_apply_turnover_rate_modifier(self):
        ruleset = RuleSet()
        interp = RuleInterpretation(
            parameter="turnover_rate_modifier", new_value=2.0, old_value=1.0
        )
        new_ruleset, change = apply_rule_change(ruleset, interp, "p-1", round_enacted=1)
        assert new_ruleset.turnover_rate_modifier == 2.0
        assert change.old_value == 1.0

    def test_apply_foul_rate_modifier(self):
        ruleset = RuleSet()
        interp = RuleInterpretation(
            parameter="foul_rate_modifier", new_value=0.5, old_value=1.0
        )
        new_ruleset, change = apply_rule_change(ruleset, interp, "p-2", round_enacted=1)
        assert new_ruleset.foul_rate_modifier == 0.5

    def test_apply_offensive_rebound_weight(self):
        ruleset = RuleSet()
        interp = RuleInterpretation(
            parameter="offensive_rebound_weight", new_value=10.0, old_value=5.0
        )
        new_ruleset, change = apply_rule_change(ruleset, interp, "p-3", round_enacted=1)
        assert new_ruleset.offensive_rebound_weight == 10.0

    def test_apply_stamina_drain_rate(self):
        ruleset = RuleSet()
        interp = RuleInterpretation(
            parameter="stamina_drain_rate", new_value=0.015, old_value=0.007
        )
        new_ruleset, change = apply_rule_change(ruleset, interp, "p-4", round_enacted=1)
        assert new_ruleset.stamina_drain_rate == 0.015

    def test_apply_dead_ball_time_seconds(self):
        ruleset = RuleSet()
        interp = RuleInterpretation(
            parameter="dead_ball_time_seconds", new_value=12.0, old_value=9.0
        )
        new_ruleset, change = apply_rule_change(ruleset, interp, "p-5", round_enacted=1)
        assert new_ruleset.dead_ball_time_seconds == 12.0

    def test_apply_out_of_range_raises(self):
        ruleset = RuleSet()
        interp = RuleInterpretation(
            parameter="turnover_rate_modifier", new_value=5.0, old_value=1.0
        )
        with pytest.raises(ValidationError):
            apply_rule_change(ruleset, interp, "p-bad", round_enacted=1)


# ============================================================================
# 8. Full game determinism with new params
# ============================================================================


class TestDeterminismNewParams:
    """Same seed + same new-param rules = same result."""

    def test_determinism_with_custom_new_params(self):
        home = _make_team("home")
        away = _make_team("away")
        rules = RuleSet(
            turnover_rate_modifier=1.5,
            foul_rate_modifier=0.8,
            offensive_rebound_weight=8.0,
            stamina_drain_rate=0.01,
            dead_ball_time_seconds=6.0,
        )
        r1 = simulate_game(home, away, rules, seed=42)
        r2 = simulate_game(home, away, rules, seed=42)
        assert r1.home_score == r2.home_score
        assert r1.away_score == r2.away_score
        assert r1.total_possessions == r2.total_possessions
