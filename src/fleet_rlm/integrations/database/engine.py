"""Database engine/session management for Neon Postgres."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _normalize_async_query(database_url: str) -> str:
    """Normalize query params for asyncpg compatibility."""
    parsed = urlparse(database_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))

    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode and "ssl" not in query:
        query["ssl"] = "require" if sslmode != "disable" else "disable"

    normalized = parsed._replace(query=urlencode(query))
    return urlunparse(normalized)


def to_async_database_url(database_url: str) -> str:
    """Normalize connection URL for SQLAlchemy async usage."""
    if database_url.startswith("postgresql+asyncpg://"):
        return _normalize_async_query(database_url)
    if database_url.startswith("postgresql+psycopg://"):
        return _normalize_async_query(
            database_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
        )
    if database_url.startswith("postgresql://"):
        return _normalize_async_query(
            database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        )
    if database_url.startswith("postgres://"):
        return _normalize_async_query(
            database_url.replace("postgres://", "postgresql+asyncpg://", 1)
        )
    return _normalize_async_query(database_url)


def to_sync_database_url(database_url: str) -> str:
    """Normalize connection URL for SQLAlchemy sync usage (Alembic)."""
    if database_url.startswith("postgresql+psycopg://"):
        return database_url
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    return database_url


class DatabaseManager:
    """Manage async SQLAlchemy engine and sessions."""

    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        self._database_url = to_async_database_url(database_url)
        self._echo = echo
        self._engine: AsyncEngine | None = None
        self._session_maker: async_sessionmaker[AsyncSession] | None = None

    @property
    def database_url(self) -> str:
        return self._database_url

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(
                self._database_url,
                echo=self._echo,
                pool_pre_ping=True,
                future=True,
            )
        return self._engine

    @property
    def session_maker(self) -> async_sessionmaker[AsyncSession]:
        if self._session_maker is None:
            self._session_maker = async_sessionmaker(
                self.engine,
                expire_on_commit=False,
                class_=AsyncSession,
            )
        return self._session_maker

    async def ping(self) -> None:
        """Validate database connectivity."""
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._session_maker = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield an async database session."""
        async with self.session_maker() as session:
            yield session
