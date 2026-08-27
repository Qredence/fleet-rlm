"""Bound Workspace Agent provider ``code_run`` timeouts (Mission 03)."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

from fleet_rlm.daytona.workspace_agent.client import (
    WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S,
    run_workspace_agent,
    run_workspace_agent_async,
)


class _RecordingProcess:
    def __init__(self) -> None:
        self.timeouts: list[int | None] = []

    def code_run(self, code: str, **kwargs: object) -> SimpleNamespace:
        del code
        timeout = kwargs.get("timeout")
        self.timeouts.append(timeout if isinstance(timeout, int) or timeout is None else int(timeout))
        return SimpleNamespace(exit_code=0, result='{"ok": true, "kind": "stat"}')


class _HangingProcess:
    """Hostile fake: never completes unless Daytona's timeout bound fires."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.timeouts: list[int | None] = []
        self.settled = threading.Event()

    def code_run(self, code: str, **kwargs: object) -> SimpleNamespace:
        del code
        timeout = kwargs.get("timeout")
        bound = timeout if isinstance(timeout, int) or timeout is None else int(timeout)
        self.timeouts.append(bound)
        self.started.set()
        if bound is None:
            threading.Event().wait()
            raise AssertionError("unbounded hang must not return")
        time.sleep(float(bound))
        self.settled.set()
        raise TimeoutError("workspace agent code_run timed out")


def _agent_arguments(tmp_path: Any) -> dict[str, object]:
    volume_root = tmp_path / "volume"
    root = volume_root / "sessions" / "session" / "workspace"
    root.mkdir(parents=True)
    return {
        "volume_root": str(volume_root),
        "root": str(root),
        "operation": "stat",
        "relative": "note.txt",
        "allow_missing": True,
        "max_bytes": 0,
        "limit": 0,
        "overwrite": False,
        "content_b64": "",
    }


def test_run_workspace_agent_passes_default_provider_timeout(tmp_path: Any) -> None:
    process = _RecordingProcess()
    payload = run_workspace_agent(SimpleNamespace(process=process), **_agent_arguments(tmp_path))
    assert payload["ok"] is True
    assert process.timeouts == [WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S]


def test_run_workspace_agent_ceils_fractional_timeout(tmp_path: Any) -> None:
    process = _RecordingProcess()
    run_workspace_agent(SimpleNamespace(process=process), timeout_s=0.25, **_agent_arguments(tmp_path))
    assert process.timeouts == [1]


@pytest.mark.asyncio
async def test_run_workspace_agent_async_forwards_timeout(tmp_path: Any) -> None:
    process = _RecordingProcess()

    class _AsyncFacade:
        async def code_run(self, code: str, **kwargs: object) -> SimpleNamespace:
            return process.code_run(code, **kwargs)

    payload = await run_workspace_agent_async(
        SimpleNamespace(process=_AsyncFacade()),
        timeout_s=3,
        **_agent_arguments(tmp_path),
    )
    assert payload["ok"] is True
    assert process.timeouts == [3]


@pytest.mark.asyncio
async def test_sync_process_facade_forwards_timeout() -> None:
    from fleet_rlm.daytona.workspace_fs import _SyncProcessFacade

    process = _RecordingProcess()
    facade = _SyncProcessFacade(process)
    await facade.code_run("print(1)", timeout=7)
    assert process.timeouts == [7]


def test_hostile_hanging_code_run_settles_via_timeout(tmp_path: Any) -> None:
    process = _HangingProcess()
    began = time.perf_counter()
    with pytest.raises(TimeoutError, match="timed out"):
        run_workspace_agent(SimpleNamespace(process=process), timeout_s=0.05, **_agent_arguments(tmp_path))
    elapsed = time.perf_counter() - began
    assert process.started.is_set()
    assert process.settled.is_set()
    assert process.timeouts == [1]
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_prepared_run_aclose_waits_for_timed_out_workspace_agent_promotion(
    tmp_path: Any,
) -> None:
    from fleet_rlm.chat.post_commit_memory import OwnedPostCommitMemoryPromotion
    from fleet_rlm.chat.run_preparation import PreparedRun, _PreparedRunResources

    process = _HangingProcess()
    release_count = 0
    resources_released = asyncio.Event()

    def hanging_promotion(_candidates: tuple[object, ...]) -> object:
        run_workspace_agent(SimpleNamespace(process=process), timeout_s=0.05, **_agent_arguments(tmp_path))
        return object()

    async def release_resources() -> None:
        nonlocal release_count
        release_count += 1
        resources_released.set()

    promotion = OwnedPostCommitMemoryPromotion(hanging_promotion)
    attempt = await promotion.promote((object(),), timeout_s=0.01)
    assert attempt.status == "deadline_exceeded"
    assert process.started.wait(1.0)

    prepared = PreparedRun(
        execution=cast("Any", object()),
        artifact_sink=cast("Any", object()),
        _resources=_PreparedRunResources((release_resources,)),
        post_commit_memory_promotion=promotion,
    )
    close = asyncio.create_task(prepared.aclose())
    await asyncio.sleep(0)
    assert not close.done()
    assert not resources_released.is_set()

    await close
    assert resources_released.is_set()
    assert release_count == 1
    assert process.settled.is_set()
