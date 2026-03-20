from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from fleet_rlm.infrastructure.providers.daytona.diagnostics import (
    DaytonaDiagnosticError,
)
from fleet_rlm.infrastructure.providers.daytona.config import ResolvedDaytonaConfig
from fleet_rlm.infrastructure.providers.daytona.protocol import (
    DriverReady,
    ExecutionEventFrame,
    ExecutionRequest,
    ExecutionResponse,
    ShutdownAck,
    ShutdownRequest,
    decode_frame,
    encode_frame,
)
from fleet_rlm.infrastructure.providers.daytona.sdk import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    build_async_daytona_client,
    build_daytona_client,
)
from fleet_rlm.infrastructure.providers.daytona.sandbox import (
    DaytonaSandboxRuntime,
    DaytonaSandboxSession,
    _resolve_clone_ref,
)


class _FakeArtifacts:
    def __init__(self, stdout: str):
        self.stdout = stdout


class _FakeExecResponse:
    def __init__(self, *, exit_code: int = 0, stdout: str = ""):
        self.exit_code = exit_code
        self.result = stdout
        self.artifacts = _FakeArtifacts(stdout)


class _FakeDriverProcess:
    def __init__(self, session: "_FakeSandbox"):
        self.session = session
        self.env: dict[str, object] = {"__builtins__": __builtins__}
        self.stdout = ""
        self.started = False
        self.closed = False
        self._final_artifact: dict[str, object] | None = None
        self._submit_fields: list[str] = []
        self.env["SUBMIT"] = self._submit

    def _emit(self, payload: dict[str, object]) -> None:
        self.stdout += encode_frame(payload) + "\n"

    def _submit_impl(
        self,
        *args: object,
        **kwargs: object,
    ) -> object:
        if kwargs:
            value: object = {
                key: value for key, value in kwargs.items() if value is not None
            }
        elif self._submit_fields and args:
            value = {
                self._submit_fields[index]: item
                for index, item in enumerate(args)
                if index < len(self._submit_fields) and item is not None
            }
            if not value and len(args) == 1:
                value = args[0]
        elif len(args) == 1:
            value = args[0]
        elif args:
            value = list(args)
        else:
            value = {}

        self._final_artifact = {
            "kind": "markdown",
            "value": value,
            "variable_name": None,
            "finalization_mode": "SUBMIT",
        }
        return value

    def _submit(self, *args: object, **kwargs: object) -> object:
        return self._submit_impl(*args, **kwargs)

    def start(self) -> None:
        self.started = True
        self._emit(DriverReady().to_dict())

    def receive(self, data: str) -> None:
        for raw_line in data.splitlines():
            frame = decode_frame(raw_line)
            if frame is None:
                continue
            frame_type = frame.get("type")
            if frame_type == ShutdownRequest().type:
                self.closed = True
                self._emit(ShutdownAck().to_dict())
                continue
            if frame_type != "execute_request":
                continue

            request = ExecutionRequest(
                request_id=str(frame["request_id"]),
                code=str(frame["code"]),
                submit_schema=frame.get("submit_schema"),
            )
            self._final_artifact = None
            self._submit_fields = [
                str(item.get("name"))
                for item in (request.submit_schema or [])
                if isinstance(item, dict) and item.get("name")
            ]
            error: str | None = None
            try:
                exec(request.code, self.env, self.env)
            except Exception as exc:  # pragma: no cover - exercised through response
                error = f"{type(exc).__name__}: {exc}"
            response = ExecutionResponse(
                request_id=request.request_id,
                stdout="",
                stderr="",
                error=error,
                final_artifact=self._final_artifact,
                duration_ms=1,
                callback_count=0,
            )
            self._emit(response.to_dict())


class _FakeFS:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.folders: list[tuple[str, str]] = []

    def create_folder(self, path: str, mode: str) -> None:
        self.folders.append((path, mode))

    def upload_file(self, file: bytes, remote_path: str) -> None:
        self.files[remote_path] = file

    def download_file(self, remote_path: str) -> bytes:
        return self.files[remote_path]

    def list_files(self, path: str):
        prefix = path.rstrip("/") + "/"
        names = []
        for stored in self.files:
            if stored.startswith(prefix):
                relative = stored[len(prefix) :]
                if "/" not in relative:
                    names.append(SimpleNamespace(name=relative))
        return names

    def search_files(self, path: str, pattern: str):
        del pattern
        prefix = path.rstrip("/") + "/"
        return SimpleNamespace(
            files=[stored for stored in self.files if stored.startswith(prefix)]
        )


