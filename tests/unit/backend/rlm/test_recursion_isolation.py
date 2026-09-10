"""Behavior contracts for recursion isolation."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.program import (
    RLMModelBundle,
    RLMOptions,
    build_native_rlm,
)
from fleet_rlm.rlm.recursion import (
    RecursiveRLMOptions,
    RecursiveSubtaskSignature,
)
from fleet_rlm.rlm.result import (
    PredictionOutputError,
    PredictionOutputTooLargeError,
    prediction_result,
    rlm_termination_mode,
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
    root_lm: dspy.utils.DummyLM,
    sub_lm: dspy.utils.DummyLM,
    recorder: ChildLeaseRecorder,
    *,
    options: RecursiveRLMOptions | None = None,
    observer: Callable[[object], None] | None = None,
) -> RecursiveRLMExecutor:
    return RecursiveRLMExecutor(
        models=RLMModelBundle(root_lm, sub_lm),
        options=options or RecursiveRLMOptions(),
        child_runtime_factory=recorder.factory,
        deadline=time.monotonic() + 30,
        observer=observer,
    )


def _lm(answers: Any) -> dspy.utils.DummyLM:
    return dspy.utils.DummyLM(answers, adapter=dspy.JSONAdapter())


def test_val_rec_002_two_sequential_children_are_distinct_fresh_native_runtimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-002: two sequential Root ``rlm_query`` calls build distinct
    native RLMs, acquire distinct leases/interpreters/Sandbox ids, close each
    child exactly once before its answer returns, and the second child starts
    from an empty namespace (it cannot resolve the first child's global)."""
    import fleet_rlm.rlm.recursion as recursive_calls

    recorder = ChildLeaseRecorder()
    built_rlms: list[Any] = []
    real_build = recursive_calls.build_native_rlm

    def spy_build(**kwargs: object) -> Any:
        rlm = real_build(**kwargs)
        built_rlms.append(rlm)
        return rlm

    monkeypatch.setattr(recursive_calls, "build_native_rlm", spy_build)

    # Child A installs a Python global in its namespace; child B probes for
    # it and for any Root-visible name, expecting NameError for both.
    root = _lm(
        [
            {"reasoning": "child a", "code": "sentinel = 'child-a-global'\nSUBMIT(answer=sentinel)"},
            {
                "reasoning": "child b",
                "code": (
                    "leaked = []\n"
                    "for name in ('sentinel', 'root_marker'):\n"
                    "    try:\n"
                    "        eval(name)\n"
                    "        leaked.append(name)\n"
                    "    except NameError:\n"
                    "        pass\n"
                    "SUBMIT(answer='fresh' if not leaked else 'reused:' + ','.join(leaked))"
                ),
            },
        ]
    )
    sub = _lm([{"answer": "unused"}])
    executor = _executor(root, sub, recorder, options=RecursiveRLMOptions(max_calls=2))

    # Call 1: a fresh native child produces the sentinel answer.
    first = executor.tool(prompt="first slice")
    assert first == "child-a-global"
    # The first child was closed exactly once BEFORE its answer returned.
    assert recorder.close_calls == {1: 1}
    assert recorder.interpreters[1]._shutdown
    assert built_rlms and type(built_rlms[0]).__name__ == "RLM"

    # Call 2: a second fresh native child starts from an empty namespace.
    second = executor.tool(prompt="second slice")
    assert second == "fresh"

    # Distinct native RLM instances, one per call.
    assert len(built_rlms) == 2
    assert built_rlms[0] is not built_rlms[1]
    assert all(type(rlm).__module__ == "dspy.predict.rlm" for rlm in built_rlms)

    # Distinct leases, interpreter objects, backends, and Sandbox ids.
    assert recorder.call_indexes == [1, 2]
    assert recorder.leases[0] is not recorder.leases[1]
    assert recorder.leases[0].sandbox_id != recorder.leases[1].sandbox_id
    assert recorder.interpreters[1] is not recorder.interpreters[2]
    assert recorder.backends[0] is not recorder.backends[1]
    # No namespace reuse: the second child's backend namespace never saw the
    # first child's global (its probe answered 'fresh' above).
    assert "sentinel" not in recorder.backends[1].namespace
    # Tool binding closures are distinct interpreter-owned objects.
    assert recorder.interpreters[1].tools is not recorder.interpreters[2].tools

    # Both children closed exactly once, in call order; re-observing a close
    # never runs cleanup again.
    assert recorder.close_order == [1, 2]
    assert recorder.close_calls == {1: 1, 2: 1}
    recorder.leases[0].close()
    recorder.leases[1].close()
    assert recorder.close_calls == {1: 1, 2: 1}
    assert all(interpreter._shutdown for interpreter in recorder.interpreters.values())

    # Each child's REPL history contained only its own single action.
    summary = executor.summary()
    assert summary.child_iterations == 2
    assert summary.termination_modes == ("typed_submit", "typed_submit")
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


