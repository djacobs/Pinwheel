"""Tests for the structural change path (Phase 5).

GameDefinitionPatch production (mock interpreter), validation (invariants +
smoke sim + cumulative patches), and registration-time defensive rejection.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.ai.interpreter import interpret_proposal_v2_mock
from pinwheel.core.effects import (
    EffectRegistry,
    register_effects_for_proposal,
)
from pinwheel.core.game_def_validation import validate_game_def_patch
from pinwheel.core.governance import _needs_admin_review, detect_tier_v2
from pinwheel.core.simulation import simulate_game
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.governance import (
    EffectSpec,
    Proposal,
)
from pinwheel.models.rules import RuleSet
from pinwheel.models.team import (
    Hooper,
    PlayerAttributes,
    Team,
    Venue,
    suppress_budget_check,
)

# --- Fixtures / helpers ---


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


def _make_team(prefix: str) -> Team:
    with suppress_budget_check():
        attrs = PlayerAttributes(
            scoring=50, passing=40, defense=40, speed=40, stamina=40,
            iq=50, ego=30, chaotic_alignment=20, fate=30,
        )
    return Team(
        id=f"{prefix}-id",
        name=prefix,
        venue=Venue(name=f"{prefix} Arena", capacity=5000),
        hoopers=[
            Hooper.model_construct(
                id=f"{prefix}-{i}",
                name=f"{prefix}-{i}",
                team_id=f"{prefix}-id",
                archetype="sharpshooter",
                backstory="",
                attributes=attrs,
                moves=[],
                is_starter=True,
            )
            for i in range(3)
        ],
    )


_PRAYER_PATCH: dict[str, object] = {
    "add_actions": [
        {
            "name": "the_prayer",
            "display_name": "The Prayer",
            "description": "A desperate half-court heave",
            "selection_weight": 0.15,
            "base_midpoint": 78.0,
            "points_on_success": 4,
            "narration_made": ["{player} answers The Prayer!"],
            "narration_missed": ["{player}'s Prayer goes unanswered."],
        }
    ],
    "description": "Adds The Prayer",
}


# --- Validator ---


class TestValidateGameDefPatch:
    def test_valid_add_action_passes(self) -> None:
        assert validate_game_def_patch(_PRAYER_PATCH, RuleSet()) == []

    def test_valid_structure_change_passes(self) -> None:
        patch = {
            "modify_structure": {"quarters": 6, "elam_trigger_quarter": 6},
            "description": "6 quarters",
        }
        assert validate_game_def_patch(patch, RuleSet()) == []

    def test_malformed_patch_fails_construction(self) -> None:
        violations = validate_game_def_patch(
            {"add_actions": "not a list"}, RuleSet(),
        )
        assert violations
        assert "construct" in violations[0]

    def test_removing_all_shot_actions_rejected(self) -> None:
        patch = {
            "remove_actions": ["at_rim", "mid_range", "three_point"],
            "description": "no shots",
        }
        violations = validate_game_def_patch(patch, RuleSet())
        assert violations
        assert "non-free-throw" in violations[0]

    def test_excessive_points_rejected(self) -> None:
        patch = {
            "modify_actions": {"three_point": {"points_on_success": 100}},
        }
        violations = validate_game_def_patch(patch, RuleSet())
        assert any("points_on_success" in v for v in violations)

    def test_zero_quarters_rejected(self) -> None:
        patch = {"modify_structure": {"quarters": 0}}
        violations = validate_game_def_patch(patch, RuleSet())
        assert any("quarters" in v for v in violations)

    def test_unreachable_elam_trigger_rejected(self) -> None:
        patch = {"modify_structure": {"elam_trigger_quarter": 9}}
        violations = validate_game_def_patch(patch, RuleSet())
        assert any("elam_trigger_quarter" in v for v in violations)

    def test_negative_selection_weight_rejected(self) -> None:
        patch = {
            "modify_actions": {"three_point": {"selection_weight": -1.0}},
        }
        violations = validate_game_def_patch(patch, RuleSet())
        assert any("selection_weight" in v for v in violations)

    def test_degenerate_scoring_caught_by_smoke_sim(self) -> None:
        """Statically legal but absurd: every shot is an easy 25-pointer.
        The smoke sim catches what the invariants can't."""
        patch = {
            "modify_actions": {
                "at_rim": {"points_on_success": 25, "base_midpoint": 1.0},
                "mid_range": {"points_on_success": 25, "base_midpoint": 1.0},
                "three_point": {"points_on_success": 25, "base_midpoint": 1.0},
            },
            "modify_structure": {"elam_ending_enabled": False},
        }
        violations = validate_game_def_patch(patch, RuleSet())
        assert violations
        assert any("degenerate" in v or "Smoke" in v for v in violations)

    def test_cumulative_validation_sees_prior_patches(self) -> None:
        """A patch that is fine alone but breaks the game given an earlier
        active patch must be rejected."""
        prior = {"remove_actions": ["three_point"]}
        new = {"remove_actions": ["at_rim", "mid_range"]}
        # Alone: fine (three_point survives)
        assert validate_game_def_patch(new, RuleSet()) == []
        # After the prior patch: no shot actions left
        violations = validate_game_def_patch(
            new, RuleSet(), existing_patches=[prior],
        )
        assert violations


