"""Admin costs dashboard — GET /admin/costs.

Shows AI API usage: total tokens, estimated cost, per-round and per-caller
breakdown. Admin-only in production.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from pinwheel.ai.usage import PRICING
from pinwheel.api.deps import RepoDep
from pinwheel.auth.deps import OptionalUser, admin_auth_context, check_admin_access
from pinwheel.config import PROJECT_ROOT
from pinwheel.db.models import AIUsageLogRow, SeasonRow

router = APIRouter(prefix="/admin", tags=["admin"])

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


async def _get_active_season_id(repo: RepoDep) -> str | None:
    """Get the first season ID."""
    stmt = select(SeasonRow).limit(1)
    result = await repo.session.execute(stmt)
    row = result.scalar_one_or_none()
    return row.id if row else None


@router.get("/costs", response_class=HTMLResponse)
async def costs_dashboard(request: Request, repo: RepoDep, current_user: OptionalUser):
    """AI costs dashboard — token usage, cost breakdown, per-round trends.

    Auth-gated: redirects to login if OAuth is enabled and user is not
    authenticated. In dev mode without OAuth credentials the page is
    accessible to support local testing.
    """
    if denied := check_admin_access(current_user, request):
        return denied

    season_id = await _get_active_season_id(repo)
    session = repo.session

    # --- Summary totals ---
    total_calls = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read_tokens = 0
    total_cost = 0.0
    avg_latency = 0.0

    if season_id:
        stmt = select(
            func.count(AIUsageLogRow.id),
            func.coalesce(func.sum(AIUsageLogRow.input_tokens), 0),
            func.coalesce(func.sum(AIUsageLogRow.output_tokens), 0),
            func.coalesce(func.sum(AIUsageLogRow.cache_read_tokens), 0),
            func.coalesce(func.sum(AIUsageLogRow.cost_usd), 0.0),
            func.coalesce(func.avg(AIUsageLogRow.latency_ms), 0.0),
        ).where(AIUsageLogRow.season_id == season_id)
        result = await session.execute(stmt)
        row = result.one()
        total_calls = row[0] or 0
        total_input_tokens = row[1] or 0
        total_output_tokens = row[2] or 0
        total_cache_read_tokens = row[3] or 0
        total_cost = float(row[4] or 0.0)
        avg_latency = float(row[5] or 0.0)

    # --- Per-caller breakdown ---
    by_caller: list[dict] = []
    if season_id:
        stmt = (
            select(
                AIUsageLogRow.call_type,
                func.count(AIUsageLogRow.id),
                func.coalesce(func.sum(AIUsageLogRow.input_tokens), 0),
                func.coalesce(func.sum(AIUsageLogRow.output_tokens), 0),
                func.coalesce(func.sum(AIUsageLogRow.cache_read_tokens), 0),
                func.coalesce(func.sum(AIUsageLogRow.cost_usd), 0.0),
                func.coalesce(func.avg(AIUsageLogRow.latency_ms), 0.0),
            )
            .where(AIUsageLogRow.season_id == season_id)
            .group_by(AIUsageLogRow.call_type)
            .order_by(func.sum(AIUsageLogRow.cost_usd).desc())
        )
        result = await session.execute(stmt)
        for row in result.all():
            by_caller.append({
                "call_type": row[0],
                "count": row[1],
                "input_tokens": row[2],
                "output_tokens": row[3],
                "cache_read_tokens": row[4],
                "cost_usd": float(row[5]),
                "avg_latency_ms": round(float(row[6]), 1),
            })

    # --- Per-round breakdown ---
    by_round: list[dict] = []
    if season_id:
        stmt = (
            select(
                AIUsageLogRow.round_number,
                func.count(AIUsageLogRow.id),
                func.coalesce(func.sum(AIUsageLogRow.input_tokens), 0),
                func.coalesce(func.sum(AIUsageLogRow.output_tokens), 0),
                func.coalesce(func.sum(AIUsageLogRow.cost_usd), 0.0),
            )
            .where(
                AIUsageLogRow.season_id == season_id,
                AIUsageLogRow.round_number.isnot(None),
            )
            .group_by(AIUsageLogRow.round_number)
            .order_by(AIUsageLogRow.round_number)
        )
        result = await session.execute(stmt)
        for row in result.all():
            by_round.append({
                "round_number": row[0],
                "count": row[1],
                "input_tokens": row[2],
                "output_tokens": row[3],
                "cost_usd": float(row[4]),
            })

    # --- Compute average cost per round ---
    avg_cost_per_round = 0.0
    if by_round:
        avg_cost_per_round = total_cost / len(by_round)

    # --- Cache hit rate ---
    cache_hit_rate = 0.0
    if total_input_tokens > 0:
        cache_hit_rate = total_cache_read_tokens / (total_input_tokens + total_cache_read_tokens)

    # --- Pricing reference ---
    pricing_ref = [
        {"model": model, **rates}
        for model, rates in PRICING.items()
    ]

    return templates.TemplateResponse(
        request,
        "pages/admin_costs.html",
        {
            "active_page": "admin",
            "has_data": total_calls > 0,
            "total_calls": total_calls,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cache_read_tokens": total_cache_read_tokens,
            "total_cost": total_cost,
            "avg_latency": round(avg_latency, 1),
            "avg_cost_per_round": avg_cost_per_round,
            "cache_hit_rate": cache_hit_rate,
            "by_caller": by_caller,
            "by_round": by_round,
            "pricing_ref": pricing_ref,
            **admin_auth_context(request, current_user),
        },
    )
