"""Stdlib-only remote Session Workspace agent and its host execution adapter.

P22 (QRE-161/162/163) transport: the hardened remote program in
``workspace_agent_runtime.py`` is wrapped into a versioned installed module
(``handle(request)`` dispatch, handshake manifest, checksum). On first use of
a Sandbox with a filesystem surface, the host uploads that module once to
``WORKSPACE_AGENT_INSTALL_PATH`` (non-Volume, versioned by protocol), verifies
protocol version + source SHA-256 + operations + bounds + capability claims
with a fail-closed handshake, and then serves every operation as a compact
JSON request (~700 B envelope for a read/stat) instead of retransmitting the
full ~53 KiB source. Verified state is cached per Sandbox id; replacements
install independently. Host Sandboxes without an ``fs.upload_file`` surface
(test doubles) keep the legacy full-source wire; mismatch or failed installs
never fall back to it.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import threading
import time
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from fleet_rlm.daytona.interpreter import DEFAULT_EXECUTION_TIMEOUT_S
from fleet_rlm.files.workspace_models import WorkspaceConflictError

# Bound provider Workspace Agent ``code_run``. Reuses the interpreter execution
# default (same numeric bound as Settings ``rlm_execution_timeout_s``).
# Not a public TOML knob — callers may override via ``timeout_s``.
WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S = DEFAULT_EXECUTION_TIMEOUT_S
WORKSPACE_AGENT_PROTOCOL_VERSION = "fleet.workspace-agent/v1"
WORKSPACE_AGENT_INSTALL_PATH = "/home/daytona/fleet_rlm_workspace_agent_v1.py"
WORKSPACE_AGENT_MODULE_NAME = "fleet_rlm_workspace_agent_v1"
WORKSPACE_AGENT_REQUEST_MAX_BYTES = 16 * 1024 * 1024
WORKSPACE_AGENT_RESPONSE_MAX_BYTES = 16 * 1024 * 1024
WORKSPACE_AGENT_SUPPORTED_OPERATIONS = (
    "list",
    "stat",
    "tail_read",
    "read",
    "read_page",
    "append",
    "memory_migrate",
    "memory_append",
    "memory_edit",
    "memory_delete",
    "unlink",
    "delete",
    "patch",
    "write",
)


class WorkspaceAgentStorageError(OSError):
    """Remote mounted-volume mutation failure."""


class WorkspaceAgentProtocolError(RuntimeError):
    """Installed Workspace Agent is absent, incompatible, or tampered with."""


_PATH_ERRORS = {
    "not_found": FileNotFoundError,
    "is_directory": IsADirectoryError,
    "not_directory": NotADirectoryError,
}
_VALUE_ERRORS = {
    "read_bound": "workspace file exceeds read bound",
    "too_large": "workspace file exceeds maximum size",
    "invalid_record": "workspace memory record is invalid",
    "invalid_utf8": "workspace file is not valid UTF-8",
    "cursor": "workspace cursor is invalid",
}
# Protocol-integrity failures are typed protocol errors, never path errors.
_PROTOCOL_ERRORS = frozenset({"protocol_mismatch", "request_invalid"})


_WORKSPACE_AGENT_RUNTIME_NAME = "workspace_agent_runtime.py"
_WORKSPACE_AGENT_RUNTIME_SOURCE: str | None = None


def _workspace_agent_runtime_source() -> str:
    """Load packaged remote-agent source text (never import it as host behavior)."""
    global _WORKSPACE_AGENT_RUNTIME_SOURCE
    if _WORKSPACE_AGENT_RUNTIME_SOURCE is None:
        _WORKSPACE_AGENT_RUNTIME_SOURCE = (
            files("fleet_rlm.daytona").joinpath(_WORKSPACE_AGENT_RUNTIME_NAME).read_text(encoding="utf-8")
        )
    return _WORKSPACE_AGENT_RUNTIME_SOURCE


def build_workspace_agent_code(
    *,
    volume_root: str,
    root: str,
    operation: str,
    relative: str,
    allow_missing: bool,
    max_bytes: int,
    limit: int,
    overwrite: bool,
    content_b64: str,
    after: str = "",
    offset: int = 0,
    max_chars: int = 0,
    total_file_bytes: int = 0,
    checksum: bool = False,
    memory_id: str = "",
    expected_sha256: str = "",
) -> str:
    """
    Construct a complete Workspace Agent script for a filesystem or memory operation.

    Parameters:
        volume_root (str): Workspace volume root.
        root (str): Root directory for the operation.
        operation (str): Operation to perform.
        relative (str): Path relative to the selected root.
        allow_missing (bool): Whether a missing target is permitted.
        max_bytes (int): Maximum number of bytes to read.
        limit (int): Maximum number of entries to return.
        overwrite (bool): Whether an existing file may be replaced.
        content_b64 (str): Base64-encoded content for write operations.
        after (str): Pagination marker for directory listings.
        offset (int): Byte offset for file reads.
        max_chars (int): Maximum number of decoded characters to return.
        total_file_bytes (int): Expected total file size for chunked reads.
        checksum (bool): Whether to include a checksum in the result.
        memory_id (str): Identifier of the memory object.
        expected_sha256 (str): Expected SHA-256 checksum for verification.

    Returns:
        str: A complete script containing the runtime and encoded operation request.

    Raises:
        WorkspaceAgentProtocolError: If the encoded request exceeds the protocol size limit.
    """
    request = {
        "protocol_version": WORKSPACE_AGENT_PROTOCOL_VERSION,
        "volume_root": volume_root,
        "root": root,
        "relative": relative,
        "allow_missing": allow_missing,
        "operation": operation,
        "max_bytes": int(max_bytes),
        "limit": int(limit),
        "overwrite": overwrite,
        "content_b64": content_b64,
        "after": after,
        "offset": int(offset),
        "max_chars": int(max_chars),
        "total_file_bytes": int(total_file_bytes),
        "checksum": checksum,
        "memory_id": memory_id,
        "expected_sha256": expected_sha256,
    }
    encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > WORKSPACE_AGENT_REQUEST_MAX_BYTES:
        raise WorkspaceAgentProtocolError("Workspace Agent request exceeds its bound")
    return (
        _workspace_agent_runtime_source()
        + "\n\nprint(json.dumps(handle(json.loads("
        + repr(encoded)
        + ")), ensure_ascii=False, separators=(',', ':')))\n"
    )


def _workspace_agent_runtime_checksum() -> str:
    """Compute the SHA-256 checksum of the packaged workspace agent runtime source.

    Returns:
        str: The hexadecimal SHA-256 checksum of the runtime source.
    """
    return hashlib.sha256(_workspace_agent_runtime_source().encode("utf-8")).hexdigest()


def build_installed_workspace_agent_source() -> str:
    # The uploaded artifact is exactly the packaged runtime module. Its
    # handler computes the checksum from ``__file__`` during the handshake, so
    # the manifest covers the bytes that were actually installed.
    """
    Return the validated source code for the installed Workspace Agent runtime.

    Raises:
        WorkspaceAgentProtocolError: If the runtime source contains a syntax error.

    Returns:
        str: The packaged Workspace Agent runtime source.
    """
    source = _workspace_agent_runtime_source()
    try:
        compile(source, _WORKSPACE_AGENT_RUNTIME_NAME, "exec")
    except SyntaxError as exc:
        raise WorkspaceAgentProtocolError("Workspace Agent runtime is invalid") from exc
    return source


def build_workspace_agent_request_code(arguments: dict[str, object]) -> str:
    # Build only the compact installed-agent request wrapper (P22/QRE-163).
    # Normal operations transmit this shim plus the JSON request — never the
    # full runtime source. A fresh module instance per call keeps request
    # state call-local.
    """
    Builds a compact script that invokes the installed Workspace Agent with a protocol-tagged request.

    Args:
        arguments: Operation-specific request fields.

    Returns:
        A Python script that executes the installed agent and prints its response.

    Raises:
        WorkspaceAgentProtocolError: If the encoded request exceeds the maximum size.
    """
    request = {"protocol_version": WORKSPACE_AGENT_PROTOCOL_VERSION, **arguments}
    encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > WORKSPACE_AGENT_REQUEST_MAX_BYTES:
        raise WorkspaceAgentProtocolError("Workspace Agent request exceeds its bound")
    code = (
        "import importlib.util as _u, json as _j\n"
        f"_s = _u.spec_from_file_location({WORKSPACE_AGENT_MODULE_NAME!r}, {WORKSPACE_AGENT_INSTALL_PATH!r})\n"
        "if _s is None or _s.loader is None: raise RuntimeError('workspace agent unavailable')\n"
        "_m = _u.module_from_spec(_s); _s.loader.exec_module(_m)\n"
        f"try:\n    _v = _m.handle(_j.loads({encoded!r}))\n"
        "except SystemExit:\n    pass\n"
        "else:\n    print(_j.dumps(_v, ensure_ascii=False, separators=(',', ':')))\n"
    )
    return code


@dataclass(slots=True)
class WorkspaceAgentMetrics:
    # Per-Sandbox transport measurements for P22 evidence receipts.
    source_transfer_bytes: int = 0
    bootstrap_count: int = 0
    handshake_calls: int = 0
    operation_calls: int = 0
    request_bytes: int = 0
    latency_ms_total: float = 0.0


class _WorkspaceAgentSession:
    def __init__(self) -> None:
        self.verified = False
        self.metrics = WorkspaceAgentMetrics()
        self._sync_lock = threading.Lock()
        self._async_lock: asyncio.Lock | None = None

    @staticmethod
    def supports_installation(sandbox: Any) -> bool:
        fs = getattr(sandbox, "fs", None)
        process = getattr(sandbox, "process", None)
        return callable(getattr(fs, "upload_file", None)) and callable(getattr(process, "code_run", None))

    @staticmethod
    def _json_response(response: Any) -> dict[str, object] | None:
        if int(getattr(response, "exit_code", 1)) != 0:
            return None
        raw = str(getattr(response, "result", ""))
        if len(raw.encode("utf-8")) > WORKSPACE_AGENT_RESPONSE_MAX_BYTES:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _valid_handshake(payload: dict[str, object] | None) -> bool:
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return False
        if payload.get("kind") != "workspace_agent_handshake":
            return False
        if payload.get("protocol_version") != WORKSPACE_AGENT_PROTOCOL_VERSION:
            return False
        if payload.get("source_checksum") != _workspace_agent_runtime_checksum():
            return False
        operations = payload.get("operations")
        if not isinstance(operations, list) or list(operations) != list(WORKSPACE_AGENT_SUPPORTED_OPERATIONS):
            return False
        return (
            payload.get("locking") == "fcntl_flock_inode_revalidation"
            and payload.get("replacement") == "replace_overwrite_recreate"
            and payload.get("fallback") == "non_atomic_overwrite_cleanup_warning"
            and payload.get("request_max_bytes") == WORKSPACE_AGENT_REQUEST_MAX_BYTES
            and payload.get("response_max_bytes") == WORKSPACE_AGENT_RESPONSE_MAX_BYTES
        )

    async def _raw_async(self, sandbox: Any, code: str, timeout_s: float) -> Any:
        started = time.monotonic()
        self.metrics.request_bytes += len(code.encode("utf-8"))
        response = await sandbox.process.code_run(code, timeout=_provider_code_run_timeout_s(timeout_s))
        self.metrics.latency_ms_total += (time.monotonic() - started) * 1000.0
        return response

    def _raw_sync(self, sandbox: Any, code: str, timeout_s: float) -> Any:
        started = time.monotonic()
        self.metrics.request_bytes += len(code.encode("utf-8"))
        response = sandbox.process.code_run(code, timeout=_provider_code_run_timeout_s(timeout_s))
        self.metrics.latency_ms_total += (time.monotonic() - started) * 1000.0
        return response

    def _handshake_code(self) -> str:
        """Build the handshake request and count the attempt."""
        code = build_workspace_agent_request_code({"operation": "__handshake__", "relative": ""})
        self.metrics.handshake_calls += 1
        return code

    def _operation_code(self, arguments: dict[str, object]) -> str:
        """Build an operation request and count the call."""
        code = build_workspace_agent_request_code(arguments)
        self.metrics.operation_calls += 1
        return code

    def _install_source(self) -> bytes:
        """Return the installable runtime source bytes."""
        return build_installed_workspace_agent_source().encode("utf-8")

    def _count_install(self, installed: bytes) -> None:
        """Count one completed install transfer."""
        self.metrics.source_transfer_bytes += len(installed)
        self.metrics.bootstrap_count += 1

    def _finish_ensure(self, payload: dict[str, object] | None) -> None:
        if not self._valid_handshake(payload):
            raise WorkspaceAgentProtocolError("Workspace Agent protocol handshake failed")
        self.verified = True

    @staticmethod
    def _relative(arguments: dict[str, object]) -> str:
        return str(arguments.get("relative") or "")

    async def _handshake_async(self, sandbox: Any, timeout_s: float) -> dict[str, object] | None:
        try:
            return self._json_response(await self._raw_async(sandbox, self._handshake_code(), timeout_s))
        except Exception:
            return None

    def _handshake_sync(self, sandbox: Any, timeout_s: float) -> dict[str, object] | None:
        try:
            return self._json_response(self._raw_sync(sandbox, self._handshake_code(), timeout_s))
        except Exception:
            return None

    async def _ensure_async(self, sandbox: Any, timeout_s: float) -> None:
        payload = await self._handshake_async(sandbox, timeout_s)
        if not self._valid_handshake(payload):
            installed = self._install_source()
            await sandbox.fs.upload_file(installed, WORKSPACE_AGENT_INSTALL_PATH)
            self._count_install(installed)
            payload = await self._handshake_async(sandbox, timeout_s)
        self._finish_ensure(payload)

    def _ensure_sync(self, sandbox: Any, timeout_s: float) -> None:
        payload = self._handshake_sync(sandbox, timeout_s)
        if not self._valid_handshake(payload):
            installed = self._install_source()
            upload = sandbox.fs.upload_file(installed, WORKSPACE_AGENT_INSTALL_PATH)
            if inspect.isawaitable(upload):
                # Test doubles and a few SDK adapters expose the filesystem
                # through async methods even on the synchronous host-tool path.
                # Resolve that narrow boundary on the worker thread instead of
                # silently dropping the install coroutine.
                asyncio.run(upload)
            self._count_install(installed)
            payload = self._handshake_sync(sandbox, timeout_s)
        self._finish_ensure(payload)

    async def ensure_async(self, sandbox: Any, timeout_s: float) -> None:
        if self.verified:
            return
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        async with self._async_lock:
            if self.verified:
                return
            await self._ensure_async(sandbox, timeout_s)

    def ensure_sync(self, sandbox: Any, timeout_s: float) -> None:
        if self.verified:
            return
        with self._sync_lock:
            if self.verified:
                return
            self._ensure_sync(sandbox, timeout_s)

    async def request_async(self, sandbox: Any, arguments: dict[str, object], timeout_s: float) -> dict[str, object]:
        await self.ensure_async(sandbox, timeout_s)
        response = await self._raw_async(sandbox, self._operation_code(arguments), timeout_s)
        return decode_workspace_agent_response(response, self._relative(arguments))

    def request_sync(self, sandbox: Any, arguments: dict[str, object], timeout_s: float) -> dict[str, object]:
        self.ensure_sync(sandbox, timeout_s)
        response = self._raw_sync(sandbox, self._operation_code(arguments), timeout_s)
        return decode_workspace_agent_response(response, self._relative(arguments))


_AGENT_SESSIONS: dict[object, _WorkspaceAgentSession] = {}
_AGENT_SESSIONS_LOCK = threading.Lock()


def _agent_session(sandbox: Any) -> _WorkspaceAgentSession:
    # Verified-install state is keyed by provider Sandbox identity so a
    # retained Sandbox reuses one install and a replacement Sandbox (new id)
    # installs and handshakes independently.
    sandbox_id = getattr(sandbox, "id", None)
    key: object = ("id", sandbox_id) if isinstance(sandbox_id, str) and sandbox_id else ("object", id(sandbox))
    with _AGENT_SESSIONS_LOCK:
        session = _AGENT_SESSIONS.get(key)
        if session is None:
            session = _WorkspaceAgentSession()
            _AGENT_SESSIONS[key] = session
        return session


def workspace_agent_metrics(sandbox: Any) -> WorkspaceAgentMetrics:
    # Live per-Sandbox P22 transport counters (used by evidence receipts).
    return _agent_session(sandbox).metrics


def drop_workspace_agent_session(sandbox: Any) -> None:
    # Test/lease-disposal hook: forget verified-install state for a Sandbox.
    sandbox_id = getattr(sandbox, "id", None)
    key: object = ("id", sandbox_id) if isinstance(sandbox_id, str) and sandbox_id else ("object", id(sandbox))
    with _AGENT_SESSIONS_LOCK:
        _AGENT_SESSIONS.pop(key, None)


def decode_workspace_agent_response(response: Any, relative: str) -> dict[str, object]:
    if int(getattr(response, "exit_code", 1)) != 0:
        raise ValueError("workspace path is unsafe")
    try:
        payload = json.loads(str(getattr(response, "result", "")))
    except (TypeError, ValueError) as exc:
        raise ValueError("workspace path is unsafe") from exc
    if payload.get("ok") is not True:
        _raise_workspace_error(payload, relative)
    return payload


def _provider_code_run_timeout_s(timeout_s: float) -> int:
    """Convert a host timeout into Daytona's integer ``code_run`` timeout."""
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("workspace agent timeout_s must be positive")
    return max(1, math.ceil(timeout_s))