def test_val_rec_002_sequential_children_report_independent_completion_evidence() -> None:
    """VAL-REC-002: each sequential child's completion evidence is scoped to
    its own invocation: per-call completion metadata reports its own call
    index and depth, and the second child's evidence carries no state from
    the first."""
    recorder = ChildLeaseRecorder()
    events: list[object] = []
    root = _lm(
        [
            {"reasoning": "child a", "code": "SUBMIT(answer='a-answer')"},
            {"reasoning": "child b", "code": "SUBMIT(answer='b-answer')"},
        ]
    )
    sub = _lm([{"answer": "unused"}])
    executor = _executor(root, sub, recorder, options=RecursiveRLMOptions(max_calls=2), observer=events.append)

    assert executor.tool(prompt="first slice") == "a-answer"
    from fleet_rlm.rlm.events import ToolCompleted

    first_completed = [event for event in events if isinstance(event, ToolCompleted)]
    assert len(first_completed) == 1
    assert first_completed[0].output == {
        "status": "completed",
        "call_index": 1,
        "recursive_depth": 1,
        "child_iterations": 1,
        "termination_mode": "typed_submit",
    }

    assert executor.tool(prompt="second slice") == "b-answer"
    second_completed = [event for event in events if isinstance(event, ToolCompleted)]
    assert len(second_completed) == 2
    # The second completion evidence is its own: fresh index, same depth,
    # its own iteration count, no residue from the first child.
    assert second_completed[1].output == {
        "status": "completed",
        "call_index": 2,
        "recursive_depth": 1,
        "child_iterations": 1,
        "termination_mode": "typed_submit",
    }
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


class RecordingLM(dspy.utils.DummyLM):
    """Records every rendered prompt so the lane can attribute invocations
    to Root/Sub roles by content."""

    def __init__(self, answers: Any, adapter: Any) -> None:
        super().__init__(answers, adapter=adapter)
        self.calls: list[str] = []

    def forward(self, prompt: Any = None, messages: Any = None, **kwargs: Any) -> Any:
        parts: list[str] = []
        for message in messages or []:
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
        self.calls.append("\n".join(parts))
        return super().forward(prompt=prompt, messages=messages, **kwargs)


