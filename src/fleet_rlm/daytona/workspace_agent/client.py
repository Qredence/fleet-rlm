"""Host-side transport and lifecycle client for the mounted Workspace Agent."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import threading
import time
from dataclasses import dataclass
from typing import Any

from fleet_rlm.daytona.workspace_agent import protocol as _protocol
from fleet_rlm.daytona.workspace_agent.protocol import (
    WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S,
    WORKSPACE_AGENT_INSTALL_PATH,
    WORKSPACE_AGENT_REQUEST_MAX_BYTES,
    WORKSPACE_AGENT_RESPONSE_MAX_BYTES,
    WorkspaceAgentProtocolError,
    WorkspaceAgentStorageError,
    build_installed_workspace_agent_source,
    build_workspace_agent_code,
    build_workspace_agent_request_code,
    decode_workspace_agent_response,
    workspace_agent_runtime_checksum,
)


@dataclass(slots=True)
class WorkspaceAgentMetrics:
    """Per-Sandbox transport measurements for Workspace Agent evidence."""

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
        from fleet_rlm.daytona.workspace_agent.protocol import (
            WORKSPACE_AGENT_PROTOCOL_VERSION,
            WORKSPACE_AGENT_SUPPORTED_OPERATIONS,
        )

        if payload.get("protocol_version") != WORKSPACE_AGENT_PROTOCOL_VERSION:
            return False
        if payload.get("source_checksum") != workspace_agent_runtime_checksum():
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
        code = _protocol.build_workspace_agent_request_code(
            {"operation": "__handshake__", "relative": ""},
            install_path=WORKSPACE_AGENT_INSTALL_PATH,
        )
        self.metrics.handshake_calls += 1
        return code

    def _operation_code(self, arguments: dict[str, object]) -> str:
        code = _protocol.build_workspace_agent_request_code(arguments, install_path=WORKSPACE_AGENT_INSTALL_PATH)
        self.metrics.operation_calls += 1
        return code

    def _install_source(self) -> bytes:
        return build_installed_workspace_agent_source().encode("utf-8")

    def _count_install(self, installed: bytes) -> None:
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


def _session_key(sandbox: Any) -> object:
    sandbox_id = getattr(sandbox, "id", None)
    return ("id", sandbox_id) if isinstance(sandbox_id, str) and sandbox_id else ("object", id(sandbox))


def _agent_session(sandbox: Any) -> _WorkspaceAgentSession:
    with _AGENT_SESSIONS_LOCK:
        session = _AGENT_SESSIONS.get(_session_key(sandbox))
        if session is None:
            session = _WorkspaceAgentSession()
            _AGENT_SESSIONS[_session_key(sandbox)] = session
        return session


def workspace_agent_metrics(sandbox: Any) -> WorkspaceAgentMetrics:
    return _agent_session(sandbox).metrics


def drop_workspace_agent_session(sandbox: Any) -> None:
    with _AGENT_SESSIONS_LOCK:
        _AGENT_SESSIONS.pop(_session_key(sandbox), None)


def _provider_code_run_timeout_s(timeout_s: float) -> int:
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("workspace agent timeout_s must be positive")
    return max(1, math.ceil(timeout_s))


def run_workspace_agent(
    sandbox: Any,
    *,
    timeout_s: float = WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S,
    **arguments: Any,
) -> dict[str, object]:
    """Execute a Workspace Agent operation through the sync host seam."""
    if _WorkspaceAgentSession.supports_installation(sandbox):
        return _agent_session(sandbox).request_sync(sandbox, arguments, timeout_s)
    relative = str(arguments.get("relative") or "")
    response = sandbox.process.code_run(
        build_workspace_agent_code(**arguments),
        timeout=_provider_code_run_timeout_s(timeout_s),
    )
    return decode_workspace_agent_response(response, relative)


async def run_workspace_agent_async(
    sandbox: Any,
    *,
    timeout_s: float = WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S,
    **arguments: Any,
) -> dict[str, object]:
    """Execute a Workspace Agent operation through the async host seam."""
    if _WorkspaceAgentSession.supports_installation(sandbox):
        return await _agent_session(sandbox).request_async(sandbox, arguments, timeout_s)
    relative = str(arguments.get("relative") or "")
    response = await sandbox.process.code_run(
        build_workspace_agent_code(**arguments), timeout=_provider_code_run_timeout_s(timeout_s)
    )
    return decode_workspace_agent_response(response, relative)


__all__ = [
    "_AGENT_SESSIONS",
    "WorkspaceAgentMetrics",
    "WorkspaceAgentProtocolError",
    "WorkspaceAgentStorageError",
    "_WorkspaceAgentSession",
    "build_installed_workspace_agent_source",
    "build_workspace_agent_code",
    "build_workspace_agent_request_code",
    "decode_workspace_agent_response",
    "drop_workspace_agent_session",
    "run_workspace_agent",
    "run_workspace_agent_async",
    "workspace_agent_metrics",
]
