from __future__ import annotations

import asyncio
import threading
import time
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLease
from fleet_rlm.rlm.events import Status, ToolCompleted, ToolStarted
from fleet_rlm.rlm.program import RLMFactory, RLMModelBundle, RLMOptions
from fleet_rlm.rlm.recursion import RecursiveRLMOptions
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


@pytest.mark.asyncio
async def test_root_child_root_flow_preserves_parent_repl_and_typed_submit() -> None:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "prepare selected data", "code": "root_marker = 'root-only'"},
            {"reasoning": "delegate selected row", "code": "child = rlm_query(prompt='classify selected row')"},
            {
                "reasoning": "check child scope",
                "code": (
                    "\ntry:\n    root_marker\n    child_cannot_see_root = False\n"
                    "except NameError:\n    child_cannot_see_root = True\n"
                    "SUBMIT(answer=str(child_cannot_see_root))"
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
            child_runtime_factory=lambda call_index: _child_lease(call_index),
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    events = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.display_text == "root-complete"
    assert stream.outcome.usage["iterations"] == 3
    tool_started = [event for event in events if event.kind == "tool.started"]
    tool_completed = [event for event in events if event.kind == "tool.completed"]
    assert len(tool_started) == len(tool_completed) == 1
    assert isinstance(tool_started[0].detail, ToolStarted)
    assert isinstance(tool_completed[0].detail, ToolCompleted)
    assert tool_started[0].detail.input == {"prompt_count": 1, "prompt_chars": len("classify selected row")}
    assert tool_completed[0].detail.output == {
        "status": "completed",
        "call_index": 1,
        "recursive_depth": 1,
        "child_iterations": 1,
        "termination_mode": "typed_submit",
    }
    statuses = [event for event in events if isinstance(event.detail, Status) and event.detail.phase == "recursive"]
    assert [event.detail.status for event in statuses] == ["child_started", "child_completed"]
    assert all("classify selected row" not in (event.detail.message or "") for event in statuses)
    assert all("root-complete" not in (event.detail.message or "") for event in statuses)
    assert all(not isinstance(detail, Status) for detail in stream.outcome.execution_details)


def _child_lease(call_index: int) -> ChildRuntimeLease:
    """Create a child runtime lease for a recursive test invocation.

    Parameters:
        call_index (int): Index used to identify the child runtime and workspace.

    Returns:
        ChildRuntimeLease: A lease backed by an in-process interpreter.
    """
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    return ChildRuntimeLease(
        interpreter,
        f"child-{call_index}",
        "test-volume",
        f"recursive/test-workspace/test-run/{call_index}",
        interpreter.shutdown,
    )


@pytest.mark.asyncio
async def test_runner_rejects_recursive_tool_after_authority_revocation() -> None:
    """Verify that recursive execution is rejected when run authority has been revoked before the run starts."""
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [{"reasoning": "delegate too late", "code": "rlm_query(prompt='late child request')"}],
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

    def child_factory(call_index: int) -> ChildRuntimeLease:
        """
        Create a child runtime lease for the specified recursive call index.

        Parameters:
                call_index (int): Index of the recursive call.

        Returns:
                ChildRuntimeLease: The runtime lease for the child call.
        """
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
            options=RLMOptions(max_iters=2, max_llm_calls=2),
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
    _events = [event async for event in stream]

    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "failed"
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

    class Factory:
        def create(self, **kwargs: object):
            """
            Create an RLM instance while recording the supplied configuration arguments.
            """
            captured.update(kwargs)
            return RLMFactory().create(**kwargs)

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

    stream = RLMRunner(factory=Factory()).stream(context)
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
            {"reasoning": "delegate", "code": "child = rlm_query(prompt='small task')"},
            {"reasoning": "child submit", "code": "SUBMIT(answer='child')"},
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

    def failed_lease(call_index: int) -> ChildRuntimeLease:
        """Create a child runtime lease whose cleanup raises an error."""
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
            {"reasoning": "batch", "code": "answers = rlm_query_batched(prompts=['blocked'])"},
            {"reasoning": "submit", "code": "SUBMIT(answer='root')"},
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    child_started = threading.Event()
    release_child = threading.Event()
    child_closed = threading.Event()

    class BlockingChild:
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            del prompt
            child_started.set()
            release_child.wait(2)
            return dspy.Prediction(answer="late", trajectory=[])

    import fleet_rlm.rlm.recursion as recursive_calls

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockingChild())

    def child_factory(call_index: int) -> ChildRuntimeLease:
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
