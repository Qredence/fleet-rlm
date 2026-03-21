"""Stateful Daytona workspace sessions backed by the custom REPL bridge."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from . import sdk as support
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
from ..diagnostics import DaytonaDiagnosticError
from ..types import (
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
    mode: str = "polling"
    has_async_stream: bool = False
    task: asyncio.Task[None] | None = None
    owning_loop: asyncio.AbstractEventLoop | None = None
    stream_error: str | None = None
    closed: bool = False


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
        self._driver_log_stream: _SessionLogStreamState | None = None
        self._driver_path = str(
            PurePosixPath(workspace_path) / ".fleet-rlm" / "driver.py"
        )
        self.phase_timings_ms: dict[str, int] = {}

    @property
    def sandbox_id(self) -> str | None:
        return getattr(self.sandbox, "id", None)

    async def _aprocess_exec(self, command: str, *, cwd: str | None = None) -> Any:
        return await support._maybe_await(
            self.sandbox.process.exec(command, cwd=cwd or self.workspace_path)
        )

    async def _afs_download_file(self, remote_path: str) -> Any:
        return await support._maybe_await(self.sandbox.fs.download_file(remote_path))

    async def _afs_create_folder(self, path: str, mode: str) -> None:
        await support._maybe_await(self.sandbox.fs.create_folder(path, mode))

    async def _afs_upload_file(self, content: bytes, remote_path: str) -> None:
        await support._maybe_await(self.sandbox.fs.upload_file(content, remote_path))

    async def _afs_list_files(self, remote_path: str) -> Any:
        return await support._maybe_await(self.sandbox.fs.list_files(remote_path))

    async def _afs_search_files(self, remote_path: str, pattern: str) -> Any:
        return await support._maybe_await(
            self.sandbox.fs.search_files(remote_path, pattern)
        )

    async def _aprocess_create_session(self, session_id: str) -> None:
        await support._maybe_await(self.sandbox.process.create_session(session_id))

    async def _aprocess_execute_session_command(
        self, session_id: str, request: Any, *, timeout: int | None = None
    ) -> Any:
        return await support._maybe_await(
            self.sandbox.process.execute_session_command(
                session_id,
                request,
                timeout=timeout,
            )
        )

    async def _aprocess_send_session_command_input(
        self, session_id: str, command_id: str, data: str
    ) -> None:
        await support._maybe_await(
            self.sandbox.process.send_session_command_input(
                session_id,
                command_id,
                data,
            )
        )

    async def _aprocess_get_session_command_logs(
        self, session_id: str, command_id: str
    ) -> Any:
        return await support._maybe_await(
            self.sandbox.process.get_session_command_logs(session_id, command_id)
        )

    async def _aprocess_delete_session(self, session_id: str) -> None:
        await support._maybe_await(self.sandbox.process.delete_session(session_id))

    async def _asandbox_delete(self) -> None:
        if hasattr(self.sandbox, "delete"):
            await support._maybe_await(self.sandbox.delete())

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
        return support._run_async_compat(self.arun(command, cwd=cwd))

    async def aread_file(self, path: str) -> str:
        remote_path = str(self._resolve_path(path))
        raw = await self._afs_download_file(remote_path)
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    def read_file(self, path: str) -> str:
        return support._run_async_compat(self.aread_file(path))

    async def awrite_file(self, path: str, content: str) -> str:
        remote_path = str(self._resolve_path(path))
        parent = str(PurePosixPath(remote_path).parent)
        if parent and parent not in {".", "/"}:
            await self._afs_create_folder(parent, "755")
        await self._afs_upload_file(content.encode("utf-8"), remote_path)
        return remote_path

    def write_file(self, path: str, content: str) -> str:
        return support._run_async_compat(self.awrite_file(path, content))

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
        return support._run_async_compat(self.alist_files(path))

    async def afind_files(self, path: str = ".", pattern: str = "*") -> list[str]:
        remote_path = str(self._resolve_path(path))
        response = await self._afs_search_files(remote_path, pattern)
        files = getattr(response, "files", []) or []
        return [str(item) for item in files]

    def find_files(self, path: str = ".", pattern: str = "*") -> list[str]:
        return support._run_async_compat(self.afind_files(path, pattern))

    def _reset_driver_state(self) -> None:
        self._driver_command_id = None
        self._driver_started = False

    def _driver_request(self) -> Any:
        return support.SessionExecuteRequest(
            command=f"python -u {self._driver_path} {self.workspace_path}",
            run_async=True,
            suppress_input_echo=True,
        )

    def _reuse_existing_driver_log_stream(
        self, *, prefer_async_log_stream: bool
    ) -> bool:
        if not self._driver_started:
            return False
        if self._driver_command_id is not None:
            self._driver_log_stream = self._open_log_stream(
                session_id=self._driver_session_id,
                command_id=self._driver_command_id,
                prefer_async_log_stream=prefer_async_log_stream,
                existing=self._driver_log_stream,
            )
        return True

    async def _astart_driver_process(self, *, prefer_async_log_stream: bool) -> None:
        await self.awrite_file(".fleet-rlm/driver.py", DAYTONA_DRIVER_SOURCE)
        await self._aprocess_create_session(self._driver_session_id)
        response = await self._aprocess_execute_session_command(
            self._driver_session_id,
            self._driver_request(),
            timeout=None,
        )
        self._driver_command_id = str(response.cmd_id)
        self._driver_started = True
        self._driver_log_stream = self._open_log_stream(
            session_id=self._driver_session_id,
            command_id=self._driver_command_id,
            prefer_async_log_stream=prefer_async_log_stream,
        )

    async def _await_driver_ready(self, *, timeout: float) -> None:
        await self._aread_until(
            predicate=lambda frame: frame.get("type") == DriverReady().type,
            timeout=timeout,
        )

    async def _cleanup_failed_driver_start(self) -> None:
        self._reset_driver_state()
        await self._aclose_log_stream(self._driver_log_stream)
        self._driver_log_stream = None

    async def astart_driver(
        self,
        *,
        timeout: float = 30.0,
        prefer_async_log_stream: bool = True,
    ) -> None:
        """Start the persistent sandbox-side driver once per sandbox."""

        if self._reuse_existing_driver_log_stream(
            prefer_async_log_stream=prefer_async_log_stream
        ):
            return

        started = time.perf_counter()
        try:
            await self._astart_driver_process(
                prefer_async_log_stream=prefer_async_log_stream
            )
            await self._await_driver_ready(timeout=timeout)
        except Exception as exc:
            await self._cleanup_failed_driver_start()
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
        support._run_async_compat(
            self.astart_driver(timeout=timeout, prefer_async_log_stream=False)
        )

    def _build_execution_request(
        self,
        *,
        code: str,
        variables: dict[str, Any] | None,
        tool_names: list[str] | None,
        submit_schema: list[dict[str, Any]] | None,
        execution_profile: str | None,
    ) -> ExecutionRequest:
        return ExecutionRequest(
            request_id=uuid.uuid4().hex,
            code=code,
            variables=variables or None,
            tool_names=list(tool_names or []) or None,
            submit_schema=submit_schema,
            execution_profile=execution_profile,
        )

    async def _handle_execution_frame(
        self,
        *,
        frame: dict[str, Any],
        request: ExecutionRequest,
        callback_handler: Callable[[HostCallbackRequest], HostCallbackResponse],
        progress_handler: Callable[[ExecutionEventFrame], None] | None,
    ) -> ExecutionResponse | None:
        if frame.get("type") == _HOST_CALLBACK_REQUEST_TYPE:
            callback_request = HostCallbackRequest.from_dict(frame)
            callback_response = callback_handler(callback_request)
            await self._asend_frame(callback_response.to_dict())
            return None
        if frame.get("type") == _EXECUTION_EVENT_TYPE:
            if (
                progress_handler is not None
                and frame.get("request_id") == request.request_id
            ):
                progress_handler(ExecutionEventFrame.from_dict(frame))
            return None
        if frame.get("request_id") != request.request_id:
            return None
        return ExecutionResponse.from_dict(frame)

    async def _await_execution_response(
        self,
        *,
        request: ExecutionRequest,
        callback_handler: Callable[[HostCallbackRequest], HostCallbackResponse],
        timeout: float,
        cancel_check: Callable[[], bool] | None,
        progress_handler: Callable[[ExecutionEventFrame], None] | None,
        execute_started: float,
    ) -> ExecutionResponse:
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
            response = await self._handle_execution_frame(
                frame=frame,
                request=request,
                callback_handler=callback_handler,
                progress_handler=progress_handler,
            )
            if response is None:
                continue
            self.phase_timings_ms.setdefault(
                "first_execute_response",
                int((time.perf_counter() - execute_started) * 1000),
            )
            return response

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
        prefer_async_log_stream: bool = True,
    ) -> ExecutionResponse:
        """Execute one code block through the persistent sandbox-side driver."""

        await self.astart_driver(
            timeout=timeout,
            prefer_async_log_stream=prefer_async_log_stream,
        )
        execute_started = time.perf_counter()
        request = self._build_execution_request(
            code=code,
            variables=variables,
            tool_names=tool_names,
            submit_schema=submit_schema,
            execution_profile=execution_profile,
        )
        await self._asend_frame(request.to_dict())
        return await self._await_execution_response(
            request=request,
            callback_handler=callback_handler,
            timeout=timeout,
            cancel_check=cancel_check,
            progress_handler=progress_handler,
            execute_started=execute_started,
        )

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
        return support._run_async_compat(
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
                prefer_async_log_stream=False,
            )
        )

    def _driver_is_active(self) -> bool:
        return self._driver_started and self._driver_command_id is not None

    async def _request_driver_shutdown(self, *, timeout: float) -> None:
        await self._asend_frame(ShutdownRequest().to_dict())
        await self._aread_until(
            predicate=lambda frame: frame.get("type") == ShutdownAck().type,
            timeout=timeout,
        )

    async def _finalize_driver_shutdown(self) -> None:
        try:
            await self._aprocess_delete_session(self._driver_session_id)
        finally:
            await self._aclose_log_stream(self._driver_log_stream)
            self._driver_log_stream = None
            self._reset_driver_state()

    async def aclose_driver(self, *, timeout: float = 5.0) -> None:
        """Gracefully stop the persistent sandbox-side driver."""

        if not self._driver_is_active():
            return

        try:
            await self._request_driver_shutdown(timeout=timeout)
        except Exception as exc:
            logging.debug(
                "Error during Daytona sandbox driver shutdown; proceeding with "
                "forced cleanup: %s",
                exc,
            )
        finally:
            await self._finalize_driver_shutdown()

    def close_driver(self, *, timeout: float = 5.0) -> None:
        support._run_async_compat(self.aclose_driver(timeout=timeout))

    async def areset_for_new_call(self, *, timeout: float = 5.0) -> None:
        """Reset REPL state while preserving the staged Daytona workspace."""

        await self.aclose_driver(timeout=timeout)
        try:
            await self.arun("rm -rf .fleet-rlm/prompts", cwd=self.workspace_path)
        except Exception as exc:
            logging.debug(
                "Failed to remove .fleet-rlm/prompts during Daytona sandbox reset: %s",
                exc,
            )

    def reset_for_new_call(self, *, timeout: float = 5.0) -> None:
        support._run_async_compat(self.areset_for_new_call(timeout=timeout))

    async def adelete(self) -> None:
        await self.aclose_driver()
        await self._asandbox_delete()

    def delete(self) -> None:
        support._run_async_compat(self.adelete())

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
        return support._run_async_compat(
            self.astore_prompt(text=text, kind=kind, label=label, timeout=timeout)
        )

    async def alist_prompts(self, *, timeout: float = 30.0) -> PromptManifest:
        payload = await self._arun_driver_helper(
            code="manifest = list_prompts()\nSUBMIT(output=manifest)",
            timeout=timeout,
        )
        return PromptManifest.from_raw(payload)

    def list_prompts(self, *, timeout: float = 30.0) -> PromptManifest:
        return support._run_async_compat(self.alist_prompts(timeout=timeout))

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
        return support._run_async_compat(
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
        support._run_async_compat(self._asend_frame(payload))

    async def _check_cancelled(self, cancel_check: Callable[[], bool] | None) -> None:
        if cancel_check is not None and cancel_check():
            await self.aclose_driver(timeout=1.0)
            raise DaytonaRunCancelled("Request cancelled.")

    async def _next_matching_frame(
        self, predicate: Callable[[dict[str, Any]], bool]
    ) -> dict[str, Any] | None:
        for frame in await self._adrain_frames():
            if predicate(frame):
                return frame
        return None

    async def _timeout_error(self) -> TimeoutError:
        stderr = (await self._alog_stderr(self._driver_log_stream)).strip()
        suffix = f" Driver stderr: {stderr}" if stderr else ""
        return TimeoutError(f"Timed out waiting for sandbox driver response.{suffix}")

    async def _aread_until(
        self,
        *,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await self._check_cancelled(cancel_check)
            frame = await self._next_matching_frame(predicate)
            if frame is not None:
                return frame
            await self._await_logs(self._driver_log_stream, deadline=deadline)
        raise await self._timeout_error()

    def _read_until(
        self,
        *,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        return support._run_async_compat(
            self._aread_until(
                predicate=predicate,
                timeout=timeout,
                cancel_check=cancel_check,
            )
        )

    async def _adrain_frames(self) -> list[dict[str, Any]]:
        return await self._adrain_log_frames(self._driver_log_stream)

    def _drain_frames(self) -> list[dict[str, Any]]:
        return support._run_async_compat(self._adrain_frames())

    def _can_use_async_log_stream(
        self,
        *,
        state: _SessionLogStreamState,
        prefer_async_log_stream: bool,
    ) -> Callable[..., Any] | None:
        stream_logs = getattr(
            self.sandbox.process, "get_session_command_logs_async", None
        )
        if (
            not prefer_async_log_stream
            or not callable(stream_logs)
            or state.has_async_stream
        ):
            return None
        return stream_logs

    def _record_log_stream_exit(
        self,
        *,
        state: _SessionLogStreamState,
        stream_error: str | None = None,
    ) -> None:
        with state.condition:
            if not state.closed:
                state.mode = "polling"
                state.has_async_stream = False
                state.task = None
                state.owning_loop = None
                state.stream_error = stream_error
            state.condition.notify_all()

    async def _stream_logs_async(
        self,
        *,
        state: _SessionLogStreamState,
        stream_logs: Callable[..., Any],
        session_id: str,
        command_id: str,
    ) -> None:
        try:
            result = stream_logs(
                session_id,
                command_id,
                lambda chunk: self._append_log_stdout(state, chunk),
                lambda chunk: self._append_log_stderr(state, chunk),
            )
            await support._maybe_await(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_log_stream_exit(state=state, stream_error=str(exc))
        else:
            self._record_log_stream_exit(state=state)
        finally:
            with state.condition:
                if state.task is not None and state.task.done():
                    state.task = None

    def _open_log_stream(
        self,
        *,
        session_id: str,
        command_id: str,
        prefer_async_log_stream: bool,
        existing: _SessionLogStreamState | None = None,
    ) -> _SessionLogStreamState:
        state = existing or _SessionLogStreamState(
            session_id=session_id,
            command_id=command_id,
        )
        state.session_id = session_id
        state.command_id = command_id
        stream_logs = self._can_use_async_log_stream(
            state=state,
            prefer_async_log_stream=prefer_async_log_stream,
        )
        if stream_logs is None:
            return state

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return state

        state.mode = "async_task"
        state.has_async_stream = True
        state.owning_loop = loop
        state.task = loop.create_task(
            self._stream_logs_async(
                state=state,
                stream_logs=stream_logs,
                session_id=session_id,
                command_id=command_id,
            )
        )
        return state

    async def _aclose_log_stream(self, state: _SessionLogStreamState | None) -> None:
        if state is None:
            return
        task: asyncio.Task[None] | None = None
        with state.condition:
            state.closed = True
            state.has_async_stream = False
            task = state.task
            state.task = None
            state.owning_loop = None
            state.condition.notify_all()
        if task is None:
            return
        task_loop = task.get_loop()
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if task_loop is current_loop:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            return
        task_loop.call_soon_threadsafe(task.cancel)

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
        return support._run_async_compat(self._alog_stderr(state))

    async def _adrain_log_frames(
        self, state: _SessionLogStreamState | None
    ) -> list[dict[str, Any]]:
        if state is None:
            return []
        if state.mode != "async_task":
            await self._arefresh_log_snapshot(state)
        with state.condition:
            return self._decode_log_frames_locked(state)

    def _drain_log_frames(
        self, state: _SessionLogStreamState | None
    ) -> list[dict[str, Any]]:
        return support._run_async_compat(self._adrain_log_frames(state))

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
        support._run_async_compat(self._arefresh_log_snapshot(state))

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


__all__ = ["DaytonaSandboxSession"]
