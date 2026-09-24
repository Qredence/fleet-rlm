from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.paths import UnsafePathError
from fleet_rlm.workspace.storage import DaytonaSandboxWorkspaceStorage

ROOT = "/workspace/sessions/session-a/workspace"


class _Fs:
    def __init__(self) -> None:
        self.directories = {"/", "/workspace", "/workspace/sessions", "/workspace/sessions/session-a", ROOT}
        self.files: dict[str, bytes] = {}
        self.symlinks: dict[str, str] = {}
        self.listing: list[object] = []

    def get_file_info(self, path: str):
        if path in self.symlinks:
            return SimpleNamespace(path=path, is_dir=False, is_symlink=True, size=0)
        if path in self.directories:
            return SimpleNamespace(path=path, is_dir=True, size=0)
        if path in self.files:
            return SimpleNamespace(path=path, is_dir=False, size=len(self.files[path]))
        raise FileNotFoundError(path)

    def download_file(self, path: str) -> bytes:
        resolved = self.symlinks.get(path, path)
        if resolved not in self.files:
            raise FileNotFoundError(path)
        return self.files[resolved]

    def upload_file(self, data: bytes, path: str) -> None:
        self.files[path] = data

    def delete_file(self, path: str) -> None:
        self.files.pop(path, None)

    def list_files(self, _path: str, *, depth: int):
        del depth
        return self.listing


def _storage(fs: _Fs) -> DaytonaSandboxWorkspaceStorage:
    return DaytonaSandboxWorkspaceStorage(SimpleNamespace(fs=fs), root=ROOT, volume_root="/workspace")


def test_daytona_storage_rejects_symlink_file_for_read_and_stat() -> None:
    fs = _Fs()
    fs.files["/outside/secret.txt"] = b"secret"
    fs.symlinks[f"{ROOT}/secret.txt"] = "/outside/secret.txt"
    storage = _storage(fs)

    with pytest.raises(UnsafePathError, match="symlink"):
        storage.read_text("secret.txt")
    with pytest.raises(UnsafePathError, match="symlink"):
        storage.stat_path("secret.txt")


def test_daytona_storage_bounded_binary_read_is_exact_and_rejects_symlink() -> None:
    fs = _Fs()
    path = f"{ROOT}/data.bin"
    fs.files[path] = b"12345678"
    storage = _storage(fs)

    assert storage.read_file_bytes("data.bin", max_bytes=8) == b"12345678"
    with pytest.raises(ValueError, match="file read bound exceeded"):
        storage.read_file_bytes("data.bin", max_bytes=7)

    fs.symlinks[f"{ROOT}/link.bin"] = path
    fs.symlinks[f"{ROOT}/link-dir"] = "/outside"
    with pytest.raises(UnsafePathError, match="symlink"):
        storage.read_file_bytes("link.bin", max_bytes=16)
    with pytest.raises(UnsafePathError, match="symlink"):
        storage.read_file_bytes("link-dir/data.bin", max_bytes=16)


def test_daytona_storage_recognizes_provider_octal_string_symlink_mode() -> None:
    path = f"{ROOT}/secret.txt"

    class _ProviderMetadataFS(_Fs):
        def get_file_info(self, candidate: str):
            if candidate == path:
                # The locked Daytona FileInfo model exposes mode as an octal
                # string and does not declare an is_symlink field.
                return {"path": candidate, "is_dir": False, "mode": "120777"}
            return super().get_file_info(candidate)

    fs = _ProviderMetadataFS()
    fs.files[path] = b"secret"
    storage = _storage(fs)

    with pytest.raises(UnsafePathError, match="symlink"):
        storage.read_text("secret.txt")


def test_daytona_storage_rejects_symlink_component_before_outside_read_or_write() -> None:
    fs = _Fs()
    fs.files["/outside/secret.txt"] = b"secret"
    fs.symlinks[f"{ROOT}/linked"] = "/outside"
    storage = _storage(fs)

    with pytest.raises(UnsafePathError, match="symlink"):
        storage.read_text("linked/secret.txt")
    with pytest.raises(UnsafePathError, match="symlink"):
        storage.write_text("linked/new.txt", "no")
    assert "/outside/new.txt" not in fs.files


def test_daytona_storage_rejects_symlink_and_outside_root_listing_records() -> None:
    fs = _Fs()
    fs.listing = [SimpleNamespace(path=f"{ROOT}/link", is_dir=False, is_symlink=True, size=0)]
    storage = _storage(fs)
    with pytest.raises(UnsafePathError, match="symlink"):
        storage.list_entries()

    fs.listing = [SimpleNamespace(path="/outside/escape.txt", is_dir=False, size=1)]
    with pytest.raises(UnsafePathError, match="escapes trusted root"):
        storage.list_entries()
