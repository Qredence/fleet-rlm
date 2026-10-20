"""DaytonaSandboxManager: one Session sandbox (and its child sandboxes), budgets, workspace files, and notes."""

from __future__ import annotations

import base64
import contextvars
import functools
import hashlib
import json
import logging
import math
import re
import threading
import time
import uuid
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
from daytona.common.errors import (
    DaytonaAuthenticationError,
    DaytonaBadRequestError,
    DaytonaConflictError,
    DaytonaConnectionError,
    DaytonaError,
    DaytonaForbiddenError,
    DaytonaNotFoundError,
    DaytonaRateLimitError,
    DaytonaTimeoutError,
    DaytonaUnprocessableEntityError,
)
from mlflow.entities import SpanType

from fleet_rlm.config import (
    validate_domain_allow_list,
    validate_root_auto_delete_minutes,
    validate_shared_notes_subpath,
)
from fleet_rlm.engine.signatures import STOPPING_RULES
from fleet_rlm.observability import activity_text
from fleet_rlm.sandbox import repl
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError, with_retries
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
# Probed rather than configured: the snapshot is user-selectable (FLEET_DAYTONA_SNAPSHOT).
_ENVIRONMENT_MODULES = ("numpy", "pandas", "scipy", "mpmath", "sympy", "gmpy2", "polars", "pyarrow", "sklearn")
_ENVIRONMENT_CODE = (
    "import importlib.metadata,importlib.util,platform,sys\n"
    "found=[]\n"
    f"for module in {_ENVIRONMENT_MODULES!r}:\n"
    " if importlib.util.find_spec(module) is None: continue\n"
    " try: found.append(module+' '+importlib.metadata.version('scikit-learn' if module=='sklearn' else module))\n"
    " except importlib.metadata.PackageNotFoundError: found.append(module)\n"
    "names=[item.split()[0] for item in found]\n"
    f"missing=[module for module in {_ENVIRONMENT_MODULES!r} if module not in names]\n"
    "environment='Sandbox environment: Python '+platform.python_version()+'; installed: '+(', '.join(found) or 'none')"
    "+'; not installed: '+(', '.join(missing) or 'none')"
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
# States in which a running execution can no longer be alive; "missing" means the provider has no such sandbox.
_LOST_STATES = _FAILED_STATES | _RESUMABLE_STATES | frozenset({"stopping", "archiving", "pausing", "missing"})
_STATE_POLL_SECONDS = 1.0
_STATE_SETTLE_SECONDS = 90.0

_DAYTONA_CLIENTS: dict[tuple[str, str], Daytona] = {}
_DAYTONA_CLIENTS_LOCK = threading.Lock()


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


def _sandbox_alive(sandbox: Any) -> bool:
    """Whether an execution in `sandbox` can still be running; an unreadable state counts as alive."""
    try:
        return _sandbox_state(sandbox) not in _LOST_STATES
    except DaytonaNotFoundError:
        return False
    except Exception:
        return True  # a failed poll is not evidence of loss; the execution timeout still bounds the wait


# Rejections that prove the provider created nothing: a retry is a fresh create, never a duplicate.
_DEFINITE_CREATE_FAILURES = (
    DaytonaBadRequestError,
    DaytonaAuthenticationError,
    DaytonaForbiddenError,
    DaytonaNotFoundError,
    DaytonaConflictError,
    DaytonaUnprocessableEntityError,
    DaytonaRateLimitError,
)


def _create_definitely_failed(exc: BaseException) -> bool:
    """True for client-side validation and 4xx rejections; timeouts, transport and 5xx errors stay uncertain."""
    if isinstance(exc, (DaytonaTimeoutError, DaytonaConnectionError)):
        return False
    if isinstance(exc, _DEFINITE_CREATE_FAILURES):
        return True
    status = exc.status_code if isinstance(exc, DaytonaError) else None
    return isinstance(status, int) and 400 <= status < 500 and status != 408


_NETWORK_POLICY_WARNED = False
_NETWORK_POLICY_WARNED_LOCK = threading.Lock()
# Root sandbox ids whose egress block has already been probed in this process.
_NETWORK_PROBED_SANDBOXES: set[str] = set()
_NETWORK_PROBED_LOCK = threading.Lock()
NETWORK_PROBE_TIMEOUT_SECONDS = 10
_NETWORK_PROBE_CODE = (
    "import socket\n"
    "try:\n"
    " socket.create_connection(('1.1.1.1', 443), timeout=2).close()\n"
    " print('connected')\n"
    "except Exception as exc:\n"
    " print('blocked ' + type(exc).__name__)\n"
)


def _domains(value: Any) -> frozenset[str]:
    return frozenset(item.strip().lower() for item in str(value or "").split(",") if item.strip())


def _reported_network_policy(sandbox: Any) -> tuple[Any, Any]:
    return getattr(sandbox, "network_block_all", None), getattr(sandbox, "domain_allow_list", None)


def _warn_network_policy_once(message: str, *args: Any) -> None:
    """Log the network-policy WARNING at most once per process; later sandboxes are not re-reported."""
    global _NETWORK_POLICY_WARNED
    with _NETWORK_POLICY_WARNED_LOCK:
        if _NETWORK_POLICY_WARNED:
            return
        _NETWORK_POLICY_WARNED = True
    _LOGGER.warning(message, *args)


class SandboxUnavailableError(RuntimeError):
    """The provider reports the sandbox failed, destroyed, or missing; it cannot be resumed."""

    def __init__(self, sandbox_id: str, state: str) -> None:
        super().__init__(f"Sandbox {sandbox_id} is in state {state!r} and cannot be resumed")
        self.state = state


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
    snapshot: str = "fleet-rlm-base"
    auto_stop_minutes: int = 30
    root_auto_delete_minutes: int = 7 * 24 * 60
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
    # Outbound network: blocked by default. A non-empty allow list replaces block-all (Daytona rejects both).
    network_block_all: bool = True
    domain_allow_list: str = ""
    event_callback: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False)
    parent: DaytonaSandboxManager | None = field(default=None, repr=False)
    agent_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    _children: dict[str, DaytonaSandboxManager] = field(default_factory=dict, init=False, repr=False)
    _allocations: int = field(default=0, init=False, repr=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _deadline: float = field(default=0, init=False, repr=False)
    _watchdog: threading.Timer | None = field(default=None, init=False, repr=False)
    _sandbox: Any = field(default=None, init=False, repr=False)
    _client: Daytona | None = field(default=None, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _activity: repl.ExecutionTracker = field(default_factory=repl.ExecutionTracker, init=False, repr=False)
    _network_probe_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _budget_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _llm_calls_used: int = field(default=0, init=False, repr=False)
    _sandbox_errors: int = field(default=0, init=False, repr=False)
    _submit_rejections: int = field(default=0, init=False, repr=False)
    _budget_breakdown: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(("generation_query", "routing", "skill_selection", "other_decisions"), 0),
        init=False,
        repr=False,
    )
    _child_receipts: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _interpreters: dict[str, DaytonaInterpreter] = field(default_factory=dict, init=False, repr=False)
    # One Daytona interpreter context per Fleet namespace; invalid once the sandbox stops.
    _contexts: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _creation_pending: bool = field(default=False, init=False, repr=False)
    # cancel_turn stops the root outside `_lock`; new remote work waits until no such stop is in flight.
    _stops_in_flight: int = field(default=0, init=False, repr=False)
    _stops_settled: threading.Event = field(default_factory=threading.Event, init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate identity, budgets, and the shared-notes subpath before any sandbox work."""
        validate_session_id(self.session_id)
        if not self.api_key or not self.org_id or not self.volume_id:
            raise ValueError("Daytona API key, organization ID, and Volume ID are required")
        if self.max_llm_calls < 1:
            raise ValueError("max_llm_calls must be positive")
        validate_shared_notes_subpath(self.shared_notes_subpath)
        validate_root_auto_delete_minutes(self.root_auto_delete_minutes)
        self.domain_allow_list = validate_domain_allow_list(self.domain_allow_list)
        self._stops_settled.set()

    @property
    def root(self) -> DaytonaSandboxManager:
        """Return the top-level manager that owns the Turn budget and deadline."""
        return self.parent.root if self.parent is not None else self

    @property
    def depth(self) -> int:
        """Return how many parents separate this agent from the root."""
        return self.parent.depth + 1 if self.parent is not None else 0

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

    def check_turn(self) -> None:
        """Raise if the Turn was cancelled or its deadline passed, marking it cancelled on timeout."""
        root = self.root
        if root._cancelled.is_set():
            raise RuntimeError("Turn cancelled")
        if root._deadline and time.monotonic() >= root._deadline:
            root._cancelled.set()
            raise TimeoutError("Turn deadline exceeded")

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
                network_block_all=root.network_block_all,
                domain_allow_list=root.domain_allow_list,
            )
            root._children[child.agent_id] = child
            root._allocations += 1
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
        """Execute one declared entry point in Daytona, bounded by the remaining Turn deadline."""
        from fleet_rlm.optimize.evidence import MAX_ARTIFACT_BYTES, artifact_path

        artifact_path(path)
        if not path.endswith(".py") or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
            raise ValueError("Invalid Python verification entry")
        self.check_turn()
        remaining = self.root._deadline - time.monotonic() if self.root._deadline else 0
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
            self.cancel_turn()
            raise ExecutionOutcomeUnknownError("Python artifact verification outcome unknown") from exc
        self.check_turn()
        return {**result, "sha256": digest}

    def cancel_turn(self) -> None:
        """Cancel the Turn, stop child sandboxes, and stop the root sandbox to interrupt remote Python."""
        root = self.root
        root._cancelled.set()
        root.emit("cancellation_requested")
        for child in tuple(root._children.values()):
            child.shutdown()
        # Stopping the root interrupts remote Python, including a running infinite loop. The stop can take
        # up to a minute per attempt, so it runs outside the lock: callers waiting on the manager (a
        # reconnecting stream, a new Turn) see the cancellation instead of queueing behind the provider.
        with root._lock:
            client, sandbox = root._client, root._sandbox
            if sandbox is None or client is None:
                return
            root._contexts.clear()  # contexts die with the stop
            with root._budget_lock:
                root._stops_in_flight += 1
                root._stops_settled.clear()
        try:
            with_retries(lambda: client.stop(sandbox, timeout=60))
            root._activity.observe_alive(False)  # stopped on purpose; the next ensure_sandbox resumes it
        finally:
            with root._budget_lock:
                root._stops_in_flight -= 1
                if root._stops_in_flight == 0:
                    root._stops_settled.set()

    def _allocation_path(self) -> Path:
        return Path(".fleet/allocations") / f"{self.agent_id}.json"

    def _record_allocation(self, status: str) -> None:
        path = self._allocation_path()
        path.parent.mkdir(parents=True, exist_ok=True)
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

    def network_params(self) -> dict[str, Any]:
        """Daytona firewall create parameters: block-all, an allow list, or (both off) the tier default.

        Daytona treats network_block_all, domain_allow_list, and network_allow_list as mutually exclusive
        (a conflicting create is a 400), so at most one is sent. The REPL bridge (run_code and file
        transfer) reaches the sandbox through Daytona's toolbox, not sandbox egress, and keeps working.
        """
        if self.domain_allow_list:
            return {"domain_allow_list": self.domain_allow_list}
        return {"network_block_all": True} if self.network_block_all else {}

    def _claim_network_probe(self, sandbox: Any) -> bool:
        """Mark a newly created root sandbox as probed; True when its egress probe should now be started.

        Only a root, only for block-all (an allow list holds domains, so an IP probe proves nothing), and at most
        once per sandbox id per process. A rebuilt root is a new id and is claimed again.
        """
        if self.parent is not None or self.network_params() != {"network_block_all": True}:
            return False
        sandbox_id = str(getattr(sandbox, "id", "") or "")
        with _NETWORK_PROBED_LOCK:
            if not sandbox_id or sandbox_id in _NETWORK_PROBED_SANDBOXES:
                return False
            _NETWORK_PROBED_SANDBOXES.add(sandbox_id)
        return True

    def _start_network_probe(self, sandbox: Any) -> None:
        """Run the claimed egress probe on a daemon thread so neither the create nor the first Turn waits on it.

        Never raises: a thread that cannot start is logged at debug. The thread copies the caller's contextvars
        and never takes the manager lock.
        """
        sandbox_id = str(getattr(sandbox, "id", "") or "")
        client = self._client
        try:
            thread = threading.Thread(
                target=contextvars.copy_context().run,
                args=(self._run_network_probe, client, sandbox_id),
                name=f"fleet-network-probe-{sandbox_id}",
                daemon=True,
            )
            self._network_probe_thread = thread
            thread.start()
        except Exception as exc:
            _LOGGER.debug("Network enforcement probe for sandbox %s did not start (%s)", sandbox_id, type(exc).__name__)

    def _run_network_probe(self, client: Any, sandbox_id: str) -> None:
        """Check from inside the sandbox that block-all is really enforced; log the result, never raise.

        The probe runs in a separate `process.exec` on a freshly fetched handle, never in the REPL/run_code
        session and never mutating the manager's cached handle, so it cannot interfere with the first Turn's
        code. A connection to 1.1.1.1:443 means Daytona did not apply the block, typically on a Tier 1/2
        organization. The exec is bounded and never retried.
        """
        try:
            try:
                sandbox = client.get(sandbox_id)
                state = getattr(sandbox, "state", None)
                state = str(getattr(state, "value", state) or "").lower()
                if state in _LOST_STATES:
                    _LOGGER.debug("Network enforcement probe skipped: sandbox %s is %s", sandbox_id, state)
                    return
                outcome = self._remote_exec(
                    sandbox, _NETWORK_PROBE_CODE, timeout=NETWORK_PROBE_TIMEOUT_SECONDS, retry=False
                ).strip()
            except DaytonaNotFoundError:
                _LOGGER.debug("Network enforcement probe skipped: sandbox %s no longer exists", sandbox_id)
                return
            except Exception as exc:
                _LOGGER.debug(
                    "Network enforcement probe did not run in sandbox %s (%s)", sandbox_id, type(exc).__name__
                )
                return
            if outcome == "connected":
                _LOGGER.warning(
                    "NETWORK BLOCKING IS NOT ENFORCED in Daytona sandbox %s: FLEET_DAYTONA_NETWORK_BLOCK_ALL=true was "
                    "requested, but the sandbox reached 1.1.1.1:443. Per-sandbox network policies require a Daytona "
                    "Tier 3 or Tier 4 organization; this is likely a Tier 1/2 org whose organization-level policy "
                    "wins. Model code in this sandbox has outbound network access.",
                    sandbox_id,
                )
            else:
                _LOGGER.debug(
                    "Network blocking is enforced in sandbox %s (probe: %s)", sandbox_id, outcome or "no output"
                )
        except BaseException:  # a background diagnostic must never surface anywhere
            _LOGGER.debug("Network enforcement probe for sandbox %s failed", sandbox_id, exc_info=True)

    def _verify_network_policy(self, sandbox: Any) -> None:
        """Warn (once per process) when a created sandbox does not report the requested network policy.

        The create response (SandboxDto, daytona 0.224.0) carries network_block_all and domain_allow_list;
        if they are absent the handle is refreshed once. Per-sandbox policies only take effect on Daytona
        Tier 3/4 organizations; on Tier 1/2 the organization policy wins. Never fails the create.
        """
        expected = self.network_params()
        if not expected:
            return
        setting = (
            "FLEET_DAYTONA_DOMAIN_ALLOW_LIST" if "domain_allow_list" in expected else "FLEET_DAYTONA_NETWORK_BLOCK_ALL"
        )
        try:
            block_all, allow_list = _reported_network_policy(sandbox)
            refresh = getattr(sandbox, "refresh_data", None)
            if block_all is None and allow_list is None and callable(refresh):
                refresh()
                block_all, allow_list = _reported_network_policy(sandbox)
        except Exception as exc:
            block_all = allow_list = None
            _LOGGER.debug("Could not read the sandbox network policy (%s)", type(exc).__name__)
        if "domain_allow_list" in expected:
            applied = _domains(allow_list) == _domains(expected["domain_allow_list"]) and not block_all
        else:
            applied = block_all is True
        if applied:
            return
        observed = (
            "is not reported by Daytona"
            if block_all is None and allow_list is None
            else f"is not in effect (Daytona reports network_block_all={block_all!r}, domain_allow_list={allow_list!r})"
        )
        _warn_network_policy_once(
            "Daytona sandbox %s: the requested network policy %s=%s %s. Per-sandbox network policies require a "
            "Daytona Tier 3 or Tier 4 organization; on Tier 1/2 the organization-level policy applies instead, "
            "so sandbox egress may not be restricted as configured. Reported once per process.",
            getattr(sandbox, "id", "?"),
            setting,
            expected.get("domain_allow_list", "true"),
            observed,
        )

    def ensure_sandbox(self) -> Any:
        """Trace provisioning/resume, including the provider state check on a reused sandbox."""
        with self._lifecycle_span("sandbox.provision") as span:
            span.set_attribute("action", "reuse" if self._sandbox is not None else "create")
            try:
                sandbox = self._ensure_sandbox()
            except SandboxUnavailableError:
                self._activity.observe_alive(False)
                raise
            # Created, or resumed after a provider state refresh: the provider has just reported it usable.
            self._activity.observe_alive(True)
            return sandbox

    def sandbox_activity(self, *, include_children: bool = True) -> repl.SandboxActivity:
        """Whether code is executing in this agent's sandbox (and since when), and whether it is alive.

        Cheap and thread-safe: it reads local state only, never the Daytona API and never the manager lock,
        so it can be called from any thread at any rate. `busy` covers `run_code` executions from
        `run_namespace`. `alive` is the last verdict the manager already had: a create or resume (True), a
        liveness poll during an execution (every `repl.LIVENESS_POLL_SECONDS`), a lost or unavailable sandbox
        (False), or a stop by `cancel_turn`/`shutdown` (False); None before the first one. With
        `include_children`, executions in child sandboxes (recursively) count as busy and as output, while
        `alive` stays this sandbox's own.
        """
        own = self._activity.snapshot()
        if not include_children:
            return own
        children = self._children.copy().values()  # an atomic copy; never iterate the live dict
        return repl.merge_activity(own, [child.sandbox_activity() for child in children])

    def _ensure_sandbox(self) -> Any:
        """Create or resume this agent's sandbox, returning the live handle.

        A pending create is adopted only when exactly one matching sandbox exists, so creates never duplicate.
        A root sandbox the provider reports failed, destroyed, or missing is rebuilt from the snapshot with the
        same Volume mounts (Session state lives on the Volume); a child's raises.
        """
        self.check_turn()
        # A cancellation's stop may not have reached the provider yet; never start work it would then kill.
        if not self.root._stops_settled.wait(_STATE_SETTLE_SECONDS):
            raise RuntimeError("A previous Turn's sandbox stop did not finish")
        self.check_turn()
        with self._lock:
            sandbox, created = self._ensure_sandbox_locked()
            # Claimed under the lock so a concurrent caller cannot probe the same id twice.
            probe = created and self._claim_network_probe(sandbox)
        if probe:  # after the root lock is released, before the Turn check: nothing waits on it
            self._start_network_probe(sandbox)
        if created:
            self.check_turn()
            self.emit("sandbox_ready", depth=self.depth)
        return sandbox

    def _ensure_sandbox_locked(self) -> tuple[Any, bool]:
        """`_ensure_sandbox` under `_lock`: the live handle and whether it was just created (or rebuilt)."""
        if self._client is None:
            self._client = _shared_daytona_client(self.api_key, self.org_id)
        if self._sandbox is not None:
            try:
                self._resume_if_needed()
                return self._sandbox, False
            except SandboxUnavailableError as exc:
                if self.parent is not None:
                    raise
                self._discard_unavailable_sandbox(exc.state)
        self.check_turn()
        if self._creation_pending:
            query = ListSandboxesQuery(labels={"fleet_session": self.session_id, "fleet_agent": self.agent_id})
            matches = with_retries(functools.partial(self._list_sandboxes, query))
            if len(matches) != 1:
                raise RuntimeError("Sandbox allocation outcome is uncertain; refusing a duplicate create")
            self._sandbox = matches[0]
            self._record_allocation("ready")
            self._creation_pending = False
            return self._ensure_sandbox_locked()
        if self.parent is None:
            self.reconcile_allocations()
        child = self.parent is not None
        root_sandbox = self.root.ensure_sandbox() if child else None
        params = self.create_params(
            linked_sandbox=root_sandbox.id if root_sandbox is not None and self.linked_children else None
        )
        self.emit("sandbox_provisioning")
        self._record_allocation("requested")
        self._creation_pending = True
        try:
            self._sandbox = self._client.create(params)
        except BaseException as exc:
            # Only an uncertain outcome (timeout, transport, 5xx) may have created a sandbox; a definite
            # rejection must not leave every later call refusing a "duplicate" create.
            if _create_definitely_failed(exc):
                self._creation_pending = False
                self._allocation_path().unlink(missing_ok=True)
            raise
        self._creation_pending = False
        self._record_allocation("ready")
        self._verify_network_policy(self._sandbox)
        return self._sandbox, True

    def create_params(self, *, linked_sandbox: str | None = None) -> CreateSandboxFromSnapshotParams:
        """Daytona create parameters for this agent's sandbox.

        A child is ephemeral and bounded by a TTL that outlives the Turn deadline, and its auto-stop never
        fires inside a Turn. A non-disposable root has no TTL; Daytona deletes it after it has been stopped
        for `root_auto_delete_minutes`, and the next Turn rebuilds it from the snapshot.
        """
        child = self.parent is not None
        ephemeral = child or self.disposable
        turn_minutes = math.ceil(self.root.turn_timeout_seconds / 60)
        return CreateSandboxFromSnapshotParams(
            snapshot=self.snapshot,
            language="python",
            volumes=self.volume_mounts(child=child),
            auto_stop_interval=max(self.auto_stop_minutes, turn_minutes + 5) if child else self.auto_stop_minutes,
            linked_sandbox=linked_sandbox,
            ephemeral=ephemeral,
            auto_delete_interval=0 if ephemeral else self.root_auto_delete_minutes,
            ttl_minutes=max(30, turn_minutes + 10) if child else None,
            **self.network_params(),
            labels={
                "fleet_session": self.session_id,
                "fleet_agent": self.agent_id,
                "fleet_parent": self.parent.agent_id if self.parent is not None else "root",
            },
        )

    def _discard_unavailable_sandbox(self, state: str) -> None:
        """Forget a failed root sandbox so it is rebuilt; best-effort delete one that still exists. Holds `_lock`."""
        sandbox, client = self._sandbox, self._client
        self.emit("sandbox_rebuilding", state=state)
        self._sandbox = None
        self._contexts.clear()
        if sandbox is not None and client is not None and state in {"error", "build_failed"}:
            try:
                with_retries(lambda: client.delete(sandbox, timeout=60))
            except Exception:
                _LOGGER.warning("Could not delete failed Daytona sandbox %s before rebuilding", sandbox.id)

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

        def state_of() -> str:
            try:
                return _sandbox_state(sandbox)
            except DaytonaNotFoundError:
                return "missing"

        state = state_of()
        deadline = time.monotonic() + _STATE_SETTLE_SECONDS
        while state in _SETTLING_STATES:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Sandbox {sandbox.id} did not settle from state {state!r}")
            time.sleep(_STATE_POLL_SECONDS)
            self.check_turn()
            state = state_of()
        if state in _FAILED_STATES or state == "missing":
            raise SandboxUnavailableError(sandbox.id, state)
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
        """Run one execution in the interpreter's context; a lost transport stops the Turn without replay."""
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
        remaining = self.root._deadline - time.monotonic() if self.root._deadline else self.root.turn_timeout_seconds
        try:
            result = repl.run(
                sandbox,
                context,
                payload,
                on_call=interpreter.answer_host_call,
                timeout=max(1, int(remaining)),
                alive=functools.partial(_sandbox_alive, sandbox),
                tracker=self._activity,
            )
        except BaseException as exc:
            self.check_turn()  # a deadline or cancellation explains the failure
            try:
                self.cancel_turn()  # a dead transport cannot establish rollback; stop remote work first
            except Exception:
                _LOGGER.warning("Could not confirm remote stop after execution failure")
            if isinstance(exc, ExecutionOutcomeUnknownError):
                raise
            raise ExecutionOutcomeUnknownError(f"Sandbox execution transport failed ({type(exc).__name__})") from exc
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

    def turn_inputs(self, notes_limit: int = 20) -> dict[str, Any]:
        """Gather a Turn's remote inputs in one exec: file digests, shared notes, and the environment.

        Digests follow `list_workspace_files` (symbolic links skipped, never traversed) and are required;
        notes are optional hints, so an unreadable mount yields none instead of failing the Turn.
        """
        with_notes = bool(self.shared_notes_subpath) and 1 <= notes_limit <= 50
        code = (
            _DIGESTS_CODE
            + _ENVIRONMENT_CODE
            + (_notes_code(notes_limit) if with_notes else "notes = []\n")
            + "print(json.dumps({'digests': digests, 'notes': notes, 'environment': environment}, sort_keys=True))"
        )
        result = json.loads(self.exec_remote_python(code) or "{}")
        digests = result.get("digests") if isinstance(result, dict) else None
        if not isinstance(digests, dict) or not all(
            isinstance(path, str) and isinstance(digest, str) for path, digest in digests.items()
        ):
            raise RuntimeError("Invalid workspace digest listing")
        if result.get("notes") is None:
            _LOGGER.warning("Shared learned notes were unavailable for Session %s", self.session_id)
        environment = result.get("environment")
        return {
            "digests": digests,
            "notes": _valid_notes(result.get("notes")),
            "environment": environment if isinstance(environment, str) else "",
        }

    def reset_turn_budget(self) -> None:
        """Start a fresh Turn: zero the counters, clear receipts, and arm the deadline watchdog."""
        root = self.root
        root.end_turn()
        with root._budget_lock:
            root._llm_calls_used = 0
            root._sandbox_errors = 0
            root._submit_rejections = 0
            root._budget_breakdown = dict.fromkeys(root._budget_breakdown, 0)
            root._allocations = 0
            root._child_receipts.clear()
            root._cancelled.clear()
            root._deadline = time.monotonic() + root.turn_timeout_seconds
            root._watchdog = threading.Timer(root.turn_timeout_seconds, root.cancel_turn)
            root._watchdog.daemon = True
            root._watchdog.start()

    def end_turn(self) -> None:
        """Cancel the watchdog and clear the Turn deadline."""
        root = self.root
        if root._watchdog is not None:
            root._watchdog.cancel()
            root._watchdog = None
        root._deadline = 0

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

    def shutdown(self) -> None:
        """Stop or delete this agent's sandbox after shutting down any children.

        A child's workspace is cleared before deletion; a root sandbox is stopped unless disposable.
        """
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
            self._activity.observe_alive(False)
            for interpreter in interpreters:
                interpreter._closed = True
        if sandbox is not None and client is not None:
            try:
                if self.parent is not None or self.disposable:
                    self.emit("sandbox_deleting", sandbox_id=sandbox.id)
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
                        self.emit("workspace_cleanup_failed", sandbox_id=sandbox.id)
                    # A stopped or failed sandbox cannot run workspace cleanup,
                    # but must still be deleted and confirmed by the provider.
                    with_retries(lambda: client.delete(sandbox, timeout=60, wait=True))
                    (Path(".fleet/allocations") / f"{self.agent_id}.json").unlink(missing_ok=True)
                    with self.root._budget_lock:
                        self.root._children.pop(self.agent_id, None)
                    self.emit("sandbox_deleted", sandbox_id=sandbox.id)
                else:
                    with_retries(lambda: client.stop(sandbox, timeout=60))
            except Exception:
                _LOGGER.warning("Daytona Session sandbox stop failed")
                if self.parent is not None:
                    # Retain the handle so a later shutdown can retry deletion.
                    self._sandbox = sandbox
                    self.emit("sandbox_cleanup_failed", sandbox_id=sandbox.id)
        elif self.parent is not None:
            with self.root._budget_lock:
                self.root._children.pop(self.agent_id, None)
