"""Daytona Python SDK sandbox adapter for the experimental RLM pilot."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import subprocess
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
import logging
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar, cast
from collections.abc import Callable

try:
    from daytona import (
        AsyncDaytona,
        Daytona,
        DaytonaConfig,
        SessionExecuteRequest,
        VolumeMount,
        CreateSandboxFromSnapshotParams,
    )
except ImportError as exc:  # pragma: no cover - exercised by runtime users
    AsyncDaytona = None  # type: ignore[assignment]
    Daytona = None  # type: ignore[assignment]
    DaytonaConfig = None  # type: ignore[assignment]
    SessionExecuteRequest = None  # type: ignore[assignment]
    VolumeMount = None  # type: ignore[assignment]
    CreateSandboxFromSnapshotParams = None  # type: ignore[assignment]
    _DAYTONA_IMPORT_ERROR = exc
else:
    _DAYTONA_IMPORT_ERROR = None
from fleet_rlm.features.document_ingestion.main import read_document_content

from .config import ResolvedDaytonaConfig, resolve_daytona_config
from .diagnostics import DaytonaDiagnosticError
from .driver import DAYTONA_DRIVER_SOURCE
from .protocol import (
    DriverReady,
    ExecutionEventFrame,
    ExecutionRequest,
    ExecutionResponse,
    HostCallbackRequest,
    HostCallbackResponse,
    ShutdownAck,
    ShutdownRequest,
    decode_frame,
    encode_frame,
)
from .sdk import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    build_async_daytona_client,
    build_daytona_client as _build_daytona_client,
)
from .types import (
    ContextSource,
    DaytonaRunCancelled,
    PromptHandle,
    PromptManifest,
    PromptSliceRef,
)

_HOST_CALLBACK_REQUEST_TYPE = "host_callback_request"
_EXECUTION_EVENT_TYPE = "execute_event"
_EXECUTION_RESPONSE_TYPE = "execute_response"
_OUTPUT_ONLY_SUBMIT_SCHEMA = [{"name": "output", "type": "object"}]
_LOG_WAIT_INTERVAL_S = 0.1
_REMOTE_REF_RESOLUTION_TIMEOUT_S = 5
_T = TypeVar("_T")
build_daytona_client = _build_daytona_client


def _require_daytona_sdk() -> tuple[Any, Any, Any]:
    if (
        AsyncDaytona is None
        or Daytona is None
        or DaytonaConfig is None
        or SessionExecuteRequest is None
        or _DAYTONA_IMPORT_ERROR is not None
    ):
        raise RuntimeError(
            "Daytona SDK is not available. Install dependencies with `uv sync` "
            "and configure DAYTONA_API_KEY / DAYTONA_API_URL before using Daytona "
            "commands. See https://www.daytona.io/docs/en/python-sdk/"
        ) from _DAYTONA_IMPORT_ERROR
    return Daytona, DaytonaConfig, SessionExecuteRequest


async def _maybe_await(value: _T | Any) -> _T:
    if inspect.isawaitable(value):
        return await cast("Any", value)
    return cast(_T, value)


def _run_async_compat(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: list[Any] = []
    error: list[BaseException] = []

    def _runner() -> None:
        try:
            result.append(asyncio.run(awaitable))
        except BaseException as exc:  # pragma: no cover - thread boundary
            error.append(exc)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0] if result else None


@dataclass(slots=True)
class _SessionLogStreamState:
    """Mutable state for one long-running Daytona session log stream."""

    session_id: str
    command_id: str
    condition: threading.Condition = field(default_factory=threading.Condition)
    stdout_snapshot: str = ""
    stderr_snapshot: str = ""
    frame_buffer: str = ""
    stdout_offset: int = 0
    has_async_stream: bool = False
    stream_error: str | None = None
    closed: bool = False


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
        await _maybe_await(fs.create_folder(parent, "755"))


async def _upload_remote_text(
    fs: Any, remote_path: PurePosixPath, content: str
) -> None:
    await _ensure_remote_parent(fs, remote_path)
    await _maybe_await(fs.upload_file(content.encode("utf-8"), str(remote_path)))


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
    await _maybe_await(fs.create_folder(str(context_root), "755"))
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


class DaytonaSandboxSession:
    """A single Daytona workspace session backed by a persistent REPL driver."""

    def __init__(
        self,
        *,
        sandbox: Any,
        repo_url: str | None,
        ref: str | None,
        workspace_path: str,
        context_sources: list[ContextSource] | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.repo_url = repo_url or ""
        self.ref = ref
        self.workspace_path = workspace_path
        self.context_sources = list(context_sources or [])
        self._driver_session_id = f"fleet-rlm-{uuid.uuid4().hex}"
        self._driver_command_id: str | None = None
        self._driver_started = False
        self._stdout_offset = 0
        self._frame_buffer = ""
        self._driver_log_stream: _SessionLogStreamState | None = None
        self._driver_path = str(
            PurePosixPath(workspace_path) / ".fleet-rlm" / "driver.py"
        )
        self.phase_timings_ms: dict[str, int] = {}

    @property
    def sandbox_id(self) -> str | None:
        return getattr(self.sandbox, "id", None)

    async def _aprocess_exec(self, command: str, *, cwd: str | None = None) -> Any:
        return await _maybe_await(
            self.sandbox.process.exec(command, cwd=cwd or self.workspace_path)
        )

    async def _afs_download_file(self, remote_path: str) -> Any:
        return await _maybe_await(self.sandbox.fs.download_file(remote_path))

    async def _afs_create_folder(self, path: str, mode: str) -> None:
        await _maybe_await(self.sandbox.fs.create_folder(path, mode))

    async def _afs_upload_file(self, content: bytes, remote_path: str) -> None:
        await _maybe_await(self.sandbox.fs.upload_file(content, remote_path))

    async def _afs_list_files(self, remote_path: str) -> Any:
        return await _maybe_await(self.sandbox.fs.list_files(remote_path))

    async def _afs_search_files(self, remote_path: str, pattern: str) -> Any:
        return await _maybe_await(self.sandbox.fs.search_files(remote_path, pattern))

    async def _aprocess_create_session(self, session_id: str) -> None:
        await _maybe_await(self.sandbox.process.create_session(session_id))

    async def _aprocess_execute_session_command(
        self, session_id: str, request: Any, *, timeout: int | None = None
    ) -> Any:
        return await _maybe_await(
            self.sandbox.process.execute_session_command(
                session_id,
                request,
                timeout=timeout,
            )
        )

    async def _aprocess_send_session_command_input(
        self, session_id: str, command_id: str, data: str
    ) -> None:
        await _maybe_await(
            self.sandbox.process.send_session_command_input(
                session_id,
                command_id,
                data,
            )
        )

    async def _aprocess_get_session_command_logs(
        self, session_id: str, command_id: str
    ) -> Any:
        return await _maybe_await(
            self.sandbox.process.get_session_command_logs(session_id, command_id)
        )

    async def _aprocess_delete_session(self, session_id: str) -> None:
        await _maybe_await(self.sandbox.process.delete_session(session_id))

    async def _asandbox_delete(self) -> None:
        if hasattr(self.sandbox, "delete"):
            await _maybe_await(self.sandbox.delete())

    async def arun(self, command: str, *, cwd: str | None = None) -> dict[str, Any]:
        response = await self._aprocess_exec(command, cwd=cwd)
        artifacts = getattr(response, "artifacts", None)
        stdout = ""
        if artifacts is not None:
            stdout = str(getattr(artifacts, "stdout", "") or "")
        if not stdout:
            stdout = str(getattr(response, "result", "") or "")
        return {
            "exit_code": int(getattr(response, "exit_code", 0) or 0),
            "stdout": stdout,
            "stderr": "",
            "ok": int(getattr(response, "exit_code", 0) or 0) == 0,
        }

    def run(self, command: str, *, cwd: str | None = None) -> dict[str, Any]:
        return _run_async_compat(self.arun(command, cwd=cwd))

    async def aread_file(self, path: str) -> str:
        remote_path = str(self._resolve_path(path))
        raw = await self._afs_download_file(remote_path)
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    def read_file(self, path: str) -> str:
        return _run_async_compat(self.aread_file(path))

    async def awrite_file(self, path: str, content: str) -> str:
        remote_path = str(self._resolve_path(path))
        parent = str(PurePosixPath(remote_path).parent)
        if parent and parent not in {".", "/"}:
            await self._afs_create_folder(parent, "755")
        await self._afs_upload_file(content.encode("utf-8"), remote_path)
        return remote_path

    def write_file(self, path: str, content: str) -> str:
        return _run_async_compat(self.awrite_file(path, content))

    async def alist_files(self, path: str = ".") -> list[str]:
        remote_path = str(self._resolve_path(path))
        files = await self._afs_list_files(remote_path)
        results: list[str] = []
        for item in files:
            name = getattr(item, "name", None)
            if name:
                results.append(str(PurePosixPath(remote_path) / str(name)))
        return sorted(results)

    def list_files(self, path: str = ".") -> list[str]:
        return _run_async_compat(self.alist_files(path))

    async def afind_files(self, path: str = ".", pattern: str = "*") -> list[str]:
        remote_path = str(self._resolve_path(path))
        response = await self._afs_search_files(remote_path, pattern)
        files = getattr(response, "files", []) or []
        return [str(item) for item in files]

    def find_files(self, path: str = ".", pattern: str = "*") -> list[str]:
        return _run_async_compat(self.afind_files(path, pattern))

    async def astart_driver(self, *, timeout: float = 30.0) -> None:
        """Start the persistent sandbox-side driver once per sandbox."""

        if self._driver_started:
            return

        started = time.perf_counter()
        try:
            await self.awrite_file(".fleet-rlm/driver.py", DAYTONA_DRIVER_SOURCE)
            await self._aprocess_create_session(self._driver_session_id)
            request = SessionExecuteRequest(
                command=f"python -u {self._driver_path} {self.workspace_path}",
                run_async=True,
                suppress_input_echo=True,
            )
            response = await self._aprocess_execute_session_command(
                self._driver_session_id,
                request,
                timeout=int(timeout) if timeout > 0 else None,
            )
            self._driver_command_id = str(response.cmd_id)
            self._driver_started = True
            self._stdout_offset = 0
            self._frame_buffer = ""
            self._driver_log_stream = self._open_log_stream(
                session_id=self._driver_session_id,
                command_id=self._driver_command_id,
            )
            await self._aread_until(
                predicate=lambda frame: frame.get("type") == DriverReady().type,
                timeout=timeout,
            )
        except Exception as exc:
            self._driver_command_id = None
            self._driver_started = False
            self._stdout_offset = 0
            self._frame_buffer = ""
            self._close_log_stream(self._driver_log_stream)
            self._driver_log_stream = None
            raise DaytonaDiagnosticError(
                f"Daytona driver handshake failure: {exc}",
                category="driver_handshake_error",
                phase="driver_start",
            ) from exc
        finally:
            self.phase_timings_ms["driver_start"] = int(
                (time.perf_counter() - started) * 1000
            )

    def start_driver(self, *, timeout: float = 30.0) -> None:
        _run_async_compat(self.astart_driver(timeout=timeout))

    async def aexecute_code(
        self,
        *,
        code: str,
        variables: dict[str, Any] | None = None,
        tool_names: list[str] | None = None,
        callback_handler: Callable[[HostCallbackRequest], HostCallbackResponse],
        timeout: float,
        submit_schema: list[dict[str, Any]] | None = None,
        execution_profile: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
        progress_handler: Callable[[ExecutionEventFrame], None] | None = None,
    ) -> ExecutionResponse:
        """Execute one code block through the persistent sandbox-side driver."""

        await self.astart_driver(timeout=timeout)
        execute_started = time.perf_counter()
        request = ExecutionRequest(
            request_id=uuid.uuid4().hex,
            code=code,
            variables=variables or None,
            tool_names=list(tool_names or []) or None,
            submit_schema=submit_schema,
            execution_profile=execution_profile,
        )
        await self._asend_frame(request.to_dict())

        while True:
            frame = await self._aread_until(
                predicate=lambda payload: (
                    payload.get("type")
                    in {
                        _HOST_CALLBACK_REQUEST_TYPE,
                        _EXECUTION_EVENT_TYPE,
                        _EXECUTION_RESPONSE_TYPE,
                    }
                ),
                timeout=timeout,
                cancel_check=cancel_check,
            )
            if frame.get("type") == _HOST_CALLBACK_REQUEST_TYPE:
                callback_request = HostCallbackRequest.from_dict(frame)
                callback_response = callback_handler(callback_request)
                await self._asend_frame(callback_response.to_dict())
                continue
            if frame.get("type") == _EXECUTION_EVENT_TYPE:
                if (
                    progress_handler is not None
                    and frame.get("request_id") == request.request_id
                ):
                    progress_handler(ExecutionEventFrame.from_dict(frame))
                continue
            if frame.get("request_id") != request.request_id:
                continue
            response = ExecutionResponse.from_dict(frame)
            self.phase_timings_ms.setdefault(
                "first_execute_response",
                int((time.perf_counter() - execute_started) * 1000),
            )
            return response

    def execute_code(
        self,
        *,
        code: str,
        variables: dict[str, Any] | None = None,
        tool_names: list[str] | None = None,
        callback_handler: Callable[[HostCallbackRequest], HostCallbackResponse],
        timeout: float,
        submit_schema: list[dict[str, Any]] | None = None,
        execution_profile: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
        progress_handler: Callable[[ExecutionEventFrame], None] | None = None,
    ) -> ExecutionResponse:
        return _run_async_compat(
            self.aexecute_code(
                code=code,
                variables=variables,
                tool_names=tool_names,
                callback_handler=callback_handler,
                timeout=timeout,
                submit_schema=submit_schema,
                execution_profile=execution_profile,
                cancel_check=cancel_check,
                progress_handler=progress_handler,
            )
        )

    async def aclose_driver(self, *, timeout: float = 5.0) -> None:
        """Gracefully stop the persistent sandbox-side driver."""

        if not self._driver_started or self._driver_command_id is None:
            return

        try:
            await self._asend_frame(ShutdownRequest().to_dict())
            await self._aread_until(
                predicate=lambda frame: frame.get("type") == ShutdownAck().type,
                timeout=timeout,
            )
        except Exception as exc:
            # Best-effort graceful shutdown: failures to send or receive shutdown
            # messages are non-fatal because we always perform forced cleanup
            # in the finally block below.
            logging.debug(
                "Error during Daytona sandbox driver shutdown; proceeding with "
                "forced cleanup: %s",
                exc,
            )
        finally:
            try:
                await self._aprocess_delete_session(self._driver_session_id)
            finally:
                self._close_log_stream(self._driver_log_stream)
                self._driver_log_stream = None
                self._driver_command_id = None
                self._driver_started = False
                self._stdout_offset = 0
                self._frame_buffer = ""

    def close_driver(self, *, timeout: float = 5.0) -> None:
        _run_async_compat(self.aclose_driver(timeout=timeout))

    async def areset_for_new_call(self, *, timeout: float = 5.0) -> None:
        """Reset REPL state while preserving the staged Daytona workspace."""

        await self.aclose_driver(timeout=timeout)
        try:
            await self.arun("rm -rf .fleet-rlm/prompts", cwd=self.workspace_path)
        except Exception as exc:
            # Best-effort cleanup: failures to remove prompt cache are non-fatal.
            logging.debug(
                "Failed to remove .fleet-rlm/prompts during Daytona sandbox reset: %s",
                exc,
            )

    def reset_for_new_call(self, *, timeout: float = 5.0) -> None:
        _run_async_compat(self.areset_for_new_call(timeout=timeout))

    async def adelete(self) -> None:
        await self.aclose_driver()
        await self._asandbox_delete()

    def delete(self) -> None:
        _run_async_compat(self.adelete())

    async def astore_prompt(
        self,
        *,
        text: str,
        kind: str = "manual",
        label: str | None = None,
        timeout: float = 30.0,
    ) -> PromptHandle:
        payload = await self._arun_driver_helper(
            code=(
                f"handle = store_prompt({text!r}, kind={kind!r}, label={label!r})\n"
                "SUBMIT(output=handle)"
            ),
            timeout=timeout,
        )
        return PromptHandle.from_raw(payload)

    def store_prompt(
        self,
        *,
        text: str,
        kind: str = "manual",
        label: str | None = None,
        timeout: float = 30.0,
    ) -> PromptHandle:
        return _run_async_compat(
            self.astore_prompt(text=text, kind=kind, label=label, timeout=timeout)
        )

    async def alist_prompts(self, *, timeout: float = 30.0) -> PromptManifest:
        payload = await self._arun_driver_helper(
            code="manifest = list_prompts()\nSUBMIT(output=manifest)",
            timeout=timeout,
        )
        return PromptManifest.from_raw(payload)

    def list_prompts(self, *, timeout: float = 30.0) -> PromptManifest:
        return _run_async_compat(self.alist_prompts(timeout=timeout))

    async def aread_prompt_slice(
        self,
        *,
        handle_id: str,
        start_line: int = 1,
        num_lines: int = 120,
        start_char: int | None = None,
        char_count: int | None = None,
        timeout: float = 30.0,
    ) -> tuple[PromptSliceRef, str]:
        payload = await self._arun_driver_helper(
            code=(
                "slice_result = read_prompt_slice("
                f"{handle_id!r}, "
                f"start_line={start_line}, "
                f"num_lines={num_lines}, "
                f"start_char={start_char!r}, "
                f"char_count={char_count!r})\n"
                "SUBMIT(output=slice_result)"
            ),
            timeout=timeout,
        )
        slice_ref = PromptSliceRef.from_raw(payload)
        return slice_ref, str(payload.get("text", "") or "")

    def read_prompt_slice(
        self,
        *,
        handle_id: str,
        start_line: int = 1,
        num_lines: int = 120,
        start_char: int | None = None,
        char_count: int | None = None,
        timeout: float = 30.0,
    ) -> tuple[PromptSliceRef, str]:
        return _run_async_compat(
            self.aread_prompt_slice(
                handle_id=handle_id,
                start_line=start_line,
                num_lines=num_lines,
                start_char=start_char,
                char_count=char_count,
                timeout=timeout,
            )
        )

    async def _arun_driver_helper(self, *, code: str, timeout: float) -> dict[str, Any]:
        def _unexpected_callback(request: HostCallbackRequest) -> HostCallbackResponse:
            raise RuntimeError(
                f"Prompt helper execution does not expect host callbacks: {request.name}"
            )

        response = await self.aexecute_code(
            code=code,
            callback_handler=_unexpected_callback,
            timeout=timeout,
            submit_schema=_OUTPUT_ONLY_SUBMIT_SCHEMA,
        )
        if response.error:
            raise RuntimeError(response.error)
        if response.final_artifact is None:
            raise RuntimeError("Prompt helper did not produce a final artifact.")
        payload = response.final_artifact.get("value")
        if isinstance(payload, dict) and "output" in payload:
            payload = payload.get("output")
        if not isinstance(payload, dict):
            raise RuntimeError("Prompt helper returned an invalid payload.")
        return payload

    async def _asend_frame(self, payload: dict[str, Any]) -> None:
        if self._driver_command_id is None:
            raise RuntimeError("Sandbox driver is not running")
        await self._aprocess_send_session_command_input(
            self._driver_session_id,
            self._driver_command_id,
            encode_frame(payload) + "\n",
        )

    def _send_frame(self, payload: dict[str, Any]) -> None:
        _run_async_compat(self._asend_frame(payload))

    async def _aread_until(
        self,
        *,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel_check is not None and cancel_check():
                await self.aclose_driver(timeout=1.0)
                raise DaytonaRunCancelled("Request cancelled.")
            for frame in await self._adrain_frames():
                if predicate(frame):
                    return frame
            await self._await_logs(self._driver_log_stream, deadline=deadline)

        stderr = (await self._alog_stderr(self._driver_log_stream)).strip()
        suffix = f" Driver stderr: {stderr}" if stderr else ""
        raise TimeoutError(f"Timed out waiting for sandbox driver response.{suffix}")

    def _read_until(
        self,
        *,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        return _run_async_compat(
            self._aread_until(
                predicate=predicate,
                timeout=timeout,
                cancel_check=cancel_check,
            )
        )

    async def _adrain_frames(self) -> list[dict[str, Any]]:
        return await self._adrain_log_frames(self._driver_log_stream)

    def _drain_frames(self) -> list[dict[str, Any]]:
        return _run_async_compat(self._adrain_frames())

    def _open_log_stream(
        self, *, session_id: str, command_id: str
    ) -> _SessionLogStreamState:
        state = _SessionLogStreamState(session_id=session_id, command_id=command_id)
        stream_logs = getattr(
            self.sandbox.process, "get_session_command_logs_async", None
        )
        if not callable(stream_logs):
            return state

        state.has_async_stream = True

        def _stream_logs() -> None:
            try:
                result = stream_logs(
                    session_id,
                    command_id,
                    lambda chunk: self._append_log_stdout(state, chunk),
                    lambda chunk: self._append_log_stderr(state, chunk),
                )
                if asyncio.iscoroutine(result):
                    asyncio.run(result)
            except Exception as exc:
                with state.condition:
                    if not state.closed:
                        state.has_async_stream = False
                        state.stream_error = str(exc)
                    state.condition.notify_all()

        threading.Thread(target=_stream_logs, daemon=True).start()
        return state

    @staticmethod
    def _close_log_stream(state: _SessionLogStreamState | None) -> None:
        if state is None:
            return
        with state.condition:
            state.closed = True
            state.condition.notify_all()

    def _append_log_stdout(
        self, state: _SessionLogStreamState, chunk: str | bytes | None
    ) -> None:
        text = (
            chunk.decode("utf-8", errors="replace")
            if isinstance(chunk, bytes)
            else str(chunk or "")
        )
        if not text:
            return
        with state.condition:
            state.stdout_snapshot += text
            state.frame_buffer += text
            state.condition.notify_all()

    def _append_log_stderr(
        self, state: _SessionLogStreamState, chunk: str | bytes | None
    ) -> None:
        text = (
            chunk.decode("utf-8", errors="replace")
            if isinstance(chunk, bytes)
            else str(chunk or "")
        )
        if not text:
            return
        with state.condition:
            state.stderr_snapshot += text
            state.condition.notify_all()

    def _wait_for_logs(
        self, state: _SessionLogStreamState | None, *, deadline: float
    ) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if state is None:
            time.sleep(min(_LOG_WAIT_INTERVAL_S, remaining))
            return
        with state.condition:
            state.condition.wait(timeout=min(_LOG_WAIT_INTERVAL_S, remaining))

    async def _await_logs(
        self, state: _SessionLogStreamState | None, *, deadline: float
    ) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(min(_LOG_WAIT_INTERVAL_S, remaining))

    async def _alog_stderr(self, state: _SessionLogStreamState | None) -> str:
        if state is None:
            return ""
        with state.condition:
            stderr = state.stderr_snapshot
        if stderr:
            return stderr
        await self._arefresh_log_snapshot(state)
        with state.condition:
            return state.stderr_snapshot

    def _log_stderr(self, state: _SessionLogStreamState | None) -> str:
        return _run_async_compat(self._alog_stderr(state))

    async def _adrain_log_frames(
        self, state: _SessionLogStreamState | None
    ) -> list[dict[str, Any]]:
        if state is None:
            return []
        if not state.has_async_stream:
            await self._arefresh_log_snapshot(state)
        with state.condition:
            return self._decode_log_frames_locked(state)

    def _drain_log_frames(
        self, state: _SessionLogStreamState | None
    ) -> list[dict[str, Any]]:
        return _run_async_compat(self._adrain_log_frames(state))

    async def _arefresh_log_snapshot(self, state: _SessionLogStreamState) -> None:
        logs = await self._aprocess_get_session_command_logs(
            state.session_id,
            state.command_id,
        )
        stdout = str(getattr(logs, "stdout", "") or "")
        stderr = str(getattr(logs, "stderr", "") or "")
        with state.condition:
            if len(stdout) >= state.stdout_offset:
                new_text = stdout[state.stdout_offset :]
            else:
                new_text = stdout
            state.stdout_offset = len(stdout)
            if new_text:
                state.stdout_snapshot = stdout
                state.frame_buffer += new_text
            else:
                state.stdout_snapshot = stdout
            state.stderr_snapshot = stderr

    def _refresh_log_snapshot(self, state: _SessionLogStreamState) -> None:
        _run_async_compat(self._arefresh_log_snapshot(state))

    @staticmethod
    def _decode_log_frames_locked(
        state: _SessionLogStreamState,
    ) -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = []
        while "\n" in state.frame_buffer:
            line, state.frame_buffer = state.frame_buffer.split("\n", 1)
            decoded = decode_frame(line.strip())
            if decoded is not None:
                frames.append(decoded)
        return frames

    def _resolve_path(self, path: str) -> PurePosixPath:
        candidate = PurePosixPath(path)
        if candidate.is_absolute():
            return candidate
        return PurePosixPath(self.workspace_path) / candidate


class DaytonaSandboxRuntime:
    """Factory for Daytona sandboxes used by the pilot."""

    def __init__(self, *, config: ResolvedDaytonaConfig | None = None) -> None:
        _require_daytona_sdk()
        resolved = config or resolve_daytona_config()
        self._resolved_config = resolved
        self._client = build_async_daytona_client(
            config=resolved if config is not None else None
        )

    async def _acreate_sandbox(self, volume_name: str | None = None) -> Any:
        try:
            if volume_name:
                volume = await _maybe_await(
                    self._client.volume.get(volume_name, create=True)
                )
                return await _maybe_await(
                    self._client.create(
                        CreateSandboxFromSnapshotParams(
                            volumes=[
                                VolumeMount(
                                    volume_id=volume.id,
                                    mount_path=str(
                                        DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH
                                    ),
                                )
                            ]
                        )
                    )
                )
            return await _maybe_await(self._client.create())
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona sandbox create failure: {exc}",
                category="sandbox_create_clone_error",
                phase="sandbox_create",
            ) from exc

    def _create_sandbox(self, volume_name: str | None = None) -> Any:
        return _run_async_compat(self._acreate_sandbox(volume_name=volume_name))

    async def _aget_sandbox(self, sandbox_id: str) -> Any:
        try:
            return await _maybe_await(self._client.get(sandbox_id))
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona sandbox resume failure: {exc}",
                category="sandbox_resume_error",
                phase="sandbox_resume",
            ) from exc

    def _get_sandbox(self, sandbox_id: str) -> Any:
        return _run_async_compat(self._aget_sandbox(sandbox_id))

    async def _abuild_workspace_path(self, sandbox: Any, repo_url: str | None) -> str:
        work_dir = (
            await _maybe_await(sandbox.get_work_dir())
            if hasattr(sandbox, "get_work_dir")
            else "/workspace"
        )
        workspace_name = _safe_workspace_name(repo_url)
        return str(PurePosixPath(work_dir) / "workspace" / workspace_name)

    def _build_workspace_path(self, sandbox: Any, repo_url: str | None) -> str:
        return _run_async_compat(self._abuild_workspace_path(sandbox, repo_url))

    async def _aensure_workspace_root(
        self, *, sandbox: Any, workspace_path: str
    ) -> None:
        try:
            await _maybe_await(
                sandbox.fs.create_folder(str(PurePosixPath(workspace_path)), "755")
            )
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona workspace create failure: {exc}",
                category="sandbox_create_clone_error",
                phase="sandbox_create",
            ) from exc

    def _ensure_workspace_root(self, *, sandbox: Any, workspace_path: str) -> None:
        _run_async_compat(
            self._aensure_workspace_root(sandbox=sandbox, workspace_path=workspace_path)
        )

    async def _aclone_repo(
        self, *, sandbox: Any, repo_url: str, ref: str | None, workspace_path: str
    ) -> None:
        try:
            work_dir = (
                await _maybe_await(sandbox.get_work_dir())
                if hasattr(sandbox, "get_work_dir")
                else "/workspace"
            )
            await _maybe_await(
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
            await _maybe_await(sandbox.git.clone(**clone_kwargs))
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona repo clone failure: {exc}",
                category="sandbox_create_clone_error",
                phase="repo_clone",
            ) from exc

    def _clone_repo(
        self, *, sandbox: Any, repo_url: str, ref: str | None, workspace_path: str
    ) -> None:
        _run_async_compat(
            self._aclone_repo(
                sandbox=sandbox,
                repo_url=repo_url,
                ref=ref,
                workspace_path=workspace_path,
            )
        )

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
            if not repo_url:
                await self._aensure_workspace_root(
                    sandbox=sandbox, workspace_path=workspace_path
                )

            resolved_ref = _resolve_clone_ref(repo_url, ref) if repo_url else ref
            if repo_url:
                clone_started = time.perf_counter()
                await self._aclone_repo(
                    sandbox=sandbox,
                    repo_url=repo_url,
                    ref=resolved_ref,
                    workspace_path=workspace_path,
                )
                timings["repo_clone"] = int(
                    (time.perf_counter() - clone_started) * 1000
                )

            context_started = time.perf_counter()
            context_sources = await _stage_context_paths(
                sandbox=sandbox,
                workspace_path=workspace_path,
                context_paths=context_paths,
            )
            timings["context_stage"] = int(
                (time.perf_counter() - context_started) * 1000
            )

            session = DaytonaSandboxSession(
                sandbox=sandbox,
                repo_url=repo_url,
                ref=resolved_ref,
                workspace_path=workspace_path,
                context_sources=context_sources,
            )
            session.phase_timings_ms.update(timings)
            return session
        except Exception:
            if sandbox is not None and hasattr(sandbox, "delete"):
                with suppress(Exception):
                    await _maybe_await(sandbox.delete())
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
        return _run_async_compat(
            self.aresume_workspace_session(
                sandbox_id=sandbox_id,
                repo_url=repo_url,
                ref=ref,
                workspace_path=workspace_path,
                context_sources=context_sources,
            )
        )
