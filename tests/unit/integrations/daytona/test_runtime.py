from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
from types import SimpleNamespace

from fleet_rlm.integrations.daytona.runtime import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    DaytonaSandboxRuntime,
    DaytonaSandboxSession,
)
from fleet_rlm.integrations.daytona.types import ContextSource, SandboxSpec
from fleet_rlm.integrations.daytona.repo import _areconcile_repo_checkout


class _FakeProcessExecResult:
    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        result: str | None = None,
        exit_code: int = 0,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.result = stdout if result is None else result
        self.exit_code = exit_code
        self.artifacts = SimpleNamespace(stdout=self.result, charts=[])


class _FakeFs:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []
        self.uploads: dict[str, bytes] = {}
        self.downloads: list[str] = []
        self.list_calls: list[str] = []
        self.files: dict[str, bytes | str] = {}
        self.listings: dict[str, list[object]] = {}

    def create_folder(self, path: str, mode: str) -> None:
        self.created.append((path, mode))

    def upload_file(self, data: bytes, path: str) -> None:
        self.uploads[path] = bytes(data)

    def download_file(self, path: str) -> bytes | str:
        self.downloads.append(path)
        return self.files.get(path, b"")

    def list_files(self, path: str) -> list[object]:
        self.list_calls.append(path)
        return list(self.listings.get(path, []))


class _FakeGit:
    def __init__(self) -> None:
        self.clone_calls: list[dict[str, str]] = []

    def clone(self, **kwargs: str) -> None:
        self.clone_calls.append(kwargs)


class _FakeSandbox:
    def __init__(self) -> None:
        self.id = "sbx-123"
        self.fs = _FakeFs()
        self.git = _FakeGit()
        self.process = SimpleNamespace(
            exec_calls=[],
            exec=self._exec,
            code_run_calls=[],
            code_run=self._code_run,
        )
        self.fork_calls: list[tuple[str | None, float]] = []
        self.snapshot_calls: list[tuple[str, float]] = []

    async def get_work_dir(self) -> str:
        return "/workspace"

    def delete(self) -> None:
        return None

    def stop(self, timeout: float = 60) -> None:
        return None

    def refresh_activity(self) -> None:
        return None

    def _exec(self, command: str):
        self.process.exec_calls.append(command)
        return _FakeProcessExecResult()

    def _code_run(self, code: str, params=None, timeout=None):
        self.process.code_run_calls.append(code)
        return _FakeProcessExecResult()

    async def _experimental_fork(
        self, *, name: str | None = None, timeout: float | None = 60
    ) -> "_FakeSandbox":
        self.fork_calls.append((name, timeout))
        forked = _FakeSandbox()
        forked.id = f"{self.id}-fork"
        return forked

    async def _experimental_create_snapshot(
        self, *, name: str, timeout: float | None = 60
    ) -> None:
        self.snapshot_calls.append((name, timeout))


class _FakeVolumeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def get(self, name: str, create: bool = False):
        self.calls.append((name, create))
        return SimpleNamespace(id="vol-123")


class _FakeClient:
    def __init__(self) -> None:
        self.volume = _FakeVolumeService()
        self.created_requests: list[object] = []
        self.create_call_kwargs: list[dict[str, object]] = []
        self.sandbox = _FakeSandbox()
        self.close_calls = 0

    def create(
        self,
        request=None,
        *,
        timeout: float = 60,
        on_snapshot_create_logs=None,
    ):
        self.created_requests.append(request)
        self.create_call_kwargs.append(
            {
                "timeout": timeout,
                "on_snapshot_create_logs": on_snapshot_create_logs,
            }
        )
        return self.sandbox

    def get(self, sandbox_id: str):
        assert sandbox_id == "sbx-123"
        return self.sandbox

    async def close(self) -> None:
        self.close_calls += 1


