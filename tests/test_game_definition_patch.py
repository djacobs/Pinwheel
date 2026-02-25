"""Tests for GameDefinitionPatch — Phase 4a of the Abstract Game Spine.

Tests cover:
- Adding new actions
- Removing existing actions
- Modifying existing actions (partial updates)
- Modifying turn structure fields
- Combined operations
- Edge cases (remove nonexistent, modify nonexistent, add overwrites)
- Serialization round-trip
- EXAMPLE_ACTIONS catalog
"""

from pinwheel.models.game_definition import (
    EXAMPLE_ACTIONS,
    ActionDefinition,
    GameDefinition,
    GameDefinitionPatch,
    basketball_game_definition,
)
from pinwheel.models.rules import DEFAULT_RULESET


def _base_game_def() -> GameDefinition:
    """Build a standard basketball GameDefinition for test baseline."""
    return basketball_game_definition(DEFAULT_RULESET)


# ---------------------------------------------------------------------------
# Phase 4a: Add actions
# ---------------------------------------------------------------------------


class TestAddActions:
    """Tests for GameDefinitionPatch.add_actions."""

    def test_add_single_action(self) -> None:
        """Adding a new action puts it in the resulting definition."""
        base = _base_game_def()
        heave = EXAMPLE_ACTIONS["half_court_heave"]
        patch = GameDefinitionPatch(add_actions=[heave])

        result = patch.apply(base)

        assert "half_court_heave" in [a.name for a in result.actions]
        assert len(result.actions) == len(base.actions) + 1

        # Verify the action properties
        added = next(a for a in result.actions if a.name == "half_court_heave")
        assert added.points_on_success == 4
        assert added.base_midpoint == 80.0

    def test_add_multiple_actions(self) -> None:
        """Adding multiple actions works correctly."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            add_actions=[
                EXAMPLE_ACTIONS["half_court_heave"],
                EXAMPLE_ACTIONS["layup"],
            ]
        )

        result = patch.apply(base)
        names = [a.name for a in result.actions]
        assert "half_court_heave" in names
        assert "layup" in names
        assert len(result.actions) == len(base.actions) + 2

    def test_add_overwrites_existing(self) -> None:
        """Adding an action with an existing name overwrites it."""
        base = _base_game_def()
        # Create a modified at_rim with 5 points
        new_at_rim = ActionDefinition(
            name="at_rim",
            display_name="Super At-Rim",
            points_on_success=5,
        )
        patch = GameDefinitionPatch(add_actions=[new_at_rim])

        result = patch.apply(base)
        at_rim = next(a for a in result.actions if a.name == "at_rim")
        assert at_rim.points_on_success == 5
        assert at_rim.display_name == "Super At-Rim"
        # Count should stay the same since it overwrote
        assert len(result.actions) == len(base.actions)

    def test_add_does_not_mutate_original(self) -> None:
        """Applying a patch does not mutate the original definition."""
        base = _base_game_def()
        original_count = len(base.actions)
        patch = GameDefinitionPatch(add_actions=[EXAMPLE_ACTIONS["layup"]])

        _result = patch.apply(base)

        assert len(base.actions) == original_count
        assert "layup" not in [a.name for a in base.actions]


# ---------------------------------------------------------------------------
# Phase 4a: Remove actions
# ---------------------------------------------------------------------------


class TestRemoveActions:
    """Tests for GameDefinitionPatch.remove_actions."""

    def test_remove_existing_action(self) -> None:
        """Removing an existing action removes it from the result."""
        base = _base_game_def()
        patch = GameDefinitionPatch(remove_actions=["three_point"])

        result = patch.apply(base)
        names = [a.name for a in result.actions]
        assert "three_point" not in names
        assert len(result.actions) == len(base.actions) - 1

    def test_remove_multiple_actions(self) -> None:
        """Removing multiple actions works correctly."""
        base = _base_game_def()
        patch = GameDefinitionPatch(remove_actions=["three_point", "mid_range"])

        result = patch.apply(base)
        names = [a.name for a in result.actions]
        assert "three_point" not in names
        assert "mid_range" not in names
        assert "at_rim" in names
        assert len(result.actions) == len(base.actions) - 2

    def test_remove_nonexistent_silently_ignored(self) -> None:
        """Removing a nonexistent action does not raise."""
        base = _base_game_def()
        original_count = len(base.actions)
        patch = GameDefinitionPatch(remove_actions=["nonexistent_shot"])

        result = patch.apply(base)
        assert len(result.actions) == original_count

    def test_remove_does_not_mutate_original(self) -> None:
        """Removing an action does not affect the original definition."""
        base = _base_game_def()
        original_names = [a.name for a in base.actions]
        patch = GameDefinitionPatch(remove_actions=["three_point"])

        _result = patch.apply(base)

        assert [a.name for a in base.actions] == original_names


# ---------------------------------------------------------------------------
# Phase 4a: Modify actions
# ---------------------------------------------------------------------------


class TestModifyActions:
    """Tests for GameDefinitionPatch.modify_actions."""

    def test_modify_single_field(self) -> None:
        """Modifying a single field on an existing action works."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            modify_actions={"three_point": {"points_on_success": 4}}
        )

        result = patch.apply(base)
        three = next(a for a in result.actions if a.name == "three_point")
        assert three.points_on_success == 4
        # Other fields unchanged
        assert three.base_midpoint == 50.0

    def test_modify_multiple_fields(self) -> None:
        """Modifying multiple fields on an action at once."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            modify_actions={
                "at_rim": {
                    "points_on_success": 3,
                    "base_midpoint": 35.0,
                    "display_name": "Power Dunk",
                }
            }
        )

        result = patch.apply(base)
        at_rim = next(a for a in result.actions if a.name == "at_rim")
        assert at_rim.points_on_success == 3
        assert at_rim.base_midpoint == 35.0
        assert at_rim.display_name == "Power Dunk"

    def test_modify_multiple_actions(self) -> None:
        """Modifying multiple different actions in one patch."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            modify_actions={
                "at_rim": {"points_on_success": 3},
                "three_point": {"points_on_success": 5},
            }
        )

        result = patch.apply(base)
        at_rim = next(a for a in result.actions if a.name == "at_rim")
        three = next(a for a in result.actions if a.name == "three_point")
        assert at_rim.points_on_success == 3
        assert three.points_on_success == 5

    def test_modify_nonexistent_silently_ignored(self) -> None:
        """Modifying a nonexistent action does not raise."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            modify_actions={"nonexistent": {"points_on_success": 10}}
        )

        result = patch.apply(base)
        assert len(result.actions) == len(base.actions)

    def test_modify_unknown_field_silently_ignored(self) -> None:
        """Modifying an unknown field on an action is silently ignored."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            modify_actions={
                "at_rim": {
                    "points_on_success": 3,
                    "totally_fake_field": 999,
                }
            }
        )

        result = patch.apply(base)
        at_rim = next(a for a in result.actions if a.name == "at_rim")
        assert at_rim.points_on_success == 3
        assert not hasattr(at_rim, "totally_fake_field")

    def test_modify_does_not_mutate_original(self) -> None:
        """Modifying an action does not affect the original definition."""
        base = _base_game_def()
        original_three_points = next(
            a for a in base.actions if a.name == "three_point"
        ).points_on_success
        patch = GameDefinitionPatch(
            modify_actions={"three_point": {"points_on_success": 10}}
        )

        _result = patch.apply(base)

        three = next(a for a in base.actions if a.name == "three_point")
        assert three.points_on_success == original_three_points


