"""Legacy SQLite compatibility helpers for deprecated CRUD routes.

This module is intentionally lazy: no SQLite engine/session factory is created
until legacy routes are explicitly enabled and used.
"""

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite+aiosqlite:///./fleet_rlm.db"
_legacy_sqlite_enabled = False
_engine: AsyncEngine | None = None
_async_session_maker: async_sessionmaker[AsyncSession] | None = None


def set_legacy_sqlite_enabled(enabled: bool) -> None:
    """Enable/disable legacy SQLite runtime paths."""
    global _legacy_sqlite_enabled
    _legacy_sqlite_enabled = enabled


def _ensure_sqlite_enabled() -> None:
    if not _legacy_sqlite_enabled:
        raise RuntimeError(
            "Legacy SQLite routes are disabled. Set LEGACY_SQLITE_ROUTES_ENABLED=true "
            "to enable compatibility database access."
        )


def _ensure_session_maker() -> async_sessionmaker[AsyncSession]:
    global _engine, _async_session_maker
    _ensure_sqlite_enabled()
    if _async_session_maker is not None:
        return _async_session_maker

    # connect_args={"check_same_thread": False} is required for SQLite.
    _engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
    )
    _async_session_maker = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return _async_session_maker


async def init_db() -> None:
    """Initialize the database by creating all tables."""
    _ensure_sqlite_enabled()
    _ensure_session_maker()
    assert _engine is not None

    logger.info("Initializing database schema...")
    async with _engine.begin() as conn:
        # Create all tables defined by SQLModel
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.info("Database schema initialized.")


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing database sessions to FastAPI endpoints."""
    session_maker = _ensure_session_maker()
    async with session_maker() as session:
        yield session
