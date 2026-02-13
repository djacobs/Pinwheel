"""Tests for prescriptive language detector."""

from pinwheel.evals.prescriptive import scan_prescriptive


def test_clean_report():
    result = scan_prescriptive(
        "The Rose City Thorns dominated the third quarter with aggressive three-point shooting.",
        report_id="m-1",
        report_type="simulation",
    )
    assert result.prescriptive_count == 0
    assert result.flagged is False


def test_single_prescriptive():
    result = scan_prescriptive(
        "The governors should consider changing the shot clock.",
        report_id="m-2",
        report_type="governance",
    )
    assert result.prescriptive_count >= 1
    assert result.flagged is True


def test_multiple_prescriptive():
    result = scan_prescriptive(
        "Players should pass more. The league needs to adjust rules. Governors must act now.",
        report_id="m-3",
        report_type="simulation",
    )
    assert result.prescriptive_count >= 3
    assert result.flagged is True


def test_case_insensitive():
    result = scan_prescriptive(
        "SHOULD this trend continue, it MUST be noted.",
        report_id="m-4",
        report_type="simulation",
    )
    assert result.prescriptive_count >= 2


def test_empty_content():
    result = scan_prescriptive("", report_id="m-5", report_type="simulation")
    assert result.prescriptive_count == 0
    assert result.flagged is False


def test_needs_to_pattern():
    result = scan_prescriptive(
        "This team needs to improve their defense.",
        report_id="m-6",
        report_type="simulation",
    )
    assert result.prescriptive_count >= 1


def test_ought_to_pattern():
    result = scan_prescriptive(
        "Governors ought to reconsider their approach.",
        report_id="m-7",
        report_type="governance",
    )
    assert result.prescriptive_count >= 1


def test_result_fields():
    result = scan_prescriptive("Clean content.", report_id="m-8", report_type="private")
    assert result.report_id == "m-8"
    assert result.report_type == "private"
