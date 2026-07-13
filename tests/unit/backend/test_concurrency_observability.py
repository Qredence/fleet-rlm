"""impl-17 concurrency + impl-20 observability."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from fleet_rlm.artifacts.store import LocalArtifactStore
from fleet_rlm.chat.turn_coordinator import ephemeral_lease
from fleet_rlm.daytona.active_leases import (
    ActiveLeaseConflictError,
    ActiveLeaseRegistry,
    get_active_lease_registry,
    set_active_lease_registry,
)
from fleet_rlm.daytona.volume_writes import (
    VolumeWriteCoordinator,
)
from fleet_rlm.observability import (
    InMemoryTurnStore,
    TurnTrace,
    apply_event_to_trace,
    safe_export,
)
from fleet_rlm.rlm.budgets import RLMBudget
from fleet_rlm.rlm.context import RLMTurnContext
from fleet_rlm.rlm.events import RuntimeEventKind
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.sessions.locks import SessionLockRegistry


@pytest.fixture(autouse=True)
def _fresh_lease_registry() -> Any:
    reg = ActiveLeaseRegistry()
    set_active_lease_registry(reg)
    yield reg
    set_active_lease_registry(ActiveLeaseRegistry())


def test_active_lease_conflict() -> None:
    reg = get_active_lease_registry()
    sid = uuid4()
    r1, r2 = uuid4(), uuid4()
    reg.acquire(sid, r1)
    with pytest.raises(ActiveLeaseConflictError):
        reg.acquire(sid, r2)
    reg.release(sid, r1)
    reg.acquire(sid, r2)  # ok after release


@pytest.mark.asyncio
async def test_session_lock_serializes_mutations() -> None:
    locks = SessionLockRegistry()
    order: list[int] = []

    async def task(n: int) -> None:
        async with locks.hold(sid):
            order.append(n)
            await asyncio.sleep(0.02)
            order.append(n + 10)

    sid = uuid4()
    await asyncio.gather(task(1), task(2))
    # nested pairs complete without interleaving across critical section
    assert order in ([1, 11, 2, 12], [2, 12, 1, 11])


@pytest.mark.asyncio
async def test_volume_write_coordinator_serializes() -> None:
    coord = VolumeWriteCoordinator()
    order: list[str] = []
    sid = uuid4()

    async def writer(tag: str) -> None:
        async with coord.hold(sid, resource="artifacts"):
            order.append(f"start-{tag}")
            await asyncio.sleep(0.02)
            order.append(f"end-{tag}")

    await asyncio.gather(writer("a"), writer("b"))
    # no interleaving of start/end pairs from different writers
    assert order.index("start-a") < order.index("end-a")
    assert order.index("start-b") < order.index("end-b")
    # one fully completes before the other starts
    a_first = order.index("end-a") < order.index("start-b")
    b_first = order.index("end-b") < order.index("start-a")
    assert a_first or b_first

    path = VolumeWriteCoordinator.run_scoped_path(sid, uuid4(), "artifacts", "x.md")
    assert path.startswith("sessions/")
    assert "/runs/" in path


def test_concurrent_artifact_creates_unique_ids(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path, max_bytes=1024 * 1024)
    user, ws, sid = uuid4(), uuid4(), uuid4()
    ids = set()
    for i in range(20):
        ref = store.create(
            user_id=user,
            workspace_id=ws,
            session_id=sid,
            run_id=uuid4(),
            kind="text",
            content=f"body-{i}",
        )
        ids.add(ref.id)
    assert len(ids) == 20


def test_observability_trace_and_safe_export() -> None:
    store = InMemoryTurnStore()

    class Boom:
        def export(self, trace: TurnTrace) -> None:
            raise RuntimeError("exporter down")

    trace = TurnTrace(
        run_id=uuid4(),
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        sandbox_id="sbx-1",
        volume_id="vol-1",
    )
    apply_event_to_trace(trace, "skill.loaded", {"skill_id": "s1"})
    apply_event_to_trace(trace, "attachment.read", {"attachment_id": "a1"})
    apply_event_to_trace(trace, "artifact.created", {"artifact_id": "art1"})
    apply_event_to_trace(trace, "usage", {"usage": {"tokens": 3}})
    apply_event_to_trace(trace, "run.completed", {"status": "completed", "duration_ms": 12})
    assert trace.skill_ids == ["s1"]
    assert trace.attachment_ids == ["a1"]
    assert trace.artifact_ids == ["art1"]
    assert trace.usage["tokens"] == 3
    assert trace.terminal_status == "completed"
    safe_export(Boom(), trace)  # must not raise
    safe_export(store, trace)
    assert len(store.traces) == 1
    public = store.traces[0].to_public_dict()
    assert public["sandbox_id"] == "sbx-1"
    assert "password" not in str(public)


@pytest.mark.asyncio
async def test_runner_records_trace_via_exporter() -> None:
    store = InMemoryTurnStore()

    class Factory:
        def create(self, **kwargs: Any) -> Any:
            def rlm(*, request: str, **_kwargs: Any) -> Any:
                return MagicMock(answer="ok", get_lm_usage=MagicMock(return_value={"n": 1}))

            return rlm

    context = RLMTurnContext(
        run_id=uuid4(),
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        request="hi",
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        budget=RLMBudget(max_wall_seconds=30),
        lease=ephemeral_lease(MagicMock()),
    )
    # attach sandbox-like attrs
    context.lease.sandbox_id = "sbx"  # type: ignore[attr-defined]
    context.lease.volume_id = "vol"  # type: ignore[attr-defined]
    context.lease.mount_path = "/home/daytona/fleet"  # type: ignore[attr-defined]

    stream = RLMRunner(factory=Factory(), turn_exporter=store).stream(context)
    events = [e async for e in stream]
    assert RuntimeEventKind.RUN_COMPLETED not in {e.kind for e in events}
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "completed"
    assert len(store.traces) == 1
    exported = store.traces[0]
    assert exported.terminal_status == "completed"
    assert exported.finished_at is not None
    assert exported.model_profiles.keys() == {"root", "sub"}
    assert exported.budget_limits["tool_calls"] == context.budget.max_tool_calls
    assert exported.usage["estimated_cost"] is None
