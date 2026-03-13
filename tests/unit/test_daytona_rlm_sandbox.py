from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.daytona_rlm.config import ResolvedDaytonaConfig
from fleet_rlm.daytona_rlm.protocol import (
    DriverReady,
    ExecutionRequest,
    ExecutionResponse,
    RunErrorEnvelope,
    RunStartRequest,
    ShutdownAck,
    ShutdownRequest,
    decode_frame,
    encode_frame,
)
from fleet_rlm.daytona_rlm.sandbox import DaytonaSandboxRuntime, DaytonaSandboxSession
from fleet_rlm.daytona_rlm.types import DaytonaRunCancelled


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
        self.env.update(
            {
                "SUBMIT": self._submit,
                "FINAL": self._final,
                "FINAL_VAR": self._final_var,
            }
        )

    def _emit(self, payload: dict[str, object]) -> None:
        self.stdout += encode_frame(payload) + "\n"

    def _submit_impl(
        self,
        *args: object,
        finalization_mode: str,
        variable_name: str | None = None,
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
            "variable_name": variable_name,
            "finalization_mode": finalization_mode,
        }
        return value

    def _submit(self, *args: object, **kwargs: object) -> object:
        return self._submit_impl(*args, finalization_mode="SUBMIT", **kwargs)

    def _final(self, value: object) -> object:
        if isinstance(value, dict):
            return self._submit_impl(finalization_mode="FINAL", **value)
        if isinstance(value, str):
            return self._submit_impl(
                finalization_mode="FINAL",
                final_markdown=value,
            )
        return self._submit_impl(value, finalization_mode="FINAL")

    def _final_var(self, variable_name: str) -> object:
        value = self.env[variable_name]
        if isinstance(value, dict):
            return self._submit_impl(
                finalization_mode="FINAL_VAR",
                variable_name=variable_name,
                **value,
            )
        if isinstance(value, str):
            return self._submit_impl(
                finalization_mode="FINAL_VAR",
                variable_name=variable_name,
                final_markdown=value,
            )
        return self._submit_impl(
            value,
            finalization_mode="FINAL_VAR",
            variable_name=variable_name,
        )

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


def test_daytona_sandbox_session_file_and_process_helpers():
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        session_request_cls=SimpleNamespace,
        resolved_config=_resolved_config(),
        repo_url="https://github.com/example/repo.git",
        ref="main",
        repo_path="/workdir/workspace/repo",
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
        session_request_cls=SimpleNamespace,
        resolved_config=_resolved_config(),
        repo_url="https://github.com/example/repo.git",
        ref="main",
        repo_path="/workdir/workspace/repo",
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


def test_daytona_sandbox_driver_supports_typed_submit_schema():
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        session_request_cls=SimpleNamespace,
        resolved_config=_resolved_config(),
        repo_url="https://github.com/example/repo.git",
        ref="main",
        repo_path="/workdir/workspace/repo",
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


def test_daytona_sandbox_driver_returns_structured_errors():
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        session_request_cls=SimpleNamespace,
        resolved_config=_resolved_config(),
        repo_url="https://github.com/example/repo.git",
        ref="main",
        repo_path="/workdir/workspace/repo",
    )

    session.start_driver(timeout=1.0)
    response = session.execute_code(
        code="raise ValueError('boom')",
        callback_handler=lambda request: pytest.fail(f"unexpected callback: {request}"),
        timeout=1.0,
    )

    assert response.error is not None
    assert "ValueError: boom" in response.error


def test_daytona_sandbox_close_driver_cleans_up_process_session():
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        session_request_cls=SimpleNamespace,
        resolved_config=_resolved_config(),
        repo_url="https://github.com/example/repo.git",
        ref="main",
        repo_path="/workdir/workspace/repo",
    )

    session.start_driver(timeout=1.0)
    session.close_driver(timeout=1.0)

    assert sandbox.process.deleted_sessions == [session._driver_session_id]


def test_daytona_sandbox_runtime_command_uses_resolved_daytona_config(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    monkeypatch.delenv("DAYTONA_API_URL", raising=False)
    monkeypatch.delenv("DAYTONA_TARGET", raising=False)

    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        session_request_cls=SimpleNamespace,
        resolved_config=ResolvedDaytonaConfig(
            api_key="dotenv-key",
            api_url="https://dotenv.daytona.example",
            target="eu-west",
        ),
        repo_url="https://github.com/example/repo.git",
        ref="main",
        repo_path="/workdir/workspace/repo",
    )

    command = session._build_runtime_command()

    assert "export DAYTONA_API_KEY=dotenv-key" in command
    assert "export DAYTONA_API_URL=https://dotenv.daytona.example" in command
    assert "export DAYTONA_TARGET=eu-west" in command


