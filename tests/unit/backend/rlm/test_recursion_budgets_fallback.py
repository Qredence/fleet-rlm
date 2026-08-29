"""P39b recursion budget, prompt-validation, and fallback contract lanes.

Behavior-only evidence for the policy-owned recursion surface:

- VAL-REC-004: depth-2 fallback is bounded and policy-owned (Sub LM only,
  deadline/budget/authority gates, typed output bound).
- VAL-REC-006: batch reservations are atomic, including under competing
  single/batch reservation pressure.
- VAL-REC-007: prompt validation precedes reservation and allocation for the
  complete invalid single/batch matrix.
- VAL-REC-008: the call-index and prompt-byte ledger is shared across
  singles, batch members, and calls initiated inside a native child.
- VAL-REC-009: native children receive the exact certified child options and
  their sub-LM call budget is enforced without unreserved retries.
- VAL-REC-010: exhausting the child sub-LM budget is terminal for that budget
  (the over-limit call never reaches the Sub LM).
- VAL-REC-011: batch settlement is all-or-nothing (no partial answer list,
  no exposed successful sibling answers).
- VAL-REC-025: model role/depth accounting is preserved across Root, native
  child, and depth-2 fallback.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import dspy
import pytest

from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.rlm.recursion import (
    RecursiveBatchError,
    RecursiveRLMExecutor,
    RecursiveRLMOptions,
)
from tests.unit.backend.rlm.fakes import ChildLeaseRecorder


class RecordingLM(dspy.utils.DummyLM):
    """A list-scripted dummy LM that records the full rendered prompts.

    DSPy renders every request into a messages list before the LM call; the
    recorded content gives the lane an exact, role-agnostic invocation ledger
    without touching any private executor symbol.
    """

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


def _executor(
    root_lm: dspy.utils.DummyLM,
    sub_lm: dspy.utils.DummyLM,
    recorder: ChildLeaseRecorder | None,
    *,
    options: RecursiveRLMOptions | None = None,
    deadline: float | None = None,
    observer: Callable[[object], None] | None = None,
    is_authorized: Callable[[], bool] | None = None,
) -> RecursiveRLMExecutor:
    return RecursiveRLMExecutor(
        models=RLMModelBundle(root_lm, sub_lm),
        options=options or RecursiveRLMOptions(),
        child_runtime_factory=recorder.factory if recorder is not None else None,
        deadline=deadline if deadline is not None else time.monotonic() + 30,
        observer=observer,
        is_authorized=is_authorized,
    )


def _lm(answers: Any) -> dspy.utils.DummyLM:
    return dspy.utils.DummyLM(answers, adapter=dspy.JSONAdapter())


def _recording_lm(answers: list[dict[str, Any]]) -> RecordingLM:
    return RecordingLM(answers, adapter=dspy.JSONAdapter())


def test_val_rec_004_depth_fallback_uses_only_the_policy_sub_lm() -> None:
    """VAL-REC-004: a depth-2 request is served exclusively by the
    policy-owned Sub LM. The Root LM has no answer for the fallback prompt,
    so using it would fail parsing; the Sub LM answers it, and the delegation
    ledger records exactly one sub-role call at depth 2 and no root-role call
    at depth 2."""
    recorder = ChildLeaseRecorder()
    # Root LM (drives child actions) has NO entry for the fallback prompt.
    root = _lm(
        [
            {"reasoning": "delegate deeper", "code": "inner = rlm_query(prompt='fallback-slice')"},
            {"reasoning": "submit", "code": "SUBMIT(answer=inner)"},
        ]
    )
    # Sub LM answers only the fallback prompt.
    sub = _lm({"fallback-slice": {"answer": "sub-fallback-answer"}})
    executor = _executor(root, sub, recorder)

    assert executor.tool(prompt="outer slice") == "sub-fallback-answer"

    snapshot = executor.summary().delegation_metrics
    # Exactly one Sub-LM call at depth 2; no Root LM served the fallback.
    assert snapshot.lm_call_counts == (("root", 1, 2), ("sub", 2, 1))
    assert snapshot.depth_fallback_calls == 1
    # One native child Sandbox, one extra reservation for the fallback.
    assert recorder.call_indexes == [1]
    assert executor.summary().call_count == 2
    assert executor.summary().termination_modes == ("depth_fallback", "typed_submit")


def test_val_rec_004_depth_fallback_consumes_the_shared_ledger_without_a_sandbox() -> None:
    """VAL-REC-004: the depth-2 fallback consumes the shared call-index and
    prompt-byte ledger and is rejected once the budget is exhausted, all
    without allocating a Sandbox or invoking any LM for the rejected request."""
    recorder = ChildLeaseRecorder()
    root = _lm(
        [
            {"reasoning": "delegate deeper", "code": "inner = rlm_query(prompt='over-budget')"},
            {"reasoning": "submit", "code": "SUBMIT(answer='settled')"},
        ]
    )
    sub = _lm({"over-budget": {"answer": "must-not-be-served"}})
    executor = _executor(root, sub, recorder, options=RecursiveRLMOptions(max_calls=1))

    assert executor.tool(prompt="outer slice") == "settled"

    snapshot = executor.summary().delegation_metrics
    # Budget exhaustion rejected the nested fallback before any Sub LM call.
    assert not any(role == "sub" for role, _depth, _count in snapshot.lm_call_counts)
    assert snapshot.depth_fallback_calls == 0
    # Only the outer reservation was ever made; nothing was partially added.
    assert executor.summary().call_count == 1
    assert recorder.call_indexes == [1]
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


def test_val_rec_004_expired_deadline_rejects_fallback_before_reservation() -> None:
    """VAL-REC-004: once the absolute deadline has passed, the depth-2
    fallback path is never entered: no second call index is reserved, no
    Sandbox is allocated, the Sub LM never receives the late prompt, and the
    parent outcome fails closed with the deadline category."""
    recorder = ChildLeaseRecorder()
    # Burn the deadline and then attempt the nested delegation inside ONE
    # child REPL cell, so the child's Root LM action is produced before the
    # deadline and only the nested reservation gate rejects it.
    root = _recording_lm(
        [
            {
                "reasoning": "burn then delegate",
                "code": ("import time\ntime.sleep(0.15)\ninner = rlm_query(prompt='late-fallback')"),
            },
            {"reasoning": "submit", "code": "SUBMIT(answer='after-late-fallback')"},
        ]
    )
    sub = _recording_lm([{"answer": "must-not-be-served"}])
    deadline = time.monotonic() + 0.05
    executor = _executor(root, sub, recorder, deadline=deadline, options=RecursiveRLMOptions(max_calls=2))

    # The child LM fork refuses every call past the deadline, so the child
    # settles with the bounded deadline error.
    began = time.monotonic()
    with pytest.raises(TimeoutError):
        executor.tool(prompt="outer slice")
    assert time.monotonic() - began < 5

    summary = executor.summary()
    # The outer reservation is the only one: the nested attempt never
    # reached reservation, allocation, or the Sub LM.
    assert summary.call_count == 1
    assert summary.depth_fallback_count == 0
    assert recorder.call_indexes == [1]
    assert not any("late-fallback" in call for call in sub.calls)
    assert "after-late-fallback" not in repr(summary.termination_modes)
    # The fenced child still settles exactly once under the ownership boundary.
    executor.wait_owned()
    executor.raise_if_cleanup_failed()
    assert sorted(recorder.close_calls.values()) == [1]


def test_val_rec_004_revoked_authority_discards_late_fallback_result() -> None:
    """VAL-REC-004: revoking authority after the Sub LM answered discards the
    fallback result through the same authorization fence."""
    recorder = ChildLeaseRecorder()
    root = _lm(
        [
            {"reasoning": "delegate deeper", "code": "inner = rlm_query(prompt='revoked-fallback')"},
            {"reasoning": "submit", "code": "SUBMIT(answer=inner)"},
        ]
    )
    sub = _lm({"revoked-fallback": {"answer": "late-fallback-answer"}})
    checks = 0

    def is_authorized() -> bool:
        # Authorized through reservation/acquisition/execution; revoked just
        # before the fallback result would settle.
        nonlocal checks
        checks += 1
        return checks < 6

    executor = _executor(root, sub, recorder, is_authorized=is_authorized)

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(prompt="outer slice")

    # The late fallback answer was discarded, never returned to Root code.
    assert executor.summary().termination_modes == ("child_error",)
    assert recorder.call_indexes == [1]
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


def test_val_rec_004_oversized_fallback_output_fails_typed_validation() -> None:
    """VAL-REC-004: the fallback answer is validated under
    ``child_max_output_chars`` at the typed-result boundary: an oversized
    Sub-LM answer fails closed as a bounded error inside the child REPL and
    never becomes a child result or escapes into Root-visible content."""
    recorder = ChildLeaseRecorder()
    # The child script observes the rejection: the oversized fallback raises
    # the typed output error at the boundary, and the child submits a short
    # bounded marker instead of any oversized content.
    root = _lm(
        [
            {
                "reasoning": "delegate deeper",
                "code": (
                    "result = 'unset'\n"
                    "try:\n"
                    "    result = rlm_query(prompt='oversized-fallback')\n"
                    "except BaseException:\n"
                    "    result = 'rejected'\n"
                    "SUBMIT(answer=result)"
                ),
            },
        ]
    )
    sub = _lm({"oversized-fallback": {"answer": "x" * 500}})
    # Bound rejects the 500-char fallback answer but accepts the short marker
    # the child submits after observing the rejection.
    executor = _executor(root, sub, recorder, options=RecursiveRLMOptions(max_calls=2, child_max_output_chars=64))

    answer = executor.tool(prompt="outer slice")
    assert answer == "rejected"
    assert "x" * 17 not in answer

    summary = executor.summary()
    # The depth-2 fallback ran (one attempt, one failure category) but its
    # oversized output was rejected at the typed boundary; the native child
    # then settled with its own bounded marker. No Sandbox for the fallback.
    assert summary.depth_fallback_count == 1
    assert recorder.call_indexes == [1]
    assert summary.call_count == 2
    assert summary.termination_modes == ("child_error", "typed_submit")
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


@pytest.mark.parametrize(
    ("invalid_prompt", "message"),
    [
        (123, "is not of type"),
        (None, "is not of type"),
        ({"prompt": "x"}, "is not of type"),
        (["x"], "is not of type"),
        (b"bytes", "is not of type"),
    ],
    ids=repr,
)
def test_val_rec_007_non_text_single_prompt_fails_before_any_mutation(invalid_prompt: object, message: str) -> None:
    """VAL-REC-007: non-text prompts fail closed with the bounded Tool-surface
    schema rejection before the executor body, budget mutation, or child
    acquisition."""
    recorder = ChildLeaseRecorder()
    executor = _executor(_lm([{"answer": "unused"}]), _lm([{"answer": "unused"}]), recorder)

    with pytest.raises(ValueError, match=message):
        executor.tool(prompt=invalid_prompt)  # type: ignore[arg-type]

    summary = executor.summary()
    assert summary.call_count == 0
    assert summary.delegated_prompt_chars == 0
    assert summary.delegation_metrics.recursive_child_calls == 0
    assert recorder.call_indexes == []


@pytest.mark.parametrize(
    ("invalid_prompt", "message"),
    [("", "must not be empty"), ("   \n\t ", "must not be empty"), ("x" * 11, "character bound")],
    ids=["empty", "blank", "oversized"],
)
def test_val_rec_007_blank_or_oversized_single_prompt_fails_before_any_mutation(
    invalid_prompt: str, message: str
) -> None:
    """VAL-REC-007: blank and oversized text prompts fail in the executor
    validation before reservation."""
    recorder = ChildLeaseRecorder()
    executor = _executor(
        _lm([{"answer": "unused"}]),
        _lm([{"answer": "unused"}]),
        recorder,
        options=RecursiveRLMOptions(max_prompt_chars=10),
    )

    with pytest.raises(ValueError, match=message):
        executor.tool(prompt=invalid_prompt)

    assert executor.summary().call_count == 0
    assert executor.summary().delegated_prompt_chars == 0
    assert recorder.call_indexes == []


@pytest.mark.parametrize(
    ("invalid_batch", "message"),
    [
        # Non-list shapes are rejected by the Tool's declared input schema
        # before the executor body runs.
        ("not a list", "is not of type"),
        ({"prompts": ["x"]}, "is not of type"),
        (123, "is not of type"),
        # List-shaped inputs are rejected by the executor validation body.
        ([], "must not be empty"),
        (["valid", 42], "is not of type"),
        (["valid", "  "], "must not be empty"),
        (["valid", "x" * 11], "character bound"),
        ([None], "is not of type"),
    ],
    ids=["string", "dict", "int", "empty-list", "mixed-non-text", "mixed-blank", "mixed-oversized", "none-member"],
)
def test_val_rec_007_invalid_batch_prompts_fail_before_reservation_or_allocation(
    invalid_batch: object, message: str
) -> None:
    """VAL-REC-007: every invalid batch shape fails closed before any call
    index, prompt byte, worker, or Sandbox is allocated; accounting cannot be
    bypassed with whitespace or mixed invalid entries."""
    recorder = ChildLeaseRecorder()
    executor = _executor(
        _lm([{"answer": "unused"}]),
        _lm([{"answer": "unused"}]),
        recorder,
        options=RecursiveRLMOptions(max_prompt_chars=10),
    )

    with pytest.raises(ValueError, match=message):
        executor.batched_tool(prompts=invalid_batch)  # type: ignore[arg-type]

    summary = executor.summary()
    assert summary.call_count == 0
    assert summary.delegated_prompt_chars == 0
    assert summary.recursive_batch_calls == 0
    assert summary.delegation_metrics.recursive_child_calls == 0
    assert recorder.call_indexes == []


def test_val_rec_007_prompt_accounting_uses_normalized_prompts() -> None:
    """VAL-REC-007: accepted prompts are normalized (stripped) before byte
    accounting; the ledger records normalized lengths only."""
    recorder = ChildLeaseRecorder()
    # Dict-mode Root LM matches by substring containment; these padded
    # prompts share no substring with any batch sibling key.
    root = _lm(
        {
            "zzq": {"reasoning": "a", "code": "SUBMIT(answer='A')"},
            "wwk": {"reasoning": "b", "code": "SUBMIT(answer='B')"},
        }
    )
    sub = _lm([{"answer": "unused"}])
    executor = _executor(root, sub, recorder, options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2))

    assert executor.batched_tool(prompts=["  zzq-padded  ", "wwk-padded\n"]) == ["A", "B"]
    # Normalized byte accounting: stripped prompts only.
    assert executor.summary().delegated_prompt_chars == len("zzq-padded") + len("wwk-padded")
    assert executor.summary().maximum_prompt_chars == len("zzq-padded")


def test_val_rec_006_atomic_reservation_under_competing_single_and_batch_pressure() -> None:
    """VAL-REC-006: competing single and batch reservations under one lock
    allocate unique, contiguous call indexes; an over-budget batch racing the
    same ledger never partially reserves."""
    recorder = ChildLeaseRecorder()
    responses = {
        "single-1": {"reasoning": "s1", "code": "SUBMIT(answer='S1')"},
        "batch-1": {"reasoning": "b1", "code": "SUBMIT(answer='B1')"},
        "batch-2": {"reasoning": "b2", "code": "SUBMIT(answer='B2')"},
    }
    executor = _executor(
        _lm(responses),
        _lm([{"answer": "unused"}]),
        recorder,
        options=RecursiveRLMOptions(max_calls=3, max_parallel_children=3),
    )
    barrier = threading.Barrier(2)
    outcomes: dict[str, object] = {}

    def run_single() -> None:
        barrier.wait()
        outcomes["single"] = executor.tool(prompt="single-1")

    def run_batch() -> None:
        barrier.wait()
        outcomes["batch"] = executor.batched_tool(prompts=["batch-1", "batch-2"])

    threads = [threading.Thread(target=run_single), threading.Thread(target=run_batch)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert not any(thread.is_alive() for thread in threads)

    assert outcomes["single"] == "S1"
    assert outcomes["batch"] == ["B1", "B2"]
    # Unique, contiguous indexes exactly once each.
    assert sorted(recorder.call_indexes) == [1, 2, 3]
    assert len(set(recorder.call_indexes)) == 3
    assert executor.summary().call_count == 3

    # A competing over-budget batch is rejected atomically: no partial index,
    # no prompt byte, no worker for any of its members.
    with pytest.raises(RuntimeError, match="budget exhausted"):
        executor.batched_tool(prompts=["late-1"])
    assert sorted(recorder.call_indexes) == [1, 2, 3]
    assert executor.summary().call_count == 3
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


def test_val_rec_008_shared_ledger_across_singles_batch_and_in_child_calls() -> None:
    """VAL-REC-008: one invocation-scoped ledger serves a Root single, a
    depth-2 call initiated inside the native child, and a Root batch, with
    monotonically unique indexes and exact cumulative normalized bytes."""
    recorder = ChildLeaseRecorder()
    # Dict-mode Root LM matches each prompt by substring: it drives the
    # single child's nested delegation AND each batch child's action through
    # one shared LM, exactly as the production fork does. The nested depth-2
    # request is served by the Sub LM.
    root = _lm(
        {
            "single-slice": {
                "reasoning": "delegate inside child",
                "code": "inner = rlm_query(prompt='nested-slice')\nSUBMIT(answer=inner)",
            },
            "batch-a": {"reasoning": "a", "code": "SUBMIT(answer='A')"},
            "batch-b": {"reasoning": "b", "code": "SUBMIT(answer='B')"},
        }
    )
    sub = _lm({"nested-slice": {"answer": "nested-answer"}})
    executor = _executor(root, sub, recorder, options=RecursiveRLMOptions(max_calls=4, max_parallel_children=2))

    assert executor.tool(prompt="single-slice") == "nested-answer"
    assert executor.batched_tool(prompts=["batch-a", "batch-b"]) == ["A", "B"]

    summary = executor.summary()
    # Single (1) + nested depth-2 (1) + batch (2): unique contiguous indexes.
    assert summary.call_count == 4
    assert sorted(recorder.call_indexes) == [1, 3, 4]
    # The nested depth-2 call reserved index 2 without allocating a Sandbox.
    assert 2 not in recorder.call_indexes
    assert summary.delegated_prompt_chars == len("single-slice") + len("nested-slice") + len("batch-a") + len("batch-b")
    assert summary.depth_fallback_count == 1
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


def test_val_rec_008_budget_consumed_by_in_child_call_is_unavailable_to_later_siblings() -> None:
    """VAL-REC-008: a call budget consumed by a request initiated inside a
    native child is unavailable to a later Root batch: the batch is rejected
    atomically before any sibling worker or Sandbox."""
    recorder = ChildLeaseRecorder()
    root = _lm(
        [
            {"reasoning": "delegate inside child", "code": "inner = rlm_query(prompt='nested-slice')"},
            {"reasoning": "submit inner", "code": "SUBMIT(answer=inner)"},
        ]
    )
    sub = _lm({"nested-slice": {"answer": "nested-answer"}})
    executor = _executor(root, sub, recorder, options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2))

    assert executor.tool(prompt="single-slice") == "nested-answer"
    # The nested call consumed the second index; a two-member batch cannot fit.
    with pytest.raises(RuntimeError, match="budget exhausted"):
        executor.batched_tool(prompts=["batch-a", "batch-b"])
    assert sorted(recorder.call_indexes) == [1]
    assert executor.summary().call_count == 2
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


def test_val_rec_009_child_receives_exact_certified_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-009: the native child is built with the exact certified
    ``child_max_iters``/``child_max_llm_calls``/``child_max_output_chars``
    options, one reservation and one Sandbox per child."""
    import fleet_rlm.rlm.recursion as recursive_calls

    captured_options: list[Any] = []
    real_build = recursive_calls.build_native_rlm

    def spy_build(**kwargs: object):
        captured_options.append(kwargs["options"])
        return real_build(**kwargs)

    monkeypatch.setattr(recursive_calls, "build_native_rlm", spy_build)
    recorder = ChildLeaseRecorder()
    root = _lm(
        [
            {"reasoning": "use the budget", "code": "first = llm_query('q1')"},
            {"reasoning": "submit", "code": "SUBMIT(answer='done')"},
        ]
    )
    sub = _lm([{"answer": "sub-one"}, {"answer": "sub-two"}])
    executor = _executor(
        root,
        sub,
        recorder,
        options=RecursiveRLMOptions(child_max_iters=3, child_max_llm_calls=1, child_max_output_chars=200),
    )

    assert executor.tool(prompt="budget slice") == "done"

    # Exact option parity for the child native RLM.
    assert len(captured_options) == 1
    options = captured_options[0]
    assert (options.max_iters, options.max_llm_calls, options.max_output_chars) == (3, 1, 200)

    # One reservation, one Sandbox: no replacement allocation.
    assert recorder.call_indexes == [1]
    assert executor.summary().call_count == 1
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


