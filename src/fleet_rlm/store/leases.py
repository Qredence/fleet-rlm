"""Pooled PostgreSQL table leases, batched heartbeat renewal, and Lakebase OAuth caching."""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Self

import psycopg
from psycopg.rows import dict_row

from fleet_rlm.store.schema import _OPTIMIZATION_LOCK_SCOPE, _optimization_owner_id, _row_value

_LOGGER = logging.getLogger("fleet_rlm.store")
LEASE_TTL_SECONDS = 60
HEARTBEAT_INTERVAL_SECONDS = 20
_OAUTH_REFRESH_MARGIN_SECONDS = 300.0
_OAUTH_DEFAULT_TTL_SECONDS = 3_300.0
_DEFAULT_OWNER_ID = f"{os.getpid()}-{uuid.uuid4().hex[:12]}"


class PooledLease:
    """Lease handle backed by pooled PostgreSQL TTL rows and shared heartbeat renewal."""

    def __init__(
        self,
        store: Any,
        scope: str,
        *,
        kind: str,
        owner_id: str,
        epoch: int,
        created: bool | None = None,
    ) -> None:
        self._store = store
        self.scope = scope
        self.kind = kind
        self.owner_id = owner_id
        self.epoch = epoch
        self.created = created
        self.closed = False
        self.lost = threading.Event()
        self._on_lost = self.lost.set
        self._close_lock = threading.Lock()

    def assert_held(self, *, verify: bool = False) -> None:
        """Fence expired or lost global coordination, optionally verifying the current DB owner."""
        heartbeat = self._store.heartbeat
        with heartbeat._lock:
            entry = heartbeat._leases.get(self.scope)
        if (
            self.closed
            or self.lost.is_set()
            or entry is None
            or entry[0] != self.epoch
            or entry[2] is not self._on_lost
            or time.monotonic() - entry[3] >= LEASE_TTL_SECONDS
        ):
            self.lost.set()
            raise RuntimeError("Optimization lease was lost or expired")
        if verify:
            with self._store.pool.connection() as connection:
                row = connection.execute(
                    "SELECT 1 FROM fleet_lite_leases WHERE scope = %s AND owner_id = %s "
                    "AND epoch = %s AND expires_at > NOW()",
                    (self.scope, self.owner_id, self.epoch),
                ).fetchone()
            if row is None:
                self.lost.set()
                raise RuntimeError("Optimization lease was lost or expired")

    def close(self) -> None:
        """Unregister heartbeat renewal and release the lease row if this epoch still owns it."""
        with self._close_lock:
            if self.closed:
                return
        heartbeat = getattr(self._store, "heartbeat", None)
        if heartbeat is not None:
            if self.kind == "session":
                heartbeat.unregister_session(self.scope, self.epoch)
            else:
                heartbeat.unregister_lease(self.scope, self.epoch, on_lost=self._on_lost)
        self._store._release_lease(self.scope, kind=self.kind, owner_id=self.owner_id, epoch=self.epoch)
        with self._close_lock:
            self.closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class LeaseHeartbeatService:
    """Single daemon thread per Store batching lease renewals and optional idle cleanup."""

    def __init__(self, store: Any, *, interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS) -> None:
        self._store = store
        self.interval_seconds = interval_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Maps session_id -> (epoch, owner_id, on_lost, last_renewed_monotonic)
        self._sessions: dict[str, tuple[int, str, Callable[[], None], float]] = {}
        # Maps scope -> (epoch, owner_id, on_lost, last_renewed_monotonic)
        self._leases: dict[str, tuple[int, str, Callable[[], None] | None, float]] = {}
        self._idle_callbacks: list[Callable[[], None]] = []
        self._idle_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background heartbeat daemon if not already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            thread = threading.Thread(
                target=self._run,
                name="fleet-lease-heartbeat",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background heartbeat daemon cleanly."""
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        idle_thread = self._idle_thread
        if idle_thread is not None and idle_thread is not threading.current_thread():
            idle_thread.join(timeout=timeout)
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def register_idle_callback(self, callback: Callable[[], None]) -> None:
        """Register an idle-eviction hook executed on a separate cleanup thread."""
        with self._lock:
            if callback not in self._idle_callbacks:
                self._idle_callbacks.append(callback)

    def unregister_idle_callback(self, callback: Callable[[], None]) -> None:
        """Remove a previously registered idle-eviction hook."""
        with self._lock:
            self._idle_callbacks = [item for item in self._idle_callbacks if item is not callback]

    def register_session(
        self,
        session_id: str,
        epoch: int,
        *,
        owner_id: str,
        on_lost: Callable[[], None],
    ) -> None:
        """Track a Session lease for batched renewal."""
        with self._lock:
            self._sessions[session_id] = (epoch, owner_id, on_lost, time.monotonic())
        self.start()

    def unregister_session(self, session_id: str, epoch: int | None = None) -> None:
        """Stop tracking a Session lease once closed or fenced."""
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None and (epoch is None or entry[0] == epoch):
                self._sessions.pop(session_id, None)

    def register_lease(
        self,
        scope: str,
        epoch: int,
        *,
        owner_id: str,
        on_lost: Callable[[], None] | None = None,
    ) -> None:
        """Track a `fleet_lite_leases` scope (optimization worker/owner) for batched renewal."""
        with self._lock:
            self._leases[scope] = (epoch, owner_id, on_lost, time.monotonic())
        self.start()

    def unregister_lease(
        self, scope: str, epoch: int | None = None, *, on_lost: Callable[[], None] | None = None
    ) -> None:
        """Stop tracking a `fleet_lite_leases` scope."""
        with self._lock:
            entry = self._leases.get(scope)
            if entry is not None and (epoch is None or entry[0] == epoch) and (on_lost is None or entry[2] is on_lost):
                self._leases.pop(scope, None)

    def tick(self) -> None:
        """Run one renewal cycle for all tracked Session and global leases."""
        now = time.monotonic()
        with self._lock:
            sessions = dict(self._sessions)
            leases = dict(self._leases)
            idle_callbacks = tuple(self._idle_callbacks)
        if sessions:
            self._renew_sessions(sessions, now)
        if leases:
            self._renew_global_leases(leases, now)
        with self._lock:
            if idle_callbacks and self._idle_thread is not None and self._idle_thread.is_alive():
                _LOGGER.warning("Previous idle-cleanup thread is still running; skipping this cleanup cycle")
            elif idle_callbacks:
                self._idle_thread = threading.Thread(
                    target=self._run_idle_callbacks, args=(idle_callbacks,), name="fleet-idle-eviction", daemon=True
                )
                self._idle_thread.start()

    def _run_idle_callbacks(self, callbacks: tuple[Callable[[], None], ...]) -> None:
        for callback in callbacks:
            if self._stop.is_set():
                break
            with suppress(Exception):
                callback()

    def _renew_sessions(
        self,
        sessions: dict[str, tuple[int, str, Callable[[], None], float]],
        now: float,
    ) -> None:
        by_owner: dict[str, list[tuple[str, int, Callable[[], None], float]]] = {}
        for session_id, (epoch, owner_id, on_lost, last_renewed) in sessions.items():
            by_owner.setdefault(owner_id, []).append((session_id, epoch, on_lost, last_renewed))
        for owner_id, items in by_owner.items():
            sids = [item[0] for item in items]
            epochs = [item[1] for item in items]
            try:
                renewed = self._store._batch_renew_session_leases(sids, epochs, owner_id, LEASE_TTL_SECONDS)
            except psycopg.OperationalError:
                _LOGGER.warning("Transient failure renewing %d session lease(s)", len(items))
                self._expire_unrenewed_sessions(items, now)
                continue
            lost: list[Callable[[], None]] = []
            with self._lock:
                for session_id, epoch, on_lost, _last_renewed in items:
                    current = self._sessions.get(session_id)
                    if current is None or current[0] != epoch:
                        continue
                    if session_id in renewed:
                        self._sessions[session_id] = (epoch, owner_id, on_lost, now)
                    else:
                        self._sessions.pop(session_id, None)
                        lost.append(on_lost)
            for on_lost in lost:
                with suppress(Exception):
                    on_lost()

    def _expire_unrenewed_sessions(
        self,
        items: list[tuple[str, int, Callable[[], None], float]],
        now: float,
    ) -> None:
        lost: list[Callable[[], None]] = []
        with self._lock:
            for session_id, epoch, on_lost, last_renewed in items:
                current = self._sessions.get(session_id)
                if current is None or current[0] != epoch:
                    continue
                if now - last_renewed >= LEASE_TTL_SECONDS:
                    self._sessions.pop(session_id, None)
                    lost.append(on_lost)
        for on_lost in lost:
            with suppress(Exception):
                on_lost()

    def _renew_global_leases(
        self,
        leases: dict[str, tuple[int, str, Callable[[], None] | None, float]],
        now: float,
    ) -> None:
        by_owner: dict[str, list[tuple[str, int, Callable[[], None] | None, float]]] = {}
        for scope, (epoch, owner_id, on_lost, last_renewed) in leases.items():
            by_owner.setdefault(owner_id, []).append((scope, epoch, on_lost, last_renewed))
        for owner_id, items in by_owner.items():
            scopes = [item[0] for item in items]
            epochs = [item[1] for item in items]
            try:
                renewed = self._store._batch_renew_global_leases(scopes, epochs, owner_id, LEASE_TTL_SECONDS)
            except psycopg.OperationalError:
                _LOGGER.warning("Transient failure renewing %d global lease(s)", len(items))
                expired_callbacks = []
                with self._lock:
                    for scope, _epoch, _owner_lost, last_renewed in items:
                        current = self._leases.get(scope)
                        # A deleted global row can be reacquired at the same epoch.
                        if current is not None and current == leases[scope] and now - last_renewed >= LEASE_TTL_SECONDS:
                            self._leases.pop(scope, None)
                            if current[2] is not None:
                                expired_callbacks.append(current[2])
                for callback in expired_callbacks:
                    with suppress(Exception):
                        callback()
                continue
            lost: list[Callable[[], None]] = []
            with self._lock:
                for scope, epoch, on_lost, _last_renewed in items:
                    current = self._leases.get(scope)
                    if current is None or current != leases[scope]:
                        continue
                    if scope in renewed:
                        self._leases[scope] = (epoch, owner_id, on_lost, now)
                    else:
                        self._leases.pop(scope, None)
                        if on_lost is not None:
                            lost.append(on_lost)
            for on_lost in lost:
                with suppress(Exception):
                    on_lost()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.tick()


class LeaseStoreMixin:
    """Pooled TTL session and optimization lease primitives for Store."""

    pool: Any
    heartbeat: LeaseHeartbeatService
    worker_id: str

    def acquire_session_lease(self, session_id: str, *, owner_id: str | None = None) -> PooledLease:
        """Atomically acquire a Session TTL lease and advance its fencing epoch."""
        owner = owner_id or self.worker_id
        with self.pool.connection() as connection:
            row = (
                connection.cursor(row_factory=dict_row)
                .execute(
                    """
                    INSERT INTO fleet_lite_sessions (session_id, ownership_epoch, lease_owner, lease_expires_at)
                    VALUES (%s, 1, %s, NOW() + (%s * INTERVAL '1 second'))
                    ON CONFLICT (session_id) DO UPDATE SET
                        ownership_epoch = fleet_lite_sessions.ownership_epoch + 1,
                        lease_owner = EXCLUDED.lease_owner,
                        lease_expires_at = EXCLUDED.lease_expires_at,
                        updated_at = NOW()
                    WHERE fleet_lite_sessions.lease_owner IS NULL
                       OR fleet_lite_sessions.lease_expires_at < NOW()
                       OR fleet_lite_sessions.lease_owner = EXCLUDED.lease_owner
                    RETURNING ownership_epoch, (xmax = 0) AS created
                    """,
                    (session_id, owner, LEASE_TTL_SECONDS),
                )
                .fetchone()
            )
        if not row:
            raise RuntimeError("Session is owned by another worker")
        epoch = int(_row_value(row, "ownership_epoch", 0))
        created = bool(_row_value(row, "created", 1))
        return PooledLease(self, session_id, kind="session", owner_id=owner, epoch=epoch, created=created)

    def acquire_optimization_lease(self, *, owner_id: str | None = None) -> PooledLease:
        """Hold the global optimization execution lease via a pooled TTL row."""
        return self._acquire_global_lease(_OPTIMIZATION_LOCK_SCOPE, owner_id=owner_id or self.worker_id)

    def assert_optimization_lease(self, *, verify: bool = False) -> None:
        """Check the optimizer execution lease when this Store has acquired one."""
        lease = getattr(self, "_optimization_lease", None)
        if lease is not None:
            lease.assert_held(verify=verify)

    def acquire_optimization_owner_lease(self, owner_id: str) -> PooledLease:
        """Keep an API optimization owner alive across pending launch and execution."""
        validated = _optimization_owner_id(owner_id)
        return self._acquire_global_lease(f"fleet:optimization-owner:{validated}", owner_id=validated)

    def optimization_owner_alive(self, owner_id: str) -> bool:
        """Check whether an optimization API owner lease is still active."""
        validated = _optimization_owner_id(owner_id)
        scope = f"fleet:optimization-owner:{validated}"
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM fleet_lite_leases WHERE scope = %s AND expires_at >= NOW() AND owner_id = %s",
                (scope, validated),
            ).fetchone()
        return row is not None

    def _acquire_global_lease(self, scope: str, *, owner_id: str) -> PooledLease:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                INSERT INTO fleet_lite_leases (scope, owner_id, epoch, expires_at)
                VALUES (%s, %s, 1, NOW() + (%s * INTERVAL '1 second'))
                ON CONFLICT (scope) DO UPDATE SET
                    owner_id = EXCLUDED.owner_id,
                    epoch = fleet_lite_leases.epoch + 1,
                    expires_at = EXCLUDED.expires_at
                WHERE fleet_lite_leases.expires_at < NOW()
                   OR fleet_lite_leases.owner_id = EXCLUDED.owner_id
                RETURNING epoch
                """,
                (scope, owner_id, LEASE_TTL_SECONDS),
            ).fetchone()
        if not row:
            raise RuntimeError("An optimization run is already owned by another worker")
        epoch = int(_row_value(row, "epoch", 0))
        lease = PooledLease(self, scope, kind="global", owner_id=owner_id, epoch=epoch)
        self.heartbeat.register_lease(scope, epoch, owner_id=owner_id, on_lost=lease._on_lost)
        if scope == _OPTIMIZATION_LOCK_SCOPE:
            self._optimization_lease = lease
        return lease

    def _release_lease(self, scope: str, *, kind: str, owner_id: str, epoch: int) -> None:
        with self.pool.connection() as connection:
            if kind == "session":
                connection.execute(
                    "UPDATE fleet_lite_sessions SET lease_owner = NULL, lease_expires_at = NULL "
                    "WHERE session_id = %s AND ownership_epoch = %s AND lease_owner = %s",
                    (scope, epoch, owner_id),
                )
            else:
                connection.execute(
                    "DELETE FROM fleet_lite_leases WHERE scope = %s AND owner_id = %s AND epoch = %s",
                    (scope, owner_id, epoch),
                )

    def _batch_renew_session_leases(
        self, session_ids: list[str], epochs: list[int], owner_id: str, ttl_seconds: int
    ) -> set[str]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                """
                UPDATE fleet_lite_sessions AS s
                SET lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                    updated_at = NOW()
                FROM (SELECT unnest(%s::text[]) AS sid, unnest(%s::bigint[]) AS ep) AS req
                WHERE s.session_id = req.sid
                  AND s.ownership_epoch = req.ep
                  AND s.lease_owner = %s
                  AND s.lease_expires_at > NOW()
                RETURNING s.session_id
                """,
                (ttl_seconds, session_ids, epochs, owner_id),
            ).fetchall()
        return {str(_row_value(row, "session_id", 0)) for row in rows}

    def _batch_renew_global_leases(
        self, scopes: list[str], epochs: list[int], owner_id: str, ttl_seconds: int
    ) -> set[str]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                """
                UPDATE fleet_lite_leases AS l
                SET expires_at = NOW() + (%s * INTERVAL '1 second')
                FROM (SELECT unnest(%s::text[]) AS sc, unnest(%s::bigint[]) AS ep) AS req
                WHERE l.scope = req.sc
                  AND l.epoch = req.ep
                  AND l.owner_id = %s
                  AND l.expires_at > NOW()
                RETURNING l.scope
                """,
                (ttl_seconds, scopes, epochs, owner_id),
            ).fetchall()
        return {str(_row_value(row, "scope", 0)) for row in rows}


