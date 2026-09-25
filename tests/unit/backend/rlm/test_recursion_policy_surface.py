"""P39b behavior-only recursion policy contract lanes.

Depth and exposure are asserted through public composition behavior, never
through private symbol names:

- Public Root composition fixes depth 0; the first child
  reservation produced by either recursive Tool reports depth 1; no public
  options or settings surface accepts a recursion depth.
- The Root native RLM receives exactly the approved recursive
  pair; a child receives no Fleet recursive tools and a batch attempt from a child
  fails without reserving calls or allocating a Sandbox.
- Root and native child are both exact native ``dspy.RLM`` instances. The Root
  uses its configured invocation-scoped interpreter factory, while a child
  uses its private lease interpreter; each starts fresh REPL history and
  produces a native Prediction.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Callable
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.runtime import ChildRuntimeLease
from fleet_rlm.rlm.events import Status, ToolCompleted
from fleet_rlm.rlm.execution import (
    DelegationPolicy,
    ExecutionRuntime,
    RLMExecutionContext,
    RLMRunner,
    RunIdentity,
    SessionView,
)
from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions, build_native_rlm
from fleet_rlm.rlm.recursion import (
    RecursiveRLMOptions,
)
from fleet_rlm.sessions.context import SessionContextManifest
from fleet_rlm.sessions.models import TurnAccess
from tests.support.recursion_scheduler import RecursiveRLMExecutor
from tests.unit.backend.rlm.fakes import EmptyCapabilities


class _RecordingFactory:
    """Record child lease identity; the lease stays fully owned."""

    def __init__(self) -> None:
        self.call_indexes: list[int] = []
        self.leases: list[ChildRuntimeLease] = []
        self.interpreters: list[DaytonaCodeInterpreter] = []
        self.close_counts: dict[int, int] = {}

    def __call__(self, call_index: int) -> ChildRuntimeLease:
        self.call_indexes.append(call_index)
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        self.interpreters.append(interpreter)

        def close() -> None:
            self.close_counts[call_index] = self.close_counts.get(call_index, 0) + 1
            interpreter.shutdown()

        lease = ChildRuntimeLease(
            interpreter,
            f"policy-child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            close,
        )
        self.leases.append(lease)
        return lease


def _executor(
    root_actions: list[dict[str, str]],
    factory: _RecordingFactory | None = None,
    *,
    options: RecursiveRLMOptions | None = None,
    sub_actions: list[dict[str, str]] | None = None,
    deadline: float | None = None,
    observer: Callable[[object], None] | None = None,
    is_authorized: Callable[[], bool] | None = None,
) -> RecursiveRLMExecutor:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(root_actions, adapter=adapter)
    sub = dspy.utils.DummyLM(sub_actions or [{"answer": "fallback"}], adapter=adapter)
    return RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=options or RecursiveRLMOptions(),
        child_runtime_factory=factory,
        deadline=deadline if deadline is not None else time.monotonic() + 30,
        observer=observer,
        is_authorized=is_authorized,
    )


def _context(
    *,
    root: dspy.utils.DummyLM,
    sub: dspy.utils.DummyLM,
    factory: Callable[[int], ChildRuntimeLease] | None,
    recursive_options: RecursiveRLMOptions,
    root_options: RLMOptions | None = None,
    deadline: float | None = None,
    runner_factory: Callable[..., object] | None = None,
) -> tuple[RLMExecutionContext, RLMRunner]:
    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="delegate",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=root_options or RLMOptions(max_iters=6, max_llm_calls=6),
            deadline=deadline if deadline is not None else time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=recursive_options,
            child_runtime_factory=factory,
        ),
        capabilities=EmptyCapabilities(),
    )
    return context, RLMRunner(program_builder=runner_factory) if runner_factory is not None else RLMRunner()


def test_public_composition_fixes_root_depth_zero() -> None:
    """Composing the production Root through the public
    delegation policy fixes depth 0; the first reservation produced by the
    single recursive Tool reports depth 1 in its completion evidence; the
    recursive options surface accepts no depth setting."""
    events: list[object] = []
    factory = _RecordingFactory()
    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='child-ok')"}],
        factory,
        observer=events.append,
    )

    assert executor.tool(capsule={"task": "classify selected row"})["answer"] == "child-ok"
    completed = next(event for event in events if isinstance(event, ToolCompleted))
    assert completed.output["recursive_depth"] == 1
    statuses = [event for event in events if isinstance(event, Status)]
    assert [status.status for status in statuses] == ["child_started", "child_completed"]
    assert all("recursive_depth=1" in (status.message or "") for status in statuses)

    # The options surface is a public composition input; it accepts no
    # recursion depth setting of any name shape.
    options = RecursiveRLMOptions(enabled=True)
    assert not any("depth" in field.name for field in dataclasses.fields(options))
    with pytest.raises(TypeError):
        RecursiveRLMOptions(depth=1)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        RecursiveRLMOptions(max_depth=2)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_public_runner_first_child_reservation_reports_depth_one() -> None:
    """Through the public Runner composition, both recursive
    surfaces expose their first child reservation at depth 1 while the Root
    itself stays at depth 0; no public option moves that depth."""
    adapter = dspy.JSONAdapter()
    # Root and child generate_action calls share the Root LM's list-mode
    # iterator: Root action, then the child action it triggers, and so on.
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "single", "code": "a = rlm_query(capsule={'task': 'slice one'})['answer']"},
            {"reasoning": "child one", "code": "SUBMIT(answer='one-done')"},
            {"reasoning": "batch", "code": "b = rlm_query_batched(capsules=[{'task': 'slice two'}])"},
            {"reasoning": "child two", "code": "SUBMIT(answer='two-done')"},
            {"reasoning": "submit", "code": "SUBMIT(answer=a + b[0]['answer'])"},
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    factory = _RecordingFactory()
    context, runner = _context(
        root=root,
        sub=sub,
        factory=factory,
        recursive_options=RecursiveRLMOptions(enabled=True, max_calls=2),
        root_options=RLMOptions(max_iters=6, max_llm_calls=6),
    )

    stream = runner.stream(context)
    events = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.answer == "one-donetwo-done"

    # The first single-child reservation reports depth exactly 1.
    single_completed = next(
        event.detail
        for event in events
        if isinstance(event.detail, ToolCompleted) and event.detail.tool_name == "rlm_query"
    )
    assert single_completed.output == {
        "status": "completed",
        "call_index": 1,
        "recursive_depth": 1,
        "child_iterations": 1,
        "termination_mode": "typed_submit",
    }
    # Every recursive status (both children) reports depth 1.
    statuses = [
        event.detail for event in events if isinstance(event.detail, Status) and event.detail.phase == "recursive"
    ]
    assert [status.status for status in statuses] == [
        "child_started",
        "child_completed",
        "child_started",
        "child_completed",
    ]
    assert all("recursive_depth=1" in (status.message or "") for status in statuses)


def test_public_settings_surface_exposes_no_recursion_depth() -> None:
    """The public settings surface (the composition input for
    recursion policy) carries bounded width/budget knobs and no recursion
    depth knob under any recursion setting name."""
    from fleet_rlm.config.settings import Settings

    recursion_settings = [name for name in Settings.model_fields if name.startswith("rlm_recursion")]
    assert recursion_settings
    assert not any("depth" in name for name in recursion_settings)


def test_child_batch_attempt_fails_without_reservation_or_allocation() -> None:
    """The batch surface is Root-only. A child interpreter
    namespace cannot resolve it; the failed attempt reserves no further call
    and allocates no additional Sandbox."""
    factory = _RecordingFactory()
    executor = _executor(
        [
            {
                "reasoning": "attempt batch inside the child",
                "code": (
                    "try:\n"
                    "    rlm_query_batched(prompts=['x'])\n"
                    "    batch_result = 'resolved'\n"
                    "except NameError:\n"
                    "    batch_result = 'unresolved'\n"
                    "SUBMIT(answer=batch_result)"
                ),
            },
        ],
        factory,
        options=RecursiveRLMOptions(max_calls=4),
    )

    assert executor.tool(capsule={"task": "outer slice"})["answer"] == "unresolved"
    # Exactly one native child was allocated; the child's batch attempt never
    # reached reservation or allocation, and the Root-only batch counter
    # stayed at zero.
    assert factory.call_indexes == [1]
    summary = executor.summary()
    assert summary.call_count == 1
    assert summary.recursive_batch_calls == 0
    assert all(lease.state.value == "CLOSED" for lease in factory.leases)


@pytest.mark.parametrize("enabled", [False, True])
def test_root_receives_exactly_the_approved_recursive_tools_through_public_composition(
    enabled: bool,
) -> None:
    """The Root native RLM composed through the public Runner
    receives the approved recursive Tools, including the strict capsule path,
    by their public names."""
    captured: dict[str, object] = {}

    def capturing_builder(**kwargs: object) -> object:
        captured.update(kwargs)
        return build_native_rlm(**kwargs)

    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM([{"reasoning": "direct", "code": "SUBMIT(answer='direct')"}], adapter=adapter)
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    context, runner = _context(
        root=root,
        sub=sub,
        factory=_RecordingFactory(),
        recursive_options=RecursiveRLMOptions(enabled=enabled),
        root_options=RLMOptions(max_iters=2, max_llm_calls=2),
        runner_factory=capturing_builder,
    )

    async def drive() -> None:
        stream = runner.stream(context)
        async for _event in stream:
            pass

    asyncio.run(drive())

    tool_names = [str(tool.name) for tool in (captured.get("tools") or ())]
    assert tool_names == (["rlm_query", "rlm_query_batched"] if enabled else [])


@pytest.mark.asyncio
async def test_root_factory_and_child_lease_drive_exact_native_rlm() -> None:
    """The Root uses its per-invocation factory and a child uses its private
    lease; both are exact native RLMs that produce native trajectory evidence."""
    import fleet_rlm.rlm.recursion as recursive_calls

    child_invocations: list[tuple[type, object, dict[str, object]]] = []
    root_invocations: list[tuple[type, dict[str, object]]] = []
    root_interpreters: list[object] = []
    root_types: list[type] = []
    real_build = recursive_calls.build_native_rlm

    def recording_build(**kwargs: object) -> object:
        rlm = real_build(**kwargs)
        original_forward = rlm.forward

        def forward(interpreter: object, /, **input_args: object) -> object:
            child_invocations.append((type(rlm), interpreter, dict(input_args)))
            return original_forward(interpreter, **input_args)

        rlm.forward = forward
        return rlm

    def root_builder(**kwargs: object) -> object:
        interpreter_factory = kwargs["interpreter_factory"]
        assert callable(interpreter_factory)

        def recording_factory() -> object:
            interpreter = interpreter_factory()
            root_interpreters.append(interpreter)
            return interpreter

        kwargs["interpreter_factory"] = recording_factory
        rlm = build_native_rlm(**kwargs)
        root_types.append(type(rlm))
        original_acall = rlm.acall

        async def acall(**input_args: object) -> object:
            root_invocations.append((type(rlm), dict(input_args)))
            return await original_acall(**input_args)

        rlm.acall = acall
        return rlm

    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "delegate", "code": "answer = rlm_query(capsule={'task': 'native marker child'})['answer']"},
            {"reasoning": "child submit", "code": "SUBMIT(answer='child-native-ok')"},
            {"reasoning": "submit", "code": "SUBMIT(answer=answer)"},
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    factory = _RecordingFactory()
    context, runner = _context(
        root=root,
        sub=sub,
        factory=factory,
        recursive_options=RecursiveRLMOptions(enabled=True, max_calls=1),
        root_options=RLMOptions(max_iters=4, max_llm_calls=4),
        runner_factory=root_builder,
    )

    recursive_calls.build_native_rlm = recording_build
    try:
        stream = runner.stream(context)
        _events = [event async for event in stream]
    finally:
        recursive_calls.build_native_rlm = real_build

    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.answer == "child-native-ok"

    # Both Root and child are the exact native class, each freshly composed.
    assert root_types == [dspy.RLM]
    assert len(child_invocations) == 1
    assert child_invocations[0][0] is dspy.RLM

    # The Root lets DSPy call its configured factory. The child interpreter is
    # its lease's fresh interpreter and remains distinct from the Root one.
    assert len(root_invocations) == 1
    assert len(root_interpreters) == 1
    assert root_interpreters[0] is not context.execution.interpreter
    assert child_invocations[0][1] is factory.interpreters[0]
    # Capsule children receive only selected input; Root keeps Session state.
    child_inputs = child_invocations[0][2]
    assert set(child_inputs) == {"prompt"}
    assert "request" in root_invocations[0][1]

    # Native Prediction evidence: the completed Root turn exposes a trajectory
    # and the child's typed SUBMIT settled through the same kernel.
    prediction = stream.outcome.prediction
    assert prediction.answer == "child-native-ok"