def test_val_rec_010_child_sub_lm_budget_is_terminal_without_unreserved_retry() -> None:
    """VAL-REC-010: exhausting the child sub-LM call budget is terminal for
    that budget: the over-limit call never reaches the Sub LM, triggers no
    unreserved retry, and allocates no replacement Sandbox."""
    recorder = ChildLeaseRecorder()
    root = _lm(
        [
            {"reasoning": "use the budget", "code": "first = llm_query('q1')"},
            {"reasoning": "exceed the budget", "code": "second = llm_query('q2')"},
            {"reasoning": "submit", "code": "SUBMIT(answer='budget-terminal')"},
        ]
    )
    sub = _lm([{"answer": "sub-one"}, {"answer": "sub-two"}])
    executor = _executor(
        root,
        sub,
        recorder,
        options=RecursiveRLMOptions(child_max_iters=4, child_max_llm_calls=1, child_max_output_chars=200),
    )

    assert executor.tool(prompt="budget slice") == "budget-terminal"

    snapshot = executor.summary().delegation_metrics
    # Exactly one sub-LM call at depth 1: the second attempt was rejected by
    # DSPy's native budget before any LM invocation (no unreserved retry).
    sub_calls = {depth: count for role, depth, count in snapshot.lm_call_counts if role == "sub"}
    assert sub_calls == {1: 1}
    # One reservation, one Sandbox: no replacement allocation on exhaustion.
    assert recorder.call_indexes == [1]
    assert executor.summary().call_count == 1
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


