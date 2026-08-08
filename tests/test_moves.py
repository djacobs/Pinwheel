"""Tests for the move system: structured governed-move modifiers, the
free-text effect parser, and move-grant name resolution."""

import random

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.core.governance import _enact_move_grant
from pinwheel.core.moves import (
    HEAT_CHECK,
    apply_move_modifier,
    apply_move_secondary_effects,
    parse_move_effect_text,
    passive_turnover_modifier,
)
from pinwheel.core.state import HooperState
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.governance import EffectSpec
from pinwheel.models.rules import RuleSet
from pinwheel.models.team import (
    Hooper,
    Move,
    PlayerAttributes,
    Team,
    Venue,
    suppress_budget_check,
)


def _make_hooper(hooper_id: str = "h1", team_id: str = "t1", **moves_kwargs) -> Hooper:
    with suppress_budget_check():
        return Hooper(
            id=hooper_id,
            name=f"Hooper {hooper_id}",
            team_id=team_id,
            archetype="balanced",
            attributes=PlayerAttributes(
                scoring=50, passing=50, defense=50, speed=50,
                iq=50, stamina=50, ego=50, chaotic_alignment=50, fate=50,
            ),
            **moves_kwargs,
        )


def _governed_move(**kwargs) -> Move:
    defaults = {
        "name": "Skyhook",
        "trigger": "any_possession",
        "effect": "a governed move",
        "source": "governed",
    }
    defaults.update(kwargs)
    return Move(**defaults)


# ============================================================================
# Free-text effect parser
# ============================================================================


class TestParseMoveEffectText:
    def test_percent_with_zone(self):
        parsed = parse_move_effect_text("+12% mid-range")
        assert parsed == {
            "modifier_kind": "shot_probability",
            "magnitude": pytest.approx(0.12),
            "applicable_action": "mid_range",
        }

    def test_at_rim_zone(self):
        parsed = parse_move_effect_text("+15% at-rim, chance to freeze defender")
        assert parsed is not None
        assert parsed["modifier_kind"] == "shot_probability"
        assert parsed["magnitude"] == pytest.approx(0.15)
        assert parsed["applicable_action"] == "at_rim"

    def test_all_shots_maps_to_any(self):
        parsed = parse_move_effect_text("+20% all shots, ignore stamina modifier")
        assert parsed is not None
        assert parsed["modifier_kind"] == "shot_probability"
        assert parsed["magnitude"] == pytest.approx(0.20)
        assert parsed["applicable_action"] == "any"

    def test_reduces_turnovers(self):
        parsed = parse_move_effect_text("reduces turnovers by 10%")
        assert parsed == {
            "modifier_kind": "turnover_rate",
            "magnitude": pytest.approx(-0.10),
        }

    def test_cuts_turnovers(self):
        parsed = parse_move_effect_text("cuts turnovers by 8%")
        assert parsed is not None
        assert parsed["modifier_kind"] == "turnover_rate"
        assert parsed["magnitude"] == pytest.approx(-0.08)

    def test_explicit_negative_percent(self):
        parsed = parse_move_effect_text("-10% three point shooting for the defender")
        assert parsed is not None
        assert parsed["modifier_kind"] == "shot_probability"
        assert parsed["magnitude"] == pytest.approx(-0.10)
        assert parsed["applicable_action"] == "three_point"

    def test_stamina_restore(self):
        parsed = parse_move_effect_text("restores 10% stamina on activation")
        assert parsed == {
            "modifier_kind": "stamina",
            "magnitude": pytest.approx(0.10),
        }

    def test_stamina_drain_reduction(self):
        parsed = parse_move_effect_text("reduces stamina drain by 5%")
        assert parsed is not None
        assert parsed["modifier_kind"] == "stamina"
        assert parsed["magnitude"] == pytest.approx(0.05)

    def test_bonus_points(self):
        parsed = parse_move_effect_text("made shots are worth +2 bonus points")
        assert parsed == {"modifier_kind": "shot_value", "magnitude": pytest.approx(2.0)}

    def test_unparseable_returns_none(self):
        assert parse_move_effect_text("an unstoppable aura of confidence") is None

    def test_empty_returns_none(self):
        assert parse_move_effect_text("") is None


# ============================================================================
# Generic application in apply_move_modifier
# ============================================================================


