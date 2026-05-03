from __future__ import annotations

from pathlib import Path

from fleet_rlm.integrations.daytona.types import SandboxSpec
from fleet_rlm.integrations.daytona.workspace_runtime import (
    WorkspaceSessionCreateRequest,
    WorkspaceSessionReconcileRequest,
)


def test_workspace_session_create_request_normalizes_inputs() -> None:
    request = WorkspaceSessionCreateRequest(
        repo_url="  https://github.com/example/repo.git  ",
        ref="  main  ",
        context_paths=[" notes.md ", Path("docs/guide.md"), "", None],
        volume_name=" tenant-a ",
        spec=SandboxSpec(volume_name="tenant-spec"),
    )

    assert request.repo_url == "https://github.com/example/repo.git"
    assert request.ref == "main"
    assert request.context_paths == ["notes.md", "docs/guide.md"]
    assert request.volume_name == "tenant-a"
    assert request.spec is not None
    assert request.spec.volume_name == "tenant-spec"


def test_workspace_session_reconcile_request_accepts_scalar_context_path() -> None:
    request = WorkspaceSessionReconcileRequest(
        repo_url="   ",
        ref=" develop ",
        context_paths=" notes.md ",
    )

    assert request.repo_url is None
    assert request.ref == "develop"
    assert request.context_paths == ["notes.md"]
