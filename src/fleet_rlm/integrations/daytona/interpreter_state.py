"""Workspace state, session manifests, and runtime metadata for Daytona interpreters."""

from __future__ import annotations

from typing import Any

from fleet_rlm.utils.paths import dedupe_paths

from .payload_models import normalized_context_sources
from .session_runtime import DaytonaSandboxSession


class DaytonaInterpreterStateMixin:
    def configure_workspace(
        self: Any,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        sandbox_labels: dict[str, str] | None = None,
        force_new_session: bool = False,
    ) -> None:
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
        should_recreate = force_new_session or self._session_needs_recreation(
            desired_volume=normalized_volume
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
            self._last_sandbox_transition = "reused"
            self._last_workspace_reconfigured = self._session_source_key != source_key

    async def aconfigure_workspace(
        self: Any,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        sandbox_labels: dict[str, str] | None = None,
        force_new_session: bool = False,
    ) -> None:
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
        should_recreate = force_new_session or self._session_needs_recreation(
            desired_volume=normalized_volume
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
            self._last_sandbox_transition = "reused"
            self._last_workspace_reconfigured = self._session_source_key != source_key

    def _normalized_workspace_config(
        self: Any,
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
        self: Any,
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

    def _session_needs_recreation(self: Any, *, desired_volume: str | None) -> bool:
        active_session = self._session
        if active_session is not None:
            return getattr(active_session, "volume_name", None) != desired_volume
        if self._persisted_sandbox_id is None:
            return False
        return self._persisted_volume_name != desired_volume

    def _apply_imported_session_state(self: Any, state: dict[str, Any]) -> None:
        raw_daytona = state.get("daytona", {})
        daytona_state = raw_daytona if isinstance(raw_daytona, dict) else {}
        self.repo_url = str(daytona_state.get("repo_url", "") or "").strip() or None
        self.repo_ref = str(daytona_state.get("repo_ref", "") or "").strip() or None
        self.context_paths = dedupe_paths(
            [str(item) for item in daytona_state.get("context_paths", []) or []]
        )
        self._persisted_sandbox_id = (
            str(daytona_state.get("sandbox_id", "") or "").strip() or None
        )
        self._persisted_workspace_path = (
            str(daytona_state.get("workspace_path", "") or "").strip() or None
        )
        self._persisted_context_sources = normalized_context_sources(
            daytona_state.get("context_sources", [])
        )
        self._persisted_context_id = (
            str(daytona_state.get("context_id", "") or "").strip() or None
        )
        self._persisted_volume_name = (
            str(daytona_state.get("volume_name", "") or "").strip() or None
        )
        self.volume_name = self._persisted_volume_name or self.volume_name
        self.volume_subpath = (
            str(daytona_state.get("volume_subpath", "") or "").strip()
            or self.volume_subpath
        )
        self._session_source_key = (
            self.repo_url,
            self.repo_ref,
            tuple(self.context_paths),
            self.volume_name,
        )

    def export_session_state(self: Any) -> dict[str, Any]:
        self._persist_session_snapshot()
        context_sources = (
            list(self._session.context_sources)
            if self._session is not None
            else list(self._persisted_context_sources)
        )
        return {
            "daytona": {
                "repo_url": self.repo_url,
                "repo_ref": self.repo_ref,
                "context_paths": list(self.context_paths),
                "sandbox_id": (
                    self._session.sandbox_id
                    if self._session is not None
                    else self._persisted_sandbox_id
                ),
                "workspace_path": (
                    self._session.workspace_path
                    if self._session is not None
                    else self._persisted_workspace_path
                ),
                "context_sources": [
                    item.to_dict() if hasattr(item, "to_dict") else item
                    for item in context_sources
                ],
                "context_id": (
                    self._session.context_id
                    if self._session is not None
                    else self._persisted_context_id
                ),
                "volume_name": (
                    getattr(self._session, "volume_name", None) or self.volume_name
                    if self._session is not None
                    else self._persisted_volume_name or self.volume_name
                ),
                "volume_subpath": self.volume_subpath,
            }
        }

    def import_session_state(self: Any, state: dict[str, Any]) -> None:
        self._detach_session(delete=False)
        self._apply_imported_session_state(state)

    async def aimport_session_state(self: Any, state: dict[str, Any]) -> None:
        await self._adetach_session(delete=False)
        self._apply_imported_session_state(state)

    def _persist_session_snapshot(
        self: Any, session: DaytonaSandboxSession | None = None
    ) -> None:
        active_session = session or self._session
        if active_session is None:
            return
        self._persisted_sandbox_id = active_session.sandbox_id
        self._persisted_workspace_path = active_session.workspace_path
        self._persisted_context_sources = list(active_session.context_sources)
        self._persisted_context_id = active_session.context_id
        self._persisted_volume_name = (
            getattr(active_session, "volume_name", None) or self.volume_name
        )

    def _clear_persisted_session(self: Any) -> None:
        self._persisted_sandbox_id = None
        self._persisted_workspace_path = None
        self._persisted_context_sources = []
        self._persisted_context_id = None
        self._persisted_volume_name = None

    def reset_runtime_degradation_state(self: Any) -> None:
        self._runtime_degraded = False
        self._runtime_failure_category = None
        self._runtime_failure_phase = None
        self._runtime_fallback_used = False

    def mark_runtime_degradation(
        self: Any,
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

    def _mark_runtime_degradation_from_exception(self: Any, exc: BaseException) -> None:
        self.mark_runtime_degradation(
            category=str(getattr(exc, "category", "") or "").strip() or None,
            phase=str(getattr(exc, "phase", "") or "").strip() or None,
            fallback_used=True,
        )

    def current_runtime_metadata(self: Any) -> dict[str, Any]:
        session = self._session
        metadata: dict[str, Any] = {
            "sandbox_active": session is not None,
            "workspace_reconfigured": self._last_workspace_reconfigured,
            "runtime_degraded": bool(self._runtime_degraded),
            "runtime_fallback_used": bool(self._runtime_fallback_used),
        }
        sandbox_id = (
            session.sandbox_id if session is not None else self._persisted_sandbox_id
        )
        workspace_path = (
            session.workspace_path
            if session is not None
            else self._persisted_workspace_path
        )
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


__all__ = ["DaytonaInterpreterStateMixin"]
