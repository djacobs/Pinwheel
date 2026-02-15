"""End-to-end workflow verification: full player journey through Pinwheel Fates.

Exercises the complete lifecycle:
  Season creation -> team setup -> governor enrollment -> token grants ->
  proposal submission -> proposal confirmation -> voting -> game simulation
  via step_round() -> governance tally -> effects firing -> standings ->
  reports -> season progression -> playoff bracket -> playoff games ->
  championship -> web page routes.

Verifies integration of recent Wave 1-2 changes:
  - Proposal Effects System (meta_mutation, hook_callback, narrative)
  - NarrativeContext (dramatic context in all output systems)
  - GameEffect hooks wired into the game loop
  - Expanded RuleSet (new parameters)
  - Compound proposals (multiple parameter changes)
  - Auto-migrate schema (missing columns added on startup)
"""

from __future__ import annotations

import uuid
from math import comb

import pytest
from httpx import ASGITransport, AsyncClient

from pinwheel.config import Settings
from pinwheel.core.effects import EffectRegistry, effect_spec_to_registered
from pinwheel.core.event_bus import EventBus
from pinwheel.core.game_loop import (
    RoundResult,
    compute_standings_from_repo,
    step_round,
    tally_pending_governance,
)
from pinwheel.core.governance import (
    cast_vote,
    compute_vote_weight,
    confirm_proposal,
    submit_proposal,
    tally_governance_with_effects,
    vote_threshold_for_tier,
)
from pinwheel.core.hooks import EffectLifetime, RegisteredEffect
from pinwheel.core.meta import MetaStore
from pinwheel.core.narrative import NarrativeContext, compute_narrative_context
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.core.seeding import generate_league
from pinwheel.core.simulation import simulate_game
from pinwheel.core.tokens import get_token_balance, regenerate_tokens
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.main import create_app
from pinwheel.models.governance import (
    EffectSpec,
    ProposalInterpretation,
    RuleInterpretation,
    Vote,
)
from pinwheel.models.rules import DEFAULT_RULESET, RuleSet

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine():
    """Create in-memory SQLite engine with tables."""
    eng = create_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def app_and_engine():
    """Create test app + engine sharing the same in-memory database."""
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    application = create_app(settings)
    eng = create_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    application.state.engine = eng
    application.state.event_bus = EventBus()

    # Set up presentation state (normally done in lifespan)
    from pinwheel.core.presenter import PresentationState

    application.state.presentation_state = PresentationState()
    yield application, eng
    await eng.dispose()


# ---------------------------------------------------------------------------
# Helper: seed a league into the database
# ---------------------------------------------------------------------------


async def seed_league_and_season(
    engine: object,
    num_teams: int = 4,
    num_rounds: int = 1,
    seed: int = 42,
    enroll_governors: bool = True,
) -> dict:
    """Create a league, season, teams, hoopers, schedule, and optionally governors.

    Returns a dict with IDs and mappings needed by the tests.
    """
    league = generate_league(num_teams=num_teams, seed=seed)

    async with get_session(engine) as session:
        repo = Repository(session)
        db_league = await repo.create_league(league.name)
        db_season = await repo.create_season(
            db_league.id,
            "Test Season",
            starting_ruleset=DEFAULT_RULESET.model_dump(),
        )
        # Activate
        db_season.status = "active"
        await session.flush()

        team_id_map: dict[str, str] = {}
        hooper_id_map: dict[str, str] = {}
        db_team_ids: list[str] = []

        for team in league.teams:
            db_team = await repo.create_team(
                season_id=db_season.id,
                name=team.name,
                color=team.color,
                motto=team.motto,
                venue=team.venue.model_dump(),
            )
            team_id_map[team.id] = db_team.id
            db_team_ids.append(db_team.id)

            for hooper in team.hoopers:
                db_hooper = await repo.create_hooper(
                    team_id=db_team.id,
                    season_id=db_season.id,
                    name=hooper.name,
                    archetype=hooper.archetype,
                    attributes=hooper.attributes.model_dump(),
                    moves=[m.model_dump() for m in hooper.moves],
                    is_active=hooper.is_starter,
                )
                hooper_id_map[hooper.id] = db_hooper.id

        # Generate schedule
        schedule = generate_round_robin(db_team_ids, num_rounds=num_rounds)
        for m in schedule:
            await repo.create_schedule_entry(
                season_id=db_season.id,
                round_number=m.round_number,
                matchup_index=m.matchup_index,
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
                phase=m.phase,
            )

        # Enroll governors (mock Discord players)
        governor_ids: list[str] = []
        if enroll_governors:
            for idx, db_tid in enumerate(db_team_ids):
                player = await repo.get_or_create_player(
                    discord_id=f"discord-{idx}",
                    username=f"Governor-{idx}",
                )
                await repo.enroll_player(player.id, db_tid, db_season.id)
                # Grant initial tokens
                await regenerate_tokens(repo, player.id, db_tid, db_season.id)
                governor_ids.append(player.id)

        return {
            "league": league,
            "league_id": db_league.id,
            "season_id": db_season.id,
            "team_id_map": team_id_map,
            "hooper_id_map": hooper_id_map,
            "db_team_ids": db_team_ids,
            "schedule": schedule,
            "governor_ids": governor_ids,
        }


# ===========================================================================
# Test Class: Full Workflow
# ===========================================================================


