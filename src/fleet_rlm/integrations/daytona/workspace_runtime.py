"""Workspace bootstrap, path helpers, repo clone, and reconciliation for Daytona sandboxes."""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import subprocess
import time
from contextlib import suppress
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .async_compat import _await_if_needed, _run_async_compat
from .config import (
    daytona_import_error as _daytona_import_error,
)
from .config import (
    format_daytona_sdk_error as _format_daytona_sdk_error,
)
from .diagnostics import DaytonaDiagnosticError
from .sandbox_spec import SandboxSpec
from .snapshot_runtime import aresolve_sandbox_spec_snapshot
from .volume_runtime import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
)
from .volume_runtime import (
    aensure_daytona_volume_layout as _aensure_daytona_volume_layout,
)
from .volume_runtime import (
    aensure_remote_directory as _aensure_remote_directory,
)
from .volume_runtime import (
    await_volume_ready as _await_volume_ready,
)

if TYPE_CHECKING:
    from .session_runtime import DaytonaSandboxSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Workspace path helpers
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
# Git ref resolution helpers
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
# Repo clone and checkout reconciliation
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
    if repo_url is None:
        await _aensure_workspace_root(
            sandbox=sandbox,
            workspace_path=workspace_path,
        )
        return

    if not await _apath_exists(sandbox=sandbox, path=workspace_path):
        await _aclone_repo(
            sandbox=sandbox,
            repo_url=repo_url,
            ref=ref,
            workspace_path=workspace_path,
        )
        return

    if not await _apath_has_git_metadata(sandbox=sandbox, path=workspace_path):
        await _areplace_repo_checkout(
            sandbox=sandbox,
            repo_url=repo_url,
            ref=ref,
            workspace_path=workspace_path,
        )
        return

    remote_url = await _agit_remote_url(sandbox=sandbox, workspace_path=workspace_path)
    if remote_url != repo_url:
        await _areplace_repo_checkout(
            sandbox=sandbox,
            repo_url=repo_url,
            ref=ref,
            workspace_path=workspace_path,
        )
        return

    if ref is None:
        await _apull_repo_checkout(sandbox=sandbox, workspace_path=workspace_path)
        return

    if _looks_like_commit(ref):
        await _aforce_checkout_ref(
            sandbox=sandbox,
            workspace_path=workspace_path,
            ref=ref,
            detached=True,
        )
        return

    if await _acheckout_branch_with_sdk(
        sandbox=sandbox,
        workspace_path=workspace_path,
        ref=ref,
    ):
        logger.debug("Checked out Daytona repo branch via SDK before force reconcile")

    await _aforce_checkout_ref(
        sandbox=sandbox,
        workspace_path=workspace_path,
        ref=ref,
        detached=False,
    )


async def _areplace_repo_checkout(
    *,
    sandbox: Any,
    repo_url: str,
    ref: str | None,
    workspace_path: str,
) -> None:
    """Replace a mismatched or non-git checkout before SDK cloning."""
    await _aexec_sandbox_command(
        sandbox=sandbox,
        command=shlex.join(["rm", "-rf", "--", workspace_path]),
        phase="repo_clone",
        error_prefix="Daytona repo replace failure",
    )
    await _aclone_repo(
        sandbox=sandbox,
        repo_url=repo_url,
        ref=ref,
        workspace_path=workspace_path,
    )


async def _apath_exists(*, sandbox: Any, path: str) -> bool:
    result = await _aexec_sandbox_command(
        sandbox=sandbox,
        command=f"test -e {shlex.quote(path)}",
        phase="repo_clone",
        error_prefix="Daytona repo path probe failure",
        check=False,
    )
    return _sandbox_exec_exit_code(result) == 0


async def _apath_has_git_metadata(*, sandbox: Any, path: str) -> bool:
    result = await _aexec_sandbox_command(
        sandbox=sandbox,
        command=f"test -d {shlex.quote(str(PurePosixPath(path) / '.git'))}",
        phase="repo_clone",
        error_prefix="Daytona repo git probe failure",
        check=False,
    )
    return _sandbox_exec_exit_code(result) == 0


async def _agit_remote_url(*, sandbox: Any, workspace_path: str) -> str | None:
    result = await _aexec_git_command(
        sandbox=sandbox,
        workspace_path=workspace_path,
        args=("remote", "get-url", "origin"),
        check=False,
    )
    if _sandbox_exec_exit_code(result) != 0:
        return None
    return _sandbox_exec_stdout(result).strip() or None


async def _apull_repo_checkout(*, sandbox: Any, workspace_path: str) -> None:
    try:
        with suppress(Exception):
            await _await_if_needed(sandbox.git.status(workspace_path))
        await _await_if_needed(sandbox.git.pull(workspace_path))
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona repo pull failure: {exc}",
            category="sandbox_create_clone_error",
            phase="repo_clone",
        ) from exc


async def _acheckout_branch_with_sdk(
    *,
    sandbox: Any,
    workspace_path: str,
    ref: str,
) -> bool:
    try:
        branches = await _await_if_needed(sandbox.git.branches(workspace_path))
        branch_names = _extract_sdk_branch_names(branches)
        if branch_names and ref not in branch_names:
            return False
        await _await_if_needed(sandbox.git.checkout_branch(workspace_path, ref))
        return True
    except Exception:
        return False


def _extract_sdk_branch_names(branches: Any) -> set[str]:
    raw_branches = getattr(branches, "branches", branches)
    raw_items = raw_branches.keys() if isinstance(raw_branches, dict) else raw_branches
    names: set[str] = set()
    for item in raw_items or []:
        name = getattr(item, "name", item)
        if name is not None:
            names.add(str(name).removeprefix("origin/"))
    return names


