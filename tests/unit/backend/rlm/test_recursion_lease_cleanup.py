"""P39 child lease/cleanup ownership contract lanes at the RLM execution scope.

Behavior-only evidence that the recursive executor and Runner honor the
contracted child-runtime owner (deterministic owner lanes live in
``tests/unit/backend/daytona/test_child_lease_cleanup_ownership.py``):

- VAL-REC-012: batch cancellation stops queued acquisition before any lease
  is acquired while a running sibling stays owned until cleanup settles.
- VAL-REC-016: parent/caller cancellation uses the same authorization fence:
  queued siblings never acquire, the terminal outcome is cancellation (never
  timeout or success), and the owned close join settles only after the
  running child's cleanup completes.
- VAL-REC-017: one absolute deadline covers model forks, batch join, and
  acquisition; an expired deadline performs no allocation; a blocked batch
  reports ``recursive child batch deadline exceeded`` bounded by the same
  deadline; the parent outcome is timeout rather than success.
- VAL-REC-026: DSPy never shuts down caller-owned child interpreters through
  native completion, extraction fallback, generated-code error, terminal
  interpreter failure, or cancellation; the Fleet lease owner shuts the
  interpreter down exactly once. This lane shares its evidence lane with
  VAL-RLM-007 (Root-scope shutdown authority in ``test_dspy_contract.py``);
  the child-scope assertion remains independently claimable here.
- VAL-REC-030: a cleanup failure after a syntactically valid child answer is
  recorded as fatal, re-observed by the executor without rerunning cleanup,
  and prevents child and Root success (Runner scope:
  ``test_recursive_runner_flow.py::test_failed_child_cleanup_prevents_successful_root_outcome``).
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from uuid import uuid4

import dspy
import pytest
from dspy import CodeExecutionError, CodeInterpreterError

from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLease
from fleet_rlm.rlm.compat_3_3_1 import wrap_final_output
from fleet_rlm.rlm.events import RunCompleted, Status
from fleet_rlm.rlm.program import (
    RLMModelBundle,
    RLMOptions,
    build_native_rlm,
)
from fleet_rlm.rlm.recursion import (
    ChildRuntimeAuthorizationError,
    ChildRuntimeCleanupError,
    RecursiveBatchError,
    RecursiveRLMExecutor,
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
from tests.unit.backend.rlm.fakes import EmptyCapabilities


class _CountingInterpreter:
    """Interpreter double counting DSPy-originated shutdown calls.

    ``behavior`` selects the execution contract:

    - ``submit``: every action submits a valid typed answer.
    - ``code_error_first``: the first action raises a generated-code error,
      later actions submit.
    - ``never_submit``: actions produce ordinary output only (drives the
      extraction fallback).
    - ``interpreter_error``: every action raises a terminal interpreter error.
    """

    def __init__(self, behavior: str = "submit") -> None:
        self.tools: dict[str, object] = {}
        self.behavior = behavior
        self.shutdown_calls = 0
        self.execute_calls = 0

    def start(self) -> None:
        return None

    def execute(self, code: str, variables: dict[str, object] | None = None) -> object:
        del code, variables
        self.execute_calls += 1
        if self.behavior == "code_error_first" and self.execute_calls == 1:
            raise CodeExecutionError("boom: generated code failed")
        if self.behavior == "never_submit":
            return "plain output, no submit"
        if self.behavior == "interpreter_error":
            raise CodeInterpreterError("interpreter process failed")
        answer = "recovered" if self.behavior == "code_error_first" else "child-ok"
        return wrap_final_output({"answer": answer})

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class _Recorder:
    """Records factory interactions: created indexes, leases, close counts."""

    def __init__(self) -> None:
        self.call_indexes: list[int] = []
        self.leases: list[ChildRuntimeLease] = []
        self.close_calls: dict[int, int] = {}
        self.interpreters: dict[int, _CountingInterpreter] = {}

    def factory(self, call_index: int, *, behavior: str = "submit") -> ChildRuntimeLease:
        self.call_indexes.append(call_index)
        interpreter = self.interpreters.get(call_index) or _CountingInterpreter(behavior)
        self.interpreters[call_index] = interpreter

        def close() -> None:
            self.close_calls[call_index] = self.close_calls.get(call_index, 0) + 1
            interpreter.shutdown()

        lease = ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            close,
        )
        self.leases.append(lease)
        return lease


def _executor(
    root_actions: list[dict[str, str]],
    recorder: _Recorder,
    *,
    options: RecursiveRLMOptions | None = None,
    deadline: float | None = None,
    observer: Callable[[object], None] | None = None,
    is_authorized: Callable[[], bool] | None = None,
    behavior: str = "submit",
    child_runtime_factory: Callable[[int], ChildRuntimeLease] | None = None,
) -> RecursiveRLMExecutor:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(root_actions, adapter=adapter)
    sub = dspy.utils.DummyLM([{"answer": "fallback"}], adapter=adapter)

    def factory(call_index: int) -> ChildRuntimeLease:
        return recorder.factory(call_index, behavior=behavior)

    return RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=options or RecursiveRLMOptions(),
        child_runtime_factory=child_runtime_factory or factory,
        deadline=deadline if deadline is not None else time.monotonic() + 30,
        observer=observer,
        is_authorized=is_authorized,
    )


def test_val_rec_012_first_failure_cancels_queued_acquisition_before_any_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-012 (first-failure trigger): with one worker slot, a
    fast-failing first child cancels the queued sibling before lease
    acquisition; the batch fails all-or-nothing with the child's cause and
    ownership settles clean."""
    import fleet_rlm.rlm.recursion as recursive_calls

    recorder = _Recorder()
    first_failure = threading.Event()
    real_run_reserved_batch = recursive_calls.run_reserved_batch

    def gated_run_reserved_batch(
        reservations,
        *,
        execute,
        deadline_monotonic,
        max_parallel,
        on_retain_running,
    ):
        """Keep a queued worker behind the first-failure cancellation fence.

        With one pool worker, the queued future can otherwise start in the
        scheduler gap between the first worker raising and the parent thread
        observing that failure. The production scheduler remains unchanged;
        this test-only gate makes the cancellation observation deterministic.
        """

        def gated_execute(reservation, batch_cancelled):
            if reservation.call_index != 1:
                assert first_failure.wait(5), "first child failure was not observed"
                remaining = max(0.0, deadline_monotonic - time.monotonic())
                assert batch_cancelled.wait(remaining), "batch cancellation was not observed"
            return execute(reservation, batch_cancelled)

        return real_run_reserved_batch(
            reservations,
            execute=gated_execute,
            deadline_monotonic=deadline_monotonic,
            max_parallel=max_parallel,
            on_retain_running=on_retain_running,
        )

    monkeypatch.setattr(recursive_calls, "run_reserved_batch", gated_run_reserved_batch)

    class FailingChild:
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            if prompt == "fail":
                first_failure.set()
                raise ValueError("provider failure")
            raise AssertionError("queued sibling must never execute")

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: FailingChild())
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        recorder,
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=1),
    )

    began = time.monotonic()
    with pytest.raises(RecursiveBatchError) as raised:
        executor.batched_tool(prompts=["fail", "queued"])
    assert time.monotonic() - began < 5

    # One all-or-nothing batch failure caused by the first child failure.
    assert isinstance(raised.value.__cause__, ValueError)
    # Only the running child reached the factory; the queued sibling never
    # acquired a lease, and the failed child's lease already settled.
    assert recorder.call_indexes == [1]
    assert recorder.close_calls == {1: 1}
    # Ownership observation settles clean: nothing pending, nothing failed.
    executor.wait_owned()
    executor.raise_if_cleanup_failed()
    summary = executor.summary()
    assert summary.recursive_children_started == 1
    assert summary.call_count == 2


