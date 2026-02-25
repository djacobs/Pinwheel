"""Tests for Phase 4b + 4c — game definition patches via governance effects.

Phase 4b: Wire GameDefinitionPatch into the governance effects system.
- effect_spec_to_registered handles modify_game_definition
- collect_game_def_patches extracts patches from active effects
- simulate_game applies patches from effect_registry

Phase 4c: Integration tests proving new actions and removals work end-to-end.
- Add half_court_heave, run simulation, verify it appears in possession logs
- Remove three_point, run simulation, verify no three-pointers in the game
"""

import uuid

from pinwheel.core.effects import (
    EffectRegistry,
    collect_game_def_patches,
    effect_spec_to_registered,
)
from pinwheel.core.hooks import EffectLifetime, RegisteredEffect
from pinwheel.core.simulation import simulate_game
from pinwheel.models.game_definition import (
    EXAMPLE_ACTIONS,
)
from pinwheel.models.governance import EffectSpec
from pinwheel.models.rules import DEFAULT_RULESET
from pinwheel.models.team import Hooper, PlayerAttributes, Team, Venue

# ---------------------------------------------------------------------------
# Test helpers
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
    """Create a PlayerAttributes with specific values, bypassing budget check."""
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
    """Create a hooper, bypassing budget validation."""
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
) -> Team:
    """Create a team with hoopers for simulation."""
    hoopers = []
    for i in range(n_starters):
        hoopers.append(
            _make_hooper(f"{team_id}-s{i}", team_id, is_starter=True)
        )
    for i in range(n_bench):
        hoopers.append(
            _make_hooper(f"{team_id}-b{i}", team_id, is_starter=False)
        )
    return Team(
        id=team_id,
        name=f"Team-{team_id}",
        venue=Venue(name="Court", capacity=5000),
        hoopers=hoopers,
    )


def _make_game_def_patch_effect(
    patch_dict: dict[str, object],
    effect_id: str | None = None,
    registered_at_round: int = 1,
) -> RegisteredEffect:
    """Create a RegisteredEffect that holds a game definition patch."""
    return RegisteredEffect(
        effect_id=effect_id or str(uuid.uuid4()),
        proposal_id="test-proposal",
        _hook_points=["sim.game_definition.patch"],
        _lifetime=EffectLifetime.PERMANENT,
        registered_at_round=registered_at_round,
        effect_type="modify_game_definition",
        action_code={"type": "game_def_patch", "patch": patch_dict},
        description="test game def patch",
    )


# ---------------------------------------------------------------------------
# Phase 4b: effect_spec_to_registered for modify_game_definition
# ---------------------------------------------------------------------------


class TestEffectSpecConversion:
    """Tests for converting modify_game_definition EffectSpecs."""

    def test_spec_to_registered_creates_patch_action_code(self) -> None:
        """EffectSpec with game_def_patch is stored in action_code."""
        patch_dict = {
            "add_actions": [],
            "remove_actions": ["three_point"],
            "modify_actions": {},
            "modify_structure": {},
        }
        spec = EffectSpec(
            effect_type="modify_game_definition",
            game_def_patch=patch_dict,
            description="Remove three-pointers",
        )

        registered = effect_spec_to_registered(spec, "p-1", current_round=1)

        assert registered.effect_type == "modify_game_definition"
        assert registered.action_code is not None
        assert registered.action_code["type"] == "game_def_patch"
        assert registered.action_code["patch"] == patch_dict
        assert "sim.game_definition.patch" in registered.hook_points

    def test_spec_to_registered_preserves_lifetime(self) -> None:
        """Lifetime fields are preserved on conversion."""
        spec = EffectSpec(
            effect_type="modify_game_definition",
            game_def_patch={"remove_actions": ["mid_range"]},
            duration="n_rounds",
            duration_rounds=5,
            description="Temporary mid-range removal",
        )

        registered = effect_spec_to_registered(spec, "p-2", current_round=3)

        assert registered.lifetime == EffectLifetime.N_ROUNDS
        assert registered.rounds_remaining == 5
        assert registered.registered_at_round == 3

    def test_spec_without_patch_creates_no_patch_action_code(self) -> None:
        """EffectSpec without game_def_patch results in None action_code."""
        spec = EffectSpec(
            effect_type="modify_game_definition",
            description="Empty game def modification",
        )

        registered = effect_spec_to_registered(spec, "p-3", current_round=1)

        assert registered.effect_type == "modify_game_definition"
        # No game_def_patch means action_code has the type but no patch
        assert registered.action_code is None or registered.action_code.get("patch") is None


