"""Tests for GQI and Rule Evaluator wiring into the game loop's _run_evals().

Verifies that compute_gqi/store_gqi and evaluate_rules/store_rule_evaluation
are called during the eval step, producing stored results that the dashboard
can read.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from pinwheel.core.game_loop import _run_evals, step_round
from pinwheel.core.scheduler import generate_round_robin
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository
from pinwheel.models.report import Report


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


def _hooper_attrs() -> dict:
    return {
        "scoring": 50,
        "passing": 40,
        "defense": 35,
        "speed": 45,
        "stamina": 40,
        "iq": 50,
        "ego": 30,
        "chaotic_alignment": 40,
        "fate": 30,
    }


async def _setup_season_with_teams(
    repo: Repository, num_rounds: int = 1
) -> tuple[str, list[str]]:
    """Create a league, season, 4 teams with 4 hoopers each, and a schedule."""
    league = await repo.create_league("Test League")
    season = await repo.create_season(
        league.id,
        "Season 1",
        starting_ruleset={"quarter_minutes": 3},
    )

    team_ids = []
    for i in range(4):
        team = await repo.create_team(
            season.id,
            f"Team {i + 1}",
            venue={"name": f"Arena {i + 1}", "capacity": 5000},
        )
        team_ids.append(team.id)
        for j in range(4):
            await repo.create_hooper(
                team_id=team.id,
                season_id=season.id,
                name=f"Hooper-{i + 1}-{j + 1}",
                archetype="sharpshooter",
                attributes=_hooper_attrs(),
            )

    matchups = generate_round_robin(team_ids, num_rounds=num_rounds)
    for m in matchups:
        await repo.create_schedule_entry(
            season_id=season.id,
            round_number=m.round_number,
            matchup_index=m.matchup_index,
            home_team_id=m.home_team_id,
            away_team_id=m.away_team_id,
        )

    return season.id, team_ids


class TestGQIWiring:
    """Verify GQI computation is wired into _run_evals and produces stored results."""

    async def test_gqi_stored_after_run_evals(self, repo: Repository):
        """_run_evals() stores a GQI eval result."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # Build minimal inputs for _run_evals
        reports = [
            Report(
                id="r-sim-1",
                report_type="simulation",
                content="Team 1 beat Team 2 in a close game.",
                season_id=season_id,
                round_number=1,
            ),
        ]
        game_summaries = [
            {
                "game_id": "g-1-0",
                "home_team": "Team 1",
                "away_team": "Team 2",
                "home_team_id": team_ids[0],
                "away_team_id": team_ids[1],
                "home_score": 21,
                "away_score": 18,
                "winner_team_id": team_ids[0],
            },
        ]

        # Load teams into cache
        from pinwheel.core.game_loop import _row_to_team

        teams_cache = {}
        for tid in team_ids:
            row = await repo.get_team(tid)
            if row:
                teams_cache[tid] = _row_to_team(row)

        await _run_evals(
            repo, season_id, 1, reports, game_summaries, teams_cache,
        )

        # GQI result should be stored
        gqi_results = await repo.get_eval_results(season_id, eval_type="gqi")
        assert len(gqi_results) == 1
        assert gqi_results[0].round_number == 1
        assert gqi_results[0].details_json is not None
        assert "composite" in gqi_results[0].details_json
        assert "proposal_diversity" in gqi_results[0].details_json

    async def test_gqi_stored_via_step_round(self, repo: Repository):
        """step_round() triggers _run_evals which stores GQI results."""
        season_id, _ = await _setup_season_with_teams(repo)

        await step_round(repo, season_id, round_number=1)

        gqi_results = await repo.get_eval_results(season_id, eval_type="gqi")
        assert len(gqi_results) == 1
        details = gqi_results[0].details_json
        assert details is not None
        assert "composite" in details
        assert "proposal_diversity" in details
        assert "participation_breadth" in details
        assert "consequence_awareness" in details
        assert "vote_deliberation" in details

    async def test_gqi_composite_is_score(self, repo: Repository):
        """GQI composite value is stored as the eval_result score."""
        season_id, _ = await _setup_season_with_teams(repo)

        await step_round(repo, season_id, round_number=1)

        gqi_results = await repo.get_eval_results(season_id, eval_type="gqi")
        assert len(gqi_results) == 1
        composite = gqi_results[0].details_json.get("composite", -1)
        assert gqi_results[0].score == pytest.approx(composite)

    async def test_gqi_stored_each_round(self, repo: Repository):
        """GQI is computed and stored for each round independently."""
        season_id, _ = await _setup_season_with_teams(repo, num_rounds=2)

        await step_round(repo, season_id, round_number=1)
        await step_round(repo, season_id, round_number=2)

        gqi_results = await repo.get_eval_results(season_id, eval_type="gqi")
        assert len(gqi_results) == 2
        rounds = {r.round_number for r in gqi_results}
        assert rounds == {1, 2}