def test_val_rec_012_deadline_cancels_queued_acquisition_and_join_waits_for_running_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-012 (deadline trigger): a blocked first worker with queued
    siblings returns at the deadline; only the running index reaches the
    factory, lease-close observation reports pending ownership, and the owned
    join completes only after the running lease closes."""
    import fleet_rlm.rlm.recursion as recursive_calls

    started = threading.Event()
    release = threading.Event()
    recorder = _Recorder()

    class BlockingChild:
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            del prompt
            started.set()
            assert release.wait(5)
            return dspy.Prediction(answer="late", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockingChild())
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        recorder,
        options=RecursiveRLMOptions(max_calls=3, max_parallel_children=1),
        deadline=time.monotonic() + 0.1,
    )

    began = time.monotonic()
    with pytest.raises(TimeoutError, match="batch deadline exceeded"):
        executor.batched_tool(prompts=["blocked", "queued-a", "queued-b"])
    assert time.monotonic() - began < 1.5
    assert started.is_set()
    # Only the running child acquired a lease; queued siblings never did.
    assert recorder.call_indexes == [1]
    # Lease-close observation reports still-pending ownership before the
    # running sibling settles.
    with pytest.raises(ChildRuntimeCleanupError, match="cleanup is still pending"):
        executor.raise_if_cleanup_failed()

    release.set()
    # The owned join completes only after the running lease closes.
    executor.wait_owned()
    executor.raise_if_cleanup_failed()
    assert recorder.close_calls.get(1) == 1


def test_val_rec_016_revoked_authority_cancels_queued_sibling_and_retains_running_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-016 (executor fence): revoking Run authority while one child
    runs and another is queued cancels the queued acquisition, discards the
    running child's answer through the same fence, yields no successful batch
    output, and still settles the running lease's cleanup exactly once."""
    import fleet_rlm.rlm.recursion as recursive_calls

    authority = RunAuthority()
    started = threading.Event()
    release = threading.Event()
    recorder = _Recorder()
    events: list[object] = []

    class BlockingChild:
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            del prompt
            started.set()
            assert release.wait(5)
            return dspy.Prediction(answer="would-have-been-kept", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockingChild())
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        recorder,
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=1),
        observer=events.append,
        is_authorized=lambda: not authority.revoked,
    )
    result: dict[str, BaseException] = {}

    def run_batch() -> None:
        try:
            executor.batched_tool(prompts=["running", "queued"])
        except BaseException as exc:
            result["error"] = exc

    worker = threading.Thread(target=run_batch)
    worker.start()
    assert started.wait(5)
    # Revocation reaches every child boundary through the shared fence.
    authority.revoke()
    release.set()
    worker.join(5)
    assert not worker.is_alive()

    # One all-or-nothing failure with the authorization cause; no answers.
    assert isinstance(result.get("error"), RecursiveBatchError)
    assert isinstance(result["error"].__cause__, ChildRuntimeAuthorizationError)
    # The queued sibling never reached lease acquisition; only index 1 did.
    assert recorder.call_indexes == [1]
    # No successful child output was observed.
    statuses = [event for event in events if isinstance(event, Status)]
    assert all(event.status != "child_completed" for event in statuses)
    # The running child remained owned until its cleanup settled exactly once.
    executor.wait_owned()
    executor.raise_if_cleanup_failed()
    assert recorder.close_calls.get(1) == 1


