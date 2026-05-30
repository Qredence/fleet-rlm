"""Workspace session orchestration and validated request models for Daytona sandboxes."""

from __future__ import annotations

import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ._git_helpers import (
    _abuild_workspace_path,
    _aclone_repo,
    _aensure_workspace_root,
    _areconcile_repo_checkout,
    _aresolve_clone_ref,
)
from .async_compat import _run_async_compat
from .models import SandboxSpec
from .sdk_ops import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
)
from .sdk_ops import (
    aensure_daytona_volume_layout as _aensure_daytona_volume_layout,
)

if TYPE_CHECKING:
    from .session_runtime import DaytonaSandboxSession


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
        create_started = time.perf_counter()
        sandbox = await runtime.acreate_sandbox(spec=resolved_spec)
        timings["sandbox_create"] = int((time.perf_counter() - create_started) * 1000)

        effective_volume = resolved_spec.volume_name or request.volume_name
        if effective_volume:
            await _aensure_daytona_volume_layout(
                sandbox=sandbox,
                mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
            )

        workspace_path = _abuild_workspace_path(sandbox, request.repo_url)
        resolved_ref = _aresolve_clone_ref(request.repo_url, request.ref) if request.repo_url else request.ref
        if request.repo_url:
            clone_started = time.perf_counter()
            _aclone_repo(
                sandbox=sandbox,
                repo_url=request.repo_url,
                ref=resolved_ref,
                workspace_path=workspace_path,
            )
            timings["repo_clone"] = int((time.perf_counter() - clone_started) * 1000)
        else:
            _aensure_workspace_root(
                sandbox=sandbox,
                workspace_path=workspace_path,
            )

        context_started = time.perf_counter()
        context_sources = _astage_context_paths(
            sandbox=sandbox,
            workspace_path=workspace_path,
            context_paths=request.context_paths or None,
        )
        timings["context_stage"] = int((time.perf_counter() - context_started) * 1000)

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
            with suppress(Exception):
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

    workspace_started = time.perf_counter()
    workspace_path = _abuild_workspace_path(session.sandbox, request.repo_url)
    resolved_ref = _aresolve_clone_ref(request.repo_url, request.ref) if request.repo_url else request.ref
    _areconcile_repo_checkout(
        sandbox=session.sandbox,
        repo_url=request.repo_url,
        ref=resolved_ref,
        workspace_path=workspace_path,
    )
    session.phase_timings_ms["workspace_reconcile"] = int((time.perf_counter() - workspace_started) * 1000)

    context_started = time.perf_counter()
    context_sources = _astage_context_paths(
        sandbox=session.sandbox,
        workspace_path=workspace_path,
        context_paths=request.context_paths or None,
        reset_existing=True,
    )
    session.phase_timings_ms["context_stage"] = int((time.perf_counter() - context_started) * 1000)
    session.repo_url = request.repo_url
    session.ref = resolved_ref
    session.workspace_path = workspace_path
    session.context_sources = context_sources
    session.bind_current_async_owner()
    return session


# Backward-compat alias
reconcile_workspace_session = areconcile_workspace_session


__all__ = [
    "WorkspaceSessionCreateRequest",
    "WorkspaceSessionReconcileRequest",
    "acreate_workspace_session",
    "areconcile_workspace_session",
    "create_workspace_session",
    "reconcile_workspace_session",
]
