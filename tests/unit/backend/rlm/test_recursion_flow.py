from __future__ import annotations

import asyncio
import threading
import time
from uuid import uuid4

import dspy
import pytest

import fleet_rlm.rlm.execution as runtime_module
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.runtime import ChildRuntimeLease
from fleet_rlm.rlm.events import ChildProgress, ObservationSession, Status, ToolCompleted, ToolFailed, ToolStarted
from fleet_rlm.rlm.execution import (
    DelegationPolicy,
    ExecutionRuntime,
    RLMExecutionContext,
    RLMRunner,
    RunIdentity,
    SessionView,
    WorkerOwnership,
)
from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
from fleet_rlm.rlm.recursion import RecursiveRLMOptions
from fleet_rlm.sessions.context import SessionContextManifest
from fleet_rlm.sessions.models import TurnAccess
from fleet_rlm.sessions.run_state import RunAuthority
from tests.support.native_rlm import build_native_rlm_for_test
from tests.unit.backend.rlm.fakes import EmptyCapabilities


@pytest.mark.asyncio
async def test_root_child_root_flow_preserves_parent_repl_and_typed_submit() -> None:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "prepare selected data", "code": "root_marker = 'root-only'"},
            {
                "reasoning": "delegate selected row",
                "code": "child = rlm_query(task='classify selected row', inputs=[])['answer']",
            },
            {
                "reasoning": "check child scope",
                "code": (
                    "\ntry:\n    root_marker\n    child_cannot_see_root = False\n"
                    "except NameError:\n    child_cannot_see_root = True\n"
                    "SUBMIT(answer=str(child_cannot_see_root), evidence=[], gaps=[], result_files=[])"
                ),
            },
            {
                "reasoning": "integrate child answer",
                "code": "assert root_marker == 'root-only'\nassert child == 'True'\nSUBMIT(answer='root-complete')",
            },
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="classify the selected row",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=4, max_llm_calls=4),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=2),
            child_runtime_factory=lambda call_index, *, profile="semantic-child": _child_lease(
                call_index, profile=profile
            ),
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    events = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.answer == "root-complete"
    assert stream.outcome.usage["iterations"] == 3
    tool_started = [event for event in events if event.kind == "tool.started"]
    tool_completed = [event for event in events if event.kind == "tool.completed"]
    assert len(tool_started) == len(tool_completed) == 1
    assert isinstance(tool_started[0].detail, ToolStarted)
    assert isinstance(tool_completed[0].detail, ToolCompleted)
    assert tool_started[0].detail.input == {"input_count": 0}
    assert tool_completed[0].detail.output == {
        "status": "completed",
        "call_index": 1,
        "recursive_depth": 1,
        "child_iterations": 1,
        "termination_mode": "typed_submit",
    }
    statuses = [event for event in events if isinstance(event.detail, Status) and event.detail.phase == "recursive"]
    assert [event.detail.status for event in statuses] == ["child_started", "child_completed"]
    child_events = [event for event in events if isinstance(event.detail, ChildProgress)]
    assert len(child_events) == 2
    assert all(event.detail.parent_run_id == str(context.identity.run_id) for event in child_events)
    assert all(event.run_id == context.identity.run_id for event in child_events)
    assert all("classify selected row" not in (event.detail.message or "") for event in statuses)
    assert all("root-complete" not in (event.detail.message or "") for event in statuses)
    assert all(not isinstance(detail, Status) for detail in stream.outcome.execution_details)