@pytest.mark.asyncio
async def test_val_rec_016_runner_cancellation_terminal_outcome_with_same_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-016 (Runner scope): cancelling while one child runs and another
    is queued revokes authority before new acquisition, produces one cancelled
    terminal outcome (never timeout or success), and the owned join settles
    only after the running child's cleanup completes with its permit
    restored."""
    import fleet_rlm.rlm.recursion as recursive_calls

    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "batch", "code": "answers = rlm_query_batched(prompts=['alpha', 'beta'])"},
            {"reasoning": "submit", "code": "SUBMIT(answer='root-done')"},
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)

    started = threading.Event()
    release = threading.Event()
    recorder = _Recorder()
    permits_held = 0
    permits_released = 0
    permit_lock = threading.Lock()

    def counting_factory(call_index: int) -> ChildRuntimeLease:
        nonlocal permits_held
        with permit_lock:
            permits_held += 1
        lease = recorder.factory(call_index)
        original_close = lease._close

        def close() -> None:
            nonlocal permits_held, permits_released
            original_close()
            with permit_lock:
                permits_released += 1
                permits_held -= 1

        lease._close = close
        return lease

    class BlockingAlphaChild:
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            if prompt == "alpha":
                started.set()
                assert release.wait(10)
            return dspy.Prediction(answer="late", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockingAlphaChild())

    authority = RunAuthority()
    cancel_requested = False

    async def cancellation_probe() -> bool:
        return cancel_requested

    context = RLMExecutionContext(
        identity=RunIdentity(
            run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4()), authority=authority
        ),
        session=SessionView(
            request="cancel during recursive batch",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=4, max_llm_calls=4),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=cancellation_probe,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=2, max_parallel_children=1),
            child_runtime_factory=counting_factory,
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
    # Cancellation revokes authority through the same fence the recursive
    # executor checks at every child boundary.
    cancel_requested = True
    authority.revoke()
    await asyncio.wait_for(consume_task, timeout=10)

    assert stream.outcome is not None
    # One cancellation terminal outcome: never timeout, never success.
    assert stream.outcome.terminal_status == "cancelled"
    assert stream.outcome.prediction is None
    assert not any(isinstance(event.detail, RunCompleted) for event in events)
    # The queued sibling never acquired; only the running child did.
    assert recorder.call_indexes == [1]
    # The owned close join blocks until the running cleanup settles.
    release.set()
    await asyncio.wait_for(stream.wait_owned(), timeout=10)
    assert recorder.close_calls.get(1) == 1
    # Admission restored to capacity; no leaked ownership.
    assert permits_held == 0
    assert permits_released == 1


def test_val_rec_017_expired_deadline_performs_no_allocation() -> None:
    """VAL-REC-017: an already-expired Turn deadline performs no reservation,
    no factory call, and no lease acquisition for either recursive surface."""
    recorder = _Recorder()
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        recorder,
        deadline=time.monotonic() - 1,
    )

    with pytest.raises(TimeoutError, match="deadline exceeded"):
        executor.tool(prompt="late child request")
    with pytest.raises(TimeoutError, match="deadline exceeded"):
        executor.batched_tool(prompts=["late"])

    assert recorder.call_indexes == []
    assert recorder.leases == []
    assert executor.summary().call_count == 0
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


def test_val_rec_017_one_absolute_deadline_covers_fork_and_batch_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-017: the model fork and the batch join both receive the one
    absolute Root deadline; a blocked batch reports the deadline error within
    a bounded tolerance and the retained child still settles."""
    import fleet_rlm.rlm.recursion as recursive_calls

    started = threading.Event()
    release = threading.Event()
    recorder = _Recorder()
    fork_deadlines: list[float] = []
    real_fork = RLMModelBundle.fork_for_child

    def spy_fork(self: RLMModelBundle, *, deadline: float) -> RLMModelBundle:
        fork_deadlines.append(deadline)
        return real_fork(self, deadline=deadline)

    monkeypatch.setattr(RLMModelBundle, "fork_for_child", spy_fork)

    class BlockingChild:
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            del prompt
            started.set()
            assert release.wait(5)
            return dspy.Prediction(answer="late", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockingChild())
    # Leave enough spawn slack for full-suite coverage + xdist load. The child
    # blocks on `release.wait(5)`, so widening the deadline only removes the
    # scheduler race without changing what is proven.
    deadline = time.monotonic() + 2.0
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        recorder,
        deadline=deadline,
    )

    began = time.monotonic()
    with pytest.raises(TimeoutError, match="recursive child batch deadline exceeded"):
        executor.batched_tool(prompts=["blocked"])
    elapsed = time.monotonic() - began
    assert started.is_set()
    # Bounded by the one absolute deadline, with a small tolerance.
    assert 0.05 <= elapsed < 3.0
    # The child's model fork received exactly the Root deadline.
    assert fork_deadlines == [deadline]

    release.set()
    executor.wait_owned()
    executor.raise_if_cleanup_failed()
    assert recorder.close_calls.get(1) == 1


