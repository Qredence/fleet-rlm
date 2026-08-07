from __future__ import annotations

import time
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLease
from fleet_rlm.rlm.context import (
    DelegationPolicy,
    ExecutionRuntime,
    RLMExecutionContext,
    RLMExecutionSpec,
    SessionView,
    TurnIdentity,
)
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.events import Status, ToolCompleted, ToolStarted
from fleet_rlm.rlm.factory import RLMFactory
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.recursive_calls import RecursiveRLMOptions
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.sessions.models import TurnAccess


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

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=TurnIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="classify the selected row",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iterations=4, max_llm_calls=4),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=2),
            child_runtime_factory=lambda call_index: _child_lease(call_index),
        ),
        capabilities=Capabilities(),
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

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            """
            Drain pending artifact candidates.

            Returns:
                tuple: An empty tuple.
            """
            return ()

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
        identity=TurnIdentity(
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
            options=RLMOptions(max_iterations=2, max_llm_calls=2),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=1), child_runtime_factory=child_factory
        ),
        capabilities=Capabilities(),
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

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            """
            Drain pending artifact candidates.

            Returns:
                tuple: An empty tuple.
            """
            return ()

    async def not_cancelled() -> bool:
        """Indicate that cancellation has not been requested.

        Returns:
                bool: `False`, indicating that cancellation has been requested.
        """
        return False

    context = RLMExecutionContext(
        identity=TurnIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer directly",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iterations=2, max_llm_calls=2),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(recursive_options=RecursiveRLMOptions(enabled=False)),
        capabilities=Capabilities(),
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

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            """
            Drain pending artifact candidates.

            Returns:
                tuple: An empty tuple.
            """
            return ()

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
        identity=TurnIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="delegate one task",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iterations=4, max_llm_calls=4),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=2), child_runtime_factory=failed_lease
        ),
        capabilities=Capabilities(),
    )

    stream = RLMRunner().stream(context)
    _events = [event async for event in stream]

    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "failed"
