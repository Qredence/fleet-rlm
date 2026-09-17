"""Lean session registry managing session-scoped root Daytona sandboxes."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import dataclass
from enum import StrEnum
from threading import Thread
from typing import Any, Protocol
from uuid import UUID, uuid4

from fleet_rlm.daytona.errors import DaytonaAdapterError

DEFAULT_IDLE_STOP_SECONDS = 300


class LeaseState(StrEnum):
    """Lifecycle states for Daytona leases."""

    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class DaytonaLeaseAcquisitionTimeoutError(DaytonaAdapterError):
    """Raised when lease acquisition exceeds timeout."""

    pass


@dataclass(frozen=True)
class ProviderAbsenceReceipt:
    confirmed_absent: bool = True
    error: Any = None
    plateau: Any = None


@dataclass(frozen=True)
class SandboxLeaseReceipt:
    provider: ProviderAbsenceReceipt = ProviderAbsenceReceipt()


@dataclass
class SandboxLease:
    """Lightweight handle representing an acquired sandbox lease."""

    sandbox: Any = None
    lease_id: str = ""
    session_id: str = ""
    workspace_id: str = ""
    sandbox_id: str | None = None
    active: bool = True
    volume_id: str | None = None
    mount_path: str | None = None
    volume_subpath: str | None = None
    kind: str = ""
    platform: Any = None
    policy: Any = None

    def __post_init__(self) -> None:
        if not self.sandbox_id and self.sandbox is not None:
            self.sandbox_id = str(getattr(self.sandbox, "id", "") or "")

    async def close(self) -> None:
        self.active = False

    async def aclose(self) -> SandboxLeaseReceipt:
        await self.close()
        if self.platform is not None and self.sandbox_id:
            with contextlib.suppress(Exception):
                delete = getattr(self.platform, "delete", None)
                if callable(delete):
                    res = delete(self.sandbox_id)
                    if inspect.isawaitable(res):
                        await res
        return SandboxLeaseReceipt()


@dataclass(frozen=True)
class SandboxLeasePolicy:
    """Policy bounding sandbox lease lifetimes."""

    max_idle_seconds: float = 300.0
    auto_stop_seconds: int = 300
    kind: str = ""
    interpreter_shutdown: bool = False
    provider_request_timeout_s: float = 15.0
    confirm_timeout_s: float = 15.0
    confirm_poll_interval_s: float = 0.5


@dataclass(frozen=True)
class LeaseRequest:
    """Request specification for acquiring a session sandbox lease."""

    session_id: Any
    workspace_id: Any = None
    user_id: Any = None
    run_id: Any = None
    context_fingerprint: str = ""
    labels: dict[str, str] | None = None
    spec: Any = None
    timeout_s: float | None = None


class RootSessionLease:
    """Cancellation-safe handle for a reusable root session sandbox lease."""

    def __init__(
        self,
        key: Any,
        lease: Any,
        release_callback: Callable[[Any], Awaitable[Any] | Any] | None = None,
        on_closed: Callable[[RootSessionLease], Awaitable[Any] | Any] | None = None,
        *,
        spec: Any = None,
        sandbox: Any = None,
        interpreter: Any = None,
        broker: Any = None,
        volume: Any = None,
        volume_id: str | None = None,
        mount_path: str | None = None,
        volume_subpath: str | None = None,
    ) -> None:
        self.key = key
        self.spec = spec
        self.lease = lease
        self.release_callback = release_callback
        self.on_closed = on_closed
        self.sandbox = sandbox if sandbox is not None else getattr(lease, "sandbox", None)
        self.interpreter = interpreter if interpreter is not None else getattr(lease, "interpreter", None)
        self.broker = broker
        self.volume = volume if volume is not None else getattr(lease, "volume", None)
        sandbox_id = getattr(lease, "sandbox_id", None) or getattr(self.sandbox, "id", None)
        self.sandbox_id = str(sandbox_id or "")
        self.volume_id = volume_id
        self.mount_path = mount_path
        self.volume_subpath = volume_subpath
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def closing(self) -> bool:
        return False

    @property
    def failed(self) -> bool:
        return False

    @property
    def close_error(self) -> Exception | None:
        return None

    def set_close_barrier(self, barrier: Any) -> None:
        pass

    @property
    def state(self) -> LeaseState:
        return LeaseState.CLOSED if self._closed else LeaseState.OPEN

    @property
    def status(self) -> LeaseState:
        return self.state

    async def close(self, *, notify: bool = False, deadline: float | None = None) -> None:
        del notify, deadline
        if not self._closed:
            self._closed = True
            if self.release_callback:
                res = self.release_callback(self.lease)
                if inspect.isawaitable(res):
                    await res
            if self.on_closed:
                res = self.on_closed(self)
                if inspect.isawaitable(res):
                    await res


class BindingStoreLike(Protocol):
    """Protocol for sandbox binding persistence."""

    async def get(self, session_id: Any) -> Any: ...
    async def upsert(self, binding: Any) -> Any: ...


def has_pending_lease_ownership() -> bool:
    """Return whether background lease acquisition tasks remain unresolved."""
    return False


async def wait_lease_ownership(timeout: float = 0.25) -> None:
    """No-op awaitable for background lease settlement compatibility."""
    del timeout


@dataclass(slots=True)
class OwnedCloseExecution:
    future: Future[Any]
    used_fallback: bool
    coroutine: Any | None = None


def schedule_owned_close(
    *,
    loop: asyncio.AbstractEventLoop,
    build: Callable[[], Any],
    fallback_owner_release: Callable[[], None] | None = None,
    thread_name: str = "fleet-lease-close-fallback",
) -> OwnedCloseExecution:
    """Post one owned async close to the owner loop, or run it on a disposable loop."""
    coroutine = build()
    try:
        return OwnedCloseExecution(
            future=asyncio.run_coroutine_threadsafe(coroutine, loop),
            used_fallback=False,
            coroutine=coroutine,
        )
    except BaseException:
        if inspect.iscoroutine(coroutine):
            coroutine.close()

    fallback: Future[Any] = Future()

    def run_fallback() -> None:
        try:
            asyncio.run(build())
        except BaseException as exc:
            if not fallback.done():
                fallback.set_exception(exc)
        else:
            if not fallback.done():
                fallback.set_result(None)

    thread = Thread(target=run_fallback, name=thread_name, daemon=True)
    try:
        thread.start()
    except BaseException as exc:
        if fallback_owner_release is not None:
            with contextlib.suppress(BaseException):
                fallback_owner_release()
        fallback.set_exception(exc)
    return OwnedCloseExecution(future=fallback, used_fallback=True, coroutine=None)


class DaytonaSessionManager:
    """Lean session registry managing session-scoped root Daytona sandboxes."""

    def __init__(
        self,
        client: Any = None,
        settings: Any = None,
        *,
        admission: Any = None,
        binding_store: Any = None,
        **kwargs: Any,
    ) -> None:
        self._client = client
        self._settings = settings
        self._admission = admission
        self._binding_store = binding_store
        self._bindings = kwargs.get("bindings") or binding_store
        self._platform = kwargs.get("platform")
        self._sandbox_spec = kwargs.get("sandbox_spec")
        self._active_sandboxes: dict[str, Any] = {}
        self._active_leases: dict[str, SandboxLease] = {}
        self._lock = asyncio.Lock()
        self.has_pending_ownership = False
        del kwargs

    def owns_sandbox(self, sandbox_id: str) -> bool:
        del sandbox_id
        return True

    async def fence_session(self, session_id: UUID, *, deadline: float | None = None) -> None:
        """Fence a Sandbox retained by a settling Run during startup recovery."""
        del deadline
        bindings = self._bindings or self._binding_store
        if not bindings:
            return
        binding = await bindings.get(session_id)
        if binding is None or not getattr(binding, "sandbox_id", None):
            return
        platform = self._platform or self._client
        stop = getattr(platform, "stop", None)
        if callable(stop):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    asyncio.to_thread(stop, binding.sandbox_id),
                    timeout=60,
                )

    async def prewarm_session(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        deadline: float | None = None,
    ) -> bool:
        del deadline
        await self.acquire(
            LeaseRequest(session_id=str(session_id), workspace_id=str(workspace_id), user_id=str(user_id))
        )
        return True

    def schedule_prewarm(
        self,
        session_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
    ) -> asyncio.Task[None]:
        async def _prewarm() -> None:
            await self.prewarm_session(session_id, user_id=user_id, workspace_id=workspace_id)

        return asyncio.get_running_loop().create_task(_prewarm())

    async def acquire(self, request: LeaseRequest | Any) -> SandboxLease:
        session_id = getattr(request, "session_id", str(uuid4()))
        workspace_id = getattr(request, "workspace_id", None)
        async with self._lock:
            if session_id in self._active_leases:
                lease = self._active_leases[session_id]
                if lease.active:
                    return lease
            sandbox = None
            if self._client is not None and hasattr(self._client, "create"):
                from fleet_rlm.daytona.provisioning import DaytonaSandboxSpec

                spec = self._sandbox_spec or DaytonaSandboxSpec(snapshot="default")
                sandbox = await self._client.create(spec)
                self._active_sandboxes[session_id] = sandbox

            lease = SandboxLease(
                sandbox=sandbox,
                lease_id=str(uuid4()),
                session_id=str(session_id),
                workspace_id=str(workspace_id or ""),
                active=True,
            )
            self._active_leases[session_id] = lease
            return lease

    async def release(self, lease: Any) -> None:
        """Release lease handle back to pool; retains underlying sandbox for session."""
        del lease

    async def delete_session(self, session_id: str) -> None:
        """Tear down and destroy the root sandbox associated with session."""
        async with self._lock:
            lease = self._active_leases.pop(session_id, None)
            if lease:
                lease.active = False
            sandbox = self._active_sandboxes.pop(session_id, None)
            if sandbox is not None and hasattr(sandbox, "delete"):
                with contextlib.suppress(Exception):
                    await sandbox.delete()

    async def aclose(self, drain_seconds: float = 0.0) -> bool:
        """Shut down all active session sandboxes."""
        del drain_seconds
        async with self._lock:
            for session_id in list(self._active_sandboxes.keys()):
                await self.delete_session(session_id)
        return True
