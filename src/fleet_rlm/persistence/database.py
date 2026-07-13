"""Async database primitives for Fleet RLM.

Production schema evolution belongs to Alembic. ``create_tables`` remains an
explicit helper for hermetic SQLite tests and offline development only.
"""

from __future__ import annotations

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fleet_rlm.persistence.models import Base


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when a database URL is required but missing."""


def normalize_database_url(url: str) -> str:
    """Accept postgres:// and sqlite paths; ensure async drivers when needed."""
    cleaned = url.strip()
    if not cleaned:
        raise DatabaseNotConfiguredError("database URL is empty")
    if cleaned.startswith("postgres://"):
        cleaned = "postgresql+asyncpg://" + cleaned.removeprefix("postgres://")
    elif cleaned.startswith("postgresql://") and "+asyncpg" not in cleaned:
        cleaned = "postgresql+asyncpg://" + cleaned.removeprefix("postgresql://")
    elif cleaned.startswith("sqlite://") and "+aiosqlite" not in cleaned:
        cleaned = cleaned.replace("sqlite://", "sqlite+aiosqlite://", 1)
    if cleaned.startswith("postgresql+asyncpg://"):
        # ``channel_binding`` is a libpq/psycopg option. SQLAlchemy forwards URL
        # query parameters to asyncpg.connect(), which does not accept it.
        # asyncpg accepts the equivalent SSL mode through its ``ssl`` option.
        parsed = make_url(cleaned)
        query = dict(parsed.query)
        changed = query.pop("channel_binding", None) is not None
        sslmode = query.pop("sslmode", None)
        if sslmode is not None:
            changed = True
            query.setdefault("ssl", sslmode)
        if changed:
            return (
                parsed.difference_update_query(list(parsed.query))
                .update_query_dict(query)  # ty: ignore[invalid-argument-type] - SQLAlchemy accepts tuple values
                .render_as_string(
                    hide_password=False,
                )
            )
    return cleaned


def create_async_engine_from_url(url: str, *, echo: bool = False) -> AsyncEngine:
    """Build an async engine. Does not open connections until first use."""
    return create_async_engine(normalize_database_url(url), echo=echo)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def create_tables(engine: AsyncEngine) -> None:
    """Create an ephemeral/offline schema; never call this from live startup."""
    # Import models so metadata is populated.
    from fleet_rlm.persistence import models as _models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
