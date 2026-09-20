"""Focused contracts for Daytona Workspace I/O projection."""

from __future__ import annotations

import pytest

from fleet_rlm.composition.daytona_workspace_gateway import _DaytonaWorkspaceFileSession
from fleet_rlm.daytona.sandbox import SandboxLeasePolicy
from fleet_rlm.workspace.models import WorkspaceEntry


@pytest.mark.asyncio
async def test_stat_preserves_an_explicit_false_checksum_request() -> None:
    calls: list[bool | None] = []

    class Workspace:
        async def stat(self, path: str, *, include_checksum: bool | None = None) -> WorkspaceEntry:
            calls.append(include_checksum)
            return WorkspaceEntry(path, "file", 3, None, None)

    session = _DaytonaWorkspaceFileSession(Workspace(), max_file_bytes=1024)
    entry = await session.stat("note.txt", include_checksum=False)

    assert calls == [False]
    assert entry is not None
    assert entry.checksum_sha256 is None


def test_volume_io_policy_preserves_explicit_confirmation_bounds() -> None:
    policy = SandboxLeasePolicy(
        kind="volume_io",
        confirm_timeout_s=7.0,
        confirm_poll_interval_s=0.25,
    )

    assert policy.confirm_absence is True
    assert policy.confirm_timeout_s == 7.0
    assert policy.confirm_poll_interval_s == 0.25
