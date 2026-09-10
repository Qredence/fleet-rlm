"""Behavior contracts for recursion fencing."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.events import RunCompleted, Status
from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
from fleet_rlm.rlm.recursion import (
    ChildRuntimeCleanupError,
    RecursiveRLMOptions,
)
from fleet_rlm.rlm.runtime import (
    DelegationPolicy,
    ExecutionRuntime,
    RLMExecutionContext,
    RLMRunner,
    RunIdentity,
    SessionView,
)
from fleet_rlm.sessions.models import TurnAccess
from tests.support.recursion_scheduler import RecursiveRLMExecutor
from tests.unit.backend.rlm.fakes import ChildLeaseRecorder, EmptyCapabilities


def _executor(
    recorder: ChildLeaseRecorder,
    *,
    deadline: float,
    options: RecursiveRLMOptions | None = None,
    observer=None,
) -> RecursiveRLMExecutor:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    return RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=options or RecursiveRLMOptions(max_calls=2),
        child_runtime_factory=recorder.factory,
        deadline=deadline,
        observer=observer,
    )


def test_child_acall_wait_is_fenced_by_the_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native child whose async invocation never completes cannot hold the
    synchronous recursive Tool past the one absolute deadline: the call fails
    with the bounded timeout classification within a bounded tolerance, the
    child lease still settles exactly once, and ownership observes clean."""
    import fleet_rlm.rlm.recursion as recursive_calls

    recorder = ChildLeaseRecorder()
    entered = threading.Event()

    class HangingChild:
        async def acall(self, interpreter: Any = None, *, prompt: str, **_kwargs: object) -> dspy.Prediction:
            del interpreter, prompt
            entered.set()
            await asyncio.sleep(30)
            raise AssertionError("the fence must cancel the hanging child wait")

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: HangingChild())
    deadline = time.monotonic() + 0.2
    events: list[object] = []
    executor = _executor(recorder, deadline=deadline, observer=events.append)

    began = time.monotonic()
    outcome = executor.tool(capsule={"task": "hanging child"})
    assert outcome["status"] == "timed_out"
    elapsed = time.monotonic() - began

    # Bounded by the one absolute deadline, never unbounded.
    assert entered.is_set()
    assert 0.1 <= elapsed < 1.5
    # The lease settled exactly once through the lifecycle owner.
    assert recorder.close_calls.get(1) == 1
    assert recorder.interpreters[1]._shutdown
    # The failure surfaced the bounded timeout classification, with no answer.
    failed = [event for event in events if isinstance(event, Status) and event.status == "child_failed"]
    assert len(failed) == 1
    assert failed[0].message is not None
    assert "failure_category=timeout" in failed[0].message
    assert "cleanup_status=completed" in failed[0].message
    assert executor.summary().termination_modes == ("child_error",)
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


