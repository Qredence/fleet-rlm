"""Session-scoped Daytona sandbox and DSPy interpreter integration."""

from __future__ import annotations

import base64
import contextvars
import hashlib
import json
import keyword
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import dspy
import mlflow
from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig, ListSandboxesQuery, VolumeMount
from mlflow.entities import SpanType

from fleet_rlm.config import validate_shared_notes_subpath
from fleet_rlm.dspy_support import decision_evidence, without_mlflow_dspy_callback
from fleet_rlm.engine.signatures import STOPPING_RULES, item_decision
from fleet_rlm.observability import activity_preview, activity_text
from fleet_rlm.sandbox import repl
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError as ExecutionOutcomeUnknownError
from fleet_rlm.workspace import WorkspaceDataset as WorkspaceDataset

_LOGGER = logging.getLogger(__name__)
SANDBOX_TRANSPORT = "daytona_code_interpreter"

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_NAMESPACE_RE = re.compile(r"^ns_[a-f0-9]{8}$")
_ALLOWED_WORKSPACE_ROOTS = frozenset({"files", "artifacts", "memory", "skills", "buffers", "sessions"})
_ACTIVE_MANAGER: contextvars.ContextVar[Any] = contextvars.ContextVar("fleet_active_manager", default=None)
_NATIVE_CHILDREN: contextvars.ContextVar[list[Any] | None] = contextvars.ContextVar(
    "fleet_native_children", default=None
)


def validate_session_id(session_id: str) -> str:
    """Validate a session key before it is used as a Daytona Volume subpath."""
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("session_id must be 1-64 letters, digits, dots, underscores, or hyphens")
    if session_id in {".", ".."} or ".." in session_id:
        raise ValueError("session_id must not contain parent path syntax")
    return session_id


def session_volume_subpath(session_id: str) -> str:
    """Return the provider-enforced private subtree for one Session."""
    return f"sessions/{validate_session_id(session_id)}"


def _child_input_path(path: str) -> str:
    """Validate a child input path, accepting the sandbox's absolute `/workspace/` form the model sees."""
    if isinstance(path, str) and path.startswith("/workspace/"):
        path = path[len("/workspace/") :]
    clean_path = _workspace_relative_path(path)
    if not clean_path.startswith(("files/", "skills/")):
        raise ValueError("Child inputs must be files beneath files/ or skills/")
    return clean_path


def _workspace_relative_path(relative_path: str) -> str:
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or len(relative_path) > 1024
        or "\\" in relative_path
        or "\x00" in relative_path
        or any(ord(character) < 32 for character in relative_path)
    ):
        raise ValueError("Workspace path must be a non-empty POSIX-relative path")
    parts = relative_path.split("/")
    if parts[0] not in _ALLOWED_WORKSPACE_ROOTS or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Workspace path is outside the allowed files, artifacts, memory, and skills roots")
    return "/".join(parts)


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
    _interpreters: dict[str, DaytonaInterpreter] = field(default_factory=dict, init=False, repr=False)
    # One Daytona interpreter context per Fleet namespace; invalid once the sandbox stops.
    _contexts: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _creation_pending: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        validate_session_id(self.session_id)
        if not self.api_key or not self.org_id or not self.volume_id:
            raise ValueError("Daytona API key, organization ID, and Volume ID are required")
        if self.max_llm_calls < 1:
            raise ValueError("max_llm_calls must be positive")
        validate_shared_notes_subpath(self.shared_notes_subpath)

    @property
    def root(self) -> DaytonaSandboxManager:
        return self.parent.root if self.parent is not None else self

    @property
    def depth(self) -> int:
        return self.parent.depth + 1 if self.parent is not None else 0

    def emit(self, event: str, **payload: Any) -> None:
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
        root = self.root
        if root._cancelled.is_set():
            raise RuntimeError("Turn cancelled")
        if root._deadline and time.monotonic() >= root._deadline:
            root._cancelled.set()
            raise TimeoutError("Turn deadline exceeded")

    def allocate_child(self, *, input_paths: list[str] | None = None) -> DaytonaSandboxManager:
        root = self.root
        self.check_turn()
        selected_paths: list[str] | None = None
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
            return child
        except BaseException:
            child.shutdown()
            raise

    def collect_child_artifacts(self) -> list[dict[str, Any]]:
        if self.parent is None:
            return []
        self.check_turn()
        results = []
        total = 0
        for path in self.list_workspace_files():
            if not path.startswith("artifacts/"):
                continue
            raw = self.read_workspace_file(path)
            total += len(raw)
            if total > 25_000_000 or len(results) >= 100:
                raise ValueError("Child output promotion quota exceeded")
            target = f"artifacts/{self.agent_id}/{path.removeprefix('artifacts/')}"
            self.parent.write_workspace_file(target, raw)
            results.append({"path": target, "sha256": hashlib.sha256(raw).hexdigest(), "agent_id": self.agent_id})
        self.emit("agent_result", artifacts=results)
        return results

    def cancel_turn(self) -> None:
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
        self.check_turn()
        with self._lock:
            if self._client is None:
                self._client = Daytona(DaytonaConfig(api_key=self.api_key, organization_id=self.org_id))
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
        root = self.root
        root.end_turn()
        with root._budget_lock:
            root._llm_calls_used = 0
            root._allocations = 0
            root._cancelled.clear()
            root._deadline = time.monotonic() + root.turn_timeout_seconds
            root._watchdog = threading.Timer(root.turn_timeout_seconds, root.cancel_turn)
            root._watchdog.daemon = True
            root._watchdog.start()

    def end_turn(self) -> None:
        root = self.root
        if root._watchdog is not None:
            root._watchdog.cancel()
            root._watchdog = None
        root._deadline = 0

    def consume_budget(self, count: int = 1) -> None:
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

    def list_workspace_files(self) -> list[str]:
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
            "Nested DSPy code agents allocate fresh child sandboxes; only files/ and skills/ inputs are copied. "
            "Successful child artifacts appear under artifacts/<agent-id>/; inspect workspace.tree() for their paths. "
            + STOPPING_RULES
        )
        return create

    def delete_workspace_file(self, rel_path: str) -> None:
        clean = _workspace_relative_path(rel_path)
        self._workspace_file_metadata(self.ensure_sandbox(), clean, create_parents=False)
        self.exec_remote_python(f"import os; os.unlink({('/workspace/' + clean)!r})")

    def _register_interpreter(self, interpreter: DaytonaInterpreter) -> None:
        with self._lock:
            self._interpreters[interpreter.ns_id] = interpreter

    def _unregister_interpreter(self, interpreter: DaytonaInterpreter) -> None:
        with self._lock:
            self._interpreters.pop(interpreter.ns_id, None)

    def shutdown(self) -> None:
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


