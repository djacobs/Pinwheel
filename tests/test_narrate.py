"""Tests for the narration layer."""

from pinwheel.core.narrate import narrate_play, narrate_winner
from pinwheel.models.game_definition import (
    EXAMPLE_ACTIONS,
    ActionDefinition,
    ActionRegistry,
    basketball_actions,
)
from pinwheel.models.rules import DEFAULT_RULESET
from pinwheel.models.team import Hooper, PlayerAttributes, Team, Venue


class TestNarratePlay:
    def test_foul_with_points_shows_hits(self) -> None:
        """Foul with points > 0 should include 'hits' from the stripe."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="mid_range",
            result="foul",
            points=2,
            seed=42,
        )
        assert "hits 2 from the stripe" in text

    def test_foul_with_zero_points_shows_misses(self) -> None:
        """Foul with 0 points should include 'misses from the stripe'."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="mid_range",
            result="foul",
            points=0,
            seed=42,
        )
        assert "misses from the stripe" in text

    def test_made_three_has_narration(self) -> None:
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="three_point",
            result="made",
            points=3,
            seed=1,
        )
        assert "Flash" in text
        assert len(text) > 10

    def test_missed_shot_has_narration(self) -> None:
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="at_rim",
            result="missed",
            points=0,
            seed=1,
        )
        assert "Flash" in text

    def test_turnover_has_narration(self) -> None:
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="mid_range",
            result="turnover",
            points=0,
            seed=1,
        )
        assert "Flash" in text or "Thunder" in text

    def test_shot_clock_violation(self) -> None:
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="shot_clock_violation",
            result="turnover",
            points=0,
            seed=1,
        )
        assert "Flash" in text
        assert "shot clock" in text.lower()

    def test_move_flourish_prepended(self) -> None:
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="three_point",
            result="made",
            points=3,
            move="Heat Check",
            seed=1,
        )
        assert "[Heat Check]" in text

    def test_deterministic_with_same_seed(self) -> None:
        t1 = narrate_play("A", "B", "mid_range", "made", 2, seed=99)
        t2 = narrate_play("A", "B", "mid_range", "made", 2, seed=99)
        assert t1 == t2

    def test_defensive_rebound_on_missed_three(self) -> None:
        """Missed three with a defensive rebounder should mention the rebounder."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="three_point",
            result="missed",
            points=0,
            rebounder="Brick",
            is_offensive_rebound=False,
            seed=7,
        )
        assert "Flash" in text
        assert "Brick" in text

    def test_offensive_rebound_on_missed_rim(self) -> None:
        """Missed at_rim with an offensive rebounder should mention the rebounder."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="at_rim",
            result="missed",
            points=0,
            rebounder="Hustle",
            is_offensive_rebound=True,
            seed=3,
        )
        assert "Flash" in text
        assert "Hustle" in text
        assert "offensive" in text.lower()

    def test_defensive_rebound_mentions_defensive(self) -> None:
        """Defensive rebound narration should include 'defensive' or 'board' or 'glass'."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="mid_range",
            result="missed",
            points=0,
            rebounder="Glass",
            is_offensive_rebound=False,
            seed=5,
        )
        assert "Glass" in text
        # Should contain some rebound-related language
        lower = text.lower()
        assert any(word in lower for word in ["rebound", "board", "glass"])

    def test_no_rebound_on_made_shot(self) -> None:
        """Made shots should not include rebound narration even if rebounder passed."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="mid_range",
            result="made",
            points=2,
            rebounder="Brick",
            is_offensive_rebound=False,
            seed=1,
        )
        assert "Brick" not in text

    def test_no_rebound_when_rebounder_empty(self) -> None:
        """Missed shots with no rebounder should not include rebound narration."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="three_point",
            result="missed",
            points=0,
            rebounder="",
            is_offensive_rebound=False,
            seed=1,
        )
        assert "rebound" not in text.lower()
        assert "board" not in text.lower()

    def test_rebound_deterministic_with_same_seed(self) -> None:
        """Rebound narration should be deterministic for the same seed."""
        t1 = narrate_play(
            "A",
            "B",
            "mid_range",
            "missed",
            0,
            rebounder="R",
            is_offensive_rebound=True,
            seed=42,
        )
        t2 = narrate_play(
            "A",
            "B",
            "mid_range",
            "missed",
            0,
            rebounder="R",
            is_offensive_rebound=True,
            seed=42,
        )
        assert t1 == t2

    def test_no_look_pass_suppressed_without_assist(self) -> None:
        """No-Look Pass tag should NOT appear when there's no assist."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="mid_range",
            result="made",
            points=2,
            move="No-Look Pass",
            seed=1,
        )
        assert "[No-Look Pass]" not in text

    def test_no_look_pass_shown_with_assist(self) -> None:
        """No-Look Pass tag should appear when there's an assist."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="mid_range",
            result="made",
            points=2,
            move="No-Look Pass",
            assist_id="teammate-1",
            seed=1,
        )
        assert "[No-Look Pass]" in text

    def test_other_moves_shown_without_assist(self) -> None:
        """Non-pass moves should still show tags regardless of assist."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="three_point",
            result="made",
            points=3,
            move="Heat Check",
            seed=1,
        )
        assert "[Heat Check]" in text

    def test_no_rebound_on_foul(self) -> None:
        """Foul results should not include rebound narration."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="at_rim",
            result="foul",
            points=2,
            rebounder="Brick",
            is_offensive_rebound=False,
            seed=1,
        )
        assert "Brick" not in text


class TestNarrateWinner:
    def test_three_point_winner(self) -> None:
        text = narrate_winner("Flash", "three_point", seed=42)
        assert "Flash" in text

    def test_mid_range_winner(self) -> None:
        text = narrate_winner("Flash", "mid_range", seed=42)
        assert "Flash" in text

    def test_at_rim_winner(self) -> None:
        text = narrate_winner("Flash", "at_rim", seed=42)
        assert "Flash" in text

    def test_unknown_action_fallback(self) -> None:
        text = narrate_winner("Flash", "unknown_action", seed=42)
        assert text == "Flash hits the game-winner"

    def test_move_flourish_appended(self) -> None:
        text = narrate_winner("Flash", "three_point", move="Clutch Gene", seed=42)
        assert "clutch gene activated" in text


# ---------------------------------------------------------------------------
# Phase 5: Data-driven narration tests
# ---------------------------------------------------------------------------


def _basketball_registry() -> ActionRegistry:
    """Build a standard basketball ActionRegistry for tests."""
    return ActionRegistry(basketball_actions(DEFAULT_RULESET))


def _registry_with_heave() -> ActionRegistry:
    """Build a basketball registry augmented with half_court_heave."""
    actions = basketball_actions(DEFAULT_RULESET) + [EXAMPLE_ACTIONS["half_court_heave"]]
    return ActionRegistry(actions)


class TestNarrationWithRegistry:
    """Narration using ActionRegistry produces non-empty, template-based text."""

    def test_made_three_with_registry(self) -> None:
        """Made three-pointer uses registry templates when provided."""
        registry = _basketball_registry()
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="three_point",
            result="made",
            points=3,
            seed=1,
            registry=registry,
        )
        assert "Flash" in text
        assert len(text) > 10

    def test_missed_mid_range_with_registry(self) -> None:
        """Missed mid-range uses registry templates when provided."""
        registry = _basketball_registry()
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="mid_range",
            result="missed",
            points=0,
            seed=1,
            registry=registry,
        )
        assert "Flash" in text

    def test_made_at_rim_with_registry(self) -> None:
        """Made at-rim uses registry templates when provided."""
        registry = _basketball_registry()
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="at_rim",
            result="made",
            points=2,
            seed=1,
            registry=registry,
        )
        assert "Flash" in text

    def test_all_basketball_actions_produce_nonempty_made(self) -> None:
        """Every basketball shot action with narration_made produces non-empty text."""
        registry = _basketball_registry()
        for action_def in registry.shot_actions():
            text = narrate_play(
                player="Player",
                defender="Defender",
                action=action_def.name,
                result="made",
                points=2,
                seed=42,
                registry=registry,
            )
            assert len(text) > 0, f"Empty narration for made {action_def.name}"
            assert "Player" in text, f"Player name missing for {action_def.name}"

    def test_all_basketball_actions_produce_nonempty_missed(self) -> None:
        """Every basketball shot action with narration_missed produces non-empty text."""
        registry = _basketball_registry()
        for action_def in registry.shot_actions():
            text = narrate_play(
                player="Player",
                defender="Defender",
                action=action_def.name,
                result="missed",
                points=0,
                seed=42,
                registry=registry,
            )
            assert len(text) > 0, f"Empty narration for missed {action_def.name}"
            assert "Player" in text, f"Player name missing for {action_def.name}"

    def test_registry_narration_deterministic(self) -> None:
        """Registry-based narration is deterministic with the same seed."""
        registry = _basketball_registry()
        t1 = narrate_play(
            "A",
            "B",
            "three_point",
            "made",
            3,
            seed=42,
            registry=registry,
        )
        t2 = narrate_play(
            "A",
            "B",
            "three_point",
            "made",
            3,
            seed=42,
            registry=registry,
        )
        assert t1 == t2

    def test_foul_with_registry_uses_foul_desc(self) -> None:
        """Foul narration uses narration_foul_desc from the registry."""
        registry = _basketball_registry()
        # three_point has narration_foul_desc="three"
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="three_point",
            result="foul",
            points=2,
            seed=42,
            registry=registry,
        )
        assert "Flash" in text
        assert "stripe" in text


class TestNarrationCustomAction:
    """Narration for custom governance-added actions uses their templates."""

    def test_half_court_heave_made(self) -> None:
        """half_court_heave made shot uses its own narration templates."""
        registry = _registry_with_heave()
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="half_court_heave",
            result="made",
            points=4,
            seed=1,
            registry=registry,
        )
        assert "Flash" in text
        # Should use one of the heave-specific templates (all caps excitement)
        assert any(word in text.upper() for word in ["BANG", "KIDDING", "NET", "ERUPTS"])

    def test_half_court_heave_missed(self) -> None:
        """half_court_heave missed shot uses its own narration templates."""
        registry = _registry_with_heave()
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="half_court_heave",
            result="missed",
            points=0,
            seed=1,
            registry=registry,
        )
        assert "Flash" in text
        # Should use one of the heave miss templates
        lower = text.lower()
        assert any(word in lower for word in ["close", "backboard", "airball", "off"])

    def test_half_court_heave_winner(self) -> None:
        """half_court_heave game-winner uses its narration_winner templates."""
        registry = _registry_with_heave()
        text = narrate_winner(
            "Flash",
            "half_court_heave",
            seed=1,
            registry=registry,
        )
        assert "Flash" in text
        # Should use heave winner templates, not generic fallback
        assert text != "Flash hits the game-winner"

    def test_half_court_heave_foul_desc(self) -> None:
        """half_court_heave foul uses its narration_foul_desc."""
        registry = _registry_with_heave()
        # The heave has narration_foul_desc="heave"
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="half_court_heave",
            result="foul",
            points=3,
            seed=42,
            registry=registry,
        )
        assert "Flash" in text
        assert "stripe" in text

    def test_layup_made_uses_templates(self) -> None:
        """Layup (EXAMPLE_ACTIONS) made shot uses its own templates."""
        actions = basketball_actions(DEFAULT_RULESET) + [EXAMPLE_ACTIONS["layup"]]
        registry = ActionRegistry(actions)
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="layup",
            result="made",
            points=2,
            seed=1,
            registry=registry,
        )
        assert "Flash" in text
        # Should NOT get generic "Flash scores"
        assert text != "Flash scores"

    def test_layup_missed_uses_templates(self) -> None:
        """Layup (EXAMPLE_ACTIONS) missed shot uses its own templates."""
        actions = basketball_actions(DEFAULT_RULESET) + [EXAMPLE_ACTIONS["layup"]]
        registry = ActionRegistry(actions)
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="layup",
            result="missed",
            points=0,
            seed=1,
            registry=registry,
        )
        assert "Flash" in text
        # Should NOT get generic "Flash misses"
        assert text != "Flash misses"


class TestNarrationBackwardCompat:
    """Narration without registry produces identical text to before."""

    def test_made_three_no_registry_unchanged(self) -> None:
        """Made three without registry produces same text as before Phase 5."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="three_point",
            result="made",
            points=3,
            seed=1,
        )
        # Same call with registry=None explicitly
        text2 = narrate_play(
            player="Flash",
            defender="Thunder",
            action="three_point",
            result="made",
            points=3,
            seed=1,
            registry=None,
        )
        assert text == text2

    def test_winner_no_registry_unchanged(self) -> None:
        """narrate_winner without registry produces same text as before Phase 5."""
        text = narrate_winner("Flash", "three_point", seed=42)
        text2 = narrate_winner("Flash", "three_point", seed=42, registry=None)
        assert text == text2

    def test_unknown_action_no_registry_fallback(self) -> None:
        """Unknown action without registry gets generic fallback."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="weird_action",
            result="made",
            points=2,
            seed=1,
        )
        assert text == "Flash scores"

    def test_unknown_action_missed_no_registry_fallback(self) -> None:
        """Unknown action miss without registry gets generic fallback."""
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="weird_action",
            result="missed",
            points=0,
            seed=1,
        )
        assert text == "Flash misses"

    def test_unknown_action_winner_no_registry_fallback(self) -> None:
        """Unknown action winner without registry gets generic fallback."""
        text = narrate_winner("Flash", "weird_action", seed=42)
        assert text == "Flash hits the game-winner"


class TestNarrationUnknownActionWithRegistry:
    """Actions not in the registry fall back to generic text."""

    def test_unknown_action_made_with_registry(self) -> None:
        """Unknown action made with registry falls back to generic."""
        registry = _basketball_registry()
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="teleportation_shot",
            result="made",
            points=5,
            seed=1,
            registry=registry,
        )
        assert text == "Flash scores"

    def test_unknown_action_missed_with_registry(self) -> None:
        """Unknown action missed with registry falls back to generic."""
        registry = _basketball_registry()
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="teleportation_shot",
            result="missed",
            points=0,
            seed=1,
            registry=registry,
        )
        assert text == "Flash misses"

    def test_unknown_action_winner_with_registry(self) -> None:
        """Unknown action winner with registry falls back to generic."""
        registry = _basketball_registry()
        text = narrate_winner("Flash", "teleportation_shot", seed=42, registry=registry)
        assert text == "Flash hits the game-winner"

    def test_unknown_action_foul_with_registry(self) -> None:
        """Unknown action foul with registry uses 'shot' as default desc."""
        registry = _basketball_registry()
        text = narrate_play(
            player="Flash",
            defender="Thunder",
            action="teleportation_shot",
            result="foul",
            points=2,
            seed=42,
            registry=registry,
        )
        assert "Flash" in text
        assert "stripe" in text


class TestNarrationTemplateValidity:
    """All narration templates on ActionDefinitions are valid (non-empty strings)."""

    def test_basketball_action_templates_non_empty(self) -> None:
        """All basketball actions with narration templates have valid strings."""
        for action_def in basketball_actions(DEFAULT_RULESET):
            for template in action_def.narration_made:
                assert isinstance(template, str)
                assert len(template.strip()) > 0, (
                    f"Empty narration_made template on {action_def.name}"
                )
            for template in action_def.narration_missed:
                assert isinstance(template, str)
                assert len(template.strip()) > 0, (
                    f"Empty narration_missed template on {action_def.name}"
                )
            for template in action_def.narration_winner:
                assert isinstance(template, str)
                assert len(template.strip()) > 0, (
                    f"Empty narration_winner template on {action_def.name}"
                )

    def test_example_action_templates_non_empty(self) -> None:
        """All EXAMPLE_ACTIONS with narration templates have valid strings."""
        for name, action_def in EXAMPLE_ACTIONS.items():
            for template in action_def.narration_made:
                assert isinstance(template, str)
                assert len(template.strip()) > 0, f"Empty narration_made template on {name}"
            for template in action_def.narration_missed:
                assert isinstance(template, str)
                assert len(template.strip()) > 0, f"Empty narration_missed template on {name}"
            for template in action_def.narration_winner:
                assert isinstance(template, str)
                assert len(template.strip()) > 0, f"Empty narration_winner template on {name}"

    def test_basketball_shot_actions_have_narration(self) -> None:
        """All basketball shot actions have narration_made and narration_missed."""
        for action_def in basketball_actions(DEFAULT_RULESET):
            if action_def.category == "shot" and not action_def.is_free_throw:
                assert len(action_def.narration_made) > 0, (
                    f"{action_def.name} missing narration_made"
                )
                assert len(action_def.narration_missed) > 0, (
                    f"{action_def.name} missing narration_missed"
                )

    def test_basketball_shot_actions_have_display_fields(self) -> None:
        """All basketball shot actions have narration_verb and narration_display."""
        for action_def in basketball_actions(DEFAULT_RULESET):
            if action_def.category == "shot" and not action_def.is_free_throw:
                assert action_def.narration_verb, f"{action_def.name} missing narration_verb"
                assert action_def.narration_display, f"{action_def.name} missing narration_display"

    def test_example_actions_have_narration(self) -> None:
        """All EXAMPLE_ACTIONS have narration_made and narration_missed."""
        for name, action_def in EXAMPLE_ACTIONS.items():
            assert len(action_def.narration_made) > 0, f"{name} missing narration_made"
            assert len(action_def.narration_missed) > 0, f"{name} missing narration_missed"
            assert action_def.narration_verb, f"{name} missing narration_verb"
            assert action_def.narration_display, f"{name} missing narration_display"

    def test_templates_format_without_error(self) -> None:
        """All templates can be formatted with player/defender without KeyError."""
        all_actions = basketball_actions(DEFAULT_RULESET) + list(EXAMPLE_ACTIONS.values())
        for action_def in all_actions:
            for template in action_def.narration_made:
                # Should not raise KeyError
                template.format(player="Test", defender="Foe")
            for template in action_def.narration_missed:
                template.format(player="Test", defender="Foe")
            for template in action_def.narration_winner:
                template.format(player="Test")


class TestNarrationRegistryMatchesLegacy:
    """With the basketball registry, narration matches legacy output exactly.

    Since the basketball actions carry the exact same template lists as the
    legacy hardcoded lists, the RNG-driven output should be identical.
    """

    def test_made_three_matches_legacy(self) -> None:
        """Made three with basketball registry matches no-registry output."""
        registry = _basketball_registry()
        for seed in range(20):
            legacy = narrate_play(
                "X",
                "Y",
                "three_point",
                "made",
                3,
                seed=seed,
            )
            driven = narrate_play(
                "X",
                "Y",
                "three_point",
                "made",
                3,
                seed=seed,
                registry=registry,
            )
            assert legacy == driven, f"Mismatch at seed {seed}: {legacy!r} vs {driven!r}"

    def test_missed_mid_range_matches_legacy(self) -> None:
        """Missed mid-range with basketball registry matches no-registry output."""
        registry = _basketball_registry()
        for seed in range(20):
            legacy = narrate_play(
                "X",
                "Y",
                "mid_range",
                "missed",
                0,
                seed=seed,
            )
            driven = narrate_play(
                "X",
                "Y",
                "mid_range",
                "missed",
                0,
                seed=seed,
                registry=registry,
            )
            assert legacy == driven, f"Mismatch at seed {seed}"

    def test_at_rim_winner_matches_legacy(self) -> None:
        """At-rim winner with basketball registry matches no-registry output."""
        registry = _basketball_registry()
        for seed in range(20):
            legacy = narrate_winner("X", "at_rim", seed=seed)
            driven = narrate_winner("X", "at_rim", seed=seed, registry=registry)
            assert legacy == driven, f"Mismatch at seed {seed}"

    def test_foul_matches_legacy(self) -> None:
        """Foul narration with basketball registry matches no-registry output."""
        registry = _basketball_registry()
        for seed in range(20):
            legacy = narrate_play(
                "X",
                "Y",
                "at_rim",
                "foul",
                2,
                seed=seed,
            )
            driven = narrate_play(
                "X",
                "Y",
                "at_rim",
                "foul",
                2,
                seed=seed,
                registry=registry,
            )
            assert legacy == driven, f"Mismatch at seed {seed}"


class TestNarrationActionDefinitionFields:
    """ActionDefinition narration fields are correctly populated."""

    def test_at_rim_narration_display(self) -> None:
        actions = {a.name: a for a in basketball_actions(DEFAULT_RULESET)}
        assert actions["at_rim"].narration_display == "RIM"

    def test_mid_range_narration_display(self) -> None:
        actions = {a.name: a for a in basketball_actions(DEFAULT_RULESET)}
        assert actions["mid_range"].narration_display == "MID"

    def test_three_point_narration_display(self) -> None:
        actions = {a.name: a for a in basketball_actions(DEFAULT_RULESET)}
        assert actions["three_point"].narration_display == "3PT"

    def test_free_throw_narration_display(self) -> None:
        actions = {a.name: a for a in basketball_actions(DEFAULT_RULESET)}
        assert actions["free_throw"].narration_display == "FT"

    def test_at_rim_narration_verb(self) -> None:
        actions = {a.name: a for a in basketball_actions(DEFAULT_RULESET)}
        assert actions["at_rim"].narration_verb == "drives"

    def test_three_point_foul_desc(self) -> None:
        actions = {a.name: a for a in basketball_actions(DEFAULT_RULESET)}
        assert actions["three_point"].narration_foul_desc == "three"

    def test_mid_range_foul_desc(self) -> None:
        actions = {a.name: a for a in basketball_actions(DEFAULT_RULESET)}
        assert actions["mid_range"].narration_foul_desc == "jumper"

    def test_at_rim_foul_desc(self) -> None:
        actions = {a.name: a for a in basketball_actions(DEFAULT_RULESET)}
        assert actions["at_rim"].narration_foul_desc == "drive"

    def test_half_court_heave_narration_display(self) -> None:
        assert EXAMPLE_ACTIONS["half_court_heave"].narration_display == "HEAVE"

    def test_half_court_heave_narration_verb(self) -> None:
        assert EXAMPLE_ACTIONS["half_court_heave"].narration_verb == "heaves"

    def test_layup_narration_display(self) -> None:
        assert EXAMPLE_ACTIONS["layup"].narration_display == "LAYUP"

    def test_default_narration_fields_empty(self) -> None:
        """ActionDefinition with no narration fields has empty defaults."""
        action = ActionDefinition(name="test_action")
        assert action.narration_made == []
        assert action.narration_missed == []
        assert action.narration_winner == []
        assert action.narration_verb == ""
        assert action.narration_display == ""
        assert action.narration_foul_desc == ""


def _make_e2e_team(team_id: str, name: str) -> Team:
    """Build a minimal Team for end-to-end narration tests."""
    attrs = PlayerAttributes.model_construct(
        scoring=50,
        passing=40,
        defense=40,
        speed=40,
        stamina=40,
        iq=50,
        ego=30,
        chaotic_alignment=20,
        fate=30,
    )

    def hooper(idx: int, starter: bool) -> Hooper:
        hid = f"{team_id}-h{idx}"
        return Hooper.model_construct(
            id=hid,
            name=f"Player-{hid}",
            team_id=team_id,
            archetype="sharpshooter",
            backstory="",
            attributes=attrs,
            is_starter=starter,
            moves=[],
        )

    hoopers = [hooper(i, True) for i in range(3)] + [hooper(3, False)]
    return Team(id=team_id, name=name, venue=Venue(name="Court", capacity=5000), hoopers=hoopers)


class TestNarrationEndToEnd:
    """End-to-end: simulate_game with custom action produces narration."""

    def test_simulate_with_half_court_heave_narration(self) -> None:
        """A game with half_court_heave produces non-generic narration for it."""
        from pinwheel.core.simulation import simulate_game
        from pinwheel.models.game_definition import (
            GameDefinitionPatch,
            basketball_game_definition,
        )
        from pinwheel.models.rules import DEFAULT_RULESET as rules

        home = _make_e2e_team("home", "Heave Squad")
        away = _make_e2e_team("away", "Blockers")

        # Add half_court_heave to the game definition with HIGH selection weight
        # so it gets selected frequently
        heave = EXAMPLE_ACTIONS["half_court_heave"].model_copy(update={"selection_weight": 200.0})
        patch = GameDefinitionPatch(add_actions=[heave])
        base_def = basketball_game_definition(rules)
        patched_def = patch.apply(base_def)

        result = simulate_game(
            home,
            away,
            rules,
            seed=999,
            game_def=patched_def,
        )

        # Find heave possessions in the log
        heave_possessions = [p for p in result.possession_log if p.action == "half_court_heave"]
        # With 200.0 weight, we should get at least some heaves
        assert len(heave_possessions) > 0, "No half_court_heave possessions found"

        # Now narrate them with the registry and verify non-generic text
        registry = patched_def.build_registry()
        for poss in heave_possessions:
            if poss.result == "made":
                text = narrate_play(
                    player="Player",
                    defender="Defender",
                    action=poss.action,
                    result=poss.result,
                    points=poss.points_scored,
                    seed=poss.possession_number,
                    registry=registry,
                )
                # Should NOT be generic
                assert text != "Player scores", f"Got generic text for heave made: {text}"
            elif poss.result == "missed":
                text = narrate_play(
                    player="Player",
                    defender="Defender",
                    action=poss.action,
                    result=poss.result,
                    points=0,
                    seed=poss.possession_number,
                    registry=registry,
                )
                assert text != "Player misses", f"Got generic text for heave missed: {text}"