# ---------------------------------------------------------------------------
# Phase 4a: Modify structure
# ---------------------------------------------------------------------------


class TestModifyStructure:
    """Tests for GameDefinitionPatch.modify_structure."""

    def test_modify_quarters(self) -> None:
        """Changing the number of quarters."""
        base = _base_game_def()
        patch = GameDefinitionPatch(modify_structure={"quarters": 6})

        result = patch.apply(base)
        assert result.quarters == 6

    def test_modify_elam_disabled(self) -> None:
        """Disabling the Elam Ending."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            modify_structure={"elam_ending_enabled": False}
        )

        result = patch.apply(base)
        assert result.elam_ending_enabled is False

    def test_modify_multiple_structure_fields(self) -> None:
        """Modifying multiple structure fields at once."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            modify_structure={
                "quarters": 5,
                "quarter_clock_seconds": 480.0,
                "elam_target_margin": 20,
                "safety_cap_possessions": 500,
            }
        )

        result = patch.apply(base)
        assert result.quarters == 5
        assert result.quarter_clock_seconds == 480.0
        assert result.elam_target_margin == 20
        assert result.safety_cap_possessions == 500

    def test_modify_structure_unknown_field_ignored(self) -> None:
        """Unknown structure fields are silently ignored."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            modify_structure={"fake_field": 42, "quarters": 6}
        )

        result = patch.apply(base)
        assert result.quarters == 6
        assert not hasattr(result, "fake_field")

    def test_modify_structure_cannot_change_actions_via_type_safety(self) -> None:
        """The 'actions' field cannot be set via modify_structure.

        PatchValue is str|int|float|bool|None|dict[str,float], so a list
        (like an actions list) is rejected at Pydantic validation time.
        This test verifies the type safety boundary.
        """
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GameDefinitionPatch(
                modify_structure={"actions": []}  # type: ignore[dict-item]
            )

    def test_modify_structure_actions_field_excluded_at_apply(self) -> None:
        """Even if 'actions' key sneaks in (e.g. via model_construct), it is excluded."""
        base = _base_game_def()
        original_count = len(base.actions)
        # Use model_construct to bypass Pydantic validation
        patch = GameDefinitionPatch.model_construct(
            add_actions=[],
            remove_actions=[],
            modify_actions={},
            modify_structure={"actions": "should_be_ignored"},
            description="",
        )

        result = patch.apply(base)
        # Actions should be unchanged — 'actions' is excluded from structure mods
        assert len(result.actions) == original_count

    def test_modify_structure_does_not_mutate_original(self) -> None:
        """Modifying structure does not affect the original definition."""
        base = _base_game_def()
        original_quarters = base.quarters
        patch = GameDefinitionPatch(modify_structure={"quarters": 10})

        _result = patch.apply(base)
        assert base.quarters == original_quarters


# ---------------------------------------------------------------------------
# Phase 4a: Combined operations
# ---------------------------------------------------------------------------


class TestCombinedOperations:
    """Tests for patches with multiple operation types."""

    def test_add_and_remove(self) -> None:
        """Add a new action while removing an old one."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            add_actions=[EXAMPLE_ACTIONS["half_court_heave"]],
            remove_actions=["mid_range"],
        )

        result = patch.apply(base)
        names = [a.name for a in result.actions]
        assert "half_court_heave" in names
        assert "mid_range" not in names
        # Net change: +1 - 1 = same count
        assert len(result.actions) == len(base.actions)

    def test_remove_then_add_same_name(self) -> None:
        """Removing and then adding with the same name replaces the action."""
        base = _base_game_def()
        new_at_rim = ActionDefinition(
            name="at_rim",
            display_name="Completely New At-Rim",
            points_on_success=10,
            base_midpoint=99.0,
        )
        patch = GameDefinitionPatch(
            remove_actions=["at_rim"],
            add_actions=[new_at_rim],
        )

        result = patch.apply(base)
        at_rim = next(a for a in result.actions if a.name == "at_rim")
        assert at_rim.points_on_success == 10
        assert at_rim.base_midpoint == 99.0
        assert at_rim.display_name == "Completely New At-Rim"

    def test_modify_and_structure_change(self) -> None:
        """Modify an action AND turn structure in the same patch."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            modify_actions={"three_point": {"points_on_success": 4}},
            modify_structure={"quarters": 5},
        )

        result = patch.apply(base)
        three = next(a for a in result.actions if a.name == "three_point")
        assert three.points_on_success == 4
        assert result.quarters == 5

    def test_all_operations_together(self) -> None:
        """Add + remove + modify + structure all in one patch."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            add_actions=[EXAMPLE_ACTIONS["layup"]],
            remove_actions=["free_throw"],
            modify_actions={"at_rim": {"points_on_success": 3}},
            modify_structure={"elam_target_margin": 20},
        )

        result = patch.apply(base)
        names = [a.name for a in result.actions]
        assert "layup" in names
        assert "free_throw" not in names
        at_rim = next(a for a in result.actions if a.name == "at_rim")
        assert at_rim.points_on_success == 3
        assert result.elam_target_margin == 20

    def test_empty_patch_is_identity(self) -> None:
        """An empty patch produces an identical definition."""
        base = _base_game_def()
        patch = GameDefinitionPatch()

        result = patch.apply(base)
        assert len(result.actions) == len(base.actions)
        assert result.quarters == base.quarters
        assert result.elam_ending_enabled == base.elam_ending_enabled
        for orig, patched in zip(base.actions, result.actions, strict=True):
            assert orig.name == patched.name
            assert orig.points_on_success == patched.points_on_success


