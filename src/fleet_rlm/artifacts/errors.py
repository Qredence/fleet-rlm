"""Artifact domain errors."""

from __future__ import annotations


class ArtifactError(RuntimeError):
    """Base artifact error."""


class ArtifactNotFoundError(ArtifactError):
    """Missing or unauthorized artifact (do not distinguish for clients)."""


class ArtifactValidationError(ArtifactError):
    """Rejected kind, size, title, or content."""


class ArtifactStorageError(ArtifactError):
    """Committed Artifact catalog or byte storage is unavailable."""
