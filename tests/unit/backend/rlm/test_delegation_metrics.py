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


def test_metrics_record_input_output_total_per_lm_call() -> None:
    metrics = DelegationMetrics()
    metrics.record_lm_call("sub", 1, usage={"input_tokens": 13, "output_tokens": 4})

    # lm_token_totals entries are (role, depth, input, output, total) so
    # per-iteration prompt growth is legible without losing the total.
    assert metrics.snapshot().lm_token_totals == (("sub", 1, 13, 4, 17),)


def test_metrics_token_totals_partial_usage_is_not_collapsed_to_zero() -> None:
    # A provider that reports only input_tokens must not read as 0 tokens.
    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0, usage={"input_tokens": 50})
    metrics.record_lm_call("root", 0, usage={"prompt_tokens": 30, "completion_tokens": 20})

    assert metrics.snapshot().lm_token_totals == (("root", 0, 80, 20, 100),)
    assert metrics.snapshot().as_dict()["lm_token_totals"] == [
        {
            "role": "root",
            "recursive_depth": 0,
            "input_tokens": 80,
            "output_tokens": 20,
            "tokens": 100,
        }
    ]


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


def test_metrics_unobserved_usage_emits_no_zero_token_totals_and_status_unavailable() -> None:
    # A provider that never reports usage must not manufacture an all-zero
    # lm_token_totals entry; call counts/latency still record.
    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0)
    metrics.record_lm_call("root", 0, usage=None)
    metrics.record_lm_call("root", 0, usage={})

    snapshot = metrics.snapshot()
    assert snapshot.root_lm_calls_depth_0 == 3
    assert snapshot.lm_token_totals == ()
    assert snapshot.token_usage_status == "unavailable"
    assert snapshot.as_dict()["lm_token_totals"] == []
    assert snapshot.as_dict()["token_usage_status"] == "unavailable"


def test_metrics_real_zero_usage_is_observed_not_unavailable() -> None:
    # A provider-reported all-zero usage mapping is a measurement, not
    # "unavailable".
    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0, usage={"input_tokens": 0, "output_tokens": 0})

    snapshot = metrics.snapshot()
    assert snapshot.lm_token_totals == (("root", 0, 0, 0, 0),)
    assert snapshot.token_usage_status == "observed"
    assert snapshot.as_dict()["token_usage_status"] == "observed"


def test_metrics_mixed_observed_and_unobserved_calls_only_total_observed() -> None:
    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0, usage={"input_tokens": 50})
    metrics.record_lm_call("root", 0)  # no usage: call counts, tokens must not
    metrics.record_lm_call("sub", 0)  # entirely unobserved role/depth key

    snapshot = metrics.snapshot()
    assert snapshot.root_lm_calls_depth_0 == 2
    assert snapshot.sub_lm_calls_depth_0 == 1
    assert snapshot.lm_token_totals == (("root", 0, 50, 0, 50),)
    assert snapshot.token_usage_status == "observed"


def test_metrics_token_aggregation_is_thread_safe_across_concurrent_recorders() -> None:
    from concurrent.futures import ThreadPoolExecutor

    metrics = DelegationMetrics()
    observed_calls_per_thread = 25

    def record(thread_index: int) -> None:
        for _ in range(observed_calls_per_thread):
            metrics.record_lm_call("root", 0, usage={"prompt_tokens": 10, "completion_tokens": 5})
            metrics.record_lm_call("sub", 0)  # unobserved: counts only
            metrics.record_lm_call("root", 1, usage={"input_tokens": thread_index})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(8)))

    snapshot = metrics.snapshot()
    assert snapshot.root_lm_calls_depth_0 == 8 * observed_calls_per_thread
    assert snapshot.sub_lm_calls_depth_0 == 8 * observed_calls_per_thread
    assert snapshot.child_root_lm_calls_depth_1 == 8 * observed_calls_per_thread
    totals = {(role, depth): (i, o, t) for role, depth, i, o, t in snapshot.lm_token_totals}
    root_expected = 8 * observed_calls_per_thread
    assert totals[("root", 0)] == (root_expected * 10, root_expected * 5, root_expected * 15)
    depth1_expected = observed_calls_per_thread * sum(range(8))
    assert totals[("root", 1)] == (depth1_expected, 0, depth1_expected)
    assert ("sub", 0) not in totals
    assert snapshot.token_usage_status == "observed"