# ---------------------------------------------------------------------------
# Phase 4a: Serialization round-trip
# ---------------------------------------------------------------------------


class TestPatchSerialization:
    """Tests for JSON serialization of GameDefinitionPatch."""

    def test_round_trip_json(self) -> None:
        """Patch serializes to JSON and deserializes back identically."""
        patch = GameDefinitionPatch(
            add_actions=[EXAMPLE_ACTIONS["half_court_heave"]],
            remove_actions=["mid_range"],
            modify_actions={"three_point": {"points_on_success": 4}},
            modify_structure={"quarters": 6},
            description="Test patch",
        )

        data = patch.model_dump(mode="json")
        restored = GameDefinitionPatch(**data)

        assert len(restored.add_actions) == 1
        assert restored.add_actions[0].name == "half_court_heave"
        assert restored.remove_actions == ["mid_range"]
        assert restored.modify_actions == {"three_point": {"points_on_success": 4}}
        assert restored.modify_structure == {"quarters": 6}
        assert restored.description == "Test patch"

    def test_empty_patch_serialization(self) -> None:
        """An empty patch serializes and deserializes cleanly."""
        patch = GameDefinitionPatch()
        data = patch.model_dump(mode="json")
        restored = GameDefinitionPatch(**data)

        assert restored.add_actions == []
        assert restored.remove_actions == []
        assert restored.modify_actions == {}
        assert restored.modify_structure == {}