@pytest.mark.asyncio
async def test_root_completion_drains_action_deadline_stragglers(monkeypatch: pytest.MonkeyPatch) -> None:
    """An action-bounded batch returns partial outcomes while a cancelled
    straggler is still stopping; the Root may submit at once, and its outcome
    is accepted only after that straggler settles and its lease closes."""
    import json

    import fleet_rlm.rlm.recursion as recursive_calls

    monkeypatch.setattr(recursive_calls, "_ACTION_RESULT_MARGIN_S", 1.0)
    # The deadline is read once at tool entry: give the calling action 2 s from then.
    monkeypatch.setattr(recursive_calls, "current_host_action_deadline", lambda: time.monotonic() + 2.0)
    straggler_settled = threading.Event()

    class Child:
        def __init__(self, interpreter_factory) -> None:
            self._interpreter_factory = interpreter_factory

        def __call__(self, *, prompt: str) -> dspy.Prediction:
            if json.loads(prompt)["task"] == "fast":
                return dspy.Prediction(answer="fast-done", evidence=[], gaps=[], result_files=[], trajectory=[])
            interpreter = self._interpreter_factory()
            try:
                for index in range(1000):
                    interpreter.execute(f"value = {index}")
                    time.sleep(0.02)
            finally:
                interpreter.shutdown()
                # An in-flight LM call outlives the refusal of the next action.
                time.sleep(0.5)
                straggler_settled.set()
            return dspy.Prediction(answer="never", evidence=[], gaps=[], result_files=[], trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **kwargs: Child(kwargs["interpreter_factory"]))
    monkeypatch.setattr(recursive_calls, "is_native_rlm", lambda _program: True)
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {
                "reasoning": "batch",
                "code": (
                    "answers = rlm_query_batched("
                    "tasks=[{'task': 'fast', 'inputs': []}, {'task': 'slow', 'inputs': []}])"
                ),
            },
            {"reasoning": "submit", "code": "SUBMIT(answer='/'.join(item['status'] for item in answers))"},
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="cross-check",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=4, max_llm_calls=4),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=2, max_parallel_children=2),
            child_runtime_factory=lambda call_index, *, profile="semantic-child": _child_lease(
                call_index, profile=profile
            ),
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    events = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded, events
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.answer == "completed/timed_out"
    assert straggler_settled.is_set()


def _child_lease(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:
    """Create a child runtime lease for a recursive test invocation.

    Parameters:
        call_index (int): Index used to identify the child runtime and workspace.

    Returns:
                ChildRuntimeLease: A lease backed by an in-process interpreter.
    """
    del profile
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    return ChildRuntimeLease(
        interpreter,
        f"child-{call_index}",
        "test-volume",
        f"recursive/test-workspace/test-run/{call_index}",
        interpreter.shutdown,
    )


@pytest.mark.asyncio
async def test_worker_startup_failure_releases_the_runner_owned_child_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = dspy.utils.DummyLM([], adapter=dspy.JSONAdapter())
    sub = dspy.utils.DummyLM([], adapter=dspy.JSONAdapter())

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="fail during worker startup",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=1, max_llm_calls=1),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=1),
            child_runtime_factory=_child_lease,
        ),
        capabilities=EmptyCapabilities(),
    )
    created = []
    real_executor = runtime_module.RecursiveRLMExecutor

    def track_executor(**kwargs):
        executor = real_executor(**kwargs)
        created.append(executor)
        return executor

    def fail_signature(*_args, **_kwargs):
        raise RuntimeError("worker startup failed")

    monkeypatch.setattr(runtime_module, "RecursiveRLMExecutor", track_executor)
    monkeypatch.setattr(runtime_module, "root_signature_for_recursion", fail_signature)
    ownership = WorkerOwnership()
    observations = ObservationSession(context.identity.run_id, context.identity.session_id)

    try:
        with pytest.raises(RuntimeError, match="worker startup failed"):
            await RLMRunner()._start_worker(context, ownership, observations)
        assert created
        assert created[0]._scheduler._loop is asyncio.get_running_loop()
        assert not created[0]._scheduler._closed
    finally:
        await ownership.wait_owned()

    assert created[0]._scheduler._closed
    assert asyncio.get_running_loop().is_running()


@pytest.mark.asyncio
async def test_runner_rejects_recursive_tool_after_authority_revocation() -> None:
    """A revoked Run authority rejects the recursive tool at the Runner's
    authorization fence: the Run observes ``tool.started`` then ``tool.failed``
    for ``rlm_query`` and the child factory is never called, so no child lease
    is ever created.

    The scripted action sits on a non-final iteration so it actually reaches the
    recursive tool instead of being intercepted by wrap-up, which is now keyed
    to the iteration count. ``max_iters=3`` keeps the subsequent scripted-response
    exhaustion off the final iteration too: ``DummyLM`` answers ``"No more
    responses"`` once its list is spent, and on a final iteration that parse
    failure would be reported as bounded finalization exhaustion (a
    ``TimeoutError``) instead of the parse exhaustion this Turn hits.
    """
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [{"reasoning": "delegate too late", "code": "rlm_query(task='late child request', inputs=[])['answer']"}],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    authority = RunAuthority()
    authority.revoke()
    created: list[int] = []

    async def not_cancelled() -> bool:
        """Indicate that cancellation has not been requested.

        Returns:
                bool: `False`, indicating that cancellation has been requested.
        """
        return False

    def child_factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:
        """
        Create a child runtime lease for the specified recursive call index.

        Parameters:
                call_index (int): Index of the recursive call.

        Returns:
                ChildRuntimeLease: The runtime lease for the child call.
        """
        del profile
        created.append(call_index)
        return _child_lease(call_index)

    context = RLMExecutionContext(
        identity=RunIdentity(
            run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4()), authority=authority
        ),
        session=SessionView(
            request="delegate after claim loss",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=3, max_llm_calls=4),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=1), child_runtime_factory=child_factory
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    events = [event async for event in stream]

    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "failed"
    # The recursive tool was really attempted and then rejected; the rejection,
    # not the tool never running, is what kept the child factory at zero.
    assert [
        (type(event.detail).__name__, event.detail.tool_name)
        for event in events
        if isinstance(event.detail, (ToolStarted, ToolFailed))
    ] == [("ToolStarted", "rlm_query"), ("ToolFailed", "rlm_query")]
    assert created == []


