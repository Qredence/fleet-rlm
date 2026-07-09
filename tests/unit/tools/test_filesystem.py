from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.tools.filesystem import FilesystemSafetyError, list_files_impl, read_file_impl


class _FakeSession:
    workspace_path = "/workspace/repo"

    def __init__(self) -> None:
        self.list_calls: list[str] = []
        self.read_calls: list[str] = []
        self.write_calls: list[tuple[str, str]] = []
        self.move_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []
        self.file_contents = {
            "/workspace/repo/README.md": "hello fleet",
            "/workspace/repo/large.txt": "x" * 12,
            "/home/daytona/memory/artifacts/report.txt": "artifact",
        }
        self.file_info = {
            path: SimpleNamespace(size=len(content.encode("utf-8"))) for path, content in self.file_contents.items()
        }
        self.list_entries = {
            "/workspace/repo": [
                SimpleNamespace(name="src", is_dir=True),
                SimpleNamespace(name="README.md", is_dir=False, size=11),
            ],
            "/home/daytona/memory/artifacts": [
                SimpleNamespace(name="report.txt", is_dir=False, size=8),
            ],
        }
        self.sandbox = SimpleNamespace(fs=_FakeFs(self))

    def _rebind_sandbox_if_needed(self) -> None:
        return None

    def _resolve_sandbox_path(self, path: str) -> str:
        return path

    def list_files(self, path: str) -> list[Any]:
        self.list_calls.append(path)
        return self.list_entries.get(path, [])

    def read_file(self, path: str) -> str:
        self.read_calls.append(path)
        return self.file_contents[path]

    def write_file(self, path: str, content: str) -> str:
        self.write_calls.append((path, content))
        self.file_contents[path] = content
        self.file_info[path] = SimpleNamespace(size=len(content.encode("utf-8")))
        return path


class _FakeFs:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def get_file_info(self, path: str) -> SimpleNamespace:
        if path not in self._session.file_contents:
            raise FileNotFoundError(path)
        return self._session.file_info[path]

    def move_files(self, source: str, destination: str) -> None:
        self._session.move_calls.append((source, destination))
        content = self._session.file_contents.pop(source)
        self._session.file_contents[destination] = content
        self._session.file_info[destination] = SimpleNamespace(size=len(content.encode("utf-8")))

    def delete_file(self, path: str) -> None:
        self._session.delete_calls.append(path)
        self._session.file_contents.pop(path, None)
        self._session.file_info.pop(path, None)


def _interpreter(session: _FakeSession | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        _session=session or _FakeSession(),
        volume_mount_path="/home/daytona/memory",
    )


def test_list_files_uses_daytona_session_not_host_filesystem() -> None:
    session = _FakeSession()

    payload = list_files_impl(".", interpreter=_interpreter(session))

    assert payload["status"] == "ok"
    assert session.list_calls == ["/workspace/repo"]
    assert payload["path"] == "/workspace/repo"
    assert payload["directories"][0]["name"] == "src"
    assert payload["files"][0]["name"] == "README.md"


def test_read_file_uses_daytona_session_not_host_filesystem() -> None:
    session = _FakeSession()

    payload = read_file_impl("README.md", interpreter=_interpreter(session))

    assert payload["content"] == "hello fleet"
    assert session.read_calls == ["/workspace/repo/README.md"]
    assert payload["path"] == "/workspace/repo/README.md"


def test_volume_root_is_supported_via_explicit_root() -> None:
    session = _FakeSession()

    payload = list_files_impl("artifacts", root="volume", interpreter=_interpreter(session))

    assert session.list_calls == ["/home/daytona/memory/artifacts"]
    assert payload["files"][0]["path"] == "/home/daytona/memory/artifacts/report.txt"


@pytest.mark.parametrize(
    "path",
    [
        "../secret.txt",
        "%2e%2e/secret.txt",
        "safe%2f..%2fsecret.txt",
        "dir\\..\\secret.txt",
        "/etc/passwd",
        "/Users/zocho/.env",
        "C:\\Users\\zocho\\.env",
    ],
)
def test_read_file_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(FilesystemSafetyError):
        read_file_impl(path, interpreter=_interpreter())


def test_read_file_allows_absolute_path_inside_selected_daytona_root() -> None:
    session = _FakeSession()

    payload = read_file_impl("/workspace/repo/README.md", interpreter=_interpreter(session))

    assert payload["content"] == "hello fleet"


def test_read_file_truncates_large_output() -> None:
    payload = read_file_impl("large.txt", max_bytes=5, interpreter=_interpreter())

    assert payload["content"] == "xxxxx"
    assert payload["size"] == 12
    assert payload["returned_bytes"] == 5
    assert payload["truncated"] is True
    assert "artifact" not in payload


def test_large_read_file_output_becomes_safe_artifact_when_session_scoped() -> None:
    session = _FakeSession()

    payload = read_file_impl("large.txt", max_bytes=5, interpreter=_interpreter(session), session_id="sess-1")

    assert payload["content"] == "xxxxx"
    assert payload["truncated"] is True
    assert payload["artifact_backed"] is True
    artifact = payload["artifact"]["ref"]
    assert artifact["category"] == "data"
    assert artifact["path"].startswith("artifacts/sessions/sess-1/data/tool-outputs/read_file/")
    assert artifact["size_bytes"] == 12
    assert artifact["checksum"]
    assert "/home/daytona/memory" not in str(payload)
    assert any(path.endswith(".txt") and "/tool-outputs/read_file/" in path for path in session.file_contents)


def test_reject_legacy_list_files_pattern() -> None:
    from fleet_rlm.tools.filesystem import reject_legacy_list_files_pattern

    assert reject_legacy_list_files_pattern(None) is None
    assert reject_legacy_list_files_pattern("**/*") is None

    rejected = reject_legacy_list_files_pattern("**/*.py")
    assert rejected is not None
    assert rejected["status"] == "error"


def test_read_file_rejects_detectable_symlink_escape() -> None:
    class _EscapingSession(_FakeSession):
        def get_file_info(self, path: str) -> dict[str, str]:
            _ = path
            return {"real_path": "/home/daytona/other/secret.txt"}

    with pytest.raises(FilesystemSafetyError):
        read_file_impl("README.md", interpreter=_interpreter(_EscapingSession()))
