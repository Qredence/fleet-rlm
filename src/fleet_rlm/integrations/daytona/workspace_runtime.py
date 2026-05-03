"""Workspace bootstrap, path helpers, repo clone, and reconciliation for Daytona sandboxes."""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from contextlib import suppress
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .async_compat import _await_if_needed, _run_async_compat
from .config import (
    daytona_import_error as _daytona_import_error,
    format_daytona_sdk_error as _format_daytona_sdk_error,
)
from .diagnostics import DaytonaDiagnosticError
from .snapshot_runtime import aresolve_snapshot, fallback_to_declarative_image
from .types import SandboxSpec
from .volume_runtime import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    aensure_daytona_volume_layout as _aensure_daytona_volume_layout,
    aensure_remote_directory as _aensure_remote_directory,
    await_volume_ready as _await_volume_ready,
)

if TYPE_CHECKING:
    from .session_runtime import DaytonaSandboxSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Workspace path helpers (formerly workspace_paths.py)
# ---------------------------------------------------------------------------


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_repo_name(repo_url: str) -> str:
    tail = repo_url.rstrip("/").rsplit("/", 1)[-1]
    tail = tail.removesuffix(".git")
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", tail).strip("-")
    return cleaned or "repo"


def _safe_workspace_name(repo_url: str | None) -> str:
    return _safe_repo_name(repo_url) if repo_url else "daytona-workspace"


async def _aget_work_dir(sandbox: Any) -> str:
    if hasattr(sandbox, "get_work_dir"):
        return str(await _await_if_needed(sandbox.get_work_dir()))
    return "/workspace"


async def _abuild_workspace_path(sandbox: Any, repo_url: str | None) -> str:
    work_dir = await _aget_work_dir(sandbox)
    workspace_name = _safe_workspace_name(repo_url)
    return str(PurePosixPath(work_dir) / "workspace" / workspace_name)


async def _aensure_workspace_root(*, sandbox: Any, workspace_path: str) -> None:
    try:
        await _aensure_remote_directory(sandbox.fs, PurePosixPath(workspace_path))
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona workspace create failure: {exc}",
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        ) from exc


# ---------------------------------------------------------------------------
# Git ref resolution helpers (formerly repo_refs.py)
# ---------------------------------------------------------------------------

_REMOTE_REF_RESOLUTION_TIMEOUT_S = 5


