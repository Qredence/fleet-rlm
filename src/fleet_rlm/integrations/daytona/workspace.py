"""Workspace and file-staging helpers for Daytona sandboxes."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from fleet_rlm.runtime.content.ingestion import read_document_content

from .admin import _arun_admin_code
from .async_compat import _await_if_needed
from .diagnostics import DaytonaDiagnosticError
from fleet_rlm.utils.paths import is_local_path
from .types import ContextSource

_REMOTE_DIRECTORY_MODE = "755"


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


async def _aclear_staged_context_paths(
    *,
    sandbox: Any,
    workspace_path: str,
) -> None:
    context_root = PurePosixPath(workspace_path) / ".fleet-rlm" / "context"
    await _arun_admin_code(
        sandbox=sandbox,
        phase="context_stage",
        category="context_stage_error",
        error_prefix="Daytona context reset failure",
        code=f"""
import pathlib as _pathlib
import shutil as _shutil

context_root = _pathlib.Path({str(context_root)!r})
if context_root.exists():
    _shutil.rmtree(context_root)
print(str(context_root))
""".strip(),
    )


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
    reset_existing: bool = False,
) -> list[ContextSource]:
    raw_paths = [
        stripped
        for item in (context_paths or [])
        if (stripped := str(item).strip()) and is_local_path(stripped)
    ]
    if reset_existing:
        await _aclear_staged_context_paths(
            sandbox=sandbox,
            workspace_path=workspace_path,
        )
    if not raw_paths:
        return []

    fs = sandbox.fs
    context_root = PurePosixPath(workspace_path) / ".fleet-rlm" / "context"
    await _aensure_remote_directory(fs, context_root)
    staged_sources: list[ContextSource] = []

    for index, raw_path in enumerate(raw_paths, start=1):
        source_id = f"context-{index}"
        display_path = raw_path
        try:
            resolved = _resolve_local_context_path(raw_path)
            display_path = str(resolved)
            staged_root = (
                context_root
                / f"{index:02d}-{_safe_context_slug(resolved.stem or resolved.name)}"
            )
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
                f"Failed to stage context path '{display_path}': {exc}",
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