class _RecordingLM:
    """Minimal copyable LM double that records the kwargs of every call."""

    def __init__(self) -> None:
        self.history: list[object] = []
        self.kwargs: dict[str, object] = {}
        self.calls: list[dict[str, object]] = []
        self.num_retries: int | None = None

    def copy(self, **kwargs: object) -> _RecordingLM:
        copied = _RecordingLM()
        copied.num_retries = kwargs.get("num_retries")  # type: ignore[assignment]
        return copied

    def forward(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return object()


def test_val_rec_017_child_receives_only_remaining_time_on_forked_lm() -> None:
    """VAL-REC-017: forked child LMs derive their per-call timeout from the
    same absolute deadline and reject calls once it has expired."""
    deadline = time.monotonic() + 5
    child = RLMModelBundle(_RecordingLM(), _RecordingLM()).fork_for_child(deadline=deadline)

    child.root_lm.forward(prompt="bounded")
    timeout = child.root_lm.calls[-1]["timeout"]
    assert isinstance(timeout, float)
    assert 0 < timeout <= 5

    expired = RLMModelBundle(_RecordingLM(), _RecordingLM()).fork_for_child(deadline=time.monotonic() - 1)
    with pytest.raises(TimeoutError, match="recursive child LM deadline exceeded"):
        expired.root_lm.forward(prompt="late")


@pytest.mark.parametrize(
    ("behavior", "lm_actions", "child_max_iters", "expected_answer"),
    [
        (
            "submit",
            [{"reasoning": "submit directly", "code": "SUBMIT(answer='child-ok')"}],
            2,
            "child-ok",
        ),
        (
            "code_error_first",
            [
                {"reasoning": "try failing code", "code": "x = 1 / 0"},
                {"reasoning": "recover", "code": "SUBMIT(answer='recovered')"},
            ],
            2,
            "recovered",
        ),
        (
            "never_submit",
            [
                {"reasoning": "do ordinary work", "code": "print('hello')"},
                {"answer": "extracted-answer"},
            ],
            1,
            "extracted-answer",
        ),
        (
            "interpreter_error",
            [{"reasoning": "run code", "code": "print('work')"}],
            2,
            None,
        ),
    ],
    ids=["typed_submit", "generated_code_error", "extraction_fallback", "terminal_interpreter_error"],
)
@pytest.mark.asyncio
async def test_val_rec_026_dspy_native_child_paths_never_shut_down_caller_owned_interpreter(
    behavior: str,
    lm_actions: list[dict[str, str]],
    child_max_iters: int,
    expected_answer: str | None,
) -> None:
    """VAL-REC-026 (native child scope, paired with VAL-RLM-007): through
    typed completion, generated-code error recovery, extraction fallback, and
    terminal interpreter failure, a native child RLM performs zero shutdown
    calls on the caller-owned interpreter; the Fleet lifecycle owner then
    shuts it down exactly once."""
    interpreter = _CountingInterpreter(behavior)
    rlm = build_native_rlm(
        signature="prompt -> answer",
        options=RLMOptions(max_iters=child_max_iters, max_llm_calls=child_max_iters + 2),
    )
    lm = dspy.utils.DummyLM(lm_actions, adapter=dspy.JSONAdapter())

    with dspy.context(lm=lm, adapter=dspy.JSONAdapter()):
        if behavior == "interpreter_error":
            with pytest.raises(CodeInterpreterError):
                await rlm.acall(interpreter, prompt="go")
        else:
            prediction = await rlm.acall(interpreter, prompt="go")
            assert prediction.answer == expected_answer

    # Zero DSPy-originated shutdown calls through completion and failure.
    assert interpreter.shutdown_calls == 0
    # Exactly one explicit Fleet-owned shutdown through the lifecycle close.
    interpreter.shutdown()
    assert interpreter.shutdown_calls == 1


@pytest.mark.asyncio
async def test_val_rec_026_cancellation_never_shuts_down_caller_owned_child_interpreter() -> None:
    """VAL-REC-026 (cancellation, paired with VAL-RLM-007): cancelling a
    native child mid-execution performs no DSPy-originated shutdown; the
    Fleet owner alone closes the interpreter."""
    entered_second_action = asyncio.Event()

    class _SuspendingSecondAction(dspy.Predict):
        """Completes the first action, then suspends so cancellation has a
        deterministic delivery point inside the native RLM loop."""

        def __init__(self) -> None:
            super().__init__("variables_info, repl_history, iteration -> reasoning, code")
            self.calls = 0

        async def aforward(self, **_kwargs: object) -> dspy.Prediction:
            self.calls += 1
            if self.calls == 1:
                return dspy.Prediction(reasoning="first action", code="print('one')")
            entered_second_action.set()
            await asyncio.sleep(30)
            raise AssertionError("cancellation must interrupt the second action")

    class _NoSubmitInterpreter:
        def __init__(self) -> None:
            self.tools: dict[str, object] = {}
            self.shutdown_calls = 0
            self.execute_calls = 0

        def start(self) -> None:
            return None

        def execute(self, code: str, variables: dict[str, object] | None = None) -> object:
            del code, variables
            self.execute_calls += 1
            return "plain output, keep iterating"

        def shutdown(self) -> None:
            self.shutdown_calls += 1

    interpreter = _NoSubmitInterpreter()
    rlm = build_native_rlm(
        signature="prompt -> answer",
        options=RLMOptions(max_iters=3, max_llm_calls=3),
    )
    rlm.generate_action = _SuspendingSecondAction()

    with dspy.context(
        lm=dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
        adapter=dspy.JSONAdapter(),
    ):
        task = asyncio.create_task(rlm.acall(interpreter, prompt="go"))
        await asyncio.wait_for(entered_second_action.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # The first action executed; cancellation interrupted the native loop and
    # never grants DSPy shutdown authority.
    assert interpreter.execute_calls == 1
    assert interpreter.shutdown_calls == 0
    # Exactly one explicit Fleet-owned shutdown through the lifecycle close.
    interpreter.shutdown()
    assert interpreter.shutdown_calls == 1


def test_val_rec_026_fleet_executor_closes_child_lease_exactly_once() -> None:
    """VAL-REC-026 (executor scope): a valid child answer is delivered through
    a real native child, and the Fleet executor, as the sole lifecycle owner,
    closes the lease exactly once before the answer reaches Root code."""
    recorder = _Recorder()
    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='child-ok')"}],
        recorder,
    )

    assert executor.tool(prompt="classify selected row") == "child-ok"

    interpreter = recorder.interpreters[1]
    assert recorder.close_calls.get(1) == 1
    assert interpreter.shutdown_calls == 1
    # Re-observation never shuts the interpreter down again.
    recorder.leases[0].close()
    assert recorder.close_calls.get(1) == 1
    assert interpreter.shutdown_calls == 1


