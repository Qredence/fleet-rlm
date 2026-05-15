"""Tests for snapshot helpers in ``fleet_rlm.integrations.daytona.snapshot_runtime``."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.daytona.sandbox_spec import SandboxSpec
from fleet_rlm.integrations.daytona.snapshot_runtime import (
    abootstrap_snapshot,
    aget_snapshot,
    alist_snapshots,
    aresolve_sandbox_spec_snapshot,
    aresolve_snapshot,
    build_base_snapshot_image,
    fallback_to_declarative_image,
)


class _FakeSnapshotService:
    def __init__(self, items: list[SimpleNamespace] | None = None) -> None:
        self._items = items or []
        self.create_calls: list[object] = []
        self.delete_calls: list[str] = []

    def list(self):
        return SimpleNamespace(items=self._items)

    def get(self, name: str):
        for s in self._items:
            if s.name == name:
                return s
        raise RuntimeError(f"snapshot {name!r} not found")

    def create(self, params, *, on_logs=None, timeout: float = 0):
        _ = on_logs, timeout
        self.create_calls.append(params)
        snapshot = _make_snapshot(params.name, snap_id="created-snap")
        self._items = [s for s in self._items if s.name != params.name]
        self._items.append(snapshot)
        return snapshot

    def delete(self, name: str) -> None:
        self.delete_calls.append(name)
        self._items = [s for s in self._items if s.name != name and s.id != name]


class _FakeClient:
    def __init__(self, snapshots: list[SimpleNamespace] | None = None) -> None:
        self.snapshot = _FakeSnapshotService(snapshots)
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def _make_snapshot(name: str, *, state: str = "ACTIVE", snap_id: str = "snap-1") -> SimpleNamespace:
    return SimpleNamespace(name=name, id=snap_id, state=state, image_name="python:3.12-slim")


def test_alist_snapshots_returns_summaries(monkeypatch) -> None:
    snaps = [_make_snapshot("base"), _make_snapshot("extra", snap_id="snap-2")]
    fake_client = _FakeClient(snaps)
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.snapshot_runtime._build_daytona_client",
        lambda config: fake_client,
    )
    result = asyncio.run(alist_snapshots(config=SimpleNamespace(api_key="k", api_url="u", target=None)))
    assert len(result) == 2
    assert result[0]["name"] == "base"
    assert result[1]["id"] == "snap-2"


def test_aget_snapshot_found(monkeypatch) -> None:
    fake_client = _FakeClient([_make_snapshot("fleet-rlm-base")])
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.snapshot_runtime._build_daytona_client",
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
        "fleet_rlm.integrations.daytona.snapshot_runtime._build_daytona_client",
        lambda config: fake_client,
    )
    result = asyncio.run(aget_snapshot("nonexistent", config=SimpleNamespace(api_key="k", api_url="u", target=None)))
    assert result is None


def test_aresolve_snapshot_active(monkeypatch) -> None:
    fake_client = _FakeClient([_make_snapshot("fleet-rlm-base", state="ACTIVE")])
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.snapshot_runtime._build_daytona_client",
        lambda config: fake_client,
    )
    result = asyncio.run(aresolve_snapshot(config=SimpleNamespace(api_key="k", api_url="u", target=None)))
    assert result == "fleet-rlm-base"


def test_aresolve_snapshot_inactive(monkeypatch) -> None:
    fake_client = _FakeClient([_make_snapshot("fleet-rlm-base", state="BUILDING")])
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.snapshot_runtime._build_daytona_client",
        lambda config: fake_client,
    )
    result = asyncio.run(aresolve_snapshot(config=SimpleNamespace(api_key="k", api_url="u", target=None)))
    assert result is None


def test_build_base_snapshot_image_includes_default_packages() -> None:
    image = build_base_snapshot_image()

    dockerfile = image.dockerfile()
    assert "FROM python:3.12-slim" in dockerfile
    assert "RUN pip install uv" in dockerfile
    for package in ("dspy-ai", "numpy", "pandas", "httpx", "pydantic"):
        assert package in dockerfile


def test_build_base_snapshot_image_accepts_custom_packages() -> None:
    image = build_base_snapshot_image(base_image="python:3.13-slim", packages=["requests"])

    dockerfile = image.dockerfile()
    assert "FROM python:3.13-slim" in dockerfile
    assert "requests" in dockerfile
    assert "dspy-ai" not in dockerfile


@pytest.mark.parametrize(
    "unsafe_spec",
    ["", " requests", "-requests", "requests; echo hacked", "requests|cat", "requests`id`", "$(id)"],
)
def test_build_base_snapshot_image_rejects_unsafe_package_specs(unsafe_spec: str) -> None:
    with pytest.raises(ValueError, match="Invalid package spec"):
        build_base_snapshot_image(packages=[unsafe_spec])


def test_fallback_to_declarative_image_uses_shared_builder() -> None:
    spec = SandboxSpec(snapshot="fleet-rlm-base", labels={"managed-by": "fleet-rlm"})

    resolved = fallback_to_declarative_image(spec)

    assert resolved.snapshot is None
    assert resolved.uses_declarative_image is True
    assert resolved.labels == {"managed-by": "fleet-rlm"}
    assert "dspy-ai" in resolved.image.dockerfile()


def test_aresolve_sandbox_spec_snapshot_keeps_active_snapshot(monkeypatch) -> None:
    async def _active_snapshot(name: str, *, config=None):
        _ = config
        return name

    monkeypatch.setattr("fleet_rlm.integrations.daytona.snapshot_runtime.aresolve_snapshot", _active_snapshot)
    spec = SandboxSpec(snapshot="fleet-rlm-base")

    resolved = asyncio.run(aresolve_sandbox_spec_snapshot(spec, config=SimpleNamespace()))

    assert resolved is spec


def test_aresolve_sandbox_spec_snapshot_falls_back_when_inactive(monkeypatch) -> None:
    async def _inactive_snapshot(name: str, *, config=None):
        _ = name, config
        return None

    monkeypatch.setattr("fleet_rlm.integrations.daytona.snapshot_runtime.aresolve_snapshot", _inactive_snapshot)
    spec = SandboxSpec(snapshot="fleet-rlm-base")

    resolved = asyncio.run(aresolve_sandbox_spec_snapshot(spec, config=SimpleNamespace()))

    assert resolved is not spec
    assert resolved.snapshot is None
    assert resolved.uses_declarative_image is True


def test_abootstrap_snapshot_reuses_existing_snapshot(monkeypatch) -> None:
    existing = _make_snapshot("fleet-rlm-base")
    fake_client = _FakeClient([existing])
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.snapshot_runtime._build_daytona_client",
        lambda config: fake_client,
    )

    result = asyncio.run(abootstrap_snapshot(config=SimpleNamespace(api_key="k", api_url="u", target=None)))

    assert result["name"] == "fleet-rlm-base"
    assert result["created"] is False
    assert result["refreshed"] is False
    assert fake_client.snapshot.create_calls == []
    assert fake_client.snapshot.delete_calls == []


def test_abootstrap_snapshot_refreshes_existing_snapshot(monkeypatch) -> None:
    existing = _make_snapshot("fleet-rlm-base", snap_id="old-snap")
    fake_client = _FakeClient([existing])
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.snapshot_runtime._build_daytona_client",
        lambda config: fake_client,
    )

    result = asyncio.run(
        abootstrap_snapshot(
            refresh=True,
            config=SimpleNamespace(api_key="k", api_url="u", target=None),
        )
    )

    assert result["name"] == "fleet-rlm-base"
    assert result["id"] == "created-snap"
    assert result["created"] is True
    assert result["refreshed"] is True
    assert fake_client.snapshot.delete_calls == ["old-snap"]
    assert len(fake_client.snapshot.create_calls) == 1