class TestApplyMoveModifierStructured:
    def test_shot_probability_any_action(self):
        move = _governed_move(modifier_kind="shot_probability", magnitude=0.2)
        rng = random.Random(1)
        assert apply_move_modifier(move, 0.5, rng, action="mid_range") == pytest.approx(0.7)

    def test_shot_probability_action_gate(self):
        move = _governed_move(
            modifier_kind="shot_probability", magnitude=0.2,
            applicable_action="mid_range",
        )
        rng = random.Random(1)
        # Matching action gets the boost
        assert apply_move_modifier(move, 0.5, rng, action="mid_range") == pytest.approx(0.7)
        # Non-matching action is untouched
        assert apply_move_modifier(move, 0.5, rng, action="three_point") == pytest.approx(0.5)

    def test_magnitude_clamped(self):
        move = _governed_move(modifier_kind="shot_probability", magnitude=0.9)
        rng = random.Random(1)
        assert apply_move_modifier(move, 0.5, rng) == pytest.approx(0.8)  # +0.30 max

    def test_negative_magnitude_clamped(self):
        move = _governed_move(modifier_kind="shot_probability", magnitude=-0.9)
        rng = random.Random(1)
        assert apply_move_modifier(move, 0.5, rng) == pytest.approx(0.2)  # -0.30 max

    def test_move_without_structured_fields_is_noop(self):
        move = _governed_move()  # legacy cosmetic move
        rng = random.Random(1)
        assert apply_move_modifier(move, 0.5, rng, action="mid_range") == pytest.approx(0.5)

    def test_archetype_move_behavior_unchanged(self):
        rng = random.Random(1)
        assert apply_move_modifier(HEAT_CHECK, 0.5, rng) == pytest.approx(0.65)

    def test_non_probability_kinds_leave_probability_unchanged(self):
        rng = random.Random(1)
        for kind in ("turnover_rate", "stamina", "shot_value"):
            move = _governed_move(modifier_kind=kind, magnitude=0.2)
            assert apply_move_modifier(move, 0.5, rng) == pytest.approx(0.5)


class TestPassiveTurnoverModifier:
    def test_reduces_turnover_rate(self):
        hooper = _make_hooper(moves=[
            _governed_move(name="Safe Hands", modifier_kind="turnover_rate", magnitude=-0.10),
        ])
        agent = HooperState(hooper=hooper)
        assert passive_turnover_modifier(agent) == pytest.approx(-0.10)

    def test_gate_blocks_modifier(self):
        move = _governed_move(
            name="Safe Hands", modifier_kind="turnover_rate", magnitude=-0.10,
            attribute_gate={"iq": 99},
        )
        agent = HooperState(hooper=_make_hooper(moves=[move]))
        assert passive_turnover_modifier(agent) == 0.0

    def test_total_clamped(self):
        moves = [
            _governed_move(name=f"m{i}", modifier_kind="turnover_rate", magnitude=-0.10)
            for i in range(4)
        ]
        agent = HooperState(hooper=_make_hooper(moves=moves))
        assert passive_turnover_modifier(agent) == pytest.approx(-0.15)

    def test_no_moves(self):
        agent = HooperState(hooper=_make_hooper())
        assert passive_turnover_modifier(agent) == 0.0


class TestSecondaryEffects:
    def test_stamina_restore(self):
        move = _governed_move(modifier_kind="stamina", magnitude=0.10)
        agent = HooperState(hooper=_make_hooper())
        agent.current_stamina = 0.5
        bonus = apply_move_secondary_effects(move, agent)
        assert bonus == 0
        assert agent.current_stamina == pytest.approx(0.6)

    def test_stamina_restore_clamped_at_one(self):
        move = _governed_move(modifier_kind="stamina", magnitude=0.25)
        agent = HooperState(hooper=_make_hooper())
        agent.current_stamina = 0.9
        apply_move_secondary_effects(move, agent)
        assert agent.current_stamina == pytest.approx(1.0)

    def test_shot_value_bonus(self):
        move = _governed_move(modifier_kind="shot_value", magnitude=2.0)
        agent = HooperState(hooper=_make_hooper())
        assert apply_move_secondary_effects(move, agent) == 2

    def test_shot_value_clamped(self):
        move = _governed_move(modifier_kind="shot_value", magnitude=10.0)
        agent = HooperState(hooper=_make_hooper())
        assert apply_move_secondary_effects(move, agent) == 3

    def test_shot_probability_kind_returns_zero(self):
        move = _governed_move(modifier_kind="shot_probability", magnitude=0.2)
        agent = HooperState(hooper=_make_hooper())
        assert apply_move_secondary_effects(move, agent) == 0


# ============================================================================
# Granted governed move changes game outcomes (same-seed comparison)
# ============================================================================