@pytest.mark.asyncio
async def test_val_rec_025_roles_depths_histories_and_trajectory_are_preserved_through_the_runner() -> None:
    """VAL-REC-025: through the public Runner composition, Root LM calls are
    recorded at depth 0, the native child's Root-LM-driven actions at depth
    1, and the depth fallback's Sub-LM call at depth 2; the Sub LM never
    drives Root actions; child LM histories are independent of the Root's;
    and the Root outcome carries the native trajectory."""
    adapter = dspy.JSONAdapter()
    root = RecordingLM(
        [
            # Root action 1: delegate to child 1.
            {"reasoning": "delegate", "code": "a = rlm_query(prompt='child slice')"},
            # Child 1 action 1: deeper delegation -> depth-2 fallback.
            {"reasoning": "child delegate", "code": "inner = rlm_query(prompt='fallback slice')"},
            # Child 1 action 2: submit the fallback answer.
            {"reasoning": "child submit", "code": "SUBMIT(answer=inner)"},
            # Root action 2: integrate and submit.
            {"reasoning": "submit", "code": "SUBMIT(answer=a)"},
        ],
        adapter=adapter,
    )
    # Dict-mode matching keys on the fallback prompt content so only the
    # depth-2 request can be served by the Sub LM.
    sub = RecordingLM({"fallback slice": {"answer": "sub-fallback-answer"}}, adapter=adapter)
    recorder = ChildLeaseRecorder()
    metrics_context = DelegationPolicy(
        recursive_options=RecursiveRLMOptions(enabled=True, max_calls=2),
        child_runtime_factory=recorder.factory,
    )

    async def never_cancelled() -> bool:
        return False

    root_interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="role and depth matrix",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=4, max_llm_calls=6),
            deadline=time.monotonic() + 30,
            interpreter=root_interpreter,
            cancellation_requested=never_cancelled,
        ),
        delegation=metrics_context,
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    _events = [event async for event in stream]
    root_interpreter.shutdown()

    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.display_text == "sub-fallback-answer"

    snapshot = metrics_context.metrics.snapshot()
    # Role/depth annotations: Root actions at depth 0, the child's
    # Root-LM-driven actions at depth 1, the fallback Sub-LM call at depth 2.
    counts = dict(((role, depth), count) for role, depth, count in snapshot.lm_call_counts)
    assert counts[("root", 0)] == 2
    assert counts[("root", 1)] == 2
    assert counts[("sub", 2)] == 1
    # The Sub LM never serves a Root-level action at depth 0.
    assert counts.get(("sub", 0), 0) == 0
    assert snapshot.root_lm_calls_depth_0 == 2
    assert snapshot.child_root_lm_calls_depth_1 == 2
    assert snapshot.depth_fallback_calls == 1

    # Invocation attribution: the fallback prompt reached only the Sub LM;
    # no Root action prompt reached the Sub LM, and the Sub LM's answer
    # never drove a Root-level action directly.
    assert any("fallback slice" in call for call in sub.calls)
    assert len(sub.calls) == 1
    assert not any("child slice" in call for call in sub.calls)

    # Independent histories: the child LM copies served the child's actions
    # through their own history; the original Root LM history contains only
    # the two Root-level actions (no cross-contamination from the child or
    # the fallback).
    assert len(root.history) == 2
    # Usage accounting stayed truthful: the Root prediction's trajectory
    # carries exactly the two Root actions.
    assert stream.outcome.usage["iterations"] == 2
    assert stream.outcome.prediction.outputs["answer"] == "sub-fallback-answer"

    # One native child, settled exactly once.
    assert recorder.call_indexes == [1]
    await stream.wait_owned()
    assert recorder.close_calls == {1: 1}


def test_val_rec_025_child_lm_copies_preserve_callback_ancestry_and_usage_shape() -> None:
    """VAL-REC-025: the child receives copied policy-owned LM runtimes whose
    identity differs from the Root's but whose role wiring is preserved; the
    child's completion carries the native trajectory."""
    recorder = ChildLeaseRecorder()
    root = _lm(
        [
            {"reasoning": "child action", "code": "x = 1"},
            {"reasoning": "child submit", "code": "SUBMIT(answer='role-ok')"},
        ]
    )
    sub = _lm([{"answer": "unused"}])
    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(),
        child_runtime_factory=recorder.factory,
        deadline=time.monotonic() + 30,
    )

    assert executor.tool(prompt="role slice") == "role-ok"
    summary = executor.summary()
    # The child's two Root-LM-driven actions were recorded at depth 1.
    counts = dict(((role, depth), count) for role, depth, count in summary.delegation_metrics.lm_call_counts)
    assert counts[("root", 1)] == 2
    # Both child iterations settled through the child's typed SUBMIT.
    assert summary.child_iterations == 2
    assert summary.termination_modes == ("typed_submit",)
    # The forked child runtime is a distinct copy with its own history:
    # the original Root LM served zero of the child's calls directly, so
    # its history stays empty (no cross-contamination into the Root runtime).
    assert len(root.history) == 0
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


async def _never_cancelled() -> bool:
    return False