def run_workspace_agent(
    sandbox: Any,
    *,
    timeout_s: float = WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S,
    **arguments: Any,
) -> dict[str, object]:
    """
    Execute a workspace agent operation synchronously.

    Parameters:
        sandbox (Any): Sandbox used to execute the operation.
        timeout_s (float): Maximum execution time in seconds.
        **arguments (Any): Operation parameters, including the requested workspace operation.

    Returns:
        dict[str, object]: The decoded workspace operation result.
    """
    if _WorkspaceAgentSession.supports_installation(sandbox):
        return _agent_session(sandbox).request_sync(sandbox, arguments, timeout_s)
    # Compatibility path for process-only test doubles: send the complete
    # packaged artifact plus a request, using the same handler as installation.
    relative = str(arguments.get("relative") or "")
    code = build_workspace_agent_code(**arguments)
    response = sandbox.process.code_run(code, timeout=_provider_code_run_timeout_s(timeout_s))
    return decode_workspace_agent_response(response, relative)


async def run_workspace_agent_async(
    sandbox: Any,
    *,
    timeout_s: float = WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S,
    **arguments: Any,
) -> dict[str, object]:
    """
    Execute a workspace operation asynchronously through the remote agent.

    Parameters:
        timeout_s (float): Maximum execution time in seconds.
        **arguments (Any): Operation name and operation-specific parameters.

    Returns:
        dict[str, object]: The decoded operation result.
    """
    if _WorkspaceAgentSession.supports_installation(sandbox):
        return await _agent_session(sandbox).request_async(sandbox, arguments, timeout_s)
    # Compatibility path for process-only test doubles (see sync adapter); the
    # complete artifact still invokes the same runtime handler.
    relative = str(arguments.get("relative") or "")
    code = build_workspace_agent_code(**arguments)
    response = await sandbox.process.code_run(code, timeout=_provider_code_run_timeout_s(timeout_s))
    return decode_workspace_agent_response(response, relative)


def _raise_workspace_error(payload: dict[str, object], relative: str) -> None:
    error = str(payload.get("error") or "")
    if error == "conflict":
        # Conflicts carry a stable detail (checksum_mismatch, not_empty,
        # ambiguous, missing) so tool hosts can render actionable feedback.
        detail = payload.get("detail")
        if isinstance(detail, str) and detail:
            raise WorkspaceConflictError(relative, detail=detail)
        raise FileExistsError(relative)
    path_error = _PATH_ERRORS.get(error)
    if path_error is not None:
        raise path_error(relative)
    value_error = _VALUE_ERRORS.get(error)
    if value_error is not None:
        raise ValueError(value_error)
    if error in _PROTOCOL_ERRORS:
        raise WorkspaceAgentProtocolError(error)
    if error == "unsupported_storage":
        raise WorkspaceAgentStorageError(str(payload.get("errno") or "unknown"))
    raise ValueError("workspace path is unsafe")