class _LoopBoundCodeInterpreter:
    def __init__(self, sandbox: "_LoopBoundSandbox") -> None:
        self._sandbox = sandbox
        self.context_calls: list[str] = []

    async def create_context(self, cwd: str):
        current_loop_id = id(asyncio.get_running_loop())
        assert self._sandbox.owner_loop_id is not None
        assert current_loop_id == self._sandbox.owner_loop_id
        self.context_calls.append(cwd)
        return SimpleNamespace(id="ctx-123")


class _LoopBoundSandbox(_FakeSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.owner_loop_id: int | None = None
        self.code_interpreter = _LoopBoundCodeInterpreter(self)


class _LoopBoundClient(_FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.sandbox = _LoopBoundSandbox()

    def create(
        self,
        request=None,
        *,
        timeout: float = 60,
        on_snapshot_create_logs=None,
    ):
        self.sandbox.owner_loop_id = id(asyncio.get_running_loop())
        return super().create(
            request=request,
            timeout=timeout,
            on_snapshot_create_logs=on_snapshot_create_logs,
        )


def test_create_workspace_session_stages_context_and_mounts_volume(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.runtime._build_daytona_client",
        lambda config: fake_client,
    )

    context_file = tmp_path / "notes.md"
    context_file.write_text("# Notes\nHello\n", encoding="utf-8")

    runtime = DaytonaSandboxRuntime(
        config=SimpleNamespace(
            api_key="key", api_url="https://api.daytona.test", target=None
        )
    )
    session = runtime.create_workspace_session(
        repo_url="https://github.com/example/repo.git",
        ref="main",
        context_paths=[str(context_file)],
        volume_name="tenant-a",
    )

    assert session.sandbox_id == "sbx-123"
    assert session.workspace_path == "/workspace/workspace/repo"
    assert session.volume_name == "tenant-a"
    assert session.volume_mount_path == str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
    assert len(session.context_sources) == 1
    assert session.context_sources[0].host_path == str(context_file.resolve())
    assert fake_client.volume.calls == [("tenant-a", True)]
    assert {
        ("/home/daytona/memory/memory", "755"),
        ("/home/daytona/memory/artifacts", "755"),
        ("/home/daytona/memory/buffers", "755"),
        ("/home/daytona/memory/meta", "755"),
    }.issubset(set(fake_client.sandbox.fs.created))
    assert fake_client.sandbox.git.clone_calls == [
        {
            "url": "https://github.com/example/repo.git",
            "path": "/workspace/workspace/repo",
            "branch": "main",
        }
    ]
    assert len(fake_client.create_call_kwargs) == 1
    assert fake_client.create_call_kwargs[0]["timeout"] == 0
    assert callable(fake_client.create_call_kwargs[0]["on_snapshot_create_logs"])
    upload_paths = set(fake_client.sandbox.fs.uploads)
    assert any(path.endswith("notes.md") for path in upload_paths)
    assert any(path.endswith("manifest.json") for path in upload_paths)


def test_create_workspace_session_preserves_spec_volume_name(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.runtime._build_daytona_client",
        lambda config: fake_client,
    )

    runtime = DaytonaSandboxRuntime(
        config=SimpleNamespace(
            api_key="key", api_url="https://api.daytona.test", target=None
        )
    )
    session = runtime.create_workspace_session(
        repo_url=None,
        ref=None,
        spec=SandboxSpec(volume_name="tenant-spec"),
    )

    assert fake_client.volume.calls == [("tenant-spec", True)]
    assert session.volume_name == "tenant-spec"


def test_resume_workspace_session_preserves_context_id(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.runtime._build_daytona_client",
        lambda config: fake_client,
    )

    runtime = DaytonaSandboxRuntime(
        config=SimpleNamespace(
            api_key="key", api_url="https://api.daytona.test", target=None
        )
    )
    session = runtime.resume_workspace_session(
        sandbox_id="sbx-123",
        repo_url="https://github.com/example/repo.git",
        ref="main",
        workspace_path="/workspace/workspace/repo",
        context_sources=[],
        context_id="ctx-1",
    )

    assert session.sandbox_id == "sbx-123"
    assert session.context_id == "ctx-1"
    assert session.volume_name is None


def test_daytona_runtime_rebuilds_async_client_when_event_loop_changes(
    monkeypatch,
) -> None:
    clients: list[_FakeClient] = []

    def _build_client(config):
        _ = config
        client = _FakeClient()
        clients.append(client)
        return client

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.runtime._build_daytona_client",
        _build_client,
    )

    runtime = DaytonaSandboxRuntime(
        config=SimpleNamespace(
            api_key="key", api_url="https://api.daytona.test", target=None
        )
    )

    first_loop = asyncio.new_event_loop()
    second_loop = asyncio.new_event_loop()
    try:
        first = first_loop.run_until_complete(runtime._aget_client())
        second = second_loop.run_until_complete(runtime._aget_client())
    finally:
        first_loop.close()
        second_loop.close()

    assert first is clients[0]
    assert second is clients[1]
    assert clients[0].close_calls == 1
    assert runtime._client is clients[1]


def test_daytona_runtime_close_closes_async_client(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.runtime._build_daytona_client",
        lambda config: fake_client,
    )

    runtime = DaytonaSandboxRuntime(
        config=SimpleNamespace(
            api_key="key", api_url="https://api.daytona.test", target=None
        )
    )
    # Client is created lazily; trigger creation before closing.
    asyncio.run(runtime._aget_client())
    runtime.close()
    runtime.close()

    assert fake_client.close_calls == 1


def test_create_workspace_session_and_context_share_async_owner_loop(
    monkeypatch,
) -> None:
    fake_client = _LoopBoundClient()
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.runtime._build_daytona_client",
        lambda config: fake_client,
    )

    runtime = DaytonaSandboxRuntime(
        config=SimpleNamespace(
            api_key="key", api_url="https://api.daytona.test", target=None
        )
    )
    session = runtime.create_workspace_session(
        repo_url=None,
        ref=None,
        context_paths=None,
        volume_name=None,
    )

    context = session.ensure_context()

    assert getattr(context, "id", None) == "ctx-123"
    assert fake_client.sandbox.code_interpreter.context_calls == [
        "/workspace/workspace/daytona-workspace"
    ]


