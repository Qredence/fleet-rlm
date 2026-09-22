"""Database engine, managed-URL policy, and persistence observation contracts.

* ``test_persistence_observations.py``: Persistence observations retain outcomes without capturing private SQL data.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from fleet_rlm.observability import tracing
from fleet_rlm.persistence.database import (
    _POSTGRES_CONNECT_TIMEOUT_SECONDS,
    _POSTGRES_POOL_PRE_PING,
    _POSTGRES_POOL_RECYCLE_SECONDS,
    ManagedDatabasePolicyError,
    _pool_kwargs_for_url,
    create_async_engine_from_url,
    observe_database_operation,
    validate_managed_postgres_url,
)


# --- from test_engine_pool_policy.py ----------------------------------
def test_pool_kwargs_postgres_enables_pre_ping_recycle_and_connect_timeout() -> None:
    kwargs = _pool_kwargs_for_url("postgresql+asyncpg://u:p@h/db")
    assert kwargs == {
        "pool_pre_ping": _POSTGRES_POOL_PRE_PING,
        "pool_recycle": _POSTGRES_POOL_RECYCLE_SECONDS,
        "connect_args": {"timeout": _POSTGRES_CONNECT_TIMEOUT_SECONDS},
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


# --- from test_managed_database_policy.py -----------------------------
@pytest.mark.parametrize(
    "url",
    (
        "sqlite+aiosqlite:///fleet.sqlite3",
        "postgresql://other:password@lakebase.example/fleet?sslmode=require",
        "postgresql://fleet_app:password@lakebase.example/fleet",
    ),
)
def test_managed_database_policy_rejects_non_durable_or_non_tls_urls(url: str) -> None:
    with pytest.raises(ManagedDatabasePolicyError):
        validate_managed_postgres_url(url)


def test_managed_database_policy_accepts_tls_durable_role() -> None:
    validate_managed_postgres_url("postgresql://fleet_app:password@lakebase.example/fleet?sslmode=require")


# --- from test_persistence_observations.py ----------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure, outcome",
    [
        (None, "completed"),
        (RuntimeError, "failed"),
        (SQLAlchemyError, "database_error"),
        (TimeoutError, "timeout"),
        (asyncio.CancelledError, "cancelled"),
    ],
)
async def test_observations_preserve_results_and_only_emit_bounded_metadata(monkeypatch, caplog, failure, outcome):
    observations = []
    secret = "private-sql-parameter-sentinel"

    def start(name, *, inputs):
        observations.append((name, inputs))
        return SimpleNamespace(finish=lambda **kwargs: observations.append(kwargs))

    monkeypatch.setattr(tracing, "start_turn_span", start)
    raised = failure(secret) if failure else None
    returned = object()

    @observe_database_operation("claim")
    async def operation(_private_value):
        if raised is not None:
            raise raised
        return returned

    with caplog.at_level(logging.INFO, logger="fleet_rlm.persistence.database"):
        if raised is None:
            assert await operation(secret) is returned
        else:
            with pytest.raises(type(raised)) as caught:
                await operation(secret)
            assert caught.value is raised
    assert observations[0] == ("database.claim", {"operation": "claim"})
    assert observations[1]["outputs"]["outcome"] == outcome
    assert observations[1]["outputs"]["duration_ms"] >= 0
    assert secret not in repr(observations) + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["start", "finish", "log"])
@pytest.mark.parametrize("fails", [False, True])
async def test_observation_failures_do_not_change_repository_outcome(monkeypatch, broken, fails):
    from fleet_rlm.persistence import database

    def explode(*_args, **_kwargs):
        raise RuntimeError("observation unavailable")

    monkeypatch.setattr(
        tracing,
        "start_turn_span",
        explode
        if broken == "start"
        else lambda *_args, **_kwargs: SimpleNamespace(
            finish=explode if broken == "finish" else lambda **_kwargs: None
        ),
    )
    if broken == "log":
        monkeypatch.setattr(database.logger, "info", explode)
    failure = ValueError("repository failure")

    @observe_database_operation("commit")
    async def operation():
        if fails:
            raise failure
        return 42

    if fails:
        with pytest.raises(ValueError) as caught:
            await operation()
        assert caught.value is failure
    else:
        assert await operation() == 42
