"""Pure artifact path helpers and reference builders."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import PurePosixPath

from fleet_rlm.tools.paths import PathSafetyError, validate_relative_posix_path
from fleet_rlm.utils.identity import sanitize_id

from .schemas import ArtifactMetadata, ArtifactRef

APPROVED_ARTIFACT_CATEGORIES = frozenset({"plans", "reports", "data"})
DEFAULT_ARTIFACT_VOLUME_ROOT = PurePosixPath("/home/daytona/memory")


class ArtifactPathError(ValueError):
    """Raised when an artifact path escapes the approved root."""


class ArtifactWriteError(ValueError):
    """Raised when an artifact write or update violates policy."""


def _validate_relative_artifact_path(path: str) -> PurePosixPath:
    try:
        return validate_relative_posix_path(
            path,
            empty_message="Artifact path must not be empty.",
            traversal_message="Artifact path traversal is not allowed.",
            absolute_message="Artifact path must stay inside the artifact root.",
            backslash_message="Backslash artifact paths are not allowed.",
        )
    except PathSafetyError as exc:
        raise ArtifactPathError(str(exc)) from exc


def artifact_session_root(
    session_id: str,
    *,
    volume_mount_path: str | None = None,
) -> PurePosixPath:
    """Return the approved artifact root for one runtime session."""
    safe_session = sanitize_id(session_id, "session")
    volume_root = PurePosixPath(str(volume_mount_path or DEFAULT_ARTIFACT_VOLUME_ROOT))
    return volume_root / "artifacts" / "sessions" / safe_session


def artifact_public_relative_path(
    session_id: str,
    *,
    category: str,
    relative_path: str,
) -> str:
    """Return the safe public relative path for one artifact under the volume root."""
    if category not in APPROVED_ARTIFACT_CATEGORIES:
        raise ArtifactPathError(f"Unsupported artifact category: {category!r}")
    safe_relative = _validate_relative_artifact_path(relative_path)
    safe_session = sanitize_id(session_id, "session")
    return str(PurePosixPath("artifacts") / "sessions" / safe_session / category / safe_relative)


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


def artifact_id_for_public_path(public_path: str) -> str:
    digest = hashlib.sha256(public_path.encode("utf-8")).hexdigest()[:16]
    return f"artifact-{digest}"


def parse_artifact_location_from_public_path(public_path: str) -> tuple[str, str]:
    """Parse (category, relative_path) from one public artifact path."""
    parts = PurePosixPath(public_path).parts
    if len(parts) < 5 or parts[0] != "artifacts" or parts[1] != "sessions":
        raise ArtifactWriteError("Invalid artifact reference.")
    return parts[3], str(PurePosixPath(*parts[4:]))


def build_artifact_ref(
    *,
    session_id: str,
    category: str,
    relative_path: str,
    mime_type: str | None = None,
    size_bytes: int | None = None,
    checksum: str | None = None,
) -> ArtifactRef:
    """Build a stable artifact reference without writing file content."""
    public_path = artifact_public_relative_path(
        session_id,
        category=category,
        relative_path=relative_path,
    )
    return ArtifactRef(
        id=artifact_id_for_public_path(public_path),
        session_id=sanitize_id(session_id, "session"),
        category=category,
        path=public_path,
        uri=f"daytona://{public_path}",
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
    "ArtifactWriteError",
    "DEFAULT_ARTIFACT_VOLUME_ROOT",
    "artifact_id_for_public_path",
    "artifact_public_relative_path",
    "artifact_session_root",
    "build_artifact_metadata",
    "build_artifact_ref",
    "parse_artifact_location_from_public_path",
    "resolve_artifact_path",
]
