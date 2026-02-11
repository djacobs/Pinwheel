"""FastAPI application factory."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pinwheel.api.games import router as games_router
from pinwheel.api.standings import router as standings_router
from pinwheel.api.teams import router as teams_router
from pinwheel.config import Settings
from pinwheel.db.engine import create_engine
from pinwheel.db.models import Base


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: create engine and tables. Shutdown: dispose engine."""
    settings: Settings = app.state.settings
    engine = create_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    yield
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

    # Routers
    app.include_router(games_router)
    app.include_router(teams_router)
    app.include_router(standings_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.pinwheel_env}

    return app


app = create_app()
