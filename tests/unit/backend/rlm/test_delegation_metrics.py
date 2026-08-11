from __future__ import annotations

from types import SimpleNamespace

from fleet_rlm.rlm.delegation_metrics import DelegationMetrics, normalize_lm_token_usage
from fleet_rlm.rlm.dspy_contract import _latest_lm_telemetry, _RLMTraceCallback


def test_dspy_callback_records_role_and_recursive_depth_without_content() -> None:
    metrics = DelegationMetrics()
    root = SimpleNamespace(model="root-model", history=[])
    sub = SimpleNamespace(model="sub-model", history=[])
    callback = _RLMTraceCallback(root_lm=root, sub_lm=sub, recursive_depth=1, metrics=metrics)

    callback.on_lm_start("root-call", root, {"prompt": "private prompt"})
    callback.on_lm_end("root-call", {"content": "private answer"})
    callback.on_lm_start("sub-call", sub, {"prompt": "private sub-prompt"})
    callback.on_lm_end("sub-call", {"content": "private sub-answer"})

    snapshot = metrics.snapshot()
    assert snapshot.child_root_lm_calls_depth_1 == 1
    assert snapshot.child_sub_lm_calls_depth_1 == 1
    assert snapshot.root_lm_calls_depth_0 == 0
    assert snapshot.sub_lm_calls_depth_0 == 0
    assert "private prompt" not in repr(snapshot)
    assert "private answer" not in repr(snapshot)


def test_metrics_track_recursive_batch_lifecycle_and_peak_width() -> None:
    metrics = DelegationMetrics()
    metrics.record_recursive_batch()
    metrics.record_recursive_call()
    metrics.record_recursive_call()
    metrics.child_started()
    metrics.child_started()
    metrics.child_completed()
    metrics.child_completed()

    snapshot = metrics.snapshot()
    assert snapshot.recursive_batch_calls == 1
    assert snapshot.recursive_child_calls == 2
    assert snapshot.recursive_children_started == 2
    assert snapshot.recursive_children_completed == 2
    assert snapshot.peak_child_concurrency == 2
    assert snapshot.as_dict()["peak_child_concurrency"] == 2


def test_token_usage_normalizer_accepts_both_supported_alias_families() -> None:
    assert normalize_lm_token_usage({"input_tokens": 11, "output_tokens": 7}) == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }
    assert normalize_lm_token_usage({"prompt_tokens": 5, "completion_tokens": 3}) == {
        "input_tokens": 5,
        "output_tokens": 3,
        "total_tokens": 8,
    }
    assert normalize_lm_token_usage({"input_tokens": 11, "output_tokens": 7, "total_tokens": 21})["total_tokens"] == 21


def test_metrics_count_input_output_aliases_when_total_is_absent() -> None:
    metrics = DelegationMetrics()
    metrics.record_lm_call("sub", 1, usage={"input_tokens": 13, "output_tokens": 4})

    assert metrics.snapshot().lm_token_totals == (("sub", 1, 17),)


def test_lm_telemetry_matches_callback_response_in_concurrent_history() -> None:
    first_response = object()
    second_response = object()
    lm = SimpleNamespace(
        history=[
            {"response": first_response, "usage": {"input_tokens": 3, "output_tokens": 2}},
            {"response": second_response, "usage": {"input_tokens": 17, "output_tokens": 11}},
        ]
    )

    usage, _provider = _latest_lm_telemetry(lm, 0, first_response)

    assert usage == {"input_tokens": 3, "output_tokens": 2}