class _FakeGit:
    def __init__(self):
        self.clone_calls: list[dict[str, str]] = []

    def clone(self, **kwargs):
        self.clone_calls.append(kwargs)


class _FakeProcess:
    def __init__(self, sandbox: "_FakeSandbox"):
        self.sandbox = sandbox
        self.exec_calls: list[tuple[str, str | None]] = []
        self.created_sessions: list[str] = []
        self.deleted_sessions: list[str] = []
        self.get_logs_calls = 0
        self.command_counter = 0
        self.driver_by_command: dict[str, _FakeDriverProcess] = {}

    def exec(self, command: str, cwd: str | None = None):
        self.exec_calls.append((command, cwd))
        return _FakeExecResponse(stdout="ok")

    def create_session(self, session_id: str) -> None:
        self.created_sessions.append(session_id)

    def execute_session_command(self, session_id: str, req, timeout: int | None = None):
        del session_id, timeout
        self.command_counter += 1
        command_id = f"cmd-{self.command_counter}"
        driver = _FakeDriverProcess(self.sandbox)
        driver.start()
        self.driver_by_command[command_id] = driver
        return SimpleNamespace(
            cmd_id=command_id, stdout="", stderr="", output="", exit_code=None
        )

    def send_session_command_input(
        self, session_id: str, command_id: str, data: str
    ) -> None:
        del session_id
        self.driver_by_command[command_id].receive(data)

    def get_session_command_logs(self, session_id: str, command_id: str):
        del session_id
        self.get_logs_calls += 1
        driver = self.driver_by_command[command_id]
        return SimpleNamespace(stdout=driver.stdout, stderr="")

    def delete_session(self, session_id: str) -> None:
        self.deleted_sessions.append(session_id)


class _FakeSandbox:
    def __init__(self):
        self.id = "sbx-123"
        self.fs = _FakeFS()
        self.git = _FakeGit()
        self.process = _FakeProcess(self)
        self.deleted = False

    def get_work_dir(self) -> str:
        return "/workdir"

    def delete(self):
        self.deleted = True


def _resolved_config() -> ResolvedDaytonaConfig:
    return ResolvedDaytonaConfig(
        api_key="key",
        api_url="https://api.daytona.example",
    )


def _patch_daytona_sdk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fake_sandbox: _FakeSandbox,
) -> None:
    class _FakeDaytonaConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeDaytona:
        def __init__(self, config):
            self.config = config

        def create(self):
            return fake_sandbox

        def get(self, sandbox_id: str):
            assert sandbox_id == fake_sandbox.id
            return fake_sandbox

    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.Daytona",
        _FakeDaytona,
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.DaytonaConfig",
        _FakeDaytonaConfig,
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.SessionExecuteRequest",
        object(),
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.build_daytona_client",
        lambda config=None: _FakeDaytona(config),
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.build_async_daytona_client",
        lambda config=None: _FakeDaytona(config),
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support._DAYTONA_IMPORT_ERROR",
        None,
    )


def test_build_daytona_client_uses_explicit_python_sdk_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeDaytonaConfig:
        def __init__(self, **kwargs):
            captured["config_kwargs"] = kwargs

    class _FakeDaytona:
        def __init__(self, config=None):
            captured["client_config"] = config

    monkeypatch.setitem(
        sys.modules,
        "daytona",
        SimpleNamespace(Daytona=_FakeDaytona, DaytonaConfig=_FakeDaytonaConfig),
    )

    build_daytona_client(config=_resolved_config())

    assert captured["config_kwargs"] == {
        "api_key": "key",
        "api_url": "https://api.daytona.example",
        "target": None,
    }
    assert captured["client_config"] is not None


def test_build_async_daytona_client_uses_explicit_python_sdk_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeDaytonaConfig:
        def __init__(self, **kwargs):
            captured["config_kwargs"] = kwargs

    class _FakeAsyncDaytona:
        def __init__(self, config=None):
            captured["client_config"] = config

    monkeypatch.setitem(
        sys.modules,
        "daytona",
        SimpleNamespace(
            AsyncDaytona=_FakeAsyncDaytona,
            Daytona=_FakeAsyncDaytona,
            DaytonaConfig=_FakeDaytonaConfig,
        ),
    )

    build_async_daytona_client(config=_resolved_config())

    assert captured["config_kwargs"] == {
        "api_key": "key",
        "api_url": "https://api.daytona.example",
        "target": None,
    }
    assert captured["client_config"] is not None


