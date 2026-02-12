"""Tests for eval Pydantic models."""

import pytest
from pydantic import ValidationError

from pinwheel.evals.models import (
    ABComparison,
    ABVariant,
    BehavioralShiftResult,
    GoldenCase,
    GQIResult,
    GroundingResult,
    PrescriptiveResult,
    RubricScore,
    RuleEvaluation,
    ScenarioFlag,
)


def test_grounding_result():
    r = GroundingResult(
        mirror_id="m-1", mirror_type="simulation",
        entities_found=3, entities_expected=5,
    )
    assert r.grounded is False


def test_prescriptive_result():
    r = PrescriptiveResult(mirror_id="m-1", mirror_type="simulation", prescriptive_count=0)
    assert r.flagged is False


def test_behavioral_shift_result():
    r = BehavioralShiftResult(governor_id="g-1", round_number=1, shifted=True)
    assert r.shifted is True


def test_rubric_score_valid():
    r = RubricScore(mirror_id="m-1", mirror_type="simulation", accuracy=5, insight=4)
    assert r.accuracy == 5


def test_rubric_score_rejects_private():
    with pytest.raises(ValidationError):
        RubricScore(mirror_id="m-1", mirror_type="private")


def test_rubric_score_range():
    with pytest.raises(ValidationError):
        RubricScore(mirror_id="m-1", mirror_type="simulation", accuracy=6)
    with pytest.raises(ValidationError):
        RubricScore(mirror_id="m-1", mirror_type="simulation", accuracy=0)


def test_golden_case():
    c = GoldenCase(id="g-1", mirror_type="private", structural_only=True)
    assert c.expected_patterns == []


def test_ab_variant_private():
    v = ABVariant(variant="A", mirror_id="m-1", mirror_type="private", content=None)
    assert v.content is None


def test_ab_comparison():
    a = ABVariant(variant="A", mirror_id="m-a", mirror_type="simulation")
    b = ABVariant(variant="B", mirror_id="m-b", mirror_type="simulation")
    c = ABComparison(comparison_id="c-1", variant_a=a, variant_b=b, winner="A")
    assert c.winner == "A"


def test_scenario_flag():
    f = ScenarioFlag(flag_type="blowout_game", severity="warning", round_number=5)
    assert f.flag_type == "blowout_game"


def test_scenario_flag_invalid_type():
    with pytest.raises(ValidationError):
        ScenarioFlag(flag_type="invalid_type", severity="info")


def test_rule_evaluation():
    r = RuleEvaluation(season_id="s-1", round_number=1, suggested_experiments=["Test A"])
    assert len(r.suggested_experiments) == 1


def test_gqi_result():
    r = GQIResult(
        season_id="s-1",
        round_number=1,
        proposal_diversity=0.5,
        participation_breadth=0.8,
        consequence_awareness=0.3,
        vote_deliberation=0.6,
        composite=0.55,
    )
    assert r.composite == 0.55
