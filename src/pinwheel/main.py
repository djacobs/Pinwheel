"""FastAPI application factory."""

import logging
import pathlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pinwheel.api.eval_dashboard import router as eval_dashboard_router
from pinwheel.api.events import router as events_router
from pinwheel.api.games import router as games_router
from pinwheel.api.governance import router as governance_router
from pinwheel.api.mirrors import router as mirrors_router
from pinwheel.api.pages import router as pages_router
from pinwheel.api.standings import router as standings_router
from pinwheel.api.teams import router as teams_router
from pinwheel.auth.oauth import router as auth_router
from pinwheel.config import Settings
from pinwheel.core.event_bus import EventBus
from pinwheel.db.engine import create_engine
from pinwheel.db.models import Base

logger = logging.getLogger(__name__)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: create engine and tables, optionally start Discord bot. Shutdown: cleanup."""
    settings: Settings = app.state.settings
    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.event_bus = EventBus()

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

    yield

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
    app.include_router(mirrors_router)
    app.include_router(events_router)
    app.include_router(eval_dashboard_router)

    # Page routes (must come after API routes so /api/ paths match first)
    app.include_router(pages_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.pinwheel_env}

    return app


app = create_app()
