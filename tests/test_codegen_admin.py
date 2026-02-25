"""Tests for Phase 6e — Admin Tooling + End-to-End codegen lifecycle.

Tests: /review-codegen, /disable-effect, /rerun-council handlers,
codegen review embed, extended effects summary, E2E lifecycle.
"""

from __future__ import annotations

import random
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from pinwheel.config import Settings
from pinwheel.core.codegen import compute_code_hash
from pinwheel.core.effects import EffectRegistry, effect_spec_to_registered
from pinwheel.core.hooks import (
    HookContext,
    RegisteredEffect,
    fire_effects,
)
from pinwheel.core.state import GameState, HooperState
from pinwheel.discord.embeds import build_codegen_review_embed
from pinwheel.models.codegen import (
    CodegenEffectSpec,
    CodegenTrustLevel,
    CouncilReview,
)
from pinwheel.models.governance import EffectSpec
from pinwheel.models.team import Hooper, PlayerAttributes, suppress_budget_check

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_interaction(**overrides: object) -> AsyncMock:
    """Build a fully-configured Discord interaction mock."""
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = overrides.get("user_id", 12345)
    interaction.user.display_name = overrides.get(
        "display_name", "TestAdmin",
    )
    interaction.guild = MagicMock(spec=discord.Guild)
    return interaction


def _make_settings(
    admin_id: str = "12345",
) -> Settings:
    """Build settings with Discord and admin configured."""
    return Settings(
        pinwheel_env="production",
        database_url="sqlite+aiosqlite:///:memory:",
        anthropic_api_key="",
        discord_bot_token="test-token",
        discord_channel_id="123456789",
        discord_guild_id="987654321",
        discord_enabled=True,
        session_secret_key="test-secret",
        pinwheel_admin_discord_id=admin_id,
    )


def _make_codegen_effect(
    effect_id: str = "e-cg-1",
    code: str = "return HookResult(score_modifier=1)",
    trust_level: str = "numeric",
    enabled: bool = True,
    description: str = "Test codegen effect",
    execution_count: int = 0,
    error_count: int = 0,
    last_error: str = "",
) -> RegisteredEffect:
    """Build a codegen RegisteredEffect for testing."""
    return RegisteredEffect(
        effect_id=effect_id,
        proposal_id="p-test",
        _hook_points=["sim.possession.post"],
        effect_type="codegen",
        codegen_code=code,
        codegen_code_hash=compute_code_hash(code),
        codegen_trust_level=trust_level,
        codegen_enabled=enabled,
        description=description,
        codegen_execution_count=execution_count,
        codegen_error_count=error_count,
        codegen_last_error=last_error,
    )


def _make_game_state() -> GameState:
    """Build a minimal GameState for E2E testing."""
    with suppress_budget_check():
        home = Hooper(
            id="h1",
            name="Ace",
            team_id="t1",
            archetype="scorer",
            attributes=PlayerAttributes(
                scoring=80, passing=60, defense=50, speed=70,
                stamina=60, iq=70, ego=40,
                chaotic_alignment=30, fate=50,
            ),
        )
        away = Hooper(
            id="h2",
            name="Block",
            team_id="t2",
            archetype="defender",
            attributes=PlayerAttributes(
                scoring=40, passing=50, defense=80, speed=60,
                stamina=70, iq=60, ego=30,
                chaotic_alignment=20, fate=50,
            ),
        )
    return GameState(
        home_agents=[HooperState(hooper=home)],
        away_agents=[HooperState(hooper=away)],
        home_score=50,
        away_score=48,
        quarter=3,
        possession_number=40,
        home_has_ball=True,
    )


# ===================================================================
# Codegen review embed tests
# ===================================================================