def _make_team(tid: str, name: str, moves: list[Move] | None = None) -> Team:
    with suppress_budget_check():
        hoopers = []
        for i in range(4):
            hoopers.append(Hooper(
                id=f"{tid}-h{i}", name=f"{name} {i}", team_id=tid,
                archetype="balanced", is_starter=i < 3,
                attributes=PlayerAttributes(
                    scoring=50, passing=50, defense=50, speed=50,
                    iq=50, stamina=50, ego=50, chaotic_alignment=50, fate=50,
                ),
                moves=list(moves or []),
            ))
        return Team(id=tid, name=name, hoopers=hoopers,
                    venue=Venue(name="Arena", capacity=5000))


class TestGovernedMoveAffectsOutcomes:
    def test_granted_move_changes_shot_outcomes_same_seed(self):
        """A non-archetype governed move with structured modifiers changes
        game outcomes under a fixed seed (mirrors the effect-modifier test
        in tests/test_effects.py)."""
        from pinwheel.core.simulation import simulate_game

        rules = RuleSet()
        home_plain = _make_team("h", "Home")
        away = _make_team("a", "Away")
        baseline = simulate_game(home_plain, away, rules, seed=123)

        boost = _governed_move(
            name="Skyhook",
            trigger="any_possession",
            modifier_kind="shot_probability",
            magnitude=0.30,
            applicable_action="any",
        )
        home_boosted = _make_team("h", "Home", moves=[boost])
        boosted = simulate_game(home_boosted, away, rules, seed=123)

        # The governed move must genuinely change outcomes: the boosted home
        # team scores more than in the identical-seed baseline game.
        assert boosted.home_score > baseline.home_score

    def test_cosmetic_governed_move_does_not_change_probability_path(self):
        """A governed move without structured fields still activates but does
        not modify shot probability (legacy behavior preserved)."""
        move = _governed_move(name="Vibes")
        rng_a = random.Random(7)
        rng_b = random.Random(7)
        assert apply_move_modifier(move, 0.42, rng_a) == pytest.approx(0.42)
        # No RNG consumed for cosmetic moves
        assert rng_a.random() == rng_b.random()


# ============================================================================
# Move grant enactment: structured fields, parser fallback, name resolution
# ============================================================================


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


async def _seed_league(repo: Repository):
    league = await repo.create_league("L")
    season = await repo.create_season(league.id, "S1")
    team_a = await repo.create_team(season.id, "Sunset Sirens")
    team_b = await repo.create_team(season.id, "Midnight Owls")
    attrs = {
        "scoring": 50, "passing": 50, "defense": 50, "speed": 50,
        "iq": 50, "stamina": 50, "ego": 50, "chaotic_alignment": 50, "fate": 50,
    }
    h1 = await repo.create_hooper(team_a.id, season.id, "Sunny Day", "balanced", attrs)
    h2 = await repo.create_hooper(team_a.id, season.id, "Ray Beam", "balanced", attrs)
    h3 = await repo.create_hooper(team_b.id, season.id, "Night Hawk", "balanced", attrs)
    return season, team_a, team_b, h1, h2, h3