@pytest.mark.asyncio
async def test_normal_daytona_policy_omits_recursive_tool_and_guidance() -> None:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [{"reasoning": "answer directly", "code": "SUBMIT(answer='normal-complete')"}],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    captured: dict[str, object] = {}

    def capturing_builder(**kwargs: object):
        captured.update(kwargs)
        return build_native_rlm_for_test(**kwargs)

    async def not_cancelled() -> bool:
        """Indicate that cancellation has not been requested.

        Returns:
                bool: `False`, indicating that cancellation has been requested.
        """
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer directly",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=2, max_llm_calls=2),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(recursive_options=RecursiveRLMOptions(enabled=False)),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner(program_builder=capturing_builder).stream(context)
    _events = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded
    assert captured["tools"] is None
    assert "rlm_query" not in captured["signature"].instructions


@pytest.mark.asyncio
async def test_failed_child_cleanup_prevents_successful_root_outcome() -> None:
    """
    Verify that failed child-runtime cleanup causes the root RLM execution to fail.
    """
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "delegate", "code": "child = rlm_query(task='small task', inputs=[])['answer']"},
            {"reasoning": "child submit", "code": "SUBMIT(answer='child', evidence=[], gaps=[], result_files=[])"},
            {"reasoning": "submit anyway", "code": "SUBMIT(answer='unexpected')"},
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)

    async def not_cancelled() -> bool:
        """Indicate that cancellation has not been requested.

        Returns:
                bool: `False`, indicating that cancellation has been requested.
        """
        return False

    def failed_lease(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:
        """Create a child runtime lease whose cleanup raises an error."""
        del profile
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

        def close() -> None:
            """Shut down the interpreter and raise an error indicating that child cleanup failed."""
            interpreter.shutdown()
            raise RuntimeError("child cleanup failed")

        return ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            close,
        )

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="delegate one task",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=4, max_llm_calls=4),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=2), child_runtime_factory=failed_lease
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    _events = [event async for event in stream]

    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "failed"


@pytest.mark.asyncio
async def test_runner_wait_owned_retains_pending_recursive_workers_until_child_lease_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed Root batch must not release Run resources before its sibling settles."""
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "batch", "code": "answers = rlm_query_batched(tasks=[{'task': 'blocked', 'inputs': []}])"},
            {"reasoning": "submit", "code": "SUBMIT(answer='root')"},
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    child_started = threading.Event()
    release_child = threading.Event()
    child_closed = threading.Event()

    class BlockingChild:
        def __call__(self, *, prompt: str) -> dspy.Prediction:
            del prompt
            child_started.set()
            release_child.wait(2)
            return dspy.Prediction(answer="late", evidence=[], gaps=[], result_files=[], trajectory=[])

    import fleet_rlm.rlm.recursion as recursive_calls

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockingChild())
    monkeypatch.setattr(recursive_calls, "is_native_rlm", lambda _program: True)

    def child_factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:
        del profile
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

        def close() -> None:
            interpreter.shutdown()
            child_closed.set()

        return ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            close,
        )

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=4, max_llm_calls=4),
            deadline=time.monotonic() + 0.5,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=1),
            child_runtime_factory=child_factory,
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    _events = [event async for event in stream]

    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "timeout"
    assert child_started.wait(2)
    owned = asyncio.create_task(stream.wait_owned())
    await asyncio.sleep(0.05)
    assert not owned.done()
    assert not child_closed.is_set()

    release_child.set()
    await asyncio.wait_for(owned, timeout=2)
    assert child_closed.is_set()
