"""Unit coverage for the live direct-RLM promotion gate helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_harness():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "validate_rlm_e2e_trace.py"
    spec = importlib.util.spec_from_file_location("validate_rlm_e2e_trace", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_extract_run_evidence_counts_tokens_and_marks_fallback() -> None:
    harness = _load_harness()

    evidence = harness._extract_run_evidence(
        backend="direct_rlm",
        run_index=2,
        duration_seconds=12.5,
        terminal_payload={
            "type": "event",
            "data": {
                "kind": "final",
                "payload": {"fallback": True, "usage": {"total_tokens": 37}},
            },
        },
        execution_events=[
            {"type": "execution_step", "payload": {"token_count": 5}},
            {"type": "execution_step", "payload": {"usage": {"input_tokens": 11, "output_tokens": 7}}},
        ],
    )

    assert evidence.token_count == 37
    assert evidence.fallback_detected is True
    assert evidence.terminal_error is None


def test_extract_token_count_rejects_zero_default_values_as_missing_evidence() -> None:
    harness = _load_harness()

    assert (
        harness._extract_token_count(
            {"usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}},
            {"summary": {"token_count": 0}},
        )
        is None
    )


@pytest.mark.parametrize("flag", ["runtime_degraded", "runtime_fallback_used"])
def test_extract_run_evidence_rejects_runtime_degradation_flags(flag: str) -> None:
    harness = _load_harness()

    evidence = harness._extract_run_evidence(
        backend="direct_rlm",
        run_index=1,
        duration_seconds=1.0,
        terminal_payload={"data": {"payload": {flag: True}}},
        execution_events=[],
    )

    assert evidence.fallback_detected is True


def test_artifact_readback_accepts_string_encoded_tool_result() -> None:
    harness = _load_harness()
    marker = "QRE-301-PROMOTION-ARTIFACT-READBACK"
    checksum = "expected-checksum"

    harness._assert_artifact_readback(
        [
            {
                "type": "execution_step",
                "payload": {
                    "event_kind": "tool_result",
                    "tool_name": "read_artifact",
                    "tool_output": (
                        '{"content":"'
                        + marker
                        + '","artifact_backed":true,"artifact":{"ref":{"checksum":"'
                        + checksum
                        + '"}}}'
                    ),
                },
            }
        ],
        marker=marker,
        checksum=checksum,
    )


def test_artifact_readback_accepts_canonical_execution_step_envelope() -> None:
    harness = _load_harness()
    marker = "QRE-301-PROMOTION-ARTIFACT-READBACK"
    checksum = "expected-checksum"

    harness._assert_artifact_readback(
        [
            {
                "type": "execution_step",
                "step": {
                    "id": "run-1:s2",
                    "type": "tool",
                    "label": "read_artifact",
                    "input": {"event_kind": "tool_result", "tool_name": "read_artifact"},
                    "output": {
                        "content": marker,
                        "artifact_backed": True,
                        "artifact": {"ref": {"checksum": checksum}},
                    },
                    "timestamp": 1.0,
                },
            }
        ],
        marker=marker,
        checksum=checksum,
    )


def test_validate_trace_debug_requires_matching_trace_spans_and_performance() -> None:
    harness = _load_harness()
    payload = {
        "trace_id": "trace-1",
        "span_count": 1,
        "spans": [{"span_id": "span-1", "name": "LM.__call__"}],
        "performance_summary": {
            "total_duration_ms": 125,
            "llm_duration_ms": 100,
            "repl_duration_ms": 0,
            "tool_duration_ms": 0,
            "total_tokens": 12,
        },
    }

    harness._validate_trace_debug_payload(payload, expected_trace_id="trace-1")

    with pytest.raises(RuntimeError, match="trace id"):
        harness._validate_trace_debug_payload(payload, expected_trace_id="trace-other")
    with pytest.raises(RuntimeError, match="spans"):
        harness._validate_trace_debug_payload(
            {**payload, "span_count": 0, "spans": []},
            expected_trace_id="trace-1",
        )
    with pytest.raises(RuntimeError, match="performance"):
        harness._validate_trace_debug_payload(
            {
                **payload,
                "performance_summary": {
                    "total_duration_ms": None,
                    "llm_duration_ms": 0,
                    "repl_duration_ms": 0,
                    "tool_duration_ms": 0,
                    "total_tokens": 0,
                },
            },
            expected_trace_id="trace-1",
        )


@pytest.mark.asyncio
async def test_promotion_matrix_applies_same_capability_workload_to_both_backends(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = _load_harness()
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(harness, "_validate_promotion_targets", lambda **_: None)
    monkeypatch.setattr(harness, "_validate_promotion_prerequisites", lambda: None)

    async def _upload(_: object, *, session_id: str, **__: object) -> tuple[str, str]:
        return ("a" * 32, f"attachment-{session_id}")

    async def _run_validation(args: object, **kwargs: object):
        calls.append({"prompt": args.prompt, **kwargs})
        return harness.ValidationResult(
            run_id=f"run-{len(calls)}",
            session_id=str(kwargs["session_id"]),
            workspace_id="workspace",
            user_id="user",
            chat_terminal_kind="final",
            execution_step_count=1,
            run_status="completed",
            run_step_count=1,
            artifact_count=1,
            mlflow_trace_id="trace",
            output_dir=tmp_path / f"run-{len(calls)}",
            duration_seconds=1.0,
            token_count=10,
        )

    async def _session_summary(_: object, **__: object) -> dict[str, int]:
        return {"history_turns": 3}

    monkeypatch.setattr(harness, "_upload_promotion_attachment", _upload)
    monkeypatch.setattr(harness, "_run_validation", _run_validation)
    monkeypatch.setattr(harness, "_fetch_session_summary", _session_summary)
    args = type(
        "Args",
        (),
        {
            "legacy_server_url": "http://legacy.test",
            "direct_server_url": "http://direct.test",
            "output_dir": str(tmp_path),
            "prompt": "base prompt",
            "max_promotion_regression_ratio": 0.25,
            "workspace_id": "workspace",
            "user_id": "user",
        },
    )()

    summary = await harness._run_promotion_gate(args)

    assert summary.passed is True
    assert len(calls) == 6
    assert all(call["selected_skill_ids"] == ["long-context"] for call in calls)
    assert all(call["attachment_refs"] == ["a" * 32] for call in calls)
    capability_fields = {
        (
            tuple(call["selected_skill_ids"]),
            bool(call["attachment_refs"]),
            call["artifact_marker"],
            call["artifact_checksum"],
            call["require_trace_debug"],
            call["prompt"],
        )
        for call in calls
    }
    assert len(capability_fields) == 3


def test_promotion_summary_uses_medians_and_rejects_material_regression() -> None:
    harness = _load_harness()
    legacy = [
        harness.PromotionRunEvidence("legacy_agent_runtime", 1, 10.0, 100, False, None),
        harness.PromotionRunEvidence("legacy_agent_runtime", 2, 12.0, 110, False, None),
        harness.PromotionRunEvidence("legacy_agent_runtime", 3, 14.0, 120, False, None),
    ]
    direct = [
        harness.PromotionRunEvidence("direct_rlm", 1, 20.0, 200, False, None),
        harness.PromotionRunEvidence("direct_rlm", 2, 24.0, 220, False, None),
        harness.PromotionRunEvidence("direct_rlm", 3, 28.0, 240, False, None),
    ]

    summary = harness._build_promotion_summary(legacy, direct, max_regression_ratio=0.25)

    assert summary.legacy_duration_median_seconds == 12.0
    assert summary.direct_duration_median_seconds == 24.0
    assert summary.duration_regression_ratio == 1.0
    assert summary.passed is False
    assert "duration regression" in summary.failure_reasons[0]


def test_promotion_summary_rejects_terminal_errors_and_fallbacks() -> None:
    harness = _load_harness()
    legacy = [
        harness.PromotionRunEvidence("legacy_agent_runtime", 1, 10.0, 10, False, None),
        harness.PromotionRunEvidence("legacy_agent_runtime", 2, 10.0, 10, False, None),
        harness.PromotionRunEvidence("legacy_agent_runtime", 3, 10.0, 10, False, None),
    ]
    direct = [
        harness.PromotionRunEvidence("direct_rlm", 1, 10.0, 10, True, None),
        harness.PromotionRunEvidence("direct_rlm", 2, 10.0, 10, False, "provider unavailable"),
        harness.PromotionRunEvidence("direct_rlm", 3, 10.0, 10, False, None),
    ]

    summary = harness._build_promotion_summary(legacy, direct, max_regression_ratio=0.25)

    assert summary.passed is False
    assert any("fallback" in reason for reason in summary.failure_reasons)
    assert any("terminal error" in reason for reason in summary.failure_reasons)


def test_promotion_mode_requires_distinct_backend_targets() -> None:
    harness = _load_harness()

    with pytest.raises(ValueError, match="distinct"):
        harness._validate_promotion_targets(
            legacy_server_url="http://127.0.0.1:8000",
            direct_server_url="http://127.0.0.1:8000",
        )


def test_promotion_failure_is_added_to_an_otherwise_passing_summary() -> None:
    harness = _load_harness()
    passing = harness.PromotionSummary(10.0, 10.0, 10.0, 10.0, 0.0, 0.0, True, ())

    failed = harness._with_promotion_failure(passing, "resumed direct session did not retain all three turns")

    assert failed.passed is False
    assert failed.failure_reasons == ("resumed direct session did not retain all three turns",)
