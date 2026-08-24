"""P39b interpreter namespace isolation and typed-result parity lanes.

Behavior-only evidence:

- VAL-REC-022: Root and each child have independent Python globals and
  executable namespaces. Root globals survive child return but are absent in
  the child; child globals and broker-installed closures are absent from
  Root and every sibling.
- VAL-REC-024: Root and child preserve identical handling at the Fleet typed
  result boundary for accepted, rejected, and oversized output values; both
  retain the certified extraction-fallback termination semantics.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLease
from fleet_rlm.rlm.context import (
    DelegationPolicy,
    ExecutionRuntime,
    RLMExecutionContext,
    RunIdentity,
    SessionView,
)
from fleet_rlm.rlm.dspy_contract import (
    PredictionOutputError,
    PredictionOutputTooLargeError,
    RLMOptions,
    build_native_rlm,
    prediction_result,
    rlm_termination_mode,
)
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.recursive_calls import (
    RecursiveRLMExecutor,
    RecursiveRLMOptions,
    RecursiveSubtaskSignature,
)
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.sessions.models import TurnAccess
from tests.unit.backend.rlm.fakes import EmptyCapabilities


class _Recorder:
    def __init__(self) -> None:
        self.interpreters: list[DaytonaCodeInterpreter] = []
        self.close_calls: dict[int, int] = {}

    def factory(self, call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        self.interpreters.append(interpreter)

        def close() -> None:
            self.close_calls[call_index] = self.close_calls.get(call_index, 0) + 1
            interpreter.shutdown()

        return ChildRuntimeLease(
            interpreter,
            f"ns-child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            close,
        )


def _executor(
    root_lm: dspy.utils.DummyLM,
    sub_lm: dspy.utils.DummyLM,
    recorder: _Recorder,
    *,
    options: RecursiveRLMOptions | None = None,
) -> RecursiveRLMExecutor:
    return RecursiveRLMExecutor(
        models=RLMModelBundle(root_lm, sub_lm),
        options=options or RecursiveRLMOptions(),
        child_runtime_factory=recorder.factory,
        deadline=time.monotonic() + 30,
    )


def _lm(answers: Any) -> dspy.utils.DummyLM:
    return dspy.utils.DummyLM(answers, adapter=dspy.JSONAdapter())


async def _never_cancelled() -> bool:
    return False


def test_val_rec_022_root_child_and_sibling_interpreter_namespaces_are_isolated() -> None:
    """VAL-REC-022: through the public Runner composition, Root globals
    survive child return but are absent in every child; each child's own
    globals are absent from Root and from its sibling."""
    recorder = _Recorder()
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
    assert recorder.interpreters[0] is not recorder.interpreters[1]
    assert recorder.close_calls == {1: 1, 2: 1}


def test_val_rec_024_root_and_child_boundaries_classify_identical_output_matrix() -> None:
    """VAL-REC-024: the same output matrix run through the Root and child
    signatures yields identical accepted values and identical closed failure
    categories at the Fleet typed-result boundary."""
    from fleet_rlm.rlm.signature import FleetRLMSignature

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
    recorder = _Recorder()
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
    recorder = _Recorder()

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
    recorder = _Recorder()
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
