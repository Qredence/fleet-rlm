"""Direct-SDK workspace bootstrap and session helpers for Daytona sandboxes."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import logging
import threading
import time
import time as _time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from fleet_rlm.runtime.execution.storage_paths import mounted_storage_roots

from .async_compat import (
    _await_if_needed,
    _run_async_compat,
)
from .config import ResolvedDaytonaConfig, resolve_daytona_config
from .diagnostics import DaytonaDiagnosticError, VolumeNotReadyError
from .types import ContextSource, SandboxSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Daytona client building helpers
# ---------------------------------------------------------------------------

DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH = PurePosixPath("/home/daytona/memory")

_REMOTE_DIRECTORY_MODE = "755"


def _daytona_import_error(exc: ImportError) -> RuntimeError:
    return RuntimeError(
        "Daytona SDK is not available. Install dependencies with `uv sync` "
        "and configure DAYTONA_API_KEY / DAYTONA_API_URL before using Daytona "
        "commands. See https://www.daytona.io/docs/en/python-sdk/"
    )


def _build_daytona_client(config: ResolvedDaytonaConfig) -> Any:
    try:
        from daytona import AsyncDaytona, DaytonaConfig
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc
    return AsyncDaytona(
        DaytonaConfig(
            api_key=config.api_key,
            api_url=config.api_url.rstrip("/"),
            target=config.target,
        )
    )


async def _aensure_remote_directory(fs: Any, remote_path: PurePosixPath) -> None:
    directory = str(remote_path)
    if directory and directory not in {".", "/"}:
        await _await_if_needed(fs.create_folder(directory, _REMOTE_DIRECTORY_MODE))


# ---------------------------------------------------------------------------
# Volume state normalization
# ---------------------------------------------------------------------------

_VOLUME_READY_STATES = frozenset({"ready"})
_VOLUME_ERROR_STATES = frozenset({"error", "failed", "deleted"})


def _canonicalize_volume_state_token(value: Any) -> str:
    candidates: list[str] = []

    if isinstance(value, str):
        candidates.append(value)
    else:
        state_value = getattr(value, "value", None)
        if state_value not in (None, ""):
            candidates.append(str(state_value))
        state_name = getattr(value, "name", None)
        if state_name not in (None, ""):
            candidates.append(str(state_name))
        if value not in (None, ""):
            candidates.append(str(value))

    for candidate in candidates:
        normalized = candidate.strip().lower()
        if not normalized:
            continue
        normalized = normalized.replace("-", "_").replace(" ", "_")
        if "." in normalized:
            normalized = normalized.rsplit(".", 1)[-1]
        if normalized:
            return normalized
    return ""


def _volume_state_details(volume: Any) -> tuple[str, str]:
    raw_state_value = getattr(volume, "state", None)
    raw_state = str(raw_state_value or "").strip()
    normalized_state = _canonicalize_volume_state_token(raw_state_value)
    return raw_state, normalized_state


def _volume_state_missing(
    volume: Any, *, raw_state: str, normalized_state: str
) -> bool:
    """Return whether the SDK response omitted a usable state token.

    Some Daytona SDK responses include a created volume ``id`` but omit the
    ``state`` field entirely. The runtime has historically treated those
    handles as ready enough to continue, and several sync workflow tests rely
    on that contract.
    """
    if raw_state or normalized_state:
        return False
    return bool(getattr(volume, "id", None))


def _raise_if_volume_error(
    volume_name: str,
    *,
    raw_state: str,
    normalized_state: str,
) -> None:
    if normalized_state in _VOLUME_ERROR_STATES:
        message = f"Volume '{volume_name}' is in error state '{normalized_state}'"
        if raw_state and raw_state != normalized_state:
            message = (
                f"Volume '{volume_name}' is in error state "
                f"'{normalized_state}' (raw='{raw_state}')"
            )
        raise DaytonaDiagnosticError(
            message,
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        )


# ---------------------------------------------------------------------------
# Volume readiness polling
# ---------------------------------------------------------------------------


async def _await_volume_ready(
    client: Any,
    volume_name: str,
    volume: Any,
    *,
    timeout: float = 60.0,
) -> Any:
    """Poll until a Daytona volume reaches ``ready`` state.

    Returns the (possibly refreshed) volume object.  Raises
    ``VolumeNotReadyError`` on timeout or ``DaytonaDiagnosticError``
    when the volume enters a terminal error state.
    """
    raw_state, state = _volume_state_details(volume)

    if _volume_state_missing(volume, raw_state=raw_state, normalized_state=state):
        return volume
    if state in _VOLUME_READY_STATES:
        return volume
    _raise_if_volume_error(
        volume_name,
        raw_state=raw_state,
        normalized_state=state,
    )

    deadline = _time.monotonic() + timeout
    interval = 1.0

    while _time.monotonic() < deadline:
        logger.debug(
            (
                "Volume '%s' not ready "
                "(raw_state=%s, normalized_state=%s, state_type=%s, state_repr=%r), "
                "polling in %.1fs"
            ),
            volume_name,
            raw_state or "<empty>",
            state or "<empty>",
            type(getattr(volume, "state", None)).__name__,
            getattr(volume, "state", None),
            interval,
        )
        await asyncio.sleep(interval)
        interval = min(interval * 2, 10.0)

        volume = await _await_if_needed(client.volume.get(volume_name))
        raw_state, state = _volume_state_details(volume)

        if _volume_state_missing(volume, raw_state=raw_state, normalized_state=state):
            return volume
        if state in _VOLUME_READY_STATES:
            return volume
        _raise_if_volume_error(
            volume_name,
            raw_state=raw_state,
            normalized_state=state,
        )

    raise VolumeNotReadyError(
        volume_name=volume_name,
        volume_state=state or raw_state or "unknown",
        raw_volume_state=raw_state or None,
        timeout_seconds=timeout,
    )


# ---------------------------------------------------------------------------
# Volume layout setup
# ---------------------------------------------------------------------------


async def _aensure_daytona_volume_layout(
    *,
    sandbox: Any,
    mounted_root: str = str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
) -> None:
    """Ensure the canonical durable directories exist on a mounted Daytona volume."""
    roots = mounted_storage_roots(mounted_root)
    try:
        for path in (
            roots.memory_root,
            roots.artifacts_root,
            roots.buffers_root,
            roots.meta_root,
        ):
            await _aensure_remote_directory(sandbox.fs, PurePosixPath(path))
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona volume layout create failure: {exc}",
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        ) from exc


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

# Default pip packages every fleet-rlm sandbox needs.
DEFAULT_SNAPSHOT_PACKAGES: list[str] = [
    "dspy-ai",
    "numpy",
    "pandas",
    "httpx",
    "pydantic",
]

DEFAULT_SNAPSHOT_NAME = "fleet-rlm-base"


async def alist_snapshots(
    config: ResolvedDaytonaConfig | None = None,
) -> list[dict[str, Any]]:
    """Return a lightweight list of available snapshots.

    Each dict contains ``name``, ``id``, ``state``, and ``image_name``.
    """
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        result = await _await_if_needed(client.snapshot.list())
        return [
            {
                "name": s.name,
                "id": s.id,
                "state": str(getattr(s, "state", "unknown")),
                "image_name": getattr(s, "image_name", None),
            }
            for s in (result.items if hasattr(result, "items") else result)
        ]
    finally:
        await _await_if_needed(client.close())


async def aget_snapshot(
    name: str,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> dict[str, Any] | None:
    """Look up a snapshot by *name*, returning a summary dict or ``None``."""
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snap = await _await_if_needed(client.snapshot.get(name))
        return {
            "name": snap.name,
            "id": snap.id,
            "state": str(getattr(snap, "state", "unknown")),
            "image_name": getattr(snap, "image_name", None),
        }
    except Exception:
        logger.debug("snapshot_lookup_failed", extra={"name": name}, exc_info=True)
        return None
    finally:
        await _await_if_needed(client.close())


async def acreate_snapshot(
    name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    base_image: str = "python:3.12-slim",
    packages: list[str] | None = None,
    config: ResolvedDaytonaConfig | None = None,
    on_logs: Any | None = None,
) -> dict[str, Any]:
    """Create a new Daytona snapshot with pre-installed packages.

    Returns a summary dict with the snapshot ``name``, ``id``, and ``state``.
    """
    from daytona import Image as DaytonaImage
    from daytona.common.snapshot import CreateSnapshotParams

    pkgs = packages if packages is not None else DEFAULT_SNAPSHOT_PACKAGES

    image = DaytonaImage.base(base_image)
    image = image.run_commands("pip install uv")
    if pkgs:
        image = image.run_commands(f"uv pip install --system {' '.join(pkgs)}")

    params = CreateSnapshotParams(name=name, image=image)
    cfg = config or resolve_daytona_config()
    client = _build_daytona_client(cfg)
    try:
        snap = await _await_if_needed(
            client.snapshot.create(params, on_logs=on_logs, timeout=0)
        )
        logger.info("Snapshot '%s' created (id=%s)", snap.name, snap.id)
        return {
            "name": snap.name,
            "id": snap.id,
            "state": str(getattr(snap, "state", "unknown")),
            "image_name": getattr(snap, "image_name", None),
        }
    finally:
        await _await_if_needed(client.close())


async def aresolve_snapshot(
    preferred_name: str = DEFAULT_SNAPSHOT_NAME,
    *,
    config: ResolvedDaytonaConfig | None = None,
) -> str | None:
    """Return the snapshot name if it exists and is ``ACTIVE``, else ``None``."""
    info = await aget_snapshot(preferred_name, config=config)
    state = str(info.get("state", "")).upper() if info else ""
    if info and state in ("ACTIVE", "SNAPSHOTSTATE.ACTIVE"):
        return info["name"]
    return None


# ---------------------------------------------------------------------------
# Admin process helpers
# ---------------------------------------------------------------------------


async def _arun_admin_code(
    *,
    sandbox: Any,
    code: str,
    phase: str,
    error_prefix: str,
    category: str = "sandbox_create_clone_error",
) -> str:
    """Run *code* inside the sandbox via ``sandbox.process.code_run``.

    This is the preferred path for Daytona SDK v0.167.0+ because code
    execution is handled server-side by the daemon.
    """
    try:
        from daytona.common.process import CodeRunParams

        result = await _await_if_needed(
            sandbox.process.code_run(code, params=CodeRunParams())
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


# ---------------------------------------------------------------------------
# Experimental SDK wrapper
# ---------------------------------------------------------------------------


async def _experimental_call(
    sandbox: Any,
    method_name: str,
    *args: Any,
    category: str = "sandbox_experimental_error",
    phase: str = "sandbox_experimental",
    **kwargs: Any,
) -> Any:
    """Safely invoke an experimental Daytona SDK method on *sandbox*."""
    try:
        method = getattr(sandbox, method_name)
        return await _await_if_needed(method(*args, **kwargs))
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona {method_name} failure: {exc}",
            category=category,
            phase=phase,
        ) from exc


def _current_async_owner() -> tuple[int, int]:
    return (threading.get_ident(), id(asyncio.get_running_loop()))


# ---------------------------------------------------------------------------
# DaytonaSandboxSession
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
    owner_loop_id: int | None = None
    execution_event_callback: Any | None = None
    _context: Any | None = field(default=None, init=False, repr=False)
    _driver_started: bool = field(default=False, init=False, repr=False)
    # Optional back-reference to the runtime that created this session's
    # sandbox.  When set, aensure_context() uses it to re-obtain a fresh
    # sandbox handle (bound to the current asyncio loop) if a cross-loop
    # call is detected, preventing "Future attached to a different loop"
    # errors in delegate-child interpreters.
    _runtime_ref: Any | None = field(default=None, init=False, repr=False)

    @property
    def sandbox_id(self) -> str | None:
        return str(getattr(self.sandbox, "id", "") or "") or None

    def bind_current_async_owner(self) -> None:
        self.owner_thread_id, self.owner_loop_id = _current_async_owner()

    def matches_current_async_owner(self) -> bool:
        if self.owner_thread_id is None or self.owner_loop_id is None:
            return False
        try:
            return (self.owner_thread_id, self.owner_loop_id) == _current_async_owner()
        except RuntimeError:
            return False

    async def aensure_context(self) -> Any:
        if self._context is not None:
            return self._context
        # If this session's sandbox handle was created on a different asyncio
        # event loop (e.g. the compat-runner's background loop), its internal
        # aiohttp session will be bound to that old loop and any awaited SDK
        # call will raise "Future attached to a different loop".  Re-obtain
        # a fresh sandbox handle via the runtime (which rebuilds its own HTTP
        # client when the loop changes) before calling create_context().
        if not self.matches_current_async_owner() and self._runtime_ref is not None:
            sandbox_id = self.sandbox_id
            if sandbox_id:
                with suppress(Exception):
                    self.sandbox = await self._runtime_ref._aget_sandbox(
                        sandbox_id, recover=False
                    )
                    self.bind_current_async_owner()
        if self.context_id:
            existing_contexts: list[Any] | None = None
            with suppress(Exception):
                existing_contexts = await _await_if_needed(
                    self.sandbox.code_interpreter.list_contexts()
                )
            if existing_contexts is not None:
                for existing in existing_contexts:
                    if str(getattr(existing, "id", "") or "") == self.context_id:
                        self._context = existing
                        return existing
        context = await _await_if_needed(
            self.sandbox.code_interpreter.create_context(cwd=self.workspace_path)
        )
        self._context = context
        self.context_id = str(getattr(context, "id", "") or "") or None
        return context

    def ensure_context(self) -> Any:
        return _run_async_compat(self.aensure_context)

    async def astart_driver(self, *, timeout: float = 30.0) -> None:
        _ = timeout
        await self.aensure_context()
        self._driver_started = True

    def start_driver(self, *, timeout: float = 30.0) -> None:
        _run_async_compat(self.astart_driver, timeout=timeout)

    async def aclose_driver(self) -> None:
        self._driver_started = False

    def close_driver(self) -> None:
        _run_async_compat(self.aclose_driver)

    def list_files(self, path: str) -> list[Any]:
        return _run_async_compat(self.alist_files, path)

    async def adelete_context(self) -> None:
        context = self._context
        self._context = None
        if context is None and self.context_id:
            with suppress(Exception):
                existing_contexts = await _await_if_needed(
                    self.sandbox.code_interpreter.list_contexts()
                )
                for existing in existing_contexts:
                    if str(getattr(existing, "id", "") or "") == self.context_id:
                        context = existing
                        break
        if context is not None:
            with suppress(Exception):
                await _await_if_needed(
                    self.sandbox.code_interpreter.delete_context(context)
                )
        self.context_id = None
        self._driver_started = False

    def delete_context(self) -> None:
        _run_async_compat(self.adelete_context)

    def _resolve_sandbox_path(self, path: str) -> str:
        candidate = PurePosixPath(str(path or "").strip() or ".")
        if candidate.is_absolute():
            return str(candidate)
        return str(PurePosixPath(self.workspace_path) / candidate)

    async def _arebind_sandbox_if_needed(self) -> None:
        if self.matches_current_async_owner() or self._runtime_ref is None:
            return
        sandbox_id = self.sandbox_id
        if not sandbox_id:
            return
        with suppress(Exception):
            self.sandbox = await self._runtime_ref._aget_sandbox(
                sandbox_id, recover=False
            )
            self.bind_current_async_owner()

    async def aread_file(self, path: str) -> str:
        await self._arebind_sandbox_if_needed()
        raw = await _await_if_needed(
            self.sandbox.fs.download_file(self._resolve_sandbox_path(path))
        )
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        return bytes(raw).decode("utf-8", errors="replace")

    def read_file(self, path: str) -> str:
        return _run_async_compat(self.aread_file, path)

    async def awrite_file(self, path: str, content: str) -> str:
        await self._arebind_sandbox_if_needed()
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
        await _await_if_needed(self.sandbox.fs.upload_file(payload, resolved_path))
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

    def write_file(self, path: str, content: str) -> str:
        return _run_async_compat(self.awrite_file, path, content)

    async def alist_files(self, path: str) -> list[Any]:
        await self._arebind_sandbox_if_needed()
        entries = await _await_if_needed(
            self.sandbox.fs.list_files(self._resolve_sandbox_path(path))
        )
        return list(entries)

    async def adelete(self) -> None:
        await self.adelete_context()
        # Graceful stop before delete to let processes flush/clean up
        with suppress(Exception):
            await _await_if_needed(self.sandbox.stop(timeout=10))
        with suppress(Exception):
            await _await_if_needed(self.sandbox.delete())
        self._driver_started = False

    def delete(self) -> None:
        _run_async_compat(self.adelete)

    async def aarchive(self) -> None:
        """Archive the sandbox for later recovery (cheaper than keeping it running)."""
        with suppress(Exception):
            await _await_if_needed(self.sandbox.archive())

    def archive(self) -> None:
        _run_async_compat(self.aarchive)

    async def arecover(self, *, timeout: float = 60.0) -> None:
        """Recover an archived sandbox, restoring it to a running state."""
        await _await_if_needed(self.sandbox.recover(timeout=timeout))

    def recover(self, *, timeout: float = 60.0) -> None:
        _run_async_compat(self.arecover, timeout=timeout)

    async def arefresh_activity(self) -> None:
        """Reset the sandbox auto-stop timer without changing state."""
        with suppress(Exception):
            await _await_if_needed(self.sandbox.refresh_activity())

    def refresh_activity(self) -> None:
        _run_async_compat(self.arefresh_activity)

    async def aresize(self, *, cpu: int, memory: int, disk: int) -> None:
        """Hot-resize sandbox resources (CPU cores, memory GiB, disk GiB)."""
        from daytona import Resources

        await _await_if_needed(
            self.sandbox.resize(Resources(cpu=cpu, memory=memory, disk=disk))
        )

    def resize(self, *, cpu: int, memory: int, disk: int) -> None:
        _run_async_compat(self.aresize, cpu=cpu, memory=memory, disk=disk)

    def create_lsp_server(
        self, *, language: str = "python", project_path: str | None = None
    ) -> Any:
        """Create a Daytona LSP server for code intelligence in the sandbox.

        Returns the raw ``LspServer`` handle from the Daytona SDK.  The caller
        must call ``await lsp.start()`` before using it and ``await lsp.stop()``
        when finished.
        """
        path = project_path or self.workspace_path
        return self.sandbox.create_lsp_server(language, path)


# ---------------------------------------------------------------------------
# DaytonaSandboxRuntime
# ---------------------------------------------------------------------------


class DaytonaSandboxRuntime:
    """Factory for Daytona sandboxes used by the pilot."""

    # Default labels applied to all sandboxes created by this runtime
    DEFAULT_LABELS: dict[str, str] = {"managed-by": "fleet-rlm"}

    def __init__(self, *, config: ResolvedDaytonaConfig | None = None) -> None:
        resolved = config or resolve_daytona_config()
        self._resolved_config = resolved
        self._client: Any | None = None
        self._client_owner: tuple[int, int] | None = None
        self._closed = False

    async def _aget_client(self) -> Any:
        if self._closed:
            raise RuntimeError("Daytona runtime client is closed")
        owner = (threading.get_ident(), id(asyncio.get_running_loop()))
        client = self._client
        if client is None:
            client = _build_daytona_client(self._resolved_config)
            self._client = client
            self._client_owner = owner
            return client
        if self._client_owner is None:
            self._client_owner = owner
            return client
        if self._client_owner == owner:
            return client
        close = getattr(client, "close", None)
        if close is not None and callable(close):
            with suppress(Exception):
                await _await_if_needed(close())
        rebuilt = _build_daytona_client(self._resolved_config)
        self._client = rebuilt
        self._client_owner = owner
        return rebuilt

    async def aclose(self) -> None:
        self._closed = True
        client = self._client
        self._client = None
        self._client_owner = None
        if client is None:
            return
        close = getattr(client, "close", None)
        if close is None or not callable(close):
            return
        await _await_if_needed(close())

    def close(self) -> None:
        _run_async_compat(self.aclose)

    @staticmethod
    def _default_sandbox_name() -> str:
        return f"fleet-rlm-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d-%H%M%S}"

    @staticmethod
    def _resolve_default_snapshot(*, image: Any, snapshot: str | None) -> str | None:
        if snapshot or image:
            return snapshot
        return DEFAULT_SNAPSHOT_NAME

    def _merge_sandbox_labels(
        self, labels: dict[str, str] | None = None
    ) -> dict[str, str]:
        merged_labels = dict(self.DEFAULT_LABELS)
        if labels:
            merged_labels.update(labels)
        return merged_labels

    def build_sandbox_spec(
        self,
        *,
        name: str | None = None,
        volume_name: str | None = None,
        image: Any = None,
        snapshot: str | None = None,
        env_vars: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
        cpu: int | None = None,
        memory: int | None = None,
        disk: int | None = None,
        auto_stop_interval: int | None = 30,
        auto_archive_interval: int | None = 60,
        auto_delete_interval: int | None = None,
        network_block_all: bool | None = None,
        network_allow_list: str | None = None,
    ) -> SandboxSpec:
        """Build a ``SandboxSpec`` with runtime defaults applied.

        The ``image`` parameter accepts a ``daytona.Image`` declarative
        builder object (e.g. ``Image.debian_slim().pip_install(...)``).

        When neither ``image`` nor ``snapshot`` is provided, the spec
        defaults to the ``fleet-rlm-base`` snapshot so that sandboxes
        start with pre-installed core packages (dspy-ai, numpy, pandas,
        httpx, pydantic).  If the snapshot has not been created yet, the
        runtime falls back to a declarative image build at sandbox
        creation time (see ``_acreate_sandbox``).

        Cost-saving lifecycle defaults:

        * ``auto_stop_interval`` — minutes of inactivity before the
          sandbox is automatically stopped (default 30).
          ``refresh_activity()`` resets the timer.
        * ``auto_archive_interval`` — minutes after stop before the
          sandbox is archived to cold storage (default 60).
        * ``auto_delete_interval`` — minutes after archive before
          permanent deletion (default ``None`` = never auto-delete).

        A human-readable ``name`` is generated automatically when not
        supplied, producing dashboard-friendly labels like
        ``fleet-rlm-20260404-090700`` instead of random hex IDs.
        """
        return SandboxSpec(
            name=name or self._default_sandbox_name(),
            language="python",
            image=image,
            snapshot=self._resolve_default_snapshot(image=image, snapshot=snapshot),
            volume_name=volume_name,
            volume_mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
            env_vars=env_vars or None,
            labels=self._merge_sandbox_labels(labels),
            ephemeral=True,
            auto_stop_interval=auto_stop_interval,
            auto_archive_interval=auto_archive_interval,
            auto_delete_interval=auto_delete_interval,
            cpu=cpu,
            memory=memory,
            disk=disk,
            network_block_all=network_block_all,
            network_allow_list=network_allow_list,
        )

    async def _acreate_sandbox_from_spec(self, spec: SandboxSpec) -> Any:
        """Create a sandbox using a declarative ``SandboxSpec``.

        When the spec carries a ``daytona.Image`` declarative builder,
        the sandbox is created via ``CreateSandboxFromImageParams`` and
        Daytona caches the built image for 24 hours.  Otherwise a
        snapshot-based sandbox is created.
        """
        try:
            from daytona import (
                CreateSandboxFromImageParams,
                CreateSandboxFromSnapshotParams,
                Resources,
                VolumeMount,
            )
        except ImportError as exc:  # pragma: no cover - environment specific
            raise _daytona_import_error(exc) from exc

        client = await self._aget_client()

        # Resolve volume if requested, waiting for it to become ready
        volume_id: str | None = None
        if spec.volume_name:
            volume = await _await_if_needed(
                client.volume.get(spec.volume_name, create=True)
            )
            volume = await _await_volume_ready(client, spec.volume_name, volume)
            volume_id = volume.id

        create_kwargs = spec.to_create_params(volume_id=volume_id)

        # Reconstruct VolumeMount objects from the raw dicts
        raw_volumes = create_kwargs.pop("volumes", None)
        if raw_volumes:
            create_kwargs["volumes"] = [VolumeMount(**v) for v in raw_volumes]

        # Reconstruct Resources from the raw dict
        raw_resources = create_kwargs.pop("resources", None)
        if raw_resources:
            create_kwargs["resources"] = Resources(**raw_resources)

        if spec.uses_declarative_image:
            # Image object is already in create_kwargs via to_create_params
            params = CreateSandboxFromImageParams(**create_kwargs)
            return await _await_if_needed(
                client.create(params, timeout=0, on_snapshot_create_logs=lambda _: None)
            )
        else:
            params = CreateSandboxFromSnapshotParams(**create_kwargs)
            return await _await_if_needed(client.create(params))

    async def _acreate_sandbox(
        self,
        volume_name: str | None = None,
        *,
        spec: SandboxSpec | None = None,
    ) -> Any:
        """Create a sandbox, optionally from a declarative spec.

        When the spec requests a named snapshot, the runtime first checks
        whether that snapshot is ``ACTIVE``.  If it is not available, the
        runtime transparently falls back to a declarative image build
        using ``DEFAULT_SNAPSHOT_PACKAGES`` so the sandbox still starts
        with the expected packages pre-installed.
        """
        try:
            resolved_spec = spec or self.build_sandbox_spec(volume_name=volume_name)

            # If a snapshot is requested but not yet created/active, fall
            # back to a declarative image build with the same packages.
            if resolved_spec.snapshot and not resolved_spec.uses_declarative_image:
                active = await aresolve_snapshot(
                    resolved_spec.snapshot, config=self._resolved_config
                )
                if active is None:
                    logger.info(
                        "Snapshot '%s' not active; falling back to declarative image",
                        resolved_spec.snapshot,
                    )
                    resolved_spec = self._fallback_to_declarative_image(resolved_spec)

            return await self._acreate_sandbox_from_spec(resolved_spec)
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona sandbox create failure: {exc}",
                category="sandbox_create_clone_error",
                phase="sandbox_create",
            ) from exc

    @staticmethod
    def _fallback_to_declarative_image(spec: SandboxSpec) -> SandboxSpec:
        """Replace a snapshot-based spec with a declarative image build."""
        try:
            from daytona import Image as DaytonaImage
        except ImportError as exc:  # pragma: no cover
            raise _daytona_import_error(exc) from exc

        image = DaytonaImage.base("python:3.12-slim")
        image = image.run_commands("pip install uv")
        if DEFAULT_SNAPSHOT_PACKAGES:
            image = image.run_commands(
                f"uv pip install --system {' '.join(DEFAULT_SNAPSHOT_PACKAGES)}"
            )

        return dataclasses.replace(spec, image=image, snapshot=None)

    async def _aget_sandbox(self, sandbox_id: str, *, recover: bool = True) -> Any:
        """Get an existing sandbox by ID, recovering from archive if needed."""
        try:
            client = await self._aget_client()
            sandbox = await _await_if_needed(client.get(sandbox_id))
            # Recover archived sandboxes automatically
            if recover:
                state = getattr(sandbox, "state", None)
                state_value = getattr(state, "value", str(state or ""))
                if str(state_value).lower() in ("archived", "stopped"):
                    await _await_if_needed(sandbox.recover(timeout=60))
            return sandbox
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona sandbox resume failure: {exc}",
                category="sandbox_resume_error",
                phase="sandbox_resume",
            ) from exc

    def _build_workspace_session(
        self,
        *,
        sandbox: Any,
        repo_url: str | None,
        resolved_ref: str | None,
        volume_name: str | None,
        workspace_path: str,
        context_sources: list[ContextSource],
        timings: dict[str, int],
        context_id: str | None = None,
    ) -> DaytonaSandboxSession:
        session = DaytonaSandboxSession(
            sandbox=sandbox,
            repo_url=repo_url,
            ref=resolved_ref,
            volume_name=volume_name,
            workspace_path=workspace_path,
            context_sources=context_sources,
            volume_mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
            context_id=context_id,
        )
        session._runtime_ref = self
        session.phase_timings_ms.update(timings)
        session.bind_current_async_owner()
        return session

    async def acreate_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
        volume_name: str | None = None,
        spec: SandboxSpec | None = None,
    ) -> DaytonaSandboxSession:
        from .filesystem import (
            _abuild_workspace_path,
            _aclone_repo,
            _aresolve_clone_ref,
            _aensure_workspace_root,
            _astage_context_paths,
        )

        timings = {"sandbox_create": 0, "repo_clone": 0, "context_stage": 0}
        sandbox: Any | None = None
        resolved_spec = spec or self.build_sandbox_spec(volume_name=volume_name)
        try:
            create_started = time.perf_counter()
            sandbox = await self._acreate_sandbox(spec=resolved_spec)
            timings["sandbox_create"] = int(
                (time.perf_counter() - create_started) * 1000
            )
            effective_volume = resolved_spec.volume_name or volume_name
            if effective_volume:
                await _aensure_daytona_volume_layout(
                    sandbox=sandbox,
                    mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
                )

            workspace_path = await _abuild_workspace_path(sandbox, repo_url)
            resolved_ref = await _aresolve_clone_ref(repo_url, ref) if repo_url else ref
            if repo_url:
                clone_started = time.perf_counter()
                await _aclone_repo(
                    sandbox=sandbox,
                    repo_url=repo_url,
                    ref=resolved_ref,
                    workspace_path=workspace_path,
                )
                timings["repo_clone"] = int(
                    (time.perf_counter() - clone_started) * 1000
                )
            else:
                await _aensure_workspace_root(
                    sandbox=sandbox,
                    workspace_path=workspace_path,
                )

            context_started = time.perf_counter()
            context_sources = await _astage_context_paths(
                sandbox=sandbox,
                workspace_path=workspace_path,
                context_paths=context_paths,
            )
            timings["context_stage"] = int(
                (time.perf_counter() - context_started) * 1000
            )
            return self._build_workspace_session(
                sandbox=sandbox,
                repo_url=repo_url,
                resolved_ref=resolved_ref,
                volume_name=effective_volume,
                workspace_path=workspace_path,
                context_sources=context_sources,
                timings=timings,
            )
        except Exception:
            if sandbox is not None:
                with suppress(Exception):
                    await _await_if_needed(sandbox.delete())
            raise

    def create_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
        volume_name: str | None = None,
        spec: SandboxSpec | None = None,
    ) -> DaytonaSandboxSession:
        return _run_async_compat(
            self.acreate_workspace_session,
            repo_url=repo_url,
            ref=ref,
            context_paths=context_paths,
            volume_name=volume_name,
            spec=spec,
        )

    async def aresume_workspace_session(
        self,
        *,
        sandbox_id: str,
        repo_url: str | None,
        ref: str | None,
        volume_name: str | None = None,
        workspace_path: str,
        context_sources: list[ContextSource] | None = None,
        context_id: str | None = None,
    ) -> DaytonaSandboxSession:
        resumed_started = time.perf_counter()
        sandbox = await self._aget_sandbox(sandbox_id)
        session = self._build_workspace_session(
            sandbox=sandbox,
            repo_url=repo_url,
            resolved_ref=ref,
            volume_name=volume_name,
            workspace_path=workspace_path,
            context_sources=list(context_sources or []),
            timings={
                "sandbox_resume": int((time.perf_counter() - resumed_started) * 1000)
            },
            context_id=context_id,
        )
        if volume_name:
            await _aensure_daytona_volume_layout(
                sandbox=sandbox,
                mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
            )
        return session

    def resume_workspace_session(
        self,
        *,
        sandbox_id: str,
        repo_url: str | None,
        ref: str | None,
        volume_name: str | None = None,
        workspace_path: str,
        context_sources: list[ContextSource] | None = None,
        context_id: str | None = None,
    ) -> DaytonaSandboxSession:
        return _run_async_compat(
            self.aresume_workspace_session,
            sandbox_id=sandbox_id,
            repo_url=repo_url,
            ref=ref,
            volume_name=volume_name,
            workspace_path=workspace_path,
            context_sources=context_sources,
            context_id=context_id,
        )

    async def afork_sandbox(
        self,
        session: DaytonaSandboxSession,
        *,
        name: str | None = None,
        timeout: float = 60.0,
    ) -> DaytonaSandboxSession:
        """Fork a sandbox session, creating a copy-on-write clone.

        Wraps the Daytona SDK's experimental ``_experimental_fork`` method.
        """
        forked = await _experimental_call(
            session.sandbox,
            "_experimental_fork",
            name=name,
            timeout=timeout,
            category="sandbox_fork_error",
            phase="sandbox_fork",
        )
        return self._build_workspace_session(
            sandbox=forked,
            repo_url=session.repo_url,
            resolved_ref=session.ref,
            volume_name=session.volume_name,
            workspace_path=session.workspace_path,
            context_sources=list(session.context_sources),
            timings={"sandbox_fork": 0},
        )

    def fork_sandbox(
        self,
        session: DaytonaSandboxSession,
        *,
        name: str | None = None,
        timeout: float = 60.0,
    ) -> DaytonaSandboxSession:
        return _run_async_compat(
            self.afork_sandbox,
            session,
            name=name,
            timeout=timeout,
        )

    async def acreate_sandbox_snapshot(
        self,
        session: DaytonaSandboxSession,
        *,
        name: str,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Create a snapshot from the current state of a sandbox session.

        Wraps the Daytona SDK's experimental ``_experimental_create_snapshot``
        method.
        """
        await _experimental_call(
            session.sandbox,
            "_experimental_create_snapshot",
            name=name,
            timeout=timeout,
            category="sandbox_snapshot_error",
            phase="sandbox_snapshot",
        )
        return {
            "name": name,
            "sandbox_id": session.sandbox_id,
            "status": "created",
        }

    def create_sandbox_snapshot(
        self,
        session: DaytonaSandboxSession,
        *,
        name: str,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        return _run_async_compat(
            self.acreate_sandbox_snapshot,
            session,
            name=name,
            timeout=timeout,
        )

    async def areconcile_workspace_session(
        self,
        session: DaytonaSandboxSession,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
    ) -> DaytonaSandboxSession:
        from .filesystem import (
            _abuild_workspace_path,
            _areconcile_repo_checkout,
            _aresolve_clone_ref,
            _astage_context_paths,
        )

        workspace_started = time.perf_counter()
        workspace_path = await _abuild_workspace_path(session.sandbox, repo_url)
        resolved_ref = await _aresolve_clone_ref(repo_url, ref) if repo_url else ref
        await _areconcile_repo_checkout(
            sandbox=session.sandbox,
            repo_url=repo_url,
            ref=resolved_ref,
            workspace_path=workspace_path,
        )
        session.phase_timings_ms["workspace_reconcile"] = int(
            (time.perf_counter() - workspace_started) * 1000
        )

        context_started = time.perf_counter()
        context_sources = await _astage_context_paths(
            sandbox=session.sandbox,
            workspace_path=workspace_path,
            context_paths=context_paths,
            reset_existing=True,
        )
        session.phase_timings_ms["context_stage"] = int(
            (time.perf_counter() - context_started) * 1000
        )
        session.repo_url = repo_url
        session.ref = resolved_ref
        session.workspace_path = workspace_path
        session.context_sources = context_sources
        session.bind_current_async_owner()
        return session

    def reconcile_workspace_session(
        self,
        session: DaytonaSandboxSession,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
    ) -> DaytonaSandboxSession:
        return _run_async_compat(
            self.areconcile_workspace_session,
            session,
            repo_url=repo_url,
            ref=ref,
            context_paths=context_paths,
        )


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    "DEFAULT_SNAPSHOT_NAME",
    "DEFAULT_SNAPSHOT_PACKAGES",
    "DaytonaSandboxRuntime",
    "DaytonaSandboxSession",
    "SandboxSpec",
    "acreate_snapshot",
    "aget_snapshot",
    "alist_snapshots",
    "aresolve_snapshot",
]