@pytest.mark.asyncio
async def test_cancellation_swallowing_child_is_retained_not_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that swallows cancellation cannot block the Tool past the
    deadline: the wait is retained under cleanup ownership (observable as
    pending), the lease closes anyway, and the owned join settles only once
    the retained child future completes."""
    import fleet_rlm.rlm.recursion as recursive_calls

    monkeypatch.setattr(recursive_calls, "_CHILD_FENCE_SETTLE_GRACE_S", 0.05)
    recorder = ChildLeaseRecorder()
    entered = threading.Event()
    release = threading.Event()

    class SwallowingChild:
        def __init__(self) -> None:
            self.cancelled = 0

        async def acall(self, interpreter: Any = None, *, prompt: str, **_kwargs: object) -> dspy.Prediction:
            del interpreter, prompt
            entered.set()
            # Swallow every cancellation; unwind only on explicit release,
            # which models a child that refuses to honor cooperative cancel.
            while not release.is_set():
                try:
                    await asyncio.sleep(0.02)
                except asyncio.CancelledError:
                    self.cancelled += 1
                    continue
            return dspy.Prediction(answer="late", trajectory=[])

    child = SwallowingChild()
    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: child)
    deadline = time.monotonic() + 0.1
    executor = _executor(recorder, deadline=deadline)

    began = time.monotonic()
    with pytest.raises(ChildRuntimeCleanupError, match="pending"):
        executor.tool(capsule={"task": "swallowing child"})
    elapsed = time.monotonic() - began

    # The fence fired and the Tool returned bounded; cancellation was sent
    # but swallowed, so the child was retained, not joined inline.
    assert entered.is_set()
    assert elapsed < 1.5
    assert child.cancelled >= 1
    # The lease closed even though the child future was still pending.
    assert recorder.close_calls.get(1) == 1
    # Ownership remains pending while the swallowed child is retained.
    with pytest.raises(ChildRuntimeCleanupError, match="cleanup is still pending"):
        executor.raise_if_cleanup_failed()
    # The owned join completes only after the retained child future settles.
    release.set()
    await asyncio.to_thread(executor.wait_owned)
    executor.raise_if_cleanup_failed()


@pytest.mark.asyncio
async def test_fenced_child_wait_preserves_batch_deadline_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In a batch, the fenced child wait returns at the absolute deadline and
    the batch settles all-or-nothing with the bounded timeout cause; the
    hanging child's lease still settles and ownership joins clean."""
    import fleet_rlm.rlm.recursion as recursive_calls

    recorder = ChildLeaseRecorder()

    class HangingChild:
        async def acall(self, interpreter: Any = None, *, prompt: str, **_kwargs: object) -> dspy.Prediction:
            del interpreter, prompt
            await asyncio.sleep(30)
            raise AssertionError("the fence must cancel the hanging child wait")

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: HangingChild())
    deadline = time.monotonic() + 0.2
    executor = _executor(recorder, deadline=deadline)

    began = time.monotonic()
    from fleet_rlm.rlm.recursion import RecursiveBatchError

    with pytest.raises((TimeoutError, RecursiveBatchError)) as raised:
        executor.batched_tool(capsules=[{"task": task} for task in ["hanging"]])
    if isinstance(raised.value, RecursiveBatchError):
        assert isinstance(raised.value.__cause__, TimeoutError)
    assert time.monotonic() - began < 2.0
    # The hanging child's lease settles through ownership, not through an
    # unbounded Tool block.
    await asyncio.to_thread(executor.wait_owned)
    executor.raise_if_cleanup_failed()
    assert recorder.close_calls.get(1) == 1


def test_completed_child_is_not_disturbed_by_the_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that completes within the deadline returns its answer through
    the same fenced seam without timeout classification."""
    import fleet_rlm.rlm.recursion as recursive_calls

    recorder = ChildLeaseRecorder()

    class PromptChild:
        async def acall(self, interpreter: Any = None, *, prompt: str, **_kwargs: object) -> dspy.Prediction:
            del interpreter
            await asyncio.sleep(0)
            return dspy.Prediction(answer=f"echo:{json.loads(prompt)['task']}", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: PromptChild())
    executor = _executor(recorder, deadline=time.monotonic() + 10)

    assert executor.tool(capsule={"task": "fast child"})["answer"] == "echo:fast child"
    assert recorder.close_calls.get(1) == 1
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


def test_child_lm_deadline_error_keeps_its_own_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that fails with its own deadline-bound LM error (raised inside
    the child future) is not relabelled by the fence: the original error
    propagates unchanged while the lease still settles."""
    import fleet_rlm.rlm.recursion as recursive_calls

    recorder = ChildLeaseRecorder()

    class LmDeadlineChild:
        async def acall(self, interpreter: Any = None, *, prompt: str, **_kwargs: object) -> Any:
            del interpreter, prompt
            await asyncio.sleep(0)
            raise TimeoutError("recursive child LM deadline exceeded")

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: LmDeadlineChild())
    executor = _executor(recorder, deadline=time.monotonic() + 10)

    outcome = executor.tool(capsule={"task": "lm deadline child"})
    assert outcome["status"] == "timed_out"
    assert outcome["error_category"] == "timeout"
    assert recorder.close_calls.get(1) == 1
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


def _authorized_executor(
    recorder: ChildLeaseRecorder,
    root_actions: list[dict[str, str]],
    *,
    authority: RunAuthority,
    options: RecursiveRLMOptions | None = None,
) -> RecursiveRLMExecutor:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(root_actions, adapter=adapter)
    sub = dspy.utils.DummyLM([{"answer": "fallback"}], adapter=adapter)
    return RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=options or RecursiveRLMOptions(),
        child_runtime_factory=recorder.factory,
        deadline=time.monotonic() + 30,
        is_authorized=lambda: not authority.revoked,
    )


