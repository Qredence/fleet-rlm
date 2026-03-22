from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fleet_rlm.integrations.providers.daytona.runtime import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    DaytonaSandboxRuntime,
)


class _FakeFs:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []
        self.uploads: dict[str, bytes] = {}

    def create_folder(self, path: str, mode: str) -> None:
        self.created.append((path, mode))

    def upload_file(self, data: bytes, path: str) -> None:
        self.uploads[path] = bytes(data)


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

    def get_work_dir(self) -> str:
        return "/workspace"

    def delete(self) -> None:
        return None


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
        self.sandbox = _FakeSandbox()

    def create(self, request=None):
        self.created_requests.append(request)
        return self.sandbox

    def get(self, sandbox_id: str):
        assert sandbox_id == "sbx-123"
        return self.sandbox


def test_create_workspace_session_stages_context_and_mounts_volume(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.integrations.providers.daytona.runtime._build_daytona_client",
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
    assert session.volume_mount_path == str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
    assert len(session.context_sources) == 1
    assert session.context_sources[0].host_path == str(context_file.resolve())
    assert fake_client.volume.calls == [("tenant-a", True)]
    assert fake_client.sandbox.git.clone_calls == [
        {
            "url": "https://github.com/example/repo.git",
            "path": "/workspace/workspace/repo",
            "branch": "main",
        }
    ]
    upload_paths = set(fake_client.sandbox.fs.uploads)
    assert any(path.endswith("notes.md") for path in upload_paths)
    assert any(path.endswith("manifest.json") for path in upload_paths)


def test_resume_workspace_session_preserves_context_id(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.integrations.providers.daytona.runtime._build_daytona_client",
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