def test_daytona_runtime_mounts_workspace_volume_via_python_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_sandbox = _FakeSandbox()
    captured: dict[str, object] = {}

    class _FakeVolume:
        id = "vol-123"

    class _FakeVolumeService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []

        def get(self, name: str, create: bool = False):
            self.calls.append((name, create))
            return _FakeVolume()

    class _FakeVolumeMount:
        def __init__(
            self,
            *,
            volume_id: str,
            mount_path: str,
            subpath: str | None = None,
        ) -> None:
            self.volume_id = volume_id
            self.mount_path = mount_path
            self.subpath = subpath

    class _FakeCreateSandboxFromSnapshotParams:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    volume_service = _FakeVolumeService()

    class _FakeDaytonaClient:
        def __init__(self) -> None:
            self.volume = volume_service

        def create(self, params=None):
            captured["create_params"] = params
            return fake_sandbox

        def get(self, sandbox_id: str):
            assert sandbox_id == fake_sandbox.id
            return fake_sandbox

    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.build_daytona_client",
        lambda config=None: _FakeDaytonaClient(),
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.build_async_daytona_client",
        lambda config=None: _FakeDaytonaClient(),
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.Daytona",
        object(),
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.DaytonaConfig",
        object(),
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.SessionExecuteRequest",
        object(),
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.VolumeMount",
        _FakeVolumeMount,
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.CreateSandboxFromSnapshotParams",
        _FakeCreateSandboxFromSnapshotParams,
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support._DAYTONA_IMPORT_ERROR",
        None,
    )

    runtime = DaytonaSandboxRuntime(config=_resolved_config())
    runtime.create_workspace_session(
        repo_url=None,
        ref=None,
        context_paths=None,
        volume_name="tenant-a",
    )

    assert volume_service.calls == [("tenant-a", True)]
    params = captured["create_params"]
    assert isinstance(params, _FakeCreateSandboxFromSnapshotParams)
    mounts = params.kwargs["volumes"]
    assert len(mounts) == 1
    assert mounts[0].volume_id == "vol-123"
    assert mounts[0].mount_path == str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)


def test_daytona_sandbox_session_file_and_process_helpers():
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url="https://github.com/example/repo.git",
        ref="main",
        workspace_path="/workdir/workspace/repo",
    )

    write_path = session.write_file("notes.txt", "hello")
    assert write_path == "/workdir/workspace/repo/notes.txt"
    assert session.read_file("notes.txt") == "hello"

    listing = session.list_files(".")
    assert "/workdir/workspace/repo/notes.txt" in listing

    found = session.find_files(".", "*")
    assert "/workdir/workspace/repo/notes.txt" in found

    result = session.run("pwd")
    assert result["ok"] is True
    assert sandbox.process.exec_calls == [("pwd", "/workdir/workspace/repo")]


def test_daytona_sandbox_driver_persists_state_and_supports_submit():
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url="https://github.com/example/repo.git",
        ref="main",
        workspace_path="/workdir/workspace/repo",
    )

    session.start_driver(timeout=1.0)
    first = session.execute_code(
        code="counter = 2",
        callback_handler=lambda request: pytest.fail(f"unexpected callback: {request}"),
        timeout=1.0,
    )
    second = session.execute_code(
        code="counter += 3\nSUBMIT(counter)",
        callback_handler=lambda request: pytest.fail(f"unexpected callback: {request}"),
        timeout=1.0,
    )

    assert first.final_artifact is None
    assert second.final_artifact is not None
    assert second.final_artifact["value"] == 5
    assert second.final_artifact["finalization_mode"] == "SUBMIT"


@pytest.mark.asyncio
async def test_daytona_async_sandbox_driver_persists_state_and_supports_submit():
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url="https://github.com/example/repo.git",
        ref="main",
        workspace_path="/workdir/workspace/repo",
    )

    await session.astart_driver(timeout=1.0)
    first = await session.aexecute_code(
        code="counter = 4",
        callback_handler=lambda request: pytest.fail(f"unexpected callback: {request}"),
        timeout=1.0,
    )
    second = await session.aexecute_code(
        code="counter += 6\nSUBMIT(counter)",
        callback_handler=lambda request: pytest.fail(f"unexpected callback: {request}"),
        timeout=1.0,
    )

    assert first.final_artifact is None
    assert second.final_artifact is not None
    assert second.final_artifact["value"] == 10
    assert second.final_artifact["finalization_mode"] == "SUBMIT"


