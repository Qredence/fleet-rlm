"""Consolidated Daytona filesystem operations.

This module merges repo staging, workspace creation, context-path staging,
volume browsing, and file upload/download helpers into a single cohesive
surface.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import subprocess
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from fleet_rlm.runtime.content.ingestion import read_document_content
from fleet_rlm.utils.paths import is_local_path
from fleet_rlm.utils.volume_tree import entry_name, stable_tree_id

from .async_compat import _await_if_needed, _run_async_compat
from .config import resolve_daytona_config
from .diagnostics import DaytonaDiagnosticError
from .runtime import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    _aensure_daytona_volume_layout,
    _aensure_remote_directory,
    _arun_admin_code,
    _await_volume_ready,
    _build_daytona_client,
)
from .types import ContextSource

# ---------------------------------------------------------------------------
# Repo helpers
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
# Workspace helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Volume helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ResolvedDaytonaPath:
    display_path: str
    mounted_path: PurePosixPath


@asynccontextmanager
async def _amounted_daytona_volume(volume_name: str) -> AsyncIterator[Any]:
    from daytona import CreateSandboxFromSnapshotParams, VolumeMount

    client = _build_daytona_client(resolve_daytona_config())
    volume = await _await_if_needed(client.volume.get(volume_name, create=True))
    volume = await _await_volume_ready(client, volume_name, volume)
    sandbox = await _await_if_needed(
        client.create(
            CreateSandboxFromSnapshotParams(
                language="python",
                volumes=[
                    VolumeMount(
                        volume_id=volume.id,
                        mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
                    )
                ],
            )
        )
    )
    await _aensure_daytona_volume_layout(
        sandbox=sandbox,
        mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
    )
    try:
        yield sandbox
    finally:
        with suppress(Exception):
            await _await_if_needed(sandbox.delete())
        with suppress(Exception):
            await _await_if_needed(client.close())


@contextmanager
def _mounted_daytona_volume(volume_name: str) -> Iterator[Any]:
    manager = _amounted_daytona_volume(volume_name)
    sandbox = _run_async_compat(manager.__aenter__)
    try:
        yield sandbox
    finally:
        _run_async_compat(manager.__aexit__, None, None, None)


def _resolve_daytona_path(
    path: str,
    *,
    default_path: str = "/",
) -> _ResolvedDaytonaPath:
    candidate = (path or default_path).strip() or default_path
    pure_path = PurePosixPath("/", candidate.lstrip("/"))
    if ".." in pure_path.parts:
        raise ValueError(f"Path traversal not allowed: {candidate!r}")

    mounted_path = DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH.joinpath(*pure_path.parts[1:])
    return _ResolvedDaytonaPath(
        display_path=str(pure_path),
        mounted_path=mounted_path,
    )


def _child_daytona_path(
    parent: _ResolvedDaytonaPath,
    name: str,
) -> _ResolvedDaytonaPath:
    return _ResolvedDaytonaPath(
        display_path=str(PurePosixPath(parent.display_path) / name),
        mounted_path=parent.mounted_path / name,
    )


def _entry_modified_iso(entry: Any) -> str | None:
    mod_time = getattr(entry, "mod_time", None)
    if hasattr(mod_time, "isoformat"):
        return mod_time.isoformat()
    if mod_time is None:
        return None
    return str(mod_time)


async def alist_daytona_volume_tree(
    volume_name: str,
    root_path: str = "/",
    max_depth: int = 4,
) -> dict[str, Any]:
    """Adapt Daytona sandbox.fs listings to the runtime volume tree schema."""
    max_depth = max(1, min(max_depth, 10))
    root = _resolve_daytona_path(root_path, default_path="/")

    counters: dict[str, int] = {"files": 0, "dirs": 0}
    truncated = False

    async def _walk(
        sandbox: Any,
        location: _ResolvedDaytonaPath,
        depth: int,
    ) -> list[dict[str, Any]]:
        nonlocal truncated
        nodes: list[dict[str, Any]] = []
        entries = await _await_if_needed(
            sandbox.fs.list_files(str(location.mounted_path))
        )

        for entry in entries:
            name = entry_name(getattr(entry, "name", "") or getattr(entry, "path", ""))
            if not name:
                continue

            child = _child_daytona_path(location, name)
            is_dir = bool(getattr(entry, "is_dir", False))
            modified_iso = _entry_modified_iso(entry)

            if is_dir:
                counters["dirs"] += 1
                children: list[dict[str, Any]] = []
                if depth + 1 < max_depth:
                    children = await _walk(sandbox, child, depth + 1)
                else:
                    truncated = True
                nodes.append(
                    {
                        "id": stable_tree_id(child.display_path),
                        "name": name,
                        "path": child.display_path,
                        "type": "directory",
                        "children": children,
                        "modified_at": modified_iso,
                    }
                )
            else:
                counters["files"] += 1
                nodes.append(
                    {
                        "id": stable_tree_id(child.display_path),
                        "name": name,
                        "path": child.display_path,
                        "type": "file",
                        "size": getattr(entry, "size", None),
                        "modified_at": modified_iso,
                    }
                )
        return nodes

    async with _amounted_daytona_volume(volume_name) as sandbox:
        children = await _walk(sandbox, root, 0)

    root_node: dict[str, Any] = {
        "id": stable_tree_id(f"daytona-volume:{volume_name}:{root.display_path}"),
        "name": volume_name,
        "path": root.display_path,
        "type": "volume",
        "children": children,
    }

    return {
        "volume_name": volume_name,
        "root_path": root.display_path,
        "nodes": [root_node],
        "total_files": counters["files"],
        "total_dirs": counters["dirs"],
        "truncated": truncated,
    }


def list_daytona_volume_tree(
    volume_name: str,
    root_path: str = "/",
    max_depth: int = 4,
) -> dict[str, Any]:
    return _run_async_compat(
        alist_daytona_volume_tree,
        volume_name,
        root_path,
        max_depth,
    )


async def aread_daytona_volume_file_text(
    volume_name: str,
    path: str,
    max_bytes: int = 200_000,
) -> dict[str, Any]:
    """Adapt Daytona sandbox.fs file downloads to the runtime preview schema."""
    if not path:
        raise ValueError("path is required")

    max_bytes = max(1, min(max_bytes, 1_000_000))
    resolved_path = _resolve_daytona_path(path)

    async with _amounted_daytona_volume(volume_name) as sandbox:
        raw = await _await_if_needed(
            sandbox.fs.download_file(str(resolved_path.mounted_path))
        )

    if raw is None:
        raw_bytes = b""
    elif isinstance(raw, str):
        raw_bytes = raw.encode("utf-8")
    else:
        raw_bytes = bytes(raw)

    size = len(raw_bytes)
    truncated = size > max_bytes
    preview_bytes = raw_bytes[:max_bytes] if truncated else raw_bytes

    mime = mimetypes.guess_type(resolved_path.display_path)[0] or "text/plain"

    return {
        "path": resolved_path.display_path,
        "mime": mime,
        "size": size,
        "content": preview_bytes.decode("utf-8", errors="replace"),
        "truncated": truncated,
    }


def read_daytona_volume_file_text(
    volume_name: str,
    path: str,
    max_bytes: int = 200_000,
) -> dict[str, Any]:
    return _run_async_compat(
        aread_daytona_volume_file_text,
        volume_name,
        path,
        max_bytes,
    )


__all__ = [
    "alist_daytona_volume_tree",
    "aread_daytona_volume_file_text",
    "list_daytona_volume_tree",
    "read_daytona_volume_file_text",
]
