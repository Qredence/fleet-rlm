"""DaytonaSandboxManager: one Session sandbox (and its child sandboxes), budgets, workspace files, and notes."""

from __future__ import annotations

import base64
import contextvars
import functools
import hashlib
import json
import logging
import re
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
    FileDownloadRequest,
    FileUpload,
    ListSandboxesQuery,
    VolumeMount,
)
from mlflow.entities import SpanType

from fleet_rlm.config import validate_shared_notes_subpath
from fleet_rlm.engine.signatures import STOPPING_RULES
from fleet_rlm.observability import activity_text
from fleet_rlm.sandbox import repl
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError, with_retries
from fleet_rlm.sandbox.snapshots import SNAPSHOT_NAME, SnapshotIdentity, preflight
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

# Remote code fragments; each binds one variable so a Turn can gather them in a single exec.
_DIGESTS_CODE = (
    "import hashlib,json,os\n"
    "digests={}\n"
    "for base,dirs,files in os.walk('/workspace',followlinks=False):\n"
    " dirs[:]=[name for name in dirs if not os.path.islink(os.path.join(base,name))]\n"
    " for name in files:\n"
    "  path=os.path.join(base,name)\n"
    "  if os.path.islink(path) or not os.path.isfile(path): continue\n"
    "  digest=hashlib.sha256()\n"
    "  with open(path,'rb') as handle:\n"
    "   for block in iter(lambda: handle.read(1048576), b''): digest.update(block)\n"
    "  digests[os.path.relpath(path,'/workspace')]=digest.hexdigest()\n"
)
# Probed rather than configured: the Turn snapshot and its package set are repository-owned
# (fleet_rlm.sandbox.snapshots); only optional packages can be absent at runtime.
_ENVIRONMENT_MODULES = (
    ("numpy", "numpy"),
    ("pandas", "pandas"),
    ("scipy", "scipy"),
    ("mpmath", "mpmath"),
    ("sympy", "sympy"),
    ("gmpy2", "gmpy2"),
    ("polars", "polars"),
    ("pyarrow", "pyarrow"),
    ("sklearn", "scikit-learn"),
    ("openpyxl", "openpyxl"),
    ("matplotlib", "matplotlib"),
    ("PIL", "pillow"),
    ("pypdf", "pypdf"),
    ("requests", "requests"),
    ("bs4", "beautifulsoup4"),
    ("pdfplumber", "pdfplumber"),
    ("reportlab", "reportlab"),
    ("docx", "python-docx"),
    ("pptx", "python-pptx"),
    ("markitdown", "markitdown"),
    ("dspy", "dspy"),
    ("gepa", "gepa"),
)
_ENVIRONMENT_CODE = (
    "import importlib.metadata,importlib.util,platform,shutil,sys\n"
    "found=[]\n"
    f"for module,distribution in {_ENVIRONMENT_MODULES!r}:\n"
    " if importlib.util.find_spec(module) is None: continue\n"
    " try: found.append(module+' '+importlib.metadata.version(distribution))\n"
    " except importlib.metadata.PackageNotFoundError: found.append(module)\n"
    "names=[item.split()[0] for item in found]\n"
    f"missing=[module for module,_ in {_ENVIRONMENT_MODULES!r} if module not in names]\n"
    "environment='Sandbox environment: Python '+platform.python_version()+'; installed: '+(', '.join(found) or 'none')"
    "+'; not installed: '+(', '.join(missing) or 'none')"
    "+'; coding tools: '+(', '.join(name for name in "
    "('git','node','npm','pnpm','tsc','uv','pytest','ruff','rg','jq') if shutil.which(name)) or 'none')"
    "+('; call sys.set_int_max_str_digits(0) before converting integers over 4300 digits to str'"
    " if hasattr(sys,'set_int_max_str_digits') else '')+'.'\n"
)