def test_daytona_sandbox_driver_supports_typed_submit_schema():
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url="https://github.com/example/repo.git",
        ref="main",
        workspace_path="/workdir/workspace/repo",
    )

    session.start_driver(timeout=1.0)
    response = session.execute_code(
        code='SUBMIT(summary="Readable summary", final_markdown="## Heading\\nBody")',
        callback_handler=lambda request: pytest.fail(f"unexpected callback: {request}"),
        timeout=1.0,
        submit_schema=[
            {"name": "summary", "type": "str | None"},
            {"name": "final_markdown", "type": "str | None"},
            {"name": "output", "type": "object"},
        ],
    )

    assert response.error is None
    assert response.final_artifact is not None
    assert response.final_artifact["value"] == {
        "summary": "Readable summary",
        "final_markdown": "## Heading\nBody",
    }
    assert response.final_artifact["finalization_mode"] == "SUBMIT"


def test_daytona_sandbox_driver_rejects_removed_final_aliases():
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url="https://github.com/example/repo.git",
        ref="main",
        workspace_path="/workdir/workspace/repo",
    )

    session.start_driver(timeout=1.0)
    response = session.execute_code(
        code="FINAL('deprecated')",
        callback_handler=lambda request: pytest.fail(f"unexpected callback: {request}"),
        timeout=1.0,
    )

    assert response.final_artifact is None
    assert response.error is not None
    assert "FINAL" in response.error


def test_daytona_sandbox_driver_returns_structured_errors():
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url="https://github.com/example/repo.git",
        ref="main",
        workspace_path="/workdir/workspace/repo",
    )

    session.start_driver(timeout=1.0)
    response = session.execute_code(
        code="raise ValueError('boom')",
        callback_handler=lambda request: pytest.fail(f"unexpected callback: {request}"),
        timeout=1.0,
    )

    assert response.error is not None
    assert "ValueError: boom" in response.error


@pytest.mark.asyncio
async def test_daytona_sandbox_execute_code_forwards_progress_events():
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url="https://github.com/example/repo.git",
        ref="main",
        workspace_path="/workdir/workspace/repo",
    )
    session.astart_driver = lambda timeout=1.0, prefer_async_log_stream=True: (
        asyncio.sleep(0)
    )
    sent_frames: list[dict[str, object]] = []
    session._asend_frame = lambda payload: asyncio.sleep(
        0, result=sent_frames.append(payload)
    )

    async def _read_until(predicate, timeout, cancel_check=None):
        del predicate, timeout, cancel_check
        request_id = str(sent_frames[0]["request_id"])
        if not emitted:
            return ExecutionEventFrame(
                request_id=request_id,
                stream="stdout",
                text="loading repository\n",
            ).to_dict()
        return ExecutionResponse(
            request_id=request_id,
            stdout="loading repository\n",
            duration_ms=1,
        ).to_dict()

    session._aread_until = _read_until

    emitted: list[ExecutionEventFrame] = []
    response = await session.aexecute_code(
        code="print('loading repository')",
        callback_handler=lambda request: pytest.fail(f"unexpected callback: {request}"),
        timeout=1.0,
        progress_handler=emitted.append,
    )

    assert response.stdout == "loading repository\n"
    assert len(emitted) == 1
    assert emitted[0].stream == "stdout"
    assert emitted[0].text == "loading repository\n"


