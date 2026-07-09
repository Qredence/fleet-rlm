"""Safe artifact root helpers for Daytona-backed runtime artifacts."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import unquote

from fleet_rlm.utils.identity import sanitize_id

from .schemas import ArtifactMetadata, ArtifactRef

APPROVED_ARTIFACT_CATEGORIES = frozenset({"plans", "reports", "data"})
DEFAULT_ARTIFACT_VOLUME_ROOT = PurePosixPath("/home/daytona/memory")


class ArtifactPathError(ValueError):
    """Raised when an artifact path escapes the approved root."""


def _validate_relative_artifact_path(path: str) -> PurePosixPath:
    raw = str(path or "").strip()
    if not raw:
        raise ArtifactPathError("Artifact path must not be empty.")
    lowered = raw.lower()
    if "%2e%2e" in lowered or "%2f" in lowered or "%5c" in lowered:
        raise ArtifactPathError("Artifact path traversal is not allowed.")
    if "\\" in raw:
        raise ArtifactPathError("Backslash artifact paths are not allowed.")
    decoded = unquote(raw)
    candidate = PurePosixPath(decoded)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ArtifactPathError("Artifact path must stay inside the artifact root.")
    return candidate


def artifact_session_root(
    session_id: str,
    *,
    volume_mount_path: str | None = None,
) -> PurePosixPath:
    """Return the approved artifact root for one runtime session."""
    safe_session = sanitize_id(session_id, "session")
    volume_root = PurePosixPath(str(volume_mount_path or DEFAULT_ARTIFACT_VOLUME_ROOT))
    return volume_root / "artifacts" / "sessions" / safe_session


def resolve_artifact_path(
    session_id: str,
    *,
    category: str,
    relative_path: str,
    volume_mount_path: str | None = None,
) -> PurePosixPath:
    """Resolve one artifact path under an approved session/category root."""
    if category not in APPROVED_ARTIFACT_CATEGORIES:
        raise ArtifactPathError(f"Unsupported artifact category: {category!r}")
    safe_relative = _validate_relative_artifact_path(relative_path)
    root = artifact_session_root(session_id, volume_mount_path=volume_mount_path)
    return root / category / safe_relative


def build_artifact_ref(
    *,
    session_id: str,
    category: str,
    relative_path: str,
    volume_mount_path: str | None = None,
    mime_type: str | None = None,
    size_bytes: int | None = None,
    checksum: str | None = None,
) -> ArtifactRef:
    """Build a stable artifact reference without writing file content."""
    resolved = resolve_artifact_path(
        session_id,
        category=category,
        relative_path=relative_path,
        volume_mount_path=volume_mount_path,
    )
    uri = f"daytona://{resolved}"
    digest = hashlib.sha256(f"{session_id}\0{category}\0{resolved}".encode("utf-8")).hexdigest()[:16]
    return ArtifactRef(
        id=f"artifact-{digest}",
        session_id=sanitize_id(session_id, "session"),
        category=category,
        path=str(resolved),
        uri=uri,
        mime_type=mime_type,
        size_bytes=size_bytes,
        checksum=checksum,
    )


def build_artifact_metadata(
    *,
    ref: ArtifactRef,
    title: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ArtifactMetadata:
    """Build artifact metadata for storage or event payloads."""
    return ArtifactMetadata(
        ref=ref,
        title=title,
        created_at=datetime.now(UTC).isoformat(),
        metadata=dict(metadata or {}),
    )


__all__ = [
    "APPROVED_ARTIFACT_CATEGORIES",
    "ArtifactPathError",
    "artifact_session_root",
    "build_artifact_metadata",
    "build_artifact_ref",
    "resolve_artifact_path",
]
