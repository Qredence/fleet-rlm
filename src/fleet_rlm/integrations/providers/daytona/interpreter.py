"""Daytona-backed interpreter compatible with the shared ReAct + RLM runtime."""

from __future__ import annotations

import json
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

import dspy
from dspy.primitives import CodeInterpreterError, FinalOutput

from fleet_rlm.runtime.execution.interpreter_events import (
    complete_event_data,
    emit_execution_event,
    start_event_data,
    summarize_code,
)
from fleet_rlm.runtime.execution.profiles import ExecutionProfile
from fleet_rlm.runtime.tools.llm_tools import LLMQueryMixin

from .bridge import DaytonaBridgeExecution, DaytonaToolBridge
from .runtime import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    DaytonaSandboxRuntime,
    DaytonaSandboxSession,
    _await_if_needed,
    _run_async_compat,
)
from .state import dedupe_paths, normalized_context_sources

_FINAL_OUTPUT_MARKER = "__DSPY_FINAL_OUTPUT__"
_DAYTONA_SANDBOX_NATIVE_TOOL_NAMES: frozenset[str] = frozenset(
    {"run", "workspace_write", "workspace_read"}
)


def _base_setup_code(*, workspace_path: str, volume_mount_path: str) -> str:
    return f"""
import ast as _ast
import glob as _glob
import json as _json
import os as _os
import pathlib as _pathlib
import re as _re
import subprocess as _subprocess
import fcntl as _fcntl
import glob as _glob
from contextlib import contextmanager as _contextmanager

REPO_PATH = {workspace_path!r}
MEMORY_ROOT = _pathlib.Path({volume_mount_path!r})
WORKSPACE_VOLUME_ROOT = MEMORY_ROOT / "workspace"
_FINAL_OUTPUT_MARKER = {_FINAL_OUTPUT_MARKER!r}
_buffers = globals().get("_buffers", {{}})

def resolve_path(path: str) -> str:
    candidate = _pathlib.Path(str(path or "").strip() or ".")
    if candidate.is_absolute():
        return str(candidate)
    return str(_pathlib.Path(REPO_PATH) / candidate)

def run(command: str, cwd: str | None = None) -> dict[str, object]:
    completed = _subprocess.run(
        command,
        shell=True,
        cwd=resolve_path(cwd) if cwd else REPO_PATH,
        capture_output=True,
        text=True,
    )
    return {{
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "ok": completed.returncode == 0,
    }}

def read_file(path: str) -> str:
    with open(resolve_path(path), "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()

def list_files(path: str = ".") -> list[str]:
    target = _pathlib.Path(resolve_path(path))
    if not target.exists():
        return []
    return sorted(str(item) for item in target.iterdir())

def find_files(path: str = ".", pattern: str = "*") -> list[str]:
    target = _pathlib.Path(resolve_path(path))
    if not target.exists():
        return []
    return sorted(_glob.glob(str(target / pattern), recursive=True))

def peek(text: str, start: int = 0, length: int = 2000) -> str:
    source = str(text or "")
    start_idx = max(0, int(start))
    window = max(0, int(length))
    return source[start_idx : start_idx + window]

def grep(text: str, pattern: str, *, context: int = 0) -> list[str]:
    if not text:
        return []
    compiled = _re.compile(pattern)
    lines = str(text).splitlines()
    radius = max(0, int(context))
    results: list[str] = []
    for index, line in enumerate(lines):
        if not compiled.search(line):
            continue
        start_idx = max(0, index - radius)
        end_idx = min(len(lines), index + radius + 1)
        results.append("\\n".join(lines[start_idx:end_idx]))
    return results

def extract_python_ast(path: str) -> str:
    target = _pathlib.Path(resolve_path(path))
    if not target.exists():
        return "File not found."
    with open(target, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()
    try:
        tree = _ast.parse(source)
    except Exception as e:
        return f"AST Parse Error: {{e}}"
    results = []
    for node in tree.body:
        if isinstance(node, _ast.ClassDef):
            methods = [m.name for m in node.body if isinstance(m, _ast.FunctionDef)]
            doc = _ast.get_docstring(node) or ""
            results.append({{"type": "Class", "name": node.name, "methods": methods, "doc": doc[:200]}})
        elif isinstance(node, _ast.FunctionDef):
            doc = _ast.get_docstring(node) or ""
            results.append({{"type": "Function", "name": node.name, "doc": doc[:200]}})
    return _json.dumps(results, indent=2)

_processes = globals().get("_processes", {{}})

def start_background_process(process_id: str, command: str) -> str:
    if process_id in _processes:
        return f"Process {{process_id}} is already running."
    import threading as _threading
    import collections as _collections
    proc = _subprocess.Popen(
        command, shell=True, cwd=REPO_PATH,
        stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True
    )
    log_buffer = _collections.deque(maxlen=1000)
    def _read_output():
        for line in proc.stdout:
            log_buffer.append(line)
    t = _threading.Thread(target=_read_output, daemon=True)
    t.start()
    _processes[process_id] = {{"proc": proc, "logs": log_buffer}}
    return f"Started process {{process_id}} (PID {{proc.pid}})"

def read_process_logs(process_id: str, tail: int = 50) -> str:
    if process_id not in _processes:
        return f"Process {{process_id}} is not running."
    pinfo = _processes[process_id]
    proc = pinfo["proc"]
    logs = pinfo["logs"]
    status = "RUNNING" if proc.poll() is None else f"EXITED({{proc.returncode}})"
    lines = list(logs)[-tail:]
    return f"Status: {{status}}\\nLogs:\\n" + "".join(lines)

def kill_process(process_id: str) -> str:
    if process_id not in _processes:
        return f"Process {{process_id}} is not running."
    proc = _processes.pop(process_id)["proc"]
    if proc.poll() is None:
        proc.terminate()
        return f"Terminated process {{process_id}}."
    return f"Process {{process_id}} was already exited."


def add_buffer(name: str, item: object) -> dict[str, object]:
    key = str(name or "").strip() or "default"
    items = _buffers.setdefault(key, [])
    items.append(item)
    return {{"status": "ok", "name": key, "count": len(items)}}

def get_buffer(name: str) -> list[object]:
    key = str(name or "").strip() or "default"
    return list(_buffers.get(key, []))

def clear_buffer(name: str | None = None) -> dict[str, object]:
    key = str(name or "").strip()
    if key:
        _buffers.pop(key, None)
        return {{"status": "ok", "scope": "single", "name": key}}
    _buffers.clear()
    return {{"status": "ok", "scope": "all"}}

def _resolve_persistent_path(path: str, *, default_root: _pathlib.Path) -> tuple[str | None, str | None]:
    raw = str(path or "").strip()
    if not raw:
        return None, f"[error: volume path cannot be empty]"
    if not MEMORY_ROOT.exists():
        return None, f"[error: no volume mounted at {{MEMORY_ROOT}}]"
    candidate = _pathlib.Path(raw)
    if candidate.is_absolute():
        resolved = _pathlib.Path(_os.path.realpath(_os.path.normpath(str(candidate))))
    else:
        resolved = _pathlib.Path(_os.path.realpath(_os.path.normpath(str(default_root / candidate))))
    memory_real = _pathlib.Path(_os.path.realpath(str(MEMORY_ROOT)))
    if resolved != memory_real and not str(resolved).startswith(str(memory_real) + _os.sep):
        return None, f"[error: invalid volume path: {{raw}}]"
    return str(resolved), None

def save_to_volume(path: str, content: str) -> str:
    full, path_error = _resolve_persistent_path(path, default_root=MEMORY_ROOT)
    if path_error is not None or full is None:
        return path_error or "[error: invalid volume path]"
    lock_path = full + ".lock"
    _os.makedirs(_os.path.dirname(full) or str(MEMORY_ROOT), exist_ok=True)
    fd = _os.open(lock_path, _os.O_CREAT | _os.O_RDWR)
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(str(content))
    finally:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
        _os.close(fd)
    return full

def load_from_volume(path: str) -> str:
    full, path_error = _resolve_persistent_path(path, default_root=MEMORY_ROOT)
    if path_error is not None or full is None:
        return path_error or "[error: invalid volume path]"
    if not _os.path.isfile(full):
        return f"[error: file not found: {{full}}]"
    with open(full, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()

def workspace_write(path: str, content: str) -> str:
    full, path_error = _resolve_persistent_path(path, default_root=WORKSPACE_VOLUME_ROOT)
    if path_error is not None or full is None:
        return path_error or "[error: invalid workspace path]"
    lock_path = full + ".lock"
    _os.makedirs(_os.path.dirname(full) or str(WORKSPACE_VOLUME_ROOT), exist_ok=True)
    fd = _os.open(lock_path, _os.O_CREAT | _os.O_RDWR)
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(str(content))
    finally:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
        _os.close(fd)
    return full

def workspace_read(path: str) -> str:
    full, path_error = _resolve_persistent_path(path, default_root=WORKSPACE_VOLUME_ROOT)
    if path_error is not None or full is None:
        return path_error or "[error: invalid workspace path]"
    if not _os.path.isfile(full):
        return f"[error: file not found: {{full}}]"
    with open(full, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()

def workspace_append(path: str, content: str) -> str:
    full, path_error = _resolve_persistent_path(path, default_root=WORKSPACE_VOLUME_ROOT)
    if path_error is not None or full is None:
        return path_error or "[error: invalid workspace path]"
    lock_path = full + ".lock"
    _os.makedirs(_os.path.dirname(full) or str(WORKSPACE_VOLUME_ROOT), exist_ok=True)
    fd = _os.open(lock_path, _os.O_CREAT | _os.O_RDWR)
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
        with open(full, "a", encoding="utf-8") as handle:
            handle.write(str(content))
    finally:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
        _os.close(fd)
    return full

class _FleetFinalOutput(Exception):
    def __init__(self, value):
        self.value = value
        super().__init__("Final output submitted")

def SUBMIT(**kwargs):
    print(f"{{_FINAL_OUTPUT_MARKER}}{{_json.dumps(kwargs, ensure_ascii=False)}}{{_FINAL_OUTPUT_MARKER}}")
    raise _FleetFinalOutput(kwargs)
""".strip()


