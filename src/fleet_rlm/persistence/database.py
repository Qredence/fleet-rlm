"""Async database primitives for Fleet RLM.

Production schema evolution belongs to Alembic. ``create_tables`` remains an
explicit helper for private SQLite tests only.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fleet_rlm.persistence.models import Base


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when a database URL is required but missing."""


class DatabaseCompatibilityError(RuntimeError):
    """Raised when a reachable database is not at the canonical Alembic head."""


class DatabaseConnectionError(RuntimeError):
    """Raised when database connectivity cannot be validated safely."""


# Single remediation path surfaced wherever migrations drift.
REMEDIATION = "run `uv run python scripts/db_init.py`"


def is_sqlite_url(url: str) -> bool:
    """Return True when the URL targets SQLite (offline/test helper path)."""
    cleaned = url.strip()
    if not cleaned:
        return False
    normalized = normalize_database_url(cleaned)
    return normalized.startswith("sqlite")


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


# Lakebase Postgres endpoints suspend when idle (scale-to-zero) and enforce a
# 24h idle / 3-day max connection lifetime. Pre-ping detects connections closed
# during suspension; recycle retires pooled connections well before the idle
# bound so long-lived processes never reuse a server-closed connection.
_POSTGRES_POOL_PRE_PING = True
_POSTGRES_POOL_RECYCLE_SECONDS = 1800


def _pool_kwargs_for_url(normalized_url: str) -> dict[str, object]:
    """Pool kwargs for a normalized URL; Postgres only, SQLite keeps defaults."""
    if normalized_url.startswith("postgresql+asyncpg://"):
        return {
            "pool_pre_ping": _POSTGRES_POOL_PRE_PING,
            "pool_recycle": _POSTGRES_POOL_RECYCLE_SECONDS,
        }
    return {}


def create_async_engine_from_url(url: str, *, echo: bool = False) -> AsyncEngine:
    """Build an async engine. Does not open connections until first use."""
    normalized = normalize_database_url(url)
    return create_async_engine(normalized, echo=echo, **_pool_kwargs_for_url(normalized))


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def check_database_compatibility(
    database_url: str,
    *,
    repo_root: Path | None = None,
) -> None:
    """Require a reachable database whose Alembic revision matches every head."""
    try:
        engine = create_async_engine_from_url(database_url)
    except (OSError, SQLAlchemyError) as exc:
        raise DatabaseConnectionError("database connectivity check failed") from exc
    try:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                has_revision_table = await connection.run_sync(
                    lambda sync_connection: inspect(sync_connection).has_table("alembic_version")
                )
                if not has_revision_table:
                    raise DatabaseCompatibilityError("database revision does not match Alembic head")
                result = await connection.execute(text("SELECT version_num FROM alembic_version"))
                current_revisions = {str(revision) for revision in result.scalars().all()}
        except (OSError, SQLAlchemyError) as exc:
            raise DatabaseConnectionError("database connectivity check failed") from exc

        root = repo_root or Path(__file__).resolve().parents[3]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        expected_revisions = set(ScriptDirectory.from_config(config).get_heads())
        if not expected_revisions or current_revisions != expected_revisions:
            raise DatabaseCompatibilityError("database revision does not match Alembic head")
    finally:
        await engine.dispose()


async def ensure_database_compatible(database_url: str, *, repo_root: Path | None = None) -> None:
    """Fail closed on unreachable DB or non-head schema, with sanitized cause.

    Used by the supervisor preflight, composition startup, and the doctor
    diagnostic so the failure policy and remediation message stay in one
    place. Chains the original error for logs while exposing only closed
    public messages.
    """
    try:
        await check_database_compatibility(database_url, repo_root=repo_root)
    except DatabaseCompatibilityError as exc:
        raise DatabaseCompatibilityError(f"Fleet database is not at Alembic head; {REMEDIATION}") from exc
    except DatabaseConnectionError:
        raise
    except Exception as exc:  # OSError, alembic CommandError, SQLAlchemyError
        raise DatabaseConnectionError("Fleet database compatibility could not be verified") from exc


async def create_tables(engine: AsyncEngine) -> None:
    """Create an ephemeral/offline schema; never call this from live startup."""
    # Import models so metadata is populated.
    from fleet_rlm.persistence import models as _models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
