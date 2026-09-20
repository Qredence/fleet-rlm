"""Public Daytona runtime boundary for reusable roots and disposable children.

DaytonaRuntime manages session-scoped reusable root Sandboxes and ephemeral
disposable child Environments for recursive execution.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from fleet_rlm.daytona.interpreter import DEFAULT_EXECUTION_OUTPUT_CHARS
from fleet_rlm.daytona.provisioning import DaytonaEnvironmentProfile, execution_timeout_s_from_settings
from fleet_rlm.daytona.session_manager import LeaseState, RootSessionLease

if TYPE_CHECKING:
    from daytona import AsyncDaytona

    from fleet_rlm.config.settings import Settings


_DAYTONA_CLOUD_API_URL = "https://app.daytona.io/api"


def build_async_daytona_client(settings: Settings) -> AsyncDaytona:
    """Construct the process-owned asynchronous Daytona SDK client."""
    from daytona import AsyncDaytona, DaytonaConfig

    api_key: str | None = None
    if settings.daytona_api_key is not None:
        raw = settings.daytona_api_key
        api_key = raw.get_secret_value() if hasattr(raw, "get_secret_value") else str(raw)
        api_key = api_key or None
    config_kwargs: dict[str, Any] = {"api_url": _DAYTONA_CLOUD_API_URL}
    if api_key:
        config_kwargs["api_key"] = api_key
    if settings.daytona_org_id:
        config_kwargs["organization_id"] = settings.daytona_org_id
    client = AsyncDaytona(DaytonaConfig(**config_kwargs))
    if settings.daytona_org_id and api_key and hasattr(client, "_api_client"):
        client._api_client.default_headers["X-Daytona-Organization-ID"] = settings.daytona_org_id
    return client


build_daytona_client = build_async_daytona_client


def _sandbox_fs(sandbox: Any) -> Any:
    return getattr(sandbox, "fs", sandbox)


async def read_file(sandbox: Any, path: str) -> bytes:
    data = await _maybe_await(_sandbox_fs(sandbox).download_file(path))
    return data.encode("utf-8") if isinstance(data, str) else bytes(data)


async def write_file(sandbox: Any, path: str, data: bytes) -> None:
    await _maybe_await(_sandbox_fs(sandbox).upload_file(data, path))


async def list_files(sandbox: Any, path: str, *, depth: int = 1) -> list[Any]:
    fs = _sandbox_fs(sandbox)
    try:
        entries = await _maybe_await(fs.list_files(path, depth=depth))
    except TypeError:
        entries = await _maybe_await(fs.list_files(path))
    return list(entries or [])


async def delete_file(sandbox: Any, path: str) -> None:
    await _maybe_await(_sandbox_fs(sandbox).delete_file(path))


async def get_file_info(sandbox: Any, path: str) -> Any:
    return await _maybe_await(_sandbox_fs(sandbox).get_file_info(path))


async def create_folder(sandbox: Any, path: str, mode: str = "755") -> None:
    await _maybe_await(_sandbox_fs(sandbox).create_folder(path, mode=mode))


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


def _coerce_uuid(value: UUID | str, name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    text = _identity_text(value, name)
    try:
        return UUID(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid UUID: {text}") from exc


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _lease_fingerprint(lease: Any) -> object | None:
    spec = getattr(lease, "spec", None)
    if spec is not None and hasattr(spec, "context_fingerprint"):
        return spec.context_fingerprint
    return getattr(lease, "context_fingerprint", None)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


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


@dataclass(frozen=True, slots=True)
class ChildEnvironmentSpec:
    """Immutable selectors and bounds for one disposable child."""

    profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.WORKSPACE_CHILD
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
        profile = self.profile
        if not isinstance(profile, DaytonaEnvironmentProfile):
            try:
                profile = DaytonaEnvironmentProfile(str(profile))
            except ValueError as exc:
                raise ValueError("unknown Daytona child environment profile") from exc
            object.__setattr__(self, "profile", profile)
        if profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD and (
            self.volume_id or self.mount_path or self.volume_subpath
        ):
            raise ValueError("SemanticChild cannot carry a Workspace Volume binding")
        if not isinstance(self.call_index, int) or isinstance(self.call_index, bool) or self.call_index < 0:
            raise ValueError("call_index must be a non-negative integer")
        if self.deadline is not None and not isinstance(self.deadline, (int, float)):
            raise TypeError("deadline must be numeric or None")

    @property
    def key(self) -> tuple[str, str] | None:
        if self.workspace_id is None or self.session_id is None:
            return None
        return (_identity_text(self.workspace_id, "workspace_id"), _identity_text(self.session_id, "session_id"))


async def _close_child_lease(lease: Any) -> Any:
    """Close a sync or async child lease."""
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

    def __init__(
        self,
        spec: ChildEnvironmentSpec,
        lease: Any,
        *,
        sandbox: Any | None = None,
        on_closed: Callable[[RootSessionLease], Any] | None = None,
    ) -> None:
        self.spec = spec
        self.lease = lease
        self.sandbox = sandbox if sandbox is not None else getattr(lease, "sandbox", None)
        self.interpreter = getattr(lease, "interpreter", None)
        sandbox_id = getattr(lease, "sandbox_id", None) or getattr(self.sandbox, "id", None)
        self.sandbox_id = str(sandbox_id or "")
        self.volume_id = _optional_text(getattr(lease, "volume_id", None)) or spec.volume_id
        self.volume_subpath = _optional_text(getattr(lease, "volume_subpath", None)) or spec.volume_subpath
        self.mount_path = _optional_text(getattr(lease, "mount_path", None)) or spec.mount_path
        self._owner = RootSessionLease(
            spec.key or ("child", str(spec.call_index)),
            lease,
            _close_child_lease,
            on_closed=on_closed,
            sandbox=self.sandbox,
            interpreter=self.interpreter,
            volume=self.volume_id,
            volume_id=self.volume_id,
            mount_path=self.mount_path,
            volume_subpath=self.volume_subpath,
        )

    @property
    def state(self) -> LeaseState:
        return self._owner.state

    @property
    def status(self) -> LeaseState:
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
        self._children: set[ChildEnvironment] = set()
        self._lock = asyncio.Lock()
        self._state = DaytonaRuntimeState.OPEN

        if self._resources is not None:
            if self._root_acquirer is None:
                self._root_acquirer = self._acquire_from_resources
            if self._root_releaser is None:
                self._root_releaser = self._release_from_resources
            if self._child_acquirer is None:
                self._child_acquirer = self._acquire_child_from_resources
            manager = getattr(self._resources, "session_manager", None)
            bind_runtime = getattr(manager, "bind_runtime", None)
            if callable(bind_runtime):
                bind_runtime(self)

    @property
    def state(self) -> DaytonaRuntimeState:
        """Return the runtime facade lifecycle state."""
        return self._state

    @property
    def roots(self) -> tuple[RootSessionLease, ...]:
        """Return a view of retained root leases."""
        return tuple(self._roots.values())

    def owns_open_root(self, workspace_id: UUID | str | None, session_id: UUID | str) -> bool:
        """Return True when an OPEN root retains this Session."""
        sid = _identity_text(session_id, "session_id")
        if workspace_id is not None:
            try:
                owner = self._roots.get((_identity_text(workspace_id, "workspace_id"), sid))
            except ValueError:
                owner = None
            if owner is not None and not owner.closed:
                return True
        return any(
            not owner.closed and isinstance(owner.key, tuple) and len(owner.key) > 1 and str(owner.key[1]) == sid
            for owner in tuple(self._roots.values())
        )

    @property
    def children(self) -> tuple[ChildEnvironment, ...]:
        """Return a view of currently owned disposable children."""
        return tuple(self._children)

    async def acquire_root_session(self, spec: RootSessionSpec) -> RootSessionLease:
        """Acquire or reuse the root for ``(workspace_id, session_id)``."""
        if not isinstance(spec, RootSessionSpec):
            raise TypeError("spec must be RootSessionSpec")
        if self._state is not DaytonaRuntimeState.OPEN:
            raise RuntimeError("Daytona runtime is not accepting root Sessions")

        key = spec.key
        async with self._lock:
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
                self._roots.pop(key, None)

            raw = await self._acquire_root_from_provider(spec, force_new=must_replace or spec.force_new)
            owner = self._coerce_root(spec, raw)
            self._roots[key] = owner
            self._tainted.discard(key)
            return owner

    async def discard_stale_root_session(
        self,
        workspace_id: UUID | str,
        session_id: UUID | str,
        *,
        deadline: float | None = None,
    ) -> None:
        """Drop a resident root after external replacement."""
        key = (_identity_text(workspace_id, "workspace_id"), _identity_text(session_id, "session_id"))
        async with self._lock:
            owner = self._roots.pop(key, None)
        if owner is not None:
            with contextlib.suppress(Exception):
                await owner.close(notify=False, deadline=deadline)

    def mark_root_tainted(self, workspace_id: UUID | str, session_id: UUID | str) -> None:
        """Fence a root so the next acquisition rotates its generation."""
        key = (_identity_text(workspace_id, "workspace_id"), _identity_text(session_id, "session_id"))
        self._tainted.add(key)

    def open_child(self, spec: ChildEnvironmentSpec) -> _ChildContext:
        """Return a disposable child context."""
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
        """Close one retained root."""
        key = (_identity_text(workspace_id, "workspace_id"), _identity_text(session_id, "session_id"))
        async with self._lock:
            owner = self._roots.get(key)
        if owner is not None:
            await owner.close(deadline=deadline)

    async def aclose(self, *, deadline: float | None = None) -> bool:
        """Close all retained roots and active children.

        Registry entries remain owned until their close is confirmed.  This is
        important when cancellation or a provider failure interrupts shutdown:
        a later close call must be able to retry the same lease instead of
        losing the only reference to it.
        """
        self._state = DaytonaRuntimeState.CLOSING
        errors: list[BaseException] = []

        async with self._lock:
            children = tuple(self._children)
            roots = tuple(self._roots.values())

        for child in children:
            try:
                await child.close(deadline=deadline)
            except BaseException as exc:
                errors.append(exc)
            else:
                if child.closed:
                    async with self._lock:
                        self._children.discard(child)

        for root in roots:
            try:
                await root.close(deadline=deadline)
            except BaseException as exc:
                errors.append(exc)
            else:
                if root.closed:
                    async with self._lock:
                        if self._roots.get(root.key) is root:
                            self._roots.pop(root.key, None)

        async with self._lock:
            retained = bool(self._children or self._roots)
        self._state = DaytonaRuntimeState.FAILED if errors or retained else DaytonaRuntimeState.CLOSED
        return not errors and not retained

    async def close(self, *, deadline: float | None = None) -> bool:
        return await self.aclose(deadline=deadline)

    async def _acquire_root_from_provider(self, spec: RootSessionSpec, *, force_new: bool) -> Any:
        if spec.deadline is not None and spec.deadline <= asyncio.get_running_loop().time():
            raise TimeoutError("root Session acquisition timed out")
        acquirer = self._root_acquirer
        if acquirer is None:
            raise RuntimeError("no root acquirer configured")

        async def _call() -> Any:
            try:
                sig = inspect.signature(acquirer)
                res = acquirer(spec, force_new=force_new) if "force_new" in sig.parameters else acquirer(spec)
            except (TypeError, ValueError):
                res = acquirer(spec)
            return await _maybe_await(res)

        if spec.deadline is None:
            return await _call()
        remaining = max(0.0, spec.deadline - asyncio.get_running_loop().time())
        return await asyncio.wait_for(_call(), timeout=remaining)

    def _coerce_root(self, spec: RootSessionSpec, raw: Any) -> RootSessionLease:
        sandbox: Any | None = None
        candidate = raw
        if isinstance(raw, tuple) and len(raw) == 2:
            candidate, sandbox = raw
        if isinstance(candidate, RootSessionLease):
            candidate.spec = spec
            candidate.key = spec.key
            if sandbox is not None:
                candidate.sandbox = sandbox
            return candidate

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
            spec=spec,
            sandbox=sandbox,
            interpreter=getattr(candidate, "interpreter", None),
            broker=getattr(candidate, "broker", None),
            volume=getattr(candidate, "volume", None),
            volume_id=getattr(candidate, "volume_id", None),
            mount_path=getattr(candidate, "mount_path", None),
            volume_subpath=getattr(candidate, "volume_subpath", None),
        )

    async def _acquire_child(self, spec: ChildEnvironmentSpec) -> ChildEnvironment:
        if self._state is not DaytonaRuntimeState.OPEN:
            raise RuntimeError("Daytona runtime is not accepting child Environments")
        acquirer = self._child_acquirer
        if acquirer is None:
            raise RuntimeError("Daytona child acquisition is unavailable")
        if spec.deadline is not None and spec.deadline <= asyncio.get_running_loop().time():
            raise TimeoutError("child Environment acquisition timed out")

        async def _call() -> Any:
            res = acquirer(spec)
            return await _maybe_await(res)

        if spec.deadline is None:
            raw = await _call()
        else:
            remaining = max(0.0, spec.deadline - asyncio.get_running_loop().time())
            raw = await asyncio.wait_for(_call(), timeout=remaining)

        environment = self._coerce_child(spec, raw)
        async with self._lock:
            environment._owner.on_closed = self._deregister_child
            self._children.add(environment)
        return environment

    def _coerce_child(self, spec: ChildEnvironmentSpec, raw: Any) -> ChildEnvironment:
        sandbox: Any | None = None
        candidate = raw
        if isinstance(raw, tuple) and len(raw) == 2:
            candidate, sandbox = raw
        if isinstance(candidate, ChildEnvironment):
            return candidate
        return ChildEnvironment(spec, candidate, sandbox=sandbox)

    async def _deregister_child(self, owner: RootSessionLease) -> None:
        """Forget a child only after its provider cleanup has succeeded."""
        async with self._lock:
            self._children = {child for child in self._children if child._owner is not owner}

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
        sandbox = await _maybe_await(platform.get(lease.sandbox_id))
        if sandbox is None:
            raise RuntimeError("acquired Daytona Sandbox is unavailable")
        return lease, sandbox

    async def _release_from_resources(self, lease: Any) -> Any:
        manager = getattr(self._resources, "session_manager", None)
        if manager is None:
            return await _close_child_lease(lease)
        result = manager.release(lease)
        return await _maybe_await(result)

    async def _acquire_child_from_resources(self, spec: ChildEnvironmentSpec, **_kwargs: Any) -> Any:
        from fleet_rlm.daytona.recursive_child_runtime import build_child_runtime_factory

        resources = self._resources
        platform = getattr(resources, "platform", None)
        admission = getattr(resources, "daytona_admission", None)
        if platform is None or admission is None:
            raise RuntimeError("Daytona child specification is incomplete")
        if spec.workspace_id is None or spec.run_id is None:
            raise RuntimeError("Daytona child specification is incomplete")
        if spec.profile is not DaytonaEnvironmentProfile.SEMANTIC_CHILD and (not spec.volume_id or not spec.mount_path):
            raise RuntimeError("WorkspaceChild specification requires a Volume binding")

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
            execution_timeout_s=(
                spec.execution_timeout_s
                if spec.execution_timeout_s is not None
                else execution_timeout_s_from_settings(settings)
            ),
            execution_output_cap=(
                spec.execution_output_cap
                if spec.execution_output_cap is not None
                else getattr(settings, "rlm_max_execution_output_chars", DEFAULT_EXECUTION_OUTPUT_CHARS)
            ),
            is_authorized=spec.is_authorized,
            profile=spec.profile,
        )
        return await asyncio.to_thread(factory, spec.call_index)
