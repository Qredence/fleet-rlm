from __future__ import annotations

import pytest

from fleet_rlm.artifacts.storage import (
    ArtifactPathError,
    artifact_public_relative_path,
    artifact_session_root,
    build_artifact_metadata,
    build_artifact_ref,
    resolve_artifact_path,
)


def test_artifact_session_root_sanitizes_session_id() -> None:
    root = artifact_session_root("../session/alpha")

    assert str(root).startswith("/home/daytona/memory/artifacts/sessions/")
    assert "/" not in root.name
    assert ".." not in root.name
    assert root.name.endswith("session-alpha")


def test_artifact_public_relative_path_uses_approved_layout() -> None:
    path = artifact_public_relative_path("sess-1", category="reports", relative_path="summary.md")

    assert path == "artifacts/sessions/sess-1/reports/summary.md"


def test_resolve_artifact_path_uses_approved_category_root() -> None:
    path = resolve_artifact_path("sess-1", category="reports", relative_path="summary.md")

    assert str(path) == "/home/daytona/memory/artifacts/sessions/sess-1/reports/summary.md"


@pytest.mark.parametrize("category", ["logs", "../reports", ""])
def test_resolve_artifact_path_rejects_unsafe_category(category: str) -> None:
    with pytest.raises(ArtifactPathError):
        resolve_artifact_path("sess-1", category=category, relative_path="summary.md")


@pytest.mark.parametrize("relative_path", ["../secret.txt", "%2e%2e/secret.txt", "a%2fb", "a\\b", "/abs.txt"])
def test_resolve_artifact_path_rejects_traversal(relative_path: str) -> None:
    with pytest.raises(ArtifactPathError):
        resolve_artifact_path("sess-1", category="data", relative_path=relative_path)


def test_build_artifact_ref_and_metadata() -> None:
    ref = build_artifact_ref(
        session_id="sess-1",
        category="plans",
        relative_path="phase-5.md",
        mime_type="text/markdown",
        size_bytes=42,
    )
    metadata = build_artifact_metadata(ref=ref, title="Phase 5")

    assert ref.id.startswith("artifact-")
    assert ref.path == "artifacts/sessions/sess-1/plans/phase-5.md"
    assert ref.uri == "daytona://artifacts/sessions/sess-1/plans/phase-5.md"
    assert "/home/daytona/memory" not in ref.path
    assert "/home/daytona/memory" not in ref.uri
    assert metadata.ref == ref
    assert metadata.title == "Phase 5"
    assert metadata.created_at
