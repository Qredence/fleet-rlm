"""Daytona sandbox session model and session-local helpers."""

from __future__ import annotations

import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from .async_compat import _run_async_compat, _run_sync_in_thread
from .diagnostics import DaytonaDiagnosticError
from .models import ContextSource
from .sdk_ops import DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH

if TYPE_CHECKING:
    from .protocols import DaytonaSandbox


# ---------------------------------------------------------------------------
# Admin code-execution helpers
# ---------------------------------------------------------------------------
def _run_admin_code(
    *,
    sandbox: DaytonaSandbox,
    code: str,
    phase: str,
    error_prefix: str,
    category: str = "sandbox_create_clone_error",
) -> str:
    """Run administrative code inside a sandbox via ``sandbox.process.code_run``."""
    try:
        from daytona.common.process import CodeRunParams

        result = _run_async_compat(
            sandbox.process.code_run,
            code,
            params=CodeRunParams(),
        )
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"{error_prefix}: {exc}",
            category=category,
            phase=phase,
        ) from exc

    exit_code = getattr(result, "exit_code", 0)
    if exit_code:
        detail = str(
            getattr(result, "stderr", "")
            or getattr(result, "result", "")
            or getattr(getattr(result, "artifacts", None), "stdout", "")
            or getattr(result, "output", "")
            or f"process exited with status {exit_code}"
        )
        raise DaytonaDiagnosticError(
            f"{error_prefix}: {detail}",
            category=category,
            phase=phase,
        )

    return str(
        getattr(result, "stdout", "")
        or getattr(result, "result", "")
        or getattr(getattr(result, "artifacts", None), "stdout", "")
        or getattr(result, "output", "")
        or ""
    )


async def _arun_admin_code(
    *,
    sandbox: DaytonaSandbox,
    code: str,
    phase: str,
    error_prefix: str,
    category: str = "sandbox_create_clone_error",
) -> str:
    return await _run_sync_in_thread(
        _run_admin_code,
        sandbox=sandbox,
        code=code,
        phase=phase,
        error_prefix=error_prefix,
        category=category,
    )


