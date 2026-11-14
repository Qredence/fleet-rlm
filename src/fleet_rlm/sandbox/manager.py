"""DaytonaSandboxManager: one Session sandbox (and its child sandboxes), budgets, workspace files, and notes."""

from __future__ import annotations

import base64
import contextvars
import logging
import threading
import time  # noqa: F401 - shared clock used by budget and lifecycle modules
import uuid
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import dspy
import mlflow
from daytona import Daytona, DaytonaConfig
from mlflow.entities import SpanType

from fleet_rlm.config import validate_shared_notes_subpath
from fleet_rlm.contracts import STOPPING_RULES
from fleet_rlm.sandbox import repl
from fleet_rlm.sandbox.budget import _UNSET, SandboxBudgetMixin
from fleet_rlm.sandbox.lifecycle import SandboxLifecycleMixin, SandboxOwnershipLostError
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError, provider_not_found, with_retries
from fleet_rlm.sandbox.snapshots import SNAPSHOT_NAME, SnapshotIdentity, preflight
from fleet_rlm.sandbox.workspace_io import WorkspaceIOMixin
from fleet_rlm.workspace import validate_session_id

if TYPE_CHECKING:
    from fleet_rlm.sandbox.interpreter import DaytonaInterpreter

_LOGGER = logging.getLogger(__name__)
RESOURCE_SAMPLE_TIMEOUT_SECONDS = 2.0
SANDBOX_TRANSPORT = "daytona_code_interpreter"

# The cleanup outcome a terminal Turn records. `stopped` and `unconfirmed` are produced by the
# manager's single teardown; `not_required` belongs to a Turn that completed and kept its root.
CLEANUP_STOPPED = "stopped"
CLEANUP_UNCONFIRMED = "unconfirmed"
CLEANUP_NOT_REQUIRED = "not_required"

_STATE_POLL_SECONDS = 1.0
_STATE_SETTLE_SECONDS = 90.0

_DAYTONA_CLIENTS: dict[tuple[str, str], Daytona] = {}
_DAYTONA_CLIENTS_LOCK = threading.Lock()

_ACTIVE_MANAGER: contextvars.ContextVar[Any] = contextvars.ContextVar("fleet_active_manager", default=None)
_NATIVE_CHILDREN: contextvars.ContextVar[list[Any] | None] = contextvars.ContextVar(
    "fleet_native_children", default=None
)

__all__ = [
    "CLEANUP_NOT_REQUIRED",
    "CLEANUP_STOPPED",
    "CLEANUP_UNCONFIRMED",
    "RESOURCE_SAMPLE_TIMEOUT_SECONDS",
    "SANDBOX_TRANSPORT",
    "_ACTIVE_MANAGER",
    "_DAYTONA_CLIENTS",
    "_NATIVE_CHILDREN",
    "_STATE_POLL_SECONDS",
    "_UNSET",
    "DaytonaSandboxManager",
    "SandboxOwnershipLostError",
    "_host_call_bound",
    "_shared_daytona_client",
    "preflight",
    "provider_not_found",
    "with_retries",
]


def _host_call_bound(remaining: float | None) -> int | None:
    """Whole seconds a bridged host call may wait: the Turn's remainder, or None when it has none."""
    if remaining is None:
        return None
    if remaining < 1:
        raise ValueError("A host-call bound needs at least one whole second remaining")
    return int(remaining)


def _shared_daytona_client(api_key: str, org_id: str) -> Daytona:
    """One client per credential pair: each `Daytona()` opens its own event-dispatcher connection and thread."""
    with _DAYTONA_CLIENTS_LOCK:
        client = _DAYTONA_CLIENTS.get((api_key, org_id))
        if client is None:
            client = _DAYTONA_CLIENTS[api_key, org_id] = Daytona(DaytonaConfig(api_key=api_key, organization_id=org_id))
        return client


