"""Focused lifecycle contracts for the public Daytona runtime facade."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.daytona.runtime import ChildEnvironmentSpec, DaytonaRuntime, RootSessionSpec


@pytest.mark.asyncio
async def test_runtime_close_retains_a_failed_root_for_retry() -> None:
    calls = 0

    async def release(_lease: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("provider close failed")

    runtime = DaytonaRuntime(
        root_acquirer=lambda _spec, **_kwargs: object(),
        root_releaser=release,
    )
    spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())
    await runtime.acquire_root_session(spec)

    assert await runtime.aclose() is False
    assert len(runtime.roots) == 1
    assert runtime.state.value == "FAILED"

    assert await runtime.aclose() is True
    assert runtime.roots == ()
    assert calls == 2


@pytest.mark.asyncio
async def test_successful_child_close_deregisters_from_runtime() -> None:
    class Lease:
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1

    lease = Lease()
    runtime = DaytonaRuntime(child_acquirer=lambda _spec: lease)
    spec = ChildEnvironmentSpec()

    async with runtime.open_child(spec):
        assert len(runtime.children) == 1

    assert runtime.children == ()
    assert lease.close_calls == 1


@pytest.mark.asyncio
async def test_child_runtime_uses_configured_rlm_execution_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def build_factory(**kwargs: object) -> object:
        captured.update(kwargs)
        return lambda _call_index: "child-lease"

    monkeypatch.setattr(recursive_child_runtime, "build_child_runtime_factory", build_factory)
    settings = SimpleNamespace(rlm_execution_timeout_s=37, rlm_max_execution_output_chars=1234)
    resources = SimpleNamespace(
        platform=object(),
        daytona_admission=object(),
        settings=settings,
        dispatcher=None,
    )
    runtime = DaytonaRuntime(resources)
    spec = ChildEnvironmentSpec(
        workspace_id=uuid4(),
        run_id=uuid4(),
        volume_id="volume",
        mount_path="/home/daytona/fleet",
        call_index=4,
    )

    assert await runtime._acquire_child_from_resources(spec) == "child-lease"
    assert captured["execution_timeout_s"] == 37
    assert captured["execution_output_cap"] == 1234
