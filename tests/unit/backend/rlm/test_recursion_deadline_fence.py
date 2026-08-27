"""P39b deadline fence for the native child acall wait (VAL-REC-017).

The one absolute Turn deadline must be enforced even while the synchronous
DSPy Tool is blocked awaiting the child future (orchestrator note from the
p35b review): a child native runtime that never completes cannot hold the
recursive Tool past the deadline, and a child that swallows cancellation is
retained under cleanup ownership instead of blocking the parent.

All lanes are behavior-only: they observe the bounded typed timeout failure,
lease settlement, and ownership join through the public Tool/executor
surface, never through private symbol names.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import dspy
import pytest

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLease
from fleet_rlm.rlm.events import Status
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.rlm.recursion import (
    ChildRuntimeCleanupError,
    RecursiveRLMExecutor,
    RecursiveRLMOptions,
)


class _Recorder:
    def __init__(self) -> None:
        self.call_indexes: list[int] = []
        self.leases: list[ChildRuntimeLease] = []
        self.interpreters: dict[int, DaytonaCodeInterpreter] = {}
        self.close_calls: dict[int, int] = {}

    def factory(self, call_index: int) -> ChildRuntimeLease:
        self.call_indexes.append(call_index)
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        self.interpreters[call_index] = interpreter

        def close() -> None:
            self.close_calls[call_index] = self.close_calls.get(call_index, 0) + 1
            interpreter.shutdown()

        lease = ChildRuntimeLease(
            interpreter,
            f"fence-child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            close,
        )
        self.leases.append(lease)
        return lease


def _executor(
    recorder: _Recorder,
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

    recorder = _Recorder()
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
    with pytest.raises(TimeoutError, match="recursive child deadline exceeded"):
        executor.tool(prompt="hanging child")
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
    recorder = _Recorder()
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
    with pytest.raises(TimeoutError, match="recursive child deadline exceeded"):
        executor.tool(prompt="swallowing child")
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

    recorder = _Recorder()

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
        executor.batched_tool(prompts=["hanging"])
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

    recorder = _Recorder()

    class PromptChild:
        async def acall(self, interpreter: Any = None, *, prompt: str, **_kwargs: object) -> dspy.Prediction:
            del interpreter
            await asyncio.sleep(0)
            return dspy.Prediction(answer=f"echo:{prompt}", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: PromptChild())
    executor = _executor(recorder, deadline=time.monotonic() + 10)

    assert executor.tool(prompt="fast child") == "echo:fast child"
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

    recorder = _Recorder()

    class LmDeadlineChild:
        async def acall(self, interpreter: Any = None, *, prompt: str, **_kwargs: object) -> Any:
            del interpreter, prompt
            await asyncio.sleep(0)
            raise TimeoutError("recursive child LM deadline exceeded")

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: LmDeadlineChild())
    executor = _executor(recorder, deadline=time.monotonic() + 10)

    with pytest.raises(TimeoutError, match="recursive child LM deadline exceeded"):
        executor.tool(prompt="lm deadline child")
    assert recorder.close_calls.get(1) == 1
    executor.wait_owned()
    executor.raise_if_cleanup_failed()
