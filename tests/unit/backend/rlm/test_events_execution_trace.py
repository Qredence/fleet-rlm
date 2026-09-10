"""Private RLM execution-trace contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.rlm.events import record_phase_failure
from fleet_rlm.rlm.result import observed_usage, validate_rlm_usage


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


def test_record_phase_success_backfills_usage_from_lm_histories() -> None:
    from fleet_rlm.rlm.events import record_phase_success
    from fleet_rlm.rlm.recursion import DelegationMetrics

    metrics = DelegationMetrics()
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    prediction = SimpleNamespace(
        trajectory=[{"reasoning": "r", "code": "c", "output": "o"}],
        get_lm_usage=lambda: {},
    )
    root = SimpleNamespace(
        model="test-root",
        history=[{"usage": {"prompt_tokens": 10, "completion_tokens": 4}}],
    )

    record_phase_success(phase, prediction, 0.0, None, metrics, lms=(root, None))

    final = outputs[-1]
    assert final["request_status"] == "completed"
    assert final["token_usage_status"] == "observed"
    assert final["observed_lm_usage"] == {"test-root": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}}


def test_record_phase_success_prefers_tracker_usage_over_histories() -> None:
    from fleet_rlm.rlm.events import record_phase_success
    from fleet_rlm.rlm.recursion import DelegationMetrics

    metrics = DelegationMetrics()
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    prediction = SimpleNamespace(
        trajectory=[{"reasoning": "r", "code": "c", "output": "o"}],
        get_lm_usage=lambda: {"test-root": {"input_tokens": 100, "output_tokens": 50}},
    )
    root = SimpleNamespace(
        model="test-root",
        history=[{"usage": {"prompt_tokens": 10, "completion_tokens": 4}}],
    )

    record_phase_success(phase, prediction, 0.0, None, metrics, lms=(root,))

    final = outputs[-1]
    assert final["token_usage_status"] == "observed"
    assert final["observed_lm_usage"] == {"test-root": {"input_tokens": 100, "output_tokens": 50}}


def test_record_phase_success_counts_shared_histories_once() -> None:
    from fleet_rlm.rlm.events import record_phase_success
    from fleet_rlm.rlm.recursion import DelegationMetrics

    metrics = DelegationMetrics()
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    prediction = SimpleNamespace(trajectory=[], get_lm_usage=lambda: {})
    shared = [{"usage": {"prompt_tokens": 10, "completion_tokens": 4}}]
    first = SimpleNamespace(model="test-root", history=shared)
    second = SimpleNamespace(model="test-proxy", history=shared)

    record_phase_success(phase, prediction, 0.0, None, metrics, lms=(first, second))

    final = outputs[-1]
    assert final["observed_lm_usage"] == {"test-root": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}}


def test_observed_usage_merges_delegation_keys_through_validate_round_trip() -> None:
    from fleet_rlm.rlm.recursion import DelegationMetrics

    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0)
    metrics.record_delegated_input_bytes(512)
    prediction = SimpleNamespace(trajectory=[{"reasoning": "r", "code": "c", "output": "o"}], get_lm_usage=lambda: {})

    usage = observed_usage(
        prediction,
        duration_ms=120,
        delegation={"recursive_call_count": 1, "delegation_metrics": metrics.snapshot().as_dict()},
    )

    assert usage["recursive_call_count"] == 1
    delegation_metrics = usage["delegation_metrics"]
    assert delegation_metrics["delegated_input_bytes"] == 512
    assert delegation_metrics["lm_call_counts"] == [{"role": "root", "recursive_depth": 0, "count": 1}]
    # Full snapshot extras (latency, token status) survive; only the two
    # required keys are normalized.
    assert delegation_metrics["token_usage_status"] == "unavailable"
    assert validate_rlm_usage(dict(usage)) == usage


def test_observed_usage_without_delegation_keeps_historical_shape() -> None:
    prediction = SimpleNamespace(trajectory=[], get_lm_usage=lambda: {})

    usage = observed_usage(prediction, duration_ms=50)

    assert set(usage) == {"iterations", "observed_lm_usage", "duration_ms"}
    assert validate_rlm_usage(dict(usage)) == usage


def test_observed_usage_keeps_tracker_tokens_alongside_delegation() -> None:
    prediction = SimpleNamespace(
        trajectory=[],
        get_lm_usage=lambda: {"gpt-test": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}},
    )

    usage = observed_usage(
        prediction,
        duration_ms=10,
        delegation={
            "recursive_call_count": 0,
            "delegation_metrics": {"lm_call_counts": [], "delegated_input_bytes": 0},
        },
    )

    assert usage["observed_lm_usage"] == {"gpt-test": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}
    assert usage["recursive_call_count"] == 0


def test_validate_rlm_usage_rejects_invalid_delegation_values() -> None:
    base: dict[str, object] = {"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}

    with pytest.raises(ValueError):
        validate_rlm_usage({**base, "recursive_call_count": -1})
    with pytest.raises(ValueError):
        validate_rlm_usage({**base, "recursive_call_count": "1"})
    with pytest.raises(ValueError):
        validate_rlm_usage({**base, "recursive_call_count": True})
    with pytest.raises(ValueError):
        validate_rlm_usage({**base, "delegation_metrics": {"lm_call_counts": [], "delegated_input_bytes": -5}})
    with pytest.raises(ValueError):
        validate_rlm_usage({**base, "delegation_metrics": {"lm_call_counts": [], "delegated_input_bytes": "0"}})
    with pytest.raises(ValueError):
        validate_rlm_usage(
            {
                **base,
                "delegation_metrics": {
                    "lm_call_counts": [{"role": "root", "recursive_depth": 0, "count": -1}],
                    "delegated_input_bytes": 0,
                },
            }
        )
    with pytest.raises(ValueError):
        validate_rlm_usage(
            {
                **base,
                "delegation_metrics": {
                    "lm_call_counts": [{"role": "", "recursive_depth": 0, "count": 0}],
                    "delegated_input_bytes": 0,
                },
            }
        )
    with pytest.raises(ValueError):
        validate_rlm_usage({**base, "delegation_metrics": {"delegated_input_bytes": 0}})
    with pytest.raises(ValueError):
        validate_rlm_usage({**base, "delegation_metrics": "none"})
    with pytest.raises(ValueError):
        validate_rlm_usage({**base, "unknown_key": 1})


def test_observed_usage_rejects_non_mapping_delegation() -> None:
    prediction = SimpleNamespace(trajectory=[], get_lm_usage=lambda: {})

    with pytest.raises(ValueError):
        observed_usage(prediction, duration_ms=1, delegation="none")  # type: ignore[arg-type]


def test_validate_rlm_usage_rejects_malformed_call_count_entries() -> None:
    base: dict[str, object] = {"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}

    def check(entries: object) -> None:
        with pytest.raises(ValueError):
            validate_rlm_usage({**base, "delegation_metrics": {"lm_call_counts": entries, "delegated_input_bytes": 0}})

    check([{"recursive_depth": 0, "count": 1}])
    check([{"role": "root", "count": 1}])
    check([{"role": "root", "recursive_depth": 0}])
    check(["not-a-mapping"])
    check([{"role": "root", "recursive_depth": "0", "count": 1}])
    check({"role": "root"})


def test_validate_rlm_usage_drops_extra_call_count_keys() -> None:
    base: dict[str, object] = {"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}

    usage = validate_rlm_usage(
        {
            **base,
            "delegation_metrics": {
                "lm_call_counts": [{"role": "root", "recursive_depth": 0, "count": 2, "future": 1}],
                "delegated_input_bytes": 0,
            },
        }
    )

    assert usage["delegation_metrics"]["lm_call_counts"] == [{"role": "root", "recursive_depth": 0, "count": 2}]


def test_observed_usage_rejects_unknown_delegation_keys() -> None:
    prediction = SimpleNamespace(trajectory=[], get_lm_usage=lambda: {})

    with pytest.raises(ValueError, match="unsupported keys"):
        observed_usage(prediction, duration_ms=1, delegation={"delegation_metric": {}})


def test_tokens_plus_delegation_payload_validates() -> None:
    prediction = SimpleNamespace(
        trajectory=[],
        get_lm_usage=lambda: {"gpt-test": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}},
    )

    usage = observed_usage(
        prediction,
        duration_ms=10,
        delegation={
            "recursive_call_count": 0,
            "delegation_metrics": {"lm_call_counts": [], "delegated_input_bytes": 0},
        },
    )

    assert validate_rlm_usage(dict(usage)) == usage