class TestCodegenReviewEmbed:
    """Tests for build_codegen_review_embed."""

    def test_embed_has_correct_title_enabled(self) -> None:
        effect = _make_codegen_effect()
        embed = build_codegen_review_embed(effect)
        assert "ENABLED" in embed.title
        assert "Codegen Effect" in embed.title

    def test_embed_has_correct_title_disabled(self) -> None:
        effect = _make_codegen_effect(enabled=False)
        embed = build_codegen_review_embed(effect)
        assert "DISABLED" in embed.title

    def test_embed_shows_description(self) -> None:
        effect = _make_codegen_effect(description="Gravity well")
        embed = build_codegen_review_embed(effect)
        assert embed.description == "Gravity well"

    def test_embed_has_trust_level_field(self) -> None:
        effect = _make_codegen_effect(trust_level="flow")
        embed = build_codegen_review_embed(effect)
        field_values = {f.name: f.value for f in embed.fields}
        assert field_values["Trust Level"] == "flow"

    def test_embed_has_execution_count(self) -> None:
        effect = _make_codegen_effect(execution_count=42)
        embed = build_codegen_review_embed(effect)
        field_values = {f.name: f.value for f in embed.fields}
        assert field_values["Executions"] == "42"

    def test_embed_has_error_count(self) -> None:
        effect = _make_codegen_effect(error_count=3)
        embed = build_codegen_review_embed(effect)
        field_values = {f.name: f.value for f in embed.fields}
        assert field_values["Errors"] == "3"

    def test_embed_shows_code_preview(self) -> None:
        code = "return HookResult(score_modifier=5)"
        effect = _make_codegen_effect(code=code)
        embed = build_codegen_review_embed(effect)
        field_values = {f.name: f.value for f in embed.fields}
        assert code in field_values["Code Preview"]

    def test_embed_truncates_long_code(self) -> None:
        long_code = "x = 1\n" * 200  # Very long code
        effect = _make_codegen_effect(code=long_code)
        embed = build_codegen_review_embed(effect)
        field_values = {f.name: f.value for f in embed.fields}
        assert "..." in field_values["Code Preview"]

    def test_embed_shows_disabled_reason(self) -> None:
        effect = _make_codegen_effect(enabled=False)
        effect.codegen_disabled_reason = "Sandbox violation"
        embed = build_codegen_review_embed(effect)
        field_values = {f.name: f.value for f in embed.fields}
        assert "Disabled Reason" in field_values
        assert "Sandbox violation" in field_values["Disabled Reason"]

    def test_embed_shows_last_error(self) -> None:
        effect = _make_codegen_effect(last_error="KeyError: 'bad'")
        embed = build_codegen_review_embed(effect)
        field_values = {f.name: f.value for f in embed.fields}
        assert "Last Error" in field_values
        assert "KeyError" in field_values["Last Error"]

    def test_embed_shows_code_hash(self) -> None:
        effect = _make_codegen_effect()
        embed = build_codegen_review_embed(effect)
        field_values = {f.name: f.value for f in embed.fields}
        assert "Code Hash" in field_values

    def test_embed_footer(self) -> None:
        effect = _make_codegen_effect()
        embed = build_codegen_review_embed(effect)
        assert embed.footer.text == "Pinwheel Fates -- Codegen Review"

    def test_embed_color(self) -> None:
        effect = _make_codegen_effect()
        embed = build_codegen_review_embed(effect)
        assert embed.color is not None
        assert embed.color.value == 0x9B59B6


# ===================================================================
# Effects summary extension tests
# ===================================================================


class TestEffectsSummaryCodegen:
    """Tests for build_effects_summary with codegen effects."""

    def test_codegen_effect_shows_metadata(self) -> None:
        registry = EffectRegistry()
        effect = _make_codegen_effect(
            execution_count=10, error_count=2,
        )
        registry.register(effect)
        summary = registry.build_effects_summary()
        assert "[codegen:" in summary
        assert "enabled" in summary
        assert "runs=10" in summary
        assert "errors=2" in summary

    def test_disabled_codegen_shows_disabled(self) -> None:
        registry = EffectRegistry()
        effect = _make_codegen_effect(enabled=False)
        registry.register(effect)
        summary = registry.build_effects_summary()
        assert "DISABLED" in summary

    def test_non_codegen_no_codegen_metadata(self) -> None:
        registry = EffectRegistry()
        effect = RegisteredEffect(
            effect_id="e-plain",
            proposal_id="p-1",
            effect_type="hook_callback",
            description="Plain effect",
        )
        registry.register(effect)
        summary = registry.build_effects_summary()
        assert "[codegen:" not in summary

    def test_mixed_effects_summary(self) -> None:
        registry = EffectRegistry()
        plain = RegisteredEffect(
            effect_id="e-plain",
            proposal_id="p-1",
            effect_type="narrative",
            description="Story effect",
        )
        codegen = _make_codegen_effect(
            effect_id="e-cg",
            execution_count=5,
            error_count=0,
        )
        registry.register(plain)
        registry.register(codegen)
        summary = registry.build_effects_summary()
        # codegen line has metadata
        lines = summary.split("\n")
        codegen_lines = [
            ln for ln in lines if "[codegen:" in ln
        ]
        plain_lines = [
            ln for ln in lines if "Story effect" in ln
        ]
        assert len(codegen_lines) == 1
        assert len(plain_lines) == 1
        assert "[codegen:" not in plain_lines[0]


# ===================================================================
# Discord command handler tests
# ===================================================================


