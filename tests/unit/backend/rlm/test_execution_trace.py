"""Private RLM execution-trace contracts."""

from __future__ import annotations

from types import SimpleNamespace

from fleet_rlm.rlm.execution_trace import record_phase_failure


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