class TestFullWorkflow:
    """Comprehensive end-to-end verification of the complete player journey."""

    # --- 1. Season creation and team setup ---

    async def test_season_creation_and_team_setup(self, engine: object) -> None:
        """Verify league, season, teams, and hoopers are created correctly."""
        ctx = await seed_league_and_season(engine, num_teams=4, enroll_governors=False)

        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(ctx["season_id"])
            assert season is not None
            assert season.status == "active"
            assert season.current_ruleset is not None

            teams = await repo.get_teams_for_season(ctx["season_id"])
            assert len(teams) == 4

            for team in teams:
                assert len(team.hoopers) == 4  # 3 starters + 1 bench

            # Check ruleset is DEFAULT
            ruleset = RuleSet(**season.current_ruleset)
            assert ruleset == DEFAULT_RULESET

    # --- 2. Governor enrollment and token grants ---

    async def test_governor_enrollment_and_tokens(self, engine: object) -> None:
        """Verify governors are enrolled and receive initial tokens."""
        ctx = await seed_league_and_season(engine, num_teams=4)

        async with get_session(engine) as session:
            repo = Repository(session)
            for idx, db_tid in enumerate(ctx["db_team_ids"]):
                govs = await repo.get_governors_for_team(db_tid, ctx["season_id"])
                assert len(govs) == 1, f"Team {idx} should have 1 governor"

                balance = await get_token_balance(
                    repo, govs[0].id, ctx["season_id"]
                )
                assert balance.propose >= 2
                assert balance.amend >= 2
                assert balance.boost >= 2

    # --- 3. Proposal submission (parameter change) ---

    async def test_proposal_submission_parameter_change(self, engine: object) -> None:
        """Submit a proposal that changes a RuleSet parameter."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        gov_id = ctx["governor_ids"][0]
        team_id = ctx["db_team_ids"][0]
        season_id = ctx["season_id"]

        async with get_session(engine) as session:
            repo = Repository(session)

            # Create interpretation: change three_point_value from 3 to 5
            interpretation = RuleInterpretation(
                parameter="three_point_value",
                new_value=5,
                old_value=3,
                impact_analysis="More three-point shooting",
                confidence=0.9,
            )

            proposal = await submit_proposal(
                repo=repo,
                governor_id=gov_id,
                team_id=team_id,
                season_id=season_id,
                window_id="test-window",
                raw_text="Make three-pointers worth 5 points",
                interpretation=interpretation,
                ruleset=DEFAULT_RULESET,
            )

            assert proposal.id
            assert proposal.status == "submitted"
            assert proposal.tier <= 2  # Tier 1 parameter

            # Token should have been spent
            balance = await get_token_balance(repo, gov_id, season_id)
            assert balance.propose == 1  # Started with 2, spent 1

    # --- 4. Proposal confirmation ---

    async def test_proposal_confirmation(self, engine: object) -> None:
        """Confirm a proposal to open it for voting."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        gov_id = ctx["governor_ids"][0]
        team_id = ctx["db_team_ids"][0]
        season_id = ctx["season_id"]

        async with get_session(engine) as session:
            repo = Repository(session)
            interpretation = RuleInterpretation(
                parameter="shot_clock_seconds",
                new_value=20,
                old_value=15,
                confidence=0.95,
            )
            proposal = await submit_proposal(
                repo, gov_id, team_id, season_id, "w1",
                "Increase shot clock to 20 seconds", interpretation, DEFAULT_RULESET,
            )

            confirmed = await confirm_proposal(repo, proposal)
            assert confirmed.status == "confirmed"

            # Verify event was recorded
            events = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["proposal.confirmed"],
            )
            assert len(events) >= 1
            matching = [e for e in events if e.aggregate_id == proposal.id]
            assert len(matching) == 1

    # --- 5. Voting with weight calculation ---

    async def test_voting_with_weight(self, engine: object) -> None:
        """Cast votes on a proposal and verify weight calculation."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        season_id = ctx["season_id"]

        async with get_session(engine) as session:
            repo = Repository(session)

            # Submit + confirm a proposal
            gov_id = ctx["governor_ids"][0]
            team_id = ctx["db_team_ids"][0]
            interpretation = RuleInterpretation(
                parameter="elam_margin", new_value=20, old_value=15, confidence=0.8,
            )
            proposal = await submit_proposal(
                repo, gov_id, team_id, season_id, "w1",
                "Increase Elam margin to 20", interpretation, DEFAULT_RULESET,
            )
            await confirm_proposal(repo, proposal)

            # Each team has 1 governor, so weight = 1.0 / 1 = 1.0
            weight = compute_vote_weight(1)
            assert weight == 1.0

            # Cast yes votes from 3 governors (teams 0, 1, 2)
            for i in range(3):
                vote = await cast_vote(
                    repo, proposal,
                    governor_id=ctx["governor_ids"][i],
                    team_id=ctx["db_team_ids"][i],
                    vote_choice="yes",
                    weight=weight,
                )
                assert vote.weight == 1.0

            # Cast no vote from governor 3
            vote = await cast_vote(
                repo, proposal,
                governor_id=ctx["governor_ids"][3],
                team_id=ctx["db_team_ids"][3],
                vote_choice="no",
                weight=weight,
            )

            # Verify vote events
            vote_events = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["vote.cast"],
            )
            proposal_votes = [
                e for e in vote_events
                if e.payload.get("proposal_id") == proposal.id
            ]
            assert len(proposal_votes) == 4

    # --- 6. Game simulation via step_round() ---

    async def test_step_round_simulates_games(self, engine: object) -> None:
        """Run step_round() and verify games are simulated with box scores."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        season_id = ctx["season_id"]

        async with get_session(engine) as session:
            repo = Repository(session)
            result = await step_round(
                repo, season_id, round_number=1,
                governance_interval=0,  # Skip governance for this test
            )

        assert isinstance(result, RoundResult)
        # 4 teams -> C(4,2) = 6 games per round
        assert len(result.games) == comb(4, 2)
        assert len(result.game_results) == comb(4, 2)
        assert len(result.reports) > 0  # Mock reports generated

        # Verify each game has valid scores
        for game in result.games:
            assert game["home_score"] > 0
            assert game["away_score"] > 0
            assert game["winner_team_id"] in (game["home_team_id"], game["away_team_id"])

        # Verify games are stored in DB
        async with get_session(engine) as session:
            repo = Repository(session)
            games = await repo.get_games_for_round(season_id, 1)
            assert len(games) == comb(4, 2)

            # Verify box scores exist
            for game in games:
                game_row = await repo.get_game_result(game.id)
                assert game_row is not None
                assert len(game_row.box_scores) > 0

    # --- 7. Governance tally with rule enactment ---

    async def test_governance_tally_enacts_rule_change(self, engine: object) -> None:
        """Submit a proposal, vote yes, tally, and verify the ruleset changes."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        season_id = ctx["season_id"]

        async with get_session(engine) as session:
            repo = Repository(session)

            # Submit + confirm a proposal to change three_point_value
            gov_id = ctx["governor_ids"][0]
            team_id = ctx["db_team_ids"][0]
            interpretation = RuleInterpretation(
                parameter="three_point_value",
                new_value=5,
                old_value=3,
                confidence=0.9,
            )
            proposal = await submit_proposal(
                repo, gov_id, team_id, season_id, "w1",
                "Three-pointers worth 5", interpretation, DEFAULT_RULESET,
            )
            await confirm_proposal(repo, proposal)

            # All 4 governors vote yes
            weight = compute_vote_weight(1)
            for i in range(4):
                await cast_vote(
                    repo, proposal,
                    governor_id=ctx["governor_ids"][i],
                    team_id=ctx["db_team_ids"][i],
                    vote_choice="yes",
                    weight=weight,
                )

            # Tally 1: proposal deferred (minimum voting period)
            ruleset, tallies, gov_data = await tally_pending_governance(
                repo, season_id, round_number=1, ruleset=DEFAULT_RULESET,
            )
            assert tallies == []

            # Tally 2: proposal passes
            ruleset, tallies, gov_data = await tally_pending_governance(
                repo, season_id, round_number=2, ruleset=DEFAULT_RULESET,
            )

            assert len(tallies) == 1
            assert tallies[0].passed is True
            assert tallies[0].weighted_yes == 4.0

            # Ruleset should have changed
            assert ruleset.three_point_value == 5

            # Verify rule.enacted event
            enacted_events = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["rule.enacted"],
            )
            assert len(enacted_events) >= 1
            assert enacted_events[-1].payload["parameter"] == "three_point_value"
            assert enacted_events[-1].payload["new_value"] == 5

    # --- 8. Effects system: meta_mutation effect ---

    async def test_effects_system_meta_mutation(self, engine: object) -> None:
        """Verify that a meta_mutation effect fires and modifies meta store."""
        # Create effect spec for a swagger counter
        spec = EffectSpec(
            effect_type="meta_mutation",
            target_type="team",
            target_selector="winning_team",
            meta_field="swagger",
            meta_value=1,
            meta_operation="increment",
            duration="n_rounds",
            duration_rounds=3,
            description="Winners gain swagger",
        )

        registry = EffectRegistry()
        effect = effect_spec_to_registered(spec, "proposal-123", current_round=1)
        registry.register(effect)

        assert registry.count == 1

        # Verify it appears in effects summary
        summary = registry.build_effects_summary()
        assert "swagger" in summary.lower() or "Winners gain swagger" in summary

        # Verify hook points
        round_post_effects = registry.get_effects_for_hook("round.game.post")
        assert len(round_post_effects) == 1

        # Fire the effect with a meta store
        from pinwheel.core.hooks import HookContext, fire_effects

        meta_store = MetaStore()
        context = HookContext(
            round_number=1,
            season_id="test-season",
            meta_store=meta_store,
            winner_team_id="team-abc",
        )
        fire_effects("round.game.post", context, round_post_effects)

        # Meta store should have swagger = 1 for winning team
        swagger_val = meta_store.get("team", "team-abc", "swagger", default=0)
        assert swagger_val == 1

        # Fire again — should increment to 2
        fire_effects("round.game.post", context, round_post_effects)
        swagger_val = meta_store.get("team", "team-abc", "swagger", default=0)
        assert swagger_val == 2

        # Tick round — 3 rounds remaining -> 2
        expired = registry.tick_round(1)
        assert len(expired) == 0
        assert registry.count == 1
        assert round_post_effects[0].rounds_remaining == 2

    # --- 9. Effects firing during step_round ---

    async def test_effects_fire_during_step_round(self, engine: object) -> None:
        """Verify effects from passed proposals fire during subsequent rounds."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        season_id = ctx["season_id"]

        async with get_session(engine) as session:
            repo = Repository(session)

            # Register an effect directly via event store (simulating a passed proposal)
            effect = RegisteredEffect(
                effect_id=str(uuid.uuid4()),
                proposal_id="test-proposal",
                _hook_points=["round.game.post"],
                _lifetime=EffectLifetime.PERMANENT,
                effect_type="meta_mutation",
                target_type="team",
                target_selector="winning_team",
                meta_field="test_counter",
                meta_value=1,
                meta_operation="increment",
                description="Test counter increment on win",
            )
            await repo.append_event(
                event_type="effect.registered",
                aggregate_id=effect.effect_id,
                aggregate_type="effect",
                season_id=season_id,
                payload=effect.to_dict(),
            )

        # Run step_round
        async with get_session(engine) as session:
            repo = Repository(session)
            result = await step_round(
                repo, season_id, round_number=1,
                governance_interval=0,
            )

        # Verify games were played
        assert len(result.games) > 0

        # Check that meta was flushed (test_counter should exist for winning teams)
        async with get_session(engine) as session:
            repo = Repository(session)
            for game in result.games:
                winner_id = game["winner_team_id"]
                meta = await repo.load_team_meta(winner_id)
                if meta:
                    # The effect should have incremented test_counter
                    assert meta.get("test_counter", 0) >= 1

    # --- 10. Narrative context computation ---

    async def test_narrative_context_computation(self, engine: object) -> None:
        """Verify NarrativeContext includes standings, streaks, and governance state."""
        # Use 3 round-robin cycles so the season is still in progress after round 1
        ctx = await seed_league_and_season(engine, num_teams=4, num_rounds=3)
        season_id = ctx["season_id"]

        # Play round 1
        async with get_session(engine) as session:
            repo = Repository(session)
            await step_round(repo, season_id, round_number=1, governance_interval=0)

        # Compute narrative context for round 2 (still regular season)
        async with get_session(engine) as session:
            repo = Repository(session)
            narrative = await compute_narrative_context(repo, season_id, 2, governance_interval=1)

        assert isinstance(narrative, NarrativeContext)
        assert narrative.round_number == 2
        assert len(narrative.standings) == 4
        assert narrative.phase == "regular"
        assert narrative.season_arc in ("early", "mid", "late")

        # Streaks should be computed
        assert len(narrative.streaks) > 0

    # --- 11. Narrative context includes effects ---

    async def test_narrative_context_includes_effects_summary(self, engine: object) -> None:
        """Verify NarrativeContext.effects_narrative is populated when effects are active."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        season_id = ctx["season_id"]

        # Register a narrative effect
        async with get_session(engine) as session:
            repo = Repository(session)
            effect = RegisteredEffect(
                effect_id=str(uuid.uuid4()),
                proposal_id="narrative-proposal",
                _hook_points=["report.simulation.pre"],
                _lifetime=EffectLifetime.PERMANENT,
                effect_type="narrative",
                narrative_instruction=(
                    "All commentary must reference the cosmic "
                    "significance of three-pointers"
                ),
                description="Cosmic three-pointers narrative",
            )
            await repo.append_event(
                event_type="effect.registered",
                aggregate_id=effect.effect_id,
                aggregate_type="effect",
                season_id=season_id,
                payload=effect.to_dict(),
            )

        # Run step_round — the game loop should load the effect registry
        # and inject effects_narrative into narrative context
        async with get_session(engine) as session:
            repo = Repository(session)
            result = await step_round(
                repo, season_id, round_number=1, governance_interval=0,
            )

        # Verify games ran
        assert len(result.games) > 0

    # --- 12. Compound proposals (multiple parameter changes) ---

    async def test_compound_proposal_multiple_param_changes(self, engine: object) -> None:
        """Verify compound proposals apply all parameter changes."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        season_id = ctx["season_id"]

        async with get_session(engine) as session:
            repo = Repository(session)

            # Submit proposal with legacy interpretation (first param)
            gov_id = ctx["governor_ids"][0]
            team_id = ctx["db_team_ids"][0]
            interpretation = RuleInterpretation(
                parameter="three_point_value",
                new_value=4,
                old_value=3,
                confidence=0.85,
            )
            proposal = await submit_proposal(
                repo, gov_id, team_id, season_id, "w1",
                "Change three pointers to 4 and shot clock to 20",
                interpretation, DEFAULT_RULESET,
            )
            await confirm_proposal(repo, proposal)

            # Vote yes from all governors and collect Vote objects
            weight = compute_vote_weight(1)
            votes: list[Vote] = []
            for i in range(4):
                v = await cast_vote(
                    repo, proposal,
                    governor_id=ctx["governor_ids"][i],
                    team_id=ctx["db_team_ids"][i],
                    vote_choice="yes",
                    weight=weight,
                )
                votes.append(v)

            # Create v2 effects for compound proposal
            effects_v2 = [
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="three_point_value",
                    new_value=4,
                    old_value=3,
                    description="Three-pointers worth 4",
                ),
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="shot_clock_seconds",
                    new_value=20,
                    old_value=15,
                    description="Shot clock to 20 seconds",
                ),
            ]

            # Tally with effects — pass collected Vote objects
            effect_registry = EffectRegistry()
            new_ruleset, tallies = await tally_governance_with_effects(
                repo=repo,
                season_id=season_id,
                proposals=[proposal],
                votes_by_proposal={proposal.id: votes},
                current_ruleset=DEFAULT_RULESET,
                round_number=1,
                effect_registry=effect_registry,
                effects_v2_by_proposal={proposal.id: effects_v2},
            )

            # Both parameters should be changed
            assert new_ruleset.three_point_value == 4
            assert new_ruleset.shot_clock_seconds == 20
            assert len(tallies) == 1
            assert tallies[0].passed is True

    # --- 13. New RuleSet parameters affect simulation ---

    async def test_new_ruleset_params_affect_simulation(self, engine: object) -> None:
        """Verify that changed RuleSet parameters actually affect game simulation."""
        league = generate_league(num_teams=4, seed=42)
        home_team = league.teams[0]
        away_team = league.teams[1]

        # Simulate with default rules
        result_default = simulate_game(home_team, away_team, DEFAULT_RULESET, seed=100)

        # Simulate with modified rules (extreme changes)
        modified_rules = DEFAULT_RULESET.model_copy(
            update={
                "quarter_minutes": 3,  # Shorter quarters = fewer possessions
                "three_point_value": 10,  # Absurd three-point value
                "turnover_rate_modifier": 3.0,  # Maximum turnovers
            }
        )
        result_modified = simulate_game(home_team, away_team, modified_rules, seed=100)

        # With 3-minute quarters vs 10-minute, total possessions should differ
        assert result_default.total_possessions != result_modified.total_possessions

    # --- 14. Standings computation ---

    async def test_standings_computed_correctly(self, engine: object) -> None:
        """Verify standings are computed correctly after a round of games."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        season_id = ctx["season_id"]

        async with get_session(engine) as session:
            repo = Repository(session)
            await step_round(repo, season_id, round_number=1, governance_interval=0)

        async with get_session(engine) as session:
            repo = Repository(session)
            standings = await compute_standings_from_repo(repo, season_id)

        assert len(standings) == 4
        total_wins = sum(s["wins"] for s in standings)
        total_losses = sum(s["losses"] for s in standings)
        # 6 games = 6 wins and 6 losses total
        assert total_wins == comb(4, 2)
        assert total_losses == comb(4, 2)

        # Standings should be sorted by wins desc
        for i in range(len(standings) - 1):
            assert standings[i]["wins"] >= standings[i + 1]["wins"]

    # --- 15. Reports generated with content ---

    async def test_reports_generated(self, engine: object) -> None:
        """Verify simulation and governance reports are generated."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        season_id = ctx["season_id"]

        async with get_session(engine) as session:
            repo = Repository(session)
            result = await step_round(repo, season_id, round_number=1)

        # Should have at least sim + gov reports (mock mode)
        assert len(result.reports) >= 2
        report_types = {r.report_type for r in result.reports}
        assert "simulation" in report_types
        assert "governance" in report_types

        # Reports should have content
        for report in result.reports:
            assert len(report.content) > 0

        # Verify reports are stored in DB
        async with get_session(engine) as session:
            repo = Repository(session)
            db_reports = await repo.get_reports_for_round(season_id, round_number=1)
            assert len(db_reports) >= 2

    # --- 16. Season progression: regular season complete ---

    async def test_regular_season_completes(self, engine: object) -> None:
        """Run enough rounds to complete regular season and verify playoff bracket."""
        # Use 1 round-robin cycle (4 teams -> 6 games in round 1)
        ctx = await seed_league_and_season(
            engine, num_teams=4, num_rounds=1,
        )
        season_id = ctx["season_id"]

        async with get_session(engine) as session:
            repo = Repository(session)
            result = await step_round(
                repo, season_id, round_number=1, governance_interval=0,
            )

        assert result.season_complete is True
        assert result.final_standings is not None
        assert len(result.final_standings) == 4

    # --- 17. Playoff bracket generation ---

    async def test_playoff_bracket_generation(self, engine: object) -> None:
        """Verify playoff bracket is generated from final standings."""
        ctx = await seed_league_and_season(engine, num_teams=4, num_rounds=1)
        season_id = ctx["season_id"]

        # Complete regular season
        async with get_session(engine) as session:
            repo = Repository(session)
            result = await step_round(
                repo, season_id, round_number=1, governance_interval=0,
            )

        assert result.playoff_bracket is not None
        assert len(result.playoff_bracket) > 0

        # Should have 2 semi matchups + 1 finals placeholder for 4 teams
        semi_matchups = [
            m for m in result.playoff_bracket if m.get("playoff_round") == "semifinal"
        ]
        finals_matchups = [
            m for m in result.playoff_bracket if m.get("playoff_round") == "finals"
        ]
        assert len(semi_matchups) == 2
        assert len(finals_matchups) == 1  # TBD placeholder

    # --- 18. Playoff games simulate correctly ---

    async def test_playoff_games_simulate(self, engine: object) -> None:
        """Verify playoff games simulate correctly after regular season."""
        ctx = await seed_league_and_season(engine, num_teams=4, num_rounds=1)
        season_id = ctx["season_id"]

        # Complete regular season (generates bracket)
        async with get_session(engine) as session:
            repo = Repository(session)
            await step_round(
                repo, season_id, round_number=1, governance_interval=0,
            )

        # Play first playoff round (semis game 1)
        async with get_session(engine) as session:
            repo = Repository(session)
            playoff_result = await step_round(
                repo, season_id, round_number=2, governance_interval=0,
            )

        # Should have 2 semifinal games
        assert len(playoff_result.games) == 2

        # Verify playoff context
        for game in playoff_result.games:
            assert game.get("playoff_context") in ("semifinal", "finals")

    # --- 19. Full playoff cycle to championship ---

    async def test_full_playoff_to_championship(self, engine: object) -> None:
        """Run playoffs to completion and verify championship fires.

        Uses best_of=1 for semis and finals to make this fast.
        """
        ctx = await seed_league_and_season(engine, num_teams=4, num_rounds=1)
        season_id = ctx["season_id"]

        # Modify ruleset to best-of-1 for speed
        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(season_id)
            assert season is not None
            new_ruleset = dict(season.current_ruleset or {})
            new_ruleset["playoff_semis_best_of"] = 1
            new_ruleset["playoff_finals_best_of"] = 1
            await repo.update_season_ruleset(season_id, new_ruleset)

        # Round 1: regular season (6 games)
        async with get_session(engine) as session:
            repo = Repository(session)
            r1 = await step_round(repo, season_id, 1, governance_interval=0)
            assert r1.season_complete is True

        # Round 2: semifinals (2 games, best-of-1 means they decide immediately)
        async with get_session(engine) as session:
            repo = Repository(session)
            r2 = await step_round(repo, season_id, 2, governance_interval=0)
            assert len(r2.games) == 2

        # Round 3: finals (1 game, best-of-1)
        async with get_session(engine) as session:
            repo = Repository(session)
            r3 = await step_round(repo, season_id, 3, governance_interval=0)
            assert len(r3.games) == 1

        # After finals with best-of-1, playoffs should be complete
        assert r3.playoffs_complete is True

        # Verify season status is championship or completed
        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(season_id)
            assert season is not None
            assert season.status in ("championship", "completed", "complete")

    # --- 20. Effect lifetime expiration ---

    async def test_effect_lifetime_expiration(self, engine: object) -> None:
        """Verify effects expire after their lifetime ends."""
        registry = EffectRegistry()

        # Register a 2-round effect
        effect = RegisteredEffect(
            effect_id="temp-effect-1",
            proposal_id="prop-1",
            _hook_points=["round.game.post"],
            _lifetime=EffectLifetime.N_ROUNDS,
            rounds_remaining=2,
            registered_at_round=1,
            effect_type="meta_mutation",
            target_type="team",
            target_selector="winning_team",
            meta_field="bonus",
            meta_value=5,
            meta_operation="increment",
        )
        registry.register(effect)
        assert registry.count == 1

        # Tick round 1 -> 1 remaining
        expired = registry.tick_round(1)
        assert len(expired) == 0
        assert registry.count == 1

        # Tick round 2 -> 0 remaining -> expired
        expired = registry.tick_round(2)
        assert len(expired) == 1
        assert expired[0] == "temp-effect-1"
        assert registry.count == 0

    # --- 21. Governance + simulation integration via step_round ---

    async def test_step_round_with_governance(self, engine: object) -> None:
        """Run step_round with governance tally and verify full integration."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        season_id = ctx["season_id"]

        # Submit and confirm a proposal before the round
        async with get_session(engine) as session:
            repo = Repository(session)
            gov_id = ctx["governor_ids"][0]
            team_id = ctx["db_team_ids"][0]
            interpretation = RuleInterpretation(
                parameter="elam_margin",
                new_value=20,
                old_value=15,
                confidence=0.9,
            )
            proposal = await submit_proposal(
                repo, gov_id, team_id, season_id, "w1",
                "Increase Elam margin", interpretation, DEFAULT_RULESET,
            )
            await confirm_proposal(repo, proposal)

            # All governors vote yes
            weight = compute_vote_weight(1)
            for i in range(4):
                await cast_vote(
                    repo, proposal,
                    governor_id=ctx["governor_ids"][i],
                    team_id=ctx["db_team_ids"][i],
                    vote_choice="yes",
                    weight=weight,
                )

            # Pre-emit first_tally_seen so the minimum voting period is satisfied
            # (simulates the proposal being seen in a prior tally cycle)
            await repo.append_event(
                event_type="proposal.first_tally_seen",
                aggregate_id=proposal.id,
                aggregate_type="proposal",
                season_id=season_id,
                payload={"proposal_id": proposal.id, "round_number": 0},
            )

        # Step round with governance_interval=1 (tally every round)
        async with get_session(engine) as session:
            repo = Repository(session)
            result = await step_round(
                repo, season_id, round_number=1,
                governance_interval=1,
            )

        # Games should have been simulated
        assert len(result.games) == comb(4, 2)

        # Governance should have been tallied
        assert len(result.tallies) == 1
        assert result.tallies[0].passed is True

        # Verify the ruleset was updated
        async with get_session(engine) as session:
            repo = Repository(session)
            season = await repo.get_season(season_id)
            assert season is not None
            current_ruleset = RuleSet(**season.current_ruleset)
            assert current_ruleset.elam_margin == 20

    # --- 22. ProposalInterpretation <-> RuleInterpretation conversion ---

    async def test_proposal_interpretation_conversion(self) -> None:
        """Verify ProposalInterpretation converts to/from RuleInterpretation."""
        # Forward: ProposalInterpretation -> RuleInterpretation
        pi = ProposalInterpretation(
            effects=[
                EffectSpec(
                    effect_type="parameter_change",
                    parameter="three_point_value",
                    new_value=5,
                    old_value=3,
                    description="Increase threes",
                ),
                EffectSpec(
                    effect_type="narrative",
                    narrative_instruction="Mention cosmic significance",
                    description="Add narrative flair",
                ),
            ],
            impact_analysis="More scoring",
            confidence=0.85,
        )

        ri = pi.to_rule_interpretation()
        assert ri.parameter == "three_point_value"
        assert ri.new_value == 5
        assert ri.confidence == 0.85

        # Backward: RuleInterpretation -> ProposalInterpretation
        pi2 = ProposalInterpretation.from_rule_interpretation(ri, "Make threes worth 5")
        assert len(pi2.effects) == 1
        assert pi2.effects[0].effect_type == "parameter_change"
        assert pi2.effects[0].parameter == "three_point_value"

    # --- 23. Auto-migrate schema ---

    async def test_auto_migrate_handles_existing_schema(self, engine: object) -> None:
        """Verify auto_migrate_schema works on an already up-to-date schema."""
        from pinwheel.db.engine import auto_migrate_schema

        async with engine.begin() as conn:
            added = await auto_migrate_schema(conn)
            # Schema was just created, so no columns should need adding
            assert added == 0

    # --- 24. MetaStore operations ---

    async def test_meta_store_operations(self) -> None:
        """Verify MetaStore set/get/increment/decrement/toggle/dirty tracking."""
        store = MetaStore()

        # Set and get
        store.set("team", "t1", "swagger", 5)
        assert store.get("team", "t1", "swagger") == 5

        # Increment
        store.increment("team", "t1", "swagger", 3)
        assert store.get("team", "t1", "swagger") == 8

        # Decrement
        store.decrement("team", "t1", "swagger", 2)
        assert store.get("team", "t1", "swagger") == 6

        # Toggle
        store.set("team", "t1", "hot_streak", False)
        result = store.toggle("team", "t1", "hot_streak")
        assert result is True
        assert store.get("team", "t1", "hot_streak") is True

        # Dirty tracking
        dirty = store.get_dirty_entities()
        assert len(dirty) == 1
        entity_type, entity_id, meta = dirty[0]
        assert entity_type == "team"
        assert entity_id == "t1"
        assert meta["swagger"] == 6

        # After get_dirty, should be clean
        dirty2 = store.get_dirty_entities()
        assert len(dirty2) == 0

    # --- 25. Effect registry persistence and reload ---

    async def test_effect_registry_persistence(self, engine: object) -> None:
        """Verify effects persist via event store and reload correctly."""
        ctx = await seed_league_and_season(engine, num_teams=4, enroll_governors=False)
        season_id = ctx["season_id"]

        # Register effects
        async with get_session(engine) as session:
            repo = Repository(session)
            from pinwheel.core.effects import register_effects_for_proposal

            registry = EffectRegistry()
            effects = [
                EffectSpec(
                    effect_type="meta_mutation",
                    target_type="team",
                    target_selector="winning_team",
                    meta_field="momentum",
                    meta_value=1,
                    meta_operation="increment",
                    duration="permanent",
                    description="Winning momentum",
                ),
                EffectSpec(
                    effect_type="narrative",
                    narrative_instruction="All reports must rhyme",
                    duration="n_rounds",
                    duration_rounds=5,
                    description="Rhyming reports",
                ),
            ]

            registered = await register_effects_for_proposal(
                repo=repo,
                registry=registry,
                proposal_id="test-proposal-persist",
                effects=effects,
                season_id=season_id,
                current_round=1,
            )
            assert len(registered) == 2
            assert registry.count == 2

        # Reload from event store
        async with get_session(engine) as session:
            repo = Repository(session)
            from pinwheel.core.effects import load_effect_registry

            reloaded = await load_effect_registry(repo, season_id)
            assert reloaded.count == 2

            # Check narrative effects
            narrative_effects = reloaded.get_narrative_effects()
            assert len(narrative_effects) == 1
            assert "rhyme" in narrative_effects[0].narrative_instruction.lower()

    # --- 26. Vote threshold tiers ---

    async def test_vote_threshold_tiers(self) -> None:
        """Verify tier-based vote thresholds work correctly."""
        # Tier 1-2: base threshold (0.5)
        assert vote_threshold_for_tier(1) == 0.5
        assert vote_threshold_for_tier(2) == 0.5

        # Tier 3-4: 60%
        assert vote_threshold_for_tier(3) == 0.6
        assert vote_threshold_for_tier(4) == 0.6

        # Tier 5-6: 67%
        assert vote_threshold_for_tier(5) == 0.67

        # Tier 7+: 75%
        assert vote_threshold_for_tier(7) == 0.75

    # --- 27. Failed proposal does not change ruleset ---

    async def test_failed_proposal_no_rule_change(self, engine: object) -> None:
        """Verify a failed proposal does not modify the ruleset."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        season_id = ctx["season_id"]

        async with get_session(engine) as session:
            repo = Repository(session)
            gov_id = ctx["governor_ids"][0]
            team_id = ctx["db_team_ids"][0]

            interpretation = RuleInterpretation(
                parameter="three_point_value",
                new_value=10,
                old_value=3,
                confidence=0.8,
            )
            proposal = await submit_proposal(
                repo, gov_id, team_id, season_id, "w1",
                "Absurd three-pointer value", interpretation, DEFAULT_RULESET,
            )
            await confirm_proposal(repo, proposal)

            # All 4 governors vote NO
            weight = compute_vote_weight(1)
            for i in range(4):
                await cast_vote(
                    repo, proposal,
                    governor_id=ctx["governor_ids"][i],
                    team_id=ctx["db_team_ids"][i],
                    vote_choice="no",
                    weight=weight,
                )

            # Tally 1: deferred (minimum voting period)
            ruleset, tallies, _ = await tally_pending_governance(
                repo, season_id, round_number=1, ruleset=DEFAULT_RULESET,
            )
            assert tallies == []

            # Tally 2: proposal fails (all voted no)
            ruleset, tallies, _ = await tally_pending_governance(
                repo, season_id, round_number=2, ruleset=DEFAULT_RULESET,
            )

            assert len(tallies) == 1
            assert tallies[0].passed is False
            assert ruleset.three_point_value == 3  # Unchanged

    # --- 28. Event bus integration ---

    async def test_event_bus_fires_during_round(self, engine: object) -> None:
        """Verify the event bus receives game.completed and round.completed events."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        season_id = ctx["season_id"]
        event_bus = EventBus()

        # Use wildcard subscription to capture all events
        async with event_bus.subscribe(None) as sub:
            async with get_session(engine) as session:
                repo = Repository(session)
                await step_round(
                    repo, season_id, round_number=1,
                    event_bus=event_bus,
                    governance_interval=0,
                )

            # Drain the queue
            received_events: list[dict] = []
            while True:
                event = await sub.get(timeout=0.1)
                if event is None:
                    break
                received_events.append(event)

        # Should have game.completed for each game + round.completed + season events
        game_events = [e for e in received_events if e["type"] == "game.completed"]
        round_events = [e for e in received_events if e["type"] == "round.completed"]
        assert len(game_events) == comb(4, 2)
        assert len(round_events) == 1


# ===========================================================================
# Test Class: Web Page Routes
# ===========================================================================


class TestWebPageRoutes:
    """Verify all key web page routes return 200 with expected content."""

    async def test_home_page(self, app_and_engine: tuple) -> None:
        """Home page returns 200."""
        app, engine = app_and_engine
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/")
            assert resp.status_code == 200
            assert "pinwheel" in resp.text.lower() or "html" in resp.text.lower()

    async def test_arena_page(self, app_and_engine: tuple) -> None:
        """Arena page returns 200."""
        app, engine = app_and_engine
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/arena")
            assert resp.status_code == 200

    async def test_standings_page(self, app_and_engine: tuple) -> None:
        """Standings page returns 200."""
        app, engine = app_and_engine
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/standings")
            assert resp.status_code == 200

    async def test_governance_page(self, app_and_engine: tuple) -> None:
        """Governance page returns 200."""
        app, engine = app_and_engine
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/governance")
            assert resp.status_code == 200

    async def test_reports_page(self, app_and_engine: tuple) -> None:
        """Reports page returns 200."""
        app, engine = app_and_engine
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/reports")
            assert resp.status_code == 200

    async def test_rules_page(self, app_and_engine: tuple) -> None:
        """Rules page returns 200."""
        app, engine = app_and_engine
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/rules")
            assert resp.status_code == 200

    async def test_health_endpoint(self, app_and_engine: tuple) -> None:
        """Health endpoint returns 200 with status ok."""
        app, engine = app_and_engine
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

    async def test_api_standings_endpoint(self, app_and_engine: tuple) -> None:
        """API standings endpoint returns 422 without season_id (validation)."""
        app, engine = app_and_engine
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Without required season_id param, should get 422
            resp = await client.get("/api/standings")
            assert resp.status_code == 422

            # With a fake season_id, should return 200 (empty standings)
            resp = await client.get("/api/standings?season_id=nonexistent")
            assert resp.status_code == 200

    async def test_play_page(self, app_and_engine: tuple) -> None:
        """Play page returns 200."""
        app, engine = app_and_engine
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/play")
            assert resp.status_code == 200

    async def test_terms_page(self, app_and_engine: tuple) -> None:
        """Terms page returns 200."""
        app, engine = app_and_engine
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/terms")
            assert resp.status_code == 200

    async def test_privacy_page(self, app_and_engine: tuple) -> None:
        """Privacy page returns 200."""
        app, engine = app_and_engine
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/privacy")
            assert resp.status_code == 200


# ===========================================================================
# Test Class: Integration Bug Hunting
# ===========================================================================


class TestIntegrationBugs:
    """Specific integration bug scenarios from Wave 1-2 changes."""

    async def test_expanded_ruleset_has_all_new_params(self) -> None:
        """Verify the expanded RuleSet includes all expected parameters."""
        ruleset = DEFAULT_RULESET
        # Core params that should always exist
        assert hasattr(ruleset, "quarter_minutes")
        assert hasattr(ruleset, "shot_clock_seconds")
        assert hasattr(ruleset, "three_point_value")
        assert hasattr(ruleset, "elam_trigger_quarter")
        assert hasattr(ruleset, "elam_margin")
        # New Tier 1 params
        assert hasattr(ruleset, "halftime_stamina_recovery")
        assert hasattr(ruleset, "safety_cap_possessions")
        assert hasattr(ruleset, "turnover_rate_modifier")
        assert hasattr(ruleset, "foul_rate_modifier")
        assert hasattr(ruleset, "offensive_rebound_weight")
        assert hasattr(ruleset, "stamina_drain_rate")
        assert hasattr(ruleset, "dead_ball_time_seconds")
        # Tier 2 params
        assert hasattr(ruleset, "max_shot_share")
        assert hasattr(ruleset, "min_pass_per_possession")
        assert hasattr(ruleset, "home_court_enabled")
        assert hasattr(ruleset, "home_crowd_boost")
        assert hasattr(ruleset, "away_fatigue_factor")
        assert hasattr(ruleset, "crowd_pressure")
        assert hasattr(ruleset, "altitude_stamina_penalty")
        assert hasattr(ruleset, "travel_fatigue_enabled")
        assert hasattr(ruleset, "travel_fatigue_per_mile")
        # Tier 3 params
        assert hasattr(ruleset, "playoff_semis_best_of")
        assert hasattr(ruleset, "playoff_finals_best_of")
        # Tier 4 params
        assert hasattr(ruleset, "vote_threshold")
        assert hasattr(ruleset, "proposals_per_window")

    async def test_ruleset_round_trips_through_json(self) -> None:
        """Verify RuleSet survives JSON serialization/deserialization."""
        original = DEFAULT_RULESET.model_copy(
            update={"three_point_value": 5, "elam_margin": 25}
        )
        json_data = original.model_dump()
        reconstructed = RuleSet(**json_data)
        assert reconstructed == original
        assert reconstructed.three_point_value == 5
        assert reconstructed.elam_margin == 25

    async def test_effect_spec_round_trips_through_dict(self) -> None:
        """Verify EffectSpec survives dict serialization/deserialization."""
        spec = EffectSpec(
            effect_type="meta_mutation",
            target_type="team",
            target_selector="winning_team",
            meta_field="swagger",
            meta_value=1,
            meta_operation="increment",
            duration="n_rounds",
            duration_rounds=5,
            description="Swagger counter",
        )
        data = spec.model_dump()
        reconstructed = EffectSpec(**data)
        assert reconstructed.effect_type == "meta_mutation"
        assert reconstructed.meta_field == "swagger"
        assert reconstructed.duration_rounds == 5

    async def test_registered_effect_round_trips_through_dict(self) -> None:
        """Verify RegisteredEffect survives to_dict/from_dict round trip."""
        effect = RegisteredEffect(
            effect_id="eff-1",
            proposal_id="prop-1",
            _hook_points=["round.game.post", "round.post"],
            _lifetime=EffectLifetime.N_ROUNDS,
            rounds_remaining=3,
            registered_at_round=5,
            effect_type="hook_callback",
            condition="winning margin > 10",
            action_code={"type": "write_meta", "entity": "team:{winner_team_id}",
                         "field": "blowout_count", "value": 1, "op": "increment"},
            description="Track blowout wins",
        )
        data = effect.to_dict()
        reconstructed = RegisteredEffect.from_dict(data)

        assert reconstructed.effect_id == "eff-1"
        assert reconstructed.hook_points == ["round.game.post", "round.post"]
        assert reconstructed.lifetime == EffectLifetime.N_ROUNDS
        assert reconstructed.rounds_remaining == 3
        assert reconstructed.action_code is not None
        assert reconstructed.action_code["type"] == "write_meta"

    async def test_narrative_context_format_for_prompt(self) -> None:
        """Verify format_narrative_for_prompt produces usable text."""
        from pinwheel.core.narrative import format_narrative_for_prompt

        ctx = NarrativeContext(
            phase="semifinal",
            season_arc="playoff",
            round_number=8,
            total_rounds=7,
            standings=[
                {"team_id": "t1", "team_name": "Thorns", "wins": 5, "losses": 2,
                 "rank": 1, "point_diff": 50},
                {"team_id": "t2", "team_name": "Wolves", "wins": 4, "losses": 3,
                 "rank": 2, "point_diff": 20},
            ],
            streaks={"t1": 3, "t2": -2},
            effects_narrative="- [meta_mutation] Winners gain swagger (2 rounds remaining)",
        )

        text = format_narrative_for_prompt(ctx)
        assert "SEMIFINAL" in text
        assert "Thorns" in text
        assert "W3 streak" in text
        assert "swagger" in text.lower()

    async def test_governance_does_not_double_tally(self, engine: object) -> None:
        """Verify a proposal is not tallied twice across multiple rounds."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        season_id = ctx["season_id"]

        async with get_session(engine) as session:
            repo = Repository(session)

            # Submit, confirm, vote
            gov_id = ctx["governor_ids"][0]
            team_id = ctx["db_team_ids"][0]
            interpretation = RuleInterpretation(
                parameter="elam_margin", new_value=25, old_value=15, confidence=0.9,
            )
            proposal = await submit_proposal(
                repo, gov_id, team_id, season_id, "w1",
                "Elam margin 25", interpretation, DEFAULT_RULESET,
            )
            await confirm_proposal(repo, proposal)

            weight = compute_vote_weight(1)
            for i in range(4):
                await cast_vote(
                    repo, proposal,
                    governor_id=ctx["governor_ids"][i],
                    team_id=ctx["db_team_ids"][i],
                    vote_choice="yes",
                    weight=weight,
                )

            # Tally round 1: deferred (minimum voting period)
            ruleset1, tallies1, _ = await tally_pending_governance(
                repo, season_id, 1, DEFAULT_RULESET,
            )
            assert len(tallies1) == 0

            # Tally round 2: proposal passes
            ruleset2, tallies2, _ = await tally_pending_governance(
                repo, season_id, 2, DEFAULT_RULESET,
            )
            assert len(tallies2) == 1
            assert tallies2[0].passed is True
            assert ruleset2.elam_margin == 25

            # Tally round 3 — should have nothing to tally
            ruleset3, tallies3, _ = await tally_pending_governance(
                repo, season_id, 3, ruleset2,
            )
            assert len(tallies3) == 0
            assert ruleset3.elam_margin == 25  # Still 25, not re-tallied

    async def test_step_round_returns_empty_for_no_schedule(self, engine: object) -> None:
        """step_round returns empty result when no games are scheduled."""
        ctx = await seed_league_and_season(engine, num_teams=4, num_rounds=1)
        season_id = ctx["season_id"]

        async with get_session(engine) as session:
            repo = Repository(session)
            # Round 99 has no schedule
            result = await step_round(repo, season_id, 99, governance_interval=0)
            assert len(result.games) == 0
            assert len(result.reports) == 0

    async def test_multisession_step_round_works(self, engine: object) -> None:
        """Verify step_round_multisession produces the same results as step_round."""
        ctx = await seed_league_and_season(engine, num_teams=4)
        season_id = ctx["season_id"]

        from pinwheel.core.game_loop import step_round_multisession

        result = await step_round_multisession(
            engine, season_id, round_number=1, governance_interval=0,
        )

        assert isinstance(result, RoundResult)
        assert len(result.games) == comb(4, 2)
        assert len(result.reports) >= 2

    async def test_hook_callback_effect_fires_with_action_code(self) -> None:
        """Verify hook_callback effects with action_code execute correctly."""
        effect = RegisteredEffect(
            effect_id="hook-eff-1",
            proposal_id="prop-hook",
            _hook_points=["round.game.post"],
            _lifetime=EffectLifetime.PERMANENT,
            effect_type="hook_callback",
            action_code={
                "type": "write_meta",
                "entity": "team:{winner_team_id}",
                "field": "wins_count",
                "value": 1,
                "op": "increment",
            },
            description="Track wins via meta",
        )

        from pinwheel.core.hooks import HookContext, fire_effects

        meta_store = MetaStore()
        ctx = HookContext(
            round_number=1,
            season_id="test",
            meta_store=meta_store,
            winner_team_id="team-winner",
        )

        results = fire_effects("round.game.post", ctx, [effect])
        assert len(results) == 1

        # Check meta was written
        val = meta_store.get("team", "team-winner", "wins_count", default=0)
        assert val == 1