class TestReviewCodegenCommand:
    """Tests for the /review-codegen command handler."""

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self) -> None:
        from pinwheel.core.event_bus import EventBus
        from pinwheel.discord.bot import PinwheelBot

        settings = _make_settings(admin_id="99999")
        bot = PinwheelBot(settings, EventBus())
        interaction = make_interaction(user_id=12345)

        await bot._handle_review_codegen(interaction)
        interaction.response.send_message.assert_awaited_once()
        msg = interaction.response.send_message.call_args
        assert "restricted" in msg.kwargs.get("content", msg.args[0]).lower()

    @pytest.mark.asyncio
    async def test_no_guild_rejected(self) -> None:
        from pinwheel.core.event_bus import EventBus
        from pinwheel.discord.bot import PinwheelBot

        settings = _make_settings()
        bot = PinwheelBot(settings, EventBus())
        interaction = make_interaction()
        interaction.guild = None

        await bot._handle_review_codegen(interaction)
        interaction.response.send_message.assert_awaited_once()
        msg = interaction.response.send_message.call_args
        assert "server" in msg.kwargs.get("content", msg.args[0]).lower()

    @pytest.mark.asyncio
    async def test_no_engine_rejected(self) -> None:
        from pinwheel.core.event_bus import EventBus
        from pinwheel.discord.bot import PinwheelBot

        settings = _make_settings()
        bot = PinwheelBot(settings, EventBus(), engine=None)
        interaction = make_interaction()

        await bot._handle_review_codegen(interaction)
        # First call is admin check → pass; next is engine check
        # With engine=None, should get "Database unavailable"
        calls = interaction.response.send_message.call_args_list
        last_msg = calls[-1].kwargs.get("content", calls[-1].args[0])
        assert "database" in last_msg.lower() or "unavailable" in last_msg.lower()


class TestDisableEffectCommand:
    """Tests for the /disable-effect command handler."""

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self) -> None:
        from pinwheel.core.event_bus import EventBus
        from pinwheel.discord.bot import PinwheelBot

        settings = _make_settings(admin_id="99999")
        bot = PinwheelBot(settings, EventBus())
        interaction = make_interaction(user_id=12345)

        await bot._handle_disable_effect(interaction, "e-1")
        interaction.response.send_message.assert_awaited_once()
        msg = interaction.response.send_message.call_args
        assert "restricted" in msg.kwargs.get("content", msg.args[0]).lower()

    @pytest.mark.asyncio
    async def test_no_guild_rejected(self) -> None:
        from pinwheel.core.event_bus import EventBus
        from pinwheel.discord.bot import PinwheelBot

        settings = _make_settings()
        bot = PinwheelBot(settings, EventBus())
        interaction = make_interaction()
        interaction.guild = None

        await bot._handle_disable_effect(interaction, "e-1")
        msg = interaction.response.send_message.call_args
        assert "server" in msg.kwargs.get("content", msg.args[0]).lower()


class TestRerunCouncilCommand:
    """Tests for the /rerun-council command handler."""

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self) -> None:
        from pinwheel.core.event_bus import EventBus
        from pinwheel.discord.bot import PinwheelBot

        settings = _make_settings(admin_id="99999")
        bot = PinwheelBot(settings, EventBus())
        interaction = make_interaction(user_id=12345)

        await bot._handle_rerun_council(interaction, "e-1")
        interaction.response.send_message.assert_awaited_once()
        msg = interaction.response.send_message.call_args
        assert "restricted" in msg.kwargs.get("content", msg.args[0]).lower()

    @pytest.mark.asyncio
    async def test_no_guild_rejected(self) -> None:
        from pinwheel.core.event_bus import EventBus
        from pinwheel.discord.bot import PinwheelBot

        settings = _make_settings()
        bot = PinwheelBot(settings, EventBus())
        interaction = make_interaction()
        interaction.guild = None

        await bot._handle_rerun_council(interaction, "e-1")
        msg = interaction.response.send_message.call_args
        assert "server" in msg.kwargs.get("content", msg.args[0]).lower()

    @pytest.mark.asyncio
    async def test_no_engine_rejected(self) -> None:
        from pinwheel.core.event_bus import EventBus
        from pinwheel.discord.bot import PinwheelBot

        settings = _make_settings()
        bot = PinwheelBot(settings, EventBus(), engine=None)
        interaction = make_interaction()

        await bot._handle_rerun_council(interaction, "e-1")
        calls = interaction.response.send_message.call_args_list
        last_msg = calls[-1].kwargs.get("content", calls[-1].args[0])
        assert "database" in last_msg.lower() or "unavailable" in last_msg.lower()


# ===================================================================
# End-to-end lifecycle test
# ===================================================================


