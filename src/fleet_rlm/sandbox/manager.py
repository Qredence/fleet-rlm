"""DaytonaSandboxManager: one Session sandbox (and its child sandboxes), budgets, workspace files, and notes."""

from __future__ import annotations

import base64
import contextvars
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import dspy
from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig, ListSandboxesQuery, VolumeMount

from fleet_rlm.config import validate_shared_notes_subpath
from fleet_rlm.engine.signatures import STOPPING_RULES
from fleet_rlm.observability import activity_text
from fleet_rlm.sandbox import repl
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError as ExecutionOutcomeUnknownError
from fleet_rlm.workspace import WorkspaceDataset as WorkspaceDataset
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

_DAYTONA_CLIENTS: dict[tuple[str, str], Daytona] = {}
_DAYTONA_CLIENTS_LOCK = threading.Lock()


def _shared_daytona_client(api_key: str, org_id: str) -> Daytona:
    """One client per credential pair: each `Daytona()` opens its own event-dispatcher connection and thread."""
    with _DAYTONA_CLIENTS_LOCK:
        client = _DAYTONA_CLIENTS.get((api_key, org_id))
        if client is None:
            client = _DAYTONA_CLIENTS[api_key, org_id] = Daytona(DaytonaConfig(api_key=api_key, organization_id=org_id))
        return client


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
    _budget_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _llm_calls_used: int = field(default=0, init=False, repr=False)
    _sandbox_errors: int = field(default=0, init=False, repr=False)
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
        child.emit("agent_spawn", depth=child.depth)
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
            total = 0
            paths_to_copy = selected_paths
            if paths_to_copy is None:
                paths_to_copy = [path for path in self.list_workspace_files() if path.startswith(("files/", "skills/"))]
            for path in paths_to_copy:
                data = self.read_workspace_file(path)
                total += len(data)
                if total > 100_000_000:
                    raise ValueError("Child input transfer quota exceeded")
                child.write_workspace_file(path, data)
                if path in ambient_skills:
                    continue
                with root._budget_lock:
                    root._child_receipts[child.agent_id]["inputs"][path] = hashlib.sha256(data).hexdigest()
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
        self.emit("agent_result", artifacts=results)
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
            result = json.loads(self._remote_exec(self.ensure_sandbox(), code, timeout=timeout + 1))
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
        # Stopping the root interrupts remote Python, including a running infinite loop.
        with root._lock:
            if root._sandbox is not None and root._client is not None:
                root._client.stop(root._sandbox, timeout=60)
                root._contexts.clear()

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
            for sandbox in self._client.list(
                ListSandboxesQuery(
                    labels={
                        "fleet_session": self.session_id,
                        "fleet_agent": record["agent_id"],
                    }
                )
            ):
                self._client.delete(sandbox, wait=True, timeout=60)
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
        """Create or resume this agent's sandbox, returning the live handle.

        A pending create is adopted only when exactly one matching sandbox exists, so creates never duplicate.
        """
        self.check_turn()
        with self._lock:
            if self._client is None:
                self._client = _shared_daytona_client(self.api_key, self.org_id)
            if self._sandbox is None:
                self.check_turn()
                if self._creation_pending:
                    matches = list(
                        self._client.list(
                            ListSandboxesQuery(
                                labels={
                                    "fleet_session": self.session_id,
                                    "fleet_agent": self.agent_id,
                                }
                            )
                        )
                    )
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
                    ttl_minutes=30 if child else 120,
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
            state = getattr(self._sandbox, "state", None)
            normalized_state = str(getattr(state, "value", state) or "").lower()
            if normalized_state in {"stopped", "archived", "archiving", "stopping"}:
                self._client.start(self._sandbox)
                self._contexts.clear()
            return self._sandbox

    def _remote_exec(self, sandbox: Any, code: str, *, timeout: int | None = None) -> str:
        encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
        command = (
            "python3 -c 'import base64; exec(compile(base64.b64decode(\"" + encoded + '"), "<fleet-remote>", "exec"))\''
        )
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
            context = repl.create_context(sandbox)
            with self._lock:
                self._contexts[interpreter.ns_id] = context
        remaining = self.root._deadline - time.monotonic() if self.root._deadline else self.root.turn_timeout_seconds
        try:
            result = repl.run(
                sandbox, context, payload, on_call=interpreter.answer_host_call, timeout=max(1, int(remaining))
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
        self.ensure_sandbox().fs.upload_file("\n".join([*header, body, ""]).encode("utf-8"), path, timeout=60)
        return path

    def list_learned_notes(self, limit: int = 20) -> list[dict[str, str]]:
        """Newest-first `{file, summary}` rows from the shared mount; empty when unavailable."""
        if not self.shared_notes_subpath or not 1 <= limit <= 50:
            return []
        code = (
            "import json, os\n"
            "root = '/learned'\n"
            "rows = []\n"
            "if os.path.ismount(root):\n"
            "    names = [name for name in os.listdir(root) if name.endswith('.md')]\n"
            "    names.sort(key=lambda name: os.stat(os.path.join(root, name)).st_mtime, reverse=True)\n"
            f"    for name in names[:{limit}]:\n"
            "        with open(os.path.join(root, name), encoding='utf-8', errors='replace') as handle:\n"
            "            head = [handle.readline() for _ in range(8)]\n"
            "        lines = [line[len('summary: '):].strip() for line in head if line.startswith('summary: ')]\n"
            "        summary = lines[0] if lines else ''\n"
            "        rows.append({'file': name, 'summary': summary[:120]})\n"
            "print(json.dumps(rows))\n"
        )
        try:
            rows = json.loads(self.exec_remote_python(code) or "[]")
        except Exception:
            _LOGGER.warning("Shared learned notes were unavailable for Session %s", self.session_id)
            return []
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    def reset_turn_budget(self) -> None:
        """Start a fresh Turn: zero the counters, clear receipts, and arm the deadline watchdog."""
        root = self.root
        root.end_turn()
        with root._budget_lock:
            root._llm_calls_used = 0
            root._sandbox_errors = 0
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

    def consume_budget(self, count: int = 1) -> None:
        """Charge LM calls against the shared Turn budget, raising once it is exhausted."""
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("budget consumption must be a positive integer")
        self.check_turn()
        root = self.root
        with root._budget_lock:
            if root._llm_calls_used + count > root.max_llm_calls:
                raise RuntimeError(f"Shared RLM budget exhausted ({root.max_llm_calls} calls)")
            root._llm_calls_used += count
        self.emit(
            "budget_update",
            lm_calls=root._llm_calls_used,
            max_llm_calls=root.max_llm_calls,
            tokens=None,
            estimated_cost=None,
        )

    def record_sandbox_error(self) -> None:
        """Count one failed sandbox execution toward the Turn's usage."""
        with self.root._budget_lock:
            self.root._sandbox_errors += 1

    def turn_usage(self) -> dict[str, int]:
        """LM calls and failed sandbox executions so far this Turn, across every agent in the tree."""
        root = self.root
        with root._budget_lock:
            return {"lm_calls": root._llm_calls_used, "sandbox_errors": root._sandbox_errors}

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
        sandbox.fs.upload_file(content, f"/workspace/{clean_path}", timeout=1800)
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
        sandbox.fs.upload_file(str(source), f"/workspace/{clean_path}", timeout=1800)
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
        content = sandbox.fs.download_file(f"/workspace/{clean_path}", 1800)
        if not isinstance(content, bytes) or len(content) != metadata["size"]:
            raise RuntimeError("Workspace file changed or could not be downloaded")
        return content

    def read_workspace_file_to_path(self, rel_path: str, local_path: str | Path, *, max_bytes: int = 25_000_000) -> int:
        """Download a workspace file to disk through Daytona's path overload."""
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 0 <= max_bytes <= 25_000_000:
            raise ValueError("max_bytes must be an integer between 0 and 25000000")
        clean_path = _workspace_relative_path(rel_path)
        sandbox = self.ensure_sandbox()
        metadata = self._workspace_file_metadata(sandbox, clean_path, create_parents=False, max_bytes=max_bytes)
        destination = Path(local_path)
        sandbox.fs.download_file(f"/workspace/{clean_path}", str(destination), 1800)
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
        path_b64 = base64.b64encode(rel_path.encode("utf-8")).decode("ascii")
        create_clause = "os.makedirs(parent,exist_ok=True)\n" if create_parents else ""
        read_clause = "if not os.path.isfile(target): raise FileNotFoundError('workspace file is unavailable')\n"
        if create_parents:
            read_clause = (
                "if os.path.exists(target) and not os.path.isfile(target): "
                "raise ValueError('workspace target is not a file')\n"
            )
        size_clause = "size=os.path.getsize(target) if os.path.isfile(target) else None\n"
        if max_bytes is not None:
            size_clause += f"if size>{max_bytes}: raise ValueError('workspace file exceeds size limit')\n"
        code = (
            "import base64,json,os\n"
            "root=os.path.realpath('/workspace')\n"
            "relative=base64.b64decode('" + path_b64 + "').decode('utf-8')\n"
            "parts=relative.split('/')\n"
            "parent=root\n"
            "for part in parts[:-1]:\n"
            " parent=os.path.join(parent,part)\n"
            " if os.path.islink(parent): raise ValueError('workspace path may not traverse symbolic links')\n"
            " if os.path.exists(parent) and not os.path.isdir(parent): "
            "raise ValueError('workspace parent is not a directory')\n"
            "if os.path.commonpath((root,os.path.realpath(parent))) != root: "
            "raise ValueError('invalid workspace path')\n" + create_clause + "target=os.path.join(parent,parts[-1])\n"
            "if os.path.islink(target): raise ValueError('workspace target may not be a symbolic link')\n"
            "if os.path.commonpath((root,parent)) != root: raise ValueError('invalid workspace path')\n"
            + read_clause
            + size_clause
            + "print(json.dumps({'size':size}))"
        )
        metadata = json.loads(self._remote_exec(sandbox, code))
        if not isinstance(metadata, dict) or (
            metadata.get("size") is not None and not isinstance(metadata["size"], int)
        ):
            raise RuntimeError("Invalid workspace file metadata")
        return metadata

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
            return DaytonaInterpreter(manager=manager, tools=dict(tools or {}), owns_manager=active is not None)

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
        sandbox.fs.delete_file(f"/workspace/{clean}")

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
                    client.delete(sandbox, timeout=60, wait=True)
                    (Path(".fleet/allocations") / f"{self.agent_id}.json").unlink(missing_ok=True)
                    with self.root._budget_lock:
                        self.root._children.pop(self.agent_id, None)
                    self.emit("sandbox_deleted", sandbox_id=sandbox.id)
                else:
                    client.stop(sandbox, timeout=60)
            except Exception:
                _LOGGER.warning("Daytona Session sandbox stop failed")
                if self.parent is not None:
                    # Retain the handle so a later shutdown can retry deletion.
                    self._sandbox = sandbox
                    self.emit("sandbox_cleanup_failed", sandbox_id=sandbox.id)
        elif self.parent is not None:
            with self.root._budget_lock:
                self.root._children.pop(self.agent_id, None)
