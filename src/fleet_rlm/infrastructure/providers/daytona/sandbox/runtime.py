"""Workspace bootstrap and runtime helpers for Daytona sandboxes."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any

from fleet_rlm.features.document_ingestion.main import read_document_content

from . import sdk as support
from .session import DaytonaSandboxSession
from .sdk import DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH
from ..config import ResolvedDaytonaConfig, resolve_daytona_config
from ..diagnostics import DaytonaDiagnosticError
from ..types import ContextSource

_REMOTE_REF_RESOLUTION_TIMEOUT_S = 5


def _looks_like_commit(ref: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", ref.strip()))


def _safe_repo_name(repo_url: str) -> str:
    tail = repo_url.rstrip("/").rsplit("/", 1)[-1]
    tail = tail.removesuffix(".git")
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", tail).strip("-")
    return cleaned or "repo"


def _safe_workspace_name(repo_url: str | None) -> str:
    if repo_url:
        return _safe_repo_name(repo_url)
    return "daytona-workspace"


def _safe_context_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    return cleaned or "context"


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
    if not remote_refs:
        return normalized
    if normalized in remote_refs:
        return normalized

    segments = [segment for segment in normalized.split("/") if segment]
    for end in range(len(segments) - 1, 0, -1):
        candidate = "/".join(segments[:end])
        if candidate in remote_refs:
            return candidate
    return normalized


def _resolve_local_context_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    resolved = candidate.resolve()
    if not resolved.exists():
        raise DaytonaDiagnosticError(
            f"Context path does not exist: {path}",
            category="context_stage_error",
            phase="context_stage",
        )
    if not os.access(resolved, os.R_OK):
        raise DaytonaDiagnosticError(
            f"Context path is not readable: {resolved}",
            category="context_stage_error",
            phase="context_stage",
        )
    return resolved


async def _ensure_remote_parent(fs: Any, remote_path: PurePosixPath) -> None:
    parent = str(remote_path.parent)
    if parent and parent not in {".", "/"}:
        await support._maybe_await(fs.create_folder(parent, "755"))


async def _upload_remote_text(
    fs: Any, remote_path: PurePosixPath, content: str
) -> None:
    await _ensure_remote_parent(fs, remote_path)
    await support._maybe_await(
        fs.upload_file(content.encode("utf-8"), str(remote_path))
    )


def _build_staged_filename(*, source_path: Path, source_type: str) -> str:
    if source_type == "text":
        return source_path.name
    return f"{source_path.name}.extracted.txt"


async def _stage_local_file(
    *,
    fs: Any,
    resolved_path: Path,
    staged_root: PurePosixPath,
    source_id: str,
) -> ContextSource:
    text, metadata = read_document_content(resolved_path)
    source_type = str(metadata.get("source_type") or "text")
    staged_relative = staged_root / _build_staged_filename(
        source_path=resolved_path,
        source_type=source_type,
    )
    await _upload_remote_text(fs, staged_relative, text)
    return ContextSource(
        source_id=source_id,
        kind="file",
        host_path=str(resolved_path),
        staged_path=str(staged_relative),
        source_type=source_type,
        extraction_method=str(metadata.get("extraction_method") or "") or None,
        file_count=1,
    )


async def _stage_local_directory(
    *,
    fs: Any,
    resolved_path: Path,
    staged_root: PurePosixPath,
    source_id: str,
) -> ContextSource:
    warnings: list[str] = []
    staged_count = 0
    skipped_count = 0
    extraction_methods: set[str] = set()
    source_types: set[str] = set()

    for local_file in sorted(
        path for path in resolved_path.rglob("*") if path.is_file()
    ):
        relative_path = local_file.relative_to(resolved_path)
        try:
            text, metadata = read_document_content(local_file)
        except Exception as exc:
            skipped_count += 1
            warnings.append(f"Skipped {relative_path.as_posix()}: {exc}")
            continue

        source_type = str(metadata.get("source_type") or "text")
        extraction_method = str(metadata.get("extraction_method") or "") or None
        source_types.add(source_type)
        if extraction_method:
            extraction_methods.add(extraction_method)
        destination_name = _build_staged_filename(
            source_path=local_file,
            source_type=source_type,
        )
        staged_relative = staged_root / relative_path.parent / destination_name
        await _upload_remote_text(fs, staged_relative, text)
        staged_count += 1

    if staged_count == 0:
        raise DaytonaDiagnosticError(
            f"No supported readable files found in directory: {resolved_path}",
            category="context_stage_error",
            phase="context_stage",
        )

    extraction_method = (
        "mixed"
        if len(extraction_methods) > 1
        else next(iter(extraction_methods), None) or "directory_walk"
    )
    source_type = (
        "mixed" if len(source_types) > 1 else next(iter(source_types), None) or "text"
    )
    return ContextSource(
        source_id=source_id,
        kind="directory",
        host_path=str(resolved_path),
        staged_path=str(staged_root),
        source_type=source_type,
        extraction_method=extraction_method,
        file_count=staged_count,
        skipped_count=skipped_count,
        warnings=warnings,
    )


async def _stage_context_paths(
    *,
    sandbox: Any,
    workspace_path: str,
    context_paths: list[str] | None,
) -> list[ContextSource]:
    raw_paths = [
        str(item).strip() for item in (context_paths or []) if str(item).strip()
    ]
    if not raw_paths:
        return []

    fs = sandbox.fs
    context_root = PurePosixPath(workspace_path) / ".fleet-rlm" / "context"
    await support._maybe_await(fs.create_folder(str(context_root), "755"))
    staged_sources: list[ContextSource] = []
    for index, raw_path in enumerate(raw_paths, start=1):
        resolved = _resolve_local_context_path(raw_path)
        source_id = f"context-{index}"
        staged_root = (
            context_root
            / f"{index:02d}-{_safe_context_slug(resolved.stem or resolved.name)}"
        )
        try:
            if resolved.is_dir():
                staged_sources.append(
                    await _stage_local_directory(
                        fs=fs,
                        resolved_path=resolved,
                        staged_root=staged_root,
                        source_id=source_id,
                    )
                )
                continue
            staged_sources.append(
                await _stage_local_file(
                    fs=fs,
                    resolved_path=resolved,
                    staged_root=staged_root,
                    source_id=source_id,
                )
            )
        except DaytonaDiagnosticError:
            raise
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Failed to stage context path '{resolved}': {exc}",
                category="context_stage_error",
                phase="context_stage",
            ) from exc

    manifest_path = context_root / "manifest.json"
    await _upload_remote_text(
        fs,
        manifest_path,
        json.dumps(
            {"context_sources": [item.to_dict() for item in staged_sources]},
            ensure_ascii=False,
            indent=2,
        ),
    )
    return staged_sources


class DaytonaSandboxRuntime:
    """Factory for Daytona sandboxes used by the pilot."""

    def __init__(self, *, config: ResolvedDaytonaConfig | None = None) -> None:
        support._require_daytona_sdk()
        resolved = config or resolve_daytona_config()
        self._resolved_config = resolved
        self._client = support.build_async_daytona_client(
            config=resolved if config is not None else None
        )

    async def _acreate_volume_mounted_sandbox(self, volume_name: str) -> Any:
        volume = await support._maybe_await(
            self._client.volume.get(volume_name, create=True)
        )
        return await support._maybe_await(
            self._client.create(
                support.CreateSandboxFromSnapshotParams(
                    volumes=[
                        support.VolumeMount(
                            volume_id=volume.id,
                            mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
                        )
                    ]
                )
            )
        )

    async def _acreate_sandbox(self, volume_name: str | None = None) -> Any:
        try:
            if volume_name:
                return await self._acreate_volume_mounted_sandbox(volume_name)
            return await support._maybe_await(self._client.create())
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona sandbox create failure: {exc}",
                category="sandbox_create_clone_error",
                phase="sandbox_create",
            ) from exc

    def _create_sandbox(self, volume_name: str | None = None) -> Any:
        return support._run_async_compat(self._acreate_sandbox(volume_name=volume_name))

    async def _aget_sandbox(self, sandbox_id: str) -> Any:
        try:
            return await support._maybe_await(self._client.get(sandbox_id))
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona sandbox resume failure: {exc}",
                category="sandbox_resume_error",
                phase="sandbox_resume",
            ) from exc

    def _get_sandbox(self, sandbox_id: str) -> Any:
        return support._run_async_compat(self._aget_sandbox(sandbox_id))

    async def _abuild_workspace_path(self, sandbox: Any, repo_url: str | None) -> str:
        work_dir = (
            await support._maybe_await(sandbox.get_work_dir())
            if hasattr(sandbox, "get_work_dir")
            else "/workspace"
        )
        workspace_name = _safe_workspace_name(repo_url)
        return str(PurePosixPath(work_dir) / "workspace" / workspace_name)

    def _build_workspace_path(self, sandbox: Any, repo_url: str | None) -> str:
        return support._run_async_compat(self._abuild_workspace_path(sandbox, repo_url))

    async def _aensure_workspace_root(
        self, *, sandbox: Any, workspace_path: str
    ) -> None:
        try:
            await support._maybe_await(
                sandbox.fs.create_folder(str(PurePosixPath(workspace_path)), "755")
            )
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona workspace create failure: {exc}",
                category="sandbox_create_clone_error",
                phase="sandbox_create",
            ) from exc

    def _ensure_workspace_root(self, *, sandbox: Any, workspace_path: str) -> None:
        support._run_async_compat(
            self._aensure_workspace_root(sandbox=sandbox, workspace_path=workspace_path)
        )

    async def _aclone_repo(
        self, *, sandbox: Any, repo_url: str, ref: str | None, workspace_path: str
    ) -> None:
        try:
            work_dir = (
                await support._maybe_await(sandbox.get_work_dir())
                if hasattr(sandbox, "get_work_dir")
                else "/workspace"
            )
            await support._maybe_await(
                sandbox.fs.create_folder(
                    str(PurePosixPath(work_dir) / "workspace"), "755"
                )
            )

            clone_kwargs: dict[str, Any] = {"url": repo_url, "path": workspace_path}
            if ref:
                if _looks_like_commit(ref):
                    clone_kwargs["commit_id"] = ref
                else:
                    clone_kwargs["branch"] = ref
            await support._maybe_await(sandbox.git.clone(**clone_kwargs))
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona repo clone failure: {exc}",
                category="sandbox_create_clone_error",
                phase="repo_clone",
            ) from exc

    def _clone_repo(
        self, *, sandbox: Any, repo_url: str, ref: str | None, workspace_path: str
    ) -> None:
        support._run_async_compat(
            self._aclone_repo(
                sandbox=sandbox,
                repo_url=repo_url,
                ref=ref,
                workspace_path=workspace_path,
            )
        )

    async def _aensure_workspace_bootstrap(
        self,
        *,
        sandbox: Any,
        repo_url: str | None,
        resolved_ref: str | None,
        workspace_path: str,
        timings: dict[str, int],
    ) -> None:
        if not repo_url:
            await self._aensure_workspace_root(
                sandbox=sandbox,
                workspace_path=workspace_path,
            )
            return

        clone_started = time.perf_counter()
        await self._aclone_repo(
            sandbox=sandbox,
            repo_url=repo_url,
            ref=resolved_ref,
            workspace_path=workspace_path,
        )
        timings["repo_clone"] = int((time.perf_counter() - clone_started) * 1000)

    async def _aresolve_context_sources(
        self,
        *,
        sandbox: Any,
        workspace_path: str,
        context_paths: list[str] | None,
        timings: dict[str, int],
    ) -> list[ContextSource]:
        context_started = time.perf_counter()
        context_sources = await _stage_context_paths(
            sandbox=sandbox,
            workspace_path=workspace_path,
            context_paths=context_paths,
        )
        timings["context_stage"] = int((time.perf_counter() - context_started) * 1000)
        return context_sources

    def _build_workspace_session(
        self,
        *,
        sandbox: Any,
        repo_url: str | None,
        resolved_ref: str | None,
        workspace_path: str,
        context_sources: list[ContextSource],
        timings: dict[str, int],
    ) -> DaytonaSandboxSession:
        session = DaytonaSandboxSession(
            sandbox=sandbox,
            repo_url=repo_url,
            ref=resolved_ref,
            workspace_path=workspace_path,
            context_sources=context_sources,
        )
        session.phase_timings_ms.update(timings)
        return session

    async def acreate_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
        volume_name: str | None = None,
    ) -> DaytonaSandboxSession:
        timings = {"sandbox_create": 0, "repo_clone": 0, "context_stage": 0}
        sandbox: Any | None = None
        try:
            create_started = time.perf_counter()
            sandbox = await self._acreate_sandbox(volume_name=volume_name)
            timings["sandbox_create"] = int(
                (time.perf_counter() - create_started) * 1000
            )

            workspace_path = await self._abuild_workspace_path(sandbox, repo_url)
            resolved_ref = _resolve_clone_ref(repo_url, ref) if repo_url else ref
            await self._aensure_workspace_bootstrap(
                sandbox=sandbox,
                repo_url=repo_url,
                resolved_ref=resolved_ref,
                workspace_path=workspace_path,
                timings=timings,
            )
            context_sources = await self._aresolve_context_sources(
                sandbox=sandbox,
                workspace_path=workspace_path,
                context_paths=context_paths,
                timings=timings,
            )
            return self._build_workspace_session(
                sandbox=sandbox,
                repo_url=repo_url,
                resolved_ref=resolved_ref,
                workspace_path=workspace_path,
                context_sources=context_sources,
                timings=timings,
            )
        except Exception:
            if sandbox is not None and hasattr(sandbox, "delete"):
                with suppress(Exception):
                    await support._maybe_await(sandbox.delete())
            raise

    def create_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
        volume_name: str | None = None,
    ) -> DaytonaSandboxSession:
        return support._run_async_compat(
            self.acreate_workspace_session(
                repo_url=repo_url,
                ref=ref,
                context_paths=context_paths,
                volume_name=volume_name,
            )
        )

    async def aresume_workspace_session(
        self,
        *,
        sandbox_id: str,
        repo_url: str | None,
        ref: str | None,
        workspace_path: str,
        context_sources: list[ContextSource] | None = None,
    ) -> DaytonaSandboxSession:
        resumed_started = time.perf_counter()
        sandbox = await self._aget_sandbox(sandbox_id)
        session = DaytonaSandboxSession(
            sandbox=sandbox,
            repo_url=repo_url,
            ref=ref,
            workspace_path=workspace_path,
            context_sources=context_sources,
        )
        session.phase_timings_ms["sandbox_resume"] = int(
            (time.perf_counter() - resumed_started) * 1000
        )
        return session

    def resume_workspace_session(
        self,
        *,
        sandbox_id: str,
        repo_url: str | None,
        ref: str | None,
        workspace_path: str,
        context_sources: list[ContextSource] | None = None,
    ) -> DaytonaSandboxSession:
        return support._run_async_compat(
            self.aresume_workspace_session(
                sandbox_id=sandbox_id,
                repo_url=repo_url,
                ref=ref,
                workspace_path=workspace_path,
                context_sources=context_sources,
            )
        )


__all__ = ["DaytonaSandboxRuntime", "_resolve_clone_ref"]
