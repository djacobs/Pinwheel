"""Tests for the repeal mechanism — effects browser and governance-driven repeal."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.core.effects import (
    EffectRegistry,
    load_effect_registry,
    register_effects_for_proposal,
    repeal_effect,
)
from pinwheel.core.governance import (
    REPEAL_TIER,
    REPEAL_TOKEN_COST,
    cast_vote,
    confirm_proposal,
    submit_repeal_proposal,
    tally_governance_with_effects,
    vote_threshold_for_tier,
)
from pinwheel.core.hooks import EffectLifetime, RegisteredEffect
from pinwheel.core.tokens import get_token_balance, regenerate_tokens
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.governance import EffectSpec
from pinwheel.models.rules import RuleSet

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
    governor_id = "gov-repeal-001"
    await regenerate_tokens(repo, governor_id, team.id, season_id)
    return governor_id, team.id


@pytest.fixture
def registry() -> EffectRegistry:
    """Create an empty EffectRegistry."""
    return EffectRegistry()


@pytest.fixture
def sample_effect() -> RegisteredEffect:
    """Create a sample registered effect for testing."""
    return RegisteredEffect(
        effect_id="effect-001",
        proposal_id="proposal-001",
        _hook_points=["round.game.post"],
        _lifetime=EffectLifetime.UNTIL_REPEALED,
        effect_type="meta_mutation",
        description="Winning team gains +1 swagger",
        target_type="team",
        target_selector="winning_team",
        meta_field="swagger",
        meta_value=1,
        meta_operation="increment",
    )


# --- EffectRegistry.remove_effect() Tests ---


class TestEffectRegistryRemoveEffect:
    def test_remove_existing_effect(
        self, registry: EffectRegistry, sample_effect: RegisteredEffect
    ) -> None:
        """remove_effect returns True and removes a registered effect."""
        registry.register(sample_effect)
        assert registry.count == 1
        assert registry.remove_effect(sample_effect.effect_id) is True
        assert registry.count == 0

    def test_remove_nonexistent_effect(self, registry: EffectRegistry) -> None:
        """remove_effect returns False for an ID not in the registry."""
        assert registry.remove_effect("nonexistent-id") is False

    def test_remove_does_not_affect_other_effects(
        self, registry: EffectRegistry, sample_effect: RegisteredEffect
    ) -> None:
        """Removing one effect leaves others intact."""
        other = RegisteredEffect(
            effect_id="effect-002",
            proposal_id="proposal-002",
            _hook_points=["report.commentary.pre"],
            _lifetime=EffectLifetime.PERMANENT,
            effect_type="narrative",
            narrative_instruction="Mention swagger in commentary.",
        )
        registry.register(sample_effect)
        registry.register(other)
        assert registry.count == 2

        registry.remove_effect(sample_effect.effect_id)
        assert registry.count == 1
        assert registry.get_effect("effect-002") is not None
        assert registry.get_effect("effect-001") is None


# --- EffectRegistry.get_effect() Tests ---


class TestEffectRegistryGetEffect:
    def test_get_existing_effect(
        self, registry: EffectRegistry, sample_effect: RegisteredEffect
    ) -> None:
        """get_effect returns the effect for a valid ID."""
        registry.register(sample_effect)
        found = registry.get_effect(sample_effect.effect_id)
        assert found is not None
        assert found.effect_id == sample_effect.effect_id

    def test_get_nonexistent_effect(self, registry: EffectRegistry) -> None:
        """get_effect returns None for an unknown ID."""
        assert registry.get_effect("unknown") is None


# --- repeal_effect() Tests ---


class TestRepealEffect:
    async def test_repeal_writes_event_and_removes(
        self, repo: Repository, season_id: str
    ) -> None:
        """repeal_effect writes an effect.repealed event and removes from registry."""
        registry = EffectRegistry()
        effect = RegisteredEffect(
            effect_id="eff-to-repeal",
            proposal_id="prop-001",
            _hook_points=["round.game.post"],
            _lifetime=EffectLifetime.UNTIL_REPEALED,
            effect_type="meta_mutation",
            description="Test effect",
        )
        registry.register(effect)

        removed = await repeal_effect(
            repo=repo,
            registry=registry,
            effect_id="eff-to-repeal",
            season_id=season_id,
            proposal_id="repeal-prop-001",
        )
        assert removed is True
        assert registry.count == 0

        # Verify the event was written
        events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["effect.repealed"],
        )
        assert len(events) == 1
        assert events[0].payload["effect_id"] == "eff-to-repeal"
        assert events[0].payload["reason"] == "governance_repeal"
        assert events[0].payload["proposal_id"] == "repeal-prop-001"

    async def test_repeal_nonexistent_writes_event_anyway(
        self, repo: Repository, season_id: str
    ) -> None:
        """repeal_effect writes event even if effect not in registry (idempotent)."""
        registry = EffectRegistry()

        removed = await repeal_effect(
            repo=repo,
            registry=registry,
            effect_id="already-gone",
            season_id=season_id,
            proposal_id="repeal-prop-002",
        )
        assert removed is False

        # Event should still be written for the event store record
        events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["effect.repealed"],
        )
        assert len(events) == 1


# --- Registry Reload After Repeal ---


class TestRegistryReloadAfterRepeal:
    async def test_repealed_effect_excluded_on_reload(
        self, repo: Repository, season_id: str
    ) -> None:
        """After a repeal event is written, load_effect_registry excludes the effect."""
        # Register an effect
        spec = EffectSpec(
            effect_type="meta_mutation",
            target_type="team",
            target_selector="winning_team",
            meta_field="swagger",
            meta_value=1,
            meta_operation="increment",
            duration="until_repealed",
            description="Swagger effect",
        )
        registry = EffectRegistry()
        registered = await register_effects_for_proposal(
            repo=repo,
            registry=registry,
            proposal_id="prop-source",
            effects=[spec],
            season_id=season_id,
            current_round=1,
        )
        assert len(registered) == 1
        effect_id = registered[0].effect_id

        # Reload — effect should be present
        reloaded = await load_effect_registry(repo, season_id)
        assert reloaded.count == 1

        # Write repeal event
        await repeal_effect(
            repo=repo,
            registry=registry,
            effect_id=effect_id,
            season_id=season_id,
            proposal_id="repeal-prop",
        )

        # Reload — effect should be excluded
        reloaded_after = await load_effect_registry(repo, season_id)
        assert reloaded_after.count == 0


# --- submit_repeal_proposal() Tests ---


class TestSubmitRepealProposal:
    async def test_creates_repeal_proposal(
        self, repo: Repository, season_id: str, seeded_governor: tuple[str, str]
    ) -> None:
        """submit_repeal_proposal creates a proposal with repeal metadata."""
        gov_id, team_id = seeded_governor

        proposal = await submit_repeal_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_id,
            season_id=season_id,
            target_effect_id="target-eff-001",
            effect_description="Winning team gains +1 swagger",
        )

        assert proposal.status == "submitted"
        assert proposal.tier == REPEAL_TIER
        assert proposal.token_cost == REPEAL_TOKEN_COST
        assert proposal.raw_text.startswith("Repeal: ")
        assert "swagger" in proposal.raw_text

    async def test_repeal_deducts_propose_tokens(
        self, repo: Repository, season_id: str, seeded_governor: tuple[str, str]
    ) -> None:
        """Submitting a repeal proposal costs PROPOSE tokens."""
        gov_id, team_id = seeded_governor
        balance_before = await get_token_balance(repo, gov_id, season_id)
        assert balance_before.propose == 2

        await submit_repeal_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_id,
            season_id=season_id,
            target_effect_id="target-eff-002",
            effect_description="Some effect",
        )

        balance_after = await get_token_balance(repo, gov_id, season_id)
        assert balance_after.propose == 2 - REPEAL_TOKEN_COST

    async def test_repeal_with_token_already_spent(
        self, repo: Repository, season_id: str, seeded_governor: tuple[str, str]
    ) -> None:
        """token_already_spent=True skips token deduction."""
        gov_id, team_id = seeded_governor

        await submit_repeal_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_id,
            season_id=season_id,
            target_effect_id="target-eff-003",
            effect_description="Some effect",
            token_already_spent=True,
        )

        # Balance should be unchanged
        balance = await get_token_balance(repo, gov_id, season_id)
        assert balance.propose == 2

    async def test_repeal_stores_target_in_payload(
        self, repo: Repository, season_id: str, seeded_governor: tuple[str, str]
    ) -> None:
        """The repeal target effect ID is stored in the event payload."""
        gov_id, team_id = seeded_governor

        await submit_repeal_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_id,
            season_id=season_id,
            target_effect_id="target-eff-004",
            effect_description="Some effect",
        )

        events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.submitted"],
        )
        assert len(events) == 1
        assert events[0].payload["repeal_target_effect_id"] == "target-eff-004"
        assert events[0].payload["proposal_type"] == "repeal"


# --- Repeal Tally Integration Tests ---


class TestRepealTally:
    async def test_passing_repeal_removes_effect(
        self, repo: Repository, season_id: str, seeded_governor: tuple[str, str]
    ) -> None:
        """When a repeal proposal passes, the target effect is removed."""
        gov_id, team_id = seeded_governor
        gov2_id = "gov-repeal-002"
        await regenerate_tokens(repo, gov2_id, team_id, season_id)

        # Register an effect
        registry = EffectRegistry()
        spec = EffectSpec(
            effect_type="meta_mutation",
            target_type="team",
            target_selector="winning_team",
            meta_field="swagger",
            meta_value=1,
            meta_operation="increment",
            duration="until_repealed",
            description="Swagger effect",
        )
        registered = await register_effects_for_proposal(
            repo=repo,
            registry=registry,
            proposal_id="source-prop",
            effects=[spec],
            season_id=season_id,
            current_round=1,
        )
        assert registry.count == 1
        target_eid = registered[0].effect_id

        # Submit and confirm repeal proposal
        proposal = await submit_repeal_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_id,
            season_id=season_id,
            target_effect_id=target_eid,
            effect_description="Swagger effect",
        )
        proposal = await confirm_proposal(repo, proposal)

        # Cast two yes votes
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

        # Tally governance
        new_ruleset, tallies = await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={proposal.id: [vote1, vote2]},
            current_ruleset=RuleSet(),
            round_number=2,
            effect_registry=registry,
        )

        assert len(tallies) == 1
        assert tallies[0].passed is True
        assert registry.count == 0  # Effect was removed

        # Verify repeal event was written
        repeal_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["effect.repealed"],
        )
        assert len(repeal_events) == 1

    async def test_failing_repeal_keeps_effect(
        self, repo: Repository, season_id: str, seeded_governor: tuple[str, str]
    ) -> None:
        """When a repeal proposal fails, the target effect remains active."""
        gov_id, team_id = seeded_governor

        # Register an effect
        registry = EffectRegistry()
        spec = EffectSpec(
            effect_type="narrative",
            narrative_instruction="Mention swagger in commentary",
            duration="permanent",
            description="Swagger narrative",
        )
        registered = await register_effects_for_proposal(
            repo=repo,
            registry=registry,
            proposal_id="source-prop-2",
            effects=[spec],
            season_id=season_id,
            current_round=1,
        )
        assert registry.count == 1
        target_eid = registered[0].effect_id

        # Submit and confirm repeal proposal
        proposal = await submit_repeal_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_id,
            season_id=season_id,
            target_effect_id=target_eid,
            effect_description="Swagger narrative",
        )
        proposal = await confirm_proposal(repo, proposal)

        # Cast a no vote — proposal fails
        vote1 = await cast_vote(
            repo=repo,
            proposal=proposal,
            governor_id=gov_id,
            team_id=team_id,
            vote_choice="no",
            weight=1.0,
        )

        new_ruleset, tallies = await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={proposal.id: [vote1]},
            current_ruleset=RuleSet(),
            round_number=2,
            effect_registry=registry,
        )

        assert len(tallies) == 1
        assert tallies[0].passed is False
        assert registry.count == 1  # Effect still active

        # No repeal event should exist
        repeal_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["effect.repealed"],
        )
        assert len(repeal_events) == 0

    async def test_repeal_only_removes_target_not_others(
        self, repo: Repository, season_id: str, seeded_governor: tuple[str, str]
    ) -> None:
        """Repealing one effect from a proposal with multiple effects keeps the others."""
        gov_id, team_id = seeded_governor
        gov2_id = "gov-repeal-003"
        await regenerate_tokens(repo, gov2_id, team_id, season_id)

        # Register two effects from the same proposal
        registry = EffectRegistry()
        specs = [
            EffectSpec(
                effect_type="meta_mutation",
                target_type="team",
                target_selector="winning_team",
                meta_field="swagger",
                meta_value=1,
                meta_operation="increment",
                duration="until_repealed",
                description="Swagger mutation",
            ),
            EffectSpec(
                effect_type="narrative",
                narrative_instruction="Mention swagger in commentary",
                duration="permanent",
                description="Swagger narrative",
            ),
        ]
        registered = await register_effects_for_proposal(
            repo=repo,
            registry=registry,
            proposal_id="source-prop-multi",
            effects=specs,
            season_id=season_id,
            current_round=1,
        )
        assert registry.count == 2
        target_eid = registered[0].effect_id  # Only repeal the first one

        # Submit, confirm, vote, tally repeal proposal
        proposal = await submit_repeal_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_id,
            season_id=season_id,
            target_effect_id=target_eid,
            effect_description="Swagger mutation",
        )
        proposal = await confirm_proposal(repo, proposal)
        vote1 = await cast_vote(
            repo=repo, proposal=proposal, governor_id=gov_id,
            team_id=team_id, vote_choice="yes", weight=1.0,
        )
        vote2 = await cast_vote(
            repo=repo, proposal=proposal, governor_id=gov2_id,
            team_id=team_id, vote_choice="yes", weight=1.0,
        )

        await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={proposal.id: [vote1, vote2]},
            current_ruleset=RuleSet(),
            round_number=2,
            effect_registry=registry,
        )

        assert registry.count == 1  # Only one remains
        remaining = registry.get_all_active()
        assert remaining[0].effect_type == "narrative"  # The narrative was kept


# --- Repeal of Nonexistent Effect (Graceful Handling) ---


class TestRepealEdgeCases:
    async def test_repeal_already_expired_effect_is_harmless(
        self, repo: Repository, season_id: str, seeded_governor: tuple[str, str]
    ) -> None:
        """Repealing an already-expired effect writes the event but does not crash."""
        gov_id, team_id = seeded_governor
        gov2_id = "gov-repeal-edge-001"
        await regenerate_tokens(repo, gov2_id, team_id, season_id)

        # Create an empty registry (effect already expired/removed)
        registry = EffectRegistry()

        # Submit repeal for a non-existent effect ID
        proposal = await submit_repeal_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_id,
            season_id=season_id,
            target_effect_id="already-expired-eid",
            effect_description="Ghost effect",
        )
        proposal = await confirm_proposal(repo, proposal)
        vote1 = await cast_vote(
            repo=repo, proposal=proposal, governor_id=gov_id,
            team_id=team_id, vote_choice="yes", weight=1.0,
        )
        vote2 = await cast_vote(
            repo=repo, proposal=proposal, governor_id=gov2_id,
            team_id=team_id, vote_choice="yes", weight=1.0,
        )

        # Should not raise an error
        new_ruleset, tallies = await tally_governance_with_effects(
            repo=repo,
            season_id=season_id,
            proposals=[proposal],
            votes_by_proposal={proposal.id: [vote1, vote2]},
            current_ruleset=RuleSet(),
            round_number=2,
            effect_registry=registry,
        )

        assert tallies[0].passed is True
        assert registry.count == 0

    async def test_repeal_is_tier_5(
        self, repo: Repository, season_id: str, seeded_governor: tuple[str, str]
    ) -> None:
        """Repeal proposals are always Tier 5."""
        gov_id, team_id = seeded_governor

        proposal = await submit_repeal_proposal(
            repo=repo,
            governor_id=gov_id,
            team_id=team_id,
            season_id=season_id,
            target_effect_id="some-eff",
            effect_description="Some effect",
        )
        assert proposal.tier == 5

    def test_repeal_threshold_is_supermajority(self) -> None:
        """Tier 5 proposals require a supermajority (0.67)."""
        threshold = vote_threshold_for_tier(REPEAL_TIER)
        assert threshold == 0.67


# --- Embed Tests ---


class TestRepealEmbeds:
    def test_effects_list_embed_empty(self) -> None:
        """Effects list embed shows 'no active effects' when empty."""
        from pinwheel.discord.embeds import build_effects_list_embed

        embed = build_effects_list_embed([])
        assert embed.description == "No active effects."

    def test_effects_list_embed_with_effects(self) -> None:
        """Effects list embed shows numbered effects with details."""
        from pinwheel.discord.embeds import build_effects_list_embed

        effects = [
            {
                "effect_id": "abcdef12-3456-7890-abcd-ef1234567890",
                "effect_type": "meta_mutation",
                "description": "Winning team gains +1 swagger",
                "lifetime": "until_repealed",
                "rounds_remaining": None,
                "proposal_text": "Give winning teams swagger points",
            },
            {
                "effect_id": "12345678-abcd-efgh-ijkl-mnopqrstuvwx",
                "effect_type": "narrative",
                "description": "Mention swagger in commentary",
                "lifetime": "permanent",
                "rounds_remaining": None,
                "proposal_text": "",
            },
        ]
        embed = build_effects_list_embed(effects, season_name="Season ONE")
        assert embed.title == "Active Effects -- Season ONE"
        assert len(embed.fields) == 2
        assert "swagger" in embed.fields[0].name.lower()

    def test_repeal_confirm_embed(self) -> None:
        """Repeal confirm embed shows effect details and cost."""
        from pinwheel.discord.embeds import build_repeal_confirm_embed

        embed = build_repeal_confirm_embed(
            effect_description="Winning team gains +1 swagger",
            effect_type="meta_mutation",
            effect_id="abcdef12-3456-7890-abcd-ef1234567890",
            token_cost=2,
            tokens_remaining=0,
            governor_name="TestGov",
        )
        assert "Repeal" in embed.title
        assert "meta mutation" in embed.description.lower()
        assert embed.author.name == "TestGov"
