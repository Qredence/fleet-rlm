"""Safe Volume path layout for Fleet-controlled Daytona mounts.

Paths are *logical* Sandbox/Volume locations (PurePosixPath). They are not host
paths. Callers never interpolate untrusted tokens without ``validate_path_id``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

# Design default mount (must be absolute, not `/`, not a prohibited system dir).
DEFAULT_VOLUME_MOUNT_PATH = "/home/daytona/fleet"

# Daytona rejects mounts under these prefixes (skill + platform rules).
_PROHIBITED_MOUNT_PREFIXES = (
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/etc",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
)

# Session/run ids: UUID string (with or without hyphens) only for foundation.
_UUID_RE = re.compile(
    r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|[0-9a-fA-F]{32})$"
)


class UnsafePathError(ValueError):
    """Raised when a path id or join would escape the Fleet volume root."""


def validate_mount_path(mount_path: str) -> PurePosixPath:
    """Validate a Daytona volume mount path (absolute, non-root, no traversal)."""
    if not isinstance(mount_path, str) or not mount_path.strip():
        raise UnsafePathError("mount path is required")
    raw = mount_path.strip()
    if "\x00" in raw:
        raise UnsafePathError("mount path must not contain NUL")
    if not raw.startswith("/"):
        raise UnsafePathError("mount path must be absolute")
    if raw in {"/", "//"}:
        raise UnsafePathError("mount path cannot be filesystem root")
    if "//" in raw.lstrip("/"):  # consecutive slashes after leading /
        # Allow only a single leading slash; reject internal //
        if re.search(r"//+", raw[1:]):
            raise UnsafePathError("mount path cannot contain consecutive slashes")
    parts = PurePosixPath(raw).parts
    if ".." in parts or "." in parts[1:]:  # "." only ok as lone; not as component mid-path
        # PurePosixPath collapses '.' but not when given explicitly in string checks
        if "/../" in raw or raw.endswith("/..") or "/./" in raw or raw.endswith("/."):
            raise UnsafePathError("mount path cannot contain relative components")
        if ".." in parts:
            raise UnsafePathError("mount path cannot contain relative components")
    path = PurePosixPath(raw)
    # Normalize trailing slash away
    normalized = PurePosixPath(str(path))
    for prefix in _PROHIBITED_MOUNT_PREFIXES:
        prefix_path = PurePosixPath(prefix)
        if normalized == prefix_path or _is_relative_to(normalized, prefix_path):
            raise UnsafePathError(f"mount path cannot be under system directory {prefix}")
    return normalized


def validate_path_id(token: str | UUID, *, label: str = "id") -> str:
    """Accept a UUID-shaped token only; reject traversal and separators."""
    if isinstance(token, UUID):
        return str(token)
    if not isinstance(token, str):
        raise UnsafePathError(f"{label} must be a UUID string")
    if not token or "\x00" in token:
        raise UnsafePathError(f"{label} is empty or contains NUL")
    if any(sep in token for sep in ("/", "\\")):
        raise UnsafePathError(f"{label} must not contain path separators")
    if ".." in token:
        raise UnsafePathError(f"{label} must not contain '..'")
    if not _UUID_RE.match(token):
        raise UnsafePathError(f"{label} must be a UUID")
    # Canonical hyphenated form when possible
    try:
        return str(UUID(token))
    except ValueError as exc:
        raise UnsafePathError(f"{label} must be a UUID") from exc


def resolve_under_root(root: PurePosixPath | str, *parts: str) -> PurePosixPath:
    """Join parts under root; raise if the result escapes root."""
    base = PurePosixPath(root) if not isinstance(root, PurePosixPath) else root
    if not str(base).startswith("/"):
        raise UnsafePathError("root must be absolute")
    for part in parts:
        if part in ("", ".", "..") or "/" in part or "\\" in part or "\x00" in part:
            raise UnsafePathError(f"unsafe path segment: {part!r}")
    candidate = base.joinpath(*parts)
    # Normalize .. if any slipped through (should not)
    if ".." in candidate.parts:
        raise UnsafePathError("path escapes volume root")
    if not _is_relative_to(candidate, base):
        raise UnsafePathError("path escapes volume root")
    return candidate


def _is_relative_to(path: PurePosixPath, root: PurePosixPath) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class VolumePaths:
    """Deep facade: Fleet volume layout under a validated mount root."""

    mount_path: PurePosixPath

    @classmethod
    def from_mount(cls, mount_path: str = DEFAULT_VOLUME_MOUNT_PATH) -> VolumePaths:
        return cls(mount_path=validate_mount_path(mount_path))

    @property
    def root(self) -> PurePosixPath:
        return self.mount_path

    def skills_root(self) -> PurePosixPath:
        return resolve_under_root(self.mount_path, "skills")

    def memory_root(self) -> PurePosixPath:
        return resolve_under_root(self.mount_path, "memory")

    def artifacts_root(self) -> PurePosixPath:
        return resolve_under_root(self.mount_path, "artifacts")

    def attachments_root(self) -> PurePosixPath:
        return resolve_under_root(self.mount_path, "attachments")

    def sessions_root(self) -> PurePosixPath:
        return resolve_under_root(self.mount_path, "sessions")

    def session_dir(self, session_id: str | UUID) -> PurePosixPath:
        sid = validate_path_id(session_id, label="session_id")
        return resolve_under_root(self.mount_path, "sessions", sid)

    def session_exports_dir(self, session_id: str | UUID) -> PurePosixPath:
        sid = validate_path_id(session_id, label="session_id")
        return resolve_under_root(self.mount_path, "sessions", sid, "exports")

    def session_staging_dir(self, session_id: str | UUID) -> PurePosixPath:
        sid = validate_path_id(session_id, label="session_id")
        return resolve_under_root(self.mount_path, "sessions", sid, "staging")

    def run_dir(self, session_id: str | UUID, run_id: str | UUID) -> PurePosixPath:
        """Unique per-run root: sessions/{session_id}/runs/{run_id}/."""
        sid = validate_path_id(session_id, label="session_id")
        rid = validate_path_id(run_id, label="run_id")
        return resolve_under_root(self.mount_path, "sessions", sid, "runs", rid)

    def run_staging_dir(self, session_id: str | UUID, run_id: str | UUID) -> PurePosixPath:
        """Unique staging root for one run (under the run directory)."""
        return resolve_under_root(self.run_dir(session_id, run_id), "staging")

    def run_artifacts_dir(self, session_id: str | UUID, run_id: str | UUID) -> PurePosixPath:
        """Run-scoped durable artifacts: sessions/{session}/runs/{run}/artifacts/."""
        return resolve_under_root(self.run_dir(session_id, run_id), "artifacts")


def as_posix(path: PurePosixPath | str) -> str:
    """String form for tool payloads and Sandbox APIs."""
    return str(path) if isinstance(path, PurePosixPath) else path


def volume_paths_from_settings(settings: Any) -> VolumePaths:
    """Build VolumePaths from clean Settings (mount path field)."""
    mount = getattr(settings, "volume_mount_path", None) or DEFAULT_VOLUME_MOUNT_PATH
    return VolumePaths.from_mount(str(mount))