def _looks_like_commit(ref: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", ref.strip()))


def _list_remote_refs(repo_url: str) -> set[str]:
    try:
        completed = subprocess.run(
            ["git", "ls-remote", "--heads", "--tags", repo_url],
            capture_output=True,
            check=False,
            text=True,
            timeout=_REMOTE_REF_RESOLUTION_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    if completed.returncode != 0:
        return set()

    refs: set[str] = set()
    for line in completed.stdout.splitlines():
        if "\t" not in line:
            continue
        _sha, raw_ref = line.split("\t", 1)
        normalized = raw_ref.strip()
        if normalized.startswith("refs/heads/"):
            refs.add(normalized.removeprefix("refs/heads/"))
            continue
        if normalized.startswith("refs/tags/"):
            refs.add(normalized.removeprefix("refs/tags/").removesuffix("^{}"))
    return refs


def _resolve_clone_ref(repo_url: str, ref: str | None) -> str | None:
    normalized = str(ref or "").strip() or None
    if normalized is None or _looks_like_commit(normalized) or "/" not in normalized:
        return normalized

    remote_refs = _list_remote_refs(repo_url)
    if not remote_refs or normalized in remote_refs:
        return normalized

    segments = [segment for segment in normalized.split("/") if segment]
    for end in range(len(segments) - 1, 0, -1):
        candidate = "/".join(segments[:end])
        if candidate in remote_refs:
            return candidate
    return normalized


async def _aresolve_clone_ref(repo_url: str, ref: str | None) -> str | None:
    return await asyncio.to_thread(_resolve_clone_ref, repo_url, ref)


# ---------------------------------------------------------------------------
# Repo clone and checkout reconciliation (formerly repo_checkout.py)
# ---------------------------------------------------------------------------


def _build_clone_kwargs(
    *,
    repo_url: str,
    ref: str | None,
    workspace_path: str,
) -> dict[str, str]:
    clone_kwargs: dict[str, str] = {"url": repo_url, "path": workspace_path}
    if ref:
        if _looks_like_commit(ref):
            clone_kwargs["commit_id"] = ref
        else:
            clone_kwargs["branch"] = ref
    return clone_kwargs


async def _aclone_repo(
    *,
    sandbox: Any,
    repo_url: str,
    ref: str | None,
    workspace_path: str,
) -> None:
    try:
        await _aensure_remote_directory(
            sandbox.fs,
            PurePosixPath(workspace_path).parent,
        )
        await _await_if_needed(
            sandbox.git.clone(
                **_build_clone_kwargs(
                    repo_url=repo_url,
                    ref=ref,
                    workspace_path=workspace_path,
                )
            )
        )
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona repo clone failure: {exc}",
            category="sandbox_create_clone_error",
            phase="repo_clone",
        ) from exc


async def _areconcile_repo_checkout(
    *,
    sandbox: Any,
    repo_url: str | None,
    ref: str | None,
    workspace_path: str,
) -> None:
    from .session_runtime import _arun_admin_code

    if repo_url is None:
        await _aensure_workspace_root(
            sandbox=sandbox,
            workspace_path=workspace_path,
        )
        return

    await _arun_admin_code(
        sandbox=sandbox,
        phase="repo_clone",
        error_prefix="Daytona repo reconcile failure",
        code=_build_repo_reconcile_script(
            repo_url=repo_url,
            ref=ref,
            workspace_path=workspace_path,
        ),
    )


def _build_repo_reconcile_script(
    *,
    repo_url: str,
    ref: str | None,
    workspace_path: str,
) -> str:
    return f"""
import json as _json
import pathlib as _pathlib
import shutil as _shutil
import subprocess as _subprocess

repo_url = {repo_url!r}
ref = {ref!r}
workspace_path = {workspace_path!r}
workspace = _pathlib.Path(workspace_path)
workspace.parent.mkdir(parents=True, exist_ok=True)

def _run(*args: str, check: bool = True):
    completed = _subprocess.run(
        list(args),
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"command failed: {{' '.join(args)}}"
        )
    return completed

if not workspace.exists():
    _run("git", "clone", repo_url, workspace_path)
else:
    git_dir = workspace / ".git"
    if not git_dir.exists():
        if any(workspace.iterdir()):
            raise RuntimeError(
                f"workspace exists without git metadata: {{workspace_path}}"
            )
        _run("git", "clone", repo_url, workspace_path)
    else:
        remote = _run(
            "git",
            "-C",
            workspace_path,
            "remote",
            "get-url",
            "origin",
            check=False,
        )
        remote_url = remote.stdout.strip()
        if remote.returncode != 0 or remote_url != repo_url:
            _shutil.rmtree(workspace)
            _run("git", "clone", repo_url, workspace_path)
        else:
            _run("git", "-C", workspace_path, "fetch", "--all", "--tags", "--prune")

if ref:
    remote_ref = f"refs/remotes/origin/{{ref}}"
    remote_probe = _run(
        "git",
        "-C",
        workspace_path,
        "rev-parse",
        "--verify",
        remote_ref,
        check=False,
    )
    local_probe = _run(
        "git",
        "-C",
        workspace_path,
        "rev-parse",
        "--verify",
        ref,
        check=False,
    )
    if remote_probe.returncode == 0:
        branch_probe = _run(
            "git",
            "-C",
            workspace_path,
            "show-ref",
            "--verify",
            f"refs/heads/{{ref}}",
            check=False,
        )
        if branch_probe.returncode == 0:
            _run("git", "-C", workspace_path, "checkout", "--force", ref)
        else:
            _run(
                "git",
                "-C",
                workspace_path,
                "checkout",
                "--force",
                "-B",
                ref,
                remote_ref,
            )
        _run("git", "-C", workspace_path, "reset", "--hard", remote_ref)
    elif local_probe.returncode == 0:
        _run("git", "-C", workspace_path, "checkout", "--force", ref)
    else:
        raise RuntimeError(f"requested ref is not available: {{ref}}")

print(
    _json.dumps(
        {{
            "repo_url": repo_url,
            "ref": ref,
            "workspace_path": workspace_path,
        }},
        ensure_ascii=False,
    )
)
""".strip()


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


def _ignore_snapshot_create_logs(_message: Any) -> None:
    return None


async def _aresolve_volume_id(*, runtime: Any, spec: SandboxSpec) -> str | None:
    if not spec.volume_name:
        return None
    client = await runtime._aget_client()
    volume = await _await_if_needed(client.volume.get(spec.volume_name, create=True))
    volume = await _await_volume_ready(client, spec.volume_name, volume)
    return str(volume.id)


def _prepare_daytona_create_kwargs(
    spec: SandboxSpec,
    *,
    volume_id: str | None,
    volume_mount_cls: Any,
    resources_cls: Any,
) -> dict[str, Any]:
    create_kwargs = spec.to_create_params(volume_id=volume_id)

    raw_volumes = create_kwargs.pop("volumes", None)
    if raw_volumes:
        create_kwargs["volumes"] = [volume_mount_cls(**item) for item in raw_volumes]

    raw_resources = create_kwargs.pop("resources", None)
    if raw_resources:
        create_kwargs["resources"] = resources_cls(**raw_resources)

    return create_kwargs


async def acreate_sandbox_from_spec(
    *,
    runtime: Any,
    spec: SandboxSpec,
) -> Any:
    """Create a sandbox from a declarative ``SandboxSpec``."""
    try:
        from daytona import (
            CreateSandboxFromImageParams,
            CreateSandboxFromSnapshotParams,
            Resources,
            VolumeMount,
        )
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc

    client = await runtime._aget_client()
    volume_id = await _aresolve_volume_id(runtime=runtime, spec=spec)
    create_kwargs = _prepare_daytona_create_kwargs(
        spec,
        volume_id=volume_id,
        volume_mount_cls=VolumeMount,
        resources_cls=Resources,
    )

    if spec.uses_declarative_image:
        params = CreateSandboxFromImageParams(**create_kwargs)
        return await _await_if_needed(
            client.create(
                params,
                timeout=0,
                on_snapshot_create_logs=_ignore_snapshot_create_logs,
            )
        )

    params = CreateSandboxFromSnapshotParams(**create_kwargs)
    return await _await_if_needed(client.create(params))


async def acreate_sandbox(
    *,
    runtime: Any,
    volume_name: str | None = None,
    spec: SandboxSpec | None = None,
) -> Any:
    """Create a sandbox, falling back from inactive snapshots when needed."""
    try:
        resolved_spec = spec or runtime.build_sandbox_spec(volume_name=volume_name)
        if resolved_spec.snapshot and not resolved_spec.uses_declarative_image:
            active_snapshot = await aresolve_snapshot(
                resolved_spec.snapshot,
                config=runtime._resolved_config,
            )
            if active_snapshot is None:
                logger.info(
                    "Snapshot '%s' not active; falling back to declarative image",
                    resolved_spec.snapshot,
                )
                resolved_spec = fallback_to_declarative_image(resolved_spec)
        return await acreate_sandbox_from_spec(runtime=runtime, spec=resolved_spec)
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona sandbox create failure: {_format_daytona_sdk_error(exc)}",
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        ) from exc


