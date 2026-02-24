"""Performance dashboard route — GET /admin/perf.

Shows system performance metrics aggregated from existing database tables:
- Round timing (game count, AI call duration per round)
- AI call latency percentiles by call_type
- SSE connection stats
- System health (DB size, game/round counts, uptime estimate)

Admin-only in production. No new tables — aggregates from GameResultRow,
AIUsageLogRow, and the SSE connection semaphore.
"""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from pinwheel.api.deps import RepoDep
from pinwheel.auth.deps import OptionalUser, admin_auth_context, check_admin_access
from pinwheel.config import PROJECT_ROOT, Settings
from pinwheel.db.models import (
    AIUsageLogRow,
    GameResultRow,
    GovernanceEventRow,
    ReportRow,
    SeasonRow,
)

router = APIRouter(prefix="/admin", tags=["admin"])

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))

# Store app startup time for uptime calculation.
_APP_START_TIME = time.monotonic()


def _format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.0f}m"
    hours = seconds / 3600
    if hours < 24:
        return f"{hours:.1f}h"
    days = hours / 24
    return f"{days:.1f}d"


def _compute_percentiles(values: list[float]) -> dict[str, float]:
    """Compute P50, P95, P99 from a sorted list of values.

    Returns a dict with keys 'p50', 'p95', 'p99'.  Returns zeros if the
    list is empty.
    """
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    values_sorted = sorted(values)
    n = len(values_sorted)

    def _pct(p: float) -> float:
        idx = int(p / 100.0 * (n - 1))
        idx = max(0, min(idx, n - 1))
        return values_sorted[idx]

    return {
        "p50": round(_pct(50), 1),
        "p95": round(_pct(95), 1),
        "p99": round(_pct(99), 1),
    }


async def _get_active_season_id(repo: RepoDep) -> str | None:
    """Get the active season ID (most recent non-terminal)."""
    row = await repo.get_active_season()
    return row.id if row else None


