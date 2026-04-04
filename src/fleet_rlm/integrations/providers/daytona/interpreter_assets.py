"""Embedded Daytona interpreter assets extracted from the main class module."""

from __future__ import annotations

from typing import Any

_FINAL_OUTPUT_MARKER = "__DSPY_FINAL_OUTPUT__"
_DAYTONA_SANDBOX_NATIVE_TOOL_NAMES: frozenset[str] = frozenset(
    {"run", "workspace_write", "workspace_read"}
)
# Agent-level tools that must NOT be called from inside sandbox code.
# Note: sub_rlm / sub_rlm_batched ARE allowed — they are the true-RLM
# recursive primitives bridged via the HTTP broker.
_UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS: tuple[str, ...] = (
    "rlm_query",
    "rlm_query_batched",
)


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


__all__ = [
    "_DAYTONA_SANDBOX_NATIVE_TOOL_NAMES",
    "_FINAL_OUTPUT_MARKER",
    "_generic_submit_code",
    "_UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS",
    "_base_setup_code",
    "_typed_submit_code",
]
