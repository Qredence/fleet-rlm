from __future__ import annotations

from fleet_rlm.persistence.database import (
    _POSTGRES_POOL_PRE_PING,
    _POSTGRES_POOL_RECYCLE_SECONDS,
    _pool_kwargs_for_url,
    create_async_engine_from_url,
)


def test_pool_kwargs_postgres_enables_pre_ping_and_recycle() -> None:
    kwargs = _pool_kwargs_for_url("postgresql+asyncpg://u:p@h/db")
    assert kwargs == {
        "pool_pre_ping": _POSTGRES_POOL_PRE_PING,
        "pool_recycle": _POSTGRES_POOL_RECYCLE_SECONDS,
    }


def test_pool_kwargs_sqlite_is_empty() -> None:
    assert _pool_kwargs_for_url("sqlite+aiosqlite:///:memory:") == {}


def test_create_async_engine_applies_postgres_pool_policy() -> None:
    # Private pool attrs are asserted under the pinned sqlalchemy floor in
    # pyproject.toml; revisit if the dependency is bumped.
    engine = create_async_engine_from_url("postgresql://user:password@example.test/fleet")
    pool = engine.sync_engine.pool
    assert pool._pre_ping is True
    assert pool._recycle == _POSTGRES_POOL_RECYCLE_SECONDS


def test_create_async_engine_leaves_sqlite_defaults() -> None:
    engine = create_async_engine_from_url("sqlite:///:memory:")
    pool = engine.sync_engine.pool
    assert not pool._pre_ping
    assert pool._recycle == -1