@router.get("/perf", response_class=HTMLResponse)
async def perf_dashboard(
    request: Request, repo: RepoDep, current_user: OptionalUser
) -> HTMLResponse:
    """Performance dashboard — system metrics aggregated from existing tables.

    Auth-gated: redirects to login if OAuth is enabled and user is not
    authenticated. In dev mode without OAuth credentials the page is
    accessible to support local testing.
    """
    if denied := check_admin_access(current_user, request):
        return denied

    session = repo.session
    season_id = await _get_active_season_id(repo)

    # -----------------------------------------------------------------------
    # 1. Round timing — games per round, AI calls per round, AI latency/round
    # -----------------------------------------------------------------------
    round_timing: list[dict] = []
    if season_id:
        # Games per round
        games_stmt = (
            select(
                GameResultRow.round_number,
                func.count(GameResultRow.id).label("game_count"),
                func.min(GameResultRow.created_at).label("started_at"),
                func.max(GameResultRow.created_at).label("finished_at"),
            )
            .where(GameResultRow.season_id == season_id)
            .group_by(GameResultRow.round_number)
            .order_by(GameResultRow.round_number)
        )
        games_result = await session.execute(games_stmt)
        games_by_round: dict[int, dict] = {}
        for row in games_result.all():
            games_by_round[row.round_number] = {
                "game_count": row.game_count,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
            }

        # AI calls per round (count + total latency)
        ai_stmt = (
            select(
                AIUsageLogRow.round_number,
                func.count(AIUsageLogRow.id).label("ai_calls"),
                func.coalesce(func.sum(AIUsageLogRow.latency_ms), 0.0).label(
                    "total_ai_latency_ms"
                ),
                func.coalesce(func.avg(AIUsageLogRow.latency_ms), 0.0).label(
                    "avg_ai_latency_ms"
                ),
            )
            .where(
                AIUsageLogRow.season_id == season_id,
                AIUsageLogRow.round_number.isnot(None),
            )
            .group_by(AIUsageLogRow.round_number)
            .order_by(AIUsageLogRow.round_number)
        )
        ai_result = await session.execute(ai_stmt)
        ai_by_round: dict[int, dict] = {}
        for row in ai_result.all():
            ai_by_round[row.round_number] = {
                "ai_calls": row.ai_calls,
                "total_ai_latency_ms": float(row.total_ai_latency_ms),
                "avg_ai_latency_ms": round(float(row.avg_ai_latency_ms), 1),
            }

        # Merge
        all_rounds = sorted(set(games_by_round.keys()) | set(ai_by_round.keys()))
        for rn in all_rounds:
            g = games_by_round.get(rn, {})
            a = ai_by_round.get(rn, {})
            round_timing.append(
                {
                    "round_number": rn,
                    "game_count": g.get("game_count", 0),
                    "ai_calls": a.get("ai_calls", 0),
                    "total_ai_latency_ms": a.get("total_ai_latency_ms", 0.0),
                    "avg_ai_latency_ms": a.get("avg_ai_latency_ms", 0.0),
                }
            )

    # Compute averages across all rounds
    avg_games_per_round = 0.0
    avg_ai_calls_per_round = 0.0
    avg_ai_latency_per_round = 0.0
    if round_timing:
        avg_games_per_round = sum(r["game_count"] for r in round_timing) / len(
            round_timing
        )
        avg_ai_calls_per_round = sum(r["ai_calls"] for r in round_timing) / len(
            round_timing
        )
        ai_rounds_with_data = [
            r for r in round_timing if r["total_ai_latency_ms"] > 0
        ]
        if ai_rounds_with_data:
            avg_ai_latency_per_round = sum(
                r["total_ai_latency_ms"] for r in ai_rounds_with_data
            ) / len(ai_rounds_with_data)

    # -----------------------------------------------------------------------
    # 2. AI call latency percentiles by call_type
    # -----------------------------------------------------------------------
    ai_latency_by_type: list[dict] = []
    if season_id:
        # Fetch all latency values grouped by call_type
        latency_stmt = (
            select(AIUsageLogRow.call_type, AIUsageLogRow.latency_ms)
            .where(
                AIUsageLogRow.season_id == season_id,
                AIUsageLogRow.latency_ms > 0,
            )
            .order_by(AIUsageLogRow.call_type, AIUsageLogRow.latency_ms)
        )
        latency_result = await session.execute(latency_stmt)
        # Group by call_type in Python
        latency_groups: dict[str, list[float]] = {}
        for row in latency_result.all():
            latency_groups.setdefault(row.call_type, []).append(
                float(row.latency_ms)
            )

        for call_type in sorted(latency_groups.keys()):
            vals = latency_groups[call_type]
            pcts = _compute_percentiles(vals)
            ai_latency_by_type.append(
                {
                    "call_type": call_type,
                    "count": len(vals),
                    "avg_ms": round(sum(vals) / len(vals), 1),
                    **pcts,
                }
            )

    # Overall AI latency percentiles
    all_ai_latencies: list[float] = []
    if season_id:
        overall_stmt = (
            select(AIUsageLogRow.latency_ms)
            .where(
                AIUsageLogRow.season_id == season_id,
                AIUsageLogRow.latency_ms > 0,
            )
            .order_by(AIUsageLogRow.latency_ms)
        )
        overall_result = await session.execute(overall_stmt)
        all_ai_latencies = [float(row[0]) for row in overall_result.all()]
    overall_ai_pcts = _compute_percentiles(all_ai_latencies)

    # -----------------------------------------------------------------------
    # 3. SSE connection stats
    # -----------------------------------------------------------------------
    sse_stats: dict[str, int] = {"active": 0, "max": 100}
    try:
        from pinwheel.api.events import _MAX_SSE_CONNECTIONS, _connection_semaphore

        sse_stats["max"] = _MAX_SSE_CONNECTIONS
        sse_stats["active"] = _MAX_SSE_CONNECTIONS - _connection_semaphore._value  # noqa: SLF001
    except (ImportError, AttributeError):
        pass

    # Also get EventBus subscriber count if available
    event_bus_subscribers = 0
    try:
        bus = request.app.state.event_bus
        event_bus_subscribers = bus.subscriber_count
    except AttributeError:
        pass

    # -----------------------------------------------------------------------
    # 4. System health
    # -----------------------------------------------------------------------
    total_games = 0
    total_rounds = 0
    total_reports = 0
    total_governance_events = 0
    total_ai_calls = 0
    total_seasons = 0
    if season_id:
        # Games this season
        count_games = await session.execute(
            select(func.count(GameResultRow.id)).where(
                GameResultRow.season_id == season_id
            )
        )
        total_games = count_games.scalar() or 0

        # Distinct rounds this season
        count_rounds = await session.execute(
            select(func.count(func.distinct(GameResultRow.round_number))).where(
                GameResultRow.season_id == season_id
            )
        )
        total_rounds = count_rounds.scalar() or 0

        # Reports this season
        count_reports = await session.execute(
            select(func.count(ReportRow.id)).where(
                ReportRow.season_id == season_id
            )
        )
        total_reports = count_reports.scalar() or 0

        # Governance events this season
        count_gov = await session.execute(
            select(func.count(GovernanceEventRow.id)).where(
                GovernanceEventRow.season_id == season_id
            )
        )
        total_governance_events = count_gov.scalar() or 0

        # AI calls this season
        count_ai = await session.execute(
            select(func.count(AIUsageLogRow.id)).where(
                AIUsageLogRow.season_id == season_id
            )
        )
        total_ai_calls = count_ai.scalar() or 0

    # Total seasons
    count_seasons = await session.execute(select(func.count(SeasonRow.id)))
    total_seasons = count_seasons.scalar() or 0

    # Database file size
    db_size_mb = 0.0
    settings: Settings = request.app.state.settings
    db_url = settings.database_url
    if db_url and "///" in db_url:
        db_path = db_url.split("///", 1)[1]
        if db_path and os.path.exists(db_path):
            db_size_mb = os.path.getsize(db_path) / (1024 * 1024)

    # Uptime
    uptime_seconds = time.monotonic() - _APP_START_TIME
    uptime_str = _format_duration(uptime_seconds)

    # -----------------------------------------------------------------------
    # Recent rounds (last 10) for the round timing table
    # -----------------------------------------------------------------------
    recent_rounds = round_timing[-10:] if round_timing else []

    return templates.TemplateResponse(
        request,
        "pages/admin_perf.html",
        {
            "active_page": "admin",
            "has_data": total_games > 0 or total_ai_calls > 0,
            # Round timing
            "round_timing": recent_rounds,
            "total_rounds_played": total_rounds,
            "avg_games_per_round": round(avg_games_per_round, 1),
            "avg_ai_calls_per_round": round(avg_ai_calls_per_round, 1),
            "avg_ai_latency_per_round": round(avg_ai_latency_per_round, 1),
            # AI latency
            "ai_latency_by_type": ai_latency_by_type,
            "overall_ai_pcts": overall_ai_pcts,
            "total_ai_calls": total_ai_calls,
            # SSE
            "sse_stats": sse_stats,
            "event_bus_subscribers": event_bus_subscribers,
            # System health
            "total_games": total_games,
            "total_rounds": total_rounds,
            "total_reports": total_reports,
            "total_governance_events": total_governance_events,
            "total_seasons": total_seasons,
            "db_size_mb": round(db_size_mb, 2),
            "uptime": uptime_str,
            **admin_auth_context(request, current_user),
        },
    )
