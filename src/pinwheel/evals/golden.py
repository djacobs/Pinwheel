"""Golden dataset (M.1) — 20 eval cases + runner.

Private cases have structural_only=True and empty expected_patterns.
Runner generates report from synthetic input data, checks patterns (public)
or structure (private). Works with mock reports (no API key needed for tests).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pinwheel.evals.models import GoldenCase
from pinwheel.evals.prescriptive import scan_prescriptive


@dataclass
class GoldenResult:
    """Result of running a single golden case."""

    case_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)


# --- The 20 Golden Cases ---

GOLDEN_CASES: list[GoldenCase] = [
    # 8 simulation reports
    GoldenCase(
        id="sim-01",
        report_type="simulation",
        input_data={
            "round_number": 1,
            "games": [
                {
                    "home_team": "Rose City Thorns",
                    "away_team": "Burnside Breakers",
                    "home_score": 45,
                    "away_score": 38,
                    "elam_activated": True,
                }
            ],
        },
        expected_patterns=["Rose City Thorns", "Burnside Breakers"],
    ),
    GoldenCase(
        id="sim-02",
        report_type="simulation",
        input_data={"round_number": 2, "games": []},
        expected_patterns=[],
        min_length=10,
    ),
    GoldenCase(
        id="sim-03",
        report_type="simulation",
        input_data={
            "round_number": 3,
            "games": [
                {
                    "home_team": "St. Johns Herons",
                    "away_team": "Hawthorne Hammers",
                    "home_score": 52,
                    "away_score": 51,
                    "elam_activated": True,
                }
            ],
        },
        expected_patterns=["Elam"],
    ),
    GoldenCase(
        id="sim-04",
        report_type="simulation",
        input_data={
            "round_number": 4,
            "games": [
                {
                    "home_team": "Rose City Thorns",
                    "away_team": "St. Johns Herons",
                    "home_score": 60,
                    "away_score": 30,
                    "elam_activated": False,
                }
            ],
        },
        expected_patterns=["Rose City"],
    ),
    GoldenCase(
        id="sim-05",
        report_type="simulation",
        input_data={
            "round_number": 5,
            "games": [
                {
                    "home_team": "Burnside Breakers",
                    "away_team": "Hawthorne Hammers",
                    "home_score": 44,
                    "away_score": 42,
                    "elam_activated": True,
                },
                {
                    "home_team": "Rose City Thorns",
                    "away_team": "St. Johns Herons",
                    "home_score": 48,
                    "away_score": 46,
                    "elam_activated": True,
                },
            ],
        },
        expected_patterns=["Elam"],
    ),
    GoldenCase(
        id="sim-06",
        report_type="simulation",
        input_data={
            "round_number": 6,
            "games": [
                {
                    "home_team": "A",
                    "away_team": "B",
                    "home_score": 100,
                    "away_score": 10,
                    "elam_activated": False,
                }
            ],
        },
        expected_patterns=["100"],
    ),
    GoldenCase(
        id="sim-07",
        report_type="simulation",
        input_data={
            "round_number": 7,
            "games": [
                {
                    "home_team": "Thorns",
                    "away_team": "Breakers",
                    "home_score": 33,
                    "away_score": 33,
                    "elam_activated": False,
                }
            ],
        },
        expected_patterns=["33"],
    ),
    GoldenCase(
        id="sim-08",
        report_type="simulation",
        input_data={
            "round_number": 8,
            "games": [
                {
                    "home_team": "X",
                    "away_team": "Y",
                    "home_score": 55,
                    "away_score": 50,
                    "elam_activated": True,
                    "total_possessions": 150,
                }
            ],
        },
        expected_patterns=[],
        min_length=20,
    ),
    # 7 governance reports
    GoldenCase(
        id="gov-01",
        report_type="governance",
        input_data={
            "proposals": [{"raw_text": "Increase three-point value to 4"}],
            "votes": [],
            "rules_changed": [],
        },
        expected_patterns=["proposal"],
    ),
    GoldenCase(
        id="gov-02",
        report_type="governance",
        input_data={"proposals": [], "votes": [], "rules_changed": []},
        expected_patterns=[],
        min_length=10,
    ),
    GoldenCase(
        id="gov-03",
        report_type="governance",
        input_data={
            "proposals": [{"raw_text": "Lower shot clock"}],
            "votes": [{"vote": "yes"}, {"vote": "yes"}, {"vote": "no"}],
            "rules_changed": [],
        },
        expected_patterns=["vote"],
    ),
    GoldenCase(
        id="gov-04",
        report_type="governance",
        input_data={
            "proposals": [{"raw_text": "Change Elam margin"}],
            "votes": [{"vote": "yes"}],
            "rules_changed": [{"parameter": "elam_margin"}],
        },
        expected_patterns=["change"],
    ),
    GoldenCase(
        id="gov-05",
        report_type="governance",
        input_data={
            "proposals": [{"raw_text": "A"}, {"raw_text": "B"}, {"raw_text": "C"}],
            "votes": [],
            "rules_changed": [],
        },
        expected_patterns=["3"],
    ),
    GoldenCase(
        id="gov-06",
        report_type="governance",
        input_data={
            "proposals": [{"raw_text": "Meta-governance change"}],
            "votes": [{"vote": "no"}, {"vote": "no"}],
            "rules_changed": [],
        },
        expected_patterns=["no"],
    ),
    GoldenCase(
        id="gov-07",
        report_type="governance",
        input_data={
            "proposals": [{"raw_text": "Radical change"}],
            "votes": [
                {"vote": "yes"},
                {"vote": "yes"},
                {"vote": "yes"},
                {"vote": "yes"},
            ],
            "rules_changed": [{"parameter": "three_point_value"}],
        },
        expected_patterns=["change"],
    ),
    # 5 private reports — structural checks only
    GoldenCase(
        id="priv-01",
        report_type="private",
        input_data={"governor_id": "gov-001", "proposals_submitted": 2, "votes_cast": 3},
        structural_only=True,
    ),
    GoldenCase(
        id="priv-02",
        report_type="private",
        input_data={"governor_id": "gov-002", "proposals_submitted": 0, "votes_cast": 0},
        structural_only=True,
    ),
    GoldenCase(
        id="priv-03",
        report_type="private",
        input_data={
            "governor_id": "gov-003",
            "proposals_submitted": 5,
            "votes_cast": 10,
            "tokens_spent": 5,
        },
        structural_only=True,
    ),
    GoldenCase(
        id="priv-04",
        report_type="private",
        input_data={"governor_id": "gov-004", "proposals_submitted": 1, "votes_cast": 1},
        structural_only=True,
        min_length=20,
    ),
    GoldenCase(
        id="priv-05",
        report_type="private",
        input_data={"governor_id": "gov-005", "proposals_submitted": 0, "votes_cast": 5},
        structural_only=True,
    ),
]


# --- Tier-2 interpreter golden cases (Phase 4) -----------------------------
#
# Same pattern as the report cases above: fixed inputs, expected structural
# patterns, a runner returning GoldenResult. These pin the interpreter's
# grounding in the micro event-chain vocabulary (screen nodes, pass-count
# and transition conditions) — run against the mock interpreter in tests
# and reusable against live interpretations.


@dataclass
class InterpreterGoldenCase:
    """A golden case for proposal interpretation (not report generation)."""

    id: str
    proposal_text: str
    expected_effect_type: str
    expected_patterns: list[str] = field(default_factory=list)
    """Regex patterns that must appear in the JSON-serialized effects."""
    min_confidence: float = 0.5


TIER2_INTERPRETER_CASES: list[InterpreterGoldenCase] = [
    InterpreterGoldenCase(
        id="tier2-screens-illegal",
        proposal_text="Picks are illegal. No more screens in this league.",
        expected_effect_type="modify_game_definition",
        expected_patterns=["screen_on_ball", "screen_off_ball", "remove_actions"],
    ),
    InterpreterGoldenCase(
        id="tier2-fourth-pass-bonus",
        proposal_text="Every fourth pass in a possession is worth a bonus point.",
        expected_effect_type="hook_callback",
        expected_patterns=[
            "sim.shot.pre",
            "pass_count_last_possession_gte",
            "modify_shot_value",
        ],
    ),
    InterpreterGoldenCase(
        id="tier2-transition-bonus",
        proposal_text="Fast-break baskets are worth an extra point.",
        expected_effect_type="hook_callback",
        expected_patterns=[
            "sim.shot.pre",
            "transition_possession",
            "modify_shot_value",
        ],
    ),
]


def run_interpreter_golden_case(
    case: InterpreterGoldenCase,
    interpretation: object,
) -> GoldenResult:
    """Run one interpreter golden case against a ProposalInterpretation.

    ``interpretation`` is any object with ``effects`` (each having an
    ``effect_type`` and dumpable via ``model_dump``) and ``confidence`` —
    the ProposalInterpretation model satisfies this.
    """
    import json

    failures: list[str] = []

    effects = getattr(interpretation, "effects", []) or []
    if not any(
        getattr(e, "effect_type", "") == case.expected_effect_type for e in effects
    ):
        failures.append(
            f"No effect of type '{case.expected_effect_type}' "
            f"(got: {[getattr(e, 'effect_type', '?') for e in effects]})"
        )

    serialized = json.dumps(
        [
            e.model_dump() if hasattr(e, "model_dump") else str(e)
            for e in effects
        ],
        default=str,
    )
    for pattern in case.expected_patterns:
        if not re.search(re.escape(pattern), serialized):
            failures.append(f"Missing expected pattern: '{pattern}'")

    confidence = getattr(interpretation, "confidence", 0.0)
    if confidence < case.min_confidence:
        failures.append(
            f"Confidence too low: {confidence} < {case.min_confidence}"
        )

    return GoldenResult(
        case_id=case.id,
        passed=len(failures) == 0,
        failures=failures,
    )


def run_golden_case(case: GoldenCase, report_content: str) -> GoldenResult:
    """Run a single golden case against generated report content."""
    failures = []

    # Length checks
    if len(report_content) < case.min_length:
        failures.append(f"Too short: {len(report_content)} < {case.min_length}")
    if len(report_content) > case.max_length:
        failures.append(f"Too long: {len(report_content)} > {case.max_length}")

    # Prescriptive language check (all reports)
    presc = scan_prescriptive(report_content, case.id, case.report_type)
    if presc.prescriptive_count > 0:
        failures.append(f"Prescriptive language detected: {presc.prescriptive_count} instances")

    if case.structural_only:
        # Private reports: structural checks only
        pass
    else:
        # Public reports: pattern matching
        for pattern in case.expected_patterns:
            if not re.search(re.escape(pattern), report_content, re.IGNORECASE):
                failures.append(f"Missing expected pattern: '{pattern}'")

    return GoldenResult(
        case_id=case.id,
        passed=len(failures) == 0,
        failures=failures,
    )


def run_golden_suite(
    generate_fn: callable,
) -> list[GoldenResult]:
    """Run all golden cases using a report generation function.

    generate_fn(case: GoldenCase) -> str: takes a case, returns report content.
    """
    results = []
    for case in GOLDEN_CASES:
        content = generate_fn(case)
        results.append(run_golden_case(case, content))
    return results
