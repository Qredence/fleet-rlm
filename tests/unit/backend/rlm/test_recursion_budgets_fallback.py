"""Capsule admission, shared reservations, and native semantic budget contracts."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import dspy
import pytest

from fleet_rlm.rlm.events import ToolCompleted
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.rlm.recursion import RecursiveRLMOptions, SubproblemCapsule
from tests.support.recursion_scheduler import RecursiveRLMExecutor
from tests.unit.backend.rlm.fakes import ChildLeaseRecorder


def _lm(answers: Any) -> dspy.utils.DummyLM:
    return dspy.utils.DummyLM(answers, adapter=dspy.JSONAdapter())


def _executor(
    root: dspy.utils.DummyLM,
    recorder: ChildLeaseRecorder,
    *,
    options: RecursiveRLMOptions | None = None,
    sub: dspy.utils.DummyLM | None = None,
    observer=None,
) -> RecursiveRLMExecutor:
    return RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub or _lm([{"answer": "unused"}])),
        options=options or RecursiveRLMOptions(),
        child_runtime_factory=recorder.factory,
        deadline=time.monotonic() + 30,
        observer=observer,
    )


@pytest.mark.parametrize(
    "capsule",
    [
        None,
        123,
        "prompt",
        [],
        {"prompt": "old shape"},
        {},
        {"task": ""},
        {"task": "  "},
        {"task": 42},
        {"task": "x", "allocation_chars": 1000},
        {"task": "x", "authorized_references": ["../secret"]},
        {"task": "x" * 50_001},
    ],
)
def test_invalid_single_capsule_never_mutates_admission(capsule: object) -> None:
    recorder = ChildLeaseRecorder()
    executor = _executor(_lm([{"answer": "unused"}]), recorder)
    with pytest.raises((ValueError, TypeError)):
        executor.tool(capsule=capsule)
    assert executor.summary().call_count == 0
    assert executor.summary().delegated_prompt_chars == 0
    assert recorder.call_indexes == []


@pytest.mark.parametrize(
    "capsules",
    [
        None,
        123,
        "prompts",
        {},
        [],
        [{"task": "valid"}, 42],
        [{"task": "valid"}, {"task": ""}],
        [{"task": "valid"}, {"task": "x", "authorized_references": ["../secret"]}],
        [{"task": "valid"}, {"task": "x" * 50_001}],
    ],
)
def test_complete_batch_is_validated_before_any_reservation(capsules: object) -> None:
    recorder = ChildLeaseRecorder()
    executor = _executor(_lm([{"answer": "unused"}]), recorder)
    with pytest.raises((ValueError, TypeError)):
        executor.batched_tool(capsules=capsules)
    summary = executor.summary()
    assert summary.call_count == 0
    assert summary.delegated_prompt_chars == 0
    assert summary.recursive_batch_calls == 0
    assert recorder.call_indexes == []


def test_capsule_size_limit_precedes_child_admission() -> None:
    recorder = ChildLeaseRecorder()
    executor = _executor(_lm([{"answer": "unused"}]), recorder, options=RecursiveRLMOptions(max_prompt_chars=10))
    with pytest.raises(ValueError, match="prompt bound"):
        executor.tool(capsule={"task": "valid but envelope too large"})
    assert executor.summary().call_count == 0
    assert recorder.call_indexes == []


def test_capsule_ledger_charges_complete_normalized_serialization() -> None:
    recorder = ChildLeaseRecorder()
    executor = _executor(
        _lm(
            {
                "zzq": {"reasoning": "a", "code": "SUBMIT(answer='A')"},
                "wwk": {"reasoning": "b", "code": "SUBMIT(answer='B')"},
            }
        ),
        recorder,
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2),
    )
    capsules = [{"task": "  zzq  "}, {"task": "wwk", "fragments": ["évidence"]}]
    outcomes = executor.batched_tool(capsules=capsules)
    assert [item["answer"] for item in outcomes] == ["A", "B"]
    sizes = [SubproblemCapsule.from_mapping(item).serialized_bytes for item in capsules]
    assert executor.summary().delegated_prompt_chars == sum(sizes)
    assert executor.summary().maximum_prompt_chars == max(sizes)
    assert sorted(recorder.close_calls.values()) == [1, 1]


def test_atomic_reservations_under_competing_single_and_batch_pressure() -> None:
    recorder = ChildLeaseRecorder()
    responses = {
        "single-1": {"reasoning": "single", "code": "SUBMIT(answer='S1')"},
        "batch-1": {"reasoning": "first", "code": "SUBMIT(answer='B1')"},
        "batch-2": {"reasoning": "second", "code": "SUBMIT(answer='B2')"},
    }
    executor = _executor(_lm(responses), recorder, options=RecursiveRLMOptions(max_calls=3, max_parallel_children=3))
    barrier = threading.Barrier(2)
    results: dict[str, Any] = {}

    def single() -> None:
        barrier.wait()
        results["single"] = executor.tool(capsule={"task": "single-1"})

    def batch() -> None:
        barrier.wait()
        results["batch"] = executor.batched_tool(capsules=[{"task": "batch-1"}, {"task": "batch-2"}])

    workers = [threading.Thread(target=single), threading.Thread(target=batch)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    assert not any(worker.is_alive() for worker in workers)
    assert results["single"]["answer"] == "S1"
    assert [item["answer"] for item in results["batch"]] == ["B1", "B2"]
    assert sorted(recorder.call_indexes) == [1, 2, 3]
    before = executor.summary()
    with pytest.raises(RuntimeError, match="budget exhausted"):
        executor.batched_tool(capsules=[{"task": "late-1"}, {"task": "late-2"}])
    assert executor.summary().call_count == before.call_count == 3
    assert executor.summary().delegated_prompt_chars == before.delegated_prompt_chars
    assert sorted(recorder.call_indexes) == [1, 2, 3]
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


def test_native_semantic_calls_do_not_consume_recursive_child_slots() -> None:
    recorder = ChildLeaseRecorder()
    executor = _executor(
        _lm(
            [
                {"reasoning": "semantic", "code": "inner = llm_query('selected judgment')"},
                {"reasoning": "submit", "code": "SUBMIT(answer=inner)"},
            ]
        ),
        recorder,
        options=RecursiveRLMOptions(max_calls=1),
        sub=_lm([{"answer": "semantic-answer"}]),
    )
    outcome = executor.tool(capsule={"task": "one iterative child"})
    assert outcome["status"] == "completed"
    assert "semantic-answer" in outcome["answer"]
    assert recorder.call_indexes == [1]
    summary = executor.summary()
    assert summary.call_count == 1
    assert summary.depth_fallback_count == 0
    assert summary.delegation_metrics.lm_call_counts == (("root", 1, 2), ("sub", 1, 1))
    with pytest.raises(RuntimeError, match="budget exhausted"):
        executor.batched_tool(capsules=[{"task": "no child slot remains"}])
    assert recorder.call_indexes == [1]


def test_native_child_receives_options_and_enforces_semantic_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.rlm.recursion as recursion

    captured = []
    build = recursion.build_native_rlm

    def spy_build(**kwargs):
        captured.append(kwargs["options"])
        return build(**kwargs)

    monkeypatch.setattr(recursion, "build_native_rlm", spy_build)
    recorder = ChildLeaseRecorder()
    executor = _executor(
        _lm(
            [
                {"reasoning": "use budget", "code": "first = llm_query('q1')"},
                {"reasoning": "exceed budget", "code": "second = llm_query('q2')"},
                {"reasoning": "submit", "code": "SUBMIT(answer='budget-terminal')"},
            ]
        ),
        recorder,
        sub=_lm([{"answer": "sub-one"}, {"answer": "must-not-be-called"}]),
        options=RecursiveRLMOptions(child_max_iters=4, child_max_llm_calls=1, child_max_output_chars=200),
    )
    assert executor.tool(capsule={"task": "bounded child"})["answer"] == "budget-terminal"
    assert len(captured) == 1
    assert (captured[0].max_iters, captured[0].max_llm_calls, captured[0].max_output_chars) == (4, 1, 200)
    calls = executor.summary().delegation_metrics.lm_call_counts
    assert [(depth, count) for role, depth, count in calls if role == "sub"] == [(1, 1)]
    assert recorder.call_indexes == [1]
    assert recorder.close_calls == {1: 1}


def test_partial_results_are_ordered_but_answers_never_enter_events(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.rlm.recursion as recursion

    class Child:
        def __call__(self, _interpreter, *, prompt):
            if json.loads(prompt)["task"] == "fail":
                raise ValueError("private-primary-cause")
            return dspy.Prediction(answer="successful-sibling-answer", trajectory=[])

    monkeypatch.setattr(recursion, "build_native_rlm", lambda **_kwargs: Child())
    recorder = ChildLeaseRecorder()
    events = []
    executor = _executor(
        _lm([{"answer": "unused"}]),
        recorder,
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2),
        observer=events.append,
    )
    outcomes = executor.batched_tool(capsules=[{"task": "fail"}, {"task": "ok"}])
    assert [item["status"] for item in outcomes] == ["failed", "completed"]
    assert outcomes[0]["answer"] == ""
    assert outcomes[1]["answer"] == "successful-sibling-answer"
    assert any(isinstance(event, ToolCompleted) and event.output.get("answer_count") == 2 for event in events)
    assert "successful-sibling-answer" not in repr(events)
    assert "private-primary-cause" not in repr(events)
    executor.wait_owned()
    executor.raise_if_cleanup_failed()
    assert sorted(recorder.close_calls.values()) == [1, 1]
