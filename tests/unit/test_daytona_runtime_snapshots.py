"""Tests for snapshot helpers in ``fleet_rlm.integrations.providers.daytona.runtime``."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fleet_rlm.integrations.providers.daytona.runtime import (
    alist_snapshots,
    aget_snapshot,
    aresolve_snapshot,
)


class _FakeSnapshotService:
    def __init__(self, items: list[SimpleNamespace] | None = None) -> None:
        self._items = items or []

    def list(self):
        return SimpleNamespace(items=self._items)

    def get(self, name: str):
        for s in self._items:
            if s.name == name:
                return s
        raise RuntimeError(f"snapshot {name!r} not found")


class _FakeClient:
    def __init__(self, snapshots: list[SimpleNamespace] | None = None) -> None:
        self.snapshot = _FakeSnapshotService(snapshots)
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def _make_snapshot(
    name: str, *, state: str = "ACTIVE", snap_id: str = "snap-1"
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name, id=snap_id, state=state, image_name="python:3.12-slim"
    )


def test_alist_snapshots_returns_summaries(monkeypatch) -> None:
    snaps = [_make_snapshot("base"), _make_snapshot("extra", snap_id="snap-2")]
    fake_client = _FakeClient(snaps)
    monkeypatch.setattr(
        "fleet_rlm.integrations.providers.daytona.runtime._build_daytona_client",
        lambda config: fake_client,
    )
    result = asyncio.run(
        alist_snapshots(config=SimpleNamespace(api_key="k", api_url="u", target=None))
    )
    assert len(result) == 2
    assert result[0]["name"] == "base"
    assert result[1]["id"] == "snap-2"


def test_aget_snapshot_found(monkeypatch) -> None:
    fake_client = _FakeClient([_make_snapshot("fleet-rlm-base")])
    monkeypatch.setattr(
        "fleet_rlm.integrations.providers.daytona.runtime._build_daytona_client",
        lambda config: fake_client,
    )
    result = asyncio.run(
        aget_snapshot(
            "fleet-rlm-base",
            config=SimpleNamespace(api_key="k", api_url="u", target=None),
        )
    )
    assert result is not None
    assert result["name"] == "fleet-rlm-base"


def test_aget_snapshot_missing(monkeypatch) -> None:
    fake_client = _FakeClient([])
    monkeypatch.setattr(
        "fleet_rlm.integrations.providers.daytona.runtime._build_daytona_client",
        lambda config: fake_client,
    )
    result = asyncio.run(
        aget_snapshot(
            "nonexistent", config=SimpleNamespace(api_key="k", api_url="u", target=None)
        )
    )
    assert result is None


def test_aresolve_snapshot_active(monkeypatch) -> None:
    fake_client = _FakeClient([_make_snapshot("fleet-rlm-base", state="ACTIVE")])
    monkeypatch.setattr(
        "fleet_rlm.integrations.providers.daytona.runtime._build_daytona_client",
        lambda config: fake_client,
    )
    result = asyncio.run(
        aresolve_snapshot(config=SimpleNamespace(api_key="k", api_url="u", target=None))
    )
    assert result == "fleet-rlm-base"


def test_aresolve_snapshot_inactive(monkeypatch) -> None:
    fake_client = _FakeClient([_make_snapshot("fleet-rlm-base", state="BUILDING")])
    monkeypatch.setattr(
        "fleet_rlm.integrations.providers.daytona.runtime._build_daytona_client",
        lambda config: fake_client,
    )
    result = asyncio.run(
        aresolve_snapshot(config=SimpleNamespace(api_key="k", api_url="u", target=None))
    )
    assert result is None
