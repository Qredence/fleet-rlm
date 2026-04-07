from __future__ import annotations

import asyncio
import threading
from pathlib import PurePosixPath
from typing import Any

import pytest


class FakeDaytonaSession:
    def __init__(self) -> None:
        self.context_sources: list[Any] = []
        self.workspace_path = "/workspace/session"
        self.sandbox_id = "sbx-root"
        self.context_id = "ctx-root"
        self.closed = 0
        self.deleted = 0
        self.driver_started = 0
        self.phase_timings_ms = {"sandbox_create": 2, "repo_clone": 4}
        self.owner_thread_id: int | None = None
        self.owner_loop_id: int | None = None

    def bind_current_async_owner(self) -> None:
        self.owner_thread_id = threading.get_ident()
        self.owner_loop_id = id(asyncio.get_running_loop())

    def matches_current_async_owner(self) -> bool:
        if self.owner_thread_id is None or self.owner_loop_id is None:
            return False
        try:
            return (
                self.owner_thread_id,
                self.owner_loop_id,
            ) == (threading.get_ident(), id(asyncio.get_running_loop()))
        except RuntimeError:
            return False

    async def astart_driver(self, *, timeout: float) -> None:
        _ = timeout
        self.driver_started += 1

    async def aclose_driver(self) -> None:
        self.closed += 1

    async def adelete(self) -> None:
        self.deleted += 1

    async def arefresh_activity(self) -> None:
        return None

    def start_driver(self, *, timeout: float = 30.0) -> None:
        _ = timeout
        self.driver_started += 1

    def delete(self) -> None:
        self.deleted += 1


class FakeDaytonaStorageSession:
    def __init__(self) -> None:
        self.read_calls: list[str] = []
        self.write_calls: list[tuple[str, str]] = []
        self.list_calls: list[str] = []
        self.file_contents: dict[str, str] = {}
        self.list_entries: dict[str, list[object]] = {}

    async def aread_file(self, path: str) -> str:
        self.read_calls.append(path)
        return self.file_contents[path]

    async def awrite_file(self, path: str, content: str) -> str:
        self.write_calls.append((path, content))
        self.file_contents[path] = content
        return path

    async def alist_files(self, path: str) -> list[object]:
        self.list_calls.append(path)
        return self.list_entries.get(path, [])


class FakeDaytonaWorkspaceSession:
    def __init__(self, workspace_path: str = "/workspace/repo") -> None:
        self.workspace_path = workspace_path
        self.files: dict[str, str] = {}
        self.read_calls: list[str] = []
        self.list_calls: list[str] = []

    def _resolve(self, path: str) -> str:
        candidate = PurePosixPath(path)
        if candidate.is_absolute():
            return str(candidate)
        return str(PurePosixPath(self.workspace_path) / candidate)

    def read_file(self, path: str) -> str:
        resolved = self._resolve(path)
        self.read_calls.append(resolved)
        if resolved not in self.files:
            raise FileNotFoundError(resolved)
        return self.files[resolved]

    def list_files(self, path: str) -> list[Any]:
        resolved = self._resolve(path)
        self.list_calls.append(resolved)
        prefix = resolved.rstrip("/") + "/"
        items: dict[str, bool] = {}
        for file_path in self.files:
            if not file_path.startswith(prefix):
                continue
            remainder = file_path[len(prefix) :]
            if not remainder:
                continue
            segment, _, tail = remainder.partition("/")
            items.setdefault(segment, bool(tail))
        if not items:
            raise FileNotFoundError(resolved)
        return [
            type("Entry", (), {"name": name, "is_dir": is_dir})()
            for name, is_dir in sorted(items.items())
        ]


class FakeDaytonaWorkspaceInterpreter:
    def __init__(self, session: FakeDaytonaWorkspaceSession) -> None:
        self._session = session

    def _ensure_session_sync(self) -> FakeDaytonaWorkspaceSession:
        return self._session


class FakeDaytonaRuntime:
    def __init__(self, session: FakeDaytonaSession | None = None) -> None:
        self.session = session or FakeDaytonaSession()
        self._resolved_config = object()
        self.create_calls: list[
            tuple[str | None, str | None, list[str], str | None]
        ] = []
        self.create_specs: list[object | None] = []
        self.resume_calls: list[tuple[str, str | None, str | None, str]] = []
        self.close_calls = 0

    async def acreate_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
        volume_name: str | None = None,
        spec: object | None = None,
    ) -> FakeDaytonaSession:
        self.create_calls.append(
            (repo_url, ref, list(context_paths or []), volume_name)
        )
        self.create_specs.append(spec)
        self.session.bind_current_async_owner()
        return self.session

    async def aresume_workspace_session(
        self,
        *,
        sandbox_id: str,
        repo_url: str | None,
        ref: str | None,
        workspace_path: str,
        context_sources=None,
        context_id: str | None = None,
    ) -> FakeDaytonaSession:
        _ = context_sources, context_id
        self.resume_calls.append((sandbox_id, repo_url, ref, workspace_path))
        self.session.bind_current_async_owner()
        return self.session

    async def aclose(self) -> None:
        self.close_calls += 1

    def create_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
        volume_name: str | None = None,
    ) -> FakeDaytonaSession:
        self.create_calls.append(
            (repo_url, ref, list(context_paths or []), volume_name)
        )
        return self.session

    def close(self) -> None:
        self.close_calls += 1


@pytest.fixture
def daytona_session() -> FakeDaytonaSession:
    return FakeDaytonaSession()


@pytest.fixture
def daytona_runtime(daytona_session: FakeDaytonaSession) -> FakeDaytonaRuntime:
    return FakeDaytonaRuntime(daytona_session)


__all__ = [
    "FakeDaytonaRuntime",
    "FakeDaytonaSession",
    "FakeDaytonaStorageSession",
    "FakeDaytonaWorkspaceInterpreter",
    "FakeDaytonaWorkspaceSession",
    "daytona_runtime",
    "daytona_session",
]