def test_val_rec_026_executor_terminal_child_failure_still_settles_lease_once() -> None:
    """VAL-REC-026 (executor failure scope): a terminal interpreter failure
    propagates to Root without any DSPy-originated shutdown, and the Fleet
    owner still settles the lease exactly once on the failure path."""
    recorder = _Recorder()
    executor = _executor(
        [{"reasoning": "run code", "code": "print('work')"}],
        recorder,
        behavior="interpreter_error",
    )

    with pytest.raises(CodeInterpreterError):
        executor.tool(prompt="terminal child failure")

    interpreter = recorder.interpreters[1]
    # DSPy propagated the failure without touching shutdown; the owner closed.
    assert recorder.close_calls.get(1) == 1
    assert interpreter.shutdown_calls == 1
    recorder.leases[0].close()
    assert recorder.close_calls.get(1) == 1
    assert interpreter.shutdown_calls == 1


def test_val_rec_030_valid_child_answer_cannot_override_failed_cleanup() -> None:
    """VAL-REC-030 (executor scope): after a syntactically valid child answer,
    a lease close failure is recorded as fatal and re-observed by the executor
    without rerunning cleanup; the child call fails closed."""
    recorder = _Recorder()
    original = RuntimeError("broker cleanup failed")
    lease_holder: dict[str, ChildRuntimeLease] = {}

    def failing_close_factory(call_index: int) -> ChildRuntimeLease:
        lease = recorder.factory(call_index)
        lease_holder["lease"] = lease
        close = lease._close

        def failing_close() -> None:
            close()
            raise original

        lease._close = failing_close
        return lease

    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='valid-child-answer')"}],
        recorder,
        child_runtime_factory=failing_close_factory,
    )

    with pytest.raises(ChildRuntimeCleanupError, match="recursive child cleanup failed") as raised:
        executor.tool(prompt="valid child")
    # The typed cleanup failure chains the original close error.
    assert raised.value.__cause__ is original
    # The fatal cleanup error is re-observed on every later check and blocks
    # further child work.
    with pytest.raises(ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        executor.raise_if_cleanup_failed()
    with pytest.raises(ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        executor.tool(prompt="second call after fatal cleanup")
    # Cleanup ran exactly once; repeated close re-surfaces without rerunning.
    lease = lease_holder["lease"]
    assert recorder.close_calls.get(1) == 1
    with pytest.raises(RuntimeError) as reobserved:
        lease.close()
    assert reobserved.value is original
    assert recorder.close_calls.get(1) == 1
    assert recorder.interpreters[1].shutdown_calls == 1