# ---------------------------------------------------------------------------
# Phase 4c: EXAMPLE_ACTIONS catalog
# ---------------------------------------------------------------------------


class TestExampleActions:
    """Tests for the EXAMPLE_ACTIONS catalog."""

    def test_half_court_heave_exists(self) -> None:
        """half_court_heave is in the catalog with correct properties."""
        heave = EXAMPLE_ACTIONS["half_court_heave"]
        assert heave.name == "half_court_heave"
        assert heave.base_midpoint == 80.0
        assert heave.points_on_success == 4
        assert heave.selection_weight == 5.0
        assert heave.category == "shot"

    def test_layup_exists(self) -> None:
        """layup is in the catalog with correct properties."""
        layup = EXAMPLE_ACTIONS["layup"]
        assert layup.name == "layup"
        assert layup.base_midpoint == 20.0
        assert layup.points_on_success == 2
        assert layup.selection_weight == 10.0

    def test_example_actions_not_in_default_game(self) -> None:
        """Example actions are NOT active in the default basketball definition."""
        base = _base_game_def()
        names = [a.name for a in base.actions]
        assert "half_court_heave" not in names
        assert "layup" not in names

    def test_example_actions_are_valid(self) -> None:
        """All example actions can be serialized and deserialized."""
        for name, action in EXAMPLE_ACTIONS.items():
            data = action.model_dump(mode="json")
            restored = ActionDefinition(**data)
            assert restored.name == name


# ---------------------------------------------------------------------------
# Phase 4a: GameDefinition.build_registry with patched actions
# ---------------------------------------------------------------------------


class TestPatchedRegistry:
    """Tests that patched definitions build valid registries."""

    def test_patched_registry_includes_added_action(self) -> None:
        """A registry built from a patched definition includes added actions."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            add_actions=[EXAMPLE_ACTIONS["half_court_heave"]]
        )
        patched = patch.apply(base)
        registry = patched.build_registry()

        assert "half_court_heave" in registry
        assert registry["half_court_heave"].points_on_success == 4
        assert len(registry) == len(base.actions) + 1

    def test_patched_registry_excludes_removed_action(self) -> None:
        """A registry built from a patched definition excludes removed actions."""
        base = _base_game_def()
        patch = GameDefinitionPatch(remove_actions=["three_point"])
        patched = patch.apply(base)
        registry = patched.build_registry()

        assert "three_point" not in registry
        assert len(registry) == len(base.actions) - 1

    def test_patched_registry_reflects_modifications(self) -> None:
        """A registry reflects modified action properties."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            modify_actions={"three_point": {"points_on_success": 4}}
        )
        patched = patch.apply(base)
        registry = patched.build_registry()

        assert registry["three_point"].points_on_success == 4

    def test_shot_actions_excludes_special(self) -> None:
        """shot_actions() on a patched registry still excludes specials."""
        base = _base_game_def()
        patch = GameDefinitionPatch(
            add_actions=[EXAMPLE_ACTIONS["half_court_heave"]]
        )
        patched = patch.apply(base)
        registry = patched.build_registry()

        shot_names = [a.name for a in registry.shot_actions()]
        assert "half_court_heave" in shot_names
        assert "free_throw" not in shot_names
