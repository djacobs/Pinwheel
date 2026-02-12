"""Tests for manual rubric scoring."""

import pytest
from pydantic import ValidationError

from pinwheel.evals.models import RubricScore
from pinwheel.evals.rubric import export_rubric_csv, get_rubric_summary, score_average, score_mirror


def test_rubric_rejects_private():
    """RubricScore must reject mirror_type='private' at the Pydantic level."""
    with pytest.raises(ValidationError):
        RubricScore(mirror_id="m-1", mirror_type="private")


def test_rubric_accepts_simulation():
    score = RubricScore(mirror_id="m-1", mirror_type="simulation")
    assert score.mirror_type == "simulation"


def test_rubric_accepts_governance():
    score = RubricScore(mirror_id="m-2", mirror_type="governance")
    assert score.mirror_type == "governance"


def test_score_average():
    score = RubricScore(
        mirror_id="m-1",
        mirror_type="simulation",
        accuracy=5,
        insight=4,
        tone=3,
        conciseness=4,
        non_prescriptive=5,
    )
    avg = score_average(score)
    assert avg == pytest.approx(4.2)


def test_score_average_default():
    score = RubricScore(mirror_id="m-1", mirror_type="simulation")
    avg = score_average(score)
    assert avg == 3.0


@pytest.mark.asyncio
async def test_score_mirror(repo):
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    rubric = RubricScore(
        mirror_id="m-1",
        mirror_type="simulation",
        accuracy=5,
        insight=4,
        tone=4,
        conciseness=3,
        non_prescriptive=5,
    )
    avg = await score_mirror(repo, season.id, 1, rubric)
    assert avg == pytest.approx(4.2)

    # Verify stored
    results = await repo.get_eval_results(season.id, eval_type="rubric")
    assert len(results) == 1
    assert results[0].score == pytest.approx(4.2)


@pytest.mark.asyncio
async def test_rubric_summary_empty(repo):
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    summary = await get_rubric_summary(repo, season.id)
    assert summary["count"] == 0
    assert summary["pass_rate"] == 0.0


def test_export_csv():
    data = [
        {
            "mirror_id": "m-1",
            "mirror_type": "simulation",
            "scorer_id": "s-1",
            "accuracy": 4,
            "insight": 3,
            "tone": 4,
            "conciseness": 3,
            "non_prescriptive": 5,
            "average": 3.8,
        }
    ]
    csv_str = export_rubric_csv(data)
    assert "mirror_id" in csv_str
    assert "m-1" in csv_str