@pytest.mark.asyncio
async def test_daytona_async_log_stream_stays_on_request_loop():
    sandbox = _FakeSandbox()
    stream_loop_ids: list[int] = []
    send_loop_ids: list[int] = []

    original_send = sandbox.process.send_session_command_input

    async def _send_input(session_id: str, command_id: str, data: str) -> None:
        send_loop_ids.append(id(asyncio.get_running_loop()))
        original_send(session_id, command_id, data)

    async def _stream_logs(session_id: str, command_id: str, on_stdout, on_stderr):
        del session_id, on_stderr
        stream_loop_ids.append(id(asyncio.get_running_loop()))
        driver = sandbox.process.driver_by_command[command_id]
        seen = 0
        idle_rounds = 0
        while idle_rounds < 5000:
            current = driver.stdout
            if len(current) > seen:
                on_stdout(current[seen:])
                seen = len(current)
                idle_rounds = 0
            else:
                idle_rounds += 1
            if driver.closed:
                break
            await asyncio.sleep(0.001)

    sandbox.process.send_session_command_input = _send_input
    sandbox.process.get_session_command_logs_async = _stream_logs
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url="https://github.com/example/repo.git",
        ref="main",
        workspace_path="/workdir/workspace/repo",
    )

    await session.astart_driver(timeout=1.0)
    response = await session.aexecute_code(
        code='SUBMIT(summary="hello from async logs")',
        callback_handler=lambda request: pytest.fail(f"unexpected callback: {request}"),
        timeout=1.0,
        submit_schema=[{"name": "summary", "type": "str | None"}],
    )
    await session.aclose_driver(timeout=1.0)

    assert response.final_artifact is not None
    assert response.final_artifact["value"] == {"summary": "hello from async logs"}
    assert stream_loop_ids
    assert send_loop_ids
    assert set(stream_loop_ids) == set(send_loop_ids)
    assert len(set(stream_loop_ids)) == 1
    assert sandbox.process.get_logs_calls <= 1


def test_daytona_sync_wrappers_poll_logs_even_when_async_stream_exists():
    sandbox = _FakeSandbox()
    async_stream_calls = 0

    async def _stream_logs(session_id: str, command_id: str, on_stdout, on_stderr):
        del session_id, command_id, on_stdout, on_stderr
        nonlocal async_stream_calls
        async_stream_calls += 1

    sandbox.process.get_session_command_logs_async = _stream_logs
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url="https://github.com/example/repo.git",
        ref="main",
        workspace_path="/workdir/workspace/repo",
    )

    session.start_driver(timeout=1.0)
    response = session.execute_code(
        code='SUBMIT(summary="hello from polled logs")',
        callback_handler=lambda request: pytest.fail(f"unexpected callback: {request}"),
        timeout=1.0,
        submit_schema=[{"name": "summary", "type": "str | None"}],
    )
    session.close_driver(timeout=1.0)

    assert response.final_artifact is not None
    assert response.final_artifact["value"] == {"summary": "hello from polled logs"}
    assert async_stream_calls == 0
    assert sandbox.process.get_logs_calls > 0


def test_daytona_sandbox_close_driver_cleans_up_process_session():
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url="https://github.com/example/repo.git",
        ref="main",
        workspace_path="/workdir/workspace/repo",
    )

    session.start_driver(timeout=1.0)
    session.close_driver(timeout=1.0)

    assert sandbox.process.deleted_sessions == [session._driver_session_id]


def test_daytona_runtime_clones_branch(monkeypatch: pytest.MonkeyPatch):
    fake_sandbox = _FakeSandbox()
    _patch_daytona_sdk(monkeypatch, fake_sandbox=fake_sandbox)
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_runtime._list_remote_refs",
        lambda repo_url: {"main"},
    )

    runtime = DaytonaSandboxRuntime(
        config=ResolvedDaytonaConfig(
            api_key="key",
            api_url="https://api.daytona.example",
        )
    )
    session = runtime.create_workspace_session(
        repo_url="https://github.com/example/repo.git",
        ref="main",
        context_paths=None,
    )

    assert session.workspace_path == "/workdir/workspace/repo"
    assert fake_sandbox.git.clone_calls == [
        {
            "url": "https://github.com/example/repo.git",
            "path": "/workdir/workspace/repo",
            "branch": "main",
        }
    ]


def test_daytona_runtime_clones_commit(monkeypatch: pytest.MonkeyPatch):
    fake_sandbox = _FakeSandbox()
    _patch_daytona_sdk(monkeypatch, fake_sandbox=fake_sandbox)

    runtime = DaytonaSandboxRuntime(
        config=ResolvedDaytonaConfig(
            api_key="key",
            api_url="https://api.daytona.example",
        )
    )
    runtime.create_workspace_session(
        repo_url="https://github.com/example/repo.git",
        ref="abc1234",
        context_paths=None,
    )

    assert fake_sandbox.git.clone_calls == [
        {
            "url": "https://github.com/example/repo.git",
            "path": "/workdir/workspace/repo",
            "commit_id": "abc1234",
        }
    ]


