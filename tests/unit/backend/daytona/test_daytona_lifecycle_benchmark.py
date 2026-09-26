from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

import scripts.benchmark_daytona_lifecycle as benchmark
from fleet_rlm.daytona.provisioning import VolumeConfig
from scripts.benchmark_daytona_lifecycle import (
    CREATE_TO_FIRST_EXECUTION_PHASES,
    benchmark_decision,
    percentile,
    summarize_samples,
)


@pytest.mark.asyncio
async def test_timed_awaits_async_operations() -> None:
    async def operation() -> str:
        return "done"

    value, elapsed = await benchmark._timed(operation)

    assert value == "done"
    assert elapsed >= 0


@pytest.mark.asyncio
async def test_run_cycle_awaits_provider_operations_and_deletes_sandbox(monkeypatch) -> None:
    calls: list[str] = []

    class FakePlatform:
        async def create(self, **_kwargs):
            calls.append("create")
            return SimpleNamespace(id="sandbox-1", state="running", region="test")

        async def delete(self, _sandbox):
            calls.append("delete")

    class FakeVolumeClient:
        pass

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

    sample, _, region = await benchmark._run_cycle(
        platform=FakePlatform(),
        volume_client=FakeVolumeClient(),
        volume_config=VolumeConfig(),
        sandbox_spec=object(),
        workspace_id=uuid4(),
    )

    assert region == "test"
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
    ]


def test_percentile_uses_nearest_rank_for_operator_threshold() -> None:
    values = [float(value) for value in range(1, 21)]

    assert percentile(values, 95) == 19.0


def test_summary_measures_create_through_first_execution() -> None:
    samples = [
        {
            "volume_readiness": 0.5,
            "sandbox_create_running": 2.0,
            "snapshot_mount_user_verification": 0.2,
            "canonical_layout": 0.1,
            "interpreter_broker_startup": 1.0,
            "first_execution": 0.2,
            "shutdown_and_deletion": 0.4,
        }
        for _ in range(20)
    ]

    summary = summarize_samples(samples)

    assert summary["create_through_first_execution"]["p95_seconds"] == 4.0
    assert set(CREATE_TO_FIRST_EXECUTION_PHASES).issubset(summary)


def test_decision_requires_threshold_and_complete_cleanup() -> None:
    assert benchmark_decision(p95_seconds=10.0, deleted=20, measured=20) == "per_turn"
    assert benchmark_decision(p95_seconds=10.001, deleted=20, measured=20) == "retained_session"
    assert benchmark_decision(p95_seconds=1.0, deleted=19, measured=20) == "retained_session"
