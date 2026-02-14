"""Eval dashboard route — GET /admin/evals.

Shows aggregate stats only. No individual report text. No private content.
Visible in dev/staging only.

Supports ``?round=N`` query parameter to drill down into a specific round.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from pinwheel.api.deps import RepoDep
from pinwheel.auth.deps import OptionalUser, SessionUser
from pinwheel.config import PROJECT_ROOT

router = APIRouter(prefix="/admin", tags=["admin"])

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


def compute_safety_summary(
    *,
    grounding_rate: float,
    grounding_total: int,
    prescriptive_flagged: int,
    injection_attempts: int,
    active_flags: list[dict],
    gqi_trend: list[dict],
    golden_pass_rate: float,
) -> dict:
    """Compute the traffic-light safety summary from eval metrics.

    Returns a dict with:
      - status: "green" | "yellow" | "red"
      - label: human-readable status label
      - total_evaluated: total reports/proposals evaluated
      - injection_attempts: number of injection attempts detected
      - eval_coverage_pct: percentage of eval types that have data
      - gqi_score: latest GQI composite score (0.0 if unavailable)
      - concerns: list of short concern strings
    """
    concerns: list[str] = []

    # Count critical and warning flags
    critical_flags = sum(1 for f in active_flags if f.get("severity") == "critical")
    warning_flags = sum(1 for f in active_flags if f.get("severity") == "warning")

    if critical_flags > 0:
        concerns.append(f"{critical_flags} critical flag(s) active")
    if warning_flags > 0:
        concerns.append(f"{warning_flags} warning flag(s) active")

    # Injection attempts
    if injection_attempts > 0:
        concerns.append(f"{injection_attempts} injection attempt(s) detected")

    # Grounding below threshold
    if grounding_total > 0 and grounding_rate < 0.5:
        concerns.append(f"Low grounding rate: {grounding_rate:.0%}")

    # Prescriptive language flags
    if prescriptive_flagged > 0:
        concerns.append(f"{prescriptive_flagged} prescriptive report(s) flagged")

    # Golden dataset pass rate below threshold
    if golden_pass_rate > 0 and golden_pass_rate < 0.7:
        concerns.append(f"Golden dataset pass rate: {golden_pass_rate:.0%}")

    # Latest GQI score
    gqi_score = 0.0
    if gqi_trend:
        gqi_score = gqi_trend[-1].get("composite", 0.0)

    # Eval coverage: count how many of the 5 eval signal types have data
    eval_types_with_data = sum([
        grounding_total > 0,
        prescriptive_flagged >= 0 and grounding_total > 0,  # prescriptive runs with grounding
        len(gqi_trend) > 0,
        golden_pass_rate > 0,
        len(active_flags) > 0,
    ])
    eval_coverage_pct = (eval_types_with_data / 5) * 100

    # Determine traffic-light status
    status: Literal["green", "yellow", "red"] = "green"
    label = "All Clear"

    if critical_flags > 0 or injection_attempts >= 3:
        status = "red"
        label = "Issues Detected"
    elif (
        warning_flags > 0
        or injection_attempts > 0
        or prescriptive_flagged > 0
        or (grounding_total > 0 and grounding_rate < 0.5)
        or (golden_pass_rate > 0 and golden_pass_rate < 0.7)
    ):
        status = "yellow"
        label = "Warnings Present"

    return {
        "status": status,
        "label": label,
        "total_evaluated": grounding_total,
        "injection_attempts": injection_attempts,
        "eval_coverage_pct": eval_coverage_pct,
        "gqi_score": gqi_score,
        "concerns": concerns,
    }


def _auth_context(request: Request, current_user: SessionUser | None) -> dict:
    settings = request.app.state.settings
    oauth_enabled = bool(settings.discord_client_id and settings.discord_client_secret)
    return {
        "current_user": current_user,
        "oauth_enabled": oauth_enabled,
        "pinwheel_env": settings.pinwheel_env,
    }


async def _get_active_season_id(repo: RepoDep) -> str | None:
    from sqlalchemy import select

    from pinwheel.db.models import SeasonRow

    stmt = select(SeasonRow).limit(1)
    result = await repo.session.execute(stmt)
    row = result.scalar_one_or_none()
    return row.id if row else None


async def _get_available_rounds(repo: RepoDep, season_id: str) -> list[int]:
    """Return sorted list of distinct round numbers that have eval results."""
    from sqlalchemy import select

    from pinwheel.db.models import EvalResultRow

    stmt = (
        select(EvalResultRow.round_number)
        .where(EvalResultRow.season_id == season_id)
        .distinct()
        .order_by(EvalResultRow.round_number)
    )
    result = await repo.session.execute(stmt)
    return [row[0] for row in result.all() if row[0] is not None]


def _parse_round_param(request: Request) -> int | None:
    """Extract and validate the ``round`` query parameter."""
    raw = request.query_params.get("round")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return None
    if value < 0:
        return None
    return value


@router.get("/evals", response_class=HTMLResponse)
async def eval_dashboard(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Eval dashboard — aggregate stats, no report text.

    Supports ``?round=N`` to filter eval results to a specific round.

    Auth-gated: redirects to login if OAuth is enabled and user is not
    authenticated. In dev mode without OAuth credentials the page is
    accessible to support local testing.
    """
    from fastapi.responses import RedirectResponse

    settings = request.app.state.settings
    oauth_enabled = bool(settings.discord_client_id and settings.discord_client_secret)
    if current_user is None and oauth_enabled:
        return RedirectResponse(url="/auth/login", status_code=302)

    season_id = await _get_active_season_id(repo)
    if not season_id:
        return templates.TemplateResponse(
            request,
            "pages/eval_dashboard.html",
            {
                "active_page": "evals",
                "has_data": False,
                "selected_round": None,
                "available_rounds": [],
                "prev_round": None,
                "next_round": None,
                **_auth_context(request, current_user),
            },
        )

    # Parse optional round filter
    selected_round = _parse_round_param(request)

    # Determine available rounds for navigation
    available_rounds = await _get_available_rounds(repo, season_id)

    # Compute prev/next for round navigation
    prev_round: int | None = None
    next_round: int | None = None
    if selected_round is not None and available_rounds:
        for i, rn in enumerate(available_rounds):
            if rn == selected_round:
                if i > 0:
                    prev_round = available_rounds[i - 1]
                if i < len(available_rounds) - 1:
                    next_round = available_rounds[i + 1]
                break

    # All eval queries below use round_number when selected_round is set
    rn_filter = selected_round

    # Grounding results
    grounding_results = await repo.get_eval_results(
        season_id, eval_type="grounding", round_number=rn_filter
    )
    grounding_total = len(grounding_results)
    grounding_grounded = sum(
        1 for r in grounding_results if (r.details_json or {}).get("grounded", False)
    )
    grounding_rate = grounding_grounded / grounding_total if grounding_total else 0.0

    # Prescriptive results
    prescriptive_results = await repo.get_eval_results(
        season_id, eval_type="prescriptive", round_number=rn_filter
    )
    prescriptive_total = len(prescriptive_results)
    prescriptive_flagged = sum(
        1 for r in prescriptive_results if (r.details_json or {}).get("flagged", False)
    )
    prescriptive_count = sum((r.details_json or {}).get("count", 0) for r in prescriptive_results)

    # Behavioral results (Report Impact Rate)
    behavioral_results = await repo.get_eval_results(
        season_id, eval_type="behavioral", round_number=rn_filter
    )
    latest_mir = behavioral_results[0] if behavioral_results else None
    report_impact_rate = 0.0
    if latest_mir:
        report_impact_rate = (latest_mir.details_json or {}).get("report_impact_rate", 0.0)

    # Rubric summary
    from pinwheel.evals.rubric import get_rubric_summary

    rubric_summary = await get_rubric_summary(repo, season_id, round_number=rn_filter)

    # Golden dataset (show latest run if available)
    golden_results = await repo.get_eval_results(
        season_id, eval_type="golden", round_number=rn_filter
    )
    golden_pass_rate = 0.0
    if golden_results:
        passed = sum(1 for r in golden_results if r.score >= 1.0)
        golden_pass_rate = passed / len(golden_results)

    # A/B win rates
    from pinwheel.evals.ab_compare import get_ab_win_rates

    ab_rates = await get_ab_win_rates(repo, season_id, round_number=rn_filter)

    # GQI trend (last 5 rounds) — when filtering by round, show only that round
    gqi_results = await repo.get_eval_results(
        season_id, eval_type="gqi", round_number=rn_filter
    )
    gqi_trend = []
    for r in sorted(gqi_results, key=lambda x: x.round_number)[-5:]:
        details = r.details_json or {}
        gqi_trend.append(
            {
                "round": r.round_number,
                "composite": details.get("composite", 0.0),
                "diversity": details.get("proposal_diversity", 0.0),
                "breadth": details.get("participation_breadth", 0.0),
                "awareness": details.get("consequence_awareness", 0.0),
                "deliberation": details.get("vote_deliberation", 0.0),
            }
        )

    # Active scenario flags
    flag_results = await repo.get_eval_results(
        season_id, eval_type="flag", round_number=rn_filter
    )
    active_flags = []
    for r in sorted(flag_results, key=lambda x: x.created_at, reverse=True)[:10]:
        details = r.details_json or {}
        active_flags.append(
            {
                "type": details.get("flag_type", r.eval_subtype),
                "severity": details.get("severity", "info"),
                "round": r.round_number,
                "details": {
                    k: v
                    for k, v in details.items()
                    if k not in ("flag_type", "severity", "created_at")
                },
            }
        )

    # Latest rule evaluation
    rule_eval_results = await repo.get_eval_results(
        season_id, eval_type="rule_evaluation", round_number=rn_filter
    )
    latest_rule_eval = None
    if rule_eval_results:
        details = rule_eval_results[0].details_json or {}
        latest_rule_eval = {
            "round": rule_eval_results[0].round_number,
            "experiments": details.get("suggested_experiments", []),
            "stale_params": details.get("stale_parameters", []),
            "equilibrium": details.get("equilibrium_notes", ""),
            "concerns": details.get("flagged_concerns", []),
        }

    # Injection classification results
    from pinwheel.evals.injection import get_injection_classifications

    injection_classifications = await get_injection_classifications(repo, season_id, limit=20)
    injection_attempts = sum(
        1
        for c in injection_classifications
        if c.classification in ("injection", "suspicious")
    )
    injection_total = len(injection_classifications)
    injection_blocked = sum(1 for c in injection_classifications if c.blocked)

    # Build display list for recent classifications
    recent_classifications = []
    for c in injection_classifications[:10]:
        recent_classifications.append(
            {
                "preview": c.proposal_text_preview,
                "classification": c.classification,
                "confidence": c.confidence,
                "reason": c.reason,
                "source": c.source,
                "blocked": c.blocked,
                "created_at": c.created_at.strftime("%Y-%m-%d %H:%M"),
            }
        )

    # Compute traffic-light safety summary
    safety_summary = compute_safety_summary(
        grounding_rate=grounding_rate,
        grounding_total=grounding_total,
        prescriptive_flagged=prescriptive_flagged,
        injection_attempts=injection_attempts,
        active_flags=active_flags,
        gqi_trend=gqi_trend,
        golden_pass_rate=golden_pass_rate,
    )

    return templates.TemplateResponse(
        request,
        "pages/eval_dashboard.html",
        {
            "active_page": "evals",
            "has_data": True,
            "selected_round": selected_round,
            "available_rounds": available_rounds,
            "prev_round": prev_round,
            "next_round": next_round,
            "safety_summary": safety_summary,
            "grounding_rate": grounding_rate,
            "grounding_total": grounding_total,
            "prescriptive_flagged": prescriptive_flagged,
            "prescriptive_total": prescriptive_total,
            "prescriptive_count": prescriptive_count,
            "report_impact_rate": report_impact_rate,
            "rubric_summary": rubric_summary,
            "golden_pass_rate": golden_pass_rate,
            "ab_rates": ab_rates,
            "gqi_trend": gqi_trend,
            "active_flags": active_flags,
            "latest_rule_eval": latest_rule_eval,
            "injection_total": injection_total,
            "injection_attempts": injection_attempts,
            "injection_blocked": injection_blocked,
            "recent_classifications": recent_classifications,
            **_auth_context(request, current_user),
        },
    )