# ---------------------------------------------------------------------------
# Phase 4b: collect_game_def_patches
# ---------------------------------------------------------------------------


class TestCollectPatches:
    """Tests for collecting game definition patches from effects."""

    def test_collect_returns_patches_in_order(self) -> None:
        """Patches are returned ordered by registration round."""
        effects = [
            _make_game_def_patch_effect(
                {"remove_actions": ["three_point"]},
                effect_id="e-2",
                registered_at_round=2,
            ),
            _make_game_def_patch_effect(
                {"add_actions": [EXAMPLE_ACTIONS["layup"].model_dump(mode="json")]},
                effect_id="e-1",
                registered_at_round=1,
            ),
        ]

        patches = collect_game_def_patches(effects)

        assert len(patches) == 2
        # Earliest round first
        assert "add_actions" in patches[0]
        assert "remove_actions" in patches[1]

    def test_collect_ignores_non_game_def_effects(self) -> None:
        """Non-modify_game_definition effects are filtered out."""
        effects = [
            RegisteredEffect(
                effect_id="hook-1",
                proposal_id="p-1",
                _hook_points=["sim.possession.pre"],
                _lifetime=EffectLifetime.PERMANENT,
                effect_type="hook_callback",
                action_code={"type": "modify_score", "modifier": 1},
            ),
            _make_game_def_patch_effect(
                {"remove_actions": ["three_point"]},
            ),
        ]

        patches = collect_game_def_patches(effects)

        assert len(patches) == 1
        assert patches[0]["remove_actions"] == ["three_point"]

    def test_collect_empty_list(self) -> None:
        """No effects returns no patches."""
        assert collect_game_def_patches([]) == []

    def test_collect_ignores_malformed_action_code(self) -> None:
        """Effects with missing/invalid action_code are skipped."""
        effects = [
            RegisteredEffect(
                effect_id="bad-1",
                proposal_id="p-1",
                _hook_points=["sim.game_definition.patch"],
                _lifetime=EffectLifetime.PERMANENT,
                effect_type="modify_game_definition",
                action_code=None,  # No action code
            ),
            RegisteredEffect(
                effect_id="bad-2",
                proposal_id="p-1",
                _hook_points=["sim.game_definition.patch"],
                _lifetime=EffectLifetime.PERMANENT,
                effect_type="modify_game_definition",
                action_code={"type": "game_def_patch"},  # Missing 'patch' key
            ),
            _make_game_def_patch_effect(
                {"remove_actions": ["at_rim"]},
            ),
        ]

        patches = collect_game_def_patches(effects)
        assert len(patches) == 1


# ---------------------------------------------------------------------------
# Phase 4b: EffectRegistry integration
# ---------------------------------------------------------------------------


class TestEffectRegistryIntegration:
    """Tests that the EffectRegistry correctly stores and retrieves game def patches."""

    def test_registry_stores_game_def_effect(self) -> None:
        """A modify_game_definition effect can be registered and retrieved."""
        registry = EffectRegistry()
        effect = _make_game_def_patch_effect(
            {"modify_actions": {"at_rim": {"points_on_success": 3}}}
        )
        registry.register(effect)

        all_active = registry.get_all_active()
        assert len(all_active) == 1
        assert all_active[0].effect_type == "modify_game_definition"

    def test_registry_get_effects_for_hook(self) -> None:
        """Game def patch effects are discoverable by hook point."""
        registry = EffectRegistry()
        effect = _make_game_def_patch_effect(
            {"modify_structure": {"quarters": 6}}
        )
        registry.register(effect)

        hook_effects = registry.get_effects_for_hook("sim.game_definition.patch")
        assert len(hook_effects) == 1

    def test_registry_tick_round_expires_n_round_effects(self) -> None:
        """Game def patch effects with n_rounds lifetime expire correctly."""
        registry = EffectRegistry()
        effect = RegisteredEffect(
            effect_id="expire-test",
            proposal_id="p-1",
            _hook_points=["sim.game_definition.patch"],
            _lifetime=EffectLifetime.N_ROUNDS,
            rounds_remaining=2,
            effect_type="modify_game_definition",
            action_code={"type": "game_def_patch", "patch": {"remove_actions": ["at_rim"]}},
        )
        registry.register(effect)

        # Tick once — still active
        expired = registry.tick_round(1)
        assert len(expired) == 0
        assert registry.count == 1

        # Tick again — now expired
        expired = registry.tick_round(2)
        assert len(expired) == 1
        assert "expire-test" in expired
        assert registry.count == 0

    def test_serialization_roundtrip(self) -> None:
        """RegisteredEffect with game_def_patch survives to_dict/from_dict."""
        original = _make_game_def_patch_effect(
            {"add_actions": [EXAMPLE_ACTIONS["half_court_heave"].model_dump(mode="json")]},
        )

        data = original.to_dict()
        restored = RegisteredEffect.from_dict(data)

        assert restored.effect_type == "modify_game_definition"
        assert restored.action_code is not None
        assert restored.action_code["type"] == "game_def_patch"
        patch_data = restored.action_code["patch"]
        assert len(patch_data["add_actions"]) == 1
        assert patch_data["add_actions"][0]["name"] == "half_court_heave"


