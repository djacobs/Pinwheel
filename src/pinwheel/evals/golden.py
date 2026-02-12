"""Golden dataset (M.1) — 20 eval cases + runner.

Private cases have structural_only=True and empty expected_patterns.
Runner generates mirror from synthetic input data, checks patterns (public)
or structure (private). Works with mock mirrors (no API key needed for tests).
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
    # 8 simulation mirrors
    GoldenCase(
        id="sim-01",
        mirror_type="simulation",
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
        mirror_type="simulation",
        input_data={"round_number": 2, "games": []},
        expected_patterns=[],
        min_length=10,
    ),
    GoldenCase(
        id="sim-03",
        mirror_type="simulation",
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
        mirror_type="simulation",
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
        mirror_type="simulation",
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
        mirror_type="simulation",
        input_data={
            "round_number": 6,
            "games": [{
                "home_team": "A", "away_team": "B",
                "home_score": 100, "away_score": 10,
                "elam_activated": False,
            }],
        },
        expected_patterns=["100"],
    ),
    GoldenCase(
        id="sim-07",
        mirror_type="simulation",
        input_data={
            "round_number": 7,
            "games": [{
                "home_team": "Thorns", "away_team": "Breakers",
                "home_score": 33, "away_score": 33,
                "elam_activated": False,
            }],
        },
        expected_patterns=["33"],
    ),
    GoldenCase(
        id="sim-08",
        mirror_type="simulation",
        input_data={
            "round_number": 8,
            "games": [{
                "home_team": "X", "away_team": "Y",
                "home_score": 55, "away_score": 50,
                "elam_activated": True, "total_possessions": 150,
            }],
        },
        expected_patterns=[],
        min_length=20,
    ),
    # 7 governance mirrors
    GoldenCase(
        id="gov-01",
        mirror_type="governance",
        input_data={
            "proposals": [{"raw_text": "Increase three-point value to 4"}],
            "votes": [],
            "rules_changed": [],
        },
        expected_patterns=["proposal"],
    ),
    GoldenCase(
        id="gov-02",
        mirror_type="governance",
        input_data={"proposals": [], "votes": [], "rules_changed": []},
        expected_patterns=[],
        min_length=10,
    ),
    GoldenCase(
        id="gov-03",
        mirror_type="governance",
        input_data={
            "proposals": [{"raw_text": "Lower shot clock"}],
            "votes": [{"vote": "yes"}, {"vote": "yes"}, {"vote": "no"}],
            "rules_changed": [],
        },
        expected_patterns=["vote"],
    ),
    GoldenCase(
        id="gov-04",
        mirror_type="governance",
        input_data={
            "proposals": [{"raw_text": "Change Elam margin"}],
            "votes": [{"vote": "yes"}],
            "rules_changed": [{"parameter": "elam_margin"}],
        },
        expected_patterns=["change"],
    ),
    GoldenCase(
        id="gov-05",
        mirror_type="governance",
        input_data={
            "proposals": [{"raw_text": "A"}, {"raw_text": "B"}, {"raw_text": "C"}],
            "votes": [],
            "rules_changed": [],
        },
        expected_patterns=["3"],
    ),
    GoldenCase(
        id="gov-06",
        mirror_type="governance",
        input_data={
            "proposals": [{"raw_text": "Meta-governance change"}],
            "votes": [{"vote": "no"}, {"vote": "no"}],
            "rules_changed": [],
        },
        expected_patterns=["no"],
    ),
    GoldenCase(
        id="gov-07",
        mirror_type="governance",
        input_data={
            "proposals": [{"raw_text": "Radical change"}],
            "votes": [
                {"vote": "yes"}, {"vote": "yes"},
                {"vote": "yes"}, {"vote": "yes"},
            ],
            "rules_changed": [{"parameter": "three_point_value"}],
        },
        expected_patterns=["change"],
    ),
    # 5 private mirrors — structural checks only
    GoldenCase(
        id="priv-01",
        mirror_type="private",
        input_data={"governor_id": "gov-001", "proposals_submitted": 2, "votes_cast": 3},
        structural_only=True,
    ),
    GoldenCase(
        id="priv-02",
        mirror_type="private",
        input_data={"governor_id": "gov-002", "proposals_submitted": 0, "votes_cast": 0},
        structural_only=True,
    ),
    GoldenCase(
        id="priv-03",
        mirror_type="private",
        input_data={
            "governor_id": "gov-003",
            "proposals_submitted": 5, "votes_cast": 10, "tokens_spent": 5,
        },
        structural_only=True,
    ),
    GoldenCase(
        id="priv-04",
        mirror_type="private",
        input_data={"governor_id": "gov-004", "proposals_submitted": 1, "votes_cast": 1},
        structural_only=True,
        min_length=20,
    ),
    GoldenCase(
        id="priv-05",
        mirror_type="private",
        input_data={"governor_id": "gov-005", "proposals_submitted": 0, "votes_cast": 5},
        structural_only=True,
    ),
]


def run_golden_case(case: GoldenCase, mirror_content: str) -> GoldenResult:
    """Run a single golden case against generated mirror content."""
    failures = []

    # Length checks
    if len(mirror_content) < case.min_length:
        failures.append(f"Too short: {len(mirror_content)} < {case.min_length}")
    if len(mirror_content) > case.max_length:
        failures.append(f"Too long: {len(mirror_content)} > {case.max_length}")

    # Prescriptive language check (all mirrors)
    presc = scan_prescriptive(mirror_content, case.id, case.mirror_type)
    if presc.prescriptive_count > 0:
        failures.append(f"Prescriptive language detected: {presc.prescriptive_count} instances")

    if case.structural_only:
        # Private mirrors: structural checks only
        pass
    else:
        # Public mirrors: pattern matching
        for pattern in case.expected_patterns:
            if not re.search(re.escape(pattern), mirror_content, re.IGNORECASE):
                failures.append(f"Missing expected pattern: '{pattern}'")

    return GoldenResult(
        case_id=case.id,
        passed=len(failures) == 0,
        failures=failures,
    )


def run_golden_suite(
    generate_fn: callable,
) -> list[GoldenResult]:
    """Run all golden cases using a mirror generation function.

    generate_fn(case: GoldenCase) -> str: takes a case, returns mirror content.
    """
    results = []
    for case in GOLDEN_CASES:
        content = generate_fn(case)
        results.append(run_golden_case(case, content))
    return results
