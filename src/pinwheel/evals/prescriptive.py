"""Prescriptive language detector (S.2c).

Scans report content for directive phrases ("should", "must", "needs to", etc.).
Returns count only — never the matched text. Privacy: the count is the signal.
"""

from __future__ import annotations

import re

from pinwheel.evals.models import PrescriptiveResult

# Patterns that indicate prescriptive (directive) language in reports.
# Reports should DESCRIBE, never PRESCRIBE.
PRESCRIPTIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bshould\b", re.IGNORECASE),
    re.compile(r"\bmust\b", re.IGNORECASE),
    re.compile(r"\bneeds?\s+to\b", re.IGNORECASE),
    re.compile(r"\bought\s+to\b", re.IGNORECASE),
    re.compile(r"\bhave\s+to\b", re.IGNORECASE),
    re.compile(r"\bhas\s+to\b", re.IGNORECASE),
    re.compile(r"\bshall\b", re.IGNORECASE),
    re.compile(r"\bconsider\s+(doing|changing|adjusting)\b", re.IGNORECASE),
    re.compile(r"\bit\s+is\s+(imperative|essential|critical)\s+that\b", re.IGNORECASE),
    re.compile(r"\bthe\s+league\s+needs\b", re.IGNORECASE),
    re.compile(r"\bgovernors?\s+should\b", re.IGNORECASE),
    re.compile(r"\bplayers?\s+should\b", re.IGNORECASE),
]


def scan_prescriptive(content: str, report_id: str, report_type: str) -> PrescriptiveResult:
    """Scan report content for prescriptive language. Returns count, never matched text."""
    count = 0
    for pattern in PRESCRIPTIVE_PATTERNS:
        count += len(pattern.findall(content))

    return PrescriptiveResult(
        report_id=report_id,
        report_type=report_type,
        prescriptive_count=count,
        flagged=count > 0,
    )