def test_resolve_clone_ref_uses_longest_matching_remote_prefix(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_runtime._list_remote_refs",
        lambda repo_url: {"main", "release/2026-03", "feature/foo"},
    )

    assert (
        _resolve_clone_ref(
            "https://github.com/example/repo.git",
            "release/2026-03/src/frontend",
        )
        == "release/2026-03"
    )
    assert (
        _resolve_clone_ref(
            "https://github.com/example/repo.git",
            "feature/foo/src/app.ts",
        )
        == "feature/foo"
    )


def test_daytona_runtime_reports_bootstrap_timings(monkeypatch: pytest.MonkeyPatch):
    fake_sandbox = _FakeSandbox()
    _patch_daytona_sdk(monkeypatch, fake_sandbox=fake_sandbox)

    runtime = DaytonaSandboxRuntime(
        config=ResolvedDaytonaConfig(
            api_key="key",
            api_url="https://api.daytona.example",
        )
    )
    session = runtime.create_workspace_session(
        repo_url="https://github.com/example/repo.git",
        ref="main",
        context_paths=None,
    )

    assert session.workspace_path == "/workdir/workspace/repo"
    assert set(session.phase_timings_ms) == {
        "sandbox_create",
        "repo_clone",
        "context_stage",
    }
    assert all(isinstance(value, int) for value in session.phase_timings_ms.values())


def test_daytona_runtime_can_resume_existing_workspace_session(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_sandbox = _FakeSandbox()
    _patch_daytona_sdk(monkeypatch, fake_sandbox=fake_sandbox)

    runtime = DaytonaSandboxRuntime(config=_resolved_config())
    session = runtime.resume_workspace_session(
        sandbox_id="sbx-123",
        repo_url="https://github.com/example/repo.git",
        ref="main",
        workspace_path="/workdir/workspace/repo",
        context_sources=[],
    )

    assert session.sandbox_id == "sbx-123"
    assert session.workspace_path == "/workdir/workspace/repo"
    assert "sandbox_resume" in session.phase_timings_ms


@pytest.mark.asyncio
async def test_daytona_async_runtime_uses_workspace_volume_and_resume_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_sandbox = _FakeSandbox()
    captured: dict[str, object] = {}

    class _FakeVolume:
        id = "vol-async"

    class _FakeVolumeService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []

        def get(self, name: str, create: bool = False):
            self.calls.append((name, create))
            return _FakeVolume()

    class _FakeVolumeMount:
        def __init__(
            self, *, volume_id: str, mount_path: str, subpath: str | None = None
        ):
            self.volume_id = volume_id
            self.mount_path = mount_path
            self.subpath = subpath

    class _FakeCreateSandboxFromSnapshotParams:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    volume_service = _FakeVolumeService()

    class _FakeAsyncDaytonaClient:
        def __init__(self) -> None:
            self.volume = volume_service

        async def create(self, params=None):
            captured["create_params"] = params
            return fake_sandbox

        async def get(self, sandbox_id: str):
            assert sandbox_id == fake_sandbox.id
            return fake_sandbox

    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.build_async_daytona_client",
        lambda config=None: _FakeAsyncDaytonaClient(),
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.Daytona",
        object(),
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.AsyncDaytona",
        object(),
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.DaytonaConfig",
        object(),
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.SessionExecuteRequest",
        object(),
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.VolumeMount",
        _FakeVolumeMount,
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.CreateSandboxFromSnapshotParams",
        _FakeCreateSandboxFromSnapshotParams,
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support._DAYTONA_IMPORT_ERROR",
        None,
    )

    runtime = DaytonaSandboxRuntime(config=_resolved_config())
    session = await runtime.acreate_workspace_session(
        repo_url=None,
        ref=None,
        context_paths=None,
        volume_name="tenant-async",
    )
    resumed = await runtime.aresume_workspace_session(
        sandbox_id="sbx-123",
        repo_url=None,
        ref=None,
        workspace_path=session.workspace_path,
        context_sources=[],
    )

    assert volume_service.calls == [("tenant-async", True)]
    params = captured["create_params"]
    mounts = params.kwargs["volumes"]
    assert mounts[0].volume_id == "vol-async"
    assert mounts[0].mount_path == str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
    assert resumed.sandbox_id == "sbx-123"


