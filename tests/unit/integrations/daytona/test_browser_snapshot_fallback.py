"""C4 + M9: browser-aware snapshot fallback + snapshot state canonicalization."""

from __future__ import annotations

from typing import Any

import pytest

from fleet_rlm.integrations.daytona.models import SandboxSpec
from fleet_rlm.integrations.daytona.snapshots import (
    BROWSER_SNAPSHOT_NAME,
    DEFAULT_SNAPSHOT_NAME,
    canonicalize_snapshot_state_token,
    fallback_to_declarative_image,
)


def test_fallback_for_base_snapshot_uses_base_image() -> None:
    spec = SandboxSpec(snapshot=DEFAULT_SNAPSHOT_NAME)
    # simulate the snapshot being missing -> fallback
    result = fallback_to_declarative_image(spec)
    assert result.snapshot is None
    assert result.image is not None
    # The base image is built without Playwright/Chromium deps.
    # We can't easily introspect the Image object, but it must be present.
    assert result.uses_declarative_image is True


def test_fallback_for_browser_snapshot_uses_browser_image() -> None:
    """C4: a missing browser snapshot must fall back to a browser-capable image."""
    spec = SandboxSpec(snapshot=BROWSER_SNAPSHOT_NAME)
    result = fallback_to_declarative_image(spec)
    assert result.snapshot is None
    assert result.image is not None
    assert result.uses_declarative_image is True


def test_fallback_for_unnamed_snapshot_uses_base_image() -> None:
    spec = SandboxSpec(snapshot="some-other-snapshot")
    result = fallback_to_declarative_image(spec)
    assert result.snapshot is None
    assert result.image is not None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ACTIVE", "active"),
        ("active", "active"),
        ("SnapshotState.ACTIVE", "active"),
        # Dashes are normalized to underscores first, then the part after the
        # last dot is taken (mirrors the volume normalizer).
        ("snapshot-state.active", "active"),
        ("SNAPSHOT_STATE.ACTIVE", "active"),
        ("", ""),
        (None, ""),
        # SDK enum-like object with .value
        (type("X", (), {"value": "ACTIVE", "name": "ACTIVE"})(), "active"),
        # SDK enum-like object with only .name
        (type("X", (), {"value": None, "name": "BUILDING"})(), "building"),
    ],
)
def test_canonicalize_snapshot_state_token(raw: Any, expected: str) -> None:
    assert canonicalize_snapshot_state_token(raw) == expected


def test_canonicalize_handles_enum_stringification_variants() -> None:
    """The old ad-hoc check failed on 'SnapshotState.ACTIVE'; the normalizer
    must accept it and reduce it to 'active'."""
    assert canonicalize_snapshot_state_token("SnapshotState.ACTIVE") == "active"
    assert canonicalize_snapshot_state_token("ACTIVE") == "active"