def test_val_rec_015_claim_loss_before_allocation_performs_no_reservation_or_acquisition() -> None:
    """VAL-REC-015: claim loss before allocation rejects the recursive call at
    the authorization fence with no reservation, no call index, no factory
    acquisition, and no budget mutation."""
    authority = RunAuthority()
    recorder = ChildLeaseRecorder()
    executor = _authorized_executor(
        recorder,
        [{"reasoning": "submit", "code": "SUBMIT(answer='never-runs')"}],
        authority=authority,
    )
    authority.revoke()

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(capsule={"task": "claimed slice"})

    assert recorder.call_indexes == []
    summary = executor.summary()
    assert summary.call_count == 0
    assert summary.delegated_prompt_chars == 0
    assert summary.recursive_batch_calls == 0
    assert summary.delegation_metrics.recursive_child_calls == 0


def test_val_rec_015_claim_loss_rejects_every_subsequent_recursive_call() -> None:
    """VAL-REC-015: after claim loss, no subsequent recursive call may
    reserve or acquire: the first call completed while the claim was held,
    and every later call (single and batched) is rejected at the fence."""
    authority = RunAuthority()
    recorder = ChildLeaseRecorder()
    executor = _authorized_executor(
        recorder,
        [{"reasoning": "submit", "code": "SUBMIT(answer='held-ok')"}],
        authority=authority,
        options=RecursiveRLMOptions(max_calls=4),
    )

    assert executor.tool(capsule={"task": "held slice"})["answer"] == "held-ok"
    authority.revoke()

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(capsule={"task": "late single"})
    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.batched_tool(capsules=[{"task": task} for task in ["late batch"]])

    # The completed call is the only reservation and acquisition ever made.
    assert recorder.call_indexes == [1]
    assert executor.summary().call_count == 1
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


@pytest.mark.asyncio
async def test_val_rec_015_claim_loss_during_blocked_child_discards_result_and_fails_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-015 (Runner scope): claim loss while a child is blocked
    discards the child's late result through the same authorization fence,
    produces a failed parent outcome with no successful structured result or
    terminal completion, performs no further allocation, and settles the
    acquired lease exactly once before the claim is released."""
    import fleet_rlm.rlm.recursion as recursive_calls

    started = threading.Event()
    release = threading.Event()

    class BlockedChild:
        async def acall(self, *, interpreter: object, prompt: str) -> dspy.Prediction:
            del interpreter, prompt
            started.set()
            await asyncio.to_thread(release.wait, 10)
            # This answer is produced after the claim was lost and must be
            # discarded by the fence instead of settling as a success.
            return dspy.Prediction(answer="late-claimed-answer", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockedChild())

    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [{"reasoning": "delegate", "code": "answer = rlm_query(capsule={'task': 'claimed slice'})"}],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    recorder = ChildLeaseRecorder()
    authority = RunAuthority()

    async def never_cancelled() -> bool:
        # Claim loss is modeled purely as authority revocation: the
        # cancellation probe never fires in this lane.
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(
            run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4()), authority=authority
        ),
        session=SessionView(
            request="claim loss during recursive child",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=3, max_llm_calls=4),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=never_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=2),
            child_runtime_factory=recorder.factory,
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    events: list[object] = []

    async def consume() -> None:
        async for event in stream:
            events.append(event)

    consume_task = asyncio.create_task(consume())
    assert await asyncio.to_thread(started.wait, 10)
    # Claim loss revokes the shared authority while the child is blocked.
    assert not authority.revoked
    authority.revoke()
    release.set()
    await asyncio.wait_for(consume_task, timeout=15)

    # Failed parent outcome: never completed, never a structured success.
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "failed"
    assert stream.outcome.prediction is None
    assert not any(isinstance(event.detail, RunCompleted) for event in events)
    # The late child answer was discarded: it never reached any event.
    assert "late-claimed-answer" not in repr(events)
    # Exactly one acquisition; no further allocation after claim loss.
    assert recorder.call_indexes == [1]
    # The acquired lease settled exactly once before the claim released.
    await asyncio.wait_for(stream.wait_owned(), timeout=10)
    assert recorder.close_calls.get(1) == 1
    assert all(lease.interpreter._shutdown for lease in recorder.leases)
