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
        self.file_contents = {
            "/workspace/repo/README.md": "hello fleet",
            "/workspace/repo/large.txt": "x" * 12,
            "/home/daytona/memory/artifacts/report.txt": "artifact",
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

    def list_files(self, path: str) -> list[Any]:
        self.list_calls.append(path)
        return self.list_entries.get(path, [])

    def read_file(self, path: str) -> str:
        self.read_calls.append(path)
        return self.file_contents[path]


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