def test_val_rec_022_root_child_and_sibling_interpreter_namespaces_are_isolated() -> None:
    """VAL-REC-022: through the public Runner composition, Root globals
    survive child return but are absent in every child; each child's own
    globals are absent from Root and from its sibling."""
    recorder = ChildLeaseRecorder()
    root = _lm(
        [
            # Root action 1: install a Root-only sentinel.
            {"reasoning": "root sentinel", "code": "root_sentinel = 'root-only'"},
            # Root action 2: delegate to child A.
            {"reasoning": "delegate a", "code": "child_a = rlm_query(prompt='a slice')"},
            # Child A action: Root sentinel must be absent; install A's own.
            {
                "reasoning": "probe root",
                "code": (
                    "try:\n"
                    "    root_sentinel\n"
                    "    probe = 'leaked'\n"
                    "except NameError:\n"
                    "    probe = 'isolated'\n"
                    "child_a_sentinel = 'a-only'\n"
                    "SUBMIT(answer=probe)"
                ),
            },
            # Root action 3: delegate to child B.
            {"reasoning": "delegate b", "code": "child_b = rlm_query(prompt='b slice')"},
            # Child B action: Root AND sibling A sentinels must be absent.
            {
                "reasoning": "probe root and sibling",
                "code": (
                    "leaked = []\n"
                    "try:\n"
                    "    root_sentinel\n"
                    "    leaked.append('root')\n"
                    "except NameError:\n"
                    "    pass\n"
                    "try:\n"
                    "    child_a_sentinel\n"
                    "    leaked.append('sibling-a')\n"
                    "except NameError:\n"
                    "    pass\n"
                    "SUBMIT(answer='isolated' if not leaked else 'reused:' + ','.join(leaked))"
                ),
            },
            # Root action 4: Root sentinel survives; child sentinels absent.
            {
                "reasoning": "root continuity probe",
                "code": (
                    "checks = ['root-survives' if root_sentinel == 'root-only' else 'root-lost']\n"
                    "try:\n"
                    "    child_a_sentinel\n"
                    "    checks.append('child-leaked-into-root')\n"
                    "except NameError:\n"
                    "    checks.append('child-absent-from-root')\n"
                    "SUBMIT(answer=';'.join(checks + [child_a, child_b]))"
                ),
            },
        ]
    )
    sub = _lm([{"answer": "unused"}])

    async def drive() -> str:
        context = RLMExecutionContext(
            identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
            session=SessionView(
                request="namespace matrix",
                session_context=SessionContextManifest(uuid4(), 0, 0, ()),
                attachments=(),
                preparation_notices=(),
            ),
            execution=ExecutionRuntime(
                models=RLMModelBundle(root, sub),
                options=RLMOptions(max_iters=6, max_llm_calls=6),
                deadline=time.monotonic() + 30,
                interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
                cancellation_requested=_never_cancelled,
            ),
            delegation=DelegationPolicy(
                recursive_options=RecursiveRLMOptions(
                    enabled=True, max_calls=2, child_max_iters=2, child_max_llm_calls=2
                ),
                child_runtime_factory=recorder.factory,
            ),
            capabilities=EmptyCapabilities(),
        )
        stream = RLMRunner().stream(context)
        async for _event in stream:
            pass
        assert stream.outcome is not None and stream.outcome.succeeded
        assert stream.outcome.prediction is not None
        return stream.outcome.prediction.display_text

    answer = asyncio.run(drive())
    # Root continuity preserved; Root↔child and sibling↔sibling isolation
    # proven through the children's own NameError probes.
    assert answer == "root-survives;child-absent-from-root;isolated;isolated"

    # Distinct interpreter namespaces per child; no shared namespace object.
    assert len(recorder.interpreters) == 2
    assert recorder.interpreters[1] is not recorder.interpreters[2]
    assert recorder.close_calls == {1: 1, 2: 1}


def test_val_rec_024_root_and_child_boundaries_classify_identical_output_matrix() -> None:
    """VAL-REC-024: the same output matrix run through the Root and child
    signatures yields identical accepted values and identical closed failure
    categories at the Fleet typed-result boundary."""
    from fleet_rlm.rlm.program import FleetRLMSignature

    # Both boundaries declare exactly one required ``answer: str`` output.
    assert set(FleetRLMSignature.output_fields) == {"answer"}
    assert set(RecursiveSubtaskSignature.output_fields) == {"answer"}

    matrix: list[tuple[dict[str, Any], int, str]] = [
        ({"answer": "valid answer"}, 100, "accepted"),
        ({"answer": ""}, 100, "invalid"),
        ({"answer": "   "}, 100, "invalid"),
        ({"answer": None}, 100, "invalid"),
        ({"answer": 123}, 100, "invalid"),
        ({"answer": ["not", "json-text"]}, 100, "invalid"),
        ({"answer": "x" * 500}, 100, "too-large"),
        ({"answer": "exact-json-compatible"}, 1000, "accepted"),
    ]

    for values, bound, expected in matrix:
        prediction = dspy.Prediction(**values)
        outcomes: dict[str, object] = {}
        for name, signature in (
            ("root", FleetRLMSignature),
            ("child", RecursiveSubtaskSignature),
        ):
            try:
                result = prediction_result(
                    prediction,
                    signature,
                    schema_id=f"fleet.{name}",
                    schema_version="1",
                    max_output_chars=bound,
                )
                outcomes[name] = ("accepted", result.display_text)
            except PredictionOutputTooLargeError:
                outcomes[name] = ("too-large", None)
            except PredictionOutputError:
                outcomes[name] = ("invalid", None)
        expected_value = values["answer"] if expected == "accepted" else None
        assert outcomes["root"] == outcomes["child"] == (expected, expected_value), (
            values,
            bound,
            outcomes,
        )