def _credential_expiry_monotonic(credential: Any, now: float) -> float:
    expiration = getattr(credential, "expiration_time", None)
    if isinstance(expiration, (int, float)):
        delta = float(expiration) - time.time()
        return now + max(delta, 0.0)
    if isinstance(expiration, datetime):
        target = expiration if expiration.tzinfo is not None else expiration.replace(tzinfo=UTC)
        delta = target.timestamp() - time.time()
        return now + max(delta, 0.0)
    return now + _OAUTH_DEFAULT_TTL_SECONDS


def lakebase_connection_class(client: Any, endpoint: str) -> type[psycopg.Connection[Any]]:
    """Create psycopg connections caching the Lakebase OAuth token until near expiry."""
    token_lock = threading.Lock()
    cached_token: str | None = None
    cached_expires_at = 0.0

    def current_token() -> str:
        nonlocal cached_token, cached_expires_at
        now = time.monotonic()
        if cached_token is not None and now + _OAUTH_REFRESH_MARGIN_SECONDS < cached_expires_at:
            return cached_token
        with token_lock:
            now = time.monotonic()
            if cached_token is not None and now + _OAUTH_REFRESH_MARGIN_SECONDS < cached_expires_at:
                return cached_token
            credential = client.postgres.generate_database_credential(endpoint=endpoint)
            cached_token = str(credential.token)
            cached_expires_at = _credential_expiry_monotonic(credential, time.monotonic())
            return cached_token

    class LakebaseOAuthConnection(psycopg.Connection):
        @classmethod
        def connect(
            cls,
            conninfo: str = "",
            *,
            autocommit: bool = False,
            prepare_threshold: int | None = 5,
            context: Any = None,
            row_factory: Any = None,
            cursor_factory: Any = None,
            **kwargs: Any,
        ) -> Self:
            kwargs["password"] = current_token()
            return super().connect(
                conninfo,
                autocommit=autocommit,
                prepare_threshold=prepare_threshold,
                context=context,
                row_factory=row_factory,
                cursor_factory=cursor_factory,
                **kwargs,
            )

    return LakebaseOAuthConnection
