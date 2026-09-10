"""Credential-free checks for the live canary's host-side evidence boundary."""

import pytest

from fleet_rlm.rlm.recursion import RecursiveRLMExecutor
from tests.live.backend.test_daytona_recursive_batch import _ChildEvidence, _install_batch_answer_capture


@pytest.mark.parametrize("status", ["failed", "timed_out", "cancelled"])
def test_live_batch_capture_rejects_unsuccessful_child_status(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    outcomes = [{"status": "completed", "answer": "first"}, {"status": status, "answer": "second"}]
    monkeypatch.setattr(RecursiveRLMExecutor, "_call_capsules_batched", lambda *_args: outcomes)
    evidence = _ChildEvidence()
    _install_batch_answer_capture(monkeypatch, evidence)
    executor = object.__new__(RecursiveRLMExecutor)
    with pytest.raises(AssertionError):
        executor._call_capsules_batched([{"task": "first"}, {"task": "second"}])
    assert evidence.batch_answers is None


def test_live_batch_capture_retains_order_and_typed_results(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = [{"status": "completed", "answer": "first"}, {"status": "completed", "answer": "second"}]
    monkeypatch.setattr(RecursiveRLMExecutor, "_call_capsules_batched", lambda *_args: outcomes)
    evidence = _ChildEvidence()
    _install_batch_answer_capture(monkeypatch, evidence)
    executor = object.__new__(RecursiveRLMExecutor)
    result = executor._call_capsules_batched([{"task": "first"}, {"task": "second"}])
    assert result is outcomes
    assert evidence.batch_answers == ["first", "second"]
