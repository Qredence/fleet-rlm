"""PostgreSQL / Lakebase persistence package for Fleet-RLM Lite."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import psycopg
import psycopg_pool
from psycopg import sql
from psycopg_pool import ConnectionPool

from fleet_rlm.store.leases import (
    HEARTBEAT_INTERVAL_SECONDS,
    LEASE_TTL_SECONDS,
    LeaseHeartbeatService,
    LeaseStoreMixin,
    PooledLease,
    lakebase_connection_class,
)
from fleet_rlm.store.optimization import OptimizationStoreMixin
from fleet_rlm.store.replays import ReplayStoreMixin
from fleet_rlm.store.schema import (
    _OPTIMIZATION_LOCK_SCOPE,
    _POOL_MAX_IDLE_SECONDS,
    _POOL_MAX_LIFETIME_SECONDS,
    _POOL_RECONNECT_TIMEOUT_SECONDS,
    _REPLAY_RETENTION_DAYS,
    _RETRY_DELAY_SECONDS,
    _SCHEMA_MIGRATIONS,
    _SCHEMA_VERSION_TABLE,
    JOB_KINDS,
    SCHEMA_STATEMENTS,
    SCHEMA_VERSION,
    IdempotencyConflictError,
    _retry_once,
)
from fleet_rlm.store.sessions import SessionStoreMixin

__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "JOB_KINDS",
    "LEASE_TTL_SECONDS",
    "SCHEMA_STATEMENTS",
    "SCHEMA_VERSION",
    "_OPTIMIZATION_LOCK_SCOPE",
    "_POOL_MAX_IDLE_SECONDS",
    "_POOL_MAX_LIFETIME_SECONDS",
    "_POOL_RECONNECT_TIMEOUT_SECONDS",
    "_REPLAY_RETENTION_DAYS",
    "_RETRY_DELAY_SECONDS",
    "_SCHEMA_MIGRATIONS",
    "_SCHEMA_VERSION_TABLE",
    "IdempotencyConflictError",
    "LeaseHeartbeatService",
    "PooledLease",
    "Store",
    "_retry_once",
    "lakebase_connection_class",
    "time",
]


class Store(LeaseStoreMixin, SessionStoreMixin, ReplayStoreMixin, OptimizationStoreMixin):
    """Durable PostgreSQL state for Sessions, sandbox ownership, memories, replays, and optimization."""

    def __init__(
        self,
        pool: Any,
        dsn: str = "",
        connection_class: Any = psycopg.Connection,
        *,
        migration_pool: Any = None,
        schema: str = "public",
        connection_kwargs: dict[str, Any] | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.pool = pool
        self.dsn = dsn
        self.connection_class = connection_class
        self.migration_pool = migration_pool or pool
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
            raise ValueError("database schema must be a lowercase PostgreSQL identifier")
        self.schema = schema
        self.connection_kwargs = connection_kwargs or {}
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        self.heartbeat = LeaseHeartbeatService(self)

    @classmethod
    def open(
        cls,
        dsn: str,
        *,
        wait: bool = False,
        max_size: int = 20,
        lakebase_endpoint: str = "",
        migration_dsn: str = "",
        schema: str = "public",
    ) -> Store:
        """Open a pooled store. Runtime connections size to handle concurrent Turns and leases."""
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
            raise ValueError("database schema must be a lowercase PostgreSQL identifier")
        connection_class: type[psycopg.Connection[Any]] = psycopg.Connection
        if lakebase_endpoint:
            from databricks.sdk import WorkspaceClient

            connection_class = lakebase_connection_class(WorkspaceClient(), lakebase_endpoint)
        connection_kwargs: dict[str, Any] = {"connect_timeout": 30, "autocommit": True}
        if schema != "public":
            connection_kwargs["options"] = f"-c search_path={schema}"
        pool_options: dict[str, Any] = {
            "kwargs": connection_kwargs,
            "connection_class": connection_class,
            "max_lifetime": _POOL_MAX_LIFETIME_SECONDS,
            "max_idle": _POOL_MAX_IDLE_SECONDS,
            "reconnect_timeout": _POOL_RECONNECT_TIMEOUT_SECONDS,
            "check": psycopg_pool.ConnectionPool.check_connection,
        }
        effective_max_size = max(20, max_size)
        pool = ConnectionPool(dsn, min_size=1, max_size=effective_max_size, open=False, name="fleet", **pool_options)
        pool.open(wait=wait, timeout=60)
        migration_pool = None
        try:
            if migration_dsn and migration_dsn != dsn:
                migration_pool = ConnectionPool(
                    migration_dsn, min_size=1, max_size=1, open=False, name="fleet-migrations", **pool_options
                )
                migration_pool.open(wait=wait, timeout=60)
        except BaseException:
            pool.close()
            raise
        return cls(
            pool,
            dsn,
            connection_class,
            migration_pool=migration_pool,
            schema=schema,
            connection_kwargs=connection_kwargs,
        )

    def close(self) -> None:
        """Stop lease heartbeats, release the DDL pool, and close the runtime pool."""
        try:
            self.heartbeat.stop()
        finally:
            try:
                self.close_migration_pool()
            finally:
                self.pool.close()

    def close_migration_pool(self) -> None:
        """Release the optional DDL role connection pool after startup migrations."""
        migration_pool = self.migration_pool
        if migration_pool is not self.pool:
            self.migration_pool = self.pool
            migration_pool.close()

    @_retry_once
    def initialize(self) -> None:
        """Apply the squashed schema idempotently under a transaction-scoped lock."""
        with self.migration_pool.connection() as connection, connection.transaction():
            if self.schema != "public":
                connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
                connection.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)))
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,17))", ("fleet_lite_schema_migrations",)
            )
            connection.execute(_SCHEMA_VERSION_TABLE)
            row = connection.execute("SELECT MAX(version) FROM fleet_lite_schema_migrations").fetchone()
            current_version = int(row[0]) if row and row[0] is not None else 0
            if current_version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {current_version} is newer than this Fleet version {SCHEMA_VERSION}"
                )
            for version, statements in _SCHEMA_MIGRATIONS:
                if version <= current_version:
                    continue
                if version != current_version + 1:
                    raise RuntimeError(f"Missing Fleet schema migration {current_version + 1}")
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO fleet_lite_schema_migrations(version) VALUES (%s) ON CONFLICT DO NOTHING",
                    (version,),
                )
                current_version = version
        self.cleanup_expired_replays()