def test_create_workspace_session_ignores_local_daytona_builder_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.runtime._build_daytona_client",
        lambda config: fake_client,
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".daytona").mkdir()
    (tmp_path / ".daytona" / "devcontainer.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".devcontainer").mkdir()
    (tmp_path / ".devcontainer" / "devcontainer.json").write_text(
        "{}",
        encoding="utf-8",
    )

    runtime = DaytonaSandboxRuntime(
        config=SimpleNamespace(
            api_key="key", api_url="https://api.daytona.test", target=None
        )
    )
    session = runtime.create_workspace_session(
        repo_url=None,
        ref=None,
        context_paths=None,
        volume_name="tenant-a",
    )

    assert session.workspace_path == "/workspace/workspace/daytona-workspace"
    assert session.volume_name == "tenant-a"
    assert fake_client.volume.calls == [("tenant-a", True)]


def test_reconcile_workspace_session_updates_repo_and_context_in_place(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.runtime._build_daytona_client",
        lambda config: fake_client,
    )

    first_context = tmp_path / "notes-a.md"
    first_context.write_text("# A\n", encoding="utf-8")
    second_context = tmp_path / "notes-b.md"
    second_context.write_text("# B\n", encoding="utf-8")

    runtime = DaytonaSandboxRuntime(
        config=SimpleNamespace(
            api_key="key", api_url="https://api.daytona.test", target=None
        )
    )
    session = runtime.create_workspace_session(
        repo_url="https://github.com/example/repo.git",
        ref="main",
        context_paths=[str(first_context)],
        volume_name="tenant-a",
    )

    reconciled = runtime.reconcile_workspace_session(
        session,
        repo_url="https://github.com/example/other.git",
        ref="develop",
        context_paths=[str(second_context)],
    )

    assert reconciled is session
    assert session.repo_url == "https://github.com/example/other.git"
    assert session.ref == "develop"
    assert session.workspace_path == "/workspace/workspace/other"
    assert session.volume_name == "tenant-a"
    assert len(session.context_sources) == 1
    assert session.context_sources[0].host_path == str(second_context.resolve())
    assert "workspace_reconcile" in session.phase_timings_ms
    assert "context_stage" in session.phase_timings_ms
    assert len(fake_client.sandbox.process.code_run_calls) == 2
    upload_paths = set(fake_client.sandbox.fs.uploads)
    assert any(path.endswith("notes-b.md") for path in upload_paths)


def test_reconcile_repo_checkout_reclones_same_named_repo_without_resetting_sandbox(
    tmp_path: Path,
) -> None:
    def _init_repo(path: Path, *, text: str) -> Path:
        path.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-b", "main", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(path), "config", "user.email", "test@example.com"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(path), "config", "user.name", "Test User"],
            check=True,
            capture_output=True,
            text=True,
        )
        (path / "README.md").write_text(text, encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(path), "add", "README.md"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(path), "commit", "-m", "init"],
            check=True,
            capture_output=True,
            text=True,
        )
        return path

    repo_a = _init_repo(tmp_path / "org-a" / "repo", text="repo A\n")
    repo_b = _init_repo(tmp_path / "org-b" / "repo", text="repo B\n")
    workspace_path = tmp_path / "workspace" / "repo"
    workspace_path.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "clone", str(repo_a), str(workspace_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    def _code_run(code: str, params=None, timeout=None):
        completed = subprocess.run(
            ["python3", "-c", code],
            check=False,
            capture_output=True,
            text=True,
        )
        return _FakeProcessExecResult(
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
        )

    sandbox = SimpleNamespace(process=SimpleNamespace(code_run=_code_run))
    asyncio.run(
        _areconcile_repo_checkout(
            sandbox=sandbox,
            repo_url=str(repo_b),
            ref="main",
            workspace_path=str(workspace_path),
        )
    )

    remote_url = subprocess.run(
        ["git", "-C", str(workspace_path), "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert remote_url == str(repo_b)
    assert (workspace_path / "README.md").read_text(encoding="utf-8") == "repo B\n"


def test_session_refresh_activity_is_silent_on_error() -> None:
    """``arefresh_activity`` swallows exceptions so callers never break."""
    from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxSession

    class _ExplodingSandbox(_FakeSandbox):
        def refresh_activity(self) -> None:
            raise RuntimeError("boom")

    session = DaytonaSandboxSession(
        sandbox=_ExplodingSandbox(),  # type: ignore[arg-type]
        repo_url=None,
        ref=None,
        volume_name=None,
        workspace_path="/workspace",
        context_sources=[],
        context_id=None,
    )
    # Must not raise
    asyncio.run(session.arefresh_activity())


def test_session_resize_calls_sandbox_resize() -> None:
    """``aresize`` delegates to the sandbox ``resize()`` method."""
    from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxSession

    resize_calls: list[object] = []

    class _ResizableSandbox(_FakeSandbox):
        def resize(self, resources: object, timeout: float | None = 60) -> None:
            resize_calls.append(resources)

    session = DaytonaSandboxSession(
        sandbox=_ResizableSandbox(),  # type: ignore[arg-type]
        repo_url=None,
        ref=None,
        volume_name=None,
        workspace_path="/workspace",
        context_sources=[],
        context_id=None,
    )
    asyncio.run(session.aresize(cpu=4, memory=8, disk=20))
    assert len(resize_calls) == 1
    r = resize_calls[0]
    assert getattr(r, "cpu") == 4
    assert getattr(r, "memory") == 8
    assert getattr(r, "disk") == 20


def test_session_delete_context_keeps_sandbox_alive() -> None:
    """``adelete_context`` removes only the active interpreter context."""
    from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxSession

    delete_calls: list[str] = []
    stop_calls = 0
    sandbox_delete_calls = 0
    contexts: list[object] = []

    class _ContextCodeInterpreter:
        def create_context(self, cwd: str | None = None) -> object:
            context = SimpleNamespace(id=f"ctx-{len(contexts) + 1}", cwd=cwd)
            contexts.append(context)
            return context

        def list_contexts(self) -> list[object]:
            return list(contexts)

        def delete_context(self, context: object) -> None:
            delete_calls.append(str(getattr(context, "id", "")))
            contexts.remove(context)

    class _ContextSandbox(_FakeSandbox):
        def __init__(self) -> None:
            super().__init__()
            self.code_interpreter = _ContextCodeInterpreter()

        def stop(self, timeout: float = 60) -> None:
            nonlocal stop_calls
            _ = timeout
            stop_calls += 1

        def delete(self) -> None:
            nonlocal sandbox_delete_calls
            sandbox_delete_calls += 1

    sandbox = _ContextSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,  # type: ignore[arg-type]
        repo_url=None,
        ref=None,
        volume_name=None,
        workspace_path="/workspace",
        context_sources=[],
    )

    asyncio.run(session.aensure_context())
    context_id = session.context_id

    asyncio.run(session.adelete_context())

    assert delete_calls == [context_id]
    assert session.context_id is None
    assert stop_calls == 0
    assert sandbox_delete_calls == 0


def test_session_create_lsp_server_delegates_to_sandbox() -> None:
    """``create_lsp_server`` calls through to the sandbox SDK method."""
    from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxSession

    lsp_calls: list[tuple[str, str]] = []

    class _LspSandbox(_FakeSandbox):
        def create_lsp_server(self, language_id: str, path_to_project: str):
            lsp_calls.append((language_id, path_to_project))
            return SimpleNamespace(language=language_id, path=path_to_project)

    session = DaytonaSandboxSession(
        sandbox=_LspSandbox(),  # type: ignore[arg-type]
        repo_url=None,
        ref=None,
        volume_name=None,
        workspace_path="/workspace/project",
        context_sources=[],
        context_id=None,
    )
    lsp = session.create_lsp_server()
    assert len(lsp_calls) == 1
    assert lsp_calls[0] == ("python", "/workspace/project")
    assert lsp.language == "python"


def test_daytona_session_write_file_rebinds_sandbox_on_loop_change() -> None:
    replacement_sandbox = _FakeSandbox()

    class _RuntimeRef:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []

        async def _aget_sandbox(self, sandbox_id: str, recover: bool = False):
            self.calls.append((sandbox_id, recover))
            return replacement_sandbox

    runtime_ref = _RuntimeRef()
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url=None,
        ref=None,
        volume_name=None,
        workspace_path="/workspace/repo",
        context_sources=[],
    )
    session._runtime_ref = runtime_ref
    session.owner_thread_id = -1
    session.owner_loop_id = -1

    written = asyncio.run(session.awrite_file("notes.txt", "hello"))

    assert written == "/workspace/repo/notes.txt"
    assert runtime_ref.calls == [("sbx-123", False)]
    assert sandbox.fs.uploads == {}
    assert replacement_sandbox.fs.uploads == {"/workspace/repo/notes.txt": b"hello"}


def test_daytona_session_read_file_rebinds_sandbox_on_loop_change() -> None:
    replacement_sandbox = _FakeSandbox()
    replacement_sandbox.fs.files["/workspace/repo/notes.txt"] = b"hello"

    class _RuntimeRef:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []

        async def _aget_sandbox(self, sandbox_id: str, recover: bool = False):
            self.calls.append((sandbox_id, recover))
            return replacement_sandbox

    runtime_ref = _RuntimeRef()
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url=None,
        ref=None,
        volume_name=None,
        workspace_path="/workspace/repo",
        context_sources=[],
    )
    session._runtime_ref = runtime_ref
    session.owner_thread_id = -1
    session.owner_loop_id = -1

    text = asyncio.run(session.aread_file("notes.txt"))

    assert text == "hello"
    assert runtime_ref.calls == [("sbx-123", False)]
    assert sandbox.fs.downloads == []
    assert replacement_sandbox.fs.downloads == ["/workspace/repo/notes.txt"]


