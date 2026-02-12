"""Tests for prescriptive language detector."""

from pinwheel.evals.prescriptive import scan_prescriptive


def test_clean_mirror():
    result = scan_prescriptive(
        "The Rose City Thorns dominated the third quarter with aggressive three-point shooting.",
        mirror_id="m-1",
        mirror_type="simulation",
    )
    assert result.prescriptive_count == 0
    assert result.flagged is False


def test_single_prescriptive():
    result = scan_prescriptive(
        "The governors should consider changing the shot clock.",
        mirror_id="m-2",
        mirror_type="governance",
    )
    assert result.prescriptive_count >= 1
    assert result.flagged is True


def test_multiple_prescriptive():
    result = scan_prescriptive(
        "Players should pass more. The league needs to adjust rules. Governors must act now.",
        mirror_id="m-3",
        mirror_type="simulation",
    )
    assert result.prescriptive_count >= 3
    assert result.flagged is True


def test_case_insensitive():
    result = scan_prescriptive(
        "SHOULD this trend continue, it MUST be noted.",
        mirror_id="m-4",
        mirror_type="simulation",
    )
    assert result.prescriptive_count >= 2


def test_empty_content():
    result = scan_prescriptive("", mirror_id="m-5", mirror_type="simulation")
    assert result.prescriptive_count == 0
    assert result.flagged is False


def test_needs_to_pattern():
    result = scan_prescriptive(
        "This team needs to improve their defense.",
        mirror_id="m-6",
        mirror_type="simulation",
    )
    assert result.prescriptive_count >= 1


def test_ought_to_pattern():
    result = scan_prescriptive(
        "Governors ought to reconsider their approach.",
        mirror_id="m-7",
        mirror_type="governance",
    )
    assert result.prescriptive_count >= 1


def test_result_fields():
    result = scan_prescriptive("Clean content.", mirror_id="m-8", mirror_type="private")
    assert result.mirror_id == "m-8"
    assert result.mirror_type == "private"
