from __future__ import annotations

from fleet_rlm.runtime.agent import runtime_helpers as rh


def test_prediction_response_text_prefers_response_field() -> None:
    class _Result:
        response = "hello"

    assert rh.prediction_response_text(_Result()) == "hello"


def test_runtime_degradation_payload_defaults_when_degraded() -> None:
    payload = rh.runtime_degradation_payload({"runtime_degraded": True})
    assert payload["runtime_degraded"] is True
    assert payload["runtime_failure_category"] == "rlm_fallback"


def test_recursive_child_review_payload_for_delegate_tool() -> None:
    payload = rh.recursive_child_review_payload(
        "delegate_to_rlm",
        {"status": "needs_human_review", "reason": "child_timeout"},
    )
    assert payload is not None
    assert payload["required"] is True
    assert "child timeout" in payload["reason"]