class DaytonaInterpreter:
    """DSPy CodeInterpreter backed by a namespace in a Session-isolated sandbox."""

    def __init__(
        self,
        manager: DaytonaSandboxManager,
        tools: dict[str, Callable[..., Any]] | None = None,
        owns_manager: bool = False,
    ):
        self.manager = manager
        self.owns_manager = owns_manager
        self.tools = dict(tools or {})
        reserved = {
            "__builtins__",
            "llm_query",
            "llm_query_batched",
            "jev_decide_batched",
            "SUBMIT",
            "list_skills",
            "use_skill",
            "read_skill_reference",
            "run_skill_script",
            "save_skill",
        }
        if any(
            not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name) or name in reserved
            for name in self.tools
        ):
            raise ValueError("Bridged tool names must be unreserved Python identifiers")
        self.ns_id = f"ns_{uuid.uuid4().hex[:8]}"
        self._context = contextvars.copy_context()
        self._started = False
        self._closed = False
        self._host_calls = 0
        self._host_call_lock = threading.Lock()

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("Interpreter has already been shut down")
        self.manager.ensure_sandbox()
        if not self._started:
            self.manager._register_interpreter(self)
            self._started = True

    def _invoke_tool(self, tool_name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        """The host invocation, not interpreter shutdown, owns child success."""
        children: list[Any] = []
        token = _NATIVE_CHILDREN.set(children)
        try:
            value = self.tools[tool_name](*args, **kwargs)
            if hasattr(self.manager, "check_turn"):
                self.manager.check_turn()
            for child in children:
                child.collect_child_artifacts()
            return value
        finally:
            _NATIVE_CHILDREN.reset(token)
            for child in children:
                child.shutdown()

    def answer_host_call(self, call: dict[str, Any]) -> dict[str, Any]:
        """Run one bridged call in this interpreter's DSPy context; the reply the sandbox is waiting for."""
        return self._context.copy().run(self._handle_host_call, call)

    def _handle_host_call(self, call: dict[str, Any]) -> dict[str, Any]:
        with self._host_call_lock:
            self._host_calls += 1
        call_id = call.get("id")
        tool_name = call.get("tool")
        args = call.get("args", [])
        kwargs = call.get("kwargs", {})
        started = time.monotonic()
        details = {"call_id": call_id, "tool": tool_name, "source": "sandbox_bridge", "namespace": self.ns_id}
        # Facade calls and semantic LM queries return predictor internals, not tool evidence.
        predictor_call = str(tool_name).startswith("__dspy_") or tool_name in {"llm_query", "llm_query_batched"}
        if hasattr(self.manager, "emit"):
            self.manager.emit(
                "tool_start",
                **details,
                inputs="[predictor payload omitted]"
                if predictor_call
                else activity_preview({"args": args, "kwargs": kwargs}),
            )
        active_token = _ACTIVE_MANAGER.set(self.manager)
        try:
            if not isinstance(args, list) or not isinstance(kwargs, dict):
                raise ValueError("Invalid host tool arguments")
            if tool_name == "llm_query":
                if len(args) != 1 or not isinstance(args[0], str) or len(args[0]) > 100_000:
                    raise ValueError("llm_query requires one prompt of at most 100000 characters")
                if tool_name in self.tools:
                    value = self._invoke_tool(tool_name, args, kwargs)
                else:
                    self.manager.consume_budget()
                    active_lm = self.manager.sub_lm or dspy.settings.lm
                    with dspy.context(lm=active_lm, disable_history=True):
                        value = active_lm(args[0])[0]
            elif tool_name == "llm_query_batched":
                if len(args) != 1:
                    raise ValueError("llm_query_batched requires one prompt list")
                prompts = args[0]
                if (
                    not isinstance(prompts, list)
                    or not prompts
                    or len(prompts) > 100
                    or any(not isinstance(prompt, str) or len(prompt) > 100_000 for prompt in prompts)
                ):
                    raise ValueError("llm_query_batched requires 1-100 bounded string prompts")
                if tool_name in self.tools:
                    value = self._invoke_tool(tool_name, args, kwargs)
                else:
                    self.manager.consume_budget(len(prompts))
                    active_lm = self.manager.sub_lm or dspy.settings.lm

                    def complete(prompt: str) -> Any:
                        with dspy.context(lm=active_lm, disable_history=True):
                            return active_lm(prompt)[0]

                    with ThreadPoolExecutor(max_workers=min(8, len(prompts))) as pool:
                        value = list(pool.map(complete, prompts))
            elif tool_name == "jev_decide_batched":
                if len(args) != 3:
                    raise ValueError("jev_decide_batched requires items, question, and choices")
                items, question, choices = args
                if (
                    not isinstance(items, list)
                    or any(not isinstance(item, str) or len(item) > 100_000 for item in items)
                    or not isinstance(question, str)
                    or len(question) > 10_000
                    or not isinstance(choices, list)
                    or any(not isinstance(choice, str) or not choice or len(choice) > 256 for choice in choices)
                    or len(set(choices)) != len(choices)
                ):
                    raise ValueError("Invalid Jev batch inputs")
                self.manager.consume_budget(len(items))
                value = self._run_jev_batch(items, question, choices)
            elif tool_name in self.tools:
                value = self._invoke_tool(tool_name, args, kwargs)
            else:
                raise KeyError(f"Unknown bridged tool: {tool_name}")
            if hasattr(self.manager, "emit"):
                self.manager.emit(
                    "tool_end",
                    **details,
                    ok=True,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    output="[predictor payload omitted]" if predictor_call else activity_preview(value),
                )
            return {"value": value}
        except Exception as exc:
            if hasattr(self.manager, "emit"):
                self.manager.emit(
                    "tool_end",
                    **details,
                    ok=False,
                    error_type=type(exc).__name__,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            return {"error": str(exc)[:2000]}
        finally:
            _ACTIVE_MANAGER.reset(active_token)

    def _run_jev_batch(self, items: list[str], question: str, choices: list[str]) -> list[dict[str, Any]]:
        if not items or not choices or len(items) > 100 or len(choices) > 20:
            raise ValueError("Jev batch inputs exceed the supported bounds")
        from pydantic import BaseModel

        predictor = dspy.Predict(item_decision(choices))
        active_lm = self.manager.jev_lm or self.manager.sub_lm or dspy.settings.lm
        examples = [dspy.Example(item=item, question=question).with_inputs("item", "question") for item in items]
        callbacks = list(dspy.settings.callbacks)
        if active_lm is self.manager.jev_lm and self.manager.jev_lm is not None:
            callbacks = without_mlflow_dspy_callback(callbacks)
        with mlflow.start_span(name="jev_batch_decisions", span_type=SpanType.LLM) as span:
            span.set_inputs({"item_count": len(items), "question": activity_text(question), "choices": choices})
            with dspy.context(lm=active_lm, disable_history=True, callbacks=callbacks):
                predictions = predictor.batch(examples, num_threads=min(8, len(items)))
            results: list[dict[str, Any]] = []
            for item, prediction in zip(items, predictions, strict=True):
                decision = getattr(prediction, "decision", None)
                choice_value = getattr(decision, "value", None) if isinstance(decision, BaseModel) else None
                if not isinstance(choice_value, str):
                    raise RuntimeError("Jev returned an invalid decision")
                results.append({"item": item, "decision": choice_value, **decision_evidence(decision, active_lm)})
            span.set_outputs({"decisions": [{k: v for k, v in row.items() if k != "item"} for row in results]})
            return results

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        if not isinstance(code, str) or len(code.encode("utf-8")) > 1_000_000:
            raise ValueError("Interpreter code exceeds the 1 MB size limit")
        if variables is not None and not isinstance(variables, dict):
            raise TypeError("Interpreter variables must be an object")
        self.start()
        from dspy.primitives.facade import SHIM_SETUP

        execution_id = uuid.uuid4().hex
        phase = "bootstrap" if code == SHIM_SETUP else "task"
        started = time.monotonic()
        if hasattr(self.manager, "check_turn"):
            self.manager.check_turn()
        if hasattr(self.manager, "emit"):
            self.manager.emit(
                "repl_code",
                code=activity_text(code),
                namespace=self.ns_id,
                execution_id=execution_id,
                phase=phase,
            )
        from dspy.primitives.code_interpreter import CodeExecutionError, FinalOutput

        response: dict[str, Any] = {}
        with mlflow.start_span(name="sandbox.exec", span_type=SpanType.TOOL) as span:
            span.set_attributes(
                {
                    "phase": phase,
                    "namespace": self.ns_id,
                    "execution_id": execution_id,
                    "agent_id": getattr(self.manager, "agent_id", None),
                    "parent_agent_id": getattr(getattr(self.manager, "parent", None), "agent_id", None),
                    "sandbox_id": getattr(getattr(self.manager, "_sandbox", None), "id", None),
                }
            )
            span.set_inputs({"code": activity_text(code)})
            host_calls_before = self._host_calls
            try:
                response = self.manager.run_namespace(
                    self, {"code": code, "variables": variables or {}, "tools": list(self.tools)}
                )
                span.set_outputs(
                    {
                        "stdout": activity_text(str(response.get("stdout", ""))),
                        "stderr": activity_text(str(response.get("stderr", ""))),
                        "value": activity_preview(
                            response.get("final") if response.get("final") is not None else response.get("value")
                        ),
                        "error": bool(response.get("error")),
                    }
                )
                span.set_attribute("transport_retries", response.get("transport_retries", 0))
                if response.get("error"):
                    span.set_attribute("outcome", "remote_code_error")
                    span.set_attribute("remote_error_type", response.get("error_type", "PythonError"))
                    output = str(response.get("stdout", "")) + str(response.get("stderr", ""))
                    raise CodeExecutionError(output[-20_000:] or "Remote Python execution failed")
                span.set_attribute("outcome", "completed")
                if response.get("final") is not None:
                    return FinalOutput(response["final"])
                if response.get("has_value") and response.get("value") is not None:
                    return response["value"]
                return (str(response.get("stdout", "")) + str(response.get("stderr", "")))[-20_000:].strip() or None
            except Exception as exc:
                response.update(error=True, error_type=response.get("error_type") or type(exc).__name__)
                span.set_attribute("transport_retries", getattr(exc, "transport_retries", 0))
                if not isinstance(exc, CodeExecutionError):
                    outcome = (
                        "transport_uncertain"
                        if isinstance(exc, ExecutionOutcomeUnknownError)
                        else (
                            "deadline"
                            if isinstance(exc, TimeoutError)
                            else "cancelled"
                            if getattr(getattr(self.manager, "root", None), "_cancelled", threading.Event()).is_set()
                            else "transport_error"
                        )
                    )
                    span.set_attribute("outcome", outcome)
                raise
            finally:
                span.set_attribute("host_calls", self._host_calls - host_calls_before)
                if hasattr(self.manager, "emit"):
                    self.manager.emit(
                        "repl_output",
                        namespace=self.ns_id,
                        execution_id=execution_id,
                        phase=phase,
                        duration_ms=round((time.monotonic() - started) * 1000),
                        stdout=activity_text(str(response.get("stdout", ""))),
                        stderr=activity_text(str(response.get("stderr", ""))),
                        value=activity_preview(
                            response.get("final") if response.get("final") is not None else response.get("value")
                        ),
                        error=bool(response.get("error")) or not response,
                        error_type=response.get("error_type"),
                    )

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._started:
            try:
                self.manager.drop_namespace(self.ns_id)
            finally:
                self.manager._unregister_interpreter(self)
        if self.owns_manager:
            children = _NATIVE_CHILDREN.get()
            if children is not None:
                children.append(self.manager)
            else:
                # No invocation can attest success; cleanup must not publish outputs.
                self.manager.shutdown()
