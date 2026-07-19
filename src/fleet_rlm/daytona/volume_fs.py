"""Workspace Volume Scope blob I/O (logical Sandbox paths ↔ durable bytes).

Offline tests use ``HostVolumeMirror``. Live runs use ``DaytonaSandboxVolumeFs``
against a Sandbox that mounts the Workspace Volume Scope subpath.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from fleet_rlm.daytona.paths import UnsafePathError, VolumePaths, as_posix, validate_mount_path


@dataclass(frozen=True, slots=True)
class VolumeFile:
    """Bounded metadata returned for a regular file in the mounted Volume."""

    path: str
    modified_at: float


class VolumeBlobFs(Protocol):
    """Read/write bytes at Fleet-controlled logical Volume paths."""

    def write_bytes(self, logical_path: str, data: bytes) -> None: ...

    def read_bytes(self, logical_path: str) -> bytes: ...

    def exists(self, logical_path: str) -> bool: ...

    def remove(self, logical_path: str) -> None: ...


class VolumeTreeFs(VolumeBlobFs, Protocol):
    """Blob filesystem with explicitly rooted, bounded file enumeration."""

    def list_files(self, logical_root: str, *, max_depth: int, max_files: int) -> tuple[VolumeFile, ...]: ...


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

    def list_files(self, logical_root: str, *, max_depth: int, max_files: int) -> tuple[VolumeFile, ...]:
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        if max_files <= 0:
            raise ValueError("max_files must be positive")
        root = self.host_path_for(logical_root)
        if not root.is_dir():
            return ()
        results: list[VolumeFile] = []
        base_depth = len(root.parts)
        for candidate in sorted(root.rglob("*")):
            if len(results) >= max_files:
                break
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if len(candidate.parts) - base_depth > max_depth:
                continue
            try:
                candidate.relative_to(self._root)
            except ValueError as exc:
                raise UnsafePathError("enumerated path escapes volume root") from exc
            logical = self._paths.mount_path / candidate.relative_to(self._root)
            results.append(VolumeFile(str(logical), candidate.stat().st_mtime))
        return tuple(results)


class DaytonaSandboxVolumeFs:
    """Write/read logical mount paths via a live Daytona Sandbox FS API."""

    def __init__(self, sandbox: Any) -> None:
        self._sandbox = sandbox

    @property
    def sandbox(self) -> Any:
        """Live Sandbox used by richer Daytona-only filesystem adapters."""
        return self._sandbox

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
        try:
            self._sandbox.fs.delete_file(as_posix(logical_path))
        except Exception as exc:  # noqa: BLE001 - provider not-found is idempotent
            if not _is_not_found(exc):
                raise

    def list_files(self, logical_root: str, *, max_depth: int, max_files: int) -> tuple[VolumeFile, ...]:
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        if max_files <= 0:
            raise ValueError("max_files must be positive")
        root = as_posix(logical_root)
        entries = self._sandbox.fs.list_files(root, depth=max_depth)
        results: list[VolumeFile] = []
        for entry in entries:
            path = getattr(entry, "path", None)
            if not isinstance(path, str) or not _is_under(path, root):
                continue
            if bool(getattr(entry, "is_dir", False)):
                continue
            modified_at = getattr(entry, "mod_time", None)
            if hasattr(modified_at, "timestamp"):
                modified_at = modified_at.timestamp()
            if not isinstance(modified_at, (int, float)):
                continue
            results.append(VolumeFile(path, float(modified_at)))
            if len(results) >= max_files:
                break
        return tuple(results)


def _is_under(path: str, root: str) -> bool:
    try:
        PurePosixPath(path).relative_to(PurePosixPath(root))
    except ValueError:
        return False
    return True


def _is_not_found(exc: BaseException) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    if getattr(exc, "status_code", None) == 404:
        return True
    response = getattr(exc, "response", None)
    return response is not None and getattr(response, "status_code", None) == 404


__all__ = ["DaytonaSandboxVolumeFs", "HostVolumeMirror", "VolumeBlobFs", "VolumeFile", "VolumeTreeFs"]