def _notes_code(limit: int) -> str:
    """Bind `notes`: newest-first `{file, summary}` rows from the shared mount, empty when unreadable."""
    return (
        "import json, os\n"
        "notes = []\n"
        "try:\n"
        "    if os.path.ismount('/learned'):\n"
        "        names = [name for name in os.listdir('/learned') if name.endswith('.md')]\n"
        "        names.sort(key=lambda name: os.stat(os.path.join('/learned', name)).st_mtime, reverse=True)\n"
        f"        for name in names[:{limit}]:\n"
        "            with open(os.path.join('/learned', name), encoding='utf-8', errors='replace') as handle:\n"
        "                head = [handle.readline() for _ in range(8)]\n"
        "            lines = [line[len('summary: '):].strip() for line in head if line.startswith('summary: ')]\n"
        "            notes.append({'file': name, 'summary': (lines[0] if lines else '')[:120]})\n"
        "except OSError:\n"
        "    notes = None\n"
    )


def _valid_notes(rows: Any) -> list[dict[str, str]]:
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


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
class DaytonaSandboxManager:
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

    @property
    def turn_generation(self) -> int:
        """The current Turn's generation; every watchdog callback is checked against it."""
        return self.root._turn_generation

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
        with mlflow.start_span(name=name, span_type=SpanType.TOOL) as span:
            span.set_attributes(
                {"agent_id": self.agent_id, "parent_agent_id": self.parent.agent_id if self.parent else None}
            )
            span.set_inputs({"operation": name})
            completed = False
            try:
                yield span
                completed = True
            finally:
                span.set_attribute("sandbox_id", getattr(self._sandbox, "id", None))
                span.set_outputs({"completed": completed})

    def _allocate_child(self, *, input_paths: list[str] | None = None) -> DaytonaSandboxManager:
        """Reserve and provision a child sandbox, copying the requested or default inputs into it.

        Capacity limits refuse the spawn before any sandbox is created. Skills are copied but not receipted as inputs.
        """
        root = self.root
        self.check_turn()
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

    def collect_child_artifacts(self) -> list[dict[str, Any]]:
        """Promote this child's new artifacts into the parent's artifacts/<agent-id>/ tree.

        Copied-in artifacts are skipped, and promoted paths are recorded relative to the root.
        """
        if self.parent is None:
            return []
        self.check_turn()
        results = []
        total = 0
        with self.root._budget_lock:
            inputs = dict(self.root._child_receipts.get(self.agent_id, {}).get("inputs", {}))
        for path in self.list_workspace_files():
            if not path.startswith("artifacts/"):
                continue
            raw = self.read_workspace_file(path)
            if inputs.get(path) == hashlib.sha256(raw).hexdigest():
                continue  # an unchanged copied-in artifact is the parent's, not this child's output
            total += len(raw)
            if total > 25_000_000 or len(results) >= 100:
                raise ValueError("Child output promotion quota exceeded")
            target = f"artifacts/{self.agent_id}/{path.removeprefix('artifacts/')}"
            self.parent.write_workspace_file(target, raw)
            results.append({"path": target, "sha256": hashlib.sha256(raw).hexdigest(), "agent_id": self.agent_id})
        self.emit("agent_result", artifacts=results, status="succeeded")
        root_receipts = []
        for row in results:
            root_path = row["path"]
            ancestor = self.parent
            while ancestor is not None and ancestor.parent is not None:
                root_path = f"artifacts/{ancestor.agent_id}/{root_path.removeprefix('artifacts/')}"
                ancestor = ancestor.parent
            root_receipts.append({**row, "root_path": root_path})
        with self.root._budget_lock:
            self.root._child_receipts[self.agent_id]["artifacts"] = root_receipts
        self.record_child_outcome(True)
        return results

    def execution_evidence(self) -> dict[str, Any]:
        """Observe actual workspace bytes before teardown, never model-provided receipts."""
        from fleet_rlm.optimize.evidence import collect_artifacts

        artifacts = collect_artifacts(self) if self._sandbox is not None else {"complete": True, "files": {}}
        with self.root._budget_lock:
            children = json.loads(json.dumps(list(self.root._child_receipts.values())))
        return {
            "schema_version": 1,
            "artifacts": artifacts,
            "children": children,
            "children_complete": all(child["status"] != "running" for child in children),
        }

    def verify_python_artifact(self, path: str, timeout_seconds: int) -> dict[str, Any]:
        """Execute one declared entry point in Daytona, bounded by the remaining Turn deadline.

        `timeout_seconds` must be an integer from 1 through 60. Return the file's SHA-256,
        process exit code, up to 65,536 stdout bytes decoded as UTF-8, and `complete`.
        A process timeout or oversized stdout sets `complete` to False; a nonzero exit
        code alone does not.

        Raises:
            ValueError: The artifact path, size, or verification timeout is invalid.
            TimeoutError: The Turn deadline has expired or too little time remains.
            RuntimeError: The Turn was cancelled.
            ExecutionOutcomeUnknownError: Execution or result decoding failed; cancellation
                is requested and the verification is not replayed.

        Errors while reading the file before verification also propagate.
        """
        from fleet_rlm.optimize.evidence import MAX_ARTIFACT_BYTES, artifact_path

        artifact_path(path)
        if not path.endswith(".py") or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
            raise ValueError("Invalid Python verification entry")
        self.check_turn()
        remaining = self.remaining_turn_seconds()
        if remaining is None:
            timeout = timeout_seconds
        else:
            if remaining <= 1:
                raise TimeoutError("No Turn time remains for artifact verification")
            timeout = min(timeout_seconds, max(1, int(remaining) - 1))
        raw = self.read_workspace_file(path, max_bytes=MAX_ARTIFACT_BYTES)
        digest = hashlib.sha256(raw).hexdigest()
        # Only the child process executes generated code. Temporary files bound host output;
        # the process group is killed on timeout so background children cannot survive it.
        code = (
            "import hashlib,json,os,signal,subprocess,sys,tempfile\n"
            f"path={json.dumps('/workspace/' + path)}\n"
            f"assert hashlib.sha256(open(path,'rb').read()).hexdigest()=={digest!r}\n"
            "with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:\n"
            " p=subprocess.Popen([sys.executable,'-I',path],cwd='/workspace',stdout=out,stderr=err,"
            "start_new_session=True)\n"
            " complete=True\n"
            f" try: p.wait(timeout={timeout})\n"
            " except subprocess.TimeoutExpired: complete=False\n"
            " finally:\n"
            "  try: os.killpg(p.pid,signal.SIGKILL)\n"
            "  except ProcessLookupError: pass\n"
            "  p.wait()\n"
            " out.seek(0); data=out.read(65537)\n"
            " print(json.dumps({'complete':complete and len(data)<=65536,'exit_code':p.returncode,"
            "'stdout':data[:65536].decode('utf-8',errors='replace')}))\n"
        )
        try:
            result = json.loads(self._remote_exec(self.ensure_sandbox(), code, timeout=timeout + 1, retry=False))
        except Exception as exc:
            # The process transport cannot establish whether execution finished. Never replay it.
            self.request_cancellation()
            raise ExecutionOutcomeUnknownError("Python artifact verification outcome unknown") from exc
        self.check_turn()
        return {**result, "sha256": digest}

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
                provider_failed = False
                for child in tuple(root._children.values()):
                    try:
                        confirmed = child.shutdown()
                    except Exception as exc:
                        # One failed child must not leave the remaining children or the root running.
                        provider_failed = True
                        _LOGGER.warning(
                            "Child sandbox shutdown failed for Session %s (%s)",
                            root.session_id,
                            type(exc).__name__,
                        )
                        continue
                    if confirmed is False:
                        provider_failed = True
                try:
                    # Stopping the root interrupts remote Python, including a running infinite loop.
                    with root._lock:
                        if root._sandbox is not None and root._client is not None:
                            client, sandbox = root._client, root._sandbox
                            with_retries(lambda: client.stop(sandbox, timeout=60))
                except Exception:
                    provider_failed = True
                finally:
                    # The root is stopped, or its stop is unconfirmed and therefore lost. Either way
                    # the cached handle is not reusable: the next Turn must cold-resume into a fresh
                    # sandbox instead of running against this one, and no later teardown may stop it
                    # again. Interpreter contexts belong to the discarded handle, so they go too.
                    with root._lock:
                        root._sandbox = None
                        root._contexts.clear()
                root._cleanup_outcome = CLEANUP_UNCONFIRMED if provider_failed else CLEANUP_STOPPED
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
        """Trace provisioning/resume, including the provider state check on a reused sandbox."""
        with self._lifecycle_span("sandbox.provision") as span:
            span.set_attribute("action", "reuse" if self._sandbox is not None else "create")
            return self._ensure_sandbox()

    def _ensure_sandbox(self) -> Any:
        """Create or resume this agent's sandbox, returning the live handle.

        A pending create is adopted only when exactly one matching sandbox exists, so creates never duplicate.
        """
        self.check_turn()
        with self._lock:
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
                self._creation_pending = True
                self._sandbox = self._client.create(params)
                self._creation_pending = False
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

    def _remote_exec(self, sandbox: Any, code: str, *, timeout: int | None = None, retry: bool = True) -> str:
        encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
        command = (
            "python3 -c 'import base64; exec(compile(base64.b64decode(\"" + encoded + '"), "<fleet-remote>", "exec"))\''
        )
        if retry:
            result = with_retries(lambda: sandbox.process.exec(command, timeout=timeout))
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

    def write_learned_note(self, turn_id: str, note: str, trace_id: str | None = None) -> str | None:
        """Write one Turn's note as an unreviewed file on the shared mount; None when skipped."""
        if not self.shared_notes_subpath or not note.strip():
            return None
        if self.exec_remote_python("import os\nprint(os.path.ismount('/learned'))").strip() != "True":
            return None  # Sandbox predates the mount; the note still reaches the Session memory summary.
        turn = re.sub(r"[^A-Za-z0-9]", "", turn_id)[:8] or "turn"
        path = f"/learned/{self.session_id}-{turn}.md"
        body = activity_text(note, limit=10_000)
        summary = next((line.strip() for line in body.splitlines() if line.strip()), "")[:120]
        header = [
            "---",
            f"session: {self.session_id}",
            f"turn: {turn}",
            f"trace_id: {trace_id or ''}",
            "status: unreviewed",
            f"summary: {summary}",
            "---",
        ]
        sandbox = self.ensure_sandbox()
        with_retries(lambda: sandbox.fs.upload_file("\n".join([*header, body, ""]).encode("utf-8"), path, timeout=60))
        return path

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

    def turn_inputs(self, notes_limit: int = 20) -> dict[str, Any]:
        """Gather a Turn's remote inputs in one exec: file digests, shared notes, and the environment.

        Digests follow `list_workspace_files` (symbolic links skipped, never traversed) and are required;
        notes are optional hints, so an unreadable mount yields none instead of failing the Turn.
        `notes_limit` enables shared notes only from 1 through 50; other values omit notes.
        Missing optional modules may emit one advisory event per Session.

        Raises:
            RuntimeError: The workspace digest listing is malformed.

        Remote execution and JSON decoding errors propagate.
        """
        with_notes = bool(self.shared_notes_subpath) and 1 <= notes_limit <= 50
        code = (
            _DIGESTS_CODE
            + _ENVIRONMENT_CODE
            + (_notes_code(notes_limit) if with_notes else "notes = []\n")
            + "print(json.dumps({'digests': digests, 'notes': notes, "
            "'environment': environment, 'missing': missing}, sort_keys=True))"
        )
        result = json.loads(self.exec_remote_python(code) or "{}")
        digests = result.get("digests") if isinstance(result, dict) else None
        if not isinstance(digests, dict) or not all(
            isinstance(path, str) and isinstance(digest, str) for path, digest in digests.items()
        ):
            raise RuntimeError("Invalid workspace digest listing")
        if result.get("notes") is None:
            _LOGGER.warning("Shared learned notes were unavailable for Session %s", self.session_id)
        self._report_incomplete_environment(result.get("missing"))
        environment = result.get("environment")
        return {
            "digests": digests,
            "notes": _valid_notes(result.get("notes")),
            "environment": environment if isinstance(environment, str) else "",
        }

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

    def list_workspace_files(self) -> list[str]:
        """List regular files under the Session workspace as sorted relative paths.

        Symbolic links are skipped and never traversed.
        """
        code = (
            "import json,os\n"
            "root='/workspace'\n"
            "items=[]\n"
            "for base,dirs,files in os.walk(root,followlinks=False):\n"
            " dirs[:]=[name for name in dirs if not os.path.islink(os.path.join(base,name))]\n"
            " for name in files:\n"
            "  path=os.path.join(base,name)\n"
            "  if not os.path.islink(path) and os.path.isfile(path): items.append(os.path.relpath(path,root))\n"
            "print(json.dumps(sorted(items)))"
        )
        return json.loads(self.exec_remote_python(code) or "[]")

    def write_workspace_file(self, rel_path: str, content: bytes) -> str:
        """Write one validated file beneath this Session's mounted Volume."""
        clean_path = _workspace_relative_path(rel_path)
        if not isinstance(content, bytes):
            raise TypeError("workspace content must be bytes")
        if len(content) > 25_000_000:
            raise ValueError("Workspace file exceeds the 25 MB limit")
        sandbox = self.ensure_sandbox()
        self._workspace_file_metadata(sandbox, clean_path, create_parents=True)
        with_retries(lambda: sandbox.fs.upload_file(content, f"/workspace/{clean_path}", timeout=1800))
        return f"/workspace/{clean_path}"

    def write_workspace_file_from_path(self, rel_path: str, local_path: str | Path) -> str:
        """Upload a local file through Daytona without loading it into memory."""
        clean_path = _workspace_relative_path(rel_path)
        source = Path(local_path)
        size = source.stat().st_size
        if size > 25_000_000:
            raise ValueError("Workspace file exceeds the 25 MB limit")
        sandbox = self.ensure_sandbox()
        self._workspace_file_metadata(sandbox, clean_path, create_parents=True)
        with_retries(lambda: sandbox.fs.upload_file(str(source), f"/workspace/{clean_path}", timeout=1800))
        return f"/workspace/{clean_path}"

    def read_workspace_file(self, rel_path: str, *, max_bytes: int = 25_000_000) -> bytes:
        """Read one workspace file, refusing anything over max_bytes.

        Metadata is checked remotely first, and a size change during download raises.
        """
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 0 <= max_bytes <= 25_000_000:
            raise ValueError("max_bytes must be an integer between 0 and 25000000")
        clean_path = _workspace_relative_path(rel_path)
        sandbox = self.ensure_sandbox()
        metadata = self._workspace_file_metadata(sandbox, clean_path, create_parents=False, max_bytes=max_bytes)
        # The SDK's download_file is positional-only at runtime (an *args overload); keep the timeout positional.
        content = with_retries(lambda: sandbox.fs.download_file(f"/workspace/{clean_path}", 1800))
        if not isinstance(content, bytes) or len(content) != metadata["size"]:
            raise RuntimeError("Workspace file changed or could not be downloaded")
        return content

    def stat_workspace_file(self, rel_path: str) -> dict[str, Any]:
        """Return size and SHA-256 without downloading content, with the same 25 MB and path checks."""
        clean_path = _workspace_relative_path(rel_path)
        sandbox = self.ensure_sandbox()
        metadata = self._workspace_files_metadata(
            sandbox, [clean_path], create_parents=False, max_bytes=25_000_000, include_digest=True
        )[0]
        return {"path": clean_path, "size_bytes": metadata["size"], "sha256": metadata["sha256"]}

    def read_workspace_file_to_path(self, rel_path: str, local_path: str | Path, *, max_bytes: int = 25_000_000) -> int:
        """Download a workspace file to disk through Daytona's path overload."""
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 0 <= max_bytes <= 25_000_000:
            raise ValueError("max_bytes must be an integer between 0 and 25000000")
        clean_path = _workspace_relative_path(rel_path)
        sandbox = self.ensure_sandbox()
        metadata = self._workspace_file_metadata(sandbox, clean_path, create_parents=False, max_bytes=max_bytes)
        destination = Path(local_path)
        with_retries(lambda: sandbox.fs.download_file(f"/workspace/{clean_path}", str(destination), 1800))
        if not destination.is_file() or destination.stat().st_size != metadata["size"]:
            raise RuntimeError("Workspace file changed or could not be downloaded")
        return metadata["size"]

    def _workspace_file_metadata(
        self,
        sandbox: Any,
        rel_path: str,
        *,
        create_parents: bool,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Check a Volume path remotely before using Daytona's file-transfer API."""
        return self._workspace_files_metadata(sandbox, [rel_path], create_parents=create_parents, max_bytes=max_bytes)[
            0
        ]

    def _workspace_files_metadata(
        self,
        sandbox: Any,
        rel_paths: list[str],
        *,
        create_parents: bool,
        max_bytes: int | None = None,
        include_digest: bool = False,
    ) -> list[dict[str, Any]]:
        """Check Volume paths remotely in one call, in order, before using Daytona's file-transfer API."""
        paths_b64 = base64.b64encode(json.dumps(rel_paths).encode("utf-8")).decode("ascii")
        create_clause = " os.makedirs(parent,exist_ok=True)\n" if create_parents else ""
        read_clause = " if not os.path.isfile(target): raise FileNotFoundError('workspace file is unavailable')\n"
        if create_parents:
            read_clause = (
                " if os.path.exists(target) and not os.path.isfile(target): "
                "raise ValueError('workspace target is not a file')\n"
            )
        size_clause = " size=os.path.getsize(target) if os.path.isfile(target) else None\n"
        if max_bytes is not None:
            size_clause += f" if size>{max_bytes}: raise ValueError('workspace file exceeds size limit')\n"
        result_clause = " return {'size':size}\n"
        if include_digest:
            result_clause = (
                " digest=hashlib.sha256()\n"
                " count=0\n"
                " with open(target,'rb') as handle:\n"
                "  before=os.fstat(handle.fileno())\n"
                "  for block in iter(lambda: handle.read(1048576),b''):\n"
                "   count+=len(block)\n"
                "   if count>size: raise ValueError('workspace file changed while inspected')\n"
                "   digest.update(block)\n"
                "  after=os.fstat(handle.fileno())\n"
                " if count!=size or before.st_size!=size or "
                "(before.st_mtime_ns,before.st_ctime_ns)!=(after.st_mtime_ns,after.st_ctime_ns):\n"
                "  raise ValueError('workspace file changed while inspected')\n"
                " return {'size':size,'sha256':digest.hexdigest()}\n"
            )
        code = (
            "import base64,hashlib,json,os\n"
            "root=os.path.realpath('/workspace')\n"
            "def check(relative):\n"
            " parts=relative.split('/')\n"
            " parent=root\n"
            " for part in parts[:-1]:\n"
            "  parent=os.path.join(parent,part)\n"
            "  if os.path.islink(parent): raise ValueError('workspace path may not traverse symbolic links')\n"
            "  if os.path.exists(parent) and not os.path.isdir(parent): "
            "raise ValueError('workspace parent is not a directory')\n"
            " if os.path.commonpath((root,os.path.realpath(parent))) != root: "
            "raise ValueError('invalid workspace path')\n" + create_clause + " target=os.path.join(parent,parts[-1])\n"
            " if os.path.islink(target): raise ValueError('workspace target may not be a symbolic link')\n"
            " if os.path.commonpath((root,parent)) != root: raise ValueError('invalid workspace path')\n"
            + read_clause
            + size_clause
            + result_clause
            + "relatives=json.loads(base64.b64decode('"
            + paths_b64
            + "').decode('utf-8'))\n"
            "print(json.dumps([check(relative) for relative in relatives]))"
        )
        metadata = json.loads(self._remote_exec(sandbox, code))
        if (
            not isinstance(metadata, list)
            or len(metadata) != len(rel_paths)
            or not all(
                isinstance(item, dict)
                and (item.get("size") is None or type(item["size"]) is int)
                and (
                    not include_digest
                    or (
                        type(item.get("size")) is int
                        and item["size"] >= 0
                        and isinstance(item.get("sha256"), str)
                        and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is not None
                    )
                )
                for item in metadata
            )
        ):
            raise RuntimeError("Invalid workspace file metadata")
        return metadata

    def read_workspace_files(self, rel_paths: list[str], *, max_total_bytes: int = 100_000_000) -> dict[str, bytes]:
        """Read several workspace files with one remote check and one batched download.

        Every path gets the same symlink, containment, and size checks as `read_workspace_file`.
        """
        clean_paths = [_workspace_relative_path(path) for path in rel_paths]
        if not clean_paths:
            return {}
        sandbox = self.ensure_sandbox()
        metadata = self._workspace_files_metadata(sandbox, clean_paths, create_parents=False, max_bytes=25_000_000)
        if sum(item["size"] for item in metadata) > max_total_bytes:
            raise ValueError("Workspace batch exceeds the transfer quota")
        responses = with_retries(
            lambda: sandbox.fs.download_files(
                [FileDownloadRequest(source=f"/workspace/{path}") for path in clean_paths], 1800
            )
        )
        by_source = {response.source: response for response in responses}
        contents: dict[str, bytes] = {}
        for path, item in zip(clean_paths, metadata, strict=True):
            response = by_source.get(f"/workspace/{path}")
            content = getattr(response, "result", None)
            if response is None or response.error or not isinstance(content, bytes) or len(content) != item["size"]:
                raise RuntimeError(f"Workspace file changed or could not be downloaded: {path}")
            contents[path] = content
        return contents

    def write_workspace_files(self, files: dict[str, bytes]) -> None:
        """Write several validated files beneath this Session's Volume with one check and one upload."""
        clean = {_workspace_relative_path(path): content for path, content in files.items()}
        if not clean:
            return
        if any(not isinstance(content, bytes) or len(content) > 25_000_000 for content in clean.values()):
            raise ValueError("Workspace files must be bytes of at most 25 MB")
        sandbox = self.ensure_sandbox()
        self._workspace_files_metadata(sandbox, list(clean), create_parents=True)
        uploads = [FileUpload(source=content, destination=f"/workspace/{path}") for path, content in clean.items()]
        with_retries(lambda: sandbox.fs.upload_files(uploads, timeout=1800))

    def interpreter_factory(
        self,
        tools: Mapping[str, Callable[..., Any]] | None = None,
        *,
        input_paths: list[str] | None = None,
    ) -> Callable[[], DaytonaInterpreter]:
        """Return the zero-argument fresh interpreter factory required by DSPy 3.4."""
        captured_input_paths = tuple(input_paths) if input_paths is not None else None

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

    def delete_workspace_file(self, rel_path: str) -> None:
        """Delete one validated workspace file, refusing symbolic links."""
        clean = _workspace_relative_path(rel_path)
        sandbox = self.ensure_sandbox()
        # The remote check rejects symbolic links; Daytona's file API does not report them.
        self._workspace_file_metadata(sandbox, clean, create_parents=False)
        with_retries(lambda: sandbox.fs.delete_file(f"/workspace/{clean}"))

    def _register_interpreter(self, interpreter: DaytonaInterpreter) -> None:
        with self._lock:
            self._interpreters[interpreter.ns_id] = interpreter

    def _unregister_interpreter(self, interpreter: DaytonaInterpreter) -> None:
        with self._lock:
            self._interpreters.pop(interpreter.ns_id, None)

    def shutdown(self) -> bool:
        """Stop or delete this agent's sandbox after shutting down any children.

        A child's workspace is cleared before deletion; a root sandbox is stopped unless disposable.

        Every call for the same manager is serialized on this agent's shutdown lock, so a normal
        child completion and ``cancel_turn()`` cannot race. A caller that arrives while a provider
        cleanup is in flight waits for it and then observes the completed result; no caller can
        report this agent confirmed before its provider call returned.

        Returns whether this agent's own provider cleanup is confirmed. ``True`` means the provider
        stop or delete returned (or there was nothing to clean up); ``False`` means the provider
        call failed. Only a child retains its handle for a later retry; a root discards it.
        Lifecycle notifications are delivered best-effort, so a refused event never changes this
        result and never skips the provider call. A local allocation-journal unlink failure is bookkeeping:
        it is logged once and never reverses confirmation or restores an already-deleted handle.
        """
        with self._shutdown_lock:
            return self._shutdown_locked()

    def _shutdown_locked(self) -> bool:
        """Run the provider cleanup while holding this agent's shutdown lock."""
        if self.parent is not None:
            with self.root._budget_lock:
                receipt = self.root._child_receipts.get(self.agent_id)
                if receipt is not None and receipt["status"] == "running":
                    receipt["status"] = "failed"
        if self.parent is None:
            self.end_turn()
            for child in tuple(self._children.values()):
                child.shutdown()
        with self._lock:
            sandbox, client = self._sandbox, self._client
            interpreters = tuple(self._interpreters.values())
            self._sandbox = None
            self._contexts.clear()
            self._interpreters.clear()
            for interpreter in interpreters:
                interpreter._closed = True
        confirmed = True
        if sandbox is not None and client is not None:
            try:
                if self.parent is not None or self.disposable:
                    self._notify("sandbox_deleting", sandbox_id=sandbox.id)
                    try:
                        self._remote_exec(
                            sandbox,
                            "import os,shutil\n"
                            "for name in os.listdir('/workspace'):\n"
                            " p=os.path.join('/workspace',name)\n"
                            " try:\n"
                            "  if os.path.islink(p) or not os.path.isdir(p): os.unlink(p)\n"
                            "  else: shutil.rmtree(p)\n"
                            " except FileNotFoundError: pass\n",
                        )
                    except Exception:
                        self._notify("workspace_cleanup_failed", sandbox_id=sandbox.id)
                    # A stopped or failed sandbox cannot run workspace cleanup,
                    # but must still be deleted and confirmed by the provider.
                    with_retries(lambda: client.delete(sandbox, timeout=60, wait=True))
                    self._discard_allocation_journal()
                    with self.root._budget_lock:
                        self.root._children.pop(self.agent_id, None)
                    self._notify("sandbox_deleted", sandbox_id=sandbox.id)
                else:
                    with_retries(lambda: client.stop(sandbox, timeout=60))
            except Exception:
                confirmed = False
                _LOGGER.warning("Daytona Session sandbox stop failed")
                if self.parent is not None:
                    # Retain the handle so a later shutdown can retry deletion.
                    self._sandbox = sandbox
                    self._notify("sandbox_cleanup_failed", sandbox_id=sandbox.id)
        elif self.parent is not None:
            with self.root._budget_lock:
                self.root._children.pop(self.agent_id, None)
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
