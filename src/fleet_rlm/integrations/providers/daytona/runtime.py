"""Direct-SDK workspace bootstrap and session helpers for Daytona sandboxes."""

from __future__ import annotations

import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from .config import ResolvedDaytonaConfig, resolve_daytona_config
from .diagnostics import DaytonaDiagnosticError
from .types_context import ContextSource
from .runtime_helpers import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    _abuild_workspace_path,
    _aclone_repo,
    _aensure_daytona_volume_layout,
    _aensure_workspace_root,
    _aresolve_clone_ref,
    _astage_context_paths,
    _await_if_needed,
    _build_daytona_client,
    _daytona_import_error,
    _run_async_compat,
)


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

    async def aensure_context(self) -> Any:
        if self._context is not None:
            return self._context
        if self.context_id:
            existing_contexts = await _await_if_needed(
                self.sandbox.code_interpreter.list_contexts()
            )
            for existing in existing_contexts:
                if str(getattr(existing, "id", "") or "") == self.context_id:
                    self._context = existing
                    return existing
        context = await _await_if_needed(
            self.sandbox.code_interpreter.create_context(cwd=self.workspace_path)
        )
        self._context = context
        self.context_id = str(getattr(context, "id", "") or "") or None
        return context

    def ensure_context(self) -> Any:
        return _run_async_compat(self.aensure_context)

    async def astart_driver(self, *, timeout: float = 30.0) -> None:
        _ = timeout
        await self.aensure_context()
        self._driver_started = True

    def start_driver(self, *, timeout: float = 30.0) -> None:
        _run_async_compat(self.astart_driver, timeout=timeout)

    async def aclose_driver(self) -> None:
        self._driver_started = False

    def close_driver(self) -> None:
        _run_async_compat(self.aclose_driver)

    def _resolve_sandbox_path(self, path: str) -> str:
        candidate = PurePosixPath(str(path or "").strip() or ".")
        if candidate.is_absolute():
            return str(candidate)
        return str(PurePosixPath(self.workspace_path) / candidate)

    async def aread_file(self, path: str) -> str:
        raw = await _await_if_needed(
            self.sandbox.fs.download_file(self._resolve_sandbox_path(path))
        )
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        return bytes(raw).decode("utf-8", errors="replace")

    def read_file(self, path: str) -> str:
        return _run_async_compat(self.aread_file, path)

    async def awrite_file(self, path: str, content: str) -> str:
        resolved_path = self._resolve_sandbox_path(path)
        await _await_if_needed(
            self.sandbox.fs.upload_file(content.encode("utf-8"), resolved_path)
        )
        return resolved_path

    def write_file(self, path: str, content: str) -> str:
        return _run_async_compat(self.awrite_file, path, content)

    async def alist_files(self, path: str) -> list[Any]:
        entries = await _await_if_needed(
            self.sandbox.fs.list_files(self._resolve_sandbox_path(path))
        )
        return list(entries)

    def list_files(self, path: str) -> list[Any]:
        return _run_async_compat(self.alist_files, path)

    async def adelete(self) -> None:
        context = self._context
        self._context = None
        if context is None and self.context_id:
            with suppress(Exception):
                existing_contexts = await _await_if_needed(
                    self.sandbox.code_interpreter.list_contexts()
                )
                for existing in existing_contexts:
                    if str(getattr(existing, "id", "") or "") == self.context_id:
                        context = existing
                        break
        if context is not None:
            with suppress(Exception):
                await _await_if_needed(
                    self.sandbox.code_interpreter.delete_context(context)
                )
        self.context_id = None
        with suppress(Exception):
            await _await_if_needed(self.sandbox.delete())
        self._driver_started = False

    def delete(self) -> None:
        _run_async_compat(self.adelete)


class DaytonaSandboxRuntime:
    """Factory for Daytona sandboxes used by the pilot."""

    def __init__(self, *, config: ResolvedDaytonaConfig | None = None) -> None:
        resolved = config or resolve_daytona_config()
        self._resolved_config = resolved
        self._client: Any | None = _build_daytona_client(resolved)

    def _require_client(self) -> Any:
        client = self._client
        if client is None:
            raise RuntimeError("Daytona runtime client is closed")
        return client

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        close = getattr(client, "close", None)
        if close is None or not callable(close):
            return
        await _await_if_needed(close())

    def close(self) -> None:
        _run_async_compat(self.aclose)

    async def _acreate_volume_mounted_sandbox(self, volume_name: str) -> Any:
        try:
            from daytona import CreateSandboxFromSnapshotParams, VolumeMount
        except ImportError as exc:  # pragma: no cover - environment specific
            raise _daytona_import_error(exc) from exc

        client = self._require_client()
        volume = await _await_if_needed(client.volume.get(volume_name, create=True))
        return await _await_if_needed(
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

    async def _acreate_sandbox(self, volume_name: str | None = None) -> Any:
        try:
            if volume_name:
                return await self._acreate_volume_mounted_sandbox(volume_name)
            return await _await_if_needed(self._require_client().create())
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona sandbox create failure: {exc}",
                category="sandbox_create_clone_error",
                phase="sandbox_create",
            ) from exc

    async def _aget_sandbox(self, sandbox_id: str) -> Any:
        try:
            return await _await_if_needed(self._require_client().get(sandbox_id))
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
            if volume_name:
                await _aensure_daytona_volume_layout(
                    sandbox=sandbox,
                    mounted_root=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
                )

            workspace_path = await _abuild_workspace_path(sandbox, repo_url)
            resolved_ref = await _aresolve_clone_ref(repo_url, ref) if repo_url else ref
            if repo_url:
                clone_started = time.perf_counter()
                await _aclone_repo(
                    sandbox=sandbox,
                    repo_url=repo_url,
                    ref=resolved_ref,
                    workspace_path=workspace_path,
                )
                timings["repo_clone"] = int(
                    (time.perf_counter() - clone_started) * 1000
                )
            else:
                await _aensure_workspace_root(
                    sandbox=sandbox,
                    workspace_path=workspace_path,
                )

            context_started = time.perf_counter()
            context_sources = await _astage_context_paths(
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
                    await _await_if_needed(sandbox.delete())
            raise

    def create_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
        volume_name: str | None = None,
    ) -> DaytonaSandboxSession:
        return _run_async_compat(
            self.acreate_workspace_session,
            repo_url=repo_url,
            ref=ref,
            context_paths=context_paths,
            volume_name=volume_name,
        )

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
        resumed_started = time.perf_counter()
        sandbox = await self._aget_sandbox(sandbox_id)
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
        return _run_async_compat(
            self.aresume_workspace_session,
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
]