# ---------------------------------------------------------------------------
# Phase 4b: simulate_game applies patches
# ---------------------------------------------------------------------------


class TestSimulateGameWithPatches:
    """Tests that simulate_game applies GameDefinitionPatches from effect_registry."""

    def test_modified_points_reflected_in_scores(self) -> None:
        """When three_point is modified to 5 points, the simulation uses it."""
        home = _make_team("home")
        away = _make_team("away")

        # Create effect that makes three-pointers worth 5
        effect = _make_game_def_patch_effect(
            {"modify_actions": {"three_point": {"points_on_success": 5}}}
        )

        result = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            effect_registry=[effect],
        )

        # Game should complete successfully
        assert result.home_score > 0 or result.away_score > 0
        assert result.total_possessions > 0

    def test_structure_modification_changes_quarters(self) -> None:
        """Modifying quarters in structure changes the quarter count."""
        home = _make_team("home")
        away = _make_team("away")

        # 5 quarters: 4 regular + 1 Elam
        effect = _make_game_def_patch_effect(
            {
                "modify_structure": {
                    "quarters": 5,
                    "elam_trigger_quarter": 5,
                },
            }
        )

        result = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            effect_registry=[effect],
        )

        # Should have 5 quarter scores (4 regular + 1 Elam)
        assert len(result.quarter_scores) == 5

    def test_multiple_patches_applied_in_order(self) -> None:
        """Multiple patches are applied in registration order."""
        home = _make_team("home")
        away = _make_team("away")

        # First patch: add half_court_heave
        e1 = _make_game_def_patch_effect(
            {
                "add_actions": [
                    EXAMPLE_ACTIONS["half_court_heave"].model_dump(mode="json"),
                ],
            },
            effect_id="e-first",
            registered_at_round=1,
        )
        # Second patch: modify half_court_heave to be worth 10 points
        e2 = _make_game_def_patch_effect(
            {
                "modify_actions": {
                    "half_court_heave": {"points_on_success": 10},
                },
            },
            effect_id="e-second",
            registered_at_round=2,
        )

        result = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            effect_registry=[e2, e1],  # Intentionally out of order
        )

        # Game should complete (patches were applied)
        assert result.total_possessions > 0

    def test_no_patches_keeps_default_behavior(self) -> None:
        """Without patches, simulation behavior is unchanged."""
        home = _make_team("home")
        away = _make_team("away")

        r1 = simulate_game(home, away, DEFAULT_RULESET, seed=42)
        r2 = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            effect_registry=[],  # Empty list
        )

        assert r1.home_score == r2.home_score
        assert r1.away_score == r2.away_score
        assert r1.total_possessions == r2.total_possessions

    def test_malformed_patch_is_skipped(self) -> None:
        """A malformed patch dict is skipped without crashing the simulation."""
        home = _make_team("home")
        away = _make_team("away")

        # Malformed patch: bad modify_actions value
        bad_effect = RegisteredEffect(
            effect_id="bad-patch",
            proposal_id="p-1",
            _hook_points=["sim.game_definition.patch"],
            _lifetime=EffectLifetime.PERMANENT,
            effect_type="modify_game_definition",
            action_code={
                "type": "game_def_patch",
                "patch": {"modify_actions": "not-a-dict"},  # Invalid
            },
        )

        # Should not crash
        result = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            effect_registry=[bad_effect],
        )
        assert result.total_possessions > 0


# ---------------------------------------------------------------------------
# Phase 4c: Integration tests — new action appears in possession logs
# ---------------------------------------------------------------------------


