"""Workspace state, session lifecycle, and runtime metadata for Daytona."""

from __future__ import annotations

import asyncio
import inspect
import time as _ws_time
from collections.abc import Callable
from contextlib import suppress as _suppress
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fleet_rlm.utils.async_compat import _await_if_needed, _run_async_compat
from fleet_rlm.utils.paths import dedupe_paths

from ._repo import (
    _abuild_workspace_path,
    _aclone_repo,
    _aensure_workspace_root,
    _areconcile_repo_checkout,
    _aresolve_clone_ref,
    amount_local_repo_tree,
)
from .concurrency import (
    reap_paused_sandboxes,
    register_paused_sandbox,
    should_pause_root_session,
)
from .models import ReconfigureOutcome, SandboxSpec, WorkspaceConfig, normalized_context_sources
from .session_runtime import DaytonaSandboxSession
from .volumes import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
)
from .volumes import (
    aensure_daytona_volume_layout as _aensure_daytona_volume_layout,
)

if TYPE_CHECKING:
    from .runtime import DaytonaSandboxRuntime

_UNSET = object()


class WorkspaceManager:
    """Own Daytona workspace/session state for a ``DaytonaInterpreter`` facade."""

    def __init__(
        self,
        *,
        runtime: DaytonaSandboxRuntime,
        owns_runtime: bool,
        initial_config: WorkspaceConfig,
        volume_subpath: str | None,
        sandbox_spec: Any | None,
        sandbox_labels: dict[str, str],
        timeout: int,
        execute_timeout: int,
        delete_session_on_shutdown: bool,
        delete_context_on_shutdown: bool,
        execution_event_callback_ref: Callable[[], Callable[[dict[str, Any]], None] | None],
        child_isolation_metadata_ref: Callable[[], dict[str, Any] | None],
        reset_executor: Callable[[], Any],
        close_executor: Callable[[], Any],
    ) -> None:
        self.runtime = runtime
        self._owns_runtime = owns_runtime
        self._runtime_config = getattr(runtime, "_resolved_config", None)
        self._runtime_closed = False
        self._workspace_config = initial_config
        self.timeout = timeout
        self.execute_timeout = execute_timeout
        self.volume_subpath = str(volume_subpath or "").strip() or None
        self.volume_mount_path = str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
        self.repo_url = initial_config.repo_url
        self.repo_ref = initial_config.repo_ref
        self.context_paths = list(initial_config.context_paths)
        self.volume_name = initial_config.volume_name
        self.sandbox_spec = sandbox_spec
        self.sandbox_labels = dict(sandbox_labels)
        self.delete_session_on_shutdown = delete_session_on_shutdown
        self.delete_context_on_shutdown = delete_context_on_shutdown
        self._execution_event_callback_ref = execution_event_callback_ref
        self._child_isolation_metadata_ref = child_isolation_metadata_ref
        self._reset_executor = reset_executor
        self._close_executor = close_executor

        self._started = False
        self._session: DaytonaSandboxSession | None = None
        self._session_source_key: tuple[str | None, str | None, tuple[str, ...], str | None] | None = None
        self._persisted_sandbox_id: str | None = None
        self._persisted_workspace_path: str | None = None
        self._persisted_context_sources: list[Any] = []
        self._persisted_context_id: str | None = None
        self._persisted_volume_name: str | None = None
        self._last_sandbox_transition: ReconfigureOutcome | None = None
        self._last_workspace_reconfigured = False
        self._runtime_degraded = False
        self._runtime_failure_category: str | None = None
        self._runtime_failure_phase: str | None = None
        self._runtime_fallback_used = False
        self._session_snapshot: str | None | object = _UNSET

    @property
    def execution_event_callback(self) -> Callable[[dict[str, Any]], None] | None:
        return self._execution_event_callback_ref()

    @property
    def child_isolation_metadata(self) -> dict[str, Any] | None:
        return self._child_isolation_metadata_ref()

    @property
    def session(self) -> DaytonaSandboxSession | None:
        return self._session

    @property
    def started(self) -> bool:
        return self._started

    @property
    def last_sandbox_transition(self) -> ReconfigureOutcome | None:
        return self._last_sandbox_transition

    @property
    def last_workspace_reconfigured(self) -> bool:
        return self._last_workspace_reconfigured

    @staticmethod
    def _callable_accepts_kwarg(func: Callable[..., Any] | None, name: str) -> bool:
        if not callable(func):
            return False
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return False
        if name in signature.parameters:
            return True
        return any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())

    @staticmethod
    async def _call_maybe_async(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        return await _await_if_needed(func(*args, **kwargs))

    async def _aresume_workspace_session(
        self: Any,
        *,
        sandbox_id: str,
        repo_url: str | None,
        ref: str | None,
        workspace_path: str,
        context_sources: list[Any],
        context_id: str | None,
    ) -> DaytonaSandboxSession:
        resume_workspace_session = (
            getattr(self.runtime, "aresume_workspace_session", None) or self.runtime.resume_workspace_session
        )
        resume_kwargs: dict[str, Any] = {
            "sandbox_id": sandbox_id,
            "repo_url": repo_url,
            "ref": ref,
            "workspace_path": workspace_path,
            "context_sources": context_sources,
            "context_id": context_id,
        }
        if self._callable_accepts_kwarg(resume_workspace_session, "volume_name"):
            resume_kwargs["volume_name"] = self._persisted_volume_name or self.volume_name
        return await self._call_maybe_async(resume_workspace_session, **resume_kwargs)

    async def _areconcile_workspace_session(
        self,
        session: DaytonaSandboxSession,
    ) -> DaytonaSandboxSession:
        reconcile_workspace_session = getattr(self.runtime, "areconcile_workspace_session", None) or getattr(
            self.runtime, "reconcile_workspace_session", None
        )
        if not callable(reconcile_workspace_session):
            raise RuntimeError("Runtime does not support workspace reconciliation")
        return await self._call_maybe_async(
            reconcile_workspace_session,
            session,
            repo_url=self.repo_url,
            ref=self.repo_ref,
            context_paths=list(self.context_paths),
        )

    def start(self) -> None:
        _run_async_compat(self.astart)

    async def astart(self) -> None:
        if self._started:
            return
        session = await self._aensure_session_impl()
        await session.astart_driver(timeout=float(self.execute_timeout or self.timeout))
        if self.child_isolation_metadata and session.sandbox_id:
            self.child_isolation_metadata.setdefault("child_sandbox_id", session.sandbox_id)
        self._started = True

    def shutdown(self) -> None:
        _run_async_compat(self.ashutdown)

    async def ashutdown(self) -> None:
        try:
            await self._adetach_session(delete=self.delete_session_on_shutdown)
        finally:
            self._started = False
            await self._aclose_runtime()

    async def arelease_idle_session(self) -> None:
        """Delete the active sandbox session without closing the runtime client."""
        await self._adetach_session(delete=True)

    def ensure_session(self) -> DaytonaSandboxSession:
        return self._ensure_session_sync()

    async def aensure_session(self) -> DaytonaSandboxSession:
        return await self._aensure_session()

    def _ensure_session_sync(self) -> DaytonaSandboxSession:
        return _run_async_compat(self._aensure_session_impl)

    def _session_matches_current_async_owner(self, session: DaytonaSandboxSession) -> bool:
        matches_current_owner = getattr(session, "matches_current_async_owner", None)
        if callable(matches_current_owner):
            return bool(matches_current_owner())
        return False

    def _current_session_source_key(self) -> tuple[str | None, str | None, tuple[str, ...], str | None]:
        return (
            self.repo_url,
            self.repo_ref,
            tuple(self.context_paths),
            self.volume_name,
        )

    def _attach_execution_callback(self, session: DaytonaSandboxSession | None) -> DaytonaSandboxSession | None:
        if session is not None:
            session.execution_event_callback = self.execution_event_callback
        return session

    async def _afinalize_session(
        self: Any,
        session: DaytonaSandboxSession,
        *,
        source_key: tuple[str | None, str | None, tuple[str, ...], str | None],
        transition: ReconfigureOutcome,
        workspace_reconfigured: bool,
    ) -> DaytonaSandboxSession:
        self._session = self._attach_execution_callback(session)
        self._session_source_key = source_key
        await self._areset_execution_state()
        self._persist_session_state()
        self._last_sandbox_transition = transition
        self._last_workspace_reconfigured = workspace_reconfigured
        return session

    async def _arelease_loop_mismatched_session(self: Any) -> None:
        await self._adetach_session(delete=False)
        self._persisted_context_id = None

    async def _aresolve_active_session(
        self: Any,
        *,
        source_key: tuple[str | None, str | None, tuple[str, ...], str | None],
    ) -> tuple[DaytonaSandboxSession | None, bool]:
        active_session = self._session
        if active_session is None:
            return None, False

        if not self._session_matches_current_async_owner(active_session):
            await self._arelease_loop_mismatched_session()
            return None, False

        if self._session_needs_recreation(desired_volume=self.volume_name):
            await self._adetach_session(delete=True)
            return None, True

        if self._session_source_key == source_key:
            session = await self._afinalize_session(
                active_session,
                source_key=source_key,
                transition=ReconfigureOutcome.REUSED,
                workspace_reconfigured=False,
            )
            return session, False

        try:
            reconciled = await self._areconcile_workspace_session(active_session)
        except Exception as exc:
            self._mark_runtime_degradation_from_exception(exc)
            await self._adetach_session(delete=True)
            return None, True

        session = await self._afinalize_session(
            reconciled,
            source_key=source_key,
            transition=ReconfigureOutcome.REUSED,
            workspace_reconfigured=True,
        )
        return session, False

    def _clear_persisted_session_for_volume_change(self) -> bool:
        if self._persisted_sandbox_id is None:
            return False
        if self._persisted_volume_name == self.volume_name:
            return False
        self._clear_persisted_session()
        return True

    @staticmethod
    def _should_reconcile_resumed_session(
        persisted_source_key: tuple[str | None, str | None, tuple[str, ...], str | None] | None,
        source_key: tuple[str | None, str | None, tuple[str, ...], str | None],
    ) -> bool:
        return persisted_source_key is not None and persisted_source_key != source_key

    async def _aresolve_persisted_session(
        self: Any,
        *,
        source_key: tuple[str | None, str | None, tuple[str, ...], str | None],
    ) -> tuple[DaytonaSandboxSession | None, bool]:
        if not (self._persisted_sandbox_id and self._persisted_workspace_path):
            return None, False

        try:
            persisted_source_key = self._session_source_key
            resumed = await self._aresume_workspace_session(
                sandbox_id=self._persisted_sandbox_id,
                repo_url=self.repo_url,
                ref=self.repo_ref,
                workspace_path=self._persisted_workspace_path,
                context_sources=self._persisted_context_sources,
                context_id=self._persisted_context_id,
            )
            workspace_reconfigured = False
            if self._should_reconcile_resumed_session(persisted_source_key, source_key):
                resumed = await self._areconcile_workspace_session(resumed)
                workspace_reconfigured = True

            session = await self._afinalize_session(
                resumed,
                source_key=source_key,
                transition=ReconfigureOutcome.RESUMED,
                workspace_reconfigured=workspace_reconfigured,
            )
            return session, False
        except Exception as exc:
            self._mark_runtime_degradation_from_exception(exc)
            self._clear_persisted_session()
            return None, True

    def _resolve_session_snapshot(self) -> str | None:
        if self._session_snapshot is _UNSET:
            if isinstance(self.sandbox_spec, SandboxSpec):
                return self.sandbox_spec.snapshot
            return getattr(self.sandbox_spec, "snapshot", None)
        if self._session_snapshot is None:
            return None
        return str(self._session_snapshot)

    def _effective_sandbox_spec(self) -> SandboxSpec:
        """Return the sandbox spec with current volume and owner labels applied."""
        labels = dict(getattr(self.sandbox_spec, "labels", None) or {})
        labels.update(self.sandbox_labels)
        snapshot = self._resolve_session_snapshot()
        if isinstance(self.sandbox_spec, SandboxSpec):
            return replace(
                self.sandbox_spec,
                volume_name=self.volume_name or self.sandbox_spec.volume_name,
                volume_subpath=(self.volume_subpath or self.sandbox_spec.volume_subpath),
                labels=labels or None,
                snapshot=snapshot,
            )
        build_sandbox_spec = getattr(self.runtime, "build_sandbox_spec", None)
        if callable(build_sandbox_spec):
            return build_sandbox_spec(
                volume_name=self.volume_name,
                volume_subpath=self.volume_subpath,
                labels=labels or None,
                snapshot=snapshot,
            )
        return SandboxSpec(
            volume_name=self.volume_name,
            volume_mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
            volume_subpath=self.volume_subpath,
            labels=labels or None,
            snapshot=snapshot,
        )

    async def _acreate_session_from_runtime(
        self: Any,
        *,
        source_key: tuple[str | None, str | None, tuple[str, ...], str | None],
        should_report_recreated: bool,
    ) -> DaytonaSandboxSession:
        create_workspace_session = (
            getattr(self.runtime, "acreate_workspace_session", None) or self.runtime.create_workspace_session
        )
        session = await self._call_maybe_async(
            create_workspace_session,
            repo_url=self.repo_url,
            ref=self.repo_ref,
            context_paths=list(self.context_paths),
            volume_name=self.volume_name,
            spec=self._effective_sandbox_spec(),
        )
        return await self._afinalize_session(
            session,
            source_key=source_key,
            transition=ReconfigureOutcome.RECREATED if should_report_recreated else ReconfigureOutcome.CREATED,
            workspace_reconfigured=False,
        )

    async def _aensure_session_impl(self) -> DaytonaSandboxSession:
        self._ensure_runtime_available()
        source_key = self._current_session_source_key()
        should_report_recreated = False

        active_session, active_recreated = await self._aresolve_active_session(source_key=source_key)
        if active_session is not None:
            return active_session
        should_report_recreated = should_report_recreated or active_recreated

        if self._clear_persisted_session_for_volume_change():
            should_report_recreated = True

        persisted_session, persisted_recreated = await self._aresolve_persisted_session(source_key=source_key)
        if persisted_session is not None:
            return persisted_session
        should_report_recreated = should_report_recreated or persisted_recreated

        return await self._acreate_session_from_runtime(
            source_key=source_key,
            should_report_recreated=should_report_recreated,
        )

    async def _aensure_session(self) -> DaytonaSandboxSession:
        session = await self._aensure_session_impl()
        await session.arefresh_activity()
        return session

    async def aget_session(self) -> DaytonaSandboxSession:
        """Public async accessor to ensure and return the active sandbox session."""
        return await self._aensure_session()

    def _detach_session(self, *, delete: bool) -> None:
        _run_async_compat(self._adetach_session, delete=delete)

    async def _adetach_session(self: Any, *, delete: bool) -> None:
        active_session = self._session
        if active_session is None:
            if delete:
                self._clear_persisted_session()
            await self._areset_execution_state()
            self._started = False
            return

        self._persist_session_state(active_session)
        await self._aclose_bridge()
        try:
            if delete:
                # Phase 2.5: root sessions may pause instead of delete so they
                # can be resumed quickly without a full sandbox rebuild. Only
                # root sessions (owns_runtime) are eligible; children always
                # delete-after-task to keep the recursive footprint bounded.
                if self._owns_runtime and should_pause_root_session():
                    await self._pause_root_session(active_session)
                else:
                    delete_fn = getattr(active_session, "adelete", None) or active_session.delete
                    await self._call_maybe_async(delete_fn)
            elif self.delete_context_on_shutdown:
                delete_context_fn = getattr(active_session, "adelete_context", None) or active_session.delete_context
                await self._call_maybe_async(delete_context_fn)
            else:
                close_driver_fn = getattr(active_session, "aclose_driver", None) or active_session.close_driver
                await self._call_maybe_async(close_driver_fn)
        finally:
            if delete:
                self._clear_persisted_session()
            self._session = None
            if delete:
                self._session_source_key = None
            await self._areset_execution_state()
            self._started = False

    def _close_bridge(self: Any) -> None:
        _run_async_compat(self._aclose_bridge)

    async def _aclose_bridge(self) -> None:
        await self._close_executor()

    async def _pause_root_session(self: Any, session: Any) -> None:
        """Pause a root session for fast resume, then run the inline LRU reaper.

        Pausing frees vCPU/RAM quota (only disk remains) while keeping the
        sandbox warm for a quick ``start()`` on resume. The Fleet concurrency
        slot is released via the slot handler's pause wrapper. The reaper
        deletes the oldest paused sandboxes when the registry exceeds
        ``FLEET_MAX_PAUSED_SANDBOXES``.
        """
        sandbox = getattr(session, "sandbox", None) or session
        sandbox_id = getattr(sandbox, "id", None) or getattr(session, "sandbox_id", "")
        pause_fn = getattr(sandbox, "pause", None) or getattr(session, "apause", None)
        if pause_fn is not None:
            await self._call_maybe_async(pause_fn)
        if sandbox_id:
            register_paused_sandbox(str(sandbox_id))
        try:
            await reap_paused_sandboxes(runtime=self.runtime)
        except Exception:
            import logging

            logging.getLogger(__name__).debug("Inline reaper failed during pause of %s", sandbox_id, exc_info=True)

    async def _aclose_runtime(self: Any) -> None:
        if not self._owns_runtime or self._runtime_closed:
            return
        close_fn = getattr(self.runtime, "aclose", None) or self.runtime.close
        await self._call_maybe_async(close_fn)
        self._runtime_closed = True

    def _ensure_runtime_available(self: Any) -> None:
        runtime = self.runtime
        if not self._owns_runtime:
            return
        # Lazy import to avoid circular dependency
        from .runtime import DaytonaSandboxRuntime as _DaytonaSandboxRuntime

        if not isinstance(runtime, _DaytonaSandboxRuntime):
            return
        if not self._runtime_closed:
            return
        if self._runtime_config is None:
            raise RuntimeError("Owned Daytona runtime cannot be recreated without config")
        self.runtime = _DaytonaSandboxRuntime(config=self._runtime_config)
        self._runtime_closed = False

    def _reset_execution_state(self) -> None:
        _run_async_compat(self._areset_execution_state)

    async def _areset_execution_state(self) -> None:
        await self._reset_executor()

    def configure_workspace(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        sandbox_labels: dict[str, str] | None = None,
        force_new_session: bool = False,
        snapshot: str | None | object = _UNSET,
    ) -> ReconfigureOutcome:
        (
            normalized_repo_url,
            normalized_repo_ref,
            normalized_context_paths,
            normalized_volume,
            normalized_sandbox_labels,
            source_key,
        ) = self._normalized_workspace_config(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=context_paths,
            volume_name=volume_name,
            sandbox_labels=sandbox_labels,
        )
        snapshot_changed = False
        if snapshot is not _UNSET:
            normalized_snapshot = str(snapshot or "").strip() or None
            current_snapshot = None if self._session_snapshot is _UNSET else self._session_snapshot
            snapshot_changed = normalized_snapshot != current_snapshot
            if snapshot_changed:
                self._session_snapshot = normalized_snapshot
        should_recreate = (
            force_new_session or snapshot_changed or self._session_needs_recreation(desired_volume=normalized_volume)
        )
        if should_recreate:
            self._detach_session(delete=True)
        self._apply_workspace_config(
            repo_url=normalized_repo_url,
            repo_ref=normalized_repo_ref,
            context_paths=normalized_context_paths,
            volume_name=normalized_volume,
            sandbox_labels=normalized_sandbox_labels,
        )
        if not should_recreate and self._session is not None:
            self._last_sandbox_transition = ReconfigureOutcome.REUSED
            self._last_workspace_reconfigured = self._session_source_key != source_key
        return self._last_sandbox_transition or ReconfigureOutcome.UPDATED

    async def aconfigure_workspace(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        sandbox_labels: dict[str, str] | None = None,
        force_new_session: bool = False,
        snapshot: str | None | object = _UNSET,
    ) -> ReconfigureOutcome:
        (
            normalized_repo_url,
            normalized_repo_ref,
            normalized_context_paths,
            normalized_volume,
            normalized_sandbox_labels,
            source_key,
        ) = self._normalized_workspace_config(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=context_paths,
            volume_name=volume_name,
            sandbox_labels=sandbox_labels,
        )
        snapshot_changed = False
        if snapshot is not _UNSET:
            normalized_snapshot = str(snapshot or "").strip() or None
            current_snapshot = None if self._session_snapshot is _UNSET else self._session_snapshot
            snapshot_changed = normalized_snapshot != current_snapshot
            if snapshot_changed:
                self._session_snapshot = normalized_snapshot
        should_recreate = (
            force_new_session or snapshot_changed or self._session_needs_recreation(desired_volume=normalized_volume)
        )
        if should_recreate:
            await self._adetach_session(delete=True)
        self._apply_workspace_config(
            repo_url=normalized_repo_url,
            repo_ref=normalized_repo_ref,
            context_paths=normalized_context_paths,
            volume_name=normalized_volume,
            sandbox_labels=normalized_sandbox_labels,
        )
        if not should_recreate and self._session is not None:
            self._last_sandbox_transition = ReconfigureOutcome.REUSED
            self._last_workspace_reconfigured = self._session_source_key != source_key
        return self._last_sandbox_transition or ReconfigureOutcome.UPDATED

    def _normalized_workspace_config(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        sandbox_labels: dict[str, str] | None,
    ) -> tuple[
        str | None,
        str | None,
        list[str],
        str | None,
        dict[str, str],
        tuple[str | None, str | None, tuple[str, ...], str | None],
    ]:
        normalized_repo_url = str(repo_url or "").strip() or None
        normalized_repo_ref = str(repo_ref or "").strip() or None
        normalized_context_paths = dedupe_paths(list(context_paths or []))
        normalized_volume = str(volume_name or "").strip() or None
        normalized_sandbox_labels = {
            str(key): str(value)
            for key, value in (sandbox_labels or {}).items()
            if str(key).strip() and str(value).strip()
        }
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
            normalized_sandbox_labels,
            source_key,
        )

    def _apply_workspace_config(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str],
        volume_name: str | None,
        sandbox_labels: dict[str, str],
    ) -> None:
        self.repo_url = repo_url
        self.repo_ref = repo_ref
        self.context_paths = context_paths
        self.volume_name = volume_name
        if sandbox_labels:
            self.sandbox_labels = dict(sandbox_labels)
        self._workspace_config = WorkspaceConfig.from_kwargs(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=context_paths,
            volume_name=volume_name,
            sandbox_labels=sandbox_labels,
        )

    def _session_needs_recreation(self, *, desired_volume: str | None) -> bool:
        active_session = self._session
        if active_session is not None:
            return getattr(active_session, "volume_name", None) != desired_volume
        if self._persisted_sandbox_id is None:
            return False
        return self._persisted_volume_name != desired_volume

    def _apply_imported_session_state(self, state: dict[str, Any]) -> None:
        raw_daytona = state.get("daytona", {})
        daytona_state = raw_daytona if isinstance(raw_daytona, dict) else {}
        self.repo_url = str(daytona_state.get("repo_url", "") or "").strip() or None
        self.repo_ref = str(daytona_state.get("repo_ref", "") or "").strip() or None
        self.context_paths = dedupe_paths([str(item) for item in daytona_state.get("context_paths", []) or []])
        self._persisted_sandbox_id = str(daytona_state.get("sandbox_id", "") or "").strip() or None
        self._persisted_workspace_path = str(daytona_state.get("workspace_path", "") or "").strip() or None
        self._persisted_context_sources = normalized_context_sources(daytona_state.get("context_sources", []))
        self._persisted_context_id = str(daytona_state.get("context_id", "") or "").strip() or None
        self._persisted_volume_name = str(daytona_state.get("volume_name", "") or "").strip() or None
        self.volume_name = self._persisted_volume_name or self.volume_name
        self.volume_subpath = str(daytona_state.get("volume_subpath", "") or "").strip() or self.volume_subpath
        self._session_source_key = (
            self.repo_url,
            self.repo_ref,
            tuple(self.context_paths),
            self.volume_name,
        )
        self._workspace_config = WorkspaceConfig.from_kwargs(
            repo_url=self.repo_url,
            repo_ref=self.repo_ref,
            context_paths=self.context_paths,
            volume_name=self.volume_name,
            sandbox_labels=getattr(self, "sandbox_labels", None),
        )

    def export_session_state(self) -> dict[str, Any]:
        self._persist_session_state()
        context_sources = (
            list(self._session.context_sources) if self._session is not None else list(self._persisted_context_sources)
        )
        return {
            "daytona": {
                "repo_url": self.repo_url,
                "repo_ref": self.repo_ref,
                "context_paths": list(self.context_paths),
                "sandbox_id": (self._session.sandbox_id if self._session is not None else self._persisted_sandbox_id),
                "workspace_path": (
                    self._session.workspace_path if self._session is not None else self._persisted_workspace_path
                ),
                "context_sources": [item.to_dict() if hasattr(item, "to_dict") else item for item in context_sources],
                "context_id": (self._session.context_id if self._session is not None else self._persisted_context_id),
                "volume_name": (
                    getattr(self._session, "volume_name", None) or self.volume_name
                    if self._session is not None
                    else self._persisted_volume_name or self.volume_name
                ),
                "volume_subpath": self.volume_subpath,
            }
        }

    def import_session_state(self, state: dict[str, Any]) -> None:
        self._detach_session(delete=False)
        self._apply_imported_session_state(state)

    async def aimport_session_state(self, state: dict[str, Any]) -> None:
        await self._adetach_session(delete=False)
        self._apply_imported_session_state(state)

    def _persist_session_state(self, session: DaytonaSandboxSession | None = None) -> None:
        active_session = session or self._session
        if active_session is None:
            return
        self._persisted_sandbox_id = active_session.sandbox_id
        self._persisted_workspace_path = active_session.workspace_path
        self._persisted_context_sources = list(active_session.context_sources)
        self._persisted_context_id = active_session.context_id
        self._persisted_volume_name = getattr(active_session, "volume_name", None) or self.volume_name

    def _clear_persisted_session(self) -> None:
        self._persisted_sandbox_id = None
        self._persisted_workspace_path = None
        self._persisted_context_sources = []
        self._persisted_context_id = None
        self._persisted_volume_name = None

    def reset_runtime_degradation_state(self) -> None:
        self._runtime_degraded = False
        self._runtime_failure_category = None
        self._runtime_failure_phase = None
        self._runtime_fallback_used = False

    reset_degradation = reset_runtime_degradation_state

    def mark_runtime_degradation(
        self,
        *,
        category: str | None = None,
        phase: str | None = None,
        fallback_used: bool = False,
    ) -> None:
        self._runtime_degraded = True
        category_value = str(category or "").strip() or None
        phase_value = str(phase or "").strip() or None
        if self._runtime_failure_category is None and category_value is not None:
            self._runtime_failure_category = category_value
        if self._runtime_failure_phase is None and phase_value is not None:
            self._runtime_failure_phase = phase_value
        if fallback_used:
            self._runtime_fallback_used = True

    mark_degradation = mark_runtime_degradation

    def _mark_runtime_degradation_from_exception(self, exc: BaseException) -> None:
        self.mark_runtime_degradation(
            category=str(getattr(exc, "category", "") or "").strip() or None,
            phase=str(getattr(exc, "phase", "") or "").strip() or None,
            fallback_used=True,
        )

    def current_runtime_metadata(self) -> dict[str, Any]:
        session = self._session
        metadata: dict[str, Any] = {
            "sandbox_active": session is not None,
            "workspace_reconfigured": self._last_workspace_reconfigured,
            "runtime_degraded": bool(self._runtime_degraded),
            "runtime_fallback_used": bool(self._runtime_fallback_used),
        }
        sandbox_id = session.sandbox_id if session is not None else self._persisted_sandbox_id
        workspace_path = session.workspace_path if session is not None else self._persisted_workspace_path
        volume_name = (
            getattr(session, "volume_name", None) or self.volume_name
            if session is not None
            else self._persisted_volume_name or self.volume_name
        )
        if sandbox_id:
            metadata["sandbox_id"] = sandbox_id
        if workspace_path:
            metadata["workspace_path"] = workspace_path
        if volume_name:
            metadata["volume_name"] = volume_name
        if self.volume_subpath:
            metadata["volume_subpath"] = self.volume_subpath
        if self.child_isolation_metadata:
            metadata["child_isolation"] = dict(self.child_isolation_metadata)
        if self._last_sandbox_transition:
            metadata["sandbox_transition"] = self._last_sandbox_transition
        if self._runtime_failure_category:
            metadata["runtime_failure_category"] = self._runtime_failure_category
        if self._runtime_failure_phase:
            metadata["runtime_failure_phase"] = self._runtime_failure_phase
        return metadata

    runtime_metadata = current_runtime_metadata


