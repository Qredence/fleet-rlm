"""Backward-compatible artifact storage facade."""

from __future__ import annotations

from .io import (
    artifact_exists,
    list_session_artifact_refs,
    read_artifact_bytes,
    resolve_artifact_by_id,
    update_artifact_bytes,
    write_artifact_bytes,
    write_large_tool_output_artifact,
)
from .paths import (
    APPROVED_ARTIFACT_CATEGORIES,
    ArtifactPathError,
    ArtifactWriteError,
    artifact_public_relative_path,
    artifact_session_root,
    build_artifact_metadata,
    build_artifact_ref,
    resolve_artifact_path,
)

__all__ = [
    "APPROVED_ARTIFACT_CATEGORIES",
    "ArtifactPathError",
    "ArtifactWriteError",
    "artifact_exists",
    "artifact_public_relative_path",
    "artifact_session_root",
    "build_artifact_metadata",
    "build_artifact_ref",
    "list_session_artifact_refs",
    "read_artifact_bytes",
    "resolve_artifact_by_id",
    "resolve_artifact_path",
    "update_artifact_bytes",
    "write_artifact_bytes",
    "write_large_tool_output_artifact",
]
