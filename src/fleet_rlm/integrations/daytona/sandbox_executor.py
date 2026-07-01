"""Execution and code-sanitization helpers for the Daytona interpreter."""

from __future__ import annotations

import ast
import json
import math
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from dspy.primitives import CodeInterpreterError, FinalOutput

from fleet_rlm.runtime.execution.interpreter_protocol import ExecutionProfile
from fleet_rlm.runtime.execution.interpreter_support import (
    SupportsExecutionEventCallback,
    complete_event_data,
    emit_execution_event,
    start_event_data,
    summarize_code,
)

from ._sandbox_constants import (
    _DAYTONA_SANDBOX_NATIVE_TOOL_NAMES,
    _FINAL_OUTPUT_MARKER,
    _UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS,
)
from .async_compat import _run_sync_in_thread
from .bridge import DaytonaBridgeExecution, DaytonaToolBridge
from .bridge_callbacks import (
    bridge_tools,
    invoke_tool,
    reject_unsupported_recursive_callbacks,
    requires_bridge,
)
from .session_runtime import DaytonaSandboxSession

_BROKER_START_FAILURE_COOLDOWN_SECONDS = 300.0
_BROKER_START_FAILURES: dict[str, tuple[float, str]] = {}


def _generic_submit_code() -> str:
    return """
def SUBMIT(**kwargs):
    print(f"{_FINAL_OUTPUT_MARKER}{_json.dumps(kwargs, ensure_ascii=False)}{_FINAL_OUTPUT_MARKER}")
    raise _FleetFinalOutput(kwargs)
""".strip()


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
from contextlib import contextmanager as _contextmanager

REPO_PATH = {workspace_path!r}
MEMORY_ROOT = _pathlib.Path({volume_mount_path!r})
_FINAL_OUTPUT_MARKER = {_FINAL_OUTPUT_MARKER!r}
_buffers = globals().get("_buffers", {{}})
_os.makedirs(REPO_PATH, exist_ok=True)
_os.chdir(REPO_PATH)

def resolve_path(path: str) -> str:
    candidate = _pathlib.Path(str(path or "").strip() or ".")
    if candidate.is_absolute():
        return str(candidate)
    return str(_pathlib.Path(REPO_PATH) / candidate)

def _resolve_workspace_path(path: str) -> tuple[str | None, str | None]:
    raw = str(path or "").strip()
    if not raw:
        return None, "[error: workspace path cannot be empty]"
    candidate = _pathlib.Path(raw)
    if candidate.is_absolute():
        return None, f"[error: invalid workspace path: {{raw}}]"
    repo_real = _pathlib.Path(_os.path.realpath(REPO_PATH))
    resolved = _pathlib.Path(
        _os.path.realpath(_os.path.normpath(str(repo_real / candidate)))
    )
    if resolved != repo_real and not str(resolved).startswith(str(repo_real) + _os.sep):
        return None, f"[error: invalid workspace path: {{raw}}]"
    return str(resolved), None

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

def list_files(path: str = ".", pattern: str | None = None) -> list[str]:
    target = _pathlib.Path(resolve_path(path))
    if not target.exists():
        return []
    if pattern:
        return sorted(str(item) for item in target.glob(str(pattern)))
    return sorted(str(item) for item in target.iterdir())

def find_files(path: str = ".", pattern: str = "*") -> list[str]:
    target = _pathlib.Path(resolve_path(path))
    if not target.exists():
        return []
    return sorted(_glob.glob(str(target / pattern), recursive=True))

def sandbox_list_files(path: str = ".", pattern: str | None = None) -> list[str]:
    return list_files(path, pattern)

def sandbox_read_file(path: str) -> str:
    return read_file(path)

def sandbox_search_files(path: str = ".", pattern: str | None = None) -> list[str]:
    raw_path = str(path or ".")
    raw_pattern = "*" if pattern is None else str(pattern or "*")
    if pattern is None and raw_path not in {{"", "."}}:
        candidate = _pathlib.Path(resolve_path(raw_path))
        if candidate.is_file():
            return [str(candidate)]
        if not candidate.exists():
            return find_files(".", raw_path)
    return find_files(raw_path or ".", raw_pattern)

def sandbox_find_in_files(path: str = ".", pattern: str = "") -> list[dict[str, object]]:
    if not pattern:
        return []
    hits: list[dict[str, object]] = []
    for file_path in find_files(path, "**/*"):
        target = _pathlib.Path(file_path)
        if not target.is_file():
            continue
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            if _re.search(pattern, line):
                hits.append({{"file": str(target), "line": line_no, "content": line}})
    return hits

