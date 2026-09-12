"""Live proof cleanup owns provider operations and reports failures truthfully."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests.live.backend import _cleanup
from tests.live.backend._cleanup import _strict_cleanup
from tests.live.backend._mvp_support import _strict_cleanup as _mvp_strict_cleanup


class _Platform:
    def __init__(self) -> None:
        self.get_calls: list[str] = []
        self.delete_calls: list[str] = []

    async def get(self, sandbox_id: str) -> SimpleNamespace:
        self.get_calls.append(sandbox_id)
        return SimpleNamespace(id=sandbox_id)

    async def delete(self, sandbox: SimpleNamespace) -> None:
        """
        Record the sandbox identifier as deleted.

        Parameters:
                sandbox (SimpleNamespace): Sandbox object whose identifier is recorded.
        """
        self.delete_calls.append(str(sandbox.id))


class _VolumeClient:
    def __init__(self) -> None:
        self.get_calls: list[tuple[str, bool]] = []
        self.delete_calls: list[object] = []

    async def get(self, name: str, *, create: bool) -> SimpleNamespace:
        """
        Retrieve a volume representation by name.

        Parameters:
                create (bool): Whether to create the volume if it does not exist.

        Returns:
                SimpleNamespace: A volume object containing the requested name.
        """
        self.get_calls.append((name, create))
        return SimpleNamespace(name=name)

    async def delete(self, volume: SimpleNamespace) -> None:
        self.delete_calls.append(volume)


def test_strict_cleanup_awaits_provider_operations_before_returning() -> None:
    platform = _Platform()
    volume = _VolumeClient()
    resources = SimpleNamespace(
        _sandbox_ids=["sandbox-b", "sandbox-a"],
        platform=platform,
        client=SimpleNamespace(volume=volume),
        forget_sandboxes=lambda: resources._sandbox_ids.clear(),
    )

    failures = asyncio.run(_strict_cleanup(resources, "phase1-volume"))

    assert failures == ()
    assert platform.get_calls == ["sandbox-a", "sandbox-b"]
    assert platform.delete_calls == ["sandbox-a", "sandbox-b"]
    assert volume.get_calls == [("phase1-volume", False)]
    assert len(volume.delete_calls) == 1
    assert resources._sandbox_ids == []


@pytest.mark.parametrize("failed_resource", ["sandbox", "volume"])
def test_cleanup_failure_is_reported_and_other_resources_still_settle(monkeypatch, failed_resource) -> None:
    monkeypatch.setattr(_cleanup, "_CLEANUP_RETRY_DELAYS", ())
    platform, volume = _Platform(), _VolumeClient()
    resources = SimpleNamespace(_sandbox_ids=["sandbox-a"], platform=platform, client=SimpleNamespace(volume=volume))

    async def fail(_resource):
        raise RuntimeError("private provider diagnostic must not enter the receipt")

    monkeypatch.setattr(platform if failed_resource == "sandbox" else volume, "delete", fail)

    failures = asyncio.run(_strict_cleanup(resources, "owned-volume"))

    assert failures == (failed_resource,)
    assert resources._sandbox_ids == []
    if failed_resource == "sandbox":
        assert len(volume.delete_calls) == 1
    else:
        assert platform.delete_calls == ["sandbox-a"]


def test_mvp_cleanup_skips_configured_shared_volume() -> None:
    platform = _Platform()
    volume = _VolumeClient()
    resources = SimpleNamespace(
        _sandbox_ids=["sandbox-b", "sandbox-a"],
        platform=platform,
        client=SimpleNamespace(volume=volume),
    )

    failures = asyncio.run(_mvp_strict_cleanup(resources, set(), "fleet-volume"))

    assert failures == ()
    assert platform.get_calls == ["sandbox-a", "sandbox-b"]
    assert platform.delete_calls == ["sandbox-a", "sandbox-b"]
    assert volume.get_calls == []
    assert volume.delete_calls == []
    assert resources._sandbox_ids == []


def test_mvp_cleanup_deletes_ephemeral_proof_volume() -> None:
    platform = _Platform()
    volume = _VolumeClient()
    resources = SimpleNamespace(
        _sandbox_ids=["sandbox-a"],
        platform=platform,
        client=SimpleNamespace(volume=volume),
    )
    name = "fleet-rlm-live-mvp-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    failures = asyncio.run(_mvp_strict_cleanup(resources, set(), name))

    assert failures == ()
    assert platform.delete_calls == ["sandbox-a"]
    assert volume.get_calls == [(name, False)]
    assert len(volume.delete_calls) == 1
    assert resources._sandbox_ids == []