@dataclass(slots=True)
class DaytonaSandboxManager(WorkspaceIOMixin, SandboxBudgetMixin, SandboxLifecycleMixin):
    """Own one warm Daytona Sandbox mounted to a single Session's Volume subpath."""

    api_key: str
    org_id: str
    volume_id: str
    session_id: str
    snapshot: str = SNAPSHOT_NAME
    auto_stop_minutes: int = 30
    sub_adapter: dspy.Adapter | None = None
    sub_lm: dspy.LM | None = None
    jev_lm: Any | None = None
    max_llm_calls: int = 150
    max_depth: int = 3
    max_children: int = 8
    max_active_children: int = 4
    turn_timeout_seconds: int = 0
    linked_children: bool = True
    disposable: bool = False
    shared_notes_subpath: str = ""
    event_callback: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False)
    environment_report_claim: Callable[[str], bool] | None = field(default=None, repr=False)
    ownership_epoch: int | None = field(default=None, repr=False)
    ownership_resource_mutate: Callable[[str, dict[str, Any] | None], bool] | None = field(default=None, repr=False)
    snapshot_resolved_callback: Callable[[SnapshotIdentity], None] | None = field(default=None, repr=False)
    parent: DaytonaSandboxManager | None = field(default=None, repr=False)
    agent_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    _children: dict[str, DaytonaSandboxManager] = field(default_factory=dict, init=False, repr=False)
    _allocations: int = field(default=0, init=False, repr=False)
    _resource_sampler: threading.Thread | None = field(default=None, init=False, repr=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _execution_fences: set[repl.ExecutionFence] = field(default_factory=set, init=False, repr=False)
    _cleanup_outcome: str | None = field(default=None, init=False, repr=False)
    _teardown_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _cancellation_claimed: bool = field(default=False, init=False, repr=False)
    _teardown_done: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _shutdown_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _deadline: float | None = field(default=None, init=False, repr=False)
    _deadline_source: str = field(default="default", init=False, repr=False)
    _effective_deadline_seconds: int | None = field(default=None, init=False, repr=False)
    _turn_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _turn_generation: int = field(default=0, init=False, repr=False)
    _turn_active: bool = field(default=False, init=False, repr=False)
    _watchdog: threading.Timer | None = field(default=None, init=False, repr=False)
    _sandbox: Any = field(default=None, init=False, repr=False)
    _ownership_lost: bool = field(default=False, init=False, repr=False)
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
    _environment_reported: bool = field(default=False, init=False, repr=False)
    _child_receipts: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _interpreters: dict[str, DaytonaInterpreter] = field(default_factory=dict, init=False, repr=False)
    _contexts: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _creation_pending: bool = field(default=False, init=False, repr=False)
    _cleanup_pending: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate identity, budgets, and the shared-notes subpath before any sandbox work."""
        validate_session_id(self.session_id)
        if not self.api_key or not self.org_id or not self.volume_id:
            raise ValueError("Daytona API key, organization ID, and Volume ID are required")
        if self.max_llm_calls < 1:
            raise ValueError("max_llm_calls must be positive")
        validate_shared_notes_subpath(self.shared_notes_subpath)

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
        """Deliver one lifecycle notification without letting delivery failure affect teardown."""
        try:
            self.emit(event, **payload)
        except Exception as exc:
            _LOGGER.warning(
                "Lifecycle notification %s failed for Session %s (%s)",
                event,
                self.session_id,
                type(exc).__name__,
            )

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
                failure = exc
                span.set_status("ERROR")
            finally:
                span.set_attribute("sandbox_id", getattr(self._sandbox, "id", None))
                span.set_outputs({"completed": completed})
        if failure is not None:
            raise failure

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
        """Run one execution in the interpreter's context without replaying uncertain work."""
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
        remaining = self.admit_execution()
        timeout = _host_call_bound(remaining)
        execution_id = payload.get("execution_id")
        fence = self._open_execution_fence()
        try:
            result = repl.run(
                sandbox,
                context,
                payload,
                on_call=lambda call: interpreter.answer_host_call(call, execution_id=execution_id),
                timeout=timeout,
                fence=fence,
                on_fence=lambda: self._report_fence(fence, interpreter.ns_id),
            )
        except BaseException as exc:
            self.check_turn()
            try:
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
        """Emit one soft observation per Session when probed modules are absent."""
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
                _LOGGER.warning("Environment observation could not be delivered for Session %s", root.session_id)

    def _claim_environment_report(self) -> bool:
        """Return True only when this process may emit the Session's one environment observation."""
        claim = self.environment_report_claim
        if claim is None:
            return True
        try:
            return bool(claim(self.session_id))
        except Exception:
            _LOGGER.warning("Environment observation marker is unavailable for Session %s", self.session_id)
            return False

    def interpreter_factory(
        self,
        tools: Mapping[str, Callable[..., Any]] | None = None,
        *,
        input_paths: list[str] | None = None,
    ) -> Callable[[], DaytonaInterpreter]:
        """Return the fresh interpreter factory required by DSPy's SandboxSession bridge."""
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
            "Forwarded dataset views become JSON metadata in nested agents: "
            "use workspace.read_text(context['path']) or WorkspaceDatasetView(context) for file access. "
            "workspace.load_image('files/...') loads bounded workspace images as dspy.Image for multimodal "
            "llm_query or dspy.Predict. "
            "Use workspace.write_artifact(name, content) for results. "
            "Artifact names are relative to artifacts/ (e.g. 'result.txt'); use the helper's returned path. "
            "Nested DSPy code agents allocate fresh child sandboxes; files/ and skills/ inputs are copied, while "
            "artifacts/ starts empty inside a child unless specific artifacts/... paths are requested in input_paths "
            "(for example via spawn_agent/spawn_agents). "
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