def test_val_rec_011_all_or_nothing_batch_exposes_no_partial_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-011: when one sibling fails, the batch surfaces the typed
    all-or-nothing failure with the primary cause, exposes no result list and
    no successful sibling answer through events, and keeps every started
    sibling owned through close."""
    import fleet_rlm.rlm.recursion as recursive_batch
    import fleet_rlm.rlm.recursion as recursive_calls

    recorder = ChildLeaseRecorder()
    events: list[object] = []

    class MixedChild:
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            if prompt == "fail":
                raise ValueError("primary cause")
            return dspy.Prediction(answer="successful-sibling-answer", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: MixedChild())
    # Deterministically start both siblings before settlement: wait for every
    # future instead of returning on the first exception.
    real_wait = recursive_batch.wait

    def wait_for_all(futures, *, timeout=None, **_kwargs):
        return real_wait(futures, timeout=timeout)

    monkeypatch.setattr(recursive_batch, "wait", wait_for_all)
    executor = _executor(
        _lm([{"answer": "unused"}]),
        _lm([{"answer": "unused"}]),
        recorder,
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2),
        observer=events.append,
    )

    with pytest.raises(RecursiveBatchError) as raised:
        executor.batched_tool(prompts=["fail", "ok"])
    assert isinstance(raised.value.__cause__, ValueError)

    # No partial answer list and no successful batch completion evidence.
    from fleet_rlm.rlm.events import ToolCompleted

    batch_completions = [event for event in events if isinstance(event, ToolCompleted)]
    assert all(
        getattr(event, "output", None) is None or "answer_count" not in dict(event.output)
        for event in batch_completions
    )
    assert "successful-sibling-answer" not in repr(events)

    # Both started siblings remained owned: each closed exactly once before
    # the ownership boundary released.
    executor.wait_owned()
    executor.raise_if_cleanup_failed()
    assert sorted(recorder.call_indexes) == [1, 2]
    assert sorted(recorder.close_calls.values()) == [1, 1]
    assert all(lease.interpreter._shutdown for lease in recorder.leases)