# --- Mock interpreter production ---


class TestMockStructuralInterpretation:
    def test_add_shot_called_x_worth_n(self) -> None:
        interp = interpret_proposal_v2_mock(
            "Add a shot called The Prayer worth 4 points", RuleSet(),
        )
        assert len(interp.effects) == 1
        effect = interp.effects[0]
        assert effect.effect_type == "modify_game_definition"
        patch = effect.game_def_patch or {}
        adds = patch.get("add_actions", [])
        assert len(adds) == 1
        assert adds[0]["name"] == "the_prayer"
        assert adds[0]["points_on_success"] == 4
        # The produced patch is valid end-to-end
        assert validate_game_def_patch(patch, RuleSet()) == []

    def test_quarters_and_no_elam(self) -> None:
        interp = interpret_proposal_v2_mock(
            "Games are 6 quarters and no Elam Ending", RuleSet(),
        )
        assert len(interp.effects) == 1
        effect = interp.effects[0]
        assert effect.effect_type == "modify_game_definition"
        structure = (effect.game_def_patch or {}).get("modify_structure", {})
        assert structure.get("quarters") == 6
        assert structure.get("elam_ending_enabled") is False
        assert validate_game_def_patch(
            effect.game_def_patch or {}, RuleSet(),
        ) == []

    def test_structural_proposals_are_admin_reviewed(self) -> None:
        interp = interpret_proposal_v2_mock(
            "Add a shot called The Prayer worth 4 points", RuleSet(),
        )
        proposal = Proposal(
            id="p-1",
            governor_id="g-1",
            team_id="t-1",
            season_id="s-1",
            window_id="",
            raw_text="Add a shot called The Prayer worth 4 points",
            tier=detect_tier_v2(interp, RuleSet()),
        )
        assert _needs_admin_review(proposal, interpretation_v2=interp)


# --- Registration-time defense ---


class TestRegistrationValidation:
    async def test_valid_patch_registers(
        self, repo: Repository, season_id: str,
    ) -> None:
        registry = EffectRegistry()
        spec = EffectSpec(
            effect_type="modify_game_definition",
            game_def_patch=_PRAYER_PATCH,
            description="Adds The Prayer",
        )
        registered = await register_effects_for_proposal(
            repo, registry, "p-1", [spec], season_id, current_round=1,
        )
        assert len(registered) == 1
        assert registry.count == 1

    async def test_invalid_patch_rejected_with_event(
        self, repo: Repository, season_id: str,
    ) -> None:
        registry = EffectRegistry()
        spec = EffectSpec(
            effect_type="modify_game_definition",
            game_def_patch={
                "remove_actions": ["at_rim", "mid_range", "three_point"],
            },
            description="Removes all shots",
        )
        registered = await register_effects_for_proposal(
            repo, registry, "p-1", [spec], season_id, current_round=1,
        )
        assert registered == []
        assert registry.count == 0
        rejected = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["effect.patch_rejected"],
        )
        assert len(rejected) == 1
        assert rejected[0].payload.get("violations")


# --- End-to-end: the new action appears in real games ---


class TestStructuralChangeEndToEnd:
    def test_new_action_appears_in_play_by_play(self) -> None:
        from pinwheel.core.hooks import RegisteredEffect

        # Dominant weight so the new action reliably appears
        patch = {
            "add_actions": [
                {
                    "name": "the_prayer",
                    "display_name": "The Prayer",
                    "description": "Half-court heave",
                    "selection_weight": 5.0,
                    "base_midpoint": 50.0,
                    "points_on_success": 4,
                }
            ],
        }
        effect = RegisteredEffect(
            effect_id="e-prayer",
            proposal_id="p-prayer",
            _hook_points=["sim.game_definition.patch"],
            effect_type="modify_game_definition",
            action_code={"type": "game_def_patch", "patch": patch},
        )
        result = simulate_game(
            _make_team("home"), _make_team("away"), RuleSet(), seed=7,
            effect_registry=[effect],
        )
        actions_used = {p.action for p in result.possession_log}
        assert "the_prayer" in actions_used
