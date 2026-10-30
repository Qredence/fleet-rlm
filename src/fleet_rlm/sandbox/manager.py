"""DaytonaSandboxManager: one Session sandbox (and its child sandboxes), budgets, workspace files, and notes."""

from __future__ import annotations

import base64
import contextvars
import functools
import hashlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import dspy
import mlflow
from daytona import (
    CreateSandboxFromSnapshotParams,
    Daytona,
    DaytonaConfig,
    ListSandboxesQuery,
    VolumeMount,
)
from mlflow.entities import SpanType

from fleet_rlm.config import validate_shared_notes_subpath
from fleet_rlm.contracts import STOPPING_RULES
from fleet_rlm.sandbox import repl
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError, provider_not_found, with_retries
from fleet_rlm.sandbox.runtime.fs_ops import (
    CLEAR_WORKSPACE_CODE,
)
from fleet_rlm.sandbox.snapshots import SNAPSHOT_NAME, SnapshotIdentity, preflight
from fleet_rlm.sandbox.workspace_io import WorkspaceIOMixin
from fleet_rlm.workspace import (
    _child_input_path,
    _workspace_relative_path,
    session_volume_subpath,
    validate_session_id,
)

if TYPE_CHECKING:
    from fleet_rlm.sandbox.interpreter import DaytonaInterpreter

_LOGGER = logging.getLogger(__name__)
SANDBOX_TRANSPORT = "daytona_code_interpreter"

# The cleanup outcome a terminal Turn records. `stopped` and `unconfirmed` are produced by the
# manager's single teardown; `not_required` belongs to a Turn that completed and kept its root.
CLEANUP_STOPPED = "stopped"
CLEANUP_UNCONFIRMED = "unconfirmed"
CLEANUP_NOT_REQUIRED = "not_required"

# Distinguishes "the caller supplied no Turn policy" from an explicit `None` (deadline-free).
_UNSET = object()

# Daytona sandbox states, as the provider reports them after `refresh_data()`.
_RESUMABLE_STATES = frozenset({"stopped", "archived", "paused"})
_SETTLING_STATES = frozenset({"stopping", "archiving", "pausing", "starting", "resuming", "restoring", "creating"})
_FAILED_STATES = frozenset({"error", "build_failed", "destroyed", "destroying"})
_STATE_POLL_SECONDS = 1.0
_STATE_SETTLE_SECONDS = 90.0
# Provider lifecycle bounds are requested, never removed: a deadline-free Turn still has them.
_ROOT_TTL_MINUTES = 120
_CHILD_TTL_MINUTES = 30

_DAYTONA_CLIENTS: dict[tuple[str, str], Daytona] = {}
_DAYTONA_CLIENTS_LOCK = threading.Lock()


def _host_call_bound(remaining: float | None) -> int | None:
    """Whole seconds a bridged host call may wait: the Turn's remainder, or None when it has none.

    Truncation never waits past the deadline. A remainder below one whole second has no honest
    representation: `0` means "no timeout" on the transport, and every positive integer is wider
    than what the Turn has left. `run_namespace` therefore admits an execution through
    `admit_execution`, which samples once and refuses such a Turn before calling this
    helper; the helper raises ValueError for a remainder below one second instead of widening it.
    """
    if remaining is None:
        return None
    if remaining < 1:
        raise ValueError("A host-call bound needs at least one whole second remaining")
    return int(remaining)