def test_daytona_runtime_supports_reasoning_only_workspace(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_sandbox = _FakeSandbox()
    _patch_daytona_sdk(monkeypatch, fake_sandbox=fake_sandbox)

    runtime = DaytonaSandboxRuntime(config=_resolved_config())
    session = runtime.create_workspace_session(
        repo_url=None,
        ref=None,
        context_paths=None,
    )

    assert session.repo_url == ""
    assert session.workspace_path == "/workdir/workspace/daytona-workspace"
    assert session.context_sources == []
    assert fake_sandbox.git.clone_calls == []


def test_daytona_runtime_stages_document_context_without_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    fake_sandbox = _FakeSandbox()
    doc_path = tmp_path / "spec.pdf"
    doc_path.write_bytes(b"%PDF-1.7 fake")
    _patch_daytona_sdk(monkeypatch, fake_sandbox=fake_sandbox)
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_runtime.read_document_content",
        lambda path: (
            f"Extracted {path.name}",
            {
                "source_type": "pdf",
                "extraction_method": "pypdf",
            },
        ),
    )

    runtime = DaytonaSandboxRuntime(config=_resolved_config())
    session = runtime.create_workspace_session(
        repo_url=None,
        ref=None,
        context_paths=[str(doc_path)],
    )

    assert session.workspace_path == "/workdir/workspace/daytona-workspace"
    assert fake_sandbox.git.clone_calls == []
    assert len(session.context_sources) == 1
    source = session.context_sources[0]
    assert source.host_path == str(doc_path)
    assert source.source_type == "pdf"
    assert source.extraction_method == "pypdf"
    assert source.staged_path.endswith("spec.pdf.extracted.txt")
    assert any(path.endswith("manifest.json") for path in fake_sandbox.fs.files)


def test_daytona_runtime_surfaces_ocr_required_for_scanned_pdf_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    fake_sandbox = _FakeSandbox()
    doc_path = tmp_path / "scanned.pdf"
    doc_path.write_bytes(b"%PDF-1.7 scanned bytes")
    _patch_daytona_sdk(monkeypatch, fake_sandbox=fake_sandbox)

    def _raise_scanned_pdf(_path):
        raise ValueError(
            f"PDF '{doc_path}' appears to be image-only or scanned. OCR is required before analysis."
        )

    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_runtime.read_document_content",
        _raise_scanned_pdf,
    )

    runtime = DaytonaSandboxRuntime(config=_resolved_config())

    with pytest.raises(
        DaytonaDiagnosticError,
        match="OCR is required before analysis",
    ):
        runtime.create_workspace_session(
            repo_url=None,
            ref=None,
            context_paths=[str(doc_path)],
        )


def test_daytona_runtime_raises_helpful_error_when_sdk_is_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.Daytona", None
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.DaytonaConfig", None
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support.SessionExecuteRequest",
        None,
    )
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_support._DAYTONA_IMPORT_ERROR",
        ImportError("missing daytona"),
    )

    with pytest.raises(
        RuntimeError, match="https://www.daytona.io/docs/en/python-sdk/"
    ):
        DaytonaSandboxRuntime(config=_resolved_config())


def test_daytona_runtime_stages_directory_context_with_skipped_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    fake_sandbox = _FakeSandbox()
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    good_file = docs_dir / "README.md"
    good_file.write_text("# Example\n", encoding="utf-8")
    bad_file = docs_dir / "archive.bin"
    bad_file.write_bytes(b"\x00\x01")

    def _fake_read_document_content(path):
        if path.suffix == ".bin":
            raise ValueError("unsupported binary file")
        return ("Directory text", {"source_type": "text"})

    _patch_daytona_sdk(monkeypatch, fake_sandbox=fake_sandbox)
    monkeypatch.setattr(
        "fleet_rlm.infrastructure.providers.daytona.sandbox_runtime.read_document_content",
        _fake_read_document_content,
    )

    runtime = DaytonaSandboxRuntime(config=_resolved_config())
    session = runtime.create_workspace_session(
        repo_url=None,
        ref=None,
        context_paths=[str(docs_dir)],
    )

    assert len(session.context_sources) == 1
    source = session.context_sources[0]
    assert source.kind == "directory"
    assert source.file_count == 1
    assert source.skipped_count == 1
    assert source.warnings
