"""Recursion isolation and recursive content-safety contracts.

* ``test_recursion_isolation.py``: Behavior contracts for recursion isolation.
* ``test_recursion_content_safety.py``: recursive event content-safety lanes; only approved
  metadata may appear in recursive Runtime Events, Tool projections, and sanitized failures.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import fields
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.events import (
    RunFailed,
    Status,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
)
from fleet_rlm.rlm.execution import (
    DelegationPolicy,
    ExecutionRuntime,
    RLMExecutionContext,
    RLMRunner,
    RunIdentity,
    SessionView,
)
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
from fleet_rlm.sessions.context import SessionContextManifest
from fleet_rlm.sessions.models import TurnAccess
from tests.support.recursion_scheduler import RecursiveRLMExecutor
from tests.unit.backend.rlm.fakes import ChildLeaseRecorder, EmptyCapabilities


# --- from test_recursion_isolation.py ---------------------------------
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


def test_two_sequential_children_are_distinct_fresh_native_runtimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two sequential Root ``rlm_query`` calls build distinct
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
    first = executor.tool(capsule={"task": "first slice"})
    assert first["answer"] == "child-a-global"
    # The first child was closed exactly once BEFORE its answer returned.
    assert recorder.close_calls == {1: 1}
    assert recorder.interpreters[1]._shutdown
    assert built_rlms and type(built_rlms[0]).__name__ == "RLM"

    # Call 2: a second fresh native child starts from an empty namespace.
    second = executor.tool(capsule={"task": "second slice"})
    assert second["answer"] == "fresh"

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


def test_sequential_children_report_independent_completion_evidence() -> None:
    """Each sequential child's completion evidence is scoped to
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

    assert executor.tool(capsule={"task": "first slice"})["answer"] == "a-answer"
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

    assert executor.tool(capsule={"task": "second slice"})["answer"] == "b-answer"
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
        if isinstance(prompt, str):
            parts.append(prompt)
        for message in messages or []:
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
        self.calls.append("\n".join(parts))
        return super().forward(prompt=prompt, messages=messages, **kwargs)


