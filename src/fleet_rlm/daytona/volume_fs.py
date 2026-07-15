"""Workspace Volume Scope blob I/O (logical Sandbox paths ↔ durable bytes).

Offline tests use ``HostVolumeMirror``. Live runs use ``DaytonaSandboxVolumeFs``
against a Sandbox that mounts the Workspace Volume Scope subpath.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from fleet_rlm.daytona.paths import UnsafePathError, VolumePaths, as_posix, validate_mount_path


class VolumeBlobFs(Protocol):
    """Read/write bytes at Fleet-controlled logical Volume paths."""

    def write_bytes(self, logical_path: str, data: bytes) -> None: ...

    def read_bytes(self, logical_path: str) -> bytes: ...

    def exists(self, logical_path: str) -> bool: ...

    def remove(self, logical_path: str) -> None: ...


class HostVolumeMirror:
    """Private Workspace Volume Scope double: host directory mirrors the mount root.

    Logical paths under ``volume_paths.mount_path`` map to files under ``host_root``.
    This is the offline stand-in for the Daytona Volume subpath mount — not a
    production source of truth by itself.
    """

    def __init__(
        self,
        host_root: Path | str,
        *,
        volume_paths: VolumePaths | None = None,
    ) -> None:
        self._paths = volume_paths or VolumePaths.from_mount()
        self._root = Path(host_root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def host_root(self) -> Path:
        return self._root

    @property
    def volume_paths(self) -> VolumePaths:
        return self._paths

    def host_path_for(self, logical_path: str) -> Path:
        mount = self._paths.mount_path
        validate_mount_path(str(mount))
        logical = PurePosixPath(logical_path)
        try:
            relative = logical.relative_to(mount)
        except ValueError as exc:
            raise UnsafePathError("logical path escapes volume mount") from exc
        if ".." in relative.parts:
            raise UnsafePathError("logical path escapes volume mount")
        return self._root.joinpath(*relative.parts)

    def write_bytes(self, logical_path: str, data: bytes) -> None:
        dest = self.host_path_for(logical_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def read_bytes(self, logical_path: str) -> bytes:
        dest = self.host_path_for(logical_path)
        if not dest.is_file():
            msg = f"volume path not found: {logical_path}"
            raise FileNotFoundError(msg)
        return dest.read_bytes()

    def exists(self, logical_path: str) -> bool:
        return self.host_path_for(logical_path).is_file()

    def remove(self, logical_path: str) -> None:
        self.host_path_for(logical_path).unlink(missing_ok=True)


class DaytonaSandboxVolumeFs:
    """Write/read logical mount paths via a live Daytona Sandbox FS API."""

    def __init__(self, sandbox: Any) -> None:
        self._sandbox = sandbox

    def write_bytes(self, logical_path: str, data: bytes) -> None:
        path = as_posix(logical_path)
        parent = str(PurePosixPath(path).parent)
        fs = self._sandbox.fs
        # Best-effort parent creation when the SDK exposes mkdir/create_folder.
        mkdir = getattr(fs, "create_folder", None) or getattr(fs, "mkdir", None)
        if callable(mkdir):
            try:
                mkdir(parent)
            except Exception:  # noqa: BLE001 - parent may already exist
                pass
        fs.upload_file(data, path)

    def read_bytes(self, logical_path: str) -> bytes:
        raw = self._sandbox.fs.download_file(as_posix(logical_path))
        if isinstance(raw, bytes):
            return raw
        if isinstance(raw, str):
            return raw.encode("utf-8")
        return bytes(raw)

    def exists(self, logical_path: str) -> bool:
        try:
            self.read_bytes(logical_path)
        except Exception:  # noqa: BLE001 - missing file surfaces as provider errors
            return False
        return True

    def remove(self, logical_path: str) -> None:
        self._sandbox.fs.delete_file(as_posix(logical_path))
