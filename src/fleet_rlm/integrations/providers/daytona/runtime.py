"""Direct-SDK workspace bootstrap and session helpers for Daytona sandboxes."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from fleet_rlm.runtime.content.document_ingestion.main import read_document_content

from .config import ResolvedDaytonaConfig, resolve_daytona_config
from .diagnostics import DaytonaDiagnosticError
from .types_context import ContextSource

DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH = PurePosixPath("/home/daytona/memory")

_REMOTE_REF_RESOLUTION_TIMEOUT_S = 5
_REMOTE_DIRECTORY_MODE = "755"


def _daytona_import_error(exc: ImportError) -> RuntimeError:
    return RuntimeError(
        "Daytona SDK is not available. Install dependencies with `uv sync` "
        "and configure DAYTONA_API_KEY / DAYTONA_API_URL before using Daytona "
        "commands. See https://www.daytona.io/docs/en/python-sdk/"
    )


def _build_daytona_client(config: ResolvedDaytonaConfig) -> Any:
    try:
        from daytona import Daytona, DaytonaConfig
    except ImportError as exc:  # pragma: no cover - environment specific
        raise _daytona_import_error(exc) from exc
    return Daytona(
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


def _get_work_dir(sandbox: Any) -> str:
    if hasattr(sandbox, "get_work_dir"):
        return str(sandbox.get_work_dir())
    return "/workspace"


def _build_workspace_path(sandbox: Any, repo_url: str | None) -> str:
    work_dir = _get_work_dir(sandbox)
    workspace_name = _safe_workspace_name(repo_url)
    return str(PurePosixPath(work_dir) / "workspace" / workspace_name)


def _ensure_remote_directory(fs: Any, remote_path: PurePosixPath) -> None:
    directory = str(remote_path)
    if directory and directory not in {".", "/"}:
        fs.create_folder(directory, _REMOTE_DIRECTORY_MODE)


def _ensure_remote_parent(fs: Any, remote_path: PurePosixPath) -> None:
    _ensure_remote_directory(fs, remote_path.parent)


def _ensure_workspace_root(*, sandbox: Any, workspace_path: str) -> None:
    try:
        _ensure_remote_directory(sandbox.fs, PurePosixPath(workspace_path))
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona workspace create failure: {exc}",
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


def _clone_repo(
    *,
    sandbox: Any,
    repo_url: str,
    ref: str | None,
    workspace_path: str,
) -> None:
    try:
        _ensure_remote_directory(sandbox.fs, PurePosixPath(workspace_path).parent)
        sandbox.git.clone(
            **_build_clone_kwargs(
                repo_url=repo_url,
                ref=ref,
                workspace_path=workspace_path,
            )
        )
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"Daytona repo clone failure: {exc}",
            category="sandbox_create_clone_error",
            phase="repo_clone",
        ) from exc


def _upload_remote_text(fs: Any, remote_path: PurePosixPath, content: str) -> None:
    _ensure_remote_parent(fs, remote_path)
    fs.upload_file(content.encode("utf-8"), str(remote_path))


def _build_staged_filename(*, source_path: Path, source_type: str) -> str:
    return (
        source_path.name
        if source_type == "text"
        else f"{source_path.name}.extracted.txt"
    )


def _stage_local_file(
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
    _upload_remote_text(fs, staged_relative, text)
    return ContextSource(
        source_id=source_id,
        kind="file",
        host_path=str(resolved_path),
        staged_path=str(staged_relative),
        source_type=source_type,
        extraction_method=str(metadata.get("extraction_method") or "") or None,
        file_count=1,
    )


def _stage_local_directory(
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
        _upload_remote_text(fs, staged_relative, text)
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


def _stage_context_paths(
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
    _ensure_remote_directory(fs, context_root)
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
                    _stage_local_directory(
                        fs=fs,
                        resolved_path=resolved,
                        staged_root=staged_root,
                        source_id=source_id,
                    )
                )
            else:
                staged_sources.append(
                    _stage_local_file(
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
    _upload_remote_text(
        fs,
        manifest_path,
        json.dumps(
            {"context_sources": [item.to_dict() for item in staged_sources]},
            ensure_ascii=False,
            indent=2,
        ),
    )
    return staged_sources


@dataclass(slots=True)
class DaytonaSandboxSession:
    """Concrete Daytona workspace session backed by a sandbox and interpreter context."""

    sandbox: Any
    repo_url: str | None
    ref: str | None
    workspace_path: str
    context_sources: list[ContextSource] = field(default_factory=list)
    phase_timings_ms: dict[str, int] = field(default_factory=dict)
    volume_mount_path: str = str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
    context_id: str | None = None
    _context: Any | None = field(default=None, init=False, repr=False)
    _driver_started: bool = field(default=False, init=False, repr=False)

    @property
    def sandbox_id(self) -> str | None:
        return str(getattr(self.sandbox, "id", "") or "") or None

    def ensure_context(self) -> Any:
        if self._context is not None:
            return self._context
        if self.context_id:
            for existing in self.sandbox.code_interpreter.list_contexts():
                if str(getattr(existing, "id", "") or "") == self.context_id:
                    self._context = existing
                    return existing
        context = self.sandbox.code_interpreter.create_context(cwd=self.workspace_path)
        self._context = context
        self.context_id = str(getattr(context, "id", "") or "") or None
        return context

    async def aensure_context(self) -> Any:
        return await asyncio.to_thread(self.ensure_context)

    def start_driver(self, *, timeout: float = 30.0) -> None:
        _ = timeout
        self.ensure_context()
        self._driver_started = True

    async def astart_driver(self, *, timeout: float = 30.0) -> None:
        await asyncio.to_thread(self.start_driver, timeout=timeout)

    def close_driver(self) -> None:
        self._driver_started = False

    async def aclose_driver(self) -> None:
        await asyncio.to_thread(self.close_driver)

    def _resolve_sandbox_path(self, path: str) -> str:
        candidate = PurePosixPath(str(path or "").strip() or ".")
        if candidate.is_absolute():
            return str(candidate)
        return str(PurePosixPath(self.workspace_path) / candidate)

    def read_file(self, path: str) -> str:
        raw = self.sandbox.fs.download_file(self._resolve_sandbox_path(path))
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        return bytes(raw).decode("utf-8", errors="replace")

    async def aread_file(self, path: str) -> str:
        return await asyncio.to_thread(self.read_file, path)

    def write_file(self, path: str, content: str) -> str:
        resolved_path = self._resolve_sandbox_path(path)
        self.sandbox.fs.upload_file(content.encode("utf-8"), resolved_path)
        return resolved_path

    async def awrite_file(self, path: str, content: str) -> str:
        return await asyncio.to_thread(self.write_file, path, content)

    def list_files(self, path: str) -> list[Any]:
        return list(self.sandbox.fs.list_files(self._resolve_sandbox_path(path)))

    async def alist_files(self, path: str) -> list[Any]:
        return await asyncio.to_thread(self.list_files, path)

    def delete(self) -> None:
        context = self._context
        self._context = None
        if context is None and self.context_id:
            with suppress(Exception):
                for existing in self.sandbox.code_interpreter.list_contexts():
                    if str(getattr(existing, "id", "") or "") == self.context_id:
                        context = existing
                        break
        if context is not None:
            with suppress(Exception):
                self.sandbox.code_interpreter.delete_context(context)
        self.context_id = None
        with suppress(Exception):
            self.sandbox.delete()
        self._driver_started = False

    async def adelete(self) -> None:
        await asyncio.to_thread(self.delete)


class DaytonaSandboxRuntime:
    """Factory for Daytona sandboxes used by the pilot."""

    def __init__(self, *, config: ResolvedDaytonaConfig | None = None) -> None:
        resolved = config or resolve_daytona_config()
        self._resolved_config = resolved
        self._client = _build_daytona_client(resolved)

    def _create_volume_mounted_sandbox(self, volume_name: str) -> Any:
        try:
            from daytona import CreateSandboxFromSnapshotParams, VolumeMount
        except ImportError as exc:  # pragma: no cover - environment specific
            raise _daytona_import_error(exc) from exc

        volume = self._client.volume.get(volume_name, create=True)
        return self._client.create(
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

    def _create_sandbox(self, volume_name: str | None = None) -> Any:
        try:
            if volume_name:
                return self._create_volume_mounted_sandbox(volume_name)
            return self._client.create()
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona sandbox create failure: {exc}",
                category="sandbox_create_clone_error",
                phase="sandbox_create",
            ) from exc

    def _get_sandbox(self, sandbox_id: str) -> Any:
        try:
            return self._client.get(sandbox_id)
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona sandbox resume failure: {exc}",
                category="sandbox_resume_error",
                phase="sandbox_resume",
            ) from exc

    def _build_workspace_session(
        self,
        *,
        sandbox: Any,
        repo_url: str | None,
        resolved_ref: str | None,
        workspace_path: str,
        context_sources: list[ContextSource],
        timings: dict[str, int],
        context_id: str | None = None,
    ) -> DaytonaSandboxSession:
        session = DaytonaSandboxSession(
            sandbox=sandbox,
            repo_url=repo_url,
            ref=resolved_ref,
            workspace_path=workspace_path,
            context_sources=context_sources,
            volume_mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
            context_id=context_id,
        )
        session.phase_timings_ms.update(timings)
        return session

    def create_workspace_session(
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
            sandbox = self._create_sandbox(volume_name=volume_name)
            timings["sandbox_create"] = int(
                (time.perf_counter() - create_started) * 1000
            )

            workspace_path = _build_workspace_path(sandbox, repo_url)
            resolved_ref = _resolve_clone_ref(repo_url, ref) if repo_url else ref
            if repo_url:
                clone_started = time.perf_counter()
                _clone_repo(
                    sandbox=sandbox,
                    repo_url=repo_url,
                    ref=resolved_ref,
                    workspace_path=workspace_path,
                )
                timings["repo_clone"] = int(
                    (time.perf_counter() - clone_started) * 1000
                )
            else:
                _ensure_workspace_root(sandbox=sandbox, workspace_path=workspace_path)

            context_started = time.perf_counter()
            context_sources = _stage_context_paths(
                sandbox=sandbox,
                workspace_path=workspace_path,
                context_paths=context_paths,
            )
            timings["context_stage"] = int(
                (time.perf_counter() - context_started) * 1000
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
            if sandbox is not None:
                with suppress(Exception):
                    sandbox.delete()
            raise

    async def acreate_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
        volume_name: str | None = None,
    ) -> DaytonaSandboxSession:
        return await asyncio.to_thread(
            self.create_workspace_session,
            repo_url=repo_url,
            ref=ref,
            context_paths=context_paths,
            volume_name=volume_name,
        )

    def resume_workspace_session(
        self,
        *,
        sandbox_id: str,
        repo_url: str | None,
        ref: str | None,
        workspace_path: str,
        context_sources: list[ContextSource] | None = None,
        context_id: str | None = None,
    ) -> DaytonaSandboxSession:
        resumed_started = time.perf_counter()
        sandbox = self._get_sandbox(sandbox_id)
        session = self._build_workspace_session(
            sandbox=sandbox,
            repo_url=repo_url,
            resolved_ref=ref,
            workspace_path=workspace_path,
            context_sources=list(context_sources or []),
            timings={
                "sandbox_resume": int((time.perf_counter() - resumed_started) * 1000)
            },
            context_id=context_id,
        )
        return session

    async def aresume_workspace_session(
        self,
        *,
        sandbox_id: str,
        repo_url: str | None,
        ref: str | None,
        workspace_path: str,
        context_sources: list[ContextSource] | None = None,
        context_id: str | None = None,
    ) -> DaytonaSandboxSession:
        return await asyncio.to_thread(
            self.resume_workspace_session,
            sandbox_id=sandbox_id,
            repo_url=repo_url,
            ref=ref,
            workspace_path=workspace_path,
            context_sources=context_sources,
            context_id=context_id,
        )


__all__ = [
    "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH",
    "DaytonaSandboxRuntime",
    "DaytonaSandboxSession",
    "_resolve_clone_ref",
]
