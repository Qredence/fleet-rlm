"""Async engine and schema bootstrap for the clean package."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fleet_rlm_clean.persistence.models import Base


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when a database URL is required but missing."""


def normalize_database_url(url: str) -> str:
    """Accept postgres:// and sqlite paths; ensure async drivers when needed."""
    cleaned = url.strip()
    if not cleaned:
        raise DatabaseNotConfiguredError("database URL is empty")
    if cleaned.startswith("postgres://"):
        return "postgresql+asyncpg://" + cleaned.removeprefix("postgres://")
    if cleaned.startswith("postgresql://") and "+asyncpg" not in cleaned:
        return "postgresql+asyncpg://" + cleaned.removeprefix("postgresql://")
    if cleaned.startswith("sqlite://") and "+aiosqlite" not in cleaned:
        return cleaned.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return cleaned


def create_async_engine_from_url(url: str, *, echo: bool = False) -> AsyncEngine:
    """Build an async engine. Does not open connections until first use."""
    return create_async_engine(normalize_database_url(url), echo=echo)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def create_tables(engine: AsyncEngine) -> None:
    """Bootstrap schema (create_all). Alembic migrations can replace this later."""
    # Import models so metadata is populated.
    from fleet_rlm_clean.persistence import models as _models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