def test_daytona_session_list_files_rebinds_sandbox_on_loop_change() -> None:
    replacement_sandbox = _FakeSandbox()
    replacement_sandbox.fs.listings["/workspace/repo"] = [
        SimpleNamespace(name="notes.txt", is_dir=False)
    ]

    class _RuntimeRef:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []

        async def _aget_sandbox(self, sandbox_id: str, recover: bool = False):
            self.calls.append((sandbox_id, recover))
            return replacement_sandbox

    runtime_ref = _RuntimeRef()
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url=None,
        ref=None,
        volume_name=None,
        workspace_path="/workspace/repo",
        context_sources=[],
    )
    session._runtime_ref = runtime_ref
    session.owner_thread_id = -1
    session.owner_loop_id = -1

    entries = asyncio.run(session.alist_files("/workspace/repo"))

    assert [entry.name for entry in entries] == ["notes.txt"]
    assert runtime_ref.calls == [("sbx-123", False)]
    assert sandbox.fs.list_calls == []
    assert replacement_sandbox.fs.list_calls == ["/workspace/repo"]


def test_daytona_session_write_file_emits_progress_events() -> None:
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url=None,
        ref=None,
        volume_name=None,
        workspace_path="/workspace/repo",
        context_sources=[],
    )
    events: list[dict[str, object]] = []
    session.execution_event_callback = events.append

    written = session.write_file("notes.txt", "hello")

    assert written == "/workspace/repo/notes.txt"
    assert [event.get("event_kind") for event in events] == [
        "durable_write_started",
        "durable_write_completed",
    ]
    assert events[-1]["bytes_written"] == 5