class TestCodegenEndToEndLifecycle:
    """E2E: create codegen effect spec → register → fire → verify."""

    def test_full_lifecycle(self) -> None:
        """Create spec → convert to RegisteredEffect → register → fire → check HookResult."""
        # 1. Create a CodegenEffectSpec (as AI interpreter would)
        code = "return HookResult(score_modifier=3, narrative_note='Boom!')"
        code_hash = compute_code_hash(code)
        codegen_spec = CodegenEffectSpec(
            code=code,
            code_hash=code_hash,
            trust_level=CodegenTrustLevel.FLOW,
            council_review=CouncilReview(
                proposal_id="p-e2e",
                code_hash=code_hash,
                consensus=True,
            ),
            hook_points=["sim.possession.post"],
            description="E2E gravity well",
        )

        # 2. Wrap in EffectSpec (governance layer)
        effect_spec = EffectSpec(
            effect_type="codegen",
            codegen=codegen_spec,
            description="E2E gravity well",
        )

        # 3. Convert to RegisteredEffect (effects layer)
        registered = effect_spec_to_registered(
            effect_spec, "p-e2e", current_round=5,
        )
        assert registered.effect_type == "codegen"
        assert registered.codegen_code == code
        assert registered.codegen_code_hash == code_hash
        assert registered.codegen_enabled is True

        # 4. Register in EffectRegistry
        registry = EffectRegistry()
        registry.register(registered)
        assert registry.count == 1

        effects = registry.get_effects_for_hook("sim.possession.post")
        assert len(effects) == 1

        # 5. Fire via fire_effects
        game_state = _make_game_state()
        ctx = HookContext(
            game_state=game_state,
            rng=random.Random(42),
        )
        results = fire_effects(
            "sim.possession.post", ctx, effects,
        )

        # 6. Verify HookResult
        assert len(results) == 1
        result = results[0]
        assert result.score_modifier == 3
        assert "Boom!" in result.narrative

        # 7. Check execution tracking
        assert registered.codegen_execution_count == 1
        assert registered.codegen_error_count == 0

    def test_lifecycle_with_disable(self) -> None:
        """Effect fires, then gets disabled, then stops firing."""
        code = "return HookResult(score_modifier=2)"
        effect = _make_codegen_effect(code=code)
        registry = EffectRegistry()
        registry.register(effect)

        game_state = _make_game_state()
        ctx = HookContext(
            game_state=game_state,
            rng=random.Random(42),
        )

        # Fire once — should work
        results = fire_effects(
            "sim.possession.post",
            ctx,
            registry.get_effects_for_hook("sim.possession.post"),
        )
        assert len(results) == 1
        assert results[0].score_modifier == 2
        assert effect.codegen_execution_count == 1

        # Disable the effect (as admin would)
        effect.codegen_enabled = False
        effect.codegen_disabled_reason = "Disabled by admin"

        # Fire again — should return no-op
        results = fire_effects(
            "sim.possession.post",
            ctx,
            registry.get_effects_for_hook("sim.possession.post"),
        )
        assert len(results) == 1
        # Disabled codegen returns empty HookResult
        assert results[0].score_modifier == 0
        # Execution count does NOT increment for disabled effects
        assert effect.codegen_execution_count == 1

    def test_lifecycle_effects_summary_reflects_state(self) -> None:
        """Effects summary updates as effects are used and disabled."""
        code = "return HookResult(score_modifier=1)"
        effect = _make_codegen_effect(
            code=code, execution_count=0, error_count=0,
        )
        registry = EffectRegistry()
        registry.register(effect)

        # Initial summary
        summary = registry.build_effects_summary()
        assert "enabled" in summary
        assert "runs=0" in summary

        # After some executions
        effect.codegen_execution_count = 15
        effect.codegen_error_count = 2
        summary = registry.build_effects_summary()
        assert "runs=15" in summary
        assert "errors=2" in summary

        # After disable
        effect.codegen_enabled = False
        summary = registry.build_effects_summary()
        assert "DISABLED" in summary

    def test_lifecycle_embed_reflects_state(self) -> None:
        """Embed updates as effect state changes."""
        effect = _make_codegen_effect()

        # Initially enabled
        embed = build_codegen_review_embed(effect)
        assert "ENABLED" in embed.title

        # After errors
        effect.codegen_error_count = 5
        effect.codegen_last_error = "TypeError: bad arg"
        embed = build_codegen_review_embed(effect)
        field_values = {f.name: f.value for f in embed.fields}
        assert field_values["Errors"] == "5"
        assert "TypeError" in field_values["Last Error"]

        # After disable
        effect.codegen_enabled = False
        effect.codegen_disabled_reason = "Too many errors"
        embed = build_codegen_review_embed(effect)
        assert "DISABLED" in embed.title
        field_values = {f.name: f.value for f in embed.fields}
        assert "Too many errors" in field_values["Disabled Reason"]