__all__ = [
    "WorkspaceManager",
    "WorkspaceSessionCreateRequest",
    "WorkspaceSessionReconcileRequest",
    "acreate_workspace_session",
    "areconcile_workspace_session",
    "create_workspace_session",
    "reconcile_workspace_session",
]


# =========================================================================
# Workspace session orchestration (merged from workspace_runtime.py)
# =========================================================================


# ---------------------------------------------------------------------------
# Shared validator helper
# ---------------------------------------------------------------------------


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# Workspace session orchestration
# ---------------------------------------------------------------------------


class _WorkspaceRequestBase(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    repo_url: str | None = None
    ref: str | None = None
    context_paths: list[str] = Field(default_factory=list)

    @field_validator("repo_url", "ref", mode="before")
    @classmethod
    def _normalize_optional_text_field(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("context_paths", mode="before")
    @classmethod
    def _normalize_context_paths(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, (str, bytes)):
            items = [value]
        else:
            try:
                items = list(value)
            except TypeError:
                items = [value]
        normalized: list[str] = []
        for item in items:
            text = _normalize_optional_text(item)
            if text is not None:
                normalized.append(text)
        return normalized


class WorkspaceSessionCreateRequest(_WorkspaceRequestBase):
    """Validated request payload for Daytona workspace creation."""

    volume_name: str | None = None
    spec: SandboxSpec | None = None

    @field_validator("volume_name", mode="before")
    @classmethod
    def _normalize_volume_name(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)


class WorkspaceSessionReconcileRequest(_WorkspaceRequestBase):
    """Validated request payload for Daytona workspace reconciliation."""


async def acreate_workspace_session(
    *,
    runtime: Any,
    request: WorkspaceSessionCreateRequest,
) -> DaytonaSandboxSession:
    """Create a fully prepared workspace session inside a Daytona sandbox."""
    from .isolation import _astage_context_paths

    timings = {"sandbox_create": 0, "repo_clone": 0, "context_stage": 0}
    sandbox: Any | None = None
    resolved_spec = request.spec or runtime.build_sandbox_spec(volume_name=request.volume_name)
    try:
        create_started = _ws_time.perf_counter()
        sandbox = await runtime.acreate_sandbox(spec=resolved_spec)
        timings["sandbox_create"] = int((_ws_time.perf_counter() - create_started) * 1000)

        effective_volume = resolved_spec.volume_name or request.volume_name
        if effective_volume:
            await _aensure_daytona_volume_layout(
                sandbox=sandbox,
                mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
            )

        workspace_path = _abuild_workspace_path(sandbox, request.repo_url)
        resolved_ref = _aresolve_clone_ref(request.repo_url, request.ref) if request.repo_url else request.ref
        if request.repo_url:
            clone_started = _ws_time.perf_counter()
            _aclone_repo(
                sandbox=sandbox,
                repo_url=request.repo_url,
                ref=resolved_ref,
                workspace_path=workspace_path,
            )
            timings["repo_clone"] = int((_ws_time.perf_counter() - clone_started) * 1000)
        else:
            _aensure_workspace_root(
                sandbox=sandbox,
                workspace_path=workspace_path,
            )
            # Local-dev mode (no repo_url): mount the host repo source tree so
            # the agent's filesystem ops (os.listdir("src/fleet_rlm/"), etc.)
            # succeed instead of dead-ending on the lossy snapshot. No-op in
            # cloud (cwd isn't a project root) and never breaks session creation.
            mount_started = _ws_time.perf_counter()
            await asyncio.to_thread(amount_local_repo_tree, sandbox=sandbox, workspace_path=workspace_path)
            timings["local_repo_mount"] = int((_ws_time.perf_counter() - mount_started) * 1000)

        context_started = _ws_time.perf_counter()
        context_sources = _astage_context_paths(
            sandbox=sandbox,
            workspace_path=workspace_path,
            context_paths=request.context_paths or None,
        )
        timings["context_stage"] = int((_ws_time.perf_counter() - context_started) * 1000)

        return runtime._build_workspace_session(
            sandbox=sandbox,
            repo_url=request.repo_url,
            resolved_ref=resolved_ref,
            volume_name=effective_volume,
            workspace_path=workspace_path,
            context_sources=context_sources,
            timings=timings,
        )
    except Exception:
        if sandbox is not None:
            with _suppress(Exception):
                sandbox.delete()
        raise


# Backward-compat alias (sync wrapper for async function)
def create_workspace_session(
    *,
    runtime: Any,
    request: WorkspaceSessionCreateRequest,
) -> DaytonaSandboxSession:
    return _run_async_compat(acreate_workspace_session, runtime=runtime, request=request)


def areconcile_workspace_session(
    *,
    session: DaytonaSandboxSession,
    request: WorkspaceSessionReconcileRequest,
) -> DaytonaSandboxSession:
    """Reconcile an existing workspace session to new repo/context inputs."""
    from .isolation import _astage_context_paths

    workspace_started = _ws_time.perf_counter()
    workspace_path = _abuild_workspace_path(session.sandbox, request.repo_url)
    resolved_ref = _aresolve_clone_ref(request.repo_url, request.ref) if request.repo_url else request.ref
    _areconcile_repo_checkout(
        sandbox=session.sandbox,
        repo_url=request.repo_url,
        ref=resolved_ref,
        workspace_path=workspace_path,
    )
    session.phase_timings_ms["workspace_reconcile"] = int((_ws_time.perf_counter() - workspace_started) * 1000)

    context_started = _ws_time.perf_counter()
    context_sources = _astage_context_paths(
        sandbox=session.sandbox,
        workspace_path=workspace_path,
        context_paths=request.context_paths or None,
        reset_existing=True,
    )
    session.phase_timings_ms["context_stage"] = int((_ws_time.perf_counter() - context_started) * 1000)
    session.repo_url = request.repo_url
    session.ref = resolved_ref
    session.workspace_path = workspace_path
    session.context_sources = context_sources
    session.bind_current_async_owner()
    return session


# Backward-compat alias
reconcile_workspace_session = areconcile_workspace_session
