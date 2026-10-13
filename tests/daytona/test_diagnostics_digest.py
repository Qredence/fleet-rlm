"""Pinned identities for Daytona snapshot environment manifests."""

from __future__ import annotations

from fleet_rlm.daytona.diagnostics import environment_manifest
from fleet_rlm.daytona.specs import DaytonaEnvironmentProfile, DaytonaSandboxSpec

_SESSION_DIGEST = "99aff83219c3482f613f1ab266a9ecf5b6ff617350c6f5e66514b8c21e31a9b0"
_SEMANTIC_CHILD_DIGEST = "52e6d5c3a8afd71c8b7a23120c458ff00dcfec005de3880abb416769913272c6"


def test_session_environment_digest_is_pinned_for_snapshot_identity() -> None:
    spec = DaytonaSandboxSpec("fleet-rlm-python313-v11")

    assert environment_manifest(spec, DaytonaEnvironmentProfile.SESSION).digest == _SESSION_DIGEST


def test_semantic_child_environment_digest_is_pinned_for_snapshot_identity() -> None:
    spec = DaytonaSandboxSpec("fleet-rlm-python313-v11")

    assert environment_manifest(spec, DaytonaEnvironmentProfile.SEMANTIC_CHILD).digest == _SEMANTIC_CHILD_DIGEST


def test_workspace_child_digest_matches_pinned_session_image_identity() -> None:
    spec = DaytonaSandboxSpec("fleet-rlm-python313-v11")

    assert environment_manifest(spec, DaytonaEnvironmentProfile.WORKSPACE_CHILD).digest == _SESSION_DIGEST
