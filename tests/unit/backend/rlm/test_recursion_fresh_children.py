"""P39b fresh-native-runtime lanes for sequential Root children.

Behavior-only evidence for VAL-REC-002: two sequential Root ``rlm_query``
calls each build a new native ``dspy.RLM``, acquire a distinct lease with a
distinct interpreter object and Sandbox id, and close the first child before
its answer returns to Root. No interpreter, REPL history, broker closure, or
executable namespace is reused between calls: the second child starts from an
empty namespace and cannot resolve a global installed by the first.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import dspy
import pytest

from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.rlm.recursion import (
    RecursiveRLMExecutor,
    RecursiveRLMOptions,
)
from tests.unit.backend.rlm.fakes import ChildLeaseRecorder


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