async def _aforce_checkout_ref(
    *,
    sandbox: Any,
    workspace_path: str,
    ref: str,
    detached: bool,
) -> None:
    await _aexec_git_command(
        sandbox=sandbox,
        workspace_path=workspace_path,
        args=("fetch", "--all", "--tags", "--prune"),
    )
    if detached:
        await _aexec_git_command(
            sandbox=sandbox,
            workspace_path=workspace_path,
            args=("checkout", "--force", ref),
        )
        return

    remote_ref = f"refs/remotes/origin/{ref}"
    remote_probe = await _agit_ref_probe(
        sandbox=sandbox,
        workspace_path=workspace_path,
        ref=remote_ref,
    )
    local_probe = await _agit_ref_probe(
        sandbox=sandbox,
        workspace_path=workspace_path,
        ref=ref,
    )
    if remote_probe:
        branch_exists = await _agit_ref_probe(
            sandbox=sandbox,
            workspace_path=workspace_path,
            ref=f"refs/heads/{ref}",
            verify_arg="show-ref",
        )
        if branch_exists:
            await _aexec_git_command(
                sandbox=sandbox,
                workspace_path=workspace_path,
                args=("checkout", "--force", ref),
            )
        else:
            await _aexec_git_command(
                sandbox=sandbox,
                workspace_path=workspace_path,
                args=("checkout", "--force", "-B", ref, remote_ref),
            )
        await _aexec_git_command(
            sandbox=sandbox,
            workspace_path=workspace_path,
            args=("reset", "--hard", remote_ref),
        )
        return

    if local_probe:
        await _aexec_git_command(
            sandbox=sandbox,
            workspace_path=workspace_path,
            args=("checkout", "--force", ref),
        )
        return

    raise DaytonaDiagnosticError(
        f"Daytona repo checkout failure: requested ref is not available: {ref}",
        category="sandbox_create_clone_error",
        phase="repo_clone",
    )


async def _agit_ref_probe(
    *,
    sandbox: Any,
    workspace_path: str,
    ref: str,
    verify_arg: str = "rev-parse",
) -> bool:
    args = ("show-ref", "--verify", ref) if verify_arg == "show-ref" else ("rev-parse", "--verify", ref)
    result = await _aexec_git_command(
        sandbox=sandbox,
        workspace_path=workspace_path,
        args=args,
        check=False,
    )
    return _sandbox_exec_exit_code(result) == 0


async def _aexec_git_command(
    *,
    sandbox: Any,
    workspace_path: str,
    args: tuple[str, ...],
    check: bool = True,
) -> Any:
    return await _aexec_sandbox_command(
        sandbox=sandbox,
        command=shlex.join(["git", "-C", workspace_path, *args]),
        phase="repo_clone",
        error_prefix="Daytona repo git failure",
        check=check,
    )


async def _aexec_sandbox_command(
    *,
    sandbox: Any,
    command: str,
    phase: str,
    error_prefix: str,
    category: str = "sandbox_create_clone_error",
    check: bool = True,
) -> Any:
    try:
        result = await _await_if_needed(sandbox.process.exec(command))
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"{error_prefix}: {exc}",
            category=category,
            phase=phase,
        ) from exc

    if check and _sandbox_exec_exit_code(result):
        raise DaytonaDiagnosticError(
            f"{error_prefix}: {_sandbox_exec_output(result)}",
            category=category,
            phase=phase,
        )
    return result


def _sandbox_exec_exit_code(result: Any) -> int:
    return int(getattr(result, "exit_code", 0) or 0)


def _sandbox_exec_output(result: Any) -> str:
    return str(
        getattr(result, "stderr", "")
        or getattr(result, "result", "")
        or getattr(getattr(result, "artifacts", None), "stdout", "")
        or getattr(result, "stdout", "")
        or getattr(result, "output", "")
        or "sandbox command failed"
    )


def _sandbox_exec_stdout(result: Any) -> str:
    return str(
        getattr(result, "stdout", "")
        or getattr(result, "result", "")
        or getattr(getattr(result, "artifacts", None), "stdout", "")
        or getattr(result, "output", "")
        or ""
    )


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
    params = spec.to_daytona_create_params(
        volume_id=volume_id,
        create_image_params_cls=CreateSandboxFromImageParams,
        create_snapshot_params_cls=CreateSandboxFromSnapshotParams,
        volume_mount_cls=VolumeMount,
        resources_cls=Resources,
    )

    if spec.uses_declarative_image:
        return await _await_if_needed(
            client.create(
                params,
                timeout=0,
                on_snapshot_create_logs=_ignore_snapshot_create_logs,
            )
        )

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
        resolved_spec = await aresolve_sandbox_spec_snapshot(
            resolved_spec,
            config=runtime._resolved_config,
        )
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
    resolved_spec = request.spec or runtime.build_sandbox_spec(volume_name=request.volume_name)
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
        resolved_ref = await _aresolve_clone_ref(request.repo_url, request.ref) if request.repo_url else request.ref
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
    resolved_ref = await _aresolve_clone_ref(request.repo_url, request.ref) if request.repo_url else request.ref
    await _areconcile_repo_checkout(
        sandbox=session.sandbox,
        repo_url=request.repo_url,
        ref=resolved_ref,
        workspace_path=workspace_path,
    )
    session.phase_timings_ms["workspace_reconcile"] = int((time.perf_counter() - workspace_started) * 1000)

    context_started = time.perf_counter()
    context_sources = await _astage_context_paths(
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
