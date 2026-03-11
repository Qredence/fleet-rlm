"""Guide-native Daytona sandbox adapter for the experimental RLM pilot."""

from __future__ import annotations

import os
import re
import shlex
import time
import uuid
from pathlib import PurePosixPath
from typing import Any, Callable

from .config import ResolvedDaytonaConfig, resolve_daytona_config
from .diagnostics import DaytonaDiagnosticError
from .driver import DAYTONA_DRIVER_SOURCE
from .protocol import (
    DriverReady,
    ExecutionRequest,
    ExecutionResponse,
    HostCallbackRequest,
    HostCallbackResponse,
    RunCancelRequest,
    RunErrorEnvelope,
    RunEventFrame,
    RunReady,
    RunResultEnvelope,
    RunStartRequest,
    ShutdownAck,
    ShutdownRequest,
    decode_frame,
    encode_frame,
)
from .types import (
    DaytonaRunCancelled,
    DaytonaRunResult,
    PromptHandle,
    PromptManifest,
    PromptSliceRef,
)

_HOST_CALLBACK_REQUEST_TYPE = "host_callback_request"
_EXECUTION_RESPONSE_TYPE = "execute_response"
_OUTPUT_ONLY_SUBMIT_SCHEMA = [{"name": "output", "type": "object"}]
_RUNTIME_ENV_KEYS = (
    "DAYTONA_API_KEY",
    "DAYTONA_API_URL",
    "DAYTONA_TARGET",
    "DSPY_LM_MODEL",
    "DSPY_LM_API_BASE",
    "DSPY_LM_MAX_TOKENS",
    "DSPY_LLM_API_KEY",
    "DSPY_LM_API_KEY",
    "DSPY_DELEGATE_LM_MODEL",
    "DSPY_DELEGATE_LM_API_BASE",
    "DSPY_DELEGATE_LM_API_KEY",
)


def _load_daytona_sdk() -> Any:
    try:
        from daytona import Daytona, DaytonaConfig, SessionExecuteRequest
    except ImportError as exc:  # pragma: no cover - exercised by runtime users
        raise RuntimeError(
            "Daytona SDK is not available. Install dependencies with `uv sync` "
            "and configure DAYTONA_API_KEY / DAYTONA_API_URL before using Daytona commands."
        ) from exc
    return Daytona, DaytonaConfig, SessionExecuteRequest


