"""Direct-SDK workspace bootstrap and session helpers for Daytona sandboxes."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from .async_compat import _run_async_compat, _run_sync_in_thread
from .concurrency import acquire_sandbox_slot, attach_slot_release_handler, release_sandbox_slot
from .config import ResolvedDaytonaConfig, resolve_daytona_config
from .config import build_daytona_client as _build_daytona_client
from .config import (
    daytona_import_error as _daytona_import_error,
)
from .config import (
    format_daytona_sdk_error as _format_daytona_sdk_error,
)
from .diagnostics import DaytonaDiagnosticError
from .models import (
    ContextSource,
    SandboxSpec,
)
from .models import (
    build_sandbox_spec as _build_sandbox_spec_helper,
)
from .models import (
    default_sandbox_name as _default_sandbox_name_helper,
)
from .models import (
    merge_sandbox_labels as _merge_sandbox_labels_helper,
)
from .sdk_ops import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    DEFAULT_SNAPSHOT_NAME,
    DEFAULT_SNAPSHOT_PACKAGES,
    acreate_snapshot,
    aget_snapshot,
    alist_snapshots,
    aresolve_sandbox_spec_snapshot,
    aresolve_snapshot,
)
from .sdk_ops import (
    acreate_sandbox_snapshot as _acreate_sandbox_snapshot_helper,
)
from .sdk_ops import (
    afork_sandbox as _afork_sandbox,
)
from .sdk_ops import (
    aget_sandbox as _aget_sandbox_helper,
)
from .sdk_ops import (
    aresume_workspace_session as _aresume_workspace_session,
)
from .sdk_ops import (
    await_volume_ready as _await_volume_ready,
)
from .sdk_ops import (
    fallback_to_declarative_image as _fallback_to_declarative_image,
)
from .sdk_ops import (
    resolve_default_snapshot as _resolve_default_snapshot,
)
from .session_runtime import DaytonaSandboxSession
from .workspace_runtime import (
    WorkspaceSessionCreateRequest,
    WorkspaceSessionReconcileRequest,
)
from .workspace_runtime import (
    acreate_workspace_session as _acreate_workspace_session_helper,
)
from .workspace_runtime import (
    areconcile_workspace_session as _areconcile_workspace_session_helper,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DaytonaSandboxRuntime
# ---------------------------------------------------------------------------


class DaytonaSandboxRuntime:
    """Factory for Daytona sandboxes used by the pilot."""

    # Default labels applied to all sandboxes created by this runtime
    DEFAULT_LABELS: dict[str, str] = dict({"managed-by": "fleet-rlm"})

    def __init__(self, *, config: ResolvedDaytonaConfig | None = None) -> None:
        resolved = config or resolve_daytona_config()
        self._resolved_config = resolved
        self._client: Any | None = None
        self._client_lock = threading.Lock()
        self._closed = False

    def _get_client(self) -> Any:
        """Return the cached sync Daytona client, building one if needed."""
        with self._client_lock:
            if self._closed:
                raise RuntimeError("Daytona runtime client is closed")
            if self._client is None:
                self._client = _build_daytona_client(self._resolved_config)
            return self._client

    def close(self) -> None:
        """Close the runtime and release the underlying client."""
        with self._client_lock:
            self._closed = True
            client = self._client
            self._client = None
        if client is None:
            return
        close = getattr(client, "close", None)
        if close is not None and callable(close):
            close()

    async def aclose(self) -> None:
        await _run_sync_in_thread(self.close)

    @staticmethod
    def _default_sandbox_name() -> str:
        return _default_sandbox_name_helper()

    @staticmethod
    def _resolve_default_snapshot(*, image: Any, snapshot: str | None) -> str | None:
        return _resolve_default_snapshot(image=image, snapshot=snapshot)

    def _merge_sandbox_labels(self, labels: dict[str, str] | None = None) -> dict[str, str]:
        return _merge_sandbox_labels_helper(
            default_labels=self.DEFAULT_LABELS,
            labels=labels,
        )

    def build_sandbox_spec(
        self,
        *,
        name: str | None = None,
        volume_name: str | None = None,
        volume_subpath: str | None = None,
        image: Any = None,
        snapshot: str | None = None,
        env_vars: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
        cpu: int | None = None,
        memory: int | None = None,
        disk: int | None = None,
        recoverable: bool = True,
        runner_tags: list[str] | None = None,
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
        creation time (see ``create_sandbox``).

        Cost-saving lifecycle defaults:

        * ``auto_stop_interval`` -- minutes of inactivity before the
          sandbox is automatically stopped (default 30).
          ``refresh_activity()`` resets the timer.
        * ``auto_archive_interval`` -- minutes after stop before the
          sandbox is archived to cold storage (default 60).
        * ``auto_delete_interval`` -- minutes after archive before
          permanent deletion (default ``None`` = never auto-delete).

        A human-readable ``name`` is generated automatically when not
        supplied, producing dashboard-friendly labels like
        ``fleet-rlm-20260404-090700`` instead of random hex IDs.
        """
        return _build_sandbox_spec_helper(
            default_labels=self.DEFAULT_LABELS,
            name=name,
            volume_name=volume_name,
            volume_subpath=volume_subpath,
            image=image,
            snapshot=snapshot,
            env_vars=env_vars,
            labels=labels,
            cpu=cpu,
            memory=memory,
            disk=disk,
            recoverable=recoverable,
            runner_tags=runner_tags,
            auto_stop_interval=auto_stop_interval,
            auto_archive_interval=auto_archive_interval,
            auto_delete_interval=auto_delete_interval,
            network_block_all=network_block_all,
            network_allow_list=network_allow_list,
        )

    # ------------------------------------------------------------------
    # Sandbox creation (inlined from sandbox_lifecycle)
    # ------------------------------------------------------------------

    @staticmethod
    def _ignore_snapshot_create_logs(_message: Any) -> None:
        return None

    def _resolve_volume_id(self, spec: SandboxSpec) -> str | None:
        """Resolve a volume ID for the given spec, creating the volume if needed."""
        if not spec.volume_name:
            return None
        client = self._get_client()
        volume = client.volume.get(spec.volume_name, create=True)
        volume = _await_volume_ready(client, spec.volume_name, volume)
        return str(volume.id)

    def _create_sandbox_from_spec_impl(self, spec: SandboxSpec) -> Any:
        """Internal sync implementation: create a sandbox from a ``SandboxSpec``."""
        try:
            from daytona import (
                CreateSandboxFromImageParams,
                CreateSandboxFromSnapshotParams,
                Resources,
                VolumeMount,
            )
        except ImportError as exc:  # pragma: no cover - environment specific
            raise _daytona_import_error(exc) from exc

        client = self._get_client()
        volume_id = self._resolve_volume_id(spec)
        params = spec.to_daytona_create_params(
            volume_id=volume_id,
            create_image_params_cls=CreateSandboxFromImageParams,
            create_snapshot_params_cls=CreateSandboxFromSnapshotParams,
            volume_mount_cls=VolumeMount,
            resources_cls=Resources,
        )

        if spec.uses_declarative_image:
            return client.create(
                params,
                timeout=0,
                on_snapshot_create_logs=self._ignore_snapshot_create_logs,
            )

        return client.create(params)

    def create_sandbox_from_spec(self, spec: SandboxSpec) -> Any:
        """Create a sandbox from a declarative ``SandboxSpec`` (sync).

        When the spec carries a ``daytona.Image`` declarative builder,
        the sandbox is created via ``CreateSandboxFromImageParams`` and
        Daytona caches the built image for 24 hours. Otherwise a
        snapshot-based sandbox is created.

        Note: This does NOT acquire a concurrency slot. Use
        ``create_sandbox`` for the full lifecycle with slot management.
        """
        return self._create_sandbox_from_spec_impl(spec)

    async def acreate_sandbox_from_spec(self, spec: SandboxSpec) -> Any:
        """Create a sandbox from a declarative ``SandboxSpec`` (async).

        Runs the synchronous Daytona SDK call in a thread to avoid
        blocking the event loop.
        """
        return await _run_sync_in_thread(self._create_sandbox_from_spec_impl, spec)

    def create_sandbox(
        self,
        volume_name: str | None = None,
        *,
        spec: SandboxSpec | None = None,
    ) -> Any:
        """Create a sandbox with concurrency control and snapshot fallback (sync).

        Acquires a global semaphore slot, resolves snapshot availability,
        and attaches a slot-release handler to the resulting sandbox.
        """
        return _run_async_compat(
            self.acreate_sandbox,
            volume_name=volume_name,
            spec=spec,
        )

    async def acreate_sandbox(
        self,
        volume_name: str | None = None,
        *,
        spec: SandboxSpec | None = None,
    ) -> Any:
        """Create a sandbox with concurrency control and snapshot fallback (async).

        Acquires a global concurrency slot before creating. If the semaphore is
        at capacity, waits up to 60 seconds before raising a busy error.
        """
        slot_acquired = False
        try:
            await acquire_sandbox_slot(timeout=60.0)
            slot_acquired = True
        except asyncio.TimeoutError as exc:
            raise DaytonaDiagnosticError(
                "Sandbox concurrency limit reached: all slots occupied. Wait briefly and retry.",
                category="sandbox_concurrency_busy",
                phase="sandbox_create",
            ) from exc

        try:
            resolved_spec = spec or self.build_sandbox_spec(volume_name=volume_name)
            resolved_spec = aresolve_sandbox_spec_snapshot(
                resolved_spec,
                config=self._resolved_config,
            )
            sandbox = self._create_sandbox_from_spec_impl(resolved_spec)
            # Attach semaphore release callback to sandbox for cleanup
            if sandbox is not None:
                attach_slot_release_handler(sandbox)
            else:
                release_sandbox_slot()
            return sandbox
        except Exception as exc:
            # Release slot on failure since sandbox won't be returned
            if slot_acquired:
                release_sandbox_slot()
            raise DaytonaDiagnosticError(
                f"Daytona sandbox create failure: {_format_daytona_sdk_error(exc)}",
                category="sandbox_create_clone_error",
                phase="sandbox_create",
            ) from exc

    @staticmethod
    def _fallback_to_declarative_image(spec: SandboxSpec) -> SandboxSpec:
        """Replace a snapshot-based spec with a declarative image build."""
        return _fallback_to_declarative_image(spec)

    def _get_sandbox(self, sandbox_id: str, *, recover: bool = True) -> Any:
        """Get an existing sandbox by ID, recovering from archive if needed."""
        return _aget_sandbox_helper(
            runtime=self,
            sandbox_id=sandbox_id,
            recover=recover,
        )

    async def _aget_sandbox(self, sandbox_id: str, *, recover: bool = True) -> Any:
        return await _run_sync_in_thread(self._get_sandbox, sandbox_id, recover=recover)

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
        return session

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
            _acreate_workspace_session_helper,
            runtime=self,
            request=WorkspaceSessionCreateRequest(
                repo_url=repo_url,
                ref=ref,
                context_paths=context_paths or [],
                volume_name=volume_name,
                spec=spec,
            ),
        )

    async def acreate_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
        volume_name: str | None = None,
        spec: SandboxSpec | None = None,
    ) -> DaytonaSandboxSession:
        return await _acreate_workspace_session_helper(
            runtime=self,
            request=WorkspaceSessionCreateRequest(
                repo_url=repo_url,
                ref=ref,
                context_paths=context_paths or [],
                volume_name=volume_name,
                spec=spec,
            ),
        )

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
        return _aresume_workspace_session(
            runtime=self,
            sandbox_id=sandbox_id,
            repo_url=repo_url,
            ref=ref,
            volume_name=volume_name,
            workspace_path=workspace_path,
            context_sources=context_sources,
            context_id=context_id,
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
        return await _run_sync_in_thread(
            self.resume_workspace_session,
            sandbox_id=sandbox_id,
            repo_url=repo_url,
            ref=ref,
            volume_name=volume_name,
            workspace_path=workspace_path,
            context_sources=context_sources,
            context_id=context_id,
        )

    def fork_sandbox(
        self,
        session: DaytonaSandboxSession,
        *,
        name: str | None = None,
        timeout: float = 60.0,
    ) -> DaytonaSandboxSession:
        """Fork a sandbox session, creating a copy-on-write clone.

        Wraps the Daytona SDK's experimental ``_experimental_fork`` method.
        """
        return _afork_sandbox(
            runtime=self,
            session=session,
            name=name,
            timeout=timeout,
        )

    async def afork_sandbox(
        self,
        session: DaytonaSandboxSession,
        *,
        name: str | None = None,
        timeout: float = 60.0,
    ) -> DaytonaSandboxSession:
        return await _run_sync_in_thread(self.fork_sandbox, session, name=name, timeout=timeout)

    def create_sandbox_snapshot(
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
        return _acreate_sandbox_snapshot_helper(
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
        return await _run_sync_in_thread(self.create_sandbox_snapshot, session, name=name, timeout=timeout)

    def reconcile_workspace_session(
        self,
        session: DaytonaSandboxSession,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
    ) -> DaytonaSandboxSession:
        return _areconcile_workspace_session_helper(
            session=session,
            request=WorkspaceSessionReconcileRequest(
                repo_url=repo_url,
                ref=ref,
                context_paths=context_paths or [],
            ),
        )

    async def areconcile_workspace_session(
        self,
        session: DaytonaSandboxSession,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
    ) -> DaytonaSandboxSession:
        return await _run_sync_in_thread(
            self.reconcile_workspace_session,
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
