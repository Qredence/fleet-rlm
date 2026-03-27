"""Shared Daytona runtime helpers extracted from the sandbox runtime module."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import subprocess
import threading
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable, Coroutine, TypeVar, cast

from fleet_rlm.runtime.content.document_ingestion.main import read_document_content
from fleet_rlm.runtime.execution.storage_paths import mounted_storage_roots

from .config import ResolvedDaytonaConfig
from .diagnostics import DaytonaDiagnosticError
from .types_context import ContextSource

DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH = PurePosixPath("/home/daytona/memory")

_REMOTE_REF_RESOLUTION_TIMEOUT_S = 5
_REMOTE_DIRECTORY_MODE = "755"
_T = TypeVar("_T")


def _daytona_import_error(exc: ImportError) -> RuntimeError:
    return RuntimeError(
        "Daytona SDK is not available. Install dependencies with `uv sync` "
        "and configure DAYTONA_API_KEY / DAYTONA_API_URL before using Daytona "
        "commands. See https://www.daytona.io/docs/en/python-sdk/"
    )


def _run_async_compat(
    async_fn: Callable[..., Awaitable[_T]],
    /,
    *args: Any,
    **kwargs: Any,
) -> _T:
    """Run an async implementation from sync code, even inside an active loop."""

    def _runner() -> _T:
        return asyncio.run(cast(Coroutine[Any, Any, _T], async_fn(*args, **kwargs)))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _runner()

    result: dict[str, _T] = {}
    error: dict[str, Exception] = {}

    def _thread_main() -> None:
        try:
            result["value"] = _runner()
        except Exception as exc:  # pragma: no cover - sync compat thread boundary
            error["exc"] = exc

    thread = threading.Thread(target=_thread_main, daemon=True)
    thread.start()
    thread.join()
    if "exc" in error:
        raise error["exc"]
    if "value" not in result:
        raise RuntimeError("Async compatibility runner terminated without a result.")
    return result["value"]


async def _await_if_needed(value: _T | Awaitable[_T]) -> _T:
    if inspect.isawaitable(value):
        return await value
    return value


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


def _looks_like_commit(ref: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", ref.strip()))


def _safe_repo_name(repo_url: str) -> str:
    tail = repo_url.rstrip("/").rsplit("/", 1)[-1]
    tail = tail.removesuffix(".git")
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", tail).strip("-")
    return cleaned or "repo"


def _safe_workspace_name(repo_url: str | None) -> str:
    return _safe_repo_name(repo_url) if repo_url else "daytona-workspace"


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


async def _aget_work_dir(sandbox: Any) -> str:
    if hasattr(sandbox, "get_work_dir"):
        return str(await _await_if_needed(sandbox.get_work_dir()))
    return "/workspace"


async def _abuild_workspace_path(sandbox: Any, repo_url: str | None) -> str:
    work_dir = await _aget_work_dir(sandbox)
    workspace_name = _safe_workspace_name(repo_url)
    return str(PurePosixPath(work_dir) / "workspace" / workspace_name)


async def _aensure_remote_directory(fs: Any, remote_path: PurePosixPath) -> None:
    directory = str(remote_path)
    if directory and directory not in {".", "/"}:
        await _await_if_needed(fs.create_folder(directory, _REMOTE_DIRECTORY_MODE))


async def _aensure_remote_parent(fs: Any, remote_path: PurePosixPath) -> None:
    await _aensure_remote_directory(fs, remote_path.parent)


async def _aensure_workspace_root(*, sandbox: Any, workspace_path: str) -> None:
    try:
        await _aensure_remote_directory(sandbox.fs, PurePosixPath(workspace_path))
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona workspace create failure: {exc}",
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        ) from exc


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
            sandbox.fs, PurePosixPath(workspace_path).parent
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


async def _aupload_remote_text(
    fs: Any, remote_path: PurePosixPath, content: str
) -> None:
    await _aensure_remote_parent(fs, remote_path)
    await _await_if_needed(fs.upload_file(content.encode("utf-8"), str(remote_path)))


async def _aread_document_content(path: Path) -> tuple[str, dict[str, Any]]:
    text, metadata = await asyncio.to_thread(read_document_content, path)
    return text, metadata if isinstance(metadata, dict) else {}


def _build_staged_filename(*, source_path: Path, source_type: str) -> str:
    return (
        source_path.name
        if source_type == "text"
        else f"{source_path.name}.extracted.txt"
    )


async def _astage_local_file(
    *,
    fs: Any,
    resolved_path: Path,
    staged_root: PurePosixPath,
    source_id: str,
) -> ContextSource:
    text, metadata = await _aread_document_content(resolved_path)
    source_type = str(metadata.get("source_type") or "text")
    staged_relative = staged_root / _build_staged_filename(
        source_path=resolved_path,
        source_type=source_type,
    )
    await _aupload_remote_text(fs, staged_relative, text)
    return ContextSource(
        source_id=source_id,
        kind="file",
        host_path=str(resolved_path),
        staged_path=str(staged_relative),
        source_type=source_type,
        extraction_method=str(metadata.get("extraction_method") or "") or None,
        file_count=1,
    )


async def _astage_local_directory(
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
            text, metadata = await _aread_document_content(local_file)
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
        await _aupload_remote_text(fs, staged_relative, text)
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


async def _astage_context_paths(
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
    await _aensure_remote_directory(fs, context_root)
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
                    await _astage_local_directory(
                        fs=fs,
                        resolved_path=resolved,
                        staged_root=staged_root,
                        source_id=source_id,
                    )
                )
            else:
                staged_sources.append(
                    await _astage_local_file(
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
    await _aupload_remote_text(
        fs,
        manifest_path,
        json.dumps(
            {"context_sources": [item.to_dict() for item in staged_sources]},
            ensure_ascii=False,
            indent=2,
        ),
    )
    return staged_sources


__all__ = [
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    "_aget_work_dir",
    "_aclone_repo",
    "_abuild_workspace_path",
    "_aensure_remote_directory",
    "_aensure_remote_parent",
    "_aensure_workspace_root",
    "_aensure_daytona_volume_layout",
    "_aread_document_content",
    "_aresolve_clone_ref",
    "_astage_context_paths",
    "_astage_local_directory",
    "_astage_local_file",
    "_aupload_remote_text",
    "_await_if_needed",
    "_build_clone_kwargs",
    "_build_staged_filename",
    "_build_daytona_client",
    "_daytona_import_error",
    "_looks_like_commit",
    "_run_async_compat",
    "_resolve_clone_ref",
    "_resolve_local_context_path",
    "_safe_context_slug",
    "_safe_repo_name",
    "_safe_workspace_name",
]
