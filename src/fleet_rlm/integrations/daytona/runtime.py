"""Direct-SDK workspace bootstrap and session helpers for Daytona sandboxes."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import threading
import time as _ws_time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from fleet_rlm.utils.async_compat import _run_async_compat, _run_sync_in_thread

from .concurrency import (
    acquire_sandbox_slot,
    attach_slot_release_handler,
    get_current_sandbox_usage,
    reconcile_sandbox_slots,
    release_sandbox_slot,
    release_sandbox_slot_for,
)
from .config import ResolvedDaytonaConfig, resolve_daytona_config
from .config import build_async_daytona_client as _build_async_daytona_client
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
from .session_runtime import DaytonaSandboxSession
from .snapshots import (
    DEFAULT_SNAPSHOT_NAME,
    DEFAULT_SNAPSHOT_PACKAGES,
    bootstrap_snapshot,
)
from .snapshots import (
    create_sandbox_snapshot as _acreate_sandbox_snapshot_helper,
)
from .snapshots import (
    create_snapshot as acreate_snapshot,
)
from .snapshots import (
    fallback_to_declarative_image as _fallback_to_declarative_image,
)
from .snapshots import (
    get_snapshot as aget_snapshot,
)
from .snapshots import (
    list_snapshots as alist_snapshots,
)
from .snapshots import (
    resolve_default_snapshot as _resolve_default_snapshot,
)
from .snapshots import (
    resolve_sandbox_spec_snapshot as aresolve_sandbox_spec_snapshot,
)
from .snapshots import (
    resolve_snapshot as aresolve_snapshot,
)
from .volumes import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
)
from .volumes import (
    await_volume_ready as _await_volume_ready,
)
from .workspace_manager import (
    WorkspaceSessionCreateRequest,
    WorkspaceSessionReconcileRequest,
)
from .workspace_manager import (
    acreate_workspace_session as _acreate_workspace_session_helper,
)
from .workspace_manager import (
    areconcile_workspace_session as _areconcile_workspace_session_helper,
)

if TYPE_CHECKING:
    from daytona import AsyncDaytona, Daytona

logger = logging.getLogger(__name__)

_GENERATED_SANDBOX_NAME_RE = re.compile(r"^fleet-rlm-\d{8}-\d{6}(?:-[0-9a-f]{8})?$")
_SANDBOX_NAME_CONFLICT_RETRIES = 3
_PROVIDER_ACTIVE_STATES = frozenset({"pending", "creating", "starting", "started", "running"})

_BROWSER_SKILL_INDICATORS = frozenset({"browser-interaction", "browser_interaction", "browser", "playwright"})


def resolve_snapshot_for_skills(selected_skills: list[str] | None = None) -> str:
    """Return the appropriate snapshot name based on selected skills.

    When any selected skill indicates browser interaction, returns the
    browser-capable snapshot. Otherwise returns the default base snapshot.
    """
    from .snapshots import BROWSER_SNAPSHOT_NAME

    if not selected_skills:
        return DEFAULT_SNAPSHOT_NAME
    for skill in selected_skills:
        if any(indicator in skill.lower() for indicator in _BROWSER_SKILL_INDICATORS):
            return BROWSER_SNAPSHOT_NAME
    return DEFAULT_SNAPSHOT_NAME


# ---------------------------------------------------------------------------
# DaytonaSandboxRuntime
# ---------------------------------------------------------------------------


def _is_generated_fleet_sandbox_name(name: str | None) -> bool:
    return bool(name and _GENERATED_SANDBOX_NAME_RE.fullmatch(name))


def _is_sandbox_name_conflict(exc: BaseException) -> bool:
    """Return True for Daytona's HTTP 409 sandbox-name collision shape."""
    exc_name = type(exc).__name__
    message = str(exc)
    return (
        exc_name == "DaytonaConflictError" or "HTTP 409" in message or "already exists" in message
    ) and "Sandbox with name" in message


def _spec_with_fresh_generated_name(spec: SandboxSpec) -> SandboxSpec:
    return replace(spec, name=_default_sandbox_name_helper())