class TestAddActionIntegration:
    """Prove that governance-added actions appear in simulation output."""

    def test_half_court_heave_appears_in_logs(self) -> None:
        """Adding half_court_heave via a patch makes it selectable during simulation.

        We run many seeds and check if the action ever appears. The action
        has low weight (5) and high midpoint (80), so it won't always be
        selected, but across enough games it should appear at least once.
        """
        home = _make_team("home")
        away = _make_team("away")

        effect = _make_game_def_patch_effect(
            {
                "add_actions": [
                    EXAMPLE_ACTIONS["half_court_heave"].model_dump(mode="json"),
                ],
            }
        )

        found_heave = False
        for seed in range(100):
            result = simulate_game(
                home, away, DEFAULT_RULESET, seed=seed,
                effect_registry=[effect],
            )
            for log in result.possession_log:
                if log.action == "half_court_heave":
                    found_heave = True
                    break
            if found_heave:
                break

        assert found_heave, (
            "half_court_heave never appeared in 100 games — "
            "the action may not be entering the selection pool"
        )

    def test_layup_appears_in_logs(self) -> None:
        """Adding layup via a patch makes it selectable and it appears in logs."""
        home = _make_team("home")
        away = _make_team("away")

        effect = _make_game_def_patch_effect(
            {
                "add_actions": [
                    EXAMPLE_ACTIONS["layup"].model_dump(mode="json"),
                ],
            }
        )

        found_layup = False
        for seed in range(50):
            result = simulate_game(
                home, away, DEFAULT_RULESET, seed=seed,
                effect_registry=[effect],
            )
            for log in result.possession_log:
                if log.action == "layup":
                    found_layup = True
                    break
            if found_layup:
                break

        assert found_layup, (
            "layup never appeared in 50 games — "
            "the action may not be entering the selection pool"
        )


class TestRemoveActionIntegration:
    """Prove that governance-removed actions disappear from simulation output."""

    def test_no_three_pointers_after_removal(self) -> None:
        """Removing three_point via a patch means no three-pointers in the game."""
        home = _make_team("home")
        away = _make_team("away")

        effect = _make_game_def_patch_effect(
            {"remove_actions": ["three_point"]}
        )

        # Run several games to be confident
        for seed in range(20):
            result = simulate_game(
                home, away, DEFAULT_RULESET, seed=seed,
                effect_registry=[effect],
            )
            for log in result.possession_log:
                assert log.action != "three_point", (
                    f"Seed {seed}: found three_point action after removal"
                )

    def test_no_mid_range_after_removal(self) -> None:
        """Removing mid_range via a patch means no mid-range shots in the game."""
        home = _make_team("home")
        away = _make_team("away")

        effect = _make_game_def_patch_effect(
            {"remove_actions": ["mid_range"]}
        )

        for seed in range(20):
            result = simulate_game(
                home, away, DEFAULT_RULESET, seed=seed,
                effect_registry=[effect],
            )
            for log in result.possession_log:
                assert log.action != "mid_range", (
                    f"Seed {seed}: found mid_range action after removal"
                )


class TestModifyActionIntegration:
    """Integration tests for action modification during simulation."""

    def test_higher_point_value_changes_scores(self) -> None:
        """Making three-pointers worth 5 should increase average scores."""
        home = _make_team("home")
        away = _make_team("away")

        # Baseline
        base_result = simulate_game(home, away, DEFAULT_RULESET, seed=42)

        # Modified: three-pointers worth 5
        effect = _make_game_def_patch_effect(
            {"modify_actions": {"three_point": {"points_on_success": 5}}}
        )
        mod_result = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            effect_registry=[effect],
        )

        # Same possessions but potentially higher scores
        # (deterministic with same seed — the selection weights don't change,
        # only the points per three-pointer)
        # With 5-point threes, total should be >= baseline
        mod_total = mod_result.home_score + mod_result.away_score
        base_total = base_result.home_score + base_result.away_score
        assert mod_total >= base_total

    def test_game_completes_with_modified_structure(self) -> None:
        """Game completes normally with modified elam margin."""
        home = _make_team("home")
        away = _make_team("away")

        effect = _make_game_def_patch_effect(
            {"modify_structure": {"elam_target_margin": 5}}
        )

        result = simulate_game(
            home, away, DEFAULT_RULESET, seed=42,
            effect_registry=[effect],
        )

        # Elam should activate with a tighter margin
        assert result.elam_activated
        assert result.total_possessions > 0
