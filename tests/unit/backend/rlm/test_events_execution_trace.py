"""Private RLM execution-trace contracts."""

from __future__ import annotations

from types import SimpleNamespace

from fleet_rlm.rlm.events import record_phase_failure


def test_record_phase_failure_preserves_sanitized_last_lm_call_structure() -> None:
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)

    record_phase_failure(
        phase,
        0.0,
        None,
        None,
        ValueError("provider failure"),
        last_lm_call={"call_index": 4, "response_keys": ()},
    )

    assert outputs[-1]["failure_category"] == "unknown"
    assert outputs[-1]["last_lm_call"] == {"call_index": 4, "response_keys": ()}


def test_phase_trace_records_wrap_up_diagnostics_without_changing_trajectory() -> None:
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    diagnostics = {
        "wrap_up_entered": True,
        "wrap_up_attempts": 2,
        "wrap_up_rejection_reason": "exploration_or_additional_code",
        "wrap_up_remaining_ms": 912,
    }

    record_phase_failure(phase, 0.0, None, None, TimeoutError("deadline"), wrap_up=diagnostics)

    assert outputs[-1]["wrap_up_entered"] is True
    assert outputs[-1]["wrap_up_attempts"] == 2
    assert outputs[-1]["wrap_up_rejection_reason"] == "exploration_or_additional_code"
    assert outputs[-1]["wrap_up_remaining_ms"] == 912


def test_record_phase_failure_marks_token_usage_unavailable_without_observed_usage() -> None:
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)

    record_phase_failure(phase, 0.0, None, None, ValueError("boom"))

    assert outputs[-1]["token_usage_status"] == "unavailable"
    assert outputs[-1]["delegation_metrics"]["lm_token_totals"] == []


def test_record_phase_success_marks_token_usage_observed_from_lm_metrics() -> None:
    from fleet_rlm.rlm.events import record_phase_success
    from fleet_rlm.rlm.recursion import DelegationMetrics

    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0, usage={"input_tokens": 40, "output_tokens": 7})
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    prediction = SimpleNamespace(trajectory=[{"reasoning": "r", "code": "c", "output": "o"}], get_lm_usage=lambda: {})

    record_phase_success(phase, prediction, 0.0, None, metrics)

    final = outputs[-1]
    assert final["request_status"] == "completed"
    assert final["token_usage_status"] == "observed"
    assert final["delegation_metrics"]["lm_token_totals"] == [
        {"role": "root", "recursive_depth": 0, "input_tokens": 40, "output_tokens": 7, "tokens": 47}
    ]
    assert final["delegation_metrics"]["token_usage_status"] == "observed"


def test_record_phase_success_marks_token_usage_unavailable_without_any_usage_signal() -> None:
    from fleet_rlm.rlm.events import record_phase_success
    from fleet_rlm.rlm.recursion import DelegationMetrics

    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0)  # completed call, but provider reported no usage
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    prediction = SimpleNamespace(trajectory=[], get_lm_usage=lambda: {})

    record_phase_success(phase, prediction, 0.0, None, metrics)

    final = outputs[-1]
    assert final["observed_lm_usage"] == {}
    assert final["token_usage_status"] == "unavailable"
    assert final["delegation_metrics"]["lm_token_totals"] == []
    assert final["delegation_metrics"]["token_usage_status"] == "unavailable"


def test_record_phase_success_cost_only_prediction_usage_reports_unavailable() -> None:
    from fleet_rlm.rlm.events import record_phase_success
    from fleet_rlm.rlm.recursion import DelegationMetrics

    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    prediction = SimpleNamespace(
        trajectory=[],
        get_lm_usage=lambda: {"gpt-test": {"cost": 0.001, "cached": True}},
    )

    record_phase_success(phase, prediction, 0.0, None, DelegationMetrics())

    final = outputs[-1]
    assert final["observed_lm_usage"] == {"gpt-test": {"cost": 0.001, "cached": True}}
    assert final["token_usage_status"] == "unavailable"


def test_record_phase_success_marks_token_usage_observed_from_prediction_usage() -> None:
    from fleet_rlm.rlm.events import record_phase_success
    from fleet_rlm.rlm.recursion import DelegationMetrics

    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    prediction = SimpleNamespace(
        trajectory=[],
        get_lm_usage=lambda: {"gpt-test": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}},
    )

    record_phase_success(phase, prediction, 0.0, None, DelegationMetrics())

    final = outputs[-1]
    assert final["observed_lm_usage"] == {"gpt-test": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}
    assert final["token_usage_status"] == "observed"
