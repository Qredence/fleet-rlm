"""Session lifecycle orchestration for Daytona interpreters."""

from __future__ import annotations

import inspect
from dataclasses import replace
from typing import Any, Callable

from .async_compat import _run_async_compat
from .runtime import DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH, DaytonaSandboxRuntime
from .sandbox_spec import SandboxSpec
from .session_runtime import DaytonaSandboxSession


class DaytonaInterpreterSessionMixin:
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
        return any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )

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
        resume_workspace_session = getattr(self.runtime, "aresume_workspace_session")
        resume_kwargs: dict[str, Any] = {
            "sandbox_id": sandbox_id,
            "repo_url": repo_url,
            "ref": ref,
            "workspace_path": workspace_path,
            "context_sources": context_sources,
            "context_id": context_id,
        }
        if self._callable_accepts_kwarg(resume_workspace_session, "volume_name"):
            resume_kwargs["volume_name"] = (
                self._persisted_volume_name or self.volume_name
            )
        return await resume_workspace_session(**resume_kwargs)

    async def _areconcile_workspace_session(
        self: Any,
        session: DaytonaSandboxSession,
    ) -> DaytonaSandboxSession:
        reconcile_workspace_session = getattr(
            self.runtime, "areconcile_workspace_session", None
        )
        if not callable(reconcile_workspace_session):
            raise RuntimeError("Runtime does not support workspace reconciliation")
        return await reconcile_workspace_session(
            session,
            repo_url=self.repo_url,
            ref=self.repo_ref,
            context_paths=list(self.context_paths),
        )

    def start(self: Any) -> None:
        _run_async_compat(self.astart)

    async def astart(self: Any) -> None:
        if self._started:
            return
        session = await self._aensure_session_impl()
        await session.astart_driver(timeout=float(self.execute_timeout or self.timeout))
        if self.child_isolation_metadata and session.sandbox_id:
            self.child_isolation_metadata.setdefault(
                "child_sandbox_id", session.sandbox_id
            )
        self._started = True

    def shutdown(self: Any) -> None:
        _run_async_compat(self.ashutdown)

    async def ashutdown(self: Any) -> None:
        try:
            await self._adetach_session(delete=self.delete_session_on_shutdown)
        finally:
            self._started = False
            await self._aclose_runtime()

    def _ensure_session_sync(self: Any) -> DaytonaSandboxSession:
        return _run_async_compat(self._aensure_session_impl)

    def _session_matches_current_async_owner(
        self, session: DaytonaSandboxSession
    ) -> bool:
        matches_current_owner = getattr(session, "matches_current_async_owner", None)
        if callable(matches_current_owner):
            return bool(matches_current_owner())
        return False

    def _current_session_source_key(
        self: Any,
    ) -> tuple[str | None, str | None, tuple[str, ...], str | None]:
        return (
            self.repo_url,
            self.repo_ref,
            tuple(self.context_paths),
            self.volume_name,
        )

    def _attach_execution_callback(
        self: Any, session: DaytonaSandboxSession | None
    ) -> DaytonaSandboxSession | None:
        if session is not None:
            session.execution_event_callback = self.execution_event_callback
        return session

    async def _afinalize_session(
        self: Any,
        session: DaytonaSandboxSession,
        *,
        source_key: tuple[str | None, str | None, tuple[str, ...], str | None],
        transition: str,
        workspace_reconfigured: bool,
    ) -> DaytonaSandboxSession:
        self._session = self._attach_execution_callback(session)
        self._session_source_key = source_key
        await self._areset_execution_state()
        self._persist_session_snapshot()
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
                transition="reused",
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
            transition="reused",
            workspace_reconfigured=True,
        )
        return session, False

    def _clear_persisted_session_for_volume_change(self: Any) -> bool:
        if self._persisted_sandbox_id is None:
            return False
        if self._persisted_volume_name == self.volume_name:
            return False
        self._clear_persisted_session()
        return True

    @staticmethod
    def _should_reconcile_resumed_session(
        persisted_source_key: tuple[str | None, str | None, tuple[str, ...], str | None]
        | None,
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
                transition="resumed",
                workspace_reconfigured=workspace_reconfigured,
            )
            return session, False
        except Exception as exc:
            self._mark_runtime_degradation_from_exception(exc)
            self._clear_persisted_session()
            return None, True

    def _effective_sandbox_spec(self: Any) -> SandboxSpec:
        """Return the sandbox spec with current volume and owner labels applied."""
        labels = dict(getattr(self.sandbox_spec, "labels", None) or {})
        labels.update(self.sandbox_labels)
        if isinstance(self.sandbox_spec, SandboxSpec):
            return replace(
                self.sandbox_spec,
                volume_name=self.volume_name or self.sandbox_spec.volume_name,
                volume_subpath=(
                    self.volume_subpath or self.sandbox_spec.volume_subpath
                ),
                labels=labels or None,
            )
        build_sandbox_spec = getattr(self.runtime, "build_sandbox_spec", None)
        if callable(build_sandbox_spec):
            return build_sandbox_spec(
                volume_name=self.volume_name,
                volume_subpath=self.volume_subpath,
                labels=labels or None,
            )
        return SandboxSpec(
            volume_name=self.volume_name,
            volume_mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
            volume_subpath=self.volume_subpath,
            labels=labels or None,
        )

    async def _acreate_session_from_runtime(
        self: Any,
        *,
        source_key: tuple[str | None, str | None, tuple[str, ...], str | None],
        should_report_recreated: bool,
    ) -> DaytonaSandboxSession:
        session = await self.runtime.acreate_workspace_session(
            repo_url=self.repo_url,
            ref=self.repo_ref,
            context_paths=list(self.context_paths),
            volume_name=self.volume_name,
            spec=self._effective_sandbox_spec(),
        )
        return await self._afinalize_session(
            session,
            source_key=source_key,
            transition="recreated" if should_report_recreated else "created",
            workspace_reconfigured=False,
        )

    async def _aensure_session_impl(self: Any) -> DaytonaSandboxSession:
        self._ensure_runtime_available()
        source_key = self._current_session_source_key()
        should_report_recreated = False

        active_session, active_recreated = await self._aresolve_active_session(
            source_key=source_key
        )
        if active_session is not None:
            return active_session
        should_report_recreated = should_report_recreated or active_recreated

        if self._clear_persisted_session_for_volume_change():
            should_report_recreated = True

        persisted_session, persisted_recreated = await self._aresolve_persisted_session(
            source_key=source_key
        )
        if persisted_session is not None:
            return persisted_session
        should_report_recreated = should_report_recreated or persisted_recreated

        return await self._acreate_session_from_runtime(
            source_key=source_key,
            should_report_recreated=should_report_recreated,
        )

    async def _aensure_session(self: Any) -> DaytonaSandboxSession:
        session = await self._aensure_session_impl()
        await session.arefresh_activity()
        return session

    async def aget_session(self: Any) -> DaytonaSandboxSession:
        """Public async accessor to ensure and return the active sandbox session."""
        return await self._aensure_session()

    def _detach_session(self: Any, *, delete: bool) -> None:
        _run_async_compat(self._adetach_session, delete=delete)

    async def _adetach_session(self: Any, *, delete: bool) -> None:
        active_session = self._session
        if active_session is None:
            if delete:
                self._clear_persisted_session()
            await self._areset_execution_state()
            self._started = False
            return

        self._persist_session_snapshot(active_session)
        await self._aclose_bridge()
        try:
            if delete:
                await active_session.adelete()
            elif self.delete_context_on_shutdown:
                await active_session.adelete_context()
            else:
                await active_session.aclose_driver()
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

    async def _aclose_bridge(self: Any) -> None:
        bridge = self._bridge
        self._bridge = None
        self._bridge_sandbox_id = None
        self._bridge_context_id = None
        if bridge is not None:
            await bridge.aclose()

    async def _aclose_runtime(self: Any) -> None:
        if not self._owns_runtime or self._runtime_closed:
            return
        await self.runtime.aclose()
        self._runtime_closed = True

    def _ensure_runtime_available(self: Any) -> None:
        runtime = self.runtime
        if not self._owns_runtime or not isinstance(runtime, DaytonaSandboxRuntime):
            return
        if not self._runtime_closed:
            return
        if self._runtime_config is None:
            raise RuntimeError(
                "Owned Daytona runtime cannot be recreated without config"
            )
        self.runtime = DaytonaSandboxRuntime(config=self._runtime_config)
        self._runtime_closed = False

    def _reset_execution_state(self: Any) -> None:
        _run_async_compat(self._areset_execution_state)

    async def _areset_execution_state(self: Any) -> None:
        await self._aclose_bridge()
        self._setup_context_id = None
        self._setup_workspace_path = None
        self._submit_signature_key = None


__all__ = ["DaytonaInterpreterSessionMixin"]