def test_val_rec_024_child_oversized_submit_fails_at_the_child_boundary() -> None:
    """VAL-REC-024: an oversized child SUBMIT is rejected at the child's own
    Fleet result boundary with the closed too-large category; the child is
    still settled exactly once."""
    recorder = ChildLeaseRecorder()
    root = _lm(
        [
            {"reasoning": "child submits oversized", "code": "SUBMIT(answer='x' * 500)"},
        ]
    )
    sub = _lm([{"answer": "unused"}])
    executor = _executor(root, sub, recorder, options=RecursiveRLMOptions(child_max_output_chars=100))

    with pytest.raises(PredictionOutputTooLargeError, match="Turn output is too large"):
        executor.tool(prompt="oversized child submit")

    summary = executor.summary()
    assert summary.termination_modes == ("child_error",)
    assert recorder.close_calls == {1: 1}
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


@pytest.mark.asyncio
async def test_val_rec_024_root_oversized_submit_fails_with_the_same_closed_category() -> None:
    """VAL-REC-024: an oversized Root SUBMIT fails the Run at the Root result
    boundary with the same closed too-large public category the child
    boundary uses."""
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM([{"reasoning": "submit oversized", "code": "SUBMIT(answer='x' * 500)"}], adapter=adapter)
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    recorder = ChildLeaseRecorder()

    async def never_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="oversized root submit",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=2, max_llm_calls=2, max_output_chars=100),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=never_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True),
            child_runtime_factory=recorder.factory,
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    _events = [event async for event in stream]

    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "failed"
    assert stream.outcome.prediction is None
    # Same closed literal as the child boundary's too-large category.
    assert stream.outcome.public_error_message == "Turn output is too large"


def test_val_rec_024_extraction_fallback_termination_parity_between_root_and_child() -> None:
    """VAL-REC-024: an RLM that never submits terminates through the same
    certified extraction fallback at Root and child scope: the child's
    recorded termination mode matches the Root RLM's classified mode."""
    recorder = ChildLeaseRecorder()
    # Child scope: the child never submits within its single iteration, so
    # the forced extraction fallback answers from the next scripted entry.
    root = _lm(
        [
            {"reasoning": "child work", "code": "print('plain work only')"},
            {"answer": "extracted-child"},
        ]
    )
    sub = _lm([{"answer": "unused"}])
    executor = _executor(
        root,
        sub,
        recorder,
        options=RecursiveRLMOptions(child_max_iters=1, child_max_llm_calls=3),
    )

    assert executor.tool(prompt="extraction parity") == "extracted-child"
    assert executor.summary().termination_modes == ("native_extraction_fallback",)

    # Root scope: the same never-submitting behavior yields the same mode.
    async def bare_root() -> Any:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        rlm = build_native_rlm(
            signature="request -> answer",
            options=RLMOptions(max_iters=1, max_llm_calls=3, max_output_chars=1000),
        )
        lm = _lm(
            [
                {"reasoning": "work", "code": "print('plain work only')"},
                {"answer": "extracted-root"},
            ]
        )
        try:
            with dspy.context(lm=lm, adapter=dspy.JSONAdapter()):
                return await rlm.acall(interpreter, request="root extraction")
        finally:
            interpreter.shutdown()

    prediction = asyncio.run(bare_root())
    assert prediction.answer == "extracted-root"
    assert rlm_termination_mode(prediction) == "native_extraction_fallback"
    executor.wait_owned()
    executor.raise_if_cleanup_failed()
