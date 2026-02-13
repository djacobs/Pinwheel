"""Tests for attribution analysis."""

import pytest

from pinwheel.evals.attribution import (
    assign_treatment_control,
    compute_attribution,
    store_attribution_result,
)


def test_assign_treatment_control():
    ids = ["gov-1", "gov-2", "gov-3", "gov-4"]
    treatment, control = assign_treatment_control(ids, treatment_ratio=0.5, seed=42)
    assert len(treatment) + len(control) == 4
    assert len(treatment) == 2
    assert len(control) == 2
    assert set(treatment + control) == set(ids)


def test_assign_deterministic():
    ids = ["a", "b", "c", "d"]
    t1, c1 = assign_treatment_control(ids, seed=42)
    t2, c2 = assign_treatment_control(ids, seed=42)
    assert t1 == t2
    assert c1 == c2


def test_assign_empty():
    treatment, control = assign_treatment_control([])
    assert treatment == []
    assert control == []


@pytest.mark.asyncio
async def test_compute_attribution(repo):
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    result = await compute_attribution(repo, season.id, 1, ["gov-1", "gov-2"], ["gov-3", "gov-4"])
    assert "treatment_shift_rate" in result
    assert "control_shift_rate" in result
    assert "delta" in result
    assert result["treatment_count"] == 2
    assert result["control_count"] == 2


@pytest.mark.asyncio
async def test_store_attribution(repo):
    league = await repo.create_league("Test")
    season = await repo.create_season(league.id, "S1")

    result = {
        "treatment_count": 2,
        "control_count": 2,
        "treatment_shift_rate": 0.5,
        "control_shift_rate": 0.0,
        "delta": 0.5,
        "round_number": 1,
    }
    await store_attribution_result(repo, season.id, 1, result)
    stored = await repo.get_eval_results(season.id, eval_type="attribution")
    assert len(stored) == 1
    assert stored[0].score == 0.5
