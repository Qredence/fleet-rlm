"""Public Daytona runtime boundary for reusable roots and disposable children.

``DaytonaRuntime`` is deliberately small.  Callers provide a root/child
acquisition seam (or a ``DaytonaRuntimeResources`` instance); admission,
provisioning, binding, broker startup, and cleanup remain behind that seam.
Root handles are retained by ``(workspace_id, session_id)`` and survive
sequential Turns until tainted, replaced, or explicitly closed.  Child handles
are one-shot async context managers and are always disposable.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from fleet_rlm.daytona._lease import LeaseState, RootSessionLease


class DaytonaRuntimeState(StrEnum):
    """Lifecycle of the process-scoped runtime facade."""

    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


def _identity_text(value: UUID | str | None, name: str) -> str:
    if value is None:
        raise ValueError(f"{name} is required")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


@dataclass(frozen=True, slots=True)
class RootSessionSpec:
    """Immutable identity and context selectors for one reusable root."""

    workspace_id: UUID | str
    session_id: UUID | str
    user_id: UUID | str | None = None
    run_id: UUID | str | None = None
    context_fingerprint: object | None = None
    deadline: float | None = None
    force_new: bool = False

    def __post_init__(self) -> None:
        _identity_text(self.workspace_id, "workspace_id")
        _identity_text(self.session_id, "session_id")
        if self.deadline is not None and not isinstance(self.deadline, (int, float)):
            raise TypeError("deadline must be numeric or None")

    @property
    def key(self) -> tuple[str, str]:
        """Return the stable root registry key."""
        return (_identity_text(self.workspace_id, "workspace_id"), _identity_text(self.session_id, "session_id"))

    @property
    def fingerprint(self) -> object | None:
        """Alias for the context selector used for root reuse."""
        return self.context_fingerprint


@dataclass(slots=True)
class _UnpublishedResourceOwner:
    """Retain compatibility-resource cleanup until provider fencing succeeds."""

    lease: Any
    request: Any
    manager: Any
    release_callback: Callable[[], Any] | None = None
    released: bool = False
    quarantined: bool = False
    callback_started: bool = False
    callback_settled: bool = False
    cleanup_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    cleanup_task: asyncio.Task[Any] | None = field(default=None, repr=False)
    deadline: float | None = None


@dataclass(frozen=True, slots=True)
class ChildEnvironmentSpec:
    """Immutable selectors and bounds for one disposable child."""

    workspace_id: UUID | str | None = None
    session_id: UUID | str | None = None
    run_id: UUID | str | None = None
    call_index: int = 0
    volume_id: str | None = None
    mount_path: str | None = None
    volume_subpath: str | None = None
    deadline: float | None = None
    execution_timeout_s: int | None = None
    execution_output_cap: int | None = None
    is_authorized: Callable[[], bool] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.call_index, int) or isinstance(self.call_index, bool) or self.call_index < 0:
            raise ValueError("call_index must be a non-negative integer")
        if self.deadline is not None and not isinstance(self.deadline, (int, float)):
            raise TypeError("deadline must be numeric or None")

    @property
    def key(self) -> tuple[str, str] | None:
        if self.workspace_id is None or self.session_id is None:
            return None
        return (_identity_text(self.workspace_id, "workspace_id"), _identity_text(self.session_id, "session_id"))


def _optional_deadline_kwargs(function: Callable[..., Any], deadline: float | None) -> dict[str, Any]:
    """Pass a deadline only to lifecycle seams that advertise the argument."""
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        return {"deadline": deadline}
    if "deadline" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    ):
        return {"deadline": deadline}
    return {}


def _call_factory(factory: Callable[..., Any], spec: Any, *, extras: dict[str, Any] | None = None) -> Any:
    """Invoke a spec factory without masking errors raised by the factory itself."""
    extras = dict(extras or {})
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return factory(spec, **extras)

    parameters = signature.parameters
    positional = tuple(
        parameter
        for parameter in parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    if positional:
        if accepts_kwargs:
            return factory(spec, **extras)
        accepted = {name: value for name, value in extras.items() if name in parameters}
        return factory(spec, **accepted)

    # Keyword-only factories are common for provider adapters. A factory with
    # no declared ``spec`` and no ``**kwargs`` is treated as a zero-argument
    # compatibility seam rather than receiving an unexpected keyword.
    call_kwargs: dict[str, Any] = {}
    if "spec" in parameters or accepts_kwargs:
        call_kwargs["spec"] = spec
    if accepts_kwargs:
        call_kwargs.update(extras)
    else:
        call_kwargs.update({name: value for name, value in extras.items() if name in parameters})
    return factory(**call_kwargs)


async def _close_child_lease(lease: Any) -> Any:
    """Close a sync child owner off-loop or await an async child owner."""
    close = getattr(lease, "close", None)
    if not callable(close):
        raise TypeError("child lease does not expose close()")
    if inspect.iscoroutinefunction(close):
        return await close()
    result = await asyncio.to_thread(close)
    if inspect.isawaitable(result):
        return await result
    return result


class ChildEnvironment:
    """Async context-managed view over one strictly disposable child lease."""

    def __init__(self, spec: ChildEnvironmentSpec, lease: Any, *, sandbox: Any | None = None) -> None:
        self.spec = spec
        self.lease = lease
        self.sandbox = sandbox if sandbox is not None else getattr(lease, "sandbox", None)
        self.interpreter = getattr(lease, "interpreter", None)
        sandbox_id = getattr(lease, "sandbox_id", None) or getattr(self.sandbox, "id", None)
        self.sandbox_id = str(sandbox_id or "")
        self.volume_id = _optional_text(getattr(lease, "volume_id", None)) or spec.volume_id
        self.volume_subpath = _optional_text(getattr(lease, "volume_subpath", None)) or spec.volume_subpath
        self.mount_path = _optional_text(getattr(lease, "mount_path", None)) or spec.mount_path
        self._daytona_runtime_owner: DaytonaRuntime | None = None
        self._owner = RootSessionLease(
            spec.key or ("child", str(spec.call_index)),
            lease,
            _close_child_lease,
            sandbox=self.sandbox,
            interpreter=self.interpreter,
            volume=self.volume_id,
            volume_id=self.volume_id,
            mount_path=self.mount_path,
            volume_subpath=self.volume_subpath,
        )

    @property
    def state(self) -> LeaseState:
        """Return the explicit child lifecycle state."""
        return self._owner.state

    @property
    def status(self) -> LeaseState:
        """Alias for ``state`` for callers that model lifecycle as a status."""
        return self.state

    @property
    def closed(self) -> bool:
        return self._owner.closed

    @property
    def closing(self) -> bool:
        return self._owner.closing

    @property
    def failed(self) -> bool:
        return self._owner.failed

    @property
    def close_error(self) -> BaseException | None:
        return self._owner.close_error

    async def close(self, *, deadline: float | None = None) -> None:
        """Close the child once; concurrent callers join the same cleanup."""
        await self._owner.close(deadline=deadline)

    async def __aenter__(self) -> ChildEnvironment:
        if self.state is not LeaseState.OPEN:
            raise RuntimeError("child environment is no longer open")
        return self

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        await self.close(deadline=self.spec.deadline)


class _ChildContext:
    """One-shot context object usable both directly and after ``await``."""

    def __init__(self, runtime: DaytonaRuntime, spec: ChildEnvironmentSpec) -> None:
        self._runtime = runtime
        self._spec = spec
        self._entered = False
        self._environment: ChildEnvironment | None = None

    def __await__(self):
        async def identity() -> _ChildContext:
            return self

        return identity().__await__()

    async def __aenter__(self) -> ChildEnvironment:
        if self._entered:
            raise RuntimeError("child context cannot be entered twice")
        self._entered = True
        self._environment = await self._runtime._acquire_child(self._spec)
        try:
            return await self._environment.__aenter__()
        except BaseException:
            await self._environment.close()
            raise

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._environment is not None:
            await self._environment.__aexit__(exc_type, exc, tb)


class DaytonaRuntime:
    """Public provider boundary for reusable root Sessions and child Environments."""

    def __init__(
        self,
        resources: Any | None = None,
        *,
        root_acquirer: Callable[..., Any] | None = None,
        root_factory: Callable[..., Any] | None = None,
        root_releaser: Callable[..., Any] | None = None,
        child_acquirer: Callable[..., Any] | None = None,
        child_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._resources = resources
        self._root_acquirer = root_acquirer or root_factory
        self._root_releaser = root_releaser
        self._child_acquirer = child_acquirer or child_factory
        self._roots: dict[tuple[str, str], RootSessionLease] = {}
        self._tainted: set[tuple[str, str]] = set()
        self._root_lock = asyncio.Lock()
        # Serialize root replacement/shutdown without holding ``_root_lock``
        # across provider cleanup.  A close callback removes its owner from
        # the registry and therefore must be able to acquire ``_root_lock``.
        self._root_transition_lock = asyncio.Lock()
        # Provider requests are shielded from caller cancellation.  If a
        # bounded acquisition completes after its caller has gone away, these
        # owners keep the raw request and the resulting lease alive until the
        # late lease is closed.
        self._root_acquisition_tasks: set[asyncio.Task[Any]] = set()
        # Keep the public acquisition coroutine itself tracked separately from
        # its shielded provider request.  Shutdown must wait for the small
        # handoff window between a provider result and registry publication.
        self._root_owner_acquisition_tasks: set[asyncio.Task[Any]] = set()
        self._late_root_acquisitions: dict[asyncio.Task[Any], RootSessionSpec] = {}
        self._late_root_cleanup_tasks: set[asyncio.Task[Any]] = set()
        self._late_raw_root_owners: dict[int, Any] = {}
        self._late_raw_root_tasks: dict[int, asyncio.Task[Any]] = {}
        self._late_root_leases: set[RootSessionLease] = set()
        # Compatibility resources without the combined release/fence boundary
        # remain owned here until both phases have completed.
        self._unpublished_resource_owners: dict[int, _UnpublishedResourceOwner] = {}
        self._unpublished_resource_tasks: set[asyncio.Task[Any]] = set()
        # Combined-manager retirement tasks are retained independently of the
        # manager's private map. This covers custom resource seams that do not
        # retain an unpublished lease after a failed ordered cleanup.
        self._unpublished_retirement_owners: dict[int, tuple[Any, Callable[[], Any]]] = {}
        self._unpublished_retirement_tasks: dict[int, asyncio.Task[Any]] = {}
        self._child_lock = asyncio.Lock()
        self._children: set[ChildEnvironment] = set()
        self._child_acquisition_tasks: set[asyncio.Task[Any]] = set()
        self._child_owner_acquisition_tasks: set[asyncio.Task[Any]] = set()
        self._late_child_acquisitions: dict[asyncio.Task[Any], ChildEnvironmentSpec] = {}
        self._late_child_cleanup_tasks: set[asyncio.Task[Any]] = set()
        self._late_raw_child_owners: dict[int, Any] = {}
        self._late_raw_child_tasks: dict[int, asyncio.Task[Any]] = {}
        self._late_child_environments: set[ChildEnvironment] = set()
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[bool] | None = None
        self._state = DaytonaRuntimeState.OPEN
        if self._resources is not None:
            if self._root_acquirer is None:
                self._root_acquirer = self._acquire_from_resources
            if self._root_releaser is None:
                self._root_releaser = self._release_from_resources
            if self._child_acquirer is None:
                self._child_acquirer = self._acquire_child_from_resources

    @property
    def state(self) -> DaytonaRuntimeState:
        """Return the runtime facade lifecycle state."""
        return self._state

    @property
    def roots(self) -> tuple[RootSessionLease, ...]:
        """Return a stable, non-provider view of retained roots."""
        return tuple(self._roots.values())

    @property
    def children(self) -> tuple[ChildEnvironment, ...]:
        """Return a stable view of currently owned disposable children."""
        return tuple(self._children)

    async def acquire_root_session(self, spec: RootSessionSpec) -> RootSessionLease:
        """Acquire or reuse the root for ``(workspace_id, session_id)``.

        A changed fingerprint, explicit ``force_new``, taint marker, failed
        handle, or closed handle retires the previous generation before a new
        provider acquisition begins.  The old handle is removed only after a
        successful close, so admission and provider ownership remain retryable
        on cleanup failure.
        """
        if not isinstance(spec, RootSessionSpec):
            raise TypeError("spec must be RootSessionSpec")
        task = asyncio.current_task()
        if task is not None:
            self._root_owner_acquisition_tasks.add(task)
            task.add_done_callback(_consume_task_exception)
        try:
            if self._state is not DaytonaRuntimeState.OPEN:
                raise RuntimeError("Daytona runtime is not accepting root Sessions")
            key = spec.key
            # Do not hold ``_root_lock`` while awaiting a lease close: its
            # successful-close callback removes the owner from this registry.
            await _acquire_lock(self._root_transition_lock, spec.deadline, "root Session transition timed out")
            try:
                async with self._root_lock:
                    if self._state is not DaytonaRuntimeState.OPEN:
                        raise RuntimeError("Daytona runtime is not accepting root Sessions")
                    current = self._roots.get(key)
                    must_replace = current is not None and (
                        current.state is not LeaseState.OPEN
                        or key in self._tainted
                        or spec.force_new
                        or _lease_fingerprint(current) != spec.context_fingerprint
                    )
                    if current is None and key in self._tainted:
                        must_replace = True
                    if current is not None and not must_replace:
                        return current
                if current is not None:
                    await current.close(notify=False, deadline=spec.deadline)
                    async with self._root_lock:
                        if self._roots.get(key) is current:
                            self._roots.pop(key, None)
                raw = await self._acquire_root_from_provider(spec, force_new=must_replace or spec.force_new)
                owner = self._coerce_root(spec, raw)
                published = False
                try:
                    async with self._root_lock:
                        self._roots[key] = owner
                        self._tainted.discard(key)
                        published = True
                    # Shutdown may have started while the provider request was
                    # in flight. Publish the owner before closing it so a
                    # failed close remains visible and retryable rather than
                    # becoming an orphan.
                    if self._state is not DaytonaRuntimeState.OPEN:
                        await owner.close(notify=False, deadline=spec.deadline)
                        raise RuntimeError("Daytona runtime is closing")
                    return owner
                except BaseException:
                    if not published:
                        # Cancellation can arrive in the handoff between the
                        # provider result and registry publication. The raw
                        # provider lease is now ours even though no caller can
                        # receive it; retain a close task rather than dropping
                        # that ownership.
                        self._retain_late_root_lease(owner)
                    raise
            finally:
                self._root_transition_lock.release()
        finally:
            if task is not None:
                self._root_owner_acquisition_tasks.discard(task)

    def open_child(self, spec: ChildEnvironmentSpec) -> _ChildContext:
        """Return a disposable child context.

        The returned object is both an async context manager and awaitable, so
        callers may use either ``async with runtime.open_child(spec)`` or
        ``async with await runtime.open_child(spec)`` while integrations migrate
        from the older async-factory spelling.
        """
        if not isinstance(spec, ChildEnvironmentSpec):
            raise TypeError("spec must be ChildEnvironmentSpec")
        return _ChildContext(self, spec)

    async def close_root_session(
        self,
        workspace_id: UUID | str,
        session_id: UUID | str,
        *,
        deadline: float | None = None,
    ) -> None:
        """Close one retained root and preserve it in the map on failure."""
        key = (_identity_text(workspace_id, "workspace_id"), _identity_text(session_id, "session_id"))
        await _acquire_lock(self._root_transition_lock, deadline, "root Session transition timed out")
        try:
            async with self._root_lock:
                owner = self._roots.get(key)
            if owner is not None:
                await owner.close(deadline=deadline)
        finally:
            self._root_transition_lock.release()

    def mark_root_tainted(self, workspace_id: UUID | str, session_id: UUID | str) -> None:
        """Fence a root so the next acquisition rotates its generation."""
        key = (_identity_text(workspace_id, "workspace_id"), _identity_text(session_id, "session_id"))
        self._tainted.add(key)

    async def aclose(self, *, deadline: float | None = None) -> bool:
        """Close all retained roots; return false when any cleanup fails.

        Shutdown is single-flight and shielded. A cancelled caller leaves the
        same close operation owned by the runtime so a later owner can join it
        instead of racing a second provider release.
        """
        async with self._close_lock:
            if self._state is DaytonaRuntimeState.CLOSED:
                return True
            task = self._close_task
            if task is None or task.done():
                self._state = DaytonaRuntimeState.CLOSING
                task = asyncio.create_task(self._close_roots(deadline), name="fleet-daytona-runtime-close")
                self._close_task = task
        return await asyncio.shield(task)

    async def _close_roots(self, deadline: float | None) -> bool:
        first_error: BaseException | None = None

        current_task = asyncio.current_task()

        async def wait_owned(tasks: set[asyncio.Task[Any]], message: str) -> None:
            nonlocal first_error
            pending_tasks = tuple(task for task in tasks if task is not current_task and not task.done())
            if not pending_tasks:
                return
            timeout = None
            if deadline is not None:
                timeout = max(0.0, deadline - asyncio.get_running_loop().time())
            _, pending = await asyncio.wait(pending_tasks, timeout=timeout)
            if pending and first_error is None:
                first_error = TimeoutError(message)

        # A root provider request is shielded from the caller. Drain it before
        # taking the transition lock so a late root can be published and
        # closed by the same shutdown operation rather than being orphaned.
        root_tasks = (
            set(self._root_acquisition_tasks)
            | set(self._root_owner_acquisition_tasks)
            | set(self._late_root_acquisitions)
        )
        await wait_owned(root_tasks, "Daytona root acquisition drain timed out")
        child_tasks = (
            set(self._child_acquisition_tasks)
            | set(self._child_owner_acquisition_tasks)
            | set(self._late_child_acquisitions)
        )
        await wait_owned(child_tasks, "Daytona child acquisition drain timed out")
        # Done callbacks that turn late acquisitions into close tasks run on
        # the next event-loop turn after ``wait`` observes task completion.
        await asyncio.sleep(0)
        await wait_owned(self._late_root_cleanup_tasks, "Daytona late root cleanup timed out")
        for raw_id, raw in tuple(self._late_raw_root_owners.items()):
            existing_raw_task = self._late_raw_root_tasks.get(raw_id)
            if existing_raw_task is not None and not existing_raw_task.done():
                continue
            try:
                cleanup = asyncio.create_task(self._close_raw_root(raw), name="fleet-daytona-raw-root-retry")
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
                continue
            self._late_root_cleanup_tasks.add(cleanup)
            self._late_raw_root_tasks[raw_id] = cleanup
            cleanup.add_done_callback(self._settled_late_root_cleanup_for(raw))
        await wait_owned(self._late_root_cleanup_tasks, "Daytona raw root cleanup timed out")
        if self._late_raw_root_owners and first_error is None:
            first_error = RuntimeError("Daytona raw root cleanup is unresolved")
        await wait_owned(self._unpublished_resource_tasks, "Daytona unpublished resource cleanup timed out")
        # Compatibility resource managers may not expose the combined
        # release/fence boundary. Retry their unpublished owners before root
        # shutdown and keep the runtime failed while any fence is unresolved.
        unpublished_tasks: set[asyncio.Task[Any]] = set()
        for owner in tuple(self._unpublished_resource_owners.values()):
            try:
                unpublished_tasks.add(self._schedule_unpublished_resource_cleanup(owner, deadline=deadline))
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        await wait_owned(unpublished_tasks, "Daytona unpublished resource cleanup timed out")
        retry_unpublished: set[asyncio.Task[Any]] = set()
        for key, (_lease, retry_factory) in tuple(self._unpublished_retirement_owners.items()):
            existing_retry = self._unpublished_retirement_tasks.get(key)
            if existing_retry is not None and not existing_retry.done():
                retry_unpublished.add(existing_retry)
                continue
            try:
                retry = asyncio.create_task(retry_factory(), name="fleet-daytona-unpublished-root-retry")
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
                continue
            retry_unpublished.add(retry)
            self._unpublished_resource_tasks.add(retry)
            self._unpublished_retirement_tasks[key] = retry

            def settled(done: asyncio.Task[Any], owner_key: int = key) -> None:
                self._unpublished_resource_tasks.discard(done)
                if self._unpublished_retirement_tasks.get(owner_key) is done:
                    self._unpublished_retirement_tasks.pop(owner_key, None)
                if not done.cancelled() and done.exception() is None:
                    self._unpublished_retirement_owners.pop(owner_key, None)
                if not done.cancelled():
                    with contextlib.suppress(BaseException):
                        done.exception()

            retry.add_done_callback(settled)
        await wait_owned(retry_unpublished, "Daytona unpublished resource retry timed out")
        if (self._unpublished_resource_owners or self._unpublished_retirement_owners) and first_error is None:
            first_error = RuntimeError("Daytona unpublished resource cleanup is unresolved")
        await wait_owned(self._late_child_cleanup_tasks, "Daytona late child cleanup timed out")
        for raw_id, raw in tuple(self._late_raw_child_owners.items()):
            existing_raw_task = self._late_raw_child_tasks.get(raw_id)
            if existing_raw_task is not None and not existing_raw_task.done():
                continue
            try:
                cleanup = asyncio.create_task(self._close_raw_child(raw), name="fleet-daytona-raw-child-retry")
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
                continue
            self._late_child_cleanup_tasks.add(cleanup)
            self._late_raw_child_tasks[raw_id] = cleanup
            cleanup.add_done_callback(self._settled_late_child_cleanup_for(raw))
        await wait_owned(self._late_child_cleanup_tasks, "Daytona raw child cleanup timed out")
        if self._late_raw_child_owners and first_error is None:
            first_error = RuntimeError("Daytona raw child cleanup is unresolved")
        # A late close can fail after the provider request itself settled.
        # Retry those visible failed owners during the next runtime shutdown
        # instead of treating the first failed attempt as terminal.
        for owner in tuple(self._late_root_leases):
            if owner.closed:
                self._late_root_leases.discard(owner)
                continue
            try:
                await owner.close(notify=False, deadline=deadline)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
            if owner.closed:
                self._late_root_leases.discard(owner)
        if self._late_root_leases and first_error is None:
            first_error = RuntimeError("Daytona late root cleanup is unresolved")
        for environment in tuple(self._late_child_environments):
            if environment.closed:
                self._late_child_environments.discard(environment)
                continue
            try:
                await environment.close(deadline=deadline)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
            if environment.closed:
                self._late_child_environments.discard(environment)
        if self._late_child_environments and first_error is None:
            first_error = RuntimeError("Daytona late child cleanup is unresolved")

        # Serialize root shutdown with replacement/acquisition, but do not hold
        # the registry lock while owners invoke their close callbacks.
        try:
            await _acquire_lock(self._root_transition_lock, deadline, "Daytona root shutdown transition timed out")
        except TimeoutError as exc:
            if first_error is None:
                first_error = exc
        else:
            try:
                # Children are disposable and independent of reusable roots.
                # Close them first so no child can keep provider work alive
                # after root teardown.
                async with self._child_lock:
                    children = tuple(self._children)
                for child in children:
                    try:
                        await child.close(deadline=deadline)
                    except BaseException as exc:
                        if first_error is None:
                            first_error = exc

                async with self._root_lock:
                    owners = tuple(self._roots.values())
                for owner in owners:
                    try:
                        await owner.close(deadline=deadline)
                    except BaseException as exc:
                        if first_error is None:
                            first_error = exc
            finally:
                self._root_transition_lock.release()

        self._state = DaytonaRuntimeState.FAILED if first_error is not None else DaytonaRuntimeState.CLOSED
        return first_error is None

    async def _acquire_root_from_provider(self, spec: RootSessionSpec, *, force_new: bool) -> Any:
        """Bound one provider request while retaining late ownership."""
        if spec.deadline is not None and spec.deadline <= asyncio.get_running_loop().time():
            raise TimeoutError("root Session acquisition timed out")
        acquisition = asyncio.create_task(
            self._call_root_acquirer(spec, force_new=force_new),
            name="fleet-daytona-root-acquisition",
        )
        self._root_acquisition_tasks.add(acquisition)
        try:
            if spec.deadline is None:
                return await asyncio.shield(acquisition)
            remaining = spec.deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError
            return await asyncio.wait_for(asyncio.shield(acquisition), timeout=remaining)
        except TimeoutError:
            self._retain_late_root_acquisition(acquisition, spec)
            raise TimeoutError("root Session acquisition timed out") from None
        except asyncio.CancelledError:
            self._retain_late_root_acquisition(acquisition, spec)
            raise
        finally:
            if acquisition.done():
                self._root_acquisition_tasks.discard(acquisition)

    def _retain_late_root_lease(self, owner: RootSessionLease) -> None:
        """Retain and, when possible, schedule cleanup for a root lease."""
        if owner.closed:
            return
        self._late_root_leases.add(owner)
        # A currently running close already owns the provider operation. Keep
        # the owner strongly reachable and let the next drain join it.
        if owner.closing:
            return
        try:
            cleanup = asyncio.create_task(
                self._close_late_root(owner),
                name="fleet-daytona-late-root-cleanup",
            )
        except BaseException:
            return
        self._late_root_cleanup_tasks.add(cleanup)

        def cleanup_settled(done: asyncio.Task[Any]) -> None:
            self._late_root_cleanup_tasks.discard(done)
            if owner.closed:
                self._late_root_leases.discard(owner)
            if not done.cancelled():
                with contextlib.suppress(BaseException):
                    done.exception()

        cleanup.add_done_callback(cleanup_settled)

    async def _close_raw_root(self, raw: Any) -> None:
        """Best-effort cleanup for an uncoercible late provider result."""
        candidate = raw[0] if isinstance(raw, tuple) and len(raw) == 2 else raw
        close = getattr(candidate, "release", None)
        if not callable(close):
            close = getattr(candidate, "close", None)
        if not callable(close):
            raise TypeError("late root result is not releasable")
        result = close()
        if inspect.isawaitable(result):
            await result

    def _retain_late_root_acquisition(self, acquisition: asyncio.Task[Any], spec: RootSessionSpec) -> None:
        """Close a provider lease that settles after its caller is gone."""
        self._late_root_acquisitions[acquisition] = spec

        def settled(completed: asyncio.Task[Any]) -> None:
            self._root_acquisition_tasks.discard(completed)
            late_spec = self._late_root_acquisitions.pop(completed, None)
            if late_spec is None or completed.cancelled():
                return
            try:
                raw = completed.result()
                owner = self._coerce_root(late_spec, raw)
            except BaseException:
                if "raw" in locals():
                    try:
                        cleanup = asyncio.create_task(
                            self._close_raw_root(raw),
                            name="fleet-daytona-raw-root-cleanup",
                        )
                    except BaseException:
                        self._late_raw_root_owners[id(raw)] = raw
                        return
                    self._late_raw_root_owners[id(raw)] = raw
                    self._late_raw_root_tasks[id(raw)] = cleanup
                    self._late_root_cleanup_tasks.add(cleanup)
                    cleanup.add_done_callback(self._settled_late_root_cleanup_for(raw))
                return
            self._retain_late_root_lease(owner)

        acquisition.add_done_callback(settled)

    def _settled_late_root_cleanup_for(self, raw: Any) -> Callable[[asyncio.Task[Any]], None]:
        def settled(completed: asyncio.Task[Any]) -> None:
            self._late_root_cleanup_tasks.discard(completed)
            if self._late_raw_root_tasks.get(id(raw)) is completed:
                self._late_raw_root_tasks.pop(id(raw), None)
            if not completed.cancelled() and completed.exception() is None:
                self._late_raw_root_owners.pop(id(raw), None)
            if not completed.cancelled():
                with contextlib.suppress(BaseException):
                    completed.exception()

        return settled

    def _settled_late_root_cleanup(self, completed: asyncio.Task[Any]) -> None:
        self._late_root_cleanup_tasks.discard(completed)
        if not completed.cancelled():
            with contextlib.suppress(BaseException):
                completed.exception()

    async def _close_late_root(self, owner: RootSessionLease) -> None:
        """Close one root acquired after its original caller timed out."""
        try:
            await owner.close(notify=False)
        except BaseException:
            # Keep the failed owner strongly reachable so a later runtime close
            # can retry it instead of losing provider/admission ownership.
            return

    async def close(self, *, deadline: float | None = None) -> bool:
        """Compatibility alias for :meth:`aclose`."""
        return await self.aclose(deadline=deadline)

    async def _call_root_acquirer(self, spec: RootSessionSpec, *, force_new: bool) -> Any:
        if self._root_acquirer is None:
            raise RuntimeError("Daytona root acquisition is unavailable")
        kwargs: dict[str, Any] = {"force_new": force_new}
        if spec.deadline is not None:
            kwargs["deadline"] = spec.deadline
        return await _maybe_await(_call_factory(self._root_acquirer, spec, extras=kwargs))

    def _coerce_root(self, spec: RootSessionSpec, raw: Any) -> RootSessionLease:
        sandbox: Any | None = None
        candidate = raw
        if isinstance(raw, tuple) and len(raw) == 2:
            candidate, sandbox = raw
        if isinstance(candidate, RootSessionLease):
            owner = candidate
            owner.spec = spec
            owner.key = spec.key
            if sandbox is not None:
                owner.sandbox = sandbox
            if owner.on_closed is None:
                owner.on_closed = self._remove_closed
            else:
                previous = owner.on_closed

                async def chained(closed: RootSessionLease) -> None:
                    await _maybe_await(previous(closed))
                    await self._remove_closed(closed)

                owner.on_closed = chained
            return owner
        releaser = self._root_releaser
        if releaser is None:
            release_method = getattr(candidate, "release", None)
            if not callable(release_method):
                release_method = getattr(candidate, "close", None)
            if not callable(release_method):
                raise TypeError("root acquisition did not return a releasable lease")

            async def releaser(_lease: Any) -> Any:
                return await _maybe_await(release_method())

        return RootSessionLease(
            spec.key,
            candidate,
            releaser,
            self._remove_closed,
            spec=spec,
            sandbox=sandbox,
            interpreter=getattr(candidate, "interpreter", None),
            broker=getattr(candidate, "broker", None),
            volume=getattr(candidate, "volume", None),
            volume_id=getattr(candidate, "volume_id", None),
            mount_path=getattr(candidate, "mount_path", None),
            volume_subpath=getattr(candidate, "volume_subpath", None),
        )

    async def _remove_closed(self, owner: RootSessionLease) -> None:
        async with self._root_lock:
            if self._roots.get(owner.key) is owner:
                self._roots.pop(owner.key, None)

    async def _acquire_child(self, spec: ChildEnvironmentSpec) -> ChildEnvironment:
        # Track the complete acquisition, not only the provider request.  A
        # shutdown that begins after the request settles still has to wait for
        # registration (or cleanup of an unpublished child) to finish.
        owner_task = asyncio.current_task()
        if owner_task is not None:
            self._child_owner_acquisition_tasks.add(owner_task)
            owner_task.add_done_callback(_consume_task_exception)
        try:
            if self._state is not DaytonaRuntimeState.OPEN:
                raise RuntimeError("Daytona runtime is not accepting child Environments")
            if self._child_acquirer is None:
                raise RuntimeError("Daytona child acquisition is unavailable")
            if spec.deadline is not None and spec.deadline <= asyncio.get_running_loop().time():
                raise TimeoutError("child Environment acquisition timed out")
            acquisition = asyncio.create_task(
                self._call_child_acquirer(spec),
                name="fleet-daytona-child-acquisition",
            )
            self._child_acquisition_tasks.add(acquisition)
            try:
                if spec.deadline is None:
                    raw = await asyncio.shield(acquisition)
                else:
                    remaining = spec.deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise TimeoutError
                    raw = await asyncio.wait_for(asyncio.shield(acquisition), timeout=remaining)
            except TimeoutError:
                self._retain_late_child_acquisition(acquisition, spec)
                raise TimeoutError("child Environment acquisition timed out") from None
            except asyncio.CancelledError:
                self._retain_late_child_acquisition(acquisition, spec)
                raise
            finally:
                if acquisition.done():
                    self._child_acquisition_tasks.discard(acquisition)

            environment = self._coerce_child(spec, raw)
            registered = False
            try:
                async with self._child_lock:
                    if self._state is DaytonaRuntimeState.OPEN:
                        self._children.add(environment)
                        self._bind_child_owner(environment)
                        registered = True
            except BaseException:
                # Cancellation can arrive while waiting for the registry lock,
                # after the provider has already transferred ownership to us.
                # Do not let that raw child become an untracked Sandbox.
                await self._close_unpublished_child(environment, spec)
                raise
            if not registered:
                # Shutdown began while the provider request was in flight. The
                # acquisition was admitted before the transition, so finish
                # its ownership handoff and close the child deterministically;
                # the caller receives a stable transition error rather than a
                # provider-admission error.
                await self._close_unpublished_child(environment, spec)
                raise RuntimeError("Daytona runtime is closing")
            return environment
        finally:
            if owner_task is not None:
                self._child_owner_acquisition_tasks.discard(owner_task)

    async def _call_child_acquirer(self, spec: ChildEnvironmentSpec) -> Any:
        return await _maybe_await(_call_factory(self._child_acquirer, spec)) if self._child_acquirer else None

    def _coerce_child(self, spec: ChildEnvironmentSpec, raw: Any) -> ChildEnvironment:
        if isinstance(raw, ChildEnvironment):
            return raw
        sandbox = None
        lease = raw
        if isinstance(raw, tuple) and len(raw) == 2:
            lease, sandbox = raw
        return ChildEnvironment(spec, lease, sandbox=sandbox)

    async def _close_unpublished_child(self, environment: ChildEnvironment, spec: ChildEnvironmentSpec) -> None:
        """Close a child that could not be published into ``_children``."""
        try:
            await environment.close(deadline=spec.deadline)
        except BaseException:
            # The close operation is shielded by ``RootSessionLease`` and may
            # still be running after a timeout/cancellation. Keep the handle
            # visible so runtime shutdown can join or retry it.
            self._retain_late_child_environment(environment)
            raise

    def _retain_late_child_environment(self, environment: ChildEnvironment) -> None:
        """Retain and, when possible, schedule cleanup for an acquired child."""
        if environment.closed:
            return
        self._late_child_environments.add(environment)
        # A currently running close already owns the provider operation. The
        # runtime's next drain can join it; scheduling a second close task only
        # adds noise and makes ownership harder to reason about.
        if environment.closing:
            return
        try:
            cleanup = asyncio.create_task(
                self._close_late_child(environment),
                name="fleet-daytona-late-child-cleanup",
            )
        except BaseException:
            return
        self._late_child_cleanup_tasks.add(cleanup)

        def cleanup_settled(done: asyncio.Task[Any]) -> None:
            self._late_child_cleanup_tasks.discard(done)
            if environment.closed:
                self._late_child_environments.discard(environment)
            if not done.cancelled():
                with contextlib.suppress(BaseException):
                    done.exception()

        cleanup.add_done_callback(cleanup_settled)

    async def _close_raw_child(self, raw: Any) -> None:
        """Best-effort cleanup for an uncoercible late child result."""
        candidate = raw[0] if isinstance(raw, tuple) and len(raw) == 2 else raw
        await _close_child_lease(candidate)

    def _retain_late_child_acquisition(self, acquisition: asyncio.Task[Any], spec: ChildEnvironmentSpec) -> None:
        """Close a child lease that settles after its caller is gone."""
        self._late_child_acquisitions[acquisition] = spec

        def settled(completed: asyncio.Task[Any]) -> None:
            self._child_acquisition_tasks.discard(completed)
            late_spec = self._late_child_acquisitions.pop(completed, None)
            if late_spec is None or completed.cancelled():
                return
            try:
                raw = completed.result()
                environment = self._coerce_child(late_spec, raw)
            except BaseException:
                if "raw" in locals():
                    try:
                        cleanup = asyncio.create_task(
                            self._close_raw_child(raw), name="fleet-daytona-raw-child-cleanup"
                        )
                    except BaseException:
                        self._late_raw_child_owners[id(raw)] = raw
                        return
                    self._late_raw_child_owners[id(raw)] = raw
                    self._late_raw_child_tasks[id(raw)] = cleanup
                    self._late_child_cleanup_tasks.add(cleanup)
                    cleanup.add_done_callback(self._settled_late_child_cleanup_for(raw))
                return
            self._retain_late_child_environment(environment)

        acquisition.add_done_callback(settled)

    def _settled_late_child_cleanup_for(self, raw: Any) -> Callable[[asyncio.Task[Any]], None]:
        def settled(completed: asyncio.Task[Any]) -> None:
            self._late_child_cleanup_tasks.discard(completed)
            if self._late_raw_child_tasks.get(id(raw)) is completed:
                self._late_raw_child_tasks.pop(id(raw), None)
            if not completed.cancelled() and completed.exception() is None:
                self._late_raw_child_owners.pop(id(raw), None)
            if not completed.cancelled():
                with contextlib.suppress(BaseException):
                    completed.exception()

        return settled

    def _settled_late_child_cleanup(self, completed: asyncio.Task[Any]) -> None:
        self._late_child_cleanup_tasks.discard(completed)
        if not completed.cancelled():
            with contextlib.suppress(BaseException):
                completed.exception()

    async def _close_late_child(self, environment: ChildEnvironment) -> None:
        """Close one child acquired after its original caller timed out."""
        try:
            await environment.close()
        except BaseException:
            # Keep failed ownership visible for the next runtime shutdown retry.
            return

    def _bind_child_owner(self, environment: ChildEnvironment) -> None:
        """Remove a child from the runtime only after successful close."""
        if getattr(environment, "_daytona_runtime_owner", None) is self:
            return
        previous = environment._owner.on_closed

        async def chained(_closed: RootSessionLease) -> None:
            try:
                if previous is not None:
                    result = previous(_closed)
                    if inspect.isawaitable(result):
                        await result
            finally:
                async with self._child_lock:
                    self._children.discard(environment)

        environment._owner.on_closed = chained
        environment._daytona_runtime_owner = self

    def _schedule_unpublished_resource_cleanup(
        self,
        owner: _UnpublishedResourceOwner,
        *,
        deadline: float | None = None,
    ) -> asyncio.Task[Any]:
        """Start one retained compatibility cleanup task and keep it owned."""
        owner.deadline = deadline
        task = owner.cleanup_task
        if task is not None and not task.done():
            return task
        cleanup_awaitable = self._finish_unpublished_resource(owner)
        try:
            cleanup = asyncio.create_task(
                cleanup_awaitable,
                name="fleet-daytona-unpublished-resource-cleanup",
            )
        except BaseException:
            cleanup_awaitable.close()
            raise
        owner.cleanup_task = cleanup
        self._unpublished_resource_tasks.add(cleanup)

        def settled(done: asyncio.Task[Any]) -> None:
            self._unpublished_resource_tasks.discard(done)
            if not done.cancelled():
                with contextlib.suppress(BaseException):
                    done.exception()

        cleanup.add_done_callback(settled)
        return cleanup

    async def _finish_unpublished_resource(self, owner: _UnpublishedResourceOwner) -> None:
        """Run release-then-quarantine for a compatibility resource owner."""
        async with owner.cleanup_lock:
            if owner.quarantined:
                pass
            else:
                lease = owner.lease
                # Keep an InterpreterLease's owner callback deferred.  This
                # preserves admission/claim ownership while the separate
                # compatibility quarantine call is still unresolved.
                if hasattr(lease, "_defer_owner_release"):
                    lease._defer_owner_release = True
                if hasattr(lease, "_defer_idle_cleanup"):
                    lease._defer_idle_cleanup = True
                if owner.release_callback is not None and hasattr(lease, "_on_release"):
                    lease._on_release = lambda: None
                if not owner.released:
                    result = owner.manager.release(lease)
                    if inspect.isawaitable(result):
                        await result
                    owner.released = True
                quarantine = getattr(owner.manager, "quarantine", None)
                if not callable(quarantine):
                    raise RuntimeError("compatibility Daytona manager cannot quarantine a Sandbox")
                result = quarantine(lease, owner.request, **_optional_deadline_kwargs(quarantine, owner.deadline))
                if inspect.isawaitable(result):
                    await result
                owner.quarantined = True
            callback = owner.release_callback
            if not owner.callback_settled:
                if owner.callback_started:
                    raise RuntimeError("unpublished resource finalization remains unresolved")
                owner.callback_started = True
                if callback is not None:
                    result = callback()
                    if inspect.isawaitable(result):
                        await result
                owner.callback_settled = True
            self._unpublished_resource_owners.pop(id(owner.lease), None)
            lease = owner.lease
            if owner.release_callback is not None and hasattr(lease, "_on_release"):
                # The manager release has settled, so restoring the callback is
                # now race-free. The lease is already closed and cannot invoke
                # it a second time.
                lease._on_release = owner.release_callback
            if hasattr(lease, "_defer_owner_release"):
                lease._defer_owner_release = False
            if hasattr(lease, "_defer_idle_cleanup"):
                lease._defer_idle_cleanup = False

    async def _release_and_quarantine_compatibility(
        self,
        lease: Any,
        request: Any,
        *,
        deadline: float | None,
    ) -> None:
        """Own fallback cleanup until interpreter release and fencing pass."""
        manager = getattr(self._resources, "session_manager", None)
        if manager is None:
            raise RuntimeError("Daytona resources do not expose a session manager")
        owner = self._unpublished_resource_owners.get(id(lease))
        if owner is None:
            callback = getattr(lease, "_on_release", None)
            owner = _UnpublishedResourceOwner(
                lease=lease,
                request=request,
                manager=manager,
                release_callback=callback if callable(callback) else None,
                deadline=deadline,
            )
            self._unpublished_resource_owners[id(lease)] = owner
        else:
            owner.request = request
            owner.deadline = deadline
        task = self._schedule_unpublished_resource_cleanup(owner, deadline=deadline)
        await asyncio.shield(task)

    async def _acquire_from_resources(self, spec: RootSessionSpec, *, force_new: bool = False, **_kwargs: Any) -> Any:
        from fleet_rlm.daytona.session_manager import LeaseRequest

        resources = self._resources
        manager = getattr(resources, "session_manager", None)
        platform = getattr(resources, "platform", None)
        if manager is None or platform is None:
            raise RuntimeError("Daytona resources do not expose a session manager")
        request = LeaseRequest(
            session_id=_coerce_uuid(spec.session_id, "session_id"),
            user_id=_coerce_uuid(spec.user_id or uuid4(), "user_id"),
            workspace_id=_coerce_uuid(spec.workspace_id, "workspace_id"),
            run_id=_coerce_uuid(spec.run_id, "run_id") if spec.run_id is not None else None,
        )
        deadline = spec.deadline if spec.deadline is not None else float("inf")
        lease = await manager.acquire(request, deadline=deadline, force_new=force_new)

        async def retire_unpublished_lease() -> None:
            # A lookup failure happens before this facade can publish a
            # RootSessionLease. Prefer the manager's single ordered boundary:
            # interpreter shutdown first, then provider fence/delete, while
            # retaining admission and retry ownership if either phase fails.
            ordered = getattr(manager, "release_and_quarantine", None)
            if callable(ordered):
                result = ordered(lease, request, **_optional_deadline_kwargs(ordered, deadline))
                if inspect.isawaitable(result):
                    await result
                return
            # Compatibility managers predate the ordered boundary. Retain a
            # runtime owner while release runs first and quarantine follows;
            # a failed fence must not strand the lease after this method exits.
            await self._release_and_quarantine_compatibility(lease, request, deadline=deadline)

        def schedule_retirement(lookup_task: asyncio.Task[Any] | None = None) -> asyncio.Task[Any]:
            existing_task = self._unpublished_retirement_tasks.get(id(lease))
            if existing_task is not None and not existing_task.done():
                return existing_task

            async def finish_retirement() -> None:
                if lookup_task is not None:
                    with contextlib.suppress(BaseException):
                        await asyncio.shield(lookup_task)
                await retire_unpublished_lease()

            self._unpublished_retirement_owners[id(lease)] = (lease, finish_retirement)
            retirement = finish_retirement()
            try:
                task = asyncio.create_task(retirement, name="fleet-daytona-unpublished-root-retirement")
            except BaseException:
                retirement.close()
                raise
            self._unpublished_resource_tasks.add(task)
            self._unpublished_retirement_tasks[id(lease)] = task

            def settled(done: asyncio.Task[Any]) -> None:
                self._unpublished_resource_tasks.discard(done)
                if self._unpublished_retirement_tasks.get(id(lease)) is done:
                    self._unpublished_retirement_tasks.pop(id(lease), None)
                if not done.cancelled() and done.exception() is None:
                    self._unpublished_retirement_owners.pop(id(lease), None)
                if not done.cancelled():
                    with contextlib.suppress(BaseException):
                        done.exception()

            task.add_done_callback(settled)
            return task

        async def lookup_sandbox() -> Any:
            return await _maybe_await(platform.get(lease.sandbox_id))

        lookup_task = asyncio.create_task(lookup_sandbox(), name="fleet-daytona-root-sandbox-lookup")
        try:
            if spec.deadline is None:
                sandbox = await asyncio.shield(lookup_task)
            else:
                remaining = spec.deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError("Daytona Sandbox lookup timed out")
                sandbox = await asyncio.wait_for(asyncio.shield(lookup_task), timeout=remaining)
        except BaseException as provider_error:
            # The manager lease is owned from acquire onward, including a
            # provider lookup failure. Fence and release it before propagating
            # so neither admission capacity nor a stale provider binding is
            # stranded. Preserve caller cancellation even if cleanup reports
            # a secondary failure.
            retirement_task = schedule_retirement(lookup_task)
            try:
                await asyncio.shield(retirement_task)
            except BaseException as cleanup_error:
                if isinstance(provider_error, asyncio.CancelledError):
                    provider_error.add_note(f"unpublished lease cleanup failed: {type(cleanup_error).__name__}")
                    raise provider_error from None
                raise cleanup_error from provider_error
            raise
        if sandbox is None:
            retirement_task = schedule_retirement(lookup_task)
            await asyncio.shield(retirement_task)
            raise RuntimeError("acquired Daytona Sandbox is unavailable")
        return lease, sandbox

    async def _release_from_resources(self, lease: Any) -> Any:
        manager = getattr(self._resources, "session_manager", None)
        if manager is None:
            return await _close_child_lease(lease)
        result = manager.release(lease)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _acquire_child_from_resources(self, spec: ChildEnvironmentSpec, **_kwargs: Any) -> Any:
        from fleet_rlm.daytona.recursive_child_runtime import build_child_runtime_factory

        resources = self._resources
        platform = getattr(resources, "platform", None)
        admission = getattr(resources, "daytona_admission", None)
        if platform is None or admission is None or not spec.volume_id or not spec.mount_path:
            raise RuntimeError("Daytona child specification is incomplete")
        if spec.workspace_id is None or spec.run_id is None:
            raise RuntimeError("Daytona child specification is incomplete")
        loop = asyncio.get_running_loop()
        settings = getattr(resources, "settings", None)
        factory = build_child_runtime_factory(
            loop=loop,
            dispatcher=getattr(resources, "dispatcher", None),
            platform=platform,
            admission=admission,
            volume_id=spec.volume_id,
            mount_path=spec.mount_path,
            workspace_id=_coerce_uuid(spec.workspace_id, "workspace_id"),
            run_id=_coerce_uuid(spec.run_id, "run_id"),
            deadline=spec.deadline if spec.deadline is not None else float("inf"),
            execution_timeout_s=spec.execution_timeout_s or int(getattr(settings, "rlm_execution_timeout_s", 600)),
            execution_output_cap=spec.execution_output_cap
            or int(getattr(settings, "rlm_max_execution_output_chars", 10_000)),
            is_authorized=spec.is_authorized,
        )
        # ``build_child_runtime_factory`` is intentionally synchronous because
        # native recursive RLM calls originate on a DSPy worker thread.  The
        # public async facade must move that wait off the owner loop; the
        # factory posts provider work back onto this loop itself.
        return await asyncio.to_thread(factory, spec.call_index)


def _lease_fingerprint(lease: RootSessionLease) -> object | None:
    spec = lease.spec
    return getattr(spec, "context_fingerprint", None)


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    """Mark an owned task exception as observed without changing its result."""
    if task.cancelled():
        return
    with contextlib.suppress(BaseException):
        task.exception()


def _coerce_uuid(value: UUID | str, name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc


async def _acquire_lock(lock: asyncio.Lock, deadline: float | None, message: str) -> None:
    """Acquire an event-loop lock without exceeding an absolute deadline."""
    if deadline is None:
        await lock.acquire()
        return
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError(message)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=remaining)
    except TimeoutError:
        raise TimeoutError(message) from None


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "ChildEnvironment",
    "ChildEnvironmentSpec",
    "DaytonaRuntime",
    "DaytonaRuntimeState",
    "LeaseState",
    "RootSessionLease",
    "RootSessionSpec",
]