class TestRuleEvaluatorWiring:
    """Verify Rule Evaluator is wired into _run_evals and produces stored results."""

    async def test_rule_eval_stored_after_run_evals(self, repo: Repository):
        """_run_evals() stores a rule_evaluation eval result (mock, no API key)."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        reports = [
            Report(
                id="r-sim-1",
                report_type="simulation",
                content="Team 1 beat Team 2.",
                season_id=season_id,
                round_number=1,
            ),
        ]
        game_summaries = [
            {
                "game_id": "g-1-0",
                "home_team": "Team 1",
                "away_team": "Team 2",
                "home_team_id": team_ids[0],
                "away_team_id": team_ids[1],
                "home_score": 21,
                "away_score": 18,
                "winner_team_id": team_ids[0],
            },
        ]

        from pinwheel.core.game_loop import _row_to_team

        teams_cache = {}
        for tid in team_ids:
            row = await repo.get_team(tid)
            if row:
                teams_cache[tid] = _row_to_team(row)

        await _run_evals(
            repo, season_id, 1, reports, game_summaries, teams_cache,
            api_key="",
        )

        rule_results = await repo.get_eval_results(season_id, eval_type="rule_evaluation")
        assert len(rule_results) == 1
        assert rule_results[0].round_number == 1
        details = rule_results[0].details_json
        assert details is not None
        assert "suggested_experiments" in details
        assert "stale_parameters" in details
        assert "equilibrium_notes" in details

    async def test_rule_eval_stored_via_step_round(self, repo: Repository):
        """step_round() triggers _run_evals which stores rule evaluation results."""
        season_id, _ = await _setup_season_with_teams(repo)

        await step_round(repo, season_id, round_number=1)

        rule_results = await repo.get_eval_results(
            season_id, eval_type="rule_evaluation"
        )
        assert len(rule_results) == 1
        details = rule_results[0].details_json
        assert details is not None
        assert "suggested_experiments" in details
        assert len(details["suggested_experiments"]) > 0

    async def test_rule_eval_mock_without_api_key(self, repo: Repository):
        """Without API key, rule evaluator uses mock and still stores results."""
        season_id, _ = await _setup_season_with_teams(repo)

        # step_round with no api_key
        await step_round(repo, season_id, round_number=1, api_key="")

        rule_results = await repo.get_eval_results(
            season_id, eval_type="rule_evaluation"
        )
        assert len(rule_results) == 1
        details = rule_results[0].details_json
        # Mock always returns specific experiment suggestions
        assert any("elam" in exp.lower() for exp in details.get("suggested_experiments", []))

    async def test_rule_eval_stored_each_round(self, repo: Repository):
        """Rule evaluation is computed and stored for each round independently."""
        season_id, _ = await _setup_season_with_teams(repo, num_rounds=2)

        await step_round(repo, season_id, round_number=1)
        await step_round(repo, season_id, round_number=2)

        rule_results = await repo.get_eval_results(
            season_id, eval_type="rule_evaluation"
        )
        assert len(rule_results) == 2
        rounds = {r.round_number for r in rule_results}
        assert rounds == {1, 2}


class TestDashboardDataPopulated:
    """Verify the eval dashboard can read the data produced by the wired evals."""

    async def test_dashboard_gqi_trend_populated(self, repo: Repository):
        """After step_round, GQI trend data is available for dashboard queries."""
        season_id, _ = await _setup_season_with_teams(repo, num_rounds=3)

        for rnd in range(1, 4):
            await step_round(repo, season_id, round_number=rnd)

        # Simulate what the dashboard does (eval_dashboard.py lines 111-124)
        gqi_results = await repo.get_eval_results(season_id, eval_type="gqi")
        assert len(gqi_results) == 3

        gqi_trend = []
        for r in sorted(gqi_results, key=lambda x: x.round_number)[-5:]:
            details = r.details_json or {}
            gqi_trend.append(
                {
                    "round": r.round_number,
                    "composite": details.get("composite", 0.0),
                    "diversity": details.get("proposal_diversity", 0.0),
                    "breadth": details.get("participation_breadth", 0.0),
                    "awareness": details.get("consequence_awareness", 0.0),
                    "deliberation": details.get("vote_deliberation", 0.0),
                }
            )

        assert len(gqi_trend) == 3
        assert gqi_trend[0]["round"] == 1
        assert gqi_trend[2]["round"] == 3

    async def test_dashboard_rule_eval_populated(self, repo: Repository):
        """After step_round, latest rule evaluation is available for dashboard."""
        season_id, _ = await _setup_season_with_teams(repo)

        await step_round(repo, season_id, round_number=1)

        # Simulate what the dashboard does (eval_dashboard.py lines 145-155)
        rule_eval_results = await repo.get_eval_results(
            season_id, eval_type="rule_evaluation"
        )
        assert len(rule_eval_results) >= 1

        details = rule_eval_results[0].details_json or {}
        latest_rule_eval = {
            "round": rule_eval_results[0].round_number,
            "experiments": details.get("suggested_experiments", []),
            "stale_params": details.get("stale_parameters", []),
            "equilibrium": details.get("equilibrium_notes", ""),
            "concerns": details.get("flagged_concerns", []),
        }

        assert latest_rule_eval["round"] == 1
        assert len(latest_rule_eval["experiments"]) > 0
        assert latest_rule_eval["equilibrium"] != ""


class TestEvalWiringRobustness:
    """Test that eval failures do not break the game loop."""

    async def test_evals_disabled_skips_gqi_and_rule_eval(self, repo: Repository):
        """When PINWHEEL_EVALS_ENABLED=false, no eval results are stored."""
        import os

        season_id, _ = await _setup_season_with_teams(repo)

        # Temporarily set evals disabled
        original = os.environ.get("PINWHEEL_EVALS_ENABLED")
        os.environ["PINWHEEL_EVALS_ENABLED"] = "false"
        try:
            await step_round(repo, season_id, round_number=1)
        finally:
            if original is not None:
                os.environ["PINWHEEL_EVALS_ENABLED"] = original
            else:
                os.environ.pop("PINWHEEL_EVALS_ENABLED", None)

        # No eval results should be stored
        gqi_results = await repo.get_eval_results(season_id, eval_type="gqi")
        rule_results = await repo.get_eval_results(
            season_id, eval_type="rule_evaluation"
        )
        assert len(gqi_results) == 0
        assert len(rule_results) == 0

    async def test_api_key_passed_through_to_run_evals(self, repo: Repository):
        """api_key from step_round is threaded through to _run_evals for rule_evaluator."""
        season_id, team_ids = await _setup_season_with_teams(repo)

        # With empty api_key, rule evaluator should use mock (no crash)
        await step_round(repo, season_id, round_number=1, api_key="")

        rule_results = await repo.get_eval_results(
            season_id, eval_type="rule_evaluation"
        )
        assert len(rule_results) == 1
        # Mock evaluation has known content
        details = rule_results[0].details_json
        assert details is not None
        assert len(details.get("suggested_experiments", [])) > 0
