"""Eval dashboard route — GET /admin/evals.

Shows aggregate stats only. No individual mirror text. No private content.
Visible in dev/staging only.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from pinwheel.api.deps import RepoDep
from pinwheel.auth.deps import OptionalUser, SessionUser
from pinwheel.config import PROJECT_ROOT

router = APIRouter(prefix="/admin", tags=["admin"])

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


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


@router.get("/evals", response_class=HTMLResponse)
async def eval_dashboard(request: Request, repo: RepoDep, current_user: OptionalUser):
    """Eval dashboard — aggregate stats, no mirror text.

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
                **_auth_context(request, current_user),
            },
        )

    # Grounding results
    grounding_results = await repo.get_eval_results(season_id, eval_type="grounding")
    grounding_total = len(grounding_results)
    grounding_grounded = sum(
        1 for r in grounding_results if (r.details_json or {}).get("grounded", False)
    )
    grounding_rate = grounding_grounded / grounding_total if grounding_total else 0.0

    # Prescriptive results
    prescriptive_results = await repo.get_eval_results(season_id, eval_type="prescriptive")
    prescriptive_total = len(prescriptive_results)
    prescriptive_flagged = sum(
        1 for r in prescriptive_results if (r.details_json or {}).get("flagged", False)
    )
    prescriptive_count = sum(
        (r.details_json or {}).get("count", 0) for r in prescriptive_results
    )

    # Behavioral results (Mirror Impact Rate)
    behavioral_results = await repo.get_eval_results(season_id, eval_type="behavioral")
    latest_mir = behavioral_results[0] if behavioral_results else None
    mirror_impact_rate = 0.0
    if latest_mir:
        mirror_impact_rate = (latest_mir.details_json or {}).get("mirror_impact_rate", 0.0)

    # Rubric summary
    from pinwheel.evals.rubric import get_rubric_summary

    rubric_summary = await get_rubric_summary(repo, season_id)

    # Golden dataset (show latest run if available)
    golden_results = await repo.get_eval_results(season_id, eval_type="golden")
    golden_pass_rate = 0.0
    if golden_results:
        passed = sum(1 for r in golden_results if r.score >= 1.0)
        golden_pass_rate = passed / len(golden_results)

    # A/B win rates
    from pinwheel.evals.ab_compare import get_ab_win_rates

    ab_rates = await get_ab_win_rates(repo, season_id)

    # GQI trend (last 5 rounds)
    gqi_results = await repo.get_eval_results(season_id, eval_type="gqi")
    gqi_trend = []
    for r in sorted(gqi_results, key=lambda x: x.round_number)[-5:]:
        details = r.details_json or {}
        gqi_trend.append({
            "round": r.round_number,
            "composite": details.get("composite", 0.0),
            "diversity": details.get("proposal_diversity", 0.0),
            "breadth": details.get("participation_breadth", 0.0),
            "awareness": details.get("consequence_awareness", 0.0),
            "deliberation": details.get("vote_deliberation", 0.0),
        })

    # Active scenario flags
    flag_results = await repo.get_eval_results(season_id, eval_type="flag")
    active_flags = []
    for r in sorted(flag_results, key=lambda x: x.created_at, reverse=True)[:10]:
        details = r.details_json or {}
        active_flags.append({
            "type": details.get("flag_type", r.eval_subtype),
            "severity": details.get("severity", "info"),
            "round": r.round_number,
            "details": {
                k: v for k, v in details.items()
                if k not in ("flag_type", "severity", "created_at")
            },
        })

    # Latest rule evaluation
    rule_eval_results = await repo.get_eval_results(season_id, eval_type="rule_evaluation")
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

    return templates.TemplateResponse(
        request,
        "pages/eval_dashboard.html",
        {
            "active_page": "evals",
            "has_data": True,
            "grounding_rate": grounding_rate,
            "grounding_total": grounding_total,
            "prescriptive_flagged": prescriptive_flagged,
            "prescriptive_total": prescriptive_total,
            "prescriptive_count": prescriptive_count,
            "mirror_impact_rate": mirror_impact_rate,
            "rubric_summary": rubric_summary,
            "golden_pass_rate": golden_pass_rate,
            "ab_rates": ab_rates,
            "gqi_trend": gqi_trend,
            "active_flags": active_flags,
            "latest_rule_eval": latest_rule_eval,
            **_auth_context(request, current_user),
        },
    )