async def acreate_workspace_session(
    *,
    runtime: Any,
    request: WorkspaceSessionCreateRequest,
) -> DaytonaSandboxSession:
    """Create a fully prepared workspace session inside a Daytona sandbox."""
    from .context_staging import _astage_context_paths

    timings = {"sandbox_create": 0, "repo_clone": 0, "context_stage": 0}
    sandbox: Any | None = None
    resolved_spec = request.spec or runtime.build_sandbox_spec(
        volume_name=request.volume_name
    )
    try:
        create_started = time.perf_counter()
        sandbox = await acreate_sandbox(runtime=runtime, spec=resolved_spec)
        timings["sandbox_create"] = int((time.perf_counter() - create_started) * 1000)

        effective_volume = resolved_spec.volume_name or request.volume_name
        if effective_volume:
            await _aensure_daytona_volume_layout(
                sandbox=sandbox,
                mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
            )

        workspace_path = await _abuild_workspace_path(sandbox, request.repo_url)
        resolved_ref = (
            await _aresolve_clone_ref(request.repo_url, request.ref)
            if request.repo_url
            else request.ref
        )
        if request.repo_url:
            clone_started = time.perf_counter()
            await _aclone_repo(
                sandbox=sandbox,
                repo_url=request.repo_url,
                ref=resolved_ref,
                workspace_path=workspace_path,
            )
            timings["repo_clone"] = int((time.perf_counter() - clone_started) * 1000)
        else:
            await _aensure_workspace_root(
                sandbox=sandbox,
                workspace_path=workspace_path,
            )

        context_started = time.perf_counter()
        context_sources = await _astage_context_paths(
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
                await _await_if_needed(sandbox.delete())
        raise


def create_workspace_session(
    *,
    runtime: Any,
    request: WorkspaceSessionCreateRequest,
) -> DaytonaSandboxSession:
    return _run_async_compat(
        acreate_workspace_session,
        runtime=runtime,
        request=request,
    )


async def areconcile_workspace_session(
    *,
    session: DaytonaSandboxSession,
    request: WorkspaceSessionReconcileRequest,
) -> DaytonaSandboxSession:
    """Reconcile an existing workspace session to new repo/context inputs."""
    from .context_staging import _astage_context_paths

    workspace_started = time.perf_counter()
    workspace_path = await _abuild_workspace_path(session.sandbox, request.repo_url)
    resolved_ref = (
        await _aresolve_clone_ref(request.repo_url, request.ref)
        if request.repo_url
        else request.ref
    )
    await _areconcile_repo_checkout(
        sandbox=session.sandbox,
        repo_url=request.repo_url,
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
        context_paths=request.context_paths or None,
        reset_existing=True,
    )
    session.phase_timings_ms["context_stage"] = int(
        (time.perf_counter() - context_started) * 1000
    )
    session.repo_url = request.repo_url
    session.ref = resolved_ref
    session.workspace_path = workspace_path
    session.context_sources = context_sources
    session.bind_current_async_owner()
    return session


def reconcile_workspace_session(
    *,
    session: DaytonaSandboxSession,
    request: WorkspaceSessionReconcileRequest,
) -> DaytonaSandboxSession:
    return _run_async_compat(
        areconcile_workspace_session,
        session=session,
        request=request,
    )


__all__ = [
    "WorkspaceSessionCreateRequest",
    "WorkspaceSessionReconcileRequest",
    "acreate_sandbox",
    "acreate_sandbox_from_spec",
    "acreate_workspace_session",
    "areconcile_workspace_session",
    "create_workspace_session",
    "reconcile_workspace_session",
]