def _provider_minutes(value: Any) -> int | None:
    """Return a provider-reported minute bound as an integer, or None when it is absent."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _shared_daytona_client(api_key: str, org_id: str) -> Daytona:
    """One client per credential pair: each `Daytona()` opens its own event-dispatcher connection and thread."""
    with _DAYTONA_CLIENTS_LOCK:
        client = _DAYTONA_CLIENTS.get((api_key, org_id))
        if client is None:
            client = _DAYTONA_CLIENTS[api_key, org_id] = Daytona(DaytonaConfig(api_key=api_key, organization_id=org_id))
        return client


def _sandbox_state(sandbox: Any) -> str:
    """Refresh the handle from the provider and return its lowercase state ("" when unknown)."""
    refresh = getattr(sandbox, "refresh_data", None)
    if callable(refresh):
        refresh()
    state = getattr(sandbox, "state", None)
    return str(getattr(state, "value", state) or "").lower()


_ACTIVE_MANAGER: contextvars.ContextVar[Any] = contextvars.ContextVar("fleet_active_manager", default=None)
_NATIVE_CHILDREN: contextvars.ContextVar[list[Any] | None] = contextvars.ContextVar(
    "fleet_native_children", default=None
)


@dataclass(slots=True)
class DaytonaSandboxManager(WorkspaceIOMixin):
    """Own one warm Daytona Sandbox mounted to a single Session's Volume subpath."""

    api_key: str
    org_id: str
    volume_id: str
    session_id: str
    snapshot: str = SNAPSHOT_NAME
    auto_stop_minutes: int = 30
    sub_lm: dspy.LM | None = None
    jev_lm: Any | None = None
    max_llm_calls: int = 150
    max_depth: int = 3
    max_children: int = 8
    max_active_children: int = 4
    turn_timeout_seconds: int = 900
    linked_children: bool = True
    disposable: bool = False
    shared_notes_subpath: str = ""
    event_callback: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False)
    # Durable once-per-Session marker owned by the Store. It must outlive a released or rebuilt
    # runtime, so it is claimed atomically instead of relying on in-process state.
    environment_report_claim: Callable[[str], bool] | None = field(default=None, repr=False)
    ownership_epoch: int | None = field(default=None, repr=False)
    ownership_resource_mutate: Callable[[str, dict[str, Any] | None], bool] | None = field(default=None, repr=False)
    # Called the instant preflight resolves the Turn identity, before any later preparation step
    # can fail, so a caller can record provenance without re-deriving it.
    snapshot_resolved_callback: Callable[[SnapshotIdentity], None] | None = field(default=None, repr=False)
    parent: DaytonaSandboxManager | None = field(default=None, repr=False)
    agent_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    _children: dict[str, DaytonaSandboxManager] = field(default_factory=dict, init=False, repr=False)
    _allocations: int = field(default=0, init=False, repr=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    # One publication token per live execution. Cancellation closes them and nothing reopens one:
    # the next Turn clears `_cancelled` for its own work, never these tokens.
    _execution_fences: set[repl.ExecutionFence] = field(default_factory=set, init=False, repr=False)
    # One teardown per Turn: the watchdog, a client disconnect, and the terminal Turn branch all
    # reach cancel_turn(), and only the first call may stop the provider sandbox.
    _cleanup_outcome: str | None = field(default=None, init=False, repr=False)
    _teardown_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    # One owner per Turn for terminal cancellation. Test-and-set under `_teardown_lock`, so the
    # watchdog, the deadline path, a client disconnect, and the terminal Turn branch cannot each
    # invoke cancel_turn(); every loser waits for the owner's teardown and reads its outcome.
    _cancellation_claimed: bool = field(default=False, init=False, repr=False)
    _teardown_done: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    # Serializes every shutdown() call for this agent. A normal child completion and cancel_turn()
    # can race for the same child; a joiner must wait for the provider cleanup it can observe.
    _shutdown_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    # One policy per Turn, resolved before the Turn starts. `None` means the Turn has no Fleet
    # deadline; ownership is tracked separately so a deadline-free Turn is still a real Turn.
    _deadline: float | None = field(default=None, init=False, repr=False)
    _deadline_source: str = field(default="default", init=False, repr=False)
    _effective_deadline_seconds: int | None = field(default=None, init=False, repr=False)
    # Every watchdog callback carries the generation it was armed for, so a timer from an
    # earlier Turn can never cancel the current one.
    _turn_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _turn_generation: int = field(default=0, init=False, repr=False)
    _turn_active: bool = field(default=False, init=False, repr=False)
    _watchdog: threading.Timer | None = field(default=None, init=False, repr=False)
    _sandbox: Any = field(default=None, init=False, repr=False)
    _snapshot_identity: SnapshotIdentity | None = field(default=None, init=False, repr=False)
    _client: Daytona | None = field(default=None, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _budget_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _llm_calls_used: int = field(default=0, init=False, repr=False)
    _sandbox_errors: int = field(default=0, init=False, repr=False)
    _submit_rejections: int = field(default=0, init=False, repr=False)
    _budget_breakdown: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(("generation_query", "routing", "skill_selection", "other_decisions"), 0),
        init=False,
        repr=False,
    )
    # One observation event per Session, not per Turn or per module: absence is context, never failure.
    _environment_reported: bool = field(default=False, init=False, repr=False)
    _child_receipts: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _interpreters: dict[str, DaytonaInterpreter] = field(default_factory=dict, init=False, repr=False)
    # One Daytona interpreter context per Fleet namespace; invalid once the sandbox stops.
    _contexts: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _creation_pending: bool = field(default=False, init=False, repr=False)
    # Independent of Turn generations: resetting a budget cannot make an unresolved resource safe.
    # Retain its handle (or pending allocation labels) until shutdown confirms the intended action.
    _cleanup_pending: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate identity, budgets, and the shared-notes subpath before any sandbox work."""
        validate_session_id(self.session_id)
        if not self.api_key or not self.org_id or not self.volume_id:
            raise ValueError("Daytona API key, organization ID, and Volume ID are required")
        if self.max_llm_calls < 1:
            raise ValueError("max_llm_calls must be positive")
        validate_shared_notes_subpath(self.shared_notes_subpath)

    @property
    def root(self) -> DaytonaSandboxManager:
        """Return the top-level manager that owns the Turn budget and deadline."""
        return self.parent.root if self.parent is not None else self

    @property
    def depth(self) -> int:
        """Return how many parents separate this agent from the root."""
        return self.parent.depth + 1 if self.parent is not None else 0

    @property
    def turn_active(self) -> bool:
        """True while a Turn owns this manager, including a deadline-free Turn."""
        return self.root._turn_active

    def deadline_provenance(self) -> dict[str, Any]:
        """Return the Turn's resolved source and effective duration; `None` is kept explicit."""
        root = self.root
        return {
            "deadline_source": root._deadline_source,
            "effective_seconds": root._effective_deadline_seconds,
        }

    @property
    def snapshot_identity(self) -> SnapshotIdentity | None:
        """Return the provider identity resolved by the Turn preflight, if any."""
        return self._snapshot_identity

    def snapshot_provenance(self) -> dict[str, Any]:
        """Return the preflight-resolved identity as flat provenance fields.

        Only the root manager resolves a Turn snapshot; a child inherits it and never
        invents a provider id, so it returns no fields.
        """
        identity = self._snapshot_identity
        return identity.provenance() if identity is not None else {}

    def provider_bounds(self) -> dict[str, Any]:
        """Report the provider-owned lifecycle bounds without changing or hiding them.

        These are Daytona's bounds, distinct from the Fleet deadline: a deadline-free Turn
        still reports them. Values the provider exposed on the handle are used as reported;
        a bound the handle does not expose falls back to the value this Turn requested, and
        an unknown maximum lifespan stays explicitly `None`.
        """
        root = self.root
        sandbox = root._sandbox
        requested_ttl = _CHILD_TTL_MINUTES if self.parent is not None else _ROOT_TTL_MINUTES
        ttl = _provider_minutes(getattr(sandbox, "ttl_minutes", None))
        auto_stop = _provider_minutes(getattr(sandbox, "auto_stop_interval", None))
        lifespan_value = getattr(sandbox, "maximum_lifespan_minutes", None)
        if lifespan_value is None:
            lifespan_value = getattr(sandbox, "max_lifespan_minutes", None)
        if lifespan_value is None:
            lifespan_value = getattr(sandbox, "lifespan_minutes", None)
        lifespan = _provider_minutes(lifespan_value)
        return {
            "ttl_minutes": requested_ttl if ttl is None else ttl,
            "auto_stop_interval": root.auto_stop_minutes if auto_stop is None else auto_stop,
            "maximum_lifespan_minutes": lifespan,
            # Explicit marker: these are provider-owned, not Fleet deadlines.
            "provider_owned": True,
        }

    @property
    def live_sandbox_id(self) -> str | None:
        """Return the live root provider handle id, or None when nothing was provisioned.

        This is the handle the Turn actually executed against, so two warm Turns can show
        they shared one sandbox. A child reads its root's handle; a Session that never
        provisioned reports no id rather than inventing one.
        """
        sandbox = self.root._sandbox
        return getattr(sandbox, "id", None) if sandbox is not None else None

    def emit(self, event: str, **payload: Any) -> None:
        """Send one lifecycle event, tagged with agent lineage, to the root's callback."""
        callback = self.root.event_callback
        if callback:
            callback(
                {
                    "event": event,
                    "agent_id": self.agent_id,
                    "parent_agent_id": self.parent.agent_id if self.parent else None,
                    "sandbox_id": getattr(self._sandbox, "id", None),
                    **payload,
                }
            )

    def _notify(self, event: str, **payload: Any) -> None:
        """Deliver one lifecycle notification without letting delivery failure affect teardown.

        Event delivery is a side channel. A persistent stream failure must not skip a provider
        cleanup, so each refused notification is logged once with a bounded message: the lifecycle
        event name, the Session, and the exception type. No event payload, generated source, or
        provider text is logged.
        """
        try:
            self.emit(event, **payload)
        except Exception as exc:
            _LOGGER.warning(
                "Lifecycle notification %s failed for Session %s (%s)",
                event,
                self.session_id,
                type(exc).__name__,
            )

    def check_turn(self, *, minimum_seconds: float = 0.0) -> None:
        """Raise if the Turn was cancelled or its deadline is within `minimum_seconds`.

        `minimum_seconds` is the smallest remainder the upcoming operation can honor. Expiry
        marks the Turn cancelled, so the terminal path classifies and stores it as a deadline
        cancellation rather than a generic failure.

        Raises:
            RuntimeError: The Turn has already been cancelled.
            TimeoutError: The remaining deadline is at or below the required minimum.
        """
        root = self.root
        if root._cancelled.is_set():
            raise RuntimeError("Turn cancelled")
        if root._deadline is not None and time.monotonic() + minimum_seconds >= root._deadline:
            self._expire_turn()

    def _expire_turn(self) -> None:
        """Mark the Turn cancelled and stop publication, then raise the deadline error."""
        root = self.root
        root._cancelled.set()
        # Publication stops the moment the deadline marks the Turn cancelled, not only when the
        # terminal teardown later runs.
        root._fence_executions()
        raise TimeoutError("Turn deadline exceeded")

    def admit_execution(self) -> float | None:
        """Sample the remaining deadline once and admit one execution, or expire the Turn.

        The returned remainder is the only value that may drive the execution. The transport
        waits in whole seconds, so a positive remainder below one second has no honest bound:
        `0` means "no timeout" and every whole positive second is wider than the Turn has left.
        Such a Turn is expired here, with the same cancellation and publication fence as any other
        deadline, before any execution or host call starts. A remainder of exactly one second is
        representable and still executes; sampling the clock twice would let the two reads straddle
        the threshold (admission 1.5 s, bound 0.4 s) and turn the missing representation into a
        transport error for work that never began.

        Returns:
            Remaining seconds, or None when the Turn has no Fleet deadline.

        Raises:
            RuntimeError: The Turn was cancelled.
            TimeoutError: Less than one second remains; the Turn is marked cancelled.
        """
        root = self.root
        if root._cancelled.is_set():
            raise RuntimeError("Turn cancelled")
        remaining = self.remaining_turn_seconds()
        if root._deadline is not None and remaining is not None and remaining < 1.0:
            self._expire_turn()
        return remaining

    def _open_execution_fence(self) -> repl.ExecutionFence:
        """Return this execution's publication token, already closed if the Turn is cancelled."""
        root = self.root
        with root._lock:
            # Bind the Turn's current emitter now: a later Turn replaces the root's callback, so
            # reading it at refusal time would attribute this execution's fence to that Turn.
            fence = repl.ExecutionFence(root.event_callback)
            if root._cancelled.is_set():
                fence.close()
            root._execution_fences.add(fence)
        return fence

    def _report_fence(self, fence: repl.ExecutionFence, ns_id: str) -> None:
        """Deliver a refused publication through the emitter its execution opened with.

        A closed channel is not an error: the fence itself already logged one bounded warning
        without the tool payload or generated source, and that warning is the record. An emitter
        that raises is reported once with a bounded message and never escapes the host-call worker.
        """
        emitter = fence.emitter
        if emitter is None:
            return
        try:
            emitter(
                {
                    "event": "fenced",
                    "agent_id": self.agent_id,
                    "parent_agent_id": self.parent.agent_id if self.parent else None,
                    "sandbox_id": getattr(self._sandbox, "id", None),
                    "namespace": ns_id,
                }
            )
        except Exception as exc:
            _LOGGER.warning(
                "Fenced observation could not be delivered for Session %s (%s)",
                self.session_id,
                type(exc).__name__,
            )

    def _release_execution_fence(self, fence: repl.ExecutionFence) -> None:
        """End this execution's publication window and stop tracking its token."""
        fence.close()
        root = self.root
        with root._lock:
            root._execution_fences.discard(fence)

    def _fence_executions(self) -> None:
        """Close every live execution token so no successful reply can publish afterwards."""
        root = self.root
        with root._lock:
            fences = tuple(root._execution_fences)
        for fence in fences:
            fence.close()

    def remaining_turn_seconds(self) -> float | None:
        """Seconds left in this Turn's deadline; None when the Turn has no Fleet deadline.

        The deadline is the only source of a task-duration bound. Connection, provisioning, and
        maintenance timeouts are separate and must never be reported as the Turn's remainder.
        """
        root = self.root
        if root._deadline is not None:
            return root._deadline - time.monotonic()
        return None

    def allocate_child(self, *, input_paths: list[str] | None = None) -> DaytonaSandboxManager:
        """Trace preparation separately from generated execution, without recording file contents."""
        with self._lifecycle_span("sandbox.child_prepare") as span:
            child = self._allocate_child(input_paths=input_paths)
            span.set_attribute("child_agent_id", child.agent_id)
            span.set_attribute("child_sandbox_id", getattr(child._sandbox, "id", None))
            return child

    @contextmanager
    def _lifecycle_span(self, name: str) -> Generator[Any, None, None]:
        # Manual spans deliberately record only identity and numeric transfer metadata, never self/credentials.
        failure: BaseException | None = None
        with mlflow.start_span(name=name, span_type=SpanType.TOOL) as span:
            span.set_attributes(
                {"agent_id": self.agent_id, "parent_agent_id": self.parent.agent_id if self.parent else None}
            )
            span.set_inputs({"operation": name})
            completed = False
            try:
                yield span
                completed = True
            except BaseException as exc:
                # Close the span before propagating provider messages or workspace paths.
                failure = exc
                span.set_status("ERROR")
            finally:
                span.set_attribute("sandbox_id", getattr(self._sandbox, "id", None))
                span.set_outputs({"completed": completed})
        if failure is not None:
            raise failure

    def _allocate_child(self, *, input_paths: list[str] | None = None) -> DaytonaSandboxManager:
        """Reserve and provision a child sandbox, copying the requested or default inputs into it.

        Capacity limits refuse the spawn before any sandbox is created. Skills are copied but not receipted as inputs.
        """
        root = self.root
        self.check_turn()
        self._refuse_unresolved_cleanup()
        selected_paths: list[str] | None = None
        ambient_skills: set[str] = set()
        if input_paths is not None:
            if not isinstance(input_paths, list):
                raise TypeError("input_paths must be a list of workspace paths")
            if len(input_paths) > 100:
                raise ValueError("Child input file limit exceeded")
            selected_paths = []
            for path in input_paths:
                selected_paths.append(_child_input_path(path))
            available_paths = set()
            for path in self.list_workspace_files():
                try:
                    available_paths.add(_workspace_relative_path(path))
                except ValueError:
                    continue
            missing_paths = [path for path in selected_paths if path not in available_paths]
            if missing_paths:
                raise ValueError(f"Child input file is unavailable: {missing_paths[0]}")
            # Skills are part of every sandbox, not task inputs, so they are copied but not receipted as inputs.
            ambient_skills = {path for path in available_paths if path.startswith("skills/")} - set(selected_paths)
            selected_paths.extend(sorted(ambient_skills))

        with root._budget_lock:
            if self.depth >= root.max_depth:
                raise RuntimeError("Agent recursion depth exhausted")
            if root._allocations >= root.max_children or len(root._children) >= root.max_active_children:
                raise RuntimeError("Child sandbox capacity exhausted; spawn refused to avoid a wait cycle")
            child = DaytonaSandboxManager(
                self.api_key,
                self.org_id,
                self.volume_id,
                self.session_id,
                snapshot=self.snapshot,
                auto_stop_minutes=self.auto_stop_minutes,
                sub_lm=self.sub_lm,
                jev_lm=self.jev_lm,
                max_llm_calls=root.max_llm_calls,
                parent=self,
                linked_children=root.linked_children,
                ownership_epoch=root.ownership_epoch,
                ownership_resource_mutate=root.ownership_resource_mutate,
            )
            root._children[child.agent_id] = child
            root._allocations += 1
        # One absolute deadline travels down the tree. A child, or a grandchild through its
        # child parent, never receives a later deadline than the Turn that started it.
        child._deadline = root._deadline
        child._deadline_source = root._deadline_source
        child._effective_deadline_seconds = root._effective_deadline_seconds
        child._turn_generation = root._turn_generation
        child._turn_active = root._turn_active
        spawn_inputs = [path for path in selected_paths if path not in ambient_skills] if selected_paths else []
        child.emit("agent_spawn", depth=child.depth, **({"input_paths": spawn_inputs} if spawn_inputs else {}))
        with root._budget_lock:
            root._child_receipts[child.agent_id] = {
                "agent_id": child.agent_id,
                "parent_agent_id": self.agent_id,
                "inputs": {},
                "artifacts": [],
                "status": "running",
            }
        try:
            child.ensure_sandbox()
            with child._lifecycle_span("sandbox.input_copy") as span:
                total = 0
                copied = 0
                try:
                    paths_to_copy = selected_paths
                    if paths_to_copy is None:
                        paths_to_copy = [
                            path for path in self.list_workspace_files() if path.startswith(("files/", "skills/"))
                        ]
                    with self._lifecycle_span("sandbox.input_read") as read_span:
                        read_span.set_attribute("target_agent_id", child.agent_id)
                        read_span.set_attribute("files_requested", len(paths_to_copy))
                        try:
                            contents = self.read_workspace_files(paths_to_copy)
                        except ValueError as exc:
                            if "transfer quota" in str(exc):
                                raise ValueError("Child input transfer quota exceeded") from exc
                            raise
                        total = sum(len(data) for data in contents.values())
                        read_span.set_attributes({"files_read": len(contents), "bytes_read": total})
                    with child._lifecycle_span("sandbox.input_write") as write_span:
                        write_span.set_attributes(
                            {
                                "source_agent_id": self.agent_id,
                                "files_requested": len(contents),
                                "bytes_requested": total,
                            }
                        )
                        child.write_workspace_files(contents)
                    copied = len(contents)
                    # Receipts are recorded only after the whole batch landed in the child.
                    with root._budget_lock:
                        root._child_receipts[child.agent_id]["inputs"].update(
                            {
                                path: hashlib.sha256(data).hexdigest()
                                for path, data in contents.items()
                                if path not in ambient_skills
                            }
                        )
                finally:
                    span.set_attributes({"files_copied": copied, "bytes_read": total})
            return child
        except BaseException:
            child.record_child_outcome(False)
            child.shutdown()
            raise

    def record_child_outcome(self, succeeded: bool) -> None:
        """Record invocation success independently of transport/tool-call success."""
        if self.parent is not None:
            with self.root._budget_lock:
                receipt = self.root._child_receipts.get(self.agent_id)
                if receipt is not None:
                    receipt["status"] = "succeeded" if succeeded else "failed"

    @property
    def cleanup_outcome(self) -> str | None:
        """The teardown outcome recorded for the current Turn, or None before any teardown."""
        return self.root._cleanup_outcome

    def request_cancellation(self) -> bool:
        """Claim this Turn's single terminal cancellation and run it, or wait for its owner.

        Every terminal entry point goes through this guard: the terminal Turn branch, the
        deadline and transport-failure paths, the deadline watchdog, a client disconnect, and a
        lost Session lease. The first caller wins the test-and-set and is the only one to invoke
        :meth:`cancel_turn`; every later caller waits until that teardown has finished and then
        returns without invoking it again. Waiting is what keeps acknowledgment after the
        provider stop, even when a disconnect raced the terminal branch.

        Returns ``True`` for the caller that owned the teardown and ``False`` for a caller that
        joined an already-claimed one. No exception escapes: :meth:`cancel_turn` is a
        finalization seam and callers read :attr:`cleanup_outcome`.
        """
        root = self.root
        with root._teardown_lock:
            claimed = not root._cancellation_claimed
            root._cancellation_claimed = True
        if not claimed:
            # The owner may still be stopping the provider; acknowledgment must not precede it.
            root._teardown_done.wait()
            return False
        try:
            self.cancel_turn()
        finally:
            root._teardown_done.set()
        return True

    def cancel_turn(self) -> None:
        """Cancel the Turn, stop child sandboxes, and stop the root sandbox to interrupt remote Python.

        Exactly one teardown runs per Turn: a watchdog expiry, a client disconnect, and the
        terminal Turn branch may all racingly reach this method, and only the first call stops the
        provider or records an outcome. A later caller waits for the teardown in progress and
        returns without repeating it, so acknowledgment can never precede the stop.

        Terminal entry points should call :meth:`request_cancellation` instead: it makes the
        single-invocation guarantee observable to callers, while this method keeps the
        provider-level idempotence that makes a stray direct call harmless.

        The lifecycle notification is a side channel. It runs before the stop so a client learns
        the cancellation promptly, but a failing stream must never skip the teardown, and its
        exception never escapes this method. The same holds for every child's lifecycle events:
        delivery is guarded per notification, then the child's provider cleanup is always attempted.

        Per-child outcomes are aggregated, so one failing child skips neither its siblings nor the
        root stop. The combined outcome is written only once every attempt has run: ``stopped`` when
        every provider cleanup returned, ``unconfirmed`` when any provider cleanup actually failed.
        A notification failure alone never makes the outcome ``unconfirmed``, and no exception
        escapes: callers read :attr:`cleanup_outcome`.
        """
        root = self.root
        root._cancelled.set()
        # Fence outstanding host-call replies before anything else: a successful result still in
        # flight must not publish, and each execution keeps its token closed for good.
        root._fence_executions()
        with root._teardown_lock:
            root._cancellation_claimed = True
            if root._cleanup_outcome is not None:
                return
            try:
                root._notify("cancellation_requested")
                # Share the shutdown owner, but retain this Turn's deadline until its caller ends it.
                with root._shutdown_lock:
                    confirmed = root._shutdown_locked()
                root._cleanup_outcome = CLEANUP_STOPPED if confirmed else CLEANUP_UNCONFIRMED
            finally:
                root._teardown_done.set()

    def _record_allocation(self, status: str) -> None:
        directory = Path(".fleet/allocations")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.agent_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "agent_id": self.agent_id,
                    "session_id": self.session_id,
                    "sandbox_id": getattr(self._sandbox, "id", None),
                    "status": status,
                    "expires_at": time.time() + self.root.turn_timeout_seconds + 120,
                }
            )
        )
        temporary.replace(path)

    def reconcile_allocations(self) -> None:
        """Delete expired, journaled allocations in this session, never unknown resources."""
        if self._client is None:
            return
        for path in Path(".fleet/allocations").glob("*.json"):
            record = json.loads(path.read_text())
            if record["session_id"] != self.session_id or record["expires_at"] > time.time():
                continue
            if record["agent_id"] == self.agent_id:
                continue
            query = ListSandboxesQuery(labels={"fleet_session": self.session_id, "fleet_agent": record["agent_id"]})
            for sandbox in with_retries(functools.partial(self._list_sandboxes, query)):
                with_retries(functools.partial(self._client.delete, sandbox, wait=True, timeout=60))
                self.emit("orphan_deleted", sandbox_id=sandbox.id, agent_id=record["agent_id"])
            path.unlink()

    def volume_mounts(self, *, child: bool) -> list[VolumeMount]:
        """Session subtree at /workspace; a root sandbox also gets the shared notes subtree at /learned."""
        mounts = [
            VolumeMount(
                volume_id=self.volume_id,
                mount_path="/workspace",
                subpath=f"agents/{self.session_id}/{self.agent_id}"
                if child
                else session_volume_subpath(self.session_id),
            )
        ]
        if not child and self.shared_notes_subpath:
            mounts.append(
                VolumeMount(volume_id=self.volume_id, mount_path="/learned", subpath=self.shared_notes_subpath)
            )
        return mounts

    def ensure_sandbox(self) -> Any:
        """Return the live handle, tracing provisioning but not the common already-live case.

        A Turn calls this from many places; emitting a span for each reuse produced 24 of one
        trace's 64 spans, none of which recorded anything happening. Only a create/resume is
        traced, and the caller sees the outcome on the spans that actually do work.
        """
        if self._sandbox is not None:
            return self._ensure_sandbox()
        with self._lifecycle_span("sandbox.provision") as span:
            span.set_attribute("action", "create")
            return self._ensure_sandbox()

    def _refuse_unresolved_cleanup(self) -> None:
        """Deny writable use while this agent or its root still awaits cleanup confirmation.

        The caller holds this manager's lock, or is single-threaded before provisioning starts.
        """
        if self._cleanup_pending or self.root._cleanup_pending:
            raise RuntimeError("Session sandbox cleanup is unconfirmed; retry shutdown before writable reuse")

    def _ensure_sandbox(self) -> Any:
        """Create or resume this agent's sandbox, returning the live handle.

        A pending create is adopted only when exactly one matching sandbox exists, so creates never duplicate.
        """
        self.check_turn()
        with self._lock:
            self._refuse_unresolved_cleanup()
            if self._client is None:
                self._client = _shared_daytona_client(self.api_key, self.org_id)
            if self.parent is None and self._sandbox is None:
                # Turn snapshots must be active at 4 vCPU / 8 GiB before provisioning.
                # Children inherit the root snapshot and are exempt from these floors.
                self._snapshot_identity = preflight(
                    self._client.snapshot,
                    name=self.snapshot,
                    api_key=self.api_key,
                    org_id=self.org_id,
                )
                # Publish the identity immediately: provisioning, replay capture, or the engine may
                # still fail, and those outcomes must keep the same provenance as the record.
                if self.snapshot_resolved_callback is not None:
                    self.snapshot_resolved_callback(self._snapshot_identity)
            created = False
            if self._sandbox is None:
                self.check_turn()
                if self._creation_pending:
                    query = ListSandboxesQuery(labels={"fleet_session": self.session_id, "fleet_agent": self.agent_id})
                    matches = with_retries(functools.partial(self._list_sandboxes, query))
                    if len(matches) != 1:
                        raise RuntimeError("Sandbox allocation outcome is uncertain; refusing a duplicate create")
                    self._sandbox = matches[0]
                    self._record_allocation("ready")
                    self._creation_pending = False
                    return self.ensure_sandbox()
                if self.parent is None:
                    self.reconcile_allocations()
                child = self.parent is not None
                root_sandbox = self.root.ensure_sandbox() if child else None
                params = CreateSandboxFromSnapshotParams(
                    snapshot=self.snapshot,
                    language="python",
                    volumes=self.volume_mounts(child=child),
                    auto_stop_interval=self.auto_stop_minutes,
                    linked_sandbox=root_sandbox.id if root_sandbox is not None and self.linked_children else None,
                    ephemeral=child or self.disposable,
                    auto_delete_interval=0 if child or self.disposable else -1,
                    ttl_minutes=_CHILD_TTL_MINUTES if child else _ROOT_TTL_MINUTES,
                    labels={
                        "fleet_session": self.session_id,
                        "fleet_agent": self.agent_id,
                        "fleet_parent": self.parent.agent_id if child else "root",
                    },
                )
                self.emit("sandbox_provisioning")
                self._record_allocation("requested")
                self._persist_ownership_resource("create", None)
                self._creation_pending = True
                self._sandbox = self._client.create(params)
                self._creation_pending = False
                self._persist_ownership_resource("stop" if not child else "delete", self._sandbox.id)
                self._record_allocation("ready")
                self.check_turn()
                self.emit("sandbox_ready", depth=self.depth)
                created = True
            if not created:
                self._resume_if_needed()
            return self._sandbox

    def _list_sandboxes(self, query: ListSandboxesQuery) -> list[Any]:
        assert self._client is not None
        return list(self._client.list(query))

    def _persist_ownership_resource(self, action: str, provider_id: str | None) -> None:
        """Persist this agent's cleanup intent before remote work, fenced by the Session epoch."""
        if self.ownership_resource_mutate is None and self.ownership_epoch is None:
            # Standalone disposable/test managers have no persisted Session owner.
            return
        if self.ownership_resource_mutate is None or self.ownership_epoch is None:
            raise RuntimeError("Durable sandbox ownership is unavailable")
        resource = (
            None
            if action == "confirmed"
            else {
                "allocation_nonce": self.agent_id,
                "provider_id": provider_id,
                "parent_agent_id": self.parent.agent_id if self.parent else None,
                "action": action,
                "intended_action": "delete" if self.parent is not None or self.disposable else "stop",
                "confirmed": False,
            }
        )
        if not self.ownership_resource_mutate(self.agent_id, resource):
            raise RuntimeError("Session ownership epoch changed; sandbox operation is fenced")

    def reconcile_sandbox_ownership(self, resources: list[dict[str, Any]]) -> None:
        """Confirm cleanup for bounded persisted Daytona resources before Session admission.

        A resource the provider reports as absent cannot still write, so its not-found answer is
        treated as confirmation and its record is dropped. Every other provider failure keeps the
        Session fenced.
        """
        if self.ownership_resource_mutate is None or self.ownership_epoch is None:
            raise RuntimeError("Durable sandbox ownership is unavailable")
        if not resources:
            # Nothing outstanding: admission must not depend on a provider round trip.
            return
        client = _shared_daytona_client(self.api_key, self.org_id)
        for entry in resources:
            agent_id = entry.get("agent_id")
            provider_id = entry.get("provider_id")
            action = entry.get("action")
            intended_action = entry.get("intended_action")
            nonce = entry.get("allocation_nonce")
            if (
                not isinstance(agent_id, str)
                or not isinstance(nonce, str)
                or action not in {"create", "stop", "delete"}
                or intended_action not in {"stop", "delete"}
            ):
                raise RuntimeError("Stored sandbox ownership record is invalid")
            try:
                if not isinstance(provider_id, str) or not provider_id:
                    query = ListSandboxesQuery(labels={"fleet_session": self.session_id, "fleet_agent": nonce})
                    matches = with_retries(functools.partial(lambda q: list(client.list(q)), query))
                    if len(matches) != 1:
                        raise RuntimeError("Sandbox allocation outcome is uncertain; Session admission remains fenced")
                    sandbox = matches[0]
                    provider_id = getattr(sandbox, "id", None)
                    if not isinstance(provider_id, str) or not provider_id:
                        raise RuntimeError("Labeled sandbox has no provider identity")
                else:
                    sandbox = with_retries(lambda provider_id=provider_id: client.get(provider_id))
                if intended_action == "delete":
                    with_retries(lambda sandbox=sandbox: client.delete(sandbox, timeout=60, wait=True))
                else:
                    with_retries(lambda sandbox=sandbox: client.stop(sandbox, timeout=60))
            except Exception as exc:
                if not provider_not_found(exc):
                    raise
                _LOGGER.info(
                    "Recorded sandbox for Session %s agent %s is gone; treating absence as confirmed cleanup",
                    self.session_id,
                    agent_id,
                )
            if not self.ownership_resource_mutate(agent_id, None):
                raise RuntimeError("Session ownership epoch changed during sandbox reconciliation")

    def _resume_if_needed(self) -> None:
        """Start a sandbox the provider has stopped, archived, or paused since the handle was cached.

        The cached `state` lags the provider (Daytona auto-stops idle sandboxes), so the handle is
        refreshed first. A sandbox still settling into a stable state is polled; a failed one raises.
        """
        sandbox = self._sandbox
        assert sandbox is not None and self._client is not None
        state = _sandbox_state(sandbox)
        deadline = time.monotonic() + _STATE_SETTLE_SECONDS
        while state in _SETTLING_STATES:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Sandbox {sandbox.id} did not settle from state {state!r}")
            time.sleep(_STATE_POLL_SECONDS)
            self.check_turn()
            state = _sandbox_state(sandbox)
        if state in _FAILED_STATES:
            raise RuntimeError(f"Sandbox {sandbox.id} is in state {state!r} and cannot be resumed")
        if state in _RESUMABLE_STATES:
            span = mlflow.get_current_active_span()
            if span is not None:
                span.set_attribute("action", "resume")
            self.emit("sandbox_resuming", state=state)
            client = self._client
            with_retries(lambda: client.start(sandbox), before_retry=self.check_turn)
            self._contexts.clear()

    def _remote_exec(
        self, sandbox: Any, code: str, *, timeout: int | None = None, retry: bool = True, retry_span: Any = None
    ) -> str:
        encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
        command = (
            "python3 -c 'import base64; exec(compile(base64.b64decode(\"" + encoded + '"), "<fleet-remote>", "exec"))\''
        )
        if retry:
            result = with_retries(lambda: sandbox.process.exec(command, timeout=timeout), span=retry_span)
        else:
            result = sandbox.process.exec(command, timeout=timeout)
        if getattr(result, "exit_code", 0) != 0:
            raise RuntimeError("Remote Daytona command failed")
        return getattr(result, "result", "") or ""

    def run_namespace(self, interpreter: DaytonaInterpreter, payload: dict[str, Any]) -> dict[str, Any]:
        """Run one execution in the interpreter's context without replaying uncertain work.

        Reuse a prepared context when available. Return the runtime result with
        `transport_retries=0`; remote Python errors remain in that result.

        Raises:
            TimeoutError: The Turn deadline expired or less than one whole second remains.
            RuntimeError: The Turn was cancelled.
            ExecutionOutcomeUnknownError: Execution transport failed or returned no result;
                cancellation is requested unless the Turn already ended.

        Provisioning and context-preparation errors propagate before execution begins.
        """
        self.check_turn()
        sandbox = self.ensure_sandbox()
        with self._lock:
            context = self._contexts.get(interpreter.ns_id)
        if context is None:
            with self._lifecycle_span("sandbox.namespace_prepare") as span:
                span.set_attribute("namespace", interpreter.ns_id)
                context = repl.create_context(sandbox)
            with self._lock:
                self._contexts[interpreter.ns_id] = context
        # A deadline that expired while provisioning must stop the Turn, not execute. One clock
        # read below decides both admission and the host-call bound; a second read could straddle
        # one second (admission 1.5 s, bound 0.4 s) and turn a missing whole-second bound into a
        # transport error for work that never began. A positive remainder below one whole second is
        # exhausted too: 0 now means "no bound", so no value is both honest and not wider than the
        # remainder. admit_execution marks the Turn cancelled and raises the deadline TimeoutError
        # here, before any execution or host call.
        remaining = self.admit_execution()
        # Admission used this exact sample, so the bound is representable. Compute it before the
        # transport region: only a failure after execution starts may become an unknown outcome.
        timeout = _host_call_bound(remaining)
        execution_id = payload.get("execution_id")
        progress_identity = {
            "namespace": interpreter.ns_id,
            "execution_id": execution_id if isinstance(execution_id, str) else None,
        }

        def report_progress(progress: dict[str, Any]) -> None:
            # Observation only: it reports through the Turn's existing emitter and never acts.
            """Emit advisory progress tagged with this namespace and execution identity."""
            self.emit("progress", **progress_identity, **progress)

        fence = self._open_execution_fence()
        try:
            result = repl.run(
                sandbox,
                context,
                payload,
                on_call=interpreter.answer_host_call,
                timeout=timeout,
                fence=fence,
                on_fence=lambda: self._report_fence(fence, interpreter.ns_id),
                on_progress=report_progress,
            )
        except BaseException as exc:
            self.check_turn()  # a deadline or cancellation explains the failure
            try:
                # A dead transport cannot establish rollback; the Turn's one owner stops remote work.
                self.request_cancellation()
            except Exception:
                _LOGGER.warning("Could not confirm remote stop after execution failure")
            if isinstance(exc, ExecutionOutcomeUnknownError):
                raise
            raise ExecutionOutcomeUnknownError(f"Sandbox execution transport failed ({type(exc).__name__})") from exc
        finally:
            self._release_execution_fence(fence)
        self.check_turn()
        return {**result, "transport_retries": 0}

    def drop_namespace(self, ns_id: str) -> None:
        """Delete one interpreter namespace's remote context, if the sandbox still exists."""
        with self._lock:
            context = self._contexts.pop(ns_id, None)
            sandbox = self._sandbox
        if context is not None and sandbox is not None:
            repl.delete_context(sandbox, context)

    def exec_remote_python(self, code: str) -> str:
        """Run bounded maintenance Python in this Session's sandbox."""
        if len(code.encode("utf-8")) > 1_000_000:
            raise ValueError("Remote maintenance code exceeds the size limit")
        return self._remote_exec(self.ensure_sandbox(), code)

    def _report_incomplete_environment(self, missing: Any) -> None:
        """Emit one soft observation per Session when probed modules are absent.

        Missing packages are context, never failure: the probe already returns them in the
        model-visible environment string, and this only records the same set once. The Store's
        atomic claim carries the guarantee across runtime release and manager rebuilds, where
        the in-memory flag necessarily starts over.
        """
        if not isinstance(missing, list):
            return
        modules = [module for module in missing if isinstance(module, str)]
        if not modules:
            return
        root = self.root
        with root._lock:
            if root._environment_reported or root.event_callback is None:
                return
            root._environment_reported = True
            if not root._claim_environment_report():
                return
            try:
                self.emit("sandbox_environment_incomplete", missing=modules)
            except Exception:
                # Observability must never fail a Turn; the claim is already durable, so no retry.
                _LOGGER.warning("Environment observation could not be delivered for Session %s", root.session_id)

    def _claim_environment_report(self) -> bool:
        """Return True only when this process may emit the Session's one environment observation.

        A claim failure suppresses the event rather than risking a duplicate after a rebuild.
        """
        claim = self.environment_report_claim
        if claim is None:
            return True
        try:
            return bool(claim(self.session_id))
        except Exception:
            _LOGGER.warning("Environment observation marker is unavailable for Session %s", self.session_id)
            return False

    def reset_turn_budget(
        self,
        *,
        deadline_seconds: int | object | None = _UNSET,
        deadline_source: str | None = None,
    ) -> None:
        """Start a fresh Turn: zero the counters, clear receipts, and apply the deadline policy.

        `deadline_seconds=None` means this Turn has no Fleet deadline. When the caller omits
        the value the manager resolves configuration itself, which is how the direct engine
        path starts a Turn. Ownership is explicit: a deadline-free Turn still owns a
        generation and a teardown, and arms no watchdog at all.
        """
        root = self.root
        if deadline_seconds is _UNSET:
            seconds: int | None = root.turn_timeout_seconds
            source = deadline_source or "configuration"
        else:
            seconds = cast("int | None", deadline_seconds)
            source = deadline_source or "request"
        effective = seconds if isinstance(seconds, int) and seconds > 0 else None
        # Serialize the generation check and cancellation with Turn end/reset. Timer.cancel()
        # alone cannot revoke a callback that already started on its timer thread.
        with root._turn_lock:
            root.end_turn()
            # A new Turn owns a new teardown; the previous Turn's outcome and owner must never be reused.
            with root._teardown_lock:
                root._cleanup_outcome = None
                root._cancellation_claimed = False
                root._teardown_done.clear()
            with root._budget_lock:
                root._llm_calls_used = 0
                root._sandbox_errors = 0
                root._submit_rejections = 0
                root._budget_breakdown = dict.fromkeys(root._budget_breakdown, 0)
                root._allocations = 0
                root._child_receipts.clear()
                root._cancelled.clear()
                root._turn_generation += 1
                root._turn_active = True
                root._deadline_source = source
                root._effective_deadline_seconds = effective
                if effective is None:
                    # A deadline-free Turn has no Fleet deadline, so there is no timer to arm.
                    # `Timer(0)` would cancel the Turn immediately and must remain impossible.
                    root._deadline = None
                    root._watchdog = None
                else:
                    root._deadline = time.monotonic() + effective
                    generation = root._turn_generation
                    root._watchdog = threading.Timer(effective, lambda: root._expire_generation(generation))
                    root._watchdog.daemon = True
                    root._watchdog.start()

    def _expire_generation(self, generation: int) -> None:
        """A watchdog callback only acts for the Turn that armed it; a stale one is a no-op."""
        root = self.root
        with root._turn_lock:
            if not root._turn_active or root._turn_generation != generation:
                return
            root.request_cancellation()

    def end_turn(self) -> None:
        """Cancel the watchdog and clear the Turn deadline without touching provenance."""
        root = self.root
        with root._turn_lock:
            if root._watchdog is not None:
                root._watchdog.cancel()
                root._watchdog = None
            root._deadline = None
            root._turn_active = False

    @contextmanager
    def turn_scope(
        self,
        *,
        deadline_seconds: int | object | None = _UNSET,
        deadline_source: str | None = None,
    ) -> Generator[None, None, None]:
        """Own a Turn only when nobody owns one already, and end only the Turn this scope started.

        Nested scopes are inert: a Turn the API stream started stays owned by that stream, so a
        program running inside it neither resets the shared budgets nor tears the Turn down. A
        caller with no outer owner (the direct engine path, an isolated optimization rollout)
        gets a fresh budget and a teardown of its own.
        """
        owns = not self.turn_active
        if owns:
            self.reset_turn_budget(deadline_seconds=deadline_seconds, deadline_source=deadline_source)
        try:
            yield
        finally:
            if owns:
                self.end_turn()

    def consume_budget(self, count: int = 1, *, category: str = "generation_query") -> None:
        """Charge shared budget units, categorized without changing admission or completion counts."""
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("budget consumption must be a positive integer")
        if category not in self.root._budget_breakdown:
            raise ValueError("Unknown budget category")
        self.check_turn()
        root = self.root
        with root._budget_lock:
            if root._llm_calls_used + count > root.max_llm_calls:
                raise RuntimeError(f"Shared RLM budget exhausted ({root.max_llm_calls} calls)")
            root._llm_calls_used += count
            root._budget_breakdown[category] += count
            usage = self._usage_unlocked()
        self.emit(
            "budget_update",
            **usage,
            max_llm_calls=root.max_llm_calls,
            tokens=None,
            estimated_cost=None,
        )

    def record_sandbox_error(self) -> None:
        """Count one failed sandbox execution toward the Turn's usage."""
        with self.root._budget_lock:
            self.root._sandbox_errors += 1

    def record_submit_rejection(self) -> None:
        """Count a blocking host rejection once, independently of remote Python failures."""
        with self.root._budget_lock:
            self.root._submit_rejections += 1

    def _usage_unlocked(self) -> dict[str, Any]:
        root = self.root
        return {
            "lm_calls": root._llm_calls_used,
            "sandbox_errors": root._sandbox_errors,
            "submit_rejections": root._submit_rejections,
            "budget_breakdown": dict(root._budget_breakdown),
        }

    def turn_usage(self) -> dict[str, Any]:
        """Host-owned shared budget and recovery counters across the whole agent tree."""
        root = self.root
        with root._budget_lock:
            return self._usage_unlocked()

    def interpreter_factory(
        self,
        tools: Mapping[str, Callable[..., Any]] | None = None,
        *,
        input_paths: list[str] | None = None,
        rlm_defaults: Mapping[str, int] | None = None,
    ) -> Callable[[], DaytonaInterpreter]:
        """Return the fresh interpreter factory required by the pinned DSPy bridge.

        Copy optional RLM defaults for outer Flex construction only. Native facade
        propagation allocates children without inheriting this omitted-value policy.
        """
        captured_input_paths = tuple(input_paths) if input_paths is not None else None
        captured_defaults = dict(rlm_defaults or {})

        def create() -> DaytonaInterpreter:
            from fleet_rlm.sandbox.interpreter import DaytonaInterpreter

            active = _ACTIVE_MANAGER.get()
            if active is not None:
                if captured_input_paths is None:
                    manager = active.allocate_child()
                else:
                    manager = active.allocate_child(input_paths=list(captured_input_paths))
            else:
                manager = self
            return DaytonaInterpreter(
                manager=manager,
                tools=dict(tools or {}),
                owns_manager=active is not None,
                native_input_paths=captured_input_paths,
                # Native facade propagation reuses this factory for deeper RLMs. Defaults
                # belong only to the outer Flex interpreter, not its allocated children.
                rlm_defaults=captured_defaults if active is None else None,
            )

        create.execution_instructions = (  # ty: ignore[unresolved-attribute]
            "Python runs in Daytona with /workspace and workspace helper. "
            "context datasets expose .path/.lines()/.chunks()/.search(). "
            "Use workspace.write_artifact(name, content) for results. "
            "Artifact names are relative to artifacts/ (e.g. 'result.txt'); use the helper's returned path. "
            "Nested DSPy code agents allocate fresh child sandboxes; files/ and skills/ inputs are copied, and "
            "spawn_agent(input_paths=[...]) can also pass earlier artifacts/ paths. "
            "Successful child artifacts appear under artifacts/<agent-id>/; inspect workspace.tree() for their paths. "
            + STOPPING_RULES
        )
        return create

    def _register_interpreter(self, interpreter: DaytonaInterpreter) -> None:
        with self._lock:
            self._interpreters[interpreter.ns_id] = interpreter

    def _unregister_interpreter(self, interpreter: DaytonaInterpreter) -> None:
        with self._lock:
            self._interpreters.pop(interpreter.ns_id, None)

    def shutdown(self) -> bool:
        """Fence work and attempt every required cleanup, retaining unresolved identities.

        Calls serialize on this manager's shutdown lock. The root is interrupted before child
        deletion, including when a child is already draining. A false result keeps writable
        admission fenced across Turn resets; a later shutdown may retry the retained action.
        This is an in-process guarantee, not durable recovery after process restart.
        """
        if self.parent is None:
            self._cancelled.set()
            self._fence_executions()
            # Never wait for the Turn lock while holding the shutdown lock: the watchdog may
            # already own the Turn lock and be joining cleanup.
            self.end_turn()
        with self._shutdown_lock:
            return self._shutdown_locked()

    def _shutdown_locked(self) -> bool:
        """Interrupt this resource first, then aggregate every required child cleanup."""
        if self.parent is not None:
            with self.root._budget_lock:
                receipt = self.root._child_receipts.get(self.agent_id)
                if receipt is not None and receipt["status"] == "running":
                    receipt["status"] = "failed"
        with self._lock:
            self._cleanup_pending = True
            sandbox, client = self._sandbox, self._client
            interpreters = tuple(self._interpreters.values())
            self._contexts.clear()
            self._interpreters.clear()
            for interpreter in interpreters:
                interpreter._closed = True
        confirmed = False
        try:
            if self._creation_pending:
                # An ambiguous create may have succeeded without returning its handle. Empty
                # lookup is not proof of absence; retain the allocation labels for another attempt.
                if client is None:
                    raise RuntimeError("Pending allocation has no provider client")
                query = ListSandboxesQuery(labels={"fleet_session": self.session_id, "fleet_agent": self.agent_id})
                matches = with_retries(functools.partial(self._list_sandboxes, query))
                if len(matches) != 1:
                    raise RuntimeError("Sandbox allocation outcome is uncertain")
                with self._lock:
                    self._sandbox = sandbox = matches[0]
                    self._creation_pending = False
            if sandbox is not None:
                if client is None:
                    raise RuntimeError("Sandbox cleanup has no provider client")
                self._persist_ownership_resource(
                    "delete" if self.parent is not None or self.disposable else "stop",
                    sandbox.id,
                )
                if self.parent is not None or self.disposable:
                    self._notify("sandbox_deleting", sandbox_id=sandbox.id)
                    try:
                        self._remote_exec(sandbox, CLEAR_WORKSPACE_CODE, timeout=60)
                    except Exception:
                        self._notify("workspace_cleanup_failed", sandbox_id=sandbox.id)
                    # A stopped sandbox cannot run workspace cleanup, but must still be deleted.
                    with_retries(lambda: client.delete(sandbox, timeout=60, wait=True))
                    self._discard_allocation_journal()
                    self._notify("sandbox_deleted", sandbox_id=sandbox.id)
                else:
                    with_retries(lambda: client.stop(sandbox, timeout=60))
                with self._lock:
                    self._sandbox = None
                self._persist_ownership_resource("confirmed", None)
            confirmed = True
        except Exception as exc:
            _LOGGER.warning(
                "Daytona sandbox cleanup unconfirmed for Session %s agent %s (%s)",
                self.session_id,
                self.agent_id,
                type(exc).__name__,
            )
            self._notify("sandbox_cleanup_failed", sandbox_id=getattr(sandbox, "id", None))
        if self.parent is None:
            with self._budget_lock:
                children = tuple(self._children.values())
            for child in children:
                try:
                    if child.shutdown() is not True:
                        confirmed = False
                except Exception as exc:
                    confirmed = False
                    _LOGGER.warning(
                        "Child sandbox shutdown failed for Session %s (%s)",
                        self.session_id,
                        type(exc).__name__,
                    )
        elif confirmed:
            with self.root._budget_lock:
                self.root._children.pop(self.agent_id, None)
        with self._lock:
            self._cleanup_pending = not confirmed
        return confirmed

    def _discard_allocation_journal(self) -> None:
        """Remove this allocation's local journal entry after a confirmed provider deletion.

        The journal is local bookkeeping, so a failure here must not reverse provider
        confirmation, restore a deleted handle, or register as an unconfirmed cleanup. It is
        logged once with a bounded message: the Session, the agent, and the exception type.
        """
        try:
            (Path(".fleet/allocations") / f"{self.agent_id}.json").unlink(missing_ok=True)
        except Exception as exc:
            _LOGGER.warning(
                "Local allocation journal unlink failed for Session %s agent %s (%s)",
                self.session_id,
                self.agent_id,
                type(exc).__name__,
            )
