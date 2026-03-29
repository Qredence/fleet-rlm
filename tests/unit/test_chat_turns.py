from __future__ import annotations

from types import SimpleNamespace

import dspy

from fleet_rlm.runtime.agent.chat_turns import (
    TurnDelegationState,
    TurnMetricsSnapshot,
    process_prediction_to_turn_result,
)


def test_turn_delegation_state_reset_and_payload() -> None:
    state = TurnDelegationState(
        effective_max_iters=3,
        delegate_calls_turn=2,
        delegate_fallback_count_turn=1,
        delegate_result_truncated_count_turn=1,
    )

    effective = state.reset(effective_max_iters=9)

    assert effective == 9
    assert state.as_payload() == {
        "effective_max_iters": 9,
        "delegate_calls_turn": 0,
        "delegate_fallback_count_turn": 0,
        "delegate_result_truncated_count_turn": 0,
    }


def test_turn_delegation_state_claims_slots_and_tracks_counters() -> None:
    state = TurnDelegationState(effective_max_iters=5)

    assert state.claim_slot(max_calls_per_turn=2) == (True, 2)
    assert state.claim_slot(max_calls_per_turn=2) == (True, 2)
    assert state.claim_slot(max_calls_per_turn=2) == (False, 2)

    state.record_fallback()
    state.record_truncation()

    assert state.as_payload() == {
        "effective_max_iters": 5,
        "delegate_calls_turn": 2,
        "delegate_fallback_count_turn": 1,
        "delegate_result_truncated_count_turn": 1,
    }


def test_process_prediction_to_turn_result_updates_history_and_payload() -> None:
    agent = SimpleNamespace(
        history=dspy.History(messages=[]),
        history_max_turns=None,
        get_core_memory_snapshot=lambda: {"persona": "test"},
        interpreter=SimpleNamespace(current_runtime_metadata=lambda: {}),
    )
    prediction = dspy.Prediction(
        assistant_response="hello",
        trajectory={"tool_name_0": "finish"},
    )

    result = process_prediction_to_turn_result(
        agent,
        prediction=prediction,
        message="say hi",
        include_core_memory_snapshot=True,
        turn_metrics=TurnMetricsSnapshot(
            effective_max_iters=4,
            delegate_calls_turn=1,
            delegate_fallback_count_turn=0,
            delegate_result_truncated_count_turn=0,
        ),
    )

    assert result["assistant_response"] == "hello"
    assert result["history_turns"] == 1
    assert result["trajectory"] == {"tool_name_0": "finish"}
    assert result["core_memory_snapshot"] == {"persona": "test"}


def test_process_prediction_to_turn_result_finalizes_and_validates() -> None:
    agent = SimpleNamespace(
        history=dspy.History(messages=[]),
        history_max_turns=None,
        _last_tool_error_count=0,
        _count_tool_errors=lambda trajectory: 3 if trajectory else 0,
        _validate_assistant_response=lambda **kwargs: (
            kwargs["assistant_response"].upper(),
            ["validated"],
        ),
        interpreter=SimpleNamespace(current_runtime_metadata=lambda: {}),
    )
    prediction = dspy.Prediction(
        assistant_response="needs validation",
        trajectory={"tool_name_0": "finish"},
    )

    result = process_prediction_to_turn_result(
        agent,
        prediction=prediction,
        message="sanitize",
        include_core_memory_snapshot=False,
        turn_metrics=TurnMetricsSnapshot(
            effective_max_iters=6,
            delegate_calls_turn=2,
            delegate_fallback_count_turn=1,
            delegate_result_truncated_count_turn=0,
        ),
        finalize_and_validate=True,
    )

    assert result["assistant_response"] == "NEEDS VALIDATION"
    assert result["guardrail_warnings"] == ["validated"]
    assert agent._last_tool_error_count == 3


def test_process_prediction_to_turn_result_includes_runtime_degradation_metadata() -> (
    None
):
    agent = SimpleNamespace(
        history=dspy.History(messages=[]),
        history_max_turns=None,
        get_core_memory_snapshot=lambda: {"persona": "test"},
        interpreter=SimpleNamespace(
            current_runtime_metadata=lambda: {
                "runtime_degraded": True,
                "runtime_failure_category": "sandbox_create_clone_error",
                "runtime_failure_phase": "sandbox_create",
                "runtime_fallback_used": True,
            }
        ),
    )
    prediction = dspy.Prediction(
        assistant_response="hello",
        trajectory={"tool_name_0": "finish"},
    )

    result = process_prediction_to_turn_result(
        agent,
        prediction=prediction,
        message="say hi",
        include_core_memory_snapshot=False,
        turn_metrics=TurnMetricsSnapshot(
            effective_max_iters=4,
            delegate_calls_turn=1,
            delegate_fallback_count_turn=0,
            delegate_result_truncated_count_turn=0,
        ),
    )

    assert result["runtime_degraded"] is True
    assert result["runtime_failure_category"] == "sandbox_create_clone_error"
    assert result["runtime_failure_phase"] == "sandbox_create"
    assert result["runtime_fallback_used"] is True
