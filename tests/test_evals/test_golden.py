"""Tests for golden dataset."""

from pinwheel.evals.golden import GOLDEN_CASES, run_golden_case, run_golden_suite


def _mock_generator(case):
    """Simple mock generator that produces content matching expected patterns."""
    parts = []
    for pattern in case.expected_patterns:
        parts.append(f"The {pattern} was notable this round.")
    if not parts:
        parts.append("This round saw standard gameplay with no unusual events to report.")
    # Add enough content to meet min_length
    content = " ".join(parts)
    while len(content) < case.min_length:
        content += " Additional context about the round's events."
    return content


def test_golden_cases_count():
    """Should have exactly 20 golden cases."""
    assert len(GOLDEN_CASES) == 20


def test_golden_case_types():
    """Should have 8 sim, 7 gov, 5 private."""
    sim = [c for c in GOLDEN_CASES if c.mirror_type == "simulation"]
    gov = [c for c in GOLDEN_CASES if c.mirror_type == "governance"]
    priv = [c for c in GOLDEN_CASES if c.mirror_type == "private"]
    assert len(sim) == 8
    assert len(gov) == 7
    assert len(priv) == 5


def test_private_cases_structural_only():
    """All private cases must have structural_only=True."""
    priv = [c for c in GOLDEN_CASES if c.mirror_type == "private"]
    for c in priv:
        assert c.structural_only is True


def test_run_golden_case_passes():
    case = GOLDEN_CASES[0]  # sim-01 expects Rose City Thorns and Burnside Breakers
    result = run_golden_case(
        case,
        "The Rose City Thorns beat the Burnside Breakers in a thrilling contest this round.",
    )
    assert result.passed is True
    assert result.failures == []


def test_run_golden_case_too_short():
    case = GOLDEN_CASES[0]
    result = run_golden_case(case, "Short.")
    assert result.passed is False
    assert any("Too short" in f for f in result.failures)


def test_run_golden_case_prescriptive():
    case = GOLDEN_CASES[0]
    result = run_golden_case(
        case,
        "The Rose City Thorns should beat the Burnside Breakers. "
        "Players must try harder next round.",
    )
    assert result.passed is False
    assert any("Prescriptive" in f for f in result.failures)


def test_run_golden_case_missing_pattern():
    case = GOLDEN_CASES[0]  # expects "Rose City Thorns" and "Burnside Breakers"
    result = run_golden_case(
        case,
        "A team won the game with impressive offensive performance "
        "and defensive strategy this round.",
    )
    assert result.passed is False
    assert any("Missing expected" in f for f in result.failures)


def test_run_golden_suite():
    results = run_golden_suite(_mock_generator)
    assert len(results) == 20
    # All should have case IDs
    ids = [r.case_id for r in results]
    assert "sim-01" in ids
    assert "priv-05" in ids


def test_private_case_structural():
    """Private case passes with non-prescriptive content of sufficient length."""
    case = [c for c in GOLDEN_CASES if c.id == "priv-01"][0]
    result = run_golden_case(
        case,
        "Governor gov-001 submitted 2 proposals and cast 3 votes this round, "
        "showing consistent engagement with the governance process.",
    )
    assert result.passed is True
