"""FastAPI application factory."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pinwheel.api.eval_dashboard import router as eval_dashboard_router
from pinwheel.api.events import router as events_router
from pinwheel.api.games import router as games_router
from pinwheel.api.governance import router as governance_router
from pinwheel.api.pace import router as pace_router
from pinwheel.api.pages import router as pages_router
from pinwheel.api.reports import router as reports_router
from pinwheel.api.seasons import router as seasons_router
from pinwheel.api.standings import router as standings_router
from pinwheel.api.teams import router as teams_router
from pinwheel.auth.oauth import router as auth_router
from pinwheel.config import PROJECT_ROOT, Settings
from pinwheel.core.event_bus import EventBus
from pinwheel.core.presenter import PresentationState
from pinwheel.db.engine import create_engine
from pinwheel.db.models import Base

logger = logging.getLogger(__name__)


async def _add_column_if_missing(
    conn: object,
    table: str,
    column: str,
    col_def: str,
) -> None:
    """Add a column to an existing table if it doesn't already exist (SQLite)."""
    from sqlalchemy import text

    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    columns = {row[1] for row in result}
    if column not in columns:
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"))
        logger.info("migration: added %s.%s", table, column)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: create engine/tables, optionally start Discord bot and scheduler."""
    settings: Settings = app.state.settings
    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Inline migration: add columns that create_all won't add to existing tables
        await _add_column_if_missing(conn, "game_results", "presented", "BOOLEAN DEFAULT 0")
        await _add_column_if_missing(
            conn,
            "teams",
            "color_secondary",
            "VARCHAR(7) DEFAULT '#ffffff'",
        )
        await _add_column_if_missing(conn, "players", "team_id", "VARCHAR(36)")
        await _add_column_if_missing(conn, "players", "enrolled_season_id", "VARCHAR(36)")
    app.state.engine = engine
    app.state.event_bus = EventBus()
    app.state.presentation_state = PresentationState()

    # Startup recovery: try to resume an interrupted presentation, otherwise
    # mark unpresented games as presented so they don't vanish.
    from pinwheel.core.scheduler_runner import resume_presentation

    resumed = await resume_presentation(
        engine=engine,
        event_bus=app.state.event_bus,
        presentation_state=app.state.presentation_state,
        quarter_replay_seconds=settings.pinwheel_quarter_replay_seconds,
    )

    if not resumed:
        from sqlalchemy import func, select, update

        from pinwheel.db.models import GameResultRow

        async with engine.begin() as conn:
            count_result = await conn.execute(
                select(func.count()).where(GameResultRow.presented.is_(False))
            )
            unpresented = count_result.scalar_one()
            if unpresented > 0:
                await conn.execute(
                    update(GameResultRow)
                    .where(GameResultRow.presented.is_(False))
                    .values(presented=True)
                )
                logger.info(
                    "startup_recovery: marked %d games as presented",
                    unpresented,
                )

    # Start Discord bot if configured
    discord_bot = None
    from pinwheel.discord.bot import is_discord_enabled

    if is_discord_enabled(settings):
        from pinwheel.discord.bot import start_discord_bot

        discord_bot = await start_discord_bot(settings, app.state.event_bus, engine)
        app.state.discord_bot = discord_bot
        logger.info("discord_bot_integration_started")
    else:
        logger.info("discord_bot_integration_disabled")

    # Start APScheduler for automatic round advancement
    scheduler = None
    effective_cron = settings.effective_game_cron()
    if settings.pinwheel_auto_advance and effective_cron is not None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        from pinwheel.core.scheduler_runner import tick_round

        scheduler = AsyncIOScheduler()
        trigger = CronTrigger.from_crontab(effective_cron)
        scheduler.add_job(
            tick_round,
            trigger=trigger,
            kwargs={
                "engine": engine,
                "event_bus": app.state.event_bus,
                "api_key": settings.anthropic_api_key,
                "presentation_state": app.state.presentation_state,
                "presentation_mode": settings.pinwheel_presentation_mode,
                "game_interval_seconds": settings.pinwheel_game_interval_seconds,
                "quarter_replay_seconds": settings.pinwheel_quarter_replay_seconds,
                "governance_interval": settings.pinwheel_governance_interval,
            },
            id="tick_round",
            name="Advance game round",
            replace_existing=True,
        )
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info(
            "scheduler_started cron=%s pace=%s",
            effective_cron,
            settings.pinwheel_presentation_pace,
        )
    else:
        app.state.scheduler = None
        logger.info(
            "scheduler_disabled pace=%s",
            settings.pinwheel_presentation_pace,
        )

    yield

    # Shutdown scheduler
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")

    # Shutdown Discord bot if running
    if discord_bot is not None:
        await discord_bot.close()
        logger.info("discord_bot_integration_stopped")

    await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the Pinwheel FastAPI application."""
    settings = settings or Settings()

    logging.basicConfig(
        level=getattr(logging, settings.pinwheel_log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = FastAPI(
        title="Pinwheel Fates",
        version="0.1.0",
        description="Auto-simulated 3v3 basketball league with AI-interpreted governance",
        docs_url="/docs" if settings.pinwheel_env != "production" else None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Static files
    static_dir = PROJECT_ROOT / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Auth routes
    app.include_router(auth_router)

    # API routers
    app.include_router(games_router)
    app.include_router(teams_router)
    app.include_router(standings_router)
    app.include_router(governance_router)
    app.include_router(reports_router)
    app.include_router(events_router)
    app.include_router(eval_dashboard_router)
    app.include_router(pace_router)
    app.include_router(seasons_router)

    # Page routes (must come after API routes so /api/ paths match first)
    app.include_router(pages_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.pinwheel_env}

    return app


app = create_app()
