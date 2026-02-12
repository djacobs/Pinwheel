"""Tests for AI rule evaluator."""

import pytest

from pinwheel.evals.rule_evaluator import (
    _mock_evaluation,
    _parse_evaluation,
    evaluate_rules,
    store_rule_evaluation,
)


def test_mock_evaluation():
    result = _mock_evaluation("season-1", 5)
    assert result.season_id == "season-1"
    assert result.round_number == 5
    assert len(result.suggested_experiments) > 0
    assert len(result.stale_parameters) > 0
    assert result.equilibrium_notes != ""


def test_parse_evaluation():
    text = """
## Suggested Experiments
- Increase elam_margin to 15
- Reduce quarter_possessions to 12

## Stale Parameters
- three_point_distance
- altitude_stamina_penalty

## Equilibrium Notes
The current meta is balanced with no dominant archetype.

## Flagged Concerns
- Participation is declining
"""
    result = _parse_evaluation(text, "s-1", 3)
    assert len(result.suggested_experiments) == 2
    assert len(result.stale_parameters) == 2
    assert "balanced" in result.equilibrium_notes
    assert len(result.flagged_concerns) == 1


def test_parse_evaluation_empty():
    result = _parse_evaluation("No structured content here.", "s-1", 1)
    assert result.season_id == "s-1"


@pytest.mark.asyncio
async def test_evaluate_rules_no_api_key(repo):
    """Without API key, should return mock evaluation."""
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    result = await evaluate_rules(repo, season.id, 1, api_key="")
    assert result.season_id == season.id
    assert len(result.suggested_experiments) > 0


@pytest.mark.asyncio
async def test_store_rule_evaluation(repo):
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    evaluation = _mock_evaluation(season.id, 1)
    await store_rule_evaluation(repo, season.id, 1, evaluation)

    stored = await repo.get_eval_results(season.id, eval_type="rule_evaluation")
    assert len(stored) == 1
    assert stored[0].details_json is not None
    assert "suggested_experiments" in stored[0].details_json