def _looks_like_commit(ref: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", ref.strip()))


def _safe_repo_name(repo_url: str) -> str:
    tail = repo_url.rstrip("/").rsplit("/", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", tail).strip("-")
    return cleaned or "repo"


class DaytonaSandboxSession:
    """A single Daytona sandbox session for one root or child node."""

    def __init__(
        self,
        *,
        sandbox: Any,
        session_request_cls: type[Any],
        resolved_config: ResolvedDaytonaConfig | None = None,
        repo_url: str,
        ref: str | None,
        repo_path: str,
    ) -> None:
        self.sandbox = sandbox
        self._session_request_cls = session_request_cls
        self._resolved_config = resolved_config
        self.repo_url = repo_url
        self.ref = ref
        self.repo_path = repo_path
        self._driver_session_id = f"fleet-rlm-{uuid.uuid4().hex}"
        self._driver_command_id: str | None = None
        self._driver_started = False
        self._stdout_offset = 0
        self._frame_buffer = ""
        self._driver_path = str(PurePosixPath(repo_path) / ".fleet-rlm" / "driver.py")
        self._runtime_session_id = f"fleet-rlm-runtime-{uuid.uuid4().hex}"
        self._runtime_command_id: str | None = None
        self._runtime_started = False
        self._runtime_stdout_offset = 0
        self._runtime_frame_buffer = ""

    @property
    def sandbox_id(self) -> str | None:
        return getattr(self.sandbox, "id", None)

    def run(self, command: str, *, cwd: str | None = None) -> dict[str, Any]:
        response = self.sandbox.process.exec(command, cwd=cwd or self.repo_path)
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

    def read_file(self, path: str) -> str:
        remote_path = str(self._resolve_path(path))
        raw = self.sandbox.fs.download_file(remote_path)
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    def write_file(self, path: str, content: str) -> str:
        remote_path = str(self._resolve_path(path))
        parent = str(PurePosixPath(remote_path).parent)
        if parent and parent not in {".", "/"}:
            self.sandbox.fs.create_folder(parent, "755")
        self.sandbox.fs.upload_file(content.encode("utf-8"), remote_path)
        return remote_path

    def list_files(self, path: str = ".") -> list[str]:
        remote_path = str(self._resolve_path(path))
        files = self.sandbox.fs.list_files(remote_path)
        results: list[str] = []
        for item in files:
            name = getattr(item, "name", None)
            if name:
                results.append(str(PurePosixPath(remote_path) / str(name)))
        return sorted(results)

    def find_files(self, path: str = ".", pattern: str = "*") -> list[str]:
        remote_path = str(self._resolve_path(path))
        response = self.sandbox.fs.search_files(remote_path, pattern)
        files = getattr(response, "files", []) or []
        return [str(item) for item in files]

    def start_driver(self, *, timeout: float = 30.0) -> None:
        """Start the persistent sandbox-side driver once per sandbox."""

        if self._driver_started:
            return

        try:
            self.write_file(".fleet-rlm/driver.py", DAYTONA_DRIVER_SOURCE)
            self.sandbox.process.create_session(self._driver_session_id)
            request = self._session_request_cls(
                command=f"python -u {self._driver_path} {self.repo_path}",
                run_async=True,
                suppress_input_echo=True,
            )
            response = self.sandbox.process.execute_session_command(
                self._driver_session_id,
                request,
                timeout=int(timeout) if timeout > 0 else None,
            )
            self._driver_command_id = str(response.cmd_id)
            self._driver_started = True
            self._stdout_offset = 0
            self._frame_buffer = ""
            self._read_until(
                predicate=lambda frame: frame.get("type") == DriverReady().type,
                timeout=timeout,
            )
        except Exception as exc:
            self._driver_command_id = None
            self._driver_started = False
            self._stdout_offset = 0
            self._frame_buffer = ""
            raise DaytonaDiagnosticError(
                f"Daytona driver handshake failure: {exc}",
                category="driver_handshake_error",
                phase="driver_start",
            ) from exc

    def execute_code(
        self,
        *,
        code: str,
        callback_handler: Callable[[HostCallbackRequest], HostCallbackResponse],
        timeout: float,
        submit_schema: list[dict[str, Any]] | None = None,
    ) -> ExecutionResponse:
        """Execute one code block through the persistent sandbox-side driver."""

        self.start_driver(timeout=timeout)
        request = ExecutionRequest(
            request_id=uuid.uuid4().hex,
            code=code,
            submit_schema=submit_schema,
        )
        self._send_frame(request.to_dict())

        while True:
            frame = self._read_until(
                predicate=lambda payload: (
                    payload.get("type")
                    in {_HOST_CALLBACK_REQUEST_TYPE, _EXECUTION_RESPONSE_TYPE}
                ),
                timeout=timeout,
            )
            if frame.get("type") == _HOST_CALLBACK_REQUEST_TYPE:
                callback_request = HostCallbackRequest.from_dict(frame)
                callback_response = callback_handler(callback_request)
                self._send_frame(callback_response.to_dict())
                continue
            if frame.get("request_id") != request.request_id:
                continue
            return ExecutionResponse.from_dict(frame)

    def close_driver(self, *, timeout: float = 5.0) -> None:
        """Gracefully stop the persistent sandbox-side driver."""

        if not self._driver_started or self._driver_command_id is None:
            return

        try:
            self._send_frame(ShutdownRequest().to_dict())
            self._read_until(
                predicate=lambda frame: frame.get("type") == ShutdownAck().type,
                timeout=timeout,
            )
        except Exception:
            pass
        finally:
            try:
                self.sandbox.process.delete_session(self._driver_session_id)
            finally:
                self._driver_command_id = None
                self._driver_started = False
                self._stdout_offset = 0
                self._frame_buffer = ""

    def start_controller(self, *, timeout: float = 120.0) -> None:
        """Start the self-orchestrated sandbox runtime once per sandbox."""

        if self._runtime_started:
            return

        runtime_command = self._build_runtime_command()
        try:
            self.sandbox.process.create_session(self._runtime_session_id)
            request = self._session_request_cls(
                command=runtime_command,
                run_async=True,
                suppress_input_echo=True,
            )
            response = self.sandbox.process.execute_session_command(
                self._runtime_session_id,
                request,
                timeout=int(timeout) if timeout > 0 else None,
            )
            self._runtime_command_id = str(response.cmd_id)
            self._runtime_started = True
            self._runtime_stdout_offset = 0
            self._runtime_frame_buffer = ""
            self._read_until_runtime(
                predicate=lambda frame: frame.get("type") == RunReady().type,
                timeout=timeout,
            )
        except Exception as exc:
            self._runtime_command_id = None
            self._runtime_started = False
            self._runtime_stdout_offset = 0
            self._runtime_frame_buffer = ""
            raise DaytonaDiagnosticError(
                f"Daytona runtime handshake failure: {exc}",
                category="driver_handshake_error",
                phase="driver_start",
            ) from exc

    def run_rollout(
        self,
        *,
        request: RunStartRequest,
        timeout: float,
        event_handler: Callable[[RunEventFrame], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> DaytonaRunResult:
        """Run one self-orchestrated Daytona rollout in this sandbox."""

        self.start_controller(timeout=timeout)
        self._send_runtime_frame(request.to_dict())
        cancel_sent = False

        while True:
            if (
                cancel_check is not None
                and cancel_check()
                and not cancel_sent
                and self._runtime_started
            ):
                self._send_runtime_frame(
                    RunCancelRequest(request_id=request.request_id).to_dict()
                )
                cancel_sent = True

            frame = self._read_until_runtime(
                predicate=lambda payload: (
                    payload.get("type")
                    in {
                        RunEventFrame(request_id="", kind="").type,
                        RunResultEnvelope(request_id="", result={}).type,
                        RunErrorEnvelope(request_id="", error="").type,
                    }
                ),
                timeout=timeout,
            )
            frame_type = str(frame.get("type", "") or "")
            if frame_type == RunEventFrame(request_id="", kind="").type:
                if event_handler is not None:
                    event_handler(RunEventFrame.from_dict(frame))
                continue
            if frame_type == RunErrorEnvelope(request_id="", error="").type:
                error = RunErrorEnvelope.from_dict(frame)
                if error.category == "cancelled":
                    raise DaytonaRunCancelled(error.error)
                raise DaytonaDiagnosticError(
                    error.error,
                    category=error.category or "runtime_error",
                    phase="run",
                )
            result = RunResultEnvelope.from_dict(frame)
            return DaytonaRunResult.from_raw(result.result)

    def close_controller(self) -> None:
        if not self._runtime_started or self._runtime_command_id is None:
            return
        try:
            self.sandbox.process.delete_session(self._runtime_session_id)
        finally:
            self._runtime_command_id = None
            self._runtime_started = False
            self._runtime_stdout_offset = 0
            self._runtime_frame_buffer = ""

    def delete(self) -> None:
        self.close_controller()
        self.close_driver()
        if hasattr(self.sandbox, "delete"):
            self.sandbox.delete()

    def store_prompt(
        self,
        *,
        text: str,
        kind: str = "manual",
        label: str | None = None,
        timeout: float = 30.0,
    ) -> PromptHandle:
        payload = self._run_driver_helper(
            code=(
                f"handle = store_prompt({text!r}, kind={kind!r}, label={label!r})\n"
                "SUBMIT(output=handle)"
            ),
            timeout=timeout,
        )
        return PromptHandle.from_raw(payload)

    def list_prompts(self, *, timeout: float = 30.0) -> PromptManifest:
        payload = self._run_driver_helper(
            code="manifest = list_prompts()\nSUBMIT(output=manifest)",
            timeout=timeout,
        )
        return PromptManifest.from_raw(payload)

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
        payload = self._run_driver_helper(
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

    def _run_driver_helper(self, *, code: str, timeout: float) -> dict[str, Any]:
        def _unexpected_callback(request: HostCallbackRequest) -> HostCallbackResponse:
            raise RuntimeError(
                f"Prompt helper execution does not expect host callbacks: {request.name}"
            )

        response = self.execute_code(
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

    def _build_runtime_command(self) -> str:
        env_values = {
            "DAYTONA_API_KEY": (
                self._resolved_config.api_key if self._resolved_config else ""
            ),
            "DAYTONA_API_URL": (
                self._resolved_config.api_url if self._resolved_config else ""
            ),
            "DAYTONA_TARGET": (
                self._resolved_config.target if self._resolved_config else ""
            )
            or "",
        }
        for key in _RUNTIME_ENV_KEYS:
            if key not in env_values:
                env_values[key] = os.environ.get(key, "")

        exports = [
            f"export {key}={shlex.quote(value)}"
            for key, value in env_values.items()
            if value
        ]
        exports.append(f"cd {shlex.quote(self.repo_path)}")
        exports.append(
            "uv run --python 3.12 python -m fleet_rlm.daytona_rlm.sandbox_controller"
        )
        return " && ".join(exports)

    def _send_frame(self, payload: dict[str, Any]) -> None:
        if self._driver_command_id is None:
            raise RuntimeError("Sandbox driver is not running")
        self.sandbox.process.send_session_command_input(
            self._driver_session_id,
            self._driver_command_id,
            encode_frame(payload) + "\n",
        )

    def _send_runtime_frame(self, payload: dict[str, Any]) -> None:
        if self._runtime_command_id is None:
            raise RuntimeError("Sandbox runtime is not running")
        self.sandbox.process.send_session_command_input(
            self._runtime_session_id,
            self._runtime_command_id,
            encode_frame(payload) + "\n",
        )

    def _read_until(
        self,
        *,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for frame in self._drain_frames():
                if predicate(frame):
                    return frame
            time.sleep(0.05)

        logs = self.sandbox.process.get_session_command_logs(
            self._driver_session_id,
            self._driver_command_id,
        )
        stderr = str(getattr(logs, "stderr", "") or "").strip()
        suffix = f" Driver stderr: {stderr}" if stderr else ""
        raise TimeoutError(f"Timed out waiting for sandbox driver response.{suffix}")

    def _read_until_runtime(
        self,
        *,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for frame in self._drain_runtime_frames():
                if predicate(frame):
                    return frame
            time.sleep(0.05)

        logs = self.sandbox.process.get_session_command_logs(
            self._runtime_session_id,
            self._runtime_command_id,
        )
        stderr = str(getattr(logs, "stderr", "") or "").strip()
        suffix = f" Runtime stderr: {stderr}" if stderr else ""
        raise TimeoutError(f"Timed out waiting for sandbox runtime response.{suffix}")

    def _drain_frames(self) -> list[dict[str, Any]]:
        if self._driver_command_id is None:
            return []
        logs = self.sandbox.process.get_session_command_logs(
            self._driver_session_id,
            self._driver_command_id,
        )
        stdout = str(getattr(logs, "stdout", "") or "")
        new_text = stdout[self._stdout_offset :]
        self._stdout_offset = len(stdout)
        self._frame_buffer += new_text

        frames: list[dict[str, Any]] = []
        while "\n" in self._frame_buffer:
            line, self._frame_buffer = self._frame_buffer.split("\n", 1)
            decoded = decode_frame(line.strip())
            if decoded is not None:
                frames.append(decoded)
        return frames

    def _drain_runtime_frames(self) -> list[dict[str, Any]]:
        if self._runtime_command_id is None:
            return []
        logs = self.sandbox.process.get_session_command_logs(
            self._runtime_session_id,
            self._runtime_command_id,
        )
        stdout = str(getattr(logs, "stdout", "") or "")
        new_text = stdout[self._runtime_stdout_offset :]
        self._runtime_stdout_offset = len(stdout)
        self._runtime_frame_buffer += new_text

        frames: list[dict[str, Any]] = []
        while "\n" in self._runtime_frame_buffer:
            line, self._runtime_frame_buffer = self._runtime_frame_buffer.split("\n", 1)
            decoded = decode_frame(line.strip())
            if decoded is not None:
                frames.append(decoded)
        return frames

    def _resolve_path(self, path: str) -> PurePosixPath:
        candidate = PurePosixPath(path)
        if candidate.is_absolute():
            return candidate
        return PurePosixPath(self.repo_path) / candidate


class DaytonaSandboxRuntime:
    """Factory for Daytona sandboxes used by the pilot."""

    def __init__(self, *, config: ResolvedDaytonaConfig | None = None) -> None:
        Daytona, DaytonaConfig, session_request_cls = _load_daytona_sdk()
        resolved = config or resolve_daytona_config()
        self._resolved_config = resolved
        self._client = Daytona(
            DaytonaConfig(
                api_key=resolved.api_key,
                api_url=resolved.api_url,
                target=resolved.target,
            )
        )
        self._session_request_cls = session_request_cls

    def _create_sandbox(self) -> Any:
        try:
            return self._client.create()
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona sandbox create failure: {exc}",
                category="sandbox_create_clone_error",
                phase="sandbox_create",
            ) from exc

    def _build_repo_path(self, sandbox: Any, repo_url: str) -> str:
        work_dir = (
            sandbox.get_work_dir() if hasattr(sandbox, "get_work_dir") else "/workspace"
        )
        repo_name = _safe_repo_name(repo_url)
        return str(PurePosixPath(work_dir) / "workspace" / repo_name)

    def _clone_repo(
        self, *, sandbox: Any, repo_url: str, ref: str | None, repo_path: str
    ) -> None:
        try:
            work_dir = (
                sandbox.get_work_dir()
                if hasattr(sandbox, "get_work_dir")
                else "/workspace"
            )
            sandbox.fs.create_folder(str(PurePosixPath(work_dir) / "workspace"), "755")

            clone_kwargs: dict[str, Any] = {"url": repo_url, "path": repo_path}
            if ref:
                if _looks_like_commit(ref):
                    clone_kwargs["commit_id"] = ref
                else:
                    clone_kwargs["branch"] = ref
            sandbox.git.clone(**clone_kwargs)
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Daytona repo clone failure: {exc}",
                category="sandbox_create_clone_error",
                phase="repo_clone",
            ) from exc

    def create_repo_session_with_diagnostics(
        self, *, repo_url: str, ref: str | None
    ) -> tuple[DaytonaSandboxSession, dict[str, int]]:
        timings = {"sandbox_create": 0, "repo_clone": 0}
        sandbox: Any | None = None
        try:
            create_started = time.perf_counter()
            sandbox = self._create_sandbox()
            timings["sandbox_create"] = int(
                (time.perf_counter() - create_started) * 1000
            )

            repo_path = self._build_repo_path(sandbox, repo_url)

            clone_started = time.perf_counter()
            self._clone_repo(
                sandbox=sandbox,
                repo_url=repo_url,
                ref=ref,
                repo_path=repo_path,
            )
            timings["repo_clone"] = int((time.perf_counter() - clone_started) * 1000)

            session = DaytonaSandboxSession(
                sandbox=sandbox,
                session_request_cls=self._session_request_cls,
                resolved_config=self._resolved_config,
                repo_url=repo_url,
                ref=ref,
                repo_path=repo_path,
            )
            return session, timings
        except Exception as exc:
            if sandbox is not None and hasattr(sandbox, "delete"):
                try:
                    sandbox.delete()
                except Exception:
                    pass
            raise exc

    def create_repo_session(
        self, *, repo_url: str, ref: str | None
    ) -> DaytonaSandboxSession:
        session, _ = self.create_repo_session_with_diagnostics(
            repo_url=repo_url, ref=ref
        )
        return session
