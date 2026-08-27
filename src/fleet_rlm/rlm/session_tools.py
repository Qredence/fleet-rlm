"""Stable, provider-neutral Tool proxies for reusable Session interpreters.

A reusable interpreter may retain a Tool alias in its Python globals after a
Turn ends.  The alias must not retain the host callable, a Run authority, or a
Turn payload.  :class:`SessionToolRegistry` therefore keeps the only mutable
binding and each proxy resolves that registry by name for every call.

The registry is deliberately independent from Daytona and from the event
observer.  Callers may install already-observed ``dspy.Tool`` instances; the
proxy forwards to the current Tool's ``func`` without replacing that wrapper.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import inspect
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from threading import RLock
from types import MappingProxyType
from typing import Any, Protocol, TypeAlias, cast
from uuid import UUID

import dspy

# Retained for import compatibility with the pre-P45 proxy API.  These values
# no longer constrain model-facing Tool metadata: the Session proxy snapshots
# the frozen source contract exactly and sanitizes only event/result payloads.
MAX_TOOL_NAME_CHARS = 96
MAX_TOOL_DESCRIPTION_CHARS = 512
MAX_TOOL_ARGUMENTS = 32
MAX_TOOL_ARGUMENT_NAME_CHARS = 96
MAX_TOOL_ARGUMENT_DESCRIPTION_CHARS = 256
MAX_TOOL_SCHEMA_BYTES = 8_192
MAX_SESSION_TOOL_COUNT = 64
MAX_SESSION_ID_CHARS = 128


class SessionToolAuthorizationError(RuntimeError):
    """Closed failure raised when a retained Tool is not authorized now."""

    public_message = "Tool is not authorized for this Turn"

    def __init__(self) -> None:
        super().__init__(self.public_message)


# A more specific spelling is useful at integration seams while retaining one
# public failure shape for all fail-closed cases.
SessionToolUnavailableError = SessionToolAuthorizationError


class ToolAuthorization(Protocol):
    """Optional structural authorization object accepted by ``install``."""

    def is_live(self) -> bool: ...

    def allows(self, tool_name: str) -> bool: ...


ClaimCheck: TypeAlias = Callable[..., bool]
AuthorizationCheck: TypeAlias = Callable[..., bool]


def _identity_key(value: object, *, label: str, max_chars: int = MAX_SESSION_ID_CHARS) -> tuple[str, str | int]:
    """Create a bounded comparison key without retaining an opaque object.

    Run and Session ids in Fleet are normally UUIDs or strings.  The identity
    fallback keeps this small provider-neutral seam useful for tests and
    adapters that use an opaque object while avoiding a strong reference to
    that object (which could itself contain authorization or payload state).
    """
    if isinstance(value, bool):
        raise TypeError(f"{label} must be a stable id")
    if isinstance(value, UUID):
        return ("uuid", str(value))
    if isinstance(value, str):
        if not value or len(value) > max_chars:
            raise ValueError(f"{label} is invalid")
        return ("str", value)
    if isinstance(value, int):
        return ("int", str(value))
    if value is None:
        raise TypeError(f"{label} must be a stable id")
    # ``id`` is intentionally used instead of repr/str: arbitrary objects
    # may contain secrets, and converting them would retain those values in
    # the registry's state or in an exception.
    return ("object", id(value))


def _identity_label(key: tuple[str, str | int]) -> str:
    """Return a bounded non-sensitive label for diagnostics/lease state."""
    kind, value = key
    if kind == "object":
        return "opaque"
    return str(value)[:MAX_SESSION_ID_CHARS]


def _copy_tool_metadata(
    source: dspy.Tool,
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, str] | None]:
    """Snapshot the source Tool's exact model-facing metadata.

    A Session proxy must not retain the source Tool or its callable, but its
    public contract is frozen: descriptions, JSON schemas, type hints, and
    argument descriptions are copied without redaction, truncation, default
    replacement, or shape changes.  Deep-copying isolates the resident proxy
    from later host mutation while keeping the metadata itself exact.
    """
    try:
        return (
            source.desc,
            copy.deepcopy(source.args),
            copy.deepcopy(source.arg_types),
            copy.deepcopy(source.arg_desc),
        )
    except Exception as exc:
        # A metadata object that cannot be snapshotted cannot safely be
        # installed into a reusable resident program.  Fail closed rather
        # than silently changing the model-facing contract.
        raise ValueError("Tool metadata is not copyable") from exc


def _set_tool_metadata(
    proxy: dspy.Tool,
    metadata: tuple[str | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, str] | None],
) -> None:
    """Apply one exact metadata snapshot to a proxy."""
    description, args, arg_types, arg_desc = metadata
    proxy.desc = description
    proxy.args = args
    proxy.arg_types = arg_types
    proxy.arg_desc = arg_desc


def _call_gate(gate: ClaimCheck | AuthorizationCheck | None, value: str) -> bool:
    """Call a zero- or one-argument gate and fail closed on every defect."""
    if gate is None:
        return False
    try:
        try:
            signature = inspect.signature(gate)
        except (TypeError, ValueError):
            result = gate(value)
        else:
            parameters = tuple(signature.parameters.values())
            accepts_value = any(parameter.kind is parameter.VAR_POSITIONAL for parameter in parameters) or any(
                parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
                for parameter in parameters
            )
            result = gate(value) if accepts_value else gate()
        # A synchronous DSPy Tool cannot safely wait on an async claim or
        # authorization callback.  Close a coroutine when possible to avoid a
        # warning, then deny rather than accidentally allowing the call.
        if inspect.isawaitable(result):
            close = getattr(result, "close", None)
            if callable(close):
                close()
            return False
        return result is True
    except BaseException:
        # Authorization is a fail-closed boundary, including cancellation or
        # an ill-behaved callback supplied by an integration adapter.
        return False


def _authorization_gate(authorization: object | None) -> AuthorizationCheck | None:
    if authorization is None:
        return None
    for name in ("allows", "authorize", "is_authorized"):
        candidate = getattr(authorization, name, None)
        if callable(candidate):
            return cast(AuthorizationCheck, candidate)
    if callable(authorization):
        return cast(AuthorizationCheck, authorization)
    return None


def _claim_gate(authorization: object | None) -> ClaimCheck | None:
    if authorization is None:
        return None
    for name in ("is_live", "is_valid", "valid", "active"):
        candidate = getattr(authorization, name, None)
        if callable(candidate):
            return cast(ClaimCheck, candidate)
    return None


@dataclass(frozen=True, slots=True)
class _ActiveBinding:
    run_key: tuple[str, str | int]
    run_label: str
    tools: Mapping[str, dspy.Tool]
    authorized_names: frozenset[str]
    claim_check: ClaimCheck | None
    authorization_check: AuthorizationCheck | None
    generation: int
    remove_revocation_listener: Callable[[], None] | None = None


@dataclass(frozen=True, slots=True)
class SessionToolBinding:
    """A removable, run-specific lease for an installed Tool set.

    The lease intentionally stores only the registry, a bounded Run label, and
    a generation.  It never stores the claim/authorization object or source
    Tool tuple.  A stale lease cannot clear a newer Turn binding.
    """

    _registry: SessionToolRegistry = field(repr=False, compare=False)
    run_id: str
    generation: int
    tools: tuple[dspy.Tool, ...]
    _run_key: tuple[str, str | int] = field(repr=False, compare=False)

    def remove(self) -> bool:
        """Remove this exact binding, returning false when it is already stale."""
        return self._registry._remove_key(self._run_key, generation=self.generation)

    def clear(self) -> bool:
        """Alias for :meth:`remove`."""
        return self.remove()

    close = remove


class SessionToolRegistry:
    """Own stable proxies and one active Turn capability binding.

    Construct one registry per Session.  ``install`` replaces the complete
    active program Tool set; it does not merge with a prior Turn.  Proxy
    objects for removed names stay as inert aliases so a Python global retained
    by the interpreter fails closed instead of silently acquiring a stale
    callable.
    """

    def __init__(
        self,
        *,
        max_tools: int = MAX_SESSION_TOOL_COUNT,
    ) -> None:
        if not 1 <= max_tools <= MAX_SESSION_TOOL_COUNT:
            raise ValueError("max_tools is invalid")
        self._max_tools = max_tools
        self._lock = RLock()
        self._active: _ActiveBinding | None = None
        self._generation = 0
        self._proxies: dict[str, dspy.Tool] = {}
        self._inflight: dict[int, set[tuple[asyncio.AbstractEventLoop, asyncio.Task[Any]]]] = {}

    def _new_proxy(self, name: str, source: dspy.Tool | None = None) -> dspy.Tool:
        """Create one proxy whose closure contains only this registry/name."""
        registry = self

        def invoke(**kwargs: Any) -> Any:
            return registry._invoke(name, kwargs)

        if source is None:
            metadata: tuple[str | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, str] | None] = (
                f"Invoke the authorized host capability {name!r}.",
                {},
                {},
                {},
            )
        else:
            metadata = _copy_tool_metadata(source)
        # Keep this constructor spelling aligned with the exact DSPy 3.3.1
        # public Tool signature.  In particular, explicit metadata prevents
        # the proxy's ``**kwargs`` callable from being reflected as its model
        # facing contract.
        proxy = dspy.Tool(
            invoke,
            name=name,
            desc=metadata[0],
            args=metadata[1],
            arg_types=metadata[2],
            arg_desc=metadata[3],
        )
        _set_tool_metadata(proxy, metadata)
        return proxy

    @staticmethod
    def _reset_proxy(proxy: dspy.Tool, name: str) -> None:
        """Keep a stale alias's frozen metadata while making its call inert.

        A retained interpreter global can still inspect a removed Tool. The
        alias must fail closed, but replacing its description/schema would make
        the model-facing contract depend on the last binding lifecycle rather
        than on the frozen source Tool. Metadata is therefore intentionally
        left untouched; only the registry's active binding is cleared.
        """
        del proxy, name

    def _update_proxy(self, proxy: dspy.Tool, source: dspy.Tool, name: str) -> None:
        del name
        _set_tool_metadata(proxy, _copy_tool_metadata(source))

    def _validate_sources(self, tools: Iterable[dspy.Tool]) -> tuple[dspy.Tool, ...]:
        values = tuple(tools)
        if len(values) > self._max_tools:
            raise ValueError("program Tool set is too large")
        names: set[str] = set()
        for tool in values:
            if not isinstance(tool, dspy.Tool):
                raise TypeError("SessionToolRegistry requires dspy.Tool values")
            name = tool.name
            if not isinstance(name, str) or not name or len(name) > MAX_TOOL_NAME_CHARS:
                raise ValueError("Tool name is invalid")
            if name in names:
                raise ValueError("program Tool names must be unique")
            names.add(name)
        return values

    def _cancel_inflight(self, generation: int) -> None:
        """Cancel async proxy calls belonging to a retired binding generation."""
        pending = self._inflight.pop(generation, ())
        for loop, task in pending:
            if task.done():
                continue
            # Removal can be called by a cleanup thread, while the Tool runs
            # on the Session worker loop; marshal cancellation to that loop.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(task.cancel)

    def install(
        self,
        tools: Iterable[dspy.Tool],
        *,
        run_id: object,
        claim_valid: ClaimCheck | None = None,
        authorized_names: Iterable[str] | None = None,
        is_authorized: AuthorizationCheck | None = None,
        authorization: object | None = None,
        revocation: object | None = None,
    ) -> tuple[dspy.Tool, ...]:
        """Replace the active Turn binding and return stable proxy objects.

        ``claim_valid`` and one of ``authorized_names``/``is_authorized`` are
        required for a call to succeed.  Omitting either intentionally installs
        an inert binding, which makes an integration defect fail closed.
        ``authorization`` is a provider-neutral convenience object exposing
        ``is_live`` and ``allows(name)`` (or equivalent method names).
        """
        values = self._validate_sources(tools)
        run_key = _identity_key(run_id, label="run_id")
        run_label = _identity_label(run_key)

        if authorization is not None:
            if claim_valid is None:
                claim_valid = _claim_gate(authorization)
            if is_authorized is None:
                is_authorized = _authorization_gate(authorization)

        names = tuple(cast(str, tool.name) for tool in values)
        if authorized_names is None:
            allowed = frozenset(names) if is_authorized is not None else frozenset()
        else:
            provided = frozenset(name for name in authorized_names if isinstance(name, str))
            allowed = frozenset(name for name in names if name in provided)

        # Snapshot source metadata before mutating the active binding.  This
        # ensures malformed metadata cannot leave a half-installed Tool set.
        metadata: dict[str, tuple[str | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, str] | None]] = {}
        for source, name in zip(values, names, strict=True):
            metadata[name] = _copy_tool_metadata(source)

        with self._lock:
            self._generation += 1
            generation = self._generation
            # Replace, never update, the active mapping.  Dropping this object
            # releases every prior source Tool and its authorization closure.
            active_tools: dict[str, dspy.Tool] = {}
            for source, name in zip(values, names, strict=True):
                proxy = self._proxies.get(name)
                if proxy is None:
                    proxy = self._new_proxy(name)
                    self._proxies[name] = proxy
                _set_tool_metadata(proxy, metadata[name])
                active_tools[name] = source
            for old_name, proxy in self._proxies.items():
                if old_name not in active_tools:
                    self._reset_proxy(proxy, old_name)
            prior = self._active
            if prior is not None:
                self._cancel_inflight(prior.generation)
                if prior.remove_revocation_listener is not None:
                    prior.remove_revocation_listener()
            active = _ActiveBinding(
                run_key=run_key,
                run_label=run_label,
                tools=MappingProxyType(active_tools),
                authorized_names=allowed,
                claim_check=claim_valid,
                authorization_check=is_authorized,
                generation=generation,
            )
            self._active = active
            add_listener = getattr(revocation, "add_revoke_listener", None)
            if callable(add_listener):

                def listener() -> None:
                    self._cancel_inflight(generation)

                try:
                    remove_listener = add_listener(listener)
                except BaseException:
                    remove_listener = None
                self._active = _ActiveBinding(
                    run_key=active.run_key,
                    run_label=active.run_label,
                    tools=active.tools,
                    authorized_names=active.authorized_names,
                    claim_check=active.claim_check,
                    authorization_check=active.authorization_check,
                    generation=active.generation,
                    remove_revocation_listener=remove_listener if callable(remove_listener) else None,
                )
            return tuple(self._proxies[name] for name in names)

    def bind_turn(
        self,
        tools: Iterable[dspy.Tool],
        *,
        run_id: object,
        claim_valid: ClaimCheck | None = None,
        authorized_names: Iterable[str] | None = None,
        is_authorized: AuthorizationCheck | None = None,
        authorization: object | None = None,
        revocation: object | None = None,
    ) -> SessionToolBinding:
        """Install one Turn and return a stale-safe removal lease."""
        proxies = self.install(
            tools,
            run_id=run_id,
            claim_valid=claim_valid,
            authorized_names=authorized_names,
            is_authorized=is_authorized,
            authorization=authorization,
            revocation=revocation,
        )
        with self._lock:
            active = self._active
            if active is None:  # pragma: no cover - install is atomic
                raise RuntimeError("Tool binding was not installed")
            return SessionToolBinding(self, active.run_label, active.generation, proxies, active.run_key)

    # Explicit spelling for callers that prefer the P45 terminology.
    install_turn = bind_turn

    def _remove_key(
        self,
        run_key: tuple[str, str | int] | None,
        *,
        generation: int | None = None,
    ) -> bool:
        """Remove by an already-normalized key (used by stale-safe leases)."""
        with self._lock:
            active = self._active
            if active is None:
                return False
            if run_key is not None and run_key != active.run_key:
                return False
            if generation is not None and generation != active.generation:
                return False
            self._cancel_inflight(active.generation)
            if active.remove_revocation_listener is not None:
                active.remove_revocation_listener()
            self._active = None
            for name, proxy in self._proxies.items():
                self._reset_proxy(proxy, name)
            return True

    def remove(
        self,
        *,
        run_id: object | None = None,
        generation: int | None = None,
    ) -> bool:
        """Remove the active binding if it matches the optional claim fence."""
        run_key = _identity_key(run_id, label="run_id") if run_id is not None else None
        return self._remove_key(run_key, generation=generation)

    def clear(
        self,
        *,
        run_id: object | None = None,
        generation: int | None = None,
    ) -> bool:
        """Alias for :meth:`remove` used by Turn cleanup paths."""
        return self.remove(run_id=run_id, generation=generation)

    def proxy(self, name: str) -> dspy.Tool:
        """Return/create the stable alias for ``name`` without authorizing it."""
        if not isinstance(name, str) or not name or len(name) > MAX_TOOL_NAME_CHARS:
            raise ValueError("Tool name is invalid")
        with self._lock:
            proxy = self._proxies.get(name)
            if proxy is None:
                proxy = self._new_proxy(name)
                self._proxies[name] = proxy
            return proxy

    get_proxy = proxy

    def tools(self) -> tuple[dspy.Tool, ...]:
        """Return exactly the currently installed program Tool set."""
        with self._lock:
            active = self._active
            if active is None:
                return ()
            return tuple(self._proxies[name] for name in active.tools)

    @property
    def active_names(self) -> frozenset[str]:
        """Return names in the current program Tool set, never prior names."""
        with self._lock:
            return frozenset(self._active.tools) if self._active is not None else frozenset()

    @property
    def active_run_id(self) -> str | None:
        """Return a bounded run label for diagnostics, not an auth object."""
        with self._lock:
            return self._active.run_label if self._active is not None else None

    def _authorize(self, active: _ActiveBinding, name: str) -> dspy.Tool:
        """Return the current Tool only after all live authorization checks pass."""
        source = active.tools.get(name)
        if source is None or name not in active.authorized_names:
            raise SessionToolAuthorizationError
        if not _call_gate(active.claim_check, active.run_label):
            raise SessionToolAuthorizationError
        if active.authorization_check is not None and not _call_gate(active.authorization_check, name):
            raise SessionToolAuthorizationError
        return source

    def _invoke(self, name: str, kwargs: Mapping[str, Any]) -> Any:
        """Resolve and invoke one Tool while the binding is current."""
        with self._lock:
            active = self._active
            if active is None:
                raise SessionToolAuthorizationError
            source = self._authorize(active, name)
            result = source.func(**dict(kwargs))
            if not inspect.isawaitable(result):
                # Keep the lock through synchronous source invocation. Same-
                # session Turns cannot swap the source during its side effect.
                return result

        async def await_authorized() -> Any:
            # Register the task as an in-flight generation lease. Retiring a
            # binding cancels it on its owning event loop, so a suspended old
            # Tool cannot resume under a newer Turn's authorization.
            task = asyncio.current_task()
            loop = asyncio.get_running_loop()
            registered = False
            with self._lock:
                current = self._active
                if current is not active:
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()
                    raise SessionToolAuthorizationError
                self._authorize(current, name)
                if task is not None:
                    self._inflight.setdefault(active.generation, set()).add((loop, task))
                    registered = True
            try:
                value = await result
                # A claim can be revoked without replacing the binding. Check
                # again before publishing the old operation's result.
                with self._lock:
                    current = self._active
                    if current is not active:
                        raise SessionToolAuthorizationError
                    self._authorize(current, name)
                return value
            finally:
                if registered and task is not None:
                    with self._lock:
                        pending = self._inflight.get(active.generation)
                        if pending is not None:
                            pending.discard((loop, task))
                            if not pending:
                                self._inflight.pop(active.generation, None)

        return await_authorized()


class SessionToolProxyFactory:
    """Create one independent stable Tool registry per Session id."""

    def __init__(self, *, max_tools: int = MAX_SESSION_TOOL_COUNT) -> None:
        self._max_tools = max_tools
        self._lock = RLock()
        self._registries: dict[tuple[tuple[str, str | int], ...], SessionToolRegistry] = {}

    def for_session(self, session_id: object, workspace_id: object | None = None) -> SessionToolRegistry:
        """Return a registry keyed by full Workspace+Session tenancy.

        The one-argument form remains a compatibility convenience for callers
        whose process already has a workspace-scoped factory.  Production
        callers should pass both IDs so equal Session UUID text across
        Workspaces cannot share authorization bindings.
        """
        session_key = _identity_key(session_id, label="session_id")
        key = (session_key,)
        if workspace_id is not None:
            key = (_identity_key(workspace_id, label="workspace_id"), session_key)
        with self._lock:
            registry = self._registries.get(key)
            if registry is None:
                registry = SessionToolRegistry(max_tools=self._max_tools)
                self._registries[key] = registry
            return registry

    create = for_session
    registry_for = for_session

    def __call__(self, session_id: object) -> SessionToolRegistry:
        return self.for_session(session_id)


def create_session_tool_registry(*, max_tools: int = MAX_SESSION_TOOL_COUNT) -> SessionToolRegistry:
    """Return a provider-neutral registry for one Session."""
    return SessionToolRegistry(max_tools=max_tools)


__all__ = [
    "MAX_SESSION_TOOL_COUNT",
    "MAX_TOOL_ARGUMENTS",
    "MAX_TOOL_DESCRIPTION_CHARS",
    "MAX_TOOL_NAME_CHARS",
    "MAX_TOOL_SCHEMA_BYTES",
    "AuthorizationCheck",
    "ClaimCheck",
    "SessionToolAuthorizationError",
    "SessionToolBinding",
    "SessionToolProxyFactory",
    "SessionToolRegistry",
    "SessionToolUnavailableError",
    "ToolAuthorization",
    "create_session_tool_registry",
]