@pytest.mark.asyncio
async def test_roles_depths_histories_and_trajectory_are_preserved_through_the_runner() -> None:
    """Through the public Runner composition, Root LM calls are
    recorded at depth 0, the native child's Root-LM-driven actions at depth
    1, and the depth fallback's Sub-LM call at depth 2; the Sub LM never
    drives Root actions; child LM histories are independent of the Root's;
    and the Root outcome carries the native trajectory."""
    adapter = dspy.JSONAdapter()
    root = RecordingLM(
        [
            # Root action 1: delegate to child 1.
            {"reasoning": "delegate", "code": "a = rlm_query(capsule={'task': 'child slice'})['answer']"},
            # Child 1 action 1: deeper delegation -> depth-2 fallback.
            {"reasoning": "child semantic", "code": "inner = llm_query('fallback slice')"},
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
    assert "sub-fallback-answer" in stream.outcome.prediction.answer

    snapshot = metrics_context.metrics.snapshot()
    # Role/depth annotations: Root actions at depth 0, the child's
    # Root-LM-driven actions at depth 1, the fallback Sub-LM call at depth 2.
    counts = dict(((role, depth), count) for role, depth, count in snapshot.lm_call_counts)
    assert counts[("root", 0)] == 2
    assert counts[("root", 1)] == 2
    assert counts[("sub", 1)] == 1
    # The Sub LM never serves a Root-level action at depth 0.
    assert counts.get(("sub", 0), 0) == 0
    assert snapshot.root_lm_calls_depth_0 == 2
    assert snapshot.child_root_lm_calls_depth_1 == 2

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
    assert "sub-fallback-answer" in stream.outcome.prediction.answer

    # One native child, settled exactly once.
    assert recorder.call_indexes == [1]
    await stream.wait_owned()
    assert recorder.close_calls == {1: 1}


def test_child_lm_copies_preserve_callback_ancestry_and_usage_shape() -> None:
    """The child receives copied policy-owned LM runtimes whose
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

    assert executor.tool(capsule={"task": "role slice"})["answer"] == "role-ok"
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


def test_root_child_and_sibling_interpreter_namespaces_are_isolated() -> None:
    """Through the public Runner composition, Root globals
    survive child return but are absent in every child; each child's own
    globals are absent from Root and from its sibling."""
    recorder = ChildLeaseRecorder()
    root = _lm(
        [
            # Root action 1: install a Root-only sentinel.
            {"reasoning": "root sentinel", "code": "root_sentinel = 'root-only'"},
            # Root action 2: delegate to child A.
            {"reasoning": "delegate a", "code": "child_a = rlm_query(capsule={'task': 'a slice'})['answer']"},
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
            {"reasoning": "delegate b", "code": "child_b = rlm_query(capsule={'task': 'b slice'})['answer']"},
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
        return stream.outcome.prediction.answer

    answer = asyncio.run(drive())
    # Root continuity preserved; Root↔child and sibling↔sibling isolation
    # proven through the children's own NameError probes.
    assert answer == "root-survives;child-absent-from-root;isolated;isolated"

    # Distinct interpreter namespaces per child; no shared namespace object.
    assert len(recorder.interpreters) == 2
    assert recorder.interpreters[1] is not recorder.interpreters[2]
    assert recorder.close_calls == {1: 1, 2: 1}


def test_root_and_child_boundaries_classify_identical_output_matrix() -> None:
    """The same output matrix run through the Root and child
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


def test_child_oversized_submit_fails_at_the_child_boundary() -> None:
    """An oversized child SUBMIT is rejected at the child's own
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

    outcome = executor.tool(capsule={"task": "oversized child submit"})
    assert outcome["status"] == "failed"
    assert outcome["answer"] == ""

    summary = executor.summary()
    assert summary.termination_modes == ("child_error",)
    assert recorder.close_calls == {1: 1}
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


@pytest.mark.asyncio
async def test_root_oversized_submit_fails_with_the_same_closed_category() -> None:
    """An oversized Root SUBMIT fails the Run at the Root result
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
            options=RLMOptions(max_iters=2, max_llm_calls=2, max_output_chars=100, max_final_output_chars=100),
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

    from fleet_rlm.rlm.result import project_outcome_prediction

    assert stream.outcome is not None
    projected = project_outcome_prediction(stream.outcome)
    assert projected.terminal_status == "failed"
    assert projected.prediction is None
    # Same closed literal as the child boundary's too-large category.
    assert projected.public_error_message == "Turn output is too large"


def test_extraction_fallback_termination_parity_between_root_and_child() -> None:
    """An RLM that never submits terminates through the same
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

    assert executor.tool(capsule={"task": "extraction parity"})["answer"] == "extracted-child"
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


# --- from test_recursion_content_safety.py ----------------------------
# Sentinel content injected into every private surface. Each token is unique so
# a leak can be attributed to the exact surface it was placed in. The
# credential sentinel uses the mission-mandated unmistakable non-secret canary
# shape.
SENTINEL_PROMPT = "QRE033-SECRET-PROMPT-bounded-child-task"
SENTINEL_ANSWER = "QRE033-SECRET-ANSWER-child-result-value"
SENTINEL_CREDENTIAL = "FAKE-CANARY-key-0000"
SENTINEL_PROVIDER_ID = "provider-internal-sbx-QRE033-XYZ"
SENTINEL_MOUNT_PATH = "/home/daytona/fleet/recursive/QRE033/secret-scope"
_SENTINELS = (
    SENTINEL_PROMPT,
    SENTINEL_ANSWER,
    SENTINEL_CREDENTIAL,
    SENTINEL_PROVIDER_ID,
    SENTINEL_MOUNT_PATH,
)


class _RootLM(dspy.utils.DummyLM):
    """Root LM whose copy() hands the child a distinct scripted runtime."""

    def __init__(self, answers: Any, child_lm: dspy.utils.DummyLM, *, adapter: Any) -> None:
        super().__init__(answers, adapter=adapter)
        self._child_lm = child_lm

    def copy(self, **kwargs: Any) -> Any:
        del kwargs
        return self._child_lm


async def _run_turn(
    *,
    root_actions: list[dict[str, Any]],
    child_answers: list[dict[str, Any]],
    sub_answers: list[dict[str, Any]],
    recorder: ChildLeaseRecorder,
    recursive_options: RecursiveRLMOptions,
    root_options: RLMOptions | None = None,
) -> tuple[list[Any], Any]:
    adapter = dspy.JSONAdapter()
    child_lm = _lm(child_answers)
    root_lm = _RootLM(root_actions, child_lm, adapter=adapter)
    sub_lm = _lm(sub_answers)

    async def never_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="content safety",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root_lm, sub_lm),
            options=root_options or RLMOptions(max_iters=3, max_llm_calls=5),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=never_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=recursive_options,
            child_runtime_factory=recorder.factory,
        ),
        capabilities=EmptyCapabilities(),
    )
    stream = RLMRunner().stream(context)
    events: list[Any] = []

    async def consume() -> None:
        async for event in stream:
            events.append(event)

    await asyncio.wait_for(consume(), timeout=20)
    return events, stream


def _detail_string_blobs(details: list[Any]) -> list[str]:
    """Collect every string carried by the given event details."""
    blobs: list[str] = []

    def walk(value: Any) -> None:
        if value is None or isinstance(value, (bool, int, float)):
            return
        if isinstance(value, str):
            blobs.append(value)
            return
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                walk(item)
            return
        blobs.append(repr(value))

    for detail in details:
        for item in fields(detail):
            walk(getattr(detail, item.name, None))
    return blobs


def _recursive_statuses(events: list[Any]) -> list[Status]:
    return [event.detail for event in events if isinstance(event.detail, Status) and event.detail.phase == "recursive"]


def _recursive_tool_details(events: list[Any]) -> list[Any]:
    return [
        event.detail
        for event in events
        if isinstance(event.detail, (ToolStarted, ToolCompleted, ToolFailed))
        and event.detail.tool_name in {"rlm_query", "rlm_query_batched"}
    ]


def _assert_sentinels_absent(*surfaces: list[Any]) -> None:
    """No sentinel may appear in any recursive evidence surface."""
    for surface in surfaces:
        joined = "\n".join(_detail_string_blobs(surface))
        for sentinel in _SENTINELS:
            assert sentinel not in joined, f"recursive evidence leaked sentinel {sentinel!r}"


@pytest.mark.asyncio
async def test_success_child_events_expose_only_approved_metadata() -> None:
    """Success: a completed depth-1 child emits bounded
    approved metadata only; sentinels planted in the prompt, answer,
    credential, provider id, and mount path never surface."""
    recorder = ChildLeaseRecorder(sandbox_prefix="content-safety-child", volume="content-safety-volume")
    child_prompt = (
        f"{SENTINEL_PROMPT} credential={SENTINEL_CREDENTIAL} "
        f"provider={SENTINEL_PROVIDER_ID} mount={SENTINEL_MOUNT_PATH}"
    )
    root_actions = [
        {
            "reasoning": "delegate one bounded child",
            "code": f"child_answer = rlm_query(capsule={{'task': {child_prompt!r}}})['answer']",
        },
        {"reasoning": "finish", "code": "SUBMIT(answer='root-done')"},
    ]
    child_answers = [
        {"reasoning": "answer the child", "code": f"SUBMIT(answer={SENTINEL_ANSWER!r})"},
    ]
    events, stream = await _run_turn(
        root_actions=root_actions,
        child_answers=child_answers,
        sub_answers=[{"answer": "unused"}],
        recorder=recorder,
        recursive_options=RecursiveRLMOptions(enabled=True, max_calls=1),
    )

    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "completed"

    statuses = _recursive_statuses(events)
    started = [status for status in statuses if status.status == "child_started"]
    completed = [status for status in statuses if status.status == "child_completed"]
    failed = [status for status in statuses if status.status == "child_failed"]

    # Exactly one start then one terminal, in order, for the one child.
    assert len(started) == 1
    assert len(completed) == 1
    assert failed == []
    assert statuses.index(started[0]) < statuses.index(completed[0])

    # Approved metadata is present on both recursive status events.
    start_message = started[0].message or ""
    assert "call_index=1" in start_message
    assert "recursive_depth=1" in start_message
    terminal_message = completed[0].message or ""
    assert "call_index=1" in terminal_message
    assert "recursive_depth=1" in terminal_message
    assert "duration_ms=" in terminal_message
    assert "cleanup_status=completed" in terminal_message

    # The recursive Tool input projection exposes only prompt counts/chars.
    tool_started = [detail for detail in _recursive_tool_details(events) if isinstance(detail, ToolStarted)]
    assert len(tool_started) == 1
    started_input = dict(tool_started[0].input or {})
    assert started_input["selected_input_bytes"] > len(child_prompt.encode("utf-8"))
    assert set(started_input) == {"selected_input_bytes"}

    # The recursive Tool output projection is the bounded completion metadata.
    tool_completed = [detail for detail in _recursive_tool_details(events) if isinstance(detail, ToolCompleted)]
    assert len(tool_completed) == 1
    output = dict(tool_completed[0].output or {})
    assert output == {
        "status": "completed",
        "call_index": 1,
        "recursive_depth": 1,
        "child_iterations": 1,
        "termination_mode": "typed_submit",
    }

    # No sentinel leaks through recursive status or Tool evidence.
    _assert_sentinels_absent(statuses, _recursive_tool_details(events))
    # The child lease settled exactly once through ownership.
    assert recorder.call_indexes == [1]
    assert recorder.close_calls == {1: 1}


@pytest.mark.asyncio
async def test_failed_child_events_stay_bounded_and_sentinel_free() -> None:
    """Failure: an oversized child answer fails closed with a
    bounded failure category and sanitized exception string; no sentinel
    leaks and child_started -> child_failed ordering is preserved."""
    recorder = ChildLeaseRecorder(sandbox_prefix="content-safety-child", volume="content-safety-volume")
    oversized_answer = f"{SENTINEL_ANSWER}-" + ("x" * 600)
    child_prompt = f"{SENTINEL_PROMPT} provider={SENTINEL_PROVIDER_ID} credential={SENTINEL_CREDENTIAL}"
    root_actions = [
        {
            "reasoning": "delegate one child that will oversubmit",
            "code": f"child_answer = rlm_query(capsule={{'task': {child_prompt!r}}})['answer']",
        },
        {"reasoning": "recover and finish", "code": "SUBMIT(answer='recovered')"},
    ]
    child_answers = [
        {"reasoning": "oversubmit", "code": f"SUBMIT(answer={oversized_answer!r})"},
    ]
    events, _stream = await _run_turn(
        root_actions=root_actions,
        child_answers=child_answers,
        sub_answers=[{"answer": "unused"}],
        recorder=recorder,
        recursive_options=RecursiveRLMOptions(enabled=True, max_calls=1, child_max_output_chars=64),
    )

    statuses = _recursive_statuses(events)
    started = [status for status in statuses if status.status == "child_started"]
    failed = [status for status in statuses if status.status == "child_failed"]
    completed = [status for status in statuses if status.status == "child_completed"]

    assert len(started) == 1
    assert len(failed) == 1
    assert completed == []
    assert statuses.index(started[0]) < statuses.index(failed[0])

    # The failed terminal carries bounded approved metadata, never content.
    failed_message = failed[0].message or ""
    assert "call_index=1" in failed_message
    assert "recursive_depth=1" in failed_message
    assert "duration_ms=" in failed_message
    assert "cleanup_status=" in failed_message
    assert "failure_category=" in failed_message

    # The recursive Tool failure projection is the closed sanitized public
    # message of the typed oversized-output failure, never the answer body.
    tool_results = [detail for detail in _recursive_tool_details(events) if isinstance(detail, ToolCompleted)]
    assert len(tool_results) == 1
    assert tool_results[0].output == {"status": "failed", "error_category": "child_failed"}

    # Run failure terminals, when present, are also sentinel-free.
    run_failed = [event.detail for event in events if isinstance(event.detail, RunFailed)]
    _assert_sentinels_absent(statuses, _recursive_tool_details(events), run_failed)
    assert recorder.call_indexes == [1]
    assert recorder.close_calls == {1: 1}
