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


def test_get_streamable_react_program_detects_planner_extract() -> None:
    class _Program:
        planner = object()
        extract = object()

        async def async_planner_step(self, *_args, **_kwargs):
            return None

    assert rh.get_streamable_react_program(_Program()) is not None


def test_format_react_trajectory_uses_program_formatter() -> None:
    class _Program:
        def _format_trajectory(self, trajectory_raw: dict[str, str]) -> str:
            return f"formatted:{trajectory_raw['thought_0']}"

    assert rh.format_react_trajectory(_Program(), {"thought_0": "hi"}) == "formatted:hi"


def test_format_react_trajectory_falls_back_to_str_without_formatter() -> None:
    class _Program:
        pass

    trajectory = {"thought_0": "hi"}
    assert rh.format_react_trajectory(_Program(), trajectory) == str(trajectory)