def test_daytona_sandbox_run_rollout_maps_cancelled_runtime_errors():
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        session_request_cls=SimpleNamespace,
        resolved_config=_resolved_config(),
        repo_url="https://github.com/example/repo.git",
        ref="main",
        repo_path="/workdir/workspace/repo",
    )
    session._runtime_started = True
    session._runtime_command_id = "cmd-1"
    session.start_controller = lambda timeout=120.0: None
    session._send_runtime_frame = lambda payload: None
    session._read_until_runtime = lambda predicate, timeout: RunErrorEnvelope(
        request_id="req-1",
        error="Request cancelled.",
        category="cancelled",
    ).to_dict()

    with pytest.raises(DaytonaRunCancelled, match="Request cancelled."):
        session.run_rollout(
            request=RunStartRequest(request_id="req-1", payload={}),
            timeout=1.0,
        )


def test_daytona_runtime_clones_branch(monkeypatch: pytest.MonkeyPatch):
    fake_sandbox = _FakeSandbox()

    class _FakeDaytonaConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeDaytona:
        def __init__(self, config):
            self.config = config

        def create(self):
            return fake_sandbox

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.sandbox._load_daytona_sdk",
        lambda: (_FakeDaytona, _FakeDaytonaConfig, SimpleNamespace),
    )

    runtime = DaytonaSandboxRuntime(
        config=ResolvedDaytonaConfig(
            api_key="key",
            api_url="https://api.daytona.example",
        )
    )
    session = runtime.create_repo_session(
        repo_url="https://github.com/example/repo.git",
        ref="main",
    )

    assert session.repo_path == "/workdir/workspace/repo"
    assert fake_sandbox.git.clone_calls == [
        {
            "url": "https://github.com/example/repo.git",
            "path": "/workdir/workspace/repo",
            "branch": "main",
        }
    ]


def test_daytona_runtime_clones_commit(monkeypatch: pytest.MonkeyPatch):
    fake_sandbox = _FakeSandbox()

    class _FakeDaytonaConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeDaytona:
        def __init__(self, config):
            self.config = config

        def create(self):
            return fake_sandbox

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.sandbox._load_daytona_sdk",
        lambda: (_FakeDaytona, _FakeDaytonaConfig, SimpleNamespace),
    )

    runtime = DaytonaSandboxRuntime(
        config=ResolvedDaytonaConfig(
            api_key="key",
            api_url="https://api.daytona.example",
        )
    )
    runtime.create_repo_session(
        repo_url="https://github.com/example/repo.git",
        ref="abc1234",
    )

    assert fake_sandbox.git.clone_calls == [
        {
            "url": "https://github.com/example/repo.git",
            "path": "/workdir/workspace/repo",
            "commit_id": "abc1234",
        }
    ]


def test_daytona_runtime_reports_bootstrap_timings(monkeypatch: pytest.MonkeyPatch):
    fake_sandbox = _FakeSandbox()

    class _FakeDaytonaConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeDaytona:
        def __init__(self, config):
            self.config = config

        def create(self):
            return fake_sandbox

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.sandbox._load_daytona_sdk",
        lambda: (_FakeDaytona, _FakeDaytonaConfig, SimpleNamespace),
    )

    runtime = DaytonaSandboxRuntime(
        config=ResolvedDaytonaConfig(
            api_key="key",
            api_url="https://api.daytona.example",
        )
    )
    session, timings = runtime.create_repo_session_with_diagnostics(
        repo_url="https://github.com/example/repo.git",
        ref="main",
    )

    assert session.repo_path == "/workdir/workspace/repo"
    assert set(timings) == {"sandbox_create", "repo_clone", "context_stage"}
    assert all(isinstance(value, int) for value in timings.values())


def test_daytona_runtime_supports_reasoning_only_workspace(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_sandbox = _FakeSandbox()

    class _FakeDaytonaConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeDaytona:
        def __init__(self, config):
            self.config = config

        def create(self):
            return fake_sandbox

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.sandbox._load_daytona_sdk",
        lambda: (_FakeDaytona, _FakeDaytonaConfig, SimpleNamespace),
    )

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

    class _FakeDaytonaConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeDaytona:
        def __init__(self, config):
            self.config = config

        def create(self):
            return fake_sandbox

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.sandbox._load_daytona_sdk",
        lambda: (_FakeDaytona, _FakeDaytonaConfig, SimpleNamespace),
    )
    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.sandbox.read_document_content",
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

    assert session.repo_path == "/workdir/workspace/daytona-workspace"
    assert fake_sandbox.git.clone_calls == []
    assert len(session.context_sources) == 1
    source = session.context_sources[0]
    assert source.host_path == str(doc_path)
    assert source.source_type == "pdf"
    assert source.extraction_method == "pypdf"
    assert source.staged_path.endswith("spec.pdf.extracted.txt")
    assert any(path.endswith("manifest.json") for path in fake_sandbox.fs.files)


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

    class _FakeDaytonaConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeDaytona:
        def __init__(self, config):
            self.config = config

        def create(self):
            return fake_sandbox

    def _fake_read_document_content(path):
        if path.suffix == ".bin":
            raise ValueError("unsupported binary file")
        return ("Directory text", {"source_type": "text"})

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.sandbox._load_daytona_sdk",
        lambda: (_FakeDaytona, _FakeDaytonaConfig, SimpleNamespace),
    )
    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.sandbox.read_document_content",
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