# ---------------------------------------------------------------------------
# Session model
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class DaytonaSandboxSession:
    """Concrete Daytona workspace session backed by a sandbox and interpreter context."""

    sandbox: Any
    repo_url: str | None
    ref: str | None
    volume_name: str | None
    workspace_path: str
    context_sources: list[ContextSource] = field(default_factory=list)
    phase_timings_ms: dict[str, int] = field(default_factory=dict)
    volume_mount_path: str = str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
    context_id: str | None = None
    owner_thread_id: int | None = None
    execution_event_callback: Any | None = None
    _context: Any | None = field(default=None, init=False, repr=False)
    _driver_started: bool = field(default=False, init=False, repr=False)
    _runtime_ref: Any | None = field(default=None, init=False, repr=False)

    @property
    def sandbox_id(self) -> str | None:
        return str(getattr(self.sandbox, "id", "") or "") or None

    def bind_current_async_owner(self) -> None:
        self.owner_thread_id = threading.get_ident()

    def matches_current_async_owner(self) -> bool:
        if self.owner_thread_id is None:
            return False
        return self.owner_thread_id == threading.get_ident()

    def ensure_context(self) -> Any:
        previous_sandbox = self.sandbox
        self._rebind_sandbox_if_needed()
        if self.sandbox is not previous_sandbox:
            self._context = None
        if self._context is not None:
            return self._context
        if self.context_id:
            existing_contexts: list[Any] | None = None
            with suppress(Exception):
                existing_contexts = self.sandbox.code_interpreter.list_contexts()
            if existing_contexts is not None:
                for existing in existing_contexts:
                    if str(getattr(existing, "id", "") or "") == self.context_id:
                        self._context = existing
                        return existing
        context = self.sandbox.code_interpreter.create_context(cwd=self.workspace_path)
        self._context = context
        self.context_id = str(getattr(context, "id", "") or "") or None
        return context

    async def aensure_context(self) -> Any:
        return await _run_sync_in_thread(self.ensure_context)

    def start_driver(self, *, timeout: float = 30.0) -> None:
        _ = timeout
        self.ensure_context()
        self._driver_started = True

    async def astart_driver(self, *, timeout: float = 30.0) -> None:
        await _run_sync_in_thread(self.start_driver, timeout=timeout)

    def close_driver(self) -> None:
        self._driver_started = False

    async def aclose_driver(self) -> None:
        await _run_sync_in_thread(self.close_driver)

    def delete_context(self) -> None:
        previous_sandbox = self.sandbox
        self._rebind_sandbox_if_needed()
        context = self._context
        self._context = None
        if self.sandbox is not previous_sandbox:
            context = None
        if context is None and self.context_id:
            with suppress(Exception):
                existing_contexts = self.sandbox.code_interpreter.list_contexts()
                for existing in existing_contexts:
                    if str(getattr(existing, "id", "") or "") == self.context_id:
                        context = existing
                        break
        if context is not None:
            with suppress(Exception):
                self.sandbox.code_interpreter.delete_context(context)
        self.context_id = None
        self._driver_started = False

    async def adelete_context(self) -> None:
        await _run_sync_in_thread(self.delete_context)

    def _resolve_sandbox_path(self, path: str) -> str:
        candidate = PurePosixPath(str(path or "").strip() or ".")
        if candidate.is_absolute():
            return str(candidate)
        return str(PurePosixPath(self.workspace_path) / candidate)

    def _rebind_sandbox_if_needed(self) -> None:
        if self.matches_current_async_owner() or self._runtime_ref is None:
            return
        sandbox_id = self.sandbox_id
        if not sandbox_id:
            return
        with suppress(Exception):
            self.sandbox = self._runtime_ref._get_sandbox(
                sandbox_id,
                recover=False,
            )
            self.bind_current_async_owner()

    async def _arebind_sandbox_if_needed(self) -> None:
        await _run_sync_in_thread(self._rebind_sandbox_if_needed)

    def read_file(self, path: str) -> str:
        self._rebind_sandbox_if_needed()
        raw = _run_async_compat(
            self.sandbox.fs.download_file,
            self._resolve_sandbox_path(path),
        )
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        return bytes(raw).decode("utf-8", errors="replace")

    async def aread_file(self, path: str) -> str:
        return await _run_sync_in_thread(self.read_file, path)

    def write_file(self, path: str, content: str) -> str:
        self._rebind_sandbox_if_needed()
        resolved_path = self._resolve_sandbox_path(path)
        payload = content.encode("utf-8")
        callback = getattr(self, "execution_event_callback", None)
        if callable(callback):
            callback(
                {
                    "phase": "progress",
                    "timestamp": time.time(),
                    "execution_profile": "durable_write",
                    "code_hash": "durable-write",
                    "code_preview": "sandbox.fs.upload_file",
                    "event_kind": "durable_write_started",
                    "path": resolved_path,
                    "bytes_total": len(payload),
                    "bytes_written": 0,
                }
            )
        _run_async_compat(self.sandbox.fs.upload_file, payload, resolved_path)
        if callable(callback):
            callback(
                {
                    "phase": "progress",
                    "timestamp": time.time(),
                    "execution_profile": "durable_write",
                    "code_hash": "durable-write",
                    "code_preview": "sandbox.fs.upload_file",
                    "event_kind": "durable_write_completed",
                    "path": resolved_path,
                    "bytes_total": len(payload),
                    "bytes_written": len(payload),
                }
            )
        return resolved_path

    async def awrite_file(self, path: str, content: str) -> str:
        return await _run_sync_in_thread(self.write_file, path, content)

    def list_files(self, path: str) -> list[Any]:
        self._rebind_sandbox_if_needed()
        entries = _run_async_compat(
            self.sandbox.fs.list_files,
            self._resolve_sandbox_path(path),
        )
        return list(entries)

    async def alist_files(self, path: str) -> list[Any]:
        return await _run_sync_in_thread(self.list_files, path)

    def delete(self) -> None:
        self.delete_context()
        # Graceful stop before delete lets sandbox processes flush if possible.
        with suppress(Exception):
            _run_async_compat(self.sandbox.stop, timeout=10)
        with suppress(Exception):
            _run_async_compat(self.sandbox.delete)
        self._driver_started = False

    async def adelete(self) -> None:
        await _run_sync_in_thread(self.delete)

    def archive(self) -> None:
        self._rebind_sandbox_if_needed()
        _run_async_compat(self.sandbox.archive)

    async def aarchive(self) -> None:
        await _run_sync_in_thread(self.archive)

    def recover(self, *, timeout: float = 60.0) -> None:
        self._rebind_sandbox_if_needed()
        _run_async_compat(self.sandbox.recover, timeout=timeout)

    async def arecover(self, *, timeout: float = 60.0) -> None:
        await _run_sync_in_thread(self.recover, timeout=timeout)

    def refresh_activity(self) -> None:
        self._rebind_sandbox_if_needed()
        with suppress(Exception):
            _run_async_compat(self.sandbox.refresh_activity)

    async def arefresh_activity(self) -> None:
        await _run_sync_in_thread(self.refresh_activity)

    def resize(self, *, cpu: int, memory: int, disk: int) -> None:
        from daytona import Resources

        self._rebind_sandbox_if_needed()
        _run_async_compat(
            self.sandbox.resize,
            Resources(cpu=cpu, memory=memory, disk=disk),
        )

    async def aresize(self, *, cpu: int, memory: int, disk: int) -> None:
        await _run_sync_in_thread(self.resize, cpu=cpu, memory=memory, disk=disk)

    def create_lsp_server(
        self,
        *,
        language: str = "python",
        project_path: str | None = None,
    ) -> Any:
        return self.sandbox.create_lsp_server(
            language,
            project_path or self.workspace_path,
        )


__all__ = ["DaytonaSandboxSession", "_run_admin_code", "_arun_admin_code"]