class TestEnactMoveGrantResolution:
    async def test_resolves_hooper_by_exact_name(self, repo: Repository):
        season, _team_a, _team_b, h1, _h2, _h3 = await _seed_league(repo)
        effect = EffectSpec(
            effect_type="move_grant",
            move_name="Skyhook",
            move_effect="+12% mid-range",
            target_hooper_id="Sunny Day",  # NAME, not ID
        )
        granted = await _enact_move_grant(repo, season.id, effect)
        assert granted == [h1.id]
        row = await repo.get_hooper(h1.id)
        assert row is not None
        assert row.moves and row.moves[0]["name"] == "Skyhook"

    async def test_resolves_hooper_case_insensitive(self, repo: Repository):
        season, _ta, _tb, h1, _h2, _h3 = await _seed_league(repo)
        effect = EffectSpec(
            effect_type="move_grant",
            move_name="Skyhook",
            target_hooper_id="sunny day",
        )
        granted = await _enact_move_grant(repo, season.id, effect)
        assert granted == [h1.id]

    async def test_resolves_hooper_by_id_first(self, repo: Repository):
        season, _ta, _tb, h1, _h2, _h3 = await _seed_league(repo)
        effect = EffectSpec(
            effect_type="move_grant",
            move_name="Skyhook",
            target_hooper_id=h1.id,
        )
        granted = await _enact_move_grant(repo, season.id, effect)
        assert granted == [h1.id]

    async def test_resolves_team_by_name(self, repo: Repository):
        season, team_a, _tb, h1, h2, _h3 = await _seed_league(repo)
        effect = EffectSpec(
            effect_type="move_grant",
            move_name="Skyhook",
            target_team_id="Sunset Sirens",  # NAME, not ID
        )
        granted = await _enact_move_grant(repo, season.id, effect)
        assert set(granted) == {h1.id, h2.id}

    async def test_unresolved_hooper_falls_back_to_team(self, repo: Repository):
        season, _ta, _tb, h1, h2, _h3 = await _seed_league(repo)
        effect = EffectSpec(
            effect_type="move_grant",
            move_name="Skyhook",
            target_hooper_id="Nobody Real",
            target_team_id="Sunset Sirens",
        )
        granted = await _enact_move_grant(repo, season.id, effect)
        assert set(granted) == {h1.id, h2.id}

    async def test_unresolved_hooper_without_team_skips_with_warning(
        self, repo: Repository
    ):
        season, *_ = await _seed_league(repo)
        effect = EffectSpec(
            effect_type="move_grant",
            move_name="Skyhook",
            target_hooper_id="Nobody Real",
        )
        granted = await _enact_move_grant(repo, season.id, effect)
        assert granted == []
        events = await repo.get_events_by_type(
            season.id, ["effect.move_grant_target_unresolved"]
        )
        assert len(events) >= 1

    async def test_unresolved_team_skips_with_warning(self, repo: Repository):
        season, *_ = await _seed_league(repo)
        effect = EffectSpec(
            effect_type="move_grant",
            move_name="Skyhook",
            target_team_id="No Such Team",
        )
        granted = await _enact_move_grant(repo, season.id, effect)
        assert granted == []
        events = await repo.get_events_by_type(
            season.id, ["effect.move_grant_target_unresolved"]
        )
        assert len(events) >= 1

    async def test_target_selector_all(self, repo: Repository):
        season, _ta, _tb, h1, h2, h3 = await _seed_league(repo)
        effect = EffectSpec(
            effect_type="move_grant",
            move_name="Skyhook",
            target_selector="all",
        )
        granted = await _enact_move_grant(repo, season.id, effect)
        assert set(granted) == {h1.id, h2.id, h3.id}

    async def test_structured_fields_carried_onto_move(self, repo: Repository):
        season, _ta, _tb, h1, _h2, _h3 = await _seed_league(repo)
        effect = EffectSpec(
            effect_type="move_grant",
            move_name="Skyhook",
            move_effect="a sweeping hook shot",
            move_modifier_kind="shot_probability",
            move_magnitude=0.12,
            move_applicable_action="mid_range",
            target_hooper_id=h1.id,
        )
        await _enact_move_grant(repo, season.id, effect)
        row = await repo.get_hooper(h1.id)
        assert row is not None
        stored = row.moves[0]
        assert stored["modifier_kind"] == "shot_probability"
        assert stored["magnitude"] == pytest.approx(0.12)
        assert stored["applicable_action"] == "mid_range"
        assert stored["source"] == "governed"

    async def test_free_text_fallback_parses_effect(self, repo: Repository):
        season, _ta, _tb, h1, _h2, _h3 = await _seed_league(repo)
        effect = EffectSpec(
            effect_type="move_grant",
            move_name="Skyhook",
            move_effect="+12% mid-range, unblockable release",
            target_hooper_id=h1.id,
        )
        await _enact_move_grant(repo, season.id, effect)
        row = await repo.get_hooper(h1.id)
        assert row is not None
        stored = row.moves[0]
        assert stored["modifier_kind"] == "shot_probability"
        assert stored["magnitude"] == pytest.approx(0.12)
        assert stored["applicable_action"] == "mid_range"

    async def test_invalid_kind_falls_back_to_parser(self, repo: Repository):
        season, _ta, _tb, h1, _h2, _h3 = await _seed_league(repo)
        effect = EffectSpec(
            effect_type="move_grant",
            move_name="Skyhook",
            move_effect="reduces turnovers by 10%",
            move_modifier_kind="banana",  # invalid — must not crash the tally
            target_hooper_id=h1.id,
        )
        granted = await _enact_move_grant(repo, season.id, effect)
        assert granted == [h1.id]
        row = await repo.get_hooper(h1.id)
        assert row is not None
        stored = row.moves[0]
        assert stored["modifier_kind"] == "turnover_rate"
        assert stored["magnitude"] == pytest.approx(-0.10)

    async def test_deduplicates_existing_move(self, repo: Repository):
        season, _ta, _tb, h1, _h2, _h3 = await _seed_league(repo)
        effect = EffectSpec(
            effect_type="move_grant",
            move_name="Skyhook",
            target_hooper_id=h1.id,
        )
        first = await _enact_move_grant(repo, season.id, effect)
        second = await _enact_move_grant(repo, season.id, effect)
        assert first == [h1.id]
        assert second == []