def get_workspace_context() -> dict[str, object]:
    manifest_path = _pathlib.Path(REPO_PATH) / ".fleet-rlm" / "context" / "manifest.json"
    manifest: dict[str, object] = {{}}
    staged_paths: list[str] = []
    if manifest_path.exists():
        try:
            manifest = _json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            manifest = {{}}
        sources = manifest.get("context_sources") if isinstance(manifest, dict) else None
        for source in (sources if isinstance(sources, list) else []):
            if isinstance(source, dict):
                staged = source.get("staged_path")
                if staged:
                    staged_paths.append(str(staged))
    return {{
        "document_text": "",
        "context_paths": staged_paths,
        "manifest": manifest,
        "metadata": {{"sandbox_staged_paths": staged_paths}},
    }}

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
    full, path_error = _resolve_workspace_path(path)
    if path_error is not None or full is None:
        return path_error or "[error: invalid workspace path]"
    lock_path = full + ".lock"
    _os.makedirs(_os.path.dirname(full) or REPO_PATH, exist_ok=True)
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
    full, path_error = _resolve_workspace_path(path)
    if path_error is not None or full is None:
        return path_error or "[error: invalid workspace path]"
    if not _os.path.isfile(full):
        return f"[error: file not found: {{full}}]"
    with open(full, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()

def workspace_append(path: str, content: str) -> str:
    full, path_error = _resolve_workspace_path(path)
    if path_error is not None or full is None:
        return path_error or "[error: invalid workspace path]"
    lock_path = full + ".lock"
    _os.makedirs(_os.path.dirname(full) or REPO_PATH, exist_ok=True)
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

{_generic_submit_code()}
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
class DaytonaExecutionResponse:
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    final_artifact: dict[str, Any] | None = None
    callback_count: int = 0


@dataclass(slots=True)
class ExecutionCallbacks:
    bridge_tools: Callable[[], dict[str, Callable[..., Any]]]
    reject_recursive_callbacks: Callable[[str], None]
    requires_bridge: Callable[[str, dict[str, Callable[..., Any]]], bool]
    ensure_bridge: Callable[..., Any]
    execute_direct: Callable[..., Any]
    response_from_execution: Callable[[DaytonaBridgeExecution], DaytonaExecutionResponse]


class CodeSanitizationError(Exception):
    """Raised when model-emitted code cannot be made executable."""


_DSPY_FIELD_SENTINEL_RE = re.compile(r"\[\[\s*##\s*[\w-]+\s*##\s*\]\]")
_PYTHON_FENCE_RE = re.compile(
    r"^\s*(?:Code:\s*)?```(?:python|py)?\s*\n(?P<body>.*?)(?:\n```)?\s*$",
    re.IGNORECASE | re.DOTALL,
)


class DaytonaExecutionOwner(SupportsExecutionEventCallback, Protocol):
    timeout: int
    execute_timeout: int | None
    broker_health_timeout: float
    broker_tool_call_timeout: float
    broker_start_retries: int
    output_fields: list[dict[str, Any]] | None
    volume_mount_path: str
    _setup_context_id: str | None
    _setup_workspace_path: str | None
    _submit_signature_key: tuple[tuple[str, str], ...] | None
    _bridge: DaytonaToolBridge | None
    _bridge_sandbox_id: str | None
    _bridge_context_id: str | None
    _bridge_start_error: str | None
    _bridge_tools: Callable[..., Any]
    _invoke_tool: Callable[..., Any]
    _reject_unsupported_recursive_callbacks: Callable[..., None]
    _requires_bridge: Callable[..., bool]

    def _close_bridge(self: Any) -> None:
        pass

    def ensure_bridge(
        self: Any,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        tools: dict[str, Callable[..., Any]],
        bridge_cls: type[DaytonaToolBridge] | None = None,
    ) -> DaytonaToolBridge:
        pass

    def execute_direct(
        self: Any,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        code: str,
        envs: dict[str, str] | None = None,
    ) -> DaytonaBridgeExecution:
        pass

    def response_from_execution(
        self: Any,
        execution: DaytonaBridgeExecution,
        *,
        extract_final_artifact_fn: Callable[[str], dict[str, Any] | None] | None = None,
    ) -> DaytonaExecutionResponse:
        pass


class ExecutorWorkspace(Protocol):
    """Workspace/session surface needed by ``SandboxExecutor``."""

    def ensure_session(self) -> DaytonaSandboxSession:
        pass


class SandboxCallbackOwner(Protocol):
    """Interpreter facade surface used by bridge callback dispatch."""

    _tools: dict[str, Callable[..., Any]]

    def llm_query(self, prompt: str, context: str = "") -> Any:
        pass

    def llm_query_batched(self, prompts: list[str], context: str = "") -> Any:
        pass


def python_parses(code: str) -> bool:
    try:
        ast.parse(code)
    except SyntaxError:
        return False
    return True


def strip_trailing_fence(code: str) -> str:
    lines = str(code or "").strip().splitlines()
    while lines and lines[-1].strip() in {"```", "```python", "```py"}:
        lines.pop()
    return "\n".join(lines).strip()


def strip_dspy_sentinel_lines(code: str) -> str:
    cleaned: list[str] = []
    for line in str(code or "").splitlines():
        match = _DSPY_FIELD_SENTINEL_RE.search(line)
        if match is None:
            if line.strip() not in {"```", "```python", "```py", "Code:"}:
                cleaned.append(line)
            continue
        prefix = line[: match.start()].rstrip()
        if prefix and prefix not in {"]]", "```"}:
            cleaned.append(prefix)
    return "\n".join(cleaned).strip()


def safe_variables(variables: dict[str, Any] | None) -> dict[str, Any]:
    safe_vars: dict[str, Any] = {}
    for key, value in (variables or {}).items():
        normalized_key = str(key)
        try:
            json.dumps(value)
            safe_vars[normalized_key] = value
        except (TypeError, ValueError, RecursionError):
            safe_vars[normalized_key] = str(value)
    return safe_vars


def submit_signature(
    output_fields: list[dict[str, Any]] | None,
) -> tuple[tuple[str, str], ...] | None:
    if not output_fields:
        return None
    normalized: list[tuple[str, str]] = []
    for field in output_fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        normalized.append((name, str(field.get("type") or "").strip()))
    return tuple(normalized) or None


def ensure_setup(
    owner: DaytonaExecutionOwner,
    session: DaytonaSandboxSession,
    *,
    base_setup_code: Callable[..., str] = _base_setup_code,
    generic_submit_code: Callable[[], str] = _generic_submit_code,
    typed_submit_code: Callable[[list[dict[str, Any]]], str] = _typed_submit_code,
    submit_signature_fn: Callable[[], tuple[tuple[str, str], ...] | None],
) -> Any:
    context = session.ensure_context()
    if owner._setup_context_id != session.context_id or owner._setup_workspace_path != session.workspace_path:
        result = session.sandbox.code_interpreter.run_code(
            base_setup_code(
                workspace_path=session.workspace_path,
                volume_mount_path=owner.volume_mount_path,
            ),
            context=context,
        )
        if result.error:
            raise CodeInterpreterError(f"Failed to initialize Daytona sandbox helpers: {result.error.value}")
        owner._setup_context_id = session.context_id
        owner._setup_workspace_path = session.workspace_path
        owner._submit_signature_key = None

    current_submit_signature = submit_signature_fn()
    if current_submit_signature is None:
        if owner._submit_signature_key is not None:
            result = session.sandbox.code_interpreter.run_code(
                generic_submit_code(),
                context=context,
            )
            if result.error:
                raise CodeInterpreterError(f"Failed to restore generic SUBMIT: {result.error.value}")
            owner._submit_signature_key = None
        return context

    if current_submit_signature != owner._submit_signature_key:
        result = session.sandbox.code_interpreter.run_code(
            typed_submit_code(owner.output_fields or []),
            context=context,
        )
        if result.error:
            raise CodeInterpreterError(f"Failed to register typed SUBMIT: {result.error.value}")
        owner._submit_signature_key = current_submit_signature
    return context


async def aensure_setup(
    owner: DaytonaExecutionOwner,
    session: DaytonaSandboxSession,
    *,
    submit_signature_fn: Callable[[], tuple[tuple[str, str], ...] | None],
) -> Any:
    return await _run_sync_in_thread(
        ensure_setup,
        owner,
        session,
        submit_signature_fn=submit_signature_fn,
    )


def _ensure_bridge(
    owner: DaytonaExecutionOwner,
    *,
    session: DaytonaSandboxSession,
    context: Any,
    tools: dict[str, Callable[..., Any]],
    bridge_cls: type[DaytonaToolBridge] | None = None,
) -> DaytonaToolBridge:
    if bridge_cls is None:
        bridge_cls = DaytonaToolBridge
    sandbox_id = session.sandbox_id
    context_id = session.context_id
    bridge = owner._bridge
    if bridge is None or owner._bridge_sandbox_id != sandbox_id or owner._bridge_context_id != context_id:
        owner._close_bridge()
        bridge = bridge_cls(
            sandbox=session.sandbox,
            context=context,
            broker_health_timeout=float(getattr(owner, "broker_health_timeout", 60.0)),
            broker_tool_call_timeout=float(getattr(owner, "broker_tool_call_timeout", 180.0)),
            broker_start_retries=int(getattr(owner, "broker_start_retries", 1)),
        )
        owner._bridge = bridge
        owner._bridge_sandbox_id = sandbox_id
        owner._bridge_context_id = context_id
    else:
        bridge.bind_context(context)
    bridge.register_tools(tools)
    return bridge


async def aensure_bridge(
    owner: DaytonaExecutionOwner,
    *,
    session: DaytonaSandboxSession,
    context: Any,
    tools: dict[str, Callable[..., Any]],
    bridge_cls: type[DaytonaToolBridge] | None = None,
) -> DaytonaToolBridge:
    return await _run_sync_in_thread(
        _ensure_bridge,
        owner,
        session=session,
        context=context,
        tools=tools,
        bridge_cls=bridge_cls,
    )


def execute_in_session(
    owner: DaytonaExecutionOwner,
    *,
    session: DaytonaSandboxSession,
    code: str,
    variables: dict[str, Any],
    envs: dict[str, str] | None = None,
    bridge_tools_fn: Callable[[], dict[str, Callable[..., Any]]] | None = None,
    reject_unsupported_recursive_callbacks_fn: Callable[[str], None] | None = None,
    requires_bridge_fn: Callable[[str, dict[str, Callable[..., Any]]], bool] | None = None,
    ensure_bridge_fn: Callable[..., Any] | None = None,
    execute_direct_fn: Callable[..., Any] | None = None,
    response_from_execution_fn: Callable[[DaytonaBridgeExecution], DaytonaExecutionResponse] | None = None,
) -> DaytonaExecutionResponse:
    callbacks = resolve_execution_callbacks(
        owner,
        bridge_tools_fn=bridge_tools_fn,
        reject_unsupported_recursive_callbacks_fn=reject_unsupported_recursive_callbacks_fn,
        requires_bridge_fn=requires_bridge_fn,
        ensure_bridge_fn=ensure_bridge_fn,
        execute_direct_fn=execute_direct_fn,
        response_from_execution_fn=response_from_execution_fn,
    )
    context = ensure_setup(
        owner,
        session,
        submit_signature_fn=lambda: submit_signature(owner.output_fields),
    )
    prepared_code = prepare_execution_code(
        owner,
        code=code,
        variables=variables,
        reject_recursive_callbacks=callbacks.reject_recursive_callbacks,
    )
    execution = run_prepared_execution(
        owner,
        session=session,
        context=context,
        code=prepared_code,
        callbacks=callbacks,
        envs=envs,
    )
    return callbacks.response_from_execution(execution)


async def aexecute_in_session(
    owner: DaytonaExecutionOwner,
    *,
    session: DaytonaSandboxSession,
    code: str,
    variables: dict[str, Any],
    envs: dict[str, str] | None = None,
    bridge_tools_fn: Callable[[], dict[str, Callable[..., Any]]] | None = None,
    reject_unsupported_recursive_callbacks_fn: Callable[[str], None] | None = None,
    requires_bridge_fn: Callable[[str, dict[str, Callable[..., Any]]], bool] | None = None,
    ensure_bridge_fn: Callable[..., Any] | None = None,
    execute_direct_fn: Callable[..., Any] | None = None,
    response_from_execution_fn: Callable[[DaytonaBridgeExecution], DaytonaExecutionResponse] | None = None,
) -> DaytonaExecutionResponse:
    return await _run_sync_in_thread(
        execute_in_session,
        owner,
        session=session,
        code=code,
        variables=variables,
        envs=envs,
        bridge_tools_fn=bridge_tools_fn,
        reject_unsupported_recursive_callbacks_fn=reject_unsupported_recursive_callbacks_fn,
        requires_bridge_fn=requires_bridge_fn,
        ensure_bridge_fn=ensure_bridge_fn,
        execute_direct_fn=execute_direct_fn,
        response_from_execution_fn=response_from_execution_fn,
    )


def resolve_execution_callbacks(
    owner: DaytonaExecutionOwner,
    *,
    bridge_tools_fn: Callable[[], dict[str, Callable[..., Any]]] | None = None,
    reject_unsupported_recursive_callbacks_fn: Callable[[str], None] | None = None,
    requires_bridge_fn: Callable[[str, dict[str, Callable[..., Any]]], bool] | None = None,
    ensure_bridge_fn: Callable[..., Any] | None = None,
    execute_direct_fn: Callable[..., Any] | None = None,
    response_from_execution_fn: Callable[[DaytonaBridgeExecution], DaytonaExecutionResponse] | None = None,
) -> ExecutionCallbacks:
    return ExecutionCallbacks(
        bridge_tools=bridge_tools_fn or owner._bridge_tools,
        reject_recursive_callbacks=reject_unsupported_recursive_callbacks_fn
        or owner._reject_unsupported_recursive_callbacks,
        requires_bridge=requires_bridge_fn or owner._requires_bridge,
        ensure_bridge=ensure_bridge_fn
        or (
            lambda *, session, context, tools: owner.ensure_bridge(
                session=session,
                context=context,
                tools=tools,
            )
        ),
        execute_direct=execute_direct_fn
        or (
            lambda *, session, context, code, envs=None: owner.execute_direct(
                session=session,
                context=context,
                code=code,
                envs=envs,
            )
        ),
        response_from_execution=response_from_execution_fn
        or (lambda execution: owner.response_from_execution(execution)),
    )


def prepare_execution_code(
    owner: DaytonaExecutionOwner,
    *,
    code: str,
    variables: dict[str, Any],
    reject_recursive_callbacks: Callable[[str], None],
) -> str:
    prepared_code = sanitize_execution_code(inject_variables(owner, code, variables))
    reject_recursive_callbacks(prepared_code)
    return prepared_code


def sanitize_execution_code(code: str) -> str:
    """Strip DSPy adapter framing that occasionally leaks into Python code."""
    original = str(code or "")
    candidate = original.strip()
    fence_match = _PYTHON_FENCE_RE.match(candidate)
    if fence_match:
        candidate = fence_match.group("body").strip()
    if candidate.lower().startswith("code:\n"):
        candidate = candidate.split("\n", 1)[1].strip()

    # If the code already parses, return it as-is — do NOT run DSPy-sentinel
    # stripping. ``strip_dspy_sentinel_lines`` discards everything after a
    # ``[[ ## field ## ]]`` match on a line, which is correct for LLM-emitted
    # adapter framing but corrupts sentinels that appear INSIDE string literals
    # (e.g. ``document_text`` content that legitimately contains the text
    # ``[[ ## reasoning ## ]]``), truncating the literal mid-way and raising a
    # spurious "unterminated string literal" ``CodeSanitizationError``.
    if python_parses(candidate):
        return candidate

    candidate = strip_trailing_fence(strip_dspy_sentinel_lines(candidate))
    if python_parses(candidate):
        return candidate

    sentinel_match = _DSPY_FIELD_SENTINEL_RE.search(original)
    if sentinel_match:
        truncated = strip_trailing_fence(original[: sentinel_match.start()])
        truncated = strip_dspy_sentinel_lines(truncated)
        if python_parses(truncated):
            return truncated

    try:
        ast.parse(candidate)
    except SyntaxError as exc:
        raise CodeSanitizationError(
            f"Unable to prepare executable Python: {exc.msg} (line {exc.lineno or '?'})"
        ) from exc
    return candidate


def structured_execution_error(*, reason: str, error: str) -> DaytonaExecutionResponse:
    payload = json.dumps(
        {
            "status": "error",
            "reason": reason,
            "error": error,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return DaytonaExecutionResponse(error=payload)


def _broker_failure_key(session: DaytonaSandboxSession) -> str:
    return str(getattr(session, "sandbox_id", "") or getattr(session, "id", "") or id(session))


def _cached_broker_start_error(session: DaytonaSandboxSession, *, now: float | None = None) -> str | None:
    key = _broker_failure_key(session)
    cached = _BROKER_START_FAILURES.get(key)
    if cached is None:
        return None
    timestamp, error = cached
    current = time.time() if now is None else now
    if current - timestamp > _BROKER_START_FAILURE_COOLDOWN_SECONDS:
        _BROKER_START_FAILURES.pop(key, None)
        return None
    return error


def _remember_broker_start_error(session: DaytonaSandboxSession, error: str) -> None:
    _BROKER_START_FAILURES[_broker_failure_key(session)] = (time.time(), error)


def _clear_broker_start_error(session: DaytonaSandboxSession) -> None:
    _BROKER_START_FAILURES.pop(_broker_failure_key(session), None)


def _owner_broker_start_error(
    owner: DaytonaExecutionOwner,
    session: DaytonaSandboxSession,
) -> str | None:
    """Return an active broker-start failure and clear stale owner state."""
    cached = _cached_broker_start_error(session)
    if cached:
        return cached
    if getattr(owner, "_bridge_start_error", None):
        owner._bridge_start_error = None
    return None


def _inject_broker_failure_stubs(
    session: DaytonaSandboxSession,
    context: Any,
    tools: dict[str, Any],
    *,
    error: str,
) -> None:
    """Inject stub functions for each bridged tool so the REPL agent gets an
    informative RuntimeError instead of a bare NameError when the broker failed.
    Best-effort: any injection error is silently suppressed.
    """
    if not tools:
        return
    short_error = error[:200].replace("'", "\\'")
    lines = [
        f"def {name}(*_a, **_kw):"
        f" raise RuntimeError('Tool {name!r} unavailable: broker failed to start. {short_error}')"
        for name in tools
        if name.isidentifier()
    ]
    if not lines:
        return
    stub_code = "\n".join(lines)
    try:
        session.sandbox.code_interpreter.run_code(stub_code, context=context)
    except Exception:
        pass  # best-effort


def run_prepared_execution(
    owner: DaytonaExecutionOwner,
    *,
    session: DaytonaSandboxSession,
    context: Any,
    code: str,
    callbacks: ExecutionCallbacks,
    envs: dict[str, str] | None = None,
) -> DaytonaBridgeExecution:
    tools = callbacks.bridge_tools()
    if callbacks.requires_bridge(code, tools):
        bridge_start_error = _owner_broker_start_error(owner, session)
        if bridge_start_error:
            raise CodeInterpreterError(
                "Broker callbacks are unavailable after a previous startup failure in this session; "
                f"llm_query should not be retried. Previous failure: {bridge_start_error}"
            )
        try:
            bridge = callbacks.ensure_bridge(
                session=session,
                context=context,
                tools=tools,
            )
        except Exception as exc:
            if "Broker server failed to start" in str(exc):
                owner._bridge_start_error = str(exc)
                _remember_broker_start_error(session, str(exc))
                _inject_broker_failure_stubs(session, context, tools, error=str(exc))
            raise
        owner._bridge_start_error = None
        _clear_broker_start_error(session)
        return bridge.execute_tool_call(
            code=code,
            timeout=int(owner.execute_timeout or owner.timeout),
            tool_executor=lambda name, args, kwargs: owner._invoke_tool(name, args, kwargs),
        )
    return callbacks.execute_direct(
        session=session,
        context=context,
        code=code,
        envs=envs,
    )


# Keep a-prefixed alias for backward compatibility
async def arun_prepared_execution(
    owner: DaytonaExecutionOwner,
    *,
    session: DaytonaSandboxSession,
    context: Any,
    code: str,
    callbacks: ExecutionCallbacks,
    envs: dict[str, str] | None = None,
) -> DaytonaBridgeExecution:
    return await _run_sync_in_thread(
        run_prepared_execution,
        owner,
        session=session,
        context=context,
        code=code,
        callbacks=callbacks,
        envs=envs,
    )


def execute_direct(
    owner: DaytonaExecutionOwner,
    *,
    session: DaytonaSandboxSession,
    context: Any,
    code: str,
    envs: dict[str, str] | None = None,
) -> DaytonaBridgeExecution:
    """Run *code* directly via the sandbox code interpreter."""
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    def _on_stdout(message: Any) -> None:
        stdout_parts.append(str(getattr(message, "output", "") or ""))

    def _on_stderr(message: Any) -> None:
        stderr_parts.append(str(getattr(message, "output", "") or ""))

    result = session.sandbox.code_interpreter.run_code(
        code,
        context=context,
        on_stdout=_on_stdout,
        on_stderr=_on_stderr,
        envs=envs,
        timeout=int(owner.execute_timeout or owner.timeout),
    )
    return DaytonaBridgeExecution(
        result=result,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
        callback_count=0,
    )


# Keep a-prefixed alias for backward compatibility
async def aexecute_direct(
    owner: DaytonaExecutionOwner,
    *,
    session: DaytonaSandboxSession,
    context: Any,
    code: str,
    envs: dict[str, str] | None = None,
) -> DaytonaBridgeExecution:
    return await _run_sync_in_thread(
        execute_direct,
        owner,
        session=session,
        context=context,
        code=code,
        envs=envs,
    )


def response_from_execution(
    owner: DaytonaExecutionOwner,
    execution: DaytonaBridgeExecution,
    *,
    extract_final_artifact_fn: Callable[[str], dict[str, Any] | None] | None = None,
) -> DaytonaExecutionResponse:
    extract_final_artifact_fn = extract_final_artifact_fn or extract_final_artifact
    final_artifact = extract_final_artifact_fn(execution.stdout)
    result = execution.result
    error = getattr(result, "error", None)
    if error is None:
        return DaytonaExecutionResponse(
            stdout=execution.stdout,
            stderr=execution.stderr,
            final_artifact=final_artifact,
            callback_count=execution.callback_count,
        )

    error_name = str(getattr(error, "name", "") or "")
    error_value = str(getattr(error, "value", "") or "")
    if error_name == "_FleetFinalOutput" and final_artifact is not None:
        return DaytonaExecutionResponse(
            stdout=execution.stdout,
            stderr=execution.stderr,
            final_artifact=final_artifact,
            callback_count=execution.callback_count,
        )

    error_text = (
        ": ".join(part for part in [error_name, error_value] if part) or error_value or error_name or "Execution failed"
    )
    return DaytonaExecutionResponse(
        stdout=execution.stdout,
        stderr=execution.stderr,
        error=error_text,
        final_artifact=final_artifact,
        callback_count=execution.callback_count,
    )


def extract_final_artifact(
    stdout: str,
    *,
    marker: str = _FINAL_OUTPUT_MARKER,
) -> dict[str, Any] | None:
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


def finalize_execution_result(
    owner: DaytonaExecutionOwner,
    *,
    response: DaytonaExecutionResponse,
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
            owner,
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
        output_keys = [str(key) for key in list(final_payload.keys())[:50]] if isinstance(final_payload, dict) else None
        emit_execution_event(
            owner,
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
        owner,
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


def inject_variables(
    owner: DaytonaExecutionOwner,
    code: str,
    variables: dict[str, Any],
) -> str:
    del owner
    fallback_assignments = [
        "if 'active_skills' not in globals(): active_skills = {'selected': [], 'catalog': {}, 'instructions': {}, 'sources': {}}",
        "if 'context' not in globals() and 'get_workspace_context' in globals(): context = get_workspace_context()",
    ]
    if not variables:
        return "\n".join(fallback_assignments) + "\n" + code
    assignments = [f"{name} = {literal(value)}" for name, value in variables.items()]
    return "\n".join(fallback_assignments + assignments) + "\n" + code


def literal(value: Any) -> str:
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
        return "[" + ", ".join(literal(item) for item in value) + "]"
    if isinstance(value, tuple):
        inner = ", ".join(literal(item) for item in value)
        if len(value) == 1:
            inner += ","
        return "(" + inner + ")"
    if isinstance(value, set):
        if not value:
            return "set()"
        return "{" + ", ".join(literal(item) for item in value) + "}"
    if isinstance(value, dict):
        pairs = [f"{literal(key)}: {literal(item)}" for key, item in value.items()]
        return "{" + ", ".join(pairs) + "}"
    raise CodeInterpreterError(f"Unsupported value type: {type(value).__name__}")


_DaytonaExecutionResponse = DaytonaExecutionResponse
_ExecutionCallbacks = ExecutionCallbacks
_ensure_setup = ensure_setup
_execute_direct = execute_direct
_execute_in_session = execute_in_session
_run_prepared_execution = run_prepared_execution
_extract_final_artifact = extract_final_artifact
_finalize_execution_result = finalize_execution_result
_inject_variables = inject_variables
_literal = literal
_prepare_execution_code = prepare_execution_code
_resolve_execution_callbacks = resolve_execution_callbacks
_response_from_execution = response_from_execution
_safe_variables = safe_variables
_sanitize_execution_code = sanitize_execution_code
_structured_execution_error = structured_execution_error
_submit_signature = submit_signature


class SandboxExecutor:
    """Execute code inside Daytona sessions and manage bridge/setup state."""

    def __init__(
        self,
        *,
        workspace: ExecutorWorkspace,
        callback_owner: SandboxCallbackOwner,
        async_execute: bool,
        timeout: int,
        execute_timeout: int,
        broker_health_timeout: float,
        broker_tool_call_timeout: float = 180.0,
        broker_start_retries: int,
        output_fields: list[dict[str, Any]] | None,
        volume_mount_path: str,
        default_execution_profile: ExecutionProfile,
        native_tool_names: frozenset[str] = _DAYTONA_SANDBOX_NATIVE_TOOL_NAMES,
        unsupported_recursive_callbacks: tuple[str, ...] = _UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS,
    ) -> None:
        self._workspace = workspace
        self._callback_owner = callback_owner
        self.async_execute = async_execute
        self.timeout = timeout
        self.execute_timeout = execute_timeout
        self.broker_health_timeout = max(1.0, float(broker_health_timeout))
        self.broker_tool_call_timeout = max(1.0, float(broker_tool_call_timeout))
        self.broker_start_retries = max(0, int(broker_start_retries))
        self.output_fields = output_fields
        self.volume_mount_path = volume_mount_path
        self.default_execution_profile = default_execution_profile
        self._native_tool_names = native_tool_names
        self._unsupported_recursive_callbacks = unsupported_recursive_callbacks
        self._tools: dict[str, Callable[..., Any]] = {}
        self.execution_event_callback: Callable[[dict[str, Any]], None] | None = None
        self._bridge: DaytonaToolBridge | None = None
        self._bridge_sandbox_id: str | None = None
        self._bridge_context_id: str | None = None
        self._bridge_start_error: str | None = None
        self._setup_context_id: str | None = None
        self._setup_workspace_path: str | None = None
        self._submit_signature_key: tuple[tuple[str, str], ...] | None = None

    @property
    def tools(self) -> dict[str, Callable[..., Any]]:
        return self._tools

    @tools.setter
    def tools(self, value: dict[str, Callable[..., Any]]) -> None:
        self._tools = dict(value)
        self._callback_owner._tools = self._tools

    def soft_reset(self) -> None:
        """Reset execution state for pool reuse WITHOUT closing the broker.

        Clears cached setup tracking so the next execution re-runs base setup
        and re-registers SUBMIT. Clears injected tools on the bridge (they
        exist in the old REPL context) but preserves the broker process.
        """
        self._setup_context_id = None
        self._setup_workspace_path = None
        self._submit_signature_key = None
        self._bridge_start_error = None
        if self._bridge is not None:
            self._bridge._injected_tools.clear()
            self._bridge_context_id = None

    async def asoft_reset(self) -> None:
        """Async wrapper for soft_reset."""
        await _run_sync_in_thread(self.soft_reset)

    def reset(self) -> None:
        self.close_bridge()
        self._setup_context_id = None
        self._setup_workspace_path = None
        self._submit_signature_key = None

    async def areset(self) -> None:
        await _run_sync_in_thread(self.reset)

    def close_bridge(self) -> None:
        bridge = self._bridge
        self._bridge = None
        self._bridge_sandbox_id = None
        self._bridge_context_id = None
        self._bridge_start_error = None
        if bridge is not None:
            bridge.close()

    async def aclose_bridge(self) -> None:
        await _run_sync_in_thread(self.close_bridge)

    def _close_bridge(self) -> None:
        self.close_bridge()

    async def _aclose_bridge(self) -> None:
        await _run_sync_in_thread(self._close_bridge)

    def _bridge_tools(self) -> dict[str, Callable[..., Any]]:
        self._callback_owner._tools = self._tools
        return bridge_tools(self._callback_owner, native_tool_names=self._native_tool_names)

    def _reject_unsupported_recursive_callbacks(self, code: str) -> None:
        reject_unsupported_recursive_callbacks(
            self._callback_owner,
            code,
            callbacks=self._unsupported_recursive_callbacks,
        )

    def _requires_bridge(self, code: str, tools: dict[str, Callable[..., Any]]) -> bool:
        return requires_bridge(self._callback_owner, code, tools)

    def _invoke_tool(self, name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        self._callback_owner._tools = self._tools
        return invoke_tool(self._callback_owner, name, args, kwargs)

    def execute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
        envs: dict[str, str] | None = None,
    ) -> str | FinalOutput:
        session = self._workspace.ensure_session()
        session.start_driver(timeout=float(self.execute_timeout or self.timeout))
        safe_vars = self.safe_variables(variables)
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
            response = self.execute_in_session(
                session=session,
                code=code,
                variables=safe_vars,
                envs=envs,
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
        return self.finalize_execution_result(
            response=response,
            started_at=started_at,
            execution_profile=str(profile_value),
            code_hash=code_hash,
            code_preview=code_preview,
        )

    async def aexecute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
        envs: dict[str, str] | None = None,
    ) -> str | FinalOutput:
        return await _run_sync_in_thread(
            self.execute,
            code,
            variables,
            execution_profile=execution_profile,
            envs=envs,
        )

    def safe_variables(self: Any, variables: dict[str, Any] | None) -> dict[str, Any]:
        return _safe_variables(variables)

    def submit_signature(self: Any) -> tuple[tuple[str, str], ...] | None:
        return _submit_signature(self.output_fields)

    def ensure_setup(
        self: Any,
        session: DaytonaSandboxSession,
        *,
        submit_signature_fn: Callable[[], tuple[tuple[str, str], ...] | None] | None = None,
    ) -> Any:
        submit_signature_fn = submit_signature_fn or self.submit_signature
        return _ensure_setup(
            self,
            session,
            submit_signature_fn=submit_signature_fn,
        )

    async def aensure_setup(
        self: Any,
        session: DaytonaSandboxSession,
        *,
        submit_signature_fn: Callable[[], tuple[tuple[str, str], ...] | None] | None = None,
    ) -> Any:
        return await _run_sync_in_thread(
            self.ensure_setup,
            session,
            submit_signature_fn=submit_signature_fn,
        )

    def ensure_bridge(
        self: Any,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        tools: dict[str, Callable[..., Any]],
        bridge_cls: type[DaytonaToolBridge] | None = None,
    ) -> DaytonaToolBridge:
        if bridge_cls is None:
            owner_module = sys.modules.get(type(self._callback_owner).__module__)
            bridge_cls = getattr(owner_module, "DaytonaToolBridge", DaytonaToolBridge)
        return _ensure_bridge(
            self,
            session=session,
            context=context,
            tools=tools,
            bridge_cls=bridge_cls,
        )

    async def aensure_bridge(
        self: Any,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        tools: dict[str, Callable[..., Any]],
        bridge_cls: type[DaytonaToolBridge] | None = None,
    ) -> DaytonaToolBridge:
        return await _run_sync_in_thread(
            self.ensure_bridge,
            session=session,
            context=context,
            tools=tools,
            bridge_cls=bridge_cls,
        )

    def execute_in_session(
        self: Any,
        *,
        session: DaytonaSandboxSession,
        code: str,
        variables: dict[str, Any],
        envs: dict[str, str] | None = None,
        bridge_tools_fn: Callable[[], dict[str, Callable[..., Any]]] | None = None,
        reject_unsupported_recursive_callbacks_fn: Callable[[str], None] | None = None,
        requires_bridge_fn: Callable[[str, dict[str, Callable[..., Any]]], bool] | None = None,
        ensure_bridge_fn: Callable[..., Any] | None = None,
        execute_direct_fn: Callable[..., Any] | None = None,
        response_from_execution_fn: Callable[[DaytonaBridgeExecution], _DaytonaExecutionResponse] | None = None,
    ) -> _DaytonaExecutionResponse:
        return _execute_in_session(
            self,
            session=session,
            code=code,
            variables=variables,
            envs=envs,
            bridge_tools_fn=bridge_tools_fn,
            reject_unsupported_recursive_callbacks_fn=reject_unsupported_recursive_callbacks_fn,
            requires_bridge_fn=requires_bridge_fn,
            ensure_bridge_fn=ensure_bridge_fn,
            execute_direct_fn=execute_direct_fn,
            response_from_execution_fn=response_from_execution_fn,
        )

    async def aexecute_in_session(
        self: Any,
        *,
        session: DaytonaSandboxSession,
        code: str,
        variables: dict[str, Any] | None = None,
        envs: dict[str, str] | None = None,
        bridge_tools_fn: Callable[[], dict[str, Callable[..., Any]]] | None = None,
        reject_unsupported_recursive_callbacks_fn: Callable[[str], None] | None = None,
        requires_bridge_fn: Callable[[str, dict[str, Callable[..., Any]]], bool] | None = None,
        ensure_bridge_fn: Callable[..., Any] | None = None,
        execute_direct_fn: Callable[..., Any] | None = None,
        response_from_execution_fn: Callable[[DaytonaBridgeExecution], _DaytonaExecutionResponse] | None = None,
    ) -> _DaytonaExecutionResponse:
        return await _run_sync_in_thread(
            self.execute_in_session,
            session=session,
            code=code,
            variables=variables,
            envs=envs,
            bridge_tools_fn=bridge_tools_fn,
            reject_unsupported_recursive_callbacks_fn=reject_unsupported_recursive_callbacks_fn,
            requires_bridge_fn=requires_bridge_fn,
            ensure_bridge_fn=ensure_bridge_fn,
            execute_direct_fn=execute_direct_fn,
            response_from_execution_fn=response_from_execution_fn,
        )

    def _resolve_execution_callbacks(
        self: Any,
        *,
        bridge_tools_fn: Callable[[], dict[str, Callable[..., Any]]] | None = None,
        reject_unsupported_recursive_callbacks_fn: Callable[[str], None] | None = None,
        requires_bridge_fn: Callable[[str, dict[str, Callable[..., Any]]], bool] | None = None,
        ensure_bridge_fn: Callable[..., Any] | None = None,
        execute_direct_fn: Callable[..., Any] | None = None,
        response_from_execution_fn: Callable[[DaytonaBridgeExecution], _DaytonaExecutionResponse] | None = None,
    ) -> _ExecutionCallbacks:
        return _resolve_execution_callbacks(
            self,
            bridge_tools_fn=bridge_tools_fn,
            reject_unsupported_recursive_callbacks_fn=reject_unsupported_recursive_callbacks_fn,
            requires_bridge_fn=requires_bridge_fn,
            ensure_bridge_fn=ensure_bridge_fn,
            execute_direct_fn=execute_direct_fn,
            response_from_execution_fn=response_from_execution_fn,
        )

    def _prepare_execution_code(
        self: Any,
        *,
        code: str,
        variables: dict[str, Any],
        reject_recursive_callbacks: Callable[[str], None],
    ) -> str:
        return _prepare_execution_code(
            self,
            code=code,
            variables=variables,
            reject_recursive_callbacks=reject_recursive_callbacks,
        )

    @staticmethod
    def _sanitize_execution_code(code: str) -> str:
        return _sanitize_execution_code(code)

    @staticmethod
    def _structured_execution_error(*, reason: str, error: str) -> _DaytonaExecutionResponse:
        return _structured_execution_error(reason=reason, error=error)

    def _run_prepared_execution(
        self: Any,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        code: str,
        callbacks: _ExecutionCallbacks,
        envs: dict[str, str] | None = None,
    ) -> DaytonaBridgeExecution:
        return _run_prepared_execution(
            self,
            session=session,
            context=context,
            code=code,
            callbacks=callbacks,
            envs=envs,
        )

    async def _arun_prepared_execution(
        self: Any,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        code: str,
        callbacks: _ExecutionCallbacks,
        envs: dict[str, str] | None = None,
    ) -> DaytonaBridgeExecution:
        return await _run_sync_in_thread(
            self._run_prepared_execution,
            session=session,
            context=context,
            code=code,
            callbacks=callbacks,
            envs=envs,
        )

    def execute_direct(
        self: Any,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        code: str,
        envs: dict[str, str] | None = None,
    ) -> DaytonaBridgeExecution:
        return _execute_direct(
            self,
            session=session,
            context=context,
            code=code,
            envs=envs,
        )

    async def aexecute_direct(
        self: Any,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        code: str,
        envs: dict[str, str] | None = None,
    ) -> DaytonaBridgeExecution:
        return await _run_sync_in_thread(
            self.execute_direct,
            session=session,
            context=context,
            code=code,
            envs=envs,
        )

    def response_from_execution(
        self: Any,
        execution: DaytonaBridgeExecution,
        *,
        extract_final_artifact_fn: Callable[[str], dict[str, Any] | None] | None = None,
    ) -> _DaytonaExecutionResponse:
        return _response_from_execution(
            self,
            execution,
            extract_final_artifact_fn=extract_final_artifact_fn,
        )

    @staticmethod
    def extract_final_artifact(
        stdout: str,
        *,
        marker: str = _FINAL_OUTPUT_MARKER,
    ) -> dict[str, Any] | None:
        return _extract_final_artifact(stdout, marker=marker)

    def finalize_execution_result(
        self: Any,
        *,
        response: _DaytonaExecutionResponse,
        started_at: float,
        execution_profile: str,
        code_hash: str,
        code_preview: str,
    ) -> str | FinalOutput:
        return _finalize_execution_result(
            self,
            response=response,
            started_at=started_at,
            execution_profile=execution_profile,
            code_hash=code_hash,
            code_preview=code_preview,
        )

    def inject_variables(self: Any, code: str, variables: dict[str, Any]) -> str:
        return _inject_variables(self, code, variables)

    def literal(self: Any, value: Any) -> str:
        return _literal(value)