class DaytonaSandboxRuntime:
    """Factory for Daytona sandboxes used by the pilot."""

    # Default labels applied to all sandboxes created by this runtime
    DEFAULT_LABELS: dict[str, str] = dict({"managed-by": "fleet-rlm"})

    def __init__(self, *, config: ResolvedDaytonaConfig | None = None) -> None:
        resolved = config or resolve_daytona_config()
        self._resolved_config = resolved
        self._client: Daytona | None = None
        self._async_client: AsyncDaytona | None = None
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

    def _get_async_client(self) -> Any:
        """Return the cached AsyncDaytona client for the native-async hot path.

        AsyncDaytona provides async ``create``/``get``/``delete``/``list``/
        ``close`` as well as ``snapshot``/``volume`` sub-clients
        (``AsyncSnapshotService``, ``AsyncVolumeService``).
        """
        with self._client_lock:
            if self._closed:
                raise RuntimeError("Daytona runtime client is closed")
            if self._async_client is None:
                self._async_client = _build_async_daytona_client(self._resolved_config)
            return self._async_client

    def close(self) -> None:
        """Close the runtime and release the underlying client."""
        with self._client_lock:
            self._closed = True
            client = self._client
            self._async_client, async_client = None, self._async_client
            self._client = None
        if client is not None:
            close = getattr(client, "close", None)
            if close is not None and callable(close):
                close()
        if async_client is not None:
            aclose = getattr(async_client, "aclose", None) or getattr(async_client, "close", None)
            if aclose is not None and callable(aclose):
                try:
                    result = aclose()
                    if inspect.isawaitable(result):

                        async def _await_aclose() -> None:
                            await result

                        _run_async_compat(_await_aclose)
                except Exception:
                    logger.debug("Failed to close async Daytona client", exc_info=True)

    async def aclose(self) -> None:
        """Close both clients natively; the async client is awaited directly."""
        with self._client_lock:
            self._closed = True
            client = self._client
            self._async_client, async_client = None, self._async_client
            self._client = None
        if client is not None:
            await _run_sync_in_thread(lambda: getattr(client, "close", lambda: None)())
        if async_client is not None:
            aclose = getattr(async_client, "aclose", None) or getattr(async_client, "close", None)
            if aclose is not None and callable(aclose):
                try:
                    result = aclose()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logger.debug("Failed to close async Daytona client", exc_info=True)

    @staticmethod
    def _default_sandbox_name() -> str:
        return _default_sandbox_name_helper()

    async def asweep_paused_sandboxes(self) -> int:
        """Startup sweep: delete provider-visible paused Fleet sandboxes.

        The in-process paused-sandbox registry is lost on restart, so paused
        root sessions from a previous run must be discovered via the provider
        list. Call this once at app startup (before serving traffic) when
        ``FLEET_SESSION_LIFECYCLE=pause`` is configured. Returns the number of
        sandboxes swept.
        """
        from .concurrency import sweep_paused_sandboxes_on_startup

        return await sweep_paused_sandboxes_on_startup(runtime=self)

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
        start with pre-installed core packages (dspy, numpy, pandas,
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
        ``fleet-rlm-20260404-090700-a1b2c3d4`` instead of opaque UUIDs.
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
        # Route snapshot build logs to debug instead of swallowing entirely;
        # a failed `uv pip install` is otherwise impossible to diagnose
        # without re-running. Daytona passes each log line here.
        logger.debug("daytona snapshot build: %s", _message)

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

        **Important:** This thread-offloads the sync ``Daytona`` client
        (sync ``Sandbox``) rather than using ``AsyncDaytona`` (which
        returns an ``AsyncSandbox``). The reason is that the entire post-
        creation pipeline (slot handler, volume layout, git helpers, local
        repo mount, code-interpreter context) expects sync ``Sandbox``
        method signatures. An ``AsyncSandbox`` in those paths produces
        unawaited-coroutine warnings and ``DaytonaError`` at runtime.

        Once the full isolation/executor/bridge stack is migrated to
        native async this can switch to ``AsyncDaytona`` directly.
        """
        return await _run_sync_in_thread(self._create_sandbox_from_spec_impl, spec)

    def _count_provider_fleet_sandboxes_sync(self) -> int:
        """Return provider-visible Fleet-managed sandboxes for slot reconciliation."""
        client = self._get_client()
        signature = inspect.signature(client.list)
        kwargs: dict[str, Any] = {}
        if "labels" in signature.parameters:
            kwargs["labels"] = self.DEFAULT_LABELS
        result = client.list(**kwargs)
        raw_items = getattr(result, "items", result) if result else []
        count = 0
        for sandbox in raw_items:
            labels = getattr(sandbox, "labels", None) or {}
            if not isinstance(labels, dict):
                labels = {}
            normalized = {str(key): str(value) for key, value in labels.items()}
            if not all(normalized.get(key) == value for key, value in self.DEFAULT_LABELS.items()):
                continue
            raw_state = getattr(sandbox, "state", None)
            state = str(getattr(raw_state, "value", raw_state) or "").lower()
            if state and state not in _PROVIDER_ACTIVE_STATES:
                continue
            count += 1
        return count

    async def _count_provider_fleet_sandboxes(self) -> int:
        """Count provider-visible Fleet sandboxes via the native async client.

        AsyncDaytona.list returns an AsyncIterator; iterate it natively
        instead of thread-offloading the sync client. Falls back to the sync
        implementation if the async client is unavailable (e.g. SDK missing
        the async surface in an older pin).
        """
        try:
            client = self._get_async_client()
        except Exception:
            return await _run_sync_in_thread(self._count_provider_fleet_sandboxes_sync)

        signature = inspect.signature(client.list)
        kwargs: dict[str, Any] = {}
        if "labels" in signature.parameters:
            kwargs["labels"] = self.DEFAULT_LABELS
        result = client.list(**kwargs)
        # AsyncDaytona.list returns an async iterator (not a list with .items).
        count = 0
        if hasattr(result, "__aiter__"):
            async for sandbox in result:
                labels = getattr(sandbox, "labels", None) or {}
                if not isinstance(labels, dict):
                    labels = {}
                normalized = {str(key): str(value) for key, value in labels.items()}
                if not all(normalized.get(key) == value for key, value in self.DEFAULT_LABELS.items()):
                    continue
                raw_state = getattr(sandbox, "state", None)
                state = str(getattr(raw_state, "value", raw_state) or "").lower()
                if state and state not in _PROVIDER_ACTIVE_STATES:
                    continue
                count += 1
            return count
        # Defensive: if a future SDK version returns a materialized list.
        raw_items = getattr(result, "items", result) if result else []
        for sandbox in raw_items:
            labels = getattr(sandbox, "labels", None) or {}
            if not isinstance(labels, dict):
                labels = {}
            normalized = {str(key): str(value) for key, value in labels.items()}
            if not all(normalized.get(key) == value for key, value in self.DEFAULT_LABELS.items()):
                continue
            raw_state = getattr(sandbox, "state", None)
            state = str(getattr(raw_state, "value", raw_state) or "").lower()
            if state and state not in _PROVIDER_ACTIVE_STATES:
                continue
            count += 1
        return count

    async def _acquire_slot_with_reconciliation(self) -> None:
        try:
            await acquire_sandbox_slot(timeout=60.0)
            return
        except asyncio.TimeoutError:
            before = get_current_sandbox_usage()

        try:
            provider_active = await self._count_provider_fleet_sandboxes()
        except Exception:
            logger.warning("Failed to reconcile sandbox slots from Daytona provider", exc_info=True)
            raise asyncio.TimeoutError from None

        if provider_active >= before.active_count:
            raise asyncio.TimeoutError

        reconcile_sandbox_slots(provider_active_count=provider_active)
        try:
            await acquire_sandbox_slot(timeout=1.0)
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError from None

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
            await self._acquire_slot_with_reconciliation()
            slot_acquired = True
        except asyncio.TimeoutError as exc:
            usage = get_current_sandbox_usage()
            raise DaytonaDiagnosticError(
                "Sandbox concurrency limit reached: "
                f"{usage.active_count}/{usage.limit} Fleet sandbox slots are occupied "
                f"({usage.available_slots} available). Wait briefly, retry, or clean up idle sessions.",
                category="sandbox_concurrency_busy",
                phase="sandbox_create",
            ) from exc

        sandbox = None
        try:
            resolved_spec = spec or self.build_sandbox_spec(volume_name=volume_name)
            resolved_spec = aresolve_sandbox_spec_snapshot(
                resolved_spec,
                config=self._resolved_config,
            )
            attempts = 0
            while True:
                try:
                    sandbox = await self.acreate_sandbox_from_spec(resolved_spec)
                    break
                except Exception as create_exc:
                    attempts += 1
                    if (
                        attempts > _SANDBOX_NAME_CONFLICT_RETRIES
                        or not _is_generated_fleet_sandbox_name(resolved_spec.name)
                        or not _is_sandbox_name_conflict(create_exc)
                    ):
                        raise
                    old_name = resolved_spec.name
                    resolved_spec = _spec_with_fresh_generated_name(resolved_spec)
                    logger.warning(
                        "Daytona generated sandbox name conflicted; retrying with a fresh name "
                        "(old_name=%s, new_name=%s, attempt=%d/%d)",
                        old_name,
                        resolved_spec.name,
                        attempts,
                        _SANDBOX_NAME_CONFLICT_RETRIES,
                    )
            # Attach semaphore release callback to sandbox for cleanup
            if sandbox is not None:
                attach_slot_release_handler(sandbox)
            else:
                release_sandbox_slot()
            return sandbox
        except Exception as exc:
            # Release slot on failure since sandbox won't be returned
            if slot_acquired:
                if sandbox is not None:
                    delete = getattr(sandbox, "delete", None)
                    if callable(delete):
                        try:
                            await _run_sync_in_thread(delete)
                        except Exception:
                            logger.warning("Failed to delete sandbox after create failure", exc_info=True)
                if sandbox is not None:
                    release_sandbox_slot_for(sandbox)
                else:
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
# Sandbox lifecycle helpers (merged from sdk_ops.py)
# ---------------------------------------------------------------------------


def _experimental_call(
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
        return method(*args, **kwargs)
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona {method_name} failure: {exc}",
            category=category,
            phase=phase,
        ) from exc


def get_sandbox(
    *,
    runtime: Any,
    sandbox_id: str,
    recover: bool = True,
) -> Any:
    """Get an existing sandbox by ID, recovering from archive if needed."""
    try:
        client = runtime._get_client()
        sandbox = client.get(sandbox_id)
        if recover:
            state = getattr(sandbox, "state", None)
            state_value = getattr(state, "value", str(state or ""))
            if str(state_value).lower() in ("archived", "stopped"):
                sandbox.recover(timeout=60)
        return sandbox
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona sandbox resume failure: {_format_daytona_sdk_error(exc)}",
            category="sandbox_resume_error",
            phase="sandbox_resume",
        ) from exc


def resume_workspace_session(
    *,
    runtime: Any,
    sandbox_id: str,
    repo_url: str | None,
    ref: str | None,
    volume_name: str | None = None,
    workspace_path: str,
    context_sources: list[Any] | None = None,
    context_id: str | None = None,
) -> Any:
    resumed_started = _ws_time.perf_counter()
    sandbox = get_sandbox(
        runtime=runtime,
        sandbox_id=sandbox_id,
    )
    session = runtime._build_workspace_session(
        sandbox=sandbox,
        repo_url=repo_url,
        resolved_ref=ref,
        volume_name=volume_name,
        workspace_path=workspace_path,
        context_sources=list(context_sources or []),
        timings={"sandbox_resume": int((_ws_time.perf_counter() - resumed_started) * 1000)},
        context_id=context_id,
    )
    if volume_name:
        from .volumes import ensure_daytona_volume_layout

        ensure_daytona_volume_layout(
            sandbox=sandbox,
            mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
        )
    return session


def fork_sandbox(
    *,
    runtime: Any,
    session: Any,
    name: str | None = None,
    timeout: float = 60.0,
) -> Any:
    """Fork a sandbox session, creating a copy-on-write clone."""
    from .concurrency import (
        acquire_sandbox_slot,
        attach_slot_release_handler,
        release_sandbox_slot,
    )

    slot_acquired = False
    try:
        _run_async_compat(acquire_sandbox_slot, timeout=60.0)
        slot_acquired = True
        forked = _experimental_call(
            session.sandbox,
            "_experimental_fork",
            name=name,
            timeout=timeout,
            category="sandbox_fork_error",
            phase="sandbox_fork",
        )
        attach_slot_release_handler(forked)
        return runtime._build_workspace_session(
            sandbox=forked,
            repo_url=session.repo_url,
            resolved_ref=session.ref,
            volume_name=session.volume_name,
            workspace_path=session.workspace_path,
            context_sources=list(session.context_sources),
            timings={"sandbox_fork": 0},
        )
    except Exception:
        if slot_acquired:
            release_sandbox_slot()
        raise


def get_sandbox_id_from_interpreter(interpreter: Any) -> str:
    """Extract the Daytona sandbox ID from a DaytonaInterpreter or session instance."""
    return (
        getattr(interpreter, "_persisted_sandbox_id", None)
        or getattr(getattr(interpreter, "session", None), "sandbox_id", None)
        or getattr(interpreter, "sandbox_id", "")
        or ""
    )


# Used internally by DaytonaSandboxRuntime for the aliased helper references
_aget_sandbox_helper = get_sandbox
_aresume_workspace_session = resume_workspace_session
_afork_sandbox = fork_sandbox


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
    "bootstrap_snapshot",
    "get_sandbox_id_from_interpreter",
    "resolve_snapshot_for_skills",
]