def test_runtime_fork_sandbox_creates_session() -> None:
    """``fork_sandbox`` clones the sandbox and returns a new session."""
    from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxSession

    runtime = DaytonaSandboxRuntime(
        config=SimpleNamespace(
            api_key="key", api_url="https://api.daytona.test", target=None
        )
    )
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url="https://github.com/example/repo",
        ref="main",
        volume_name="vol-1",
        workspace_path="/workspace/repo",
        context_sources=[
            ContextSource(
                source_id="ctx-1",
                kind="file",
                host_path="/host/file.txt",
                staged_path="/workspace/repo/file.txt",
            )
        ],
    )

    forked = runtime.fork_sandbox(session, name="my-fork", timeout=30.0)

    assert sandbox.fork_calls == [("my-fork", 30.0)]
    assert forked.sandbox_id == "sbx-123-fork"
    assert forked.repo_url == session.repo_url
    assert forked.ref == session.ref
    assert forked.volume_name == session.volume_name
    assert forked.workspace_path == session.workspace_path
    assert len(forked.context_sources) == len(session.context_sources)


def test_runtime_create_sandbox_snapshot_returns_summary() -> None:
    """``create_sandbox_snapshot`` triggers snapshot creation and returns metadata."""
    from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxSession

    runtime = DaytonaSandboxRuntime(
        config=SimpleNamespace(
            api_key="key", api_url="https://api.daytona.test", target=None
        )
    )
    sandbox = _FakeSandbox()
    session = DaytonaSandboxSession(
        sandbox=sandbox,
        repo_url=None,
        ref=None,
        volume_name=None,
        workspace_path="/workspace/repo",
        context_sources=[],
    )

    result = runtime.create_sandbox_snapshot(session, name="my-snapshot", timeout=45.0)

    assert sandbox.snapshot_calls == [("my-snapshot", 45.0)]
    assert result["name"] == "my-snapshot"
    assert result["sandbox_id"] == "sbx-123"
    assert result["status"] == "created"
