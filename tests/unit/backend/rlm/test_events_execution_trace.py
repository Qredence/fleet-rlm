"""Private RLM execution-trace and observation-session contracts.

* ``test_events_execution_trace.py``: Private RLM execution-trace contracts.
* ``test_events_observation.py``: Private RLM observation-session contracts.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.rlm.events import ObservationSession, RLMOutput, RunStarted, Status, record_phase_failure
from fleet_rlm.rlm.result import observed_usage, validate_rlm_usage

# --- from test_events_execution_trace.py ------------------------------


def test_record_phase_failure_preserves_callback_reasoning_flag_on_empty_parse() -> None:
    import dspy
    from dspy.utils.exceptions import AdapterParseError

    class _Sig(dspy.Signature):
        reasoning: str = dspy.OutputField()
        code: str = dspy.OutputField()

    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    error = AdapterParseError(
        adapter_name="JSONAdapter",
        signature=_Sig,
        lm_response="",
        message="The LM returned an empty or null response.",
    )

    record_phase_failure(
        phase,
        0.0,
        None,
        None,
        error,
        last_lm_call={"call_index": 14, "has_reasoning_content": True},
    )

    last_call = outputs[-1]["last_lm_call"]
    assert last_call["parse_failure_kind"] == "empty"
    assert last_call["has_reasoning_content"] is True


def test_phase_trace_records_wrap_up_diagnostics_without_changing_trajectory() -> None:
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    diagnostics = {
        "wrap_up_entered": True,
        "wrap_up_attempts": 2,
        "wrap_up_rejection_reason": "exploration_or_additional_code",
    }

    record_phase_failure(phase, 0.0, None, None, TimeoutError("deadline"), wrap_up=diagnostics)

    assert outputs[-1]["wrap_up_entered"] is True
    assert outputs[-1]["wrap_up_attempts"] == 2
    assert outputs[-1]["wrap_up_rejection_reason"] == "exploration_or_additional_code"


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


def test_observed_usage_rejects_non_mapping_delegation() -> None:
    prediction = SimpleNamespace(trajectory=[], get_lm_usage=lambda: {})

    with pytest.raises(ValueError):
        observed_usage(prediction, duration_ms=1, delegation="none")  # type: ignore[arg-type]


def test_observed_usage_rejects_unknown_delegation_keys() -> None:
    prediction = SimpleNamespace(trajectory=[], get_lm_usage=lambda: {})

    with pytest.raises(ValueError, match="unsupported keys"):
        observed_usage(prediction, duration_ms=1, delegation={"delegation_metric": {}})


def test_phase_trace_records_parse_repair_diagnostics() -> None:
    """Corrective re-asks must reach the Turn span as bounded adapter diagnostics."""
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)

    record_phase_failure(
        phase,
        0.0,
        None,
        None,
        TimeoutError("deadline"),
        repair={"parse_repairs_used": 2},
    )

    assert outputs[-1]["parse_repairs_used"] == 2


def test_phase_trace_omits_parse_repair_diagnostics_when_absent() -> None:
    """A Turn without an adapter must not gain a phantom repair field."""
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)

    record_phase_failure(phase, 0.0, None, None, TimeoutError("deadline"))

    assert "parse_repairs_used" not in outputs[-1]


# --- from test_events_observation.py ----------------------------------
@pytest.mark.asyncio
async def test_observation_session_separates_stream_envelopes_from_execution_details() -> None:
    session = ObservationSession(uuid4(), uuid4())

    started = session.record_event(RunStarted(delivery="live"))
    status = session.record_event(Status("execution", "running"))
    detail = session.record(RLMOutput("answer", 1))

    assert [started.sequence, status.sequence, detail.sequence] == [1, 2, 3]
    assert session.details == [RLMOutput("answer", 1)]
