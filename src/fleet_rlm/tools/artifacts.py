"""RLM tool-facing artifact helpers."""

from __future__ import annotations

from fleet_rlm.artifacts.storage import build_artifact_metadata, build_artifact_ref


def create_artifact_ref(**kwargs):
    """Build a safe artifact reference; content writes are deferred."""
    return build_artifact_ref(**kwargs)


def update_artifact_ref(**kwargs):
    """Deferred Phase 5 update shape."""
    _ = kwargs
    return {
        "status": "disabled",
        "error": "update_artifact is disabled by policy in the Phase 5 foundation slice.",
    }


__all__ = ["build_artifact_metadata", "build_artifact_ref", "create_artifact_ref", "update_artifact_ref"]
