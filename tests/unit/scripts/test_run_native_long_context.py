from __future__ import annotations

import pytest

from scripts.benchmarks.run_native_long_context import (
    NativeLongContextSignature,
    _evaluate_cases,
    _run_case,
    _source,
    _with_native_interpreter,
)

MARKERS = ("first-marker", "middle-marker", "last-marker")


class _FakeInterpreter:
    def __init__(self, *, shutdown_error: BaseException | None = None) -> None:
        self.shutdown_error = shutdown_error
        self.shutdown_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        if self.shutdown_error is not None:
            raise self.shutdown_error


def test_source_rejects_sizes_that_cannot_hold_planted_markers() -> None:
    with pytest.raises(ValueError, match="too small"):
        _source(1_024, MARKERS)

    with pytest.raises(ValueError, match="overlap"):
        _source(64 * 1024, MARKERS)


def test_source_preserves_marker_layout_for_valid_size() -> None:
    source = _source(128 * 1024, MARKERS)

    assert len(source.encode("utf-8")) == 128 * 1024
    assert source.index(MARKERS[0]) == 64 * 1024 - len(MARKERS[0])
    assert source.index(MARKERS[1]) == (128 * 1024) // 2
    assert source.index(MARKERS[2]) == 128 * 1024 - len(MARKERS[2]) - 1


def test_signature_declares_typed_program_interface() -> None:
    assert list(NativeLongContextSignature.input_fields) == ["request"]
    assert list(NativeLongContextSignature.output_fields) == ["answer"]
    assert NativeLongContextSignature.output_fields["answer"].annotation is str


@pytest.mark.asyncio
async def test_native_case_records_stock_dspy_tools_usage_and_typed_submit() -> None:
    case = await _run_case(128 * 1024, trace_enabled=False)

    assert case["correct"] is True
    assert case["rlm_type"] == "dspy.predict.rlm.RLM"
    assert case["registered_tool_names"] == ["fetch_url"]
    assert case["native_tool_call_counts"] == {"llm_query": 0, "llm_query_batched": 1}
    assert case["sub_lm_call_count"] == 3
    assert case["termination"] == {"first_turn": "typed_submit", "follow_up": "typed_submit"}
    assert case["typed_completion"] is True
    assert case["usage_tracking_attached"] is True
    assert case["prediction_lm_usage"] == {"first_turn": {}, "follow_up": {}}

    evaluation = _evaluate_cases([case])
    assert evaluation["engine"] == "dspy.Evaluate"
    assert evaluation["score"] == 100.0
    assert evaluation["examples"][0]["sub_scores"] == {
        "answer_correctness": 1.0,
        "evidence_presence": 1.0,
        "typed_completion": 1.0,
    }


@pytest.mark.asyncio
async def test_native_interpreter_is_shutdown_when_rlm_construction_fails() -> None:
    interpreter = _FakeInterpreter()

    async def construct_and_fail(_interpreter: _FakeInterpreter) -> None:
        raise RuntimeError("RLM construction failed")

    with pytest.raises(RuntimeError, match="RLM construction failed"):
        await _with_native_interpreter(lambda: interpreter, construct_and_fail)

    assert interpreter.shutdown_calls == 1


@pytest.mark.asyncio
async def test_native_interpreter_shutdown_failure_does_not_mask_primary_error() -> None:
    interpreter = _FakeInterpreter(shutdown_error=OSError("shutdown failed"))

    async def execute_and_fail(_interpreter: _FakeInterpreter) -> None:
        raise ValueError("execution failed")

    with pytest.raises(ValueError, match="execution failed") as caught:
        await _with_native_interpreter(lambda: interpreter, execute_and_fail)

    assert interpreter.shutdown_calls == 1
    assert any("shutdown failed" in note for note in caught.value.__notes__)
