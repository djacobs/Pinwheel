"""Shared fixtures for eval tests."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from pinwheel.config import Settings
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository


@pytest.fixture
def settings() -> Settings:
    return Settings(
        pinwheel_env="development",
        database_url="sqlite+aiosqlite:///:memory:",
        pinwheel_evals_enabled=True,
    )


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def repo(engine):
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield Repository(session)
        await session.commit()
