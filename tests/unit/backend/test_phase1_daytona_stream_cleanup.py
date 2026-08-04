"""Unit coverage for the Phase 1 live proof's async strict cleanup."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tests.live.backend.test_phase1_daytona_stream import _strict_cleanup


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
