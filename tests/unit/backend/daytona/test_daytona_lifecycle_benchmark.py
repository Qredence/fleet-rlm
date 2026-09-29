from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

import scripts.benchmark_daytona_lifecycle as benchmark
from fleet_rlm.daytona.runtime import VolumeConfig
from scripts.benchmark_daytona_lifecycle import (
    benchmark_decision,
)


@pytest.mark.asyncio
async def test_timed_awaits_async_operations() -> None:
    async def operation() -> str:
        return "done"

    value, elapsed = await benchmark._timed(operation)

    assert value == "done"
    assert elapsed >= 0


def _install_cycle_collaborators(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    """Install the non-provider collaborators one lifecycle cycle drives."""

    class FakeInterpreter:
        def __init__(self, **_kwargs):
            pass

        def execute(self, _code):
            calls.append("execute")
            return "fleet-benchmark-ok"

        def shutdown(self):
            calls.append("shutdown")

    class FakeBridge:
        class Process:
            @staticmethod
            def code_run(_code):
                return SimpleNamespace(result="daytona")

        process = Process()

    async def volume_id(_client, _config):
        calls.append("volume")
        return "volume-1"

    async def layout(*_args, **_kwargs):
        calls.append("layout")

    monkeypatch.setattr(benchmark, "get_or_create_volume_id", volume_id)
    monkeypatch.setattr(benchmark, "ensure_volume_layout", layout)
    monkeypatch.setattr(benchmark, "verify_sandbox_spec", lambda *_args: calls.append("verify-spec"))
    monkeypatch.setattr(benchmark, "verify_sandbox_workspace_mount", lambda *_args: calls.append("verify-mount"))
    monkeypatch.setattr(benchmark, "sync_sandbox", lambda *_args: FakeBridge())
    monkeypatch.setattr(benchmark, "DaytonaCodeInterpreter", FakeInterpreter)
    monkeypatch.setattr(benchmark, "sandbox_backend", lambda *_args, **_kwargs: object())


@pytest.mark.asyncio
async def test_run_cycle_awaits_provider_operations_and_deletes_sandbox(monkeypatch) -> None:
    calls: list[str] = []

    class FakePlatform:
        async def create(self, **_kwargs):
            calls.append("create")
            return SimpleNamespace(id="sandbox-1", state="running", region="test")

        async def delete(self, _sandbox):
            calls.append("delete")

        async def get(self, _sandbox_id):
            calls.append("probe")
            # Provider truth: the Sandbox is purged once the request settles.
            return None

    class FakeVolumeClient:
        pass

    _install_cycle_collaborators(monkeypatch, calls)

    sample, _, region = await benchmark._run_cycle(
        platform=FakePlatform(),
        volume_client=FakeVolumeClient(),
        volume_config=VolumeConfig(),
        sandbox_spec=object(),
        workspace_id=uuid4(),
    )

    assert region == "test"
    # Counted only because the provider probe reported the Sandbox absent.
    assert sample["_deleted"] == 1.0
    assert calls == [
        "volume",
        "create",
        "verify-spec",
        "verify-mount",
        "layout",
        "execute",
        "execute",
        "shutdown",
        "delete",
        "probe",
    ]


@pytest.mark.asyncio
async def test_run_cycle_counts_deletion_only_on_provider_confirmed_absence(monkeypatch) -> None:
    """An accepted delete request whose Sandbox stays present is not a deletion."""
    calls: list[str] = []
    probes = 0

    class FakePlatform:
        async def create(self, **_kwargs):
            return SimpleNamespace(id="sandbox-1", state="running", region="test")

        async def delete(self, _sandbox):
            # Acceptance only: no state transition is implied by the request.
            calls.append("delete")

        async def get(self, _sandbox_id):
            nonlocal probes
            probes += 1
            return SimpleNamespace(id="sandbox-1", state="started")

    _install_cycle_collaborators(monkeypatch, calls)
    monkeypatch.setattr(benchmark, "DELETION_CONFIRM_TIMEOUT_S", 0.05)
    monkeypatch.setattr(benchmark, "DELETION_CONFIRM_POLL_INTERVAL_S", 0.0)

    sample, _, _ = await benchmark._run_cycle(
        platform=FakePlatform(),
        volume_client=object(),
        volume_config=VolumeConfig(),
        sandbox_spec=object(),
        workspace_id=uuid4(),
    )

    # The request was issued, the provider kept reporting the Sandbox present,
    # so the cycle is not counted as deleted.
    assert calls[-1] == "delete"
    assert probes > 1
    assert sample["_deleted"] == 0.0


def test_decision_requires_threshold_and_complete_cleanup() -> None:
    assert benchmark_decision(p95_seconds=10.0, deleted=20, measured=20) == "per_turn"
    assert benchmark_decision(p95_seconds=10.001, deleted=20, measured=20) == "retained_session"
    assert benchmark_decision(p95_seconds=1.0, deleted=19, measured=20) == "retained_session"