def _typed_submit_code(output_fields: list[dict[str, Any]]) -> str:
    sig_parts: list[str] = []
    dict_parts: list[str] = []
    for field in output_fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        part = name
        type_hint = str(field.get("type") or "").strip()
        if type_hint:
            part += f": {type_hint}"
        sig_parts.append(part)
        dict_parts.append(f'"{name}": {name}')
    signature = ", ".join(sig_parts)
    payload = ", ".join(dict_parts)
    return f"""
def SUBMIT({signature}):
    result = {{{payload}}}
    print(f"{{_FINAL_OUTPUT_MARKER}}{{_json.dumps(result, ensure_ascii=False)}}{{_FINAL_OUTPUT_MARKER}}")
    raise _FleetFinalOutput(result)
""".strip()


@dataclass(slots=True)
class _DaytonaExecutionResponse:
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    final_artifact: dict[str, Any] | None = None
    callback_count: int = 0


class DaytonaInterpreter(LLMQueryMixin):
    """Stateful Daytona interpreter that plugs into canonical ``dspy.RLM`` flows."""

    def __init__(
        self,
        *,
        runtime: DaytonaSandboxRuntime | None = None,
        owns_runtime: bool = False,
        timeout: int = 900,
        execute_timeout: int | None = None,
        volume_name: str | None = None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
        context_paths: list[str] | None = None,
        delete_session_on_shutdown: bool = True,
        sub_lm: dspy.LM | None = None,
        max_llm_calls: int = 50,
        llm_call_timeout: int = 60,
        default_execution_profile: ExecutionProfile = ExecutionProfile.RLM_DELEGATE,
        async_execute: bool = True,
    ) -> None:
        provided_runtime = runtime
        self.runtime = provided_runtime or DaytonaSandboxRuntime()
        self._owns_runtime = owns_runtime or provided_runtime is None
        self._runtime_config = getattr(self.runtime, "_resolved_config", None)
        self._runtime_closed = False
        self.timeout = timeout
        self.execute_timeout = execute_timeout or timeout
        self.volume_name = volume_name
        self.volume_mount_path = str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
        self.repo_url = repo_url
        self.repo_ref = repo_ref
        self.context_paths = dedupe_paths(list(context_paths or []))
        self.delete_session_on_shutdown = delete_session_on_shutdown
        self.default_execution_profile = default_execution_profile
        self.async_execute = async_execute

        self.sub_lm = sub_lm
        self.max_llm_calls = max_llm_calls
        self.llm_call_timeout = llm_call_timeout
        self._llm_call_count = 0
        self._llm_call_lock = threading.Lock()
        self._sub_lm_executor = None
        self._sub_lm_executor_lock = threading.Lock()

        self.output_fields: list[dict[str, Any]] | None = None
        self._tools: dict[str, Callable[..., Any]] = {}
        self.execution_event_callback: Callable[[dict[str, Any]], None] | None = None
        self._volume = None

        self._started = False
        self._session: DaytonaSandboxSession | None = None
        self._session_source_key: (
            tuple[str | None, str | None, tuple[str, ...], str | None] | None
        ) = None
        self._persisted_sandbox_id: str | None = None
        self._persisted_workspace_path: str | None = None
        self._persisted_context_sources: list[Any] = []
        self._persisted_context_id: str | None = None
        self._bridge: DaytonaToolBridge | None = None
        self._bridge_sandbox_id: str | None = None
        self._bridge_context_id: str | None = None
        self._setup_context_id: str | None = None
        self._submit_signature_key: tuple[tuple[str, str], ...] | None = None

    def __enter__(self) -> DaytonaInterpreter:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        _ = (exc_type, exc_val, exc_tb)
        self.shutdown()
        return False

    async def __aenter__(self) -> DaytonaInterpreter:
        await self.astart()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        _ = (exc_type, exc_val, exc_tb)
        await self.ashutdown()
        return False

    @property
    def tools(self) -> dict[str, Callable[..., Any]]:
        return self._tools

    @tools.setter
    def tools(self, value: dict[str, Callable[..., Any]]) -> None:
        self._tools = value

    @contextmanager
    def execution_profile(self, profile: ExecutionProfile):
        previous = self.default_execution_profile
        self.default_execution_profile = profile
        try:
            yield self
        finally:
            self.default_execution_profile = previous

    def configure_workspace(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        force_new_session: bool = False,
    ) -> None:
        (
            normalized_repo_url,
            normalized_repo_ref,
            normalized_context_paths,
            normalized_volume,
            source_key,
        ) = self._normalized_workspace_config(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=context_paths,
            volume_name=volume_name,
        )
        if force_new_session or (
            self._session is not None and self._session_source_key != source_key
        ):
            self._detach_session(delete=True)
        self._apply_workspace_config(
            repo_url=normalized_repo_url,
            repo_ref=normalized_repo_ref,
            context_paths=normalized_context_paths,
            volume_name=normalized_volume,
        )

    async def aconfigure_workspace(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        force_new_session: bool = False,
    ) -> None:
        (
            normalized_repo_url,
            normalized_repo_ref,
            normalized_context_paths,
            normalized_volume,
            source_key,
        ) = self._normalized_workspace_config(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=context_paths,
            volume_name=volume_name,
        )
        if force_new_session or (
            self._session is not None and self._session_source_key != source_key
        ):
            await self._adetach_session(delete=True)
        self._apply_workspace_config(
            repo_url=normalized_repo_url,
            repo_ref=normalized_repo_ref,
            context_paths=normalized_context_paths,
            volume_name=normalized_volume,
        )

    def _normalized_workspace_config(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
    ) -> tuple[
        str | None,
        str | None,
        list[str],
        str | None,
        tuple[str | None, str | None, tuple[str, ...], str | None],
    ]:
        normalized_repo_url = str(repo_url or "").strip() or None
        normalized_repo_ref = str(repo_ref or "").strip() or None
        normalized_context_paths = dedupe_paths(list(context_paths or []))
        normalized_volume = str(volume_name or "").strip() or None
        source_key = (
            normalized_repo_url,
            normalized_repo_ref,
            tuple(normalized_context_paths),
            normalized_volume,
        )
        return (
            normalized_repo_url,
            normalized_repo_ref,
            normalized_context_paths,
            normalized_volume,
            source_key,
        )

    def _apply_workspace_config(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str],
        volume_name: str | None,
    ) -> None:
        self.repo_url = repo_url
        self.repo_ref = repo_ref
        self.context_paths = context_paths
        self.volume_name = volume_name

    def _apply_imported_session_state(self, state: dict[str, Any]) -> None:
        raw_daytona = state.get("daytona", {})
        daytona_state = raw_daytona if isinstance(raw_daytona, dict) else {}
        self.repo_url = str(daytona_state.get("repo_url", "") or "").strip() or None
        self.repo_ref = str(daytona_state.get("repo_ref", "") or "").strip() or None
        self.context_paths = dedupe_paths(
            [str(item) for item in daytona_state.get("context_paths", []) or []]
        )
        self._persisted_sandbox_id = (
            str(daytona_state.get("sandbox_id", "") or "").strip() or None
        )
        self._persisted_workspace_path = (
            str(daytona_state.get("workspace_path", "") or "").strip() or None
        )
        self._persisted_context_sources = normalized_context_sources(
            daytona_state.get("context_sources", [])
        )
        self._persisted_context_id = (
            str(daytona_state.get("context_id", "") or "").strip() or None
        )
        self._session_source_key = None

    def export_session_state(self) -> dict[str, Any]:
        self._persist_session_snapshot()
        context_sources = (
            list(self._session.context_sources)
            if self._session is not None
            else list(self._persisted_context_sources)
        )
        return {
            "daytona": {
                "repo_url": self.repo_url,
                "repo_ref": self.repo_ref,
                "context_paths": list(self.context_paths),
                "sandbox_id": (
                    self._session.sandbox_id
                    if self._session is not None
                    else self._persisted_sandbox_id
                ),
                "workspace_path": (
                    self._session.workspace_path
                    if self._session is not None
                    else self._persisted_workspace_path
                ),
                "context_sources": [
                    item.to_dict() if hasattr(item, "to_dict") else item
                    for item in context_sources
                ],
                "context_id": (
                    self._session.context_id
                    if self._session is not None
                    else self._persisted_context_id
                ),
            }
        }

    def import_session_state(self, state: dict[str, Any]) -> None:
        self._detach_session(delete=False)
        self._apply_imported_session_state(state)

    async def aimport_session_state(self, state: dict[str, Any]) -> None:
        await self._adetach_session(delete=False)
        self._apply_imported_session_state(state)

    def start(self) -> None:
        _run_async_compat(self.astart)

    async def astart(self) -> None:
        if self._started:
            return
        session = await self._aensure_session_impl()
        await session.astart_driver(timeout=float(self.execute_timeout))
        self._started = True

    def shutdown(self) -> None:
        _run_async_compat(self.ashutdown)

    async def ashutdown(self) -> None:
        try:
            await self._adetach_session(delete=self.delete_session_on_shutdown)
        finally:
            self._started = False
            await self._aclose_runtime()

    def _ensure_session_sync(self) -> DaytonaSandboxSession:
        return _run_async_compat(self._aensure_session_impl)

    async def _aensure_session_impl(self) -> DaytonaSandboxSession:
        self._ensure_runtime_available()
        source_key = (
            self.repo_url,
            self.repo_ref,
            tuple(self.context_paths),
            self.volume_name,
        )
        if self._session is not None and self._session_source_key == source_key:
            return self._session

        if self._session is not None:
            await self._adetach_session(delete=True)

        if (
            self._persisted_sandbox_id
            and self._persisted_workspace_path
            and self._session_source_key in {None, source_key}
        ):
            try:
                async_resume = getattr(self.runtime, "aresume_workspace_session", None)
                if async_resume is not None and callable(async_resume):
                    self._session = await _await_if_needed(
                        async_resume(
                            sandbox_id=self._persisted_sandbox_id,
                            repo_url=self.repo_url,
                            ref=self.repo_ref,
                            workspace_path=self._persisted_workspace_path,
                            context_sources=self._persisted_context_sources,
                            context_id=self._persisted_context_id,
                        )
                    )
                else:
                    self._session = await _await_if_needed(
                        self.runtime.resume_workspace_session(
                            sandbox_id=self._persisted_sandbox_id,
                            repo_url=self.repo_url,
                            ref=self.repo_ref,
                            workspace_path=self._persisted_workspace_path,
                            context_sources=self._persisted_context_sources,
                            context_id=self._persisted_context_id,
                        )
                    )
                self._session_source_key = source_key
                await self._areset_execution_state()
                self._persist_session_snapshot()
                return self._session
            except Exception:
                self._persisted_sandbox_id = None
                self._persisted_workspace_path = None
                self._persisted_context_sources = []
                self._persisted_context_id = None

        async_create = getattr(self.runtime, "acreate_workspace_session", None)
        if async_create is not None and callable(async_create):
            self._session = await _await_if_needed(
                async_create(
                    repo_url=self.repo_url,
                    ref=self.repo_ref,
                    context_paths=list(self.context_paths),
                    volume_name=self.volume_name,
                )
            )
        else:
            self._session = await _await_if_needed(
                self.runtime.create_workspace_session(
                    repo_url=self.repo_url,
                    ref=self.repo_ref,
                    context_paths=list(self.context_paths),
                    volume_name=self.volume_name,
                )
            )
        self._session_source_key = source_key
        await self._areset_execution_state()
        self._persist_session_snapshot()
        return self._session

    async def _aensure_session(self) -> DaytonaSandboxSession:
        return await self._aensure_session_impl()

    async def aget_session(self) -> DaytonaSandboxSession:
        """Public async accessor to ensure and return the active sandbox session."""
        return await self._aensure_session()

    def _persist_session_snapshot(
        self, session: DaytonaSandboxSession | None = None
    ) -> None:
        active_session = session or self._session
        if active_session is None:
            return
        self._persisted_sandbox_id = active_session.sandbox_id
        self._persisted_workspace_path = active_session.workspace_path
        self._persisted_context_sources = list(active_session.context_sources)
        self._persisted_context_id = active_session.context_id

    def _detach_session(self, *, delete: bool) -> None:
        _run_async_compat(self._adetach_session, delete=delete)

    async def _adetach_session(self, *, delete: bool) -> None:
        active_session = self._session
        if active_session is None:
            if delete:
                self._persisted_sandbox_id = None
                self._persisted_workspace_path = None
                self._persisted_context_sources = []
                self._persisted_context_id = None
            await self._areset_execution_state()
            self._started = False
            return

        self._persist_session_snapshot(active_session)
        await self._aclose_bridge()
        try:
            if delete:
                async_delete = getattr(active_session, "adelete", None)
                if async_delete is not None and callable(async_delete):
                    await _await_if_needed(async_delete())
                else:
                    await _await_if_needed(active_session.delete())
            else:
                async_close = getattr(active_session, "aclose_driver", None)
                if async_close is not None and callable(async_close):
                    await _await_if_needed(async_close())
                else:
                    await _await_if_needed(active_session.close_driver())
        finally:
            if delete:
                self._persisted_sandbox_id = None
                self._persisted_workspace_path = None
                self._persisted_context_sources = []
                self._persisted_context_id = None
            self._session = None
            self._session_source_key = None
            await self._areset_execution_state()
            self._started = False

    def _close_bridge(self) -> None:
        _run_async_compat(self._aclose_bridge)

    async def _aclose_bridge(self) -> None:
        bridge = self._bridge
        self._bridge = None
        self._bridge_sandbox_id = None
        self._bridge_context_id = None
        if bridge is not None:
            close = getattr(bridge, "aclose", None)
            if close is not None and callable(close):
                await _await_if_needed(close())
            else:
                await _await_if_needed(bridge.close())

    async def _aclose_runtime(self) -> None:
        if not self._owns_runtime or self._runtime_closed:
            return
        close = getattr(self.runtime, "aclose", None)
        if close is not None and callable(close):
            await _await_if_needed(close())
        else:
            close = getattr(self.runtime, "close", None)
            if close is not None and callable(close):
                await _await_if_needed(close())
        self._runtime_closed = True

    def _ensure_runtime_available(self) -> None:
        runtime = self.runtime
        if not self._owns_runtime or not isinstance(runtime, DaytonaSandboxRuntime):
            return
        if getattr(runtime, "_client", None) is not None:
            return
        if self._runtime_config is None:
            raise RuntimeError(
                "Owned Daytona runtime cannot be recreated without config"
            )
        self.runtime = DaytonaSandboxRuntime(config=self._runtime_config)
        self._runtime_closed = False

    def _reset_execution_state(self) -> None:
        _run_async_compat(self._areset_execution_state)

    async def _areset_execution_state(self) -> None:
        await self._aclose_bridge()
        self._setup_context_id = None
        self._submit_signature_key = None

    def build_delegate_child(self, *, remaining_llm_budget: int) -> DaytonaInterpreter:
        runtime = DaytonaSandboxRuntime(config=self.runtime._resolved_config)
        child = DaytonaInterpreter(
            runtime=runtime,
            owns_runtime=True,
            timeout=self.timeout,
            execute_timeout=self.execute_timeout,
            volume_name=self.volume_name,
            repo_url=self.repo_url,
            repo_ref=self.repo_ref,
            context_paths=list(self.context_paths),
            delete_session_on_shutdown=True,
            sub_lm=self.sub_lm,
            max_llm_calls=remaining_llm_budget,
            llm_call_timeout=self.llm_call_timeout,
            default_execution_profile=ExecutionProfile.RLM_DELEGATE,
            async_execute=self.async_execute,
        )
        setattr(
            child, "_check_and_increment_llm_calls", self._check_and_increment_llm_calls
        )
        return child

    def execute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
    ) -> str | FinalOutput:
        return _run_async_compat(
            self.aexecute,
            code,
            variables,
            execution_profile=execution_profile,
        )

    async def aexecute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
    ) -> str | FinalOutput:
        session = await self._aensure_session_impl()
        await session.astart_driver(timeout=float(self.execute_timeout))
        safe_vars = self._safe_variables(variables)
        profile = execution_profile or self.default_execution_profile
        profile_value = profile.value if hasattr(profile, "value") else str(profile)
        code_hash, code_preview = summarize_code(code)
        started_at = time.time()
        emit_execution_event(
            self,
            start_event_data(
                execution_profile=str(profile_value),
                code_hash=code_hash,
                code_preview=code_preview,
            ),
        )
        try:
            response = await self._aexecute_in_session(
                session=session,
                code=code,
                variables=safe_vars,
            )
        except Exception as exc:
            emit_execution_event(
                self,
                complete_event_data(
                    started_at=started_at,
                    execution_profile=str(profile_value),
                    code_hash=code_hash,
                    code_preview=code_preview,
                    success=False,
                    result_kind="exception",
                    error_type=type(exc).__name__,
                    error=str(exc),
                ),
            )
            raise CodeInterpreterError(str(exc)) from exc
        return self._finalize_execution_result(
            response=response,
            started_at=started_at,
            execution_profile=str(profile_value),
            code_hash=code_hash,
            code_preview=code_preview,
        )

    def _safe_variables(self, variables: dict[str, Any] | None) -> dict[str, Any]:
        safe_vars: dict[str, Any] = {}
        for key, value in (variables or {}).items():
            normalized_key = str(key)
            try:
                json.dumps(value)
                safe_vars[normalized_key] = value
            except TypeError:
                safe_vars[normalized_key] = str(value)
        return safe_vars

    def _submit_signature(self) -> tuple[tuple[str, str], ...] | None:
        if not self.output_fields:
            return None
        normalized: list[tuple[str, str]] = []
        for field in self.output_fields:
            name = str(field.get("name") or "").strip()
            if not name:
                continue
            normalized.append((name, str(field.get("type") or "").strip()))
        return tuple(normalized) or None

    async def _aensure_setup(self, session: DaytonaSandboxSession) -> Any:
        context = await session.aensure_context()
        if self._setup_context_id != session.context_id:
            result = await _await_if_needed(
                session.sandbox.code_interpreter.run_code(
                    _base_setup_code(
                        workspace_path=session.workspace_path,
                        volume_mount_path=self.volume_mount_path,
                    ),
                    context=context,
                )
            )
            if result.error:
                raise CodeInterpreterError(
                    f"Failed to initialize Daytona sandbox helpers: {result.error.value}"
                )
            self._setup_context_id = session.context_id
            self._submit_signature_key = None

        submit_signature = self._submit_signature()
        if submit_signature and submit_signature != self._submit_signature_key:
            result = await _await_if_needed(
                session.sandbox.code_interpreter.run_code(
                    _typed_submit_code(self.output_fields or []),
                    context=context,
                )
            )
            if result.error:
                raise CodeInterpreterError(
                    f"Failed to register typed SUBMIT: {result.error.value}"
                )
            self._submit_signature_key = submit_signature
        return context

    async def _aensure_bridge(
        self,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        tools: dict[str, Callable[..., Any]],
    ) -> DaytonaToolBridge:
        sandbox_id = session.sandbox_id
        context_id = session.context_id
        bridge = self._bridge
        if (
            bridge is None
            or self._bridge_sandbox_id != sandbox_id
            or self._bridge_context_id != context_id
        ):
            await self._aclose_bridge()
            bridge = DaytonaToolBridge(
                sandbox=session.sandbox,
                context=context,
            )
            self._bridge = bridge
            self._bridge_sandbox_id = sandbox_id
            self._bridge_context_id = context_id
        else:
            bridge.bind_context(context)
        bridge_sync = getattr(bridge, "async_tools", None)
        if bridge_sync is not None and callable(bridge_sync):
            await _await_if_needed(bridge_sync(tools))
        else:
            await _await_if_needed(bridge.sync_tools(tools))
        return bridge

    async def _aexecute_in_session(
        self,
        *,
        session: DaytonaSandboxSession,
        code: str,
        variables: dict[str, Any],
    ) -> _DaytonaExecutionResponse:
        context = await self._aensure_setup(session)
        prepared_code = self._inject_variables(code, variables)
        tools = self._bridge_tools()
        if self._requires_bridge(prepared_code, tools):
            bridge = await self._aensure_bridge(
                session=session,
                context=context,
                tools=tools,
            )
            async_execute = getattr(bridge, "aexecute", None)
            if async_execute is not None and callable(async_execute):
                execution = await _await_if_needed(
                    async_execute(
                        code=prepared_code,
                        timeout=int(self.execute_timeout),
                        tool_executor=self._invoke_tool,
                    )
                )
            else:
                execution = await _await_if_needed(
                    bridge.execute(
                        code=prepared_code,
                        timeout=int(self.execute_timeout),
                        tool_executor=self._invoke_tool,
                    )
                )
        else:
            execution = await self._aexecute_direct(
                session=session,
                context=context,
                code=prepared_code,
            )
        return self._response_from_execution(execution)

    async def _aexecute_direct(
        self,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        code: str,
    ) -> DaytonaBridgeExecution:
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []

        def _on_stdout(message: Any) -> None:
            stdout_parts.append(str(getattr(message, "output", "") or ""))

        def _on_stderr(message: Any) -> None:
            stderr_parts.append(str(getattr(message, "output", "") or ""))

        result = await _await_if_needed(
            session.sandbox.code_interpreter.run_code(
                code,
                context=context,
                on_stdout=_on_stdout,
                on_stderr=_on_stderr,
                timeout=int(self.execute_timeout),
            )
        )
        return DaytonaBridgeExecution(
            result=result,
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            callback_count=0,
        )

    def _bridge_tools(self) -> dict[str, Callable[..., Any]]:
        tools = {
            name: tool
            for name, tool in self._tools.items()
            if name not in _DAYTONA_SANDBOX_NATIVE_TOOL_NAMES
        }
        tools["llm_query"] = self.llm_query
        tools["llm_query_batched"] = self.llm_query_batched
        tools["rlm_query"] = self.llm_query
        tools["rlm_query_batched"] = self.llm_query_batched
        return tools

    def _requires_bridge(self, code: str, tools: dict[str, Callable[..., Any]]) -> bool:
        for tool_name in tools:
            if f"{tool_name}(" in code:
                return True
        return False

    def _invoke_tool(
        self,
        name: str,
        args: list[Any],
        kwargs: dict[str, Any],
    ) -> Any:
        try:
            if name in {"llm_query", "rlm_query"}:
                prompt = args[0] if args else kwargs.get("prompt", "")
                value = self.llm_query(str(prompt))
            elif name in {"llm_query_batched", "rlm_query_batched"}:
                prompts = args[0] if args else kwargs.get("prompts", [])
                if not isinstance(prompts, list):
                    prompts = []
                value = self.llm_query_batched([str(item) for item in prompts])
            elif name in self._tools:
                value = self._tools[name](*args, **kwargs)
            else:
                raise RuntimeError(f"Unknown host callback: {name}")
            try:
                json.dumps(value)
                return value
            except TypeError:
                return str(value)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def _response_from_execution(
        self,
        execution: DaytonaBridgeExecution,
    ) -> _DaytonaExecutionResponse:
        final_artifact = self._extract_final_artifact(execution.stdout)
        result = execution.result
        error = getattr(result, "error", None)
        if error is None:
            return _DaytonaExecutionResponse(
                stdout=execution.stdout,
                stderr=execution.stderr,
                final_artifact=final_artifact,
                callback_count=execution.callback_count,
            )

        error_name = str(getattr(error, "name", "") or "")
        error_value = str(getattr(error, "value", "") or "")
        if error_name == "_FleetFinalOutput" and final_artifact is not None:
            return _DaytonaExecutionResponse(
                stdout=execution.stdout,
                stderr=execution.stderr,
                final_artifact=final_artifact,
                callback_count=execution.callback_count,
            )

        error_text = (
            ": ".join(part for part in [error_name, error_value] if part)
            or error_value
            or error_name
            or "Execution failed"
        )
        return _DaytonaExecutionResponse(
            stdout=execution.stdout,
            stderr=execution.stderr,
            error=error_text,
            final_artifact=final_artifact,
            callback_count=execution.callback_count,
        )

    def _extract_final_artifact(self, stdout: str) -> dict[str, Any] | None:
        marker = _FINAL_OUTPUT_MARKER
        start = stdout.find(marker)
        if start == -1:
            return None
        start += len(marker)
        end = stdout.find(marker, start)
        if end == -1:
            return None
        payload = stdout[start:end]
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return {
            "kind": "structured",
            "value": parsed,
            "finalization_mode": "SUBMIT",
        }

    def _finalize_execution_result(
        self,
        *,
        response: _DaytonaExecutionResponse,
        started_at: float,
        execution_profile: str,
        code_hash: str,
        code_preview: str,
    ) -> str | FinalOutput:
        final_payload = None
        if isinstance(response.final_artifact, dict):
            final_payload = response.final_artifact.get("value")

        stdout_preview = str(response.stdout or "")
        stderr_preview = str(response.stderr or "")
        if response.error:
            error_text = str(response.error)
            emit_execution_event(
                self,
                complete_event_data(
                    started_at=started_at,
                    execution_profile=execution_profile,
                    code_hash=code_hash,
                    code_preview=code_preview,
                    success=False,
                    result_kind="stderr",
                    stdout_preview=stdout_preview or None,
                    stderr_preview=stderr_preview or None,
                    error_type="ExecutionError",
                    error=error_text,
                ),
            )
            combined = stdout_preview.strip()
            return f"{combined}\n{error_text}" if combined else error_text

        if final_payload is not None:
            output_keys = (
                list(final_payload.keys())[:50]
                if isinstance(final_payload, dict)
                else None
            )
            emit_execution_event(
                self,
                complete_event_data(
                    started_at=started_at,
                    execution_profile=execution_profile,
                    code_hash=code_hash,
                    code_preview=code_preview,
                    success=True,
                    result_kind="final_output",
                    output_keys=output_keys,
                    stdout_preview=stdout_preview or None,
                    stderr_preview=stderr_preview or None,
                ),
            )
            return FinalOutput(final_payload)

        emit_execution_event(
            self,
            complete_event_data(
                started_at=started_at,
                execution_profile=execution_profile,
                code_hash=code_hash,
                code_preview=code_preview,
                success=not bool(stderr_preview),
                result_kind="stderr" if stderr_preview else "stdout",
                stdout_preview=stdout_preview or None,
                stderr_preview=stderr_preview or None,
            ),
        )
        if stderr_preview:
            combined = stdout_preview.strip()
            return f"{combined}\n{stderr_preview}" if combined else stderr_preview
        return stdout_preview

    def _inject_variables(self, code: str, variables: dict[str, Any]) -> str:
        if not variables:
            return code
        assignments = [
            f"{name} = {self._literal(value)}" for name, value in variables.items()
        ]
        return "\n".join(assignments) + "\n" + code

    def _literal(self, value: Any) -> str:
        if value is None:
            return "None"
        if isinstance(value, bool):
            return "True" if value else "False"
        if isinstance(value, int):
            return repr(value)
        if isinstance(value, float):
            if math.isnan(value):
                return "float('nan')"
            if math.isinf(value):
                return "float('inf')" if value > 0 else "float('-inf')"
            return repr(value)
        if isinstance(value, str):
            return repr(value)
        if isinstance(value, list):
            return "[" + ", ".join(self._literal(item) for item in value) + "]"
        if isinstance(value, tuple):
            inner = ", ".join(self._literal(item) for item in value)
            if len(value) == 1:
                inner += ","
            return "(" + inner + ")"
        if isinstance(value, set):
            if not value:
                return "set()"
            return "{" + ", ".join(self._literal(item) for item in value) + "}"
        if isinstance(value, dict):
            pairs = [
                f"{self._literal(key)}: {self._literal(item)}"
                for key, item in value.items()
            ]
            return "{" + ", ".join(pairs) + "}"
        raise CodeInterpreterError(f"Unsupported value type: {type(value).__name__}")


__all__ = ["DaytonaInterpreter"]
