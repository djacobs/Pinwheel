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
    sim = [c for c in GOLDEN_CASES if c.report_type == "simulation"]
    gov = [c for c in GOLDEN_CASES if c.report_type == "governance"]
    priv = [c for c in GOLDEN_CASES if c.report_type == "private"]
    assert len(sim) == 8
    assert len(gov) == 7
    assert len(priv) == 5


def test_private_cases_structural_only():
    """All private cases must have structural_only=True."""
    priv = [c for c in GOLDEN_CASES if c.report_type == "private"]
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


# --- Tier-2 interpreter golden cases (Phase 4) ------------------------------


def test_tier2_interpreter_cases_pass_against_mock():
    """The Tier-2 golden cases pin the interpreter's grounding in the micro
    event-chain vocabulary — run against the mock interpreter (the same
    structural expectations apply to live interpretations)."""
    from pinwheel.ai.interpreter import interpret_proposal_v2_mock
    from pinwheel.evals.golden import (
        TIER2_INTERPRETER_CASES,
        run_interpreter_golden_case,
    )
    from pinwheel.models.rules import DEFAULT_RULESET

    assert len(TIER2_INTERPRETER_CASES) == 3
    for case in TIER2_INTERPRETER_CASES:
        interpretation = interpret_proposal_v2_mock(
            case.proposal_text, DEFAULT_RULESET,
        )
        result = run_interpreter_golden_case(case, interpretation)
        assert result.passed, f"{case.id}: {result.failures}"


def test_tier2_interpreter_case_fails_on_wrong_interpretation():
    from pinwheel.ai.interpreter import interpret_proposal_v2_mock
    from pinwheel.evals.golden import (
        TIER2_INTERPRETER_CASES,
        run_interpreter_golden_case,
    )
    from pinwheel.models.rules import DEFAULT_RULESET

    # A parameter-change interpretation does not satisfy the screens case.
    wrong = interpret_proposal_v2_mock(
        "Make three pointers worth 4", DEFAULT_RULESET,
    )
    result = run_interpreter_golden_case(TIER2_INTERPRETER_CASES[0], wrong)
    assert result.passed is False
    assert result.failures
