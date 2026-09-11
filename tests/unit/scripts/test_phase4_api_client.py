from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from scripts.benchmarks.phase4_api_client import (
    Phase4ApiClientError,
    Phase4ApiTrialRunner,
    _parse_sse,
    _record_label,
    _telemetry,
)
from scripts.benchmarks.phase4_campaign import PublicRateCard, Trial, load_cases
from scripts.benchmarks.run_phase4_campaign import _prior_receipt_spend

_CASES = Path(__file__).resolve().parents[3] / "scripts" / "benchmarks" / "phase4_cases.json"


def _trial(case_id: str, arm: str = "D") -> Trial:
    return Trial(case_id, "suitable", 1, arm, ("A", "B", "C", "D"))  # type: ignore[arg-type]


def _usage() -> dict[str, object]:
    return {
        "observed_lm_usage": {
            "root": {"input_tokens": 32, "output_tokens": 16},
            "child": {"input_tokens": 16, "output_tokens": 8},
        },
        "delegation_metrics": {
            "delegated_input_bytes": 64,
            "lm_call_counts": [
                {"role": "root", "recursive_depth": 0, "count": 1},
                {"role": "child", "recursive_depth": 1, "count": 1},
            ],
            "lm_token_totals": [
                {"input_tokens": 32, "output_tokens": 16},
                {"input_tokens": 16, "output_tokens": 8},
            ],
        },
    }


def _stream(
    case, *, finish: bool = True, header: bool = True, error: bool = False, trace_id: str | None = "tr-001"
) -> tuple[dict[str, str], bytes]:
    usage = _usage()
    chunks: list[dict[str, object]] = [
        {"type": "tool-input-available", "toolName": "rlm_query", "input": {"prompt_count": 1}},
        {"type": "data-usage", "data": {"usage": usage}},
        {
            "type": "data-structured-result",
            "data": {
                "value": {
                    "answer": case.expected_answer,
                    "evidence": list(case.required_evidence),
                    "uncertainty": case.required_uncertainty,
                }
            },
        },
    ]
    if finish:
        # Mirror the backend: error finishes project no metadata, so failed
        # trials carry an explicit null linkage instead of a trace identifier.
        metadata = {} if error or trace_id is None else {"messageMetadata": {"traceId": trace_id}}
        chunks.append({"type": "finish", "finishReason": "stop", **metadata})
    if error:
        chunks.insert(0, {"type": "error", "errorText": "discarded"})
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    if finish:
        body += "data: [DONE]\n\n"
    return ({"x-vercel-ai-ui-message-stream": "v1"} if header else {}), body.encode()


def _transport(
    case,
    telemetry_path: Path,
    *,
    finish: bool = True,
    header: bool = True,
    error: bool = False,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/attachments":
            return httpx.Response(201, json={"id": str(uuid4())}, request=request)
        if request.url.path == "/api/sessions":
            return httpx.Response(201, json={"id": str(uuid4())}, request=request)
        if request.url.path.endswith("/turns"):
            token = request.headers["x-fleet-phase4-trial"]
            telemetry_path.write_text(
                json.dumps(
                    {
                        "event": "turn_cleanup",
                        "trial": token,
                        "cleanup": True,
                        "created": 1,
                        "deleted": 1,
                        "sandbox_count": 1,
                        "sandbox_seconds": 1,
                        "shape": [4, 8, 8],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            headers, body = _stream(case, finish=finish, header=header, error=error)
            return httpx.Response(200, headers=headers, content=body, request=request)
        return httpx.Response(404, request=request)

    return httpx.MockTransport(handler)


def test_api_runner_uses_public_sse_and_sanitized_lifecycle_telemetry(tmp_path: Path) -> None:
    case = load_cases(_CASES)[0]
    telemetry = tmp_path / "telemetry.ndjson"
    result = Phase4ApiTrialRunner(
        base_url="http://fake",
        telemetry_path=telemetry,
        transport=_transport(case, telemetry),
    )(_trial(case.identifier), case)

    assert result.completed is True
    assert result.answer == case.expected_answer
    assert result.cited_evidence == case.required_evidence
    assert result.input_tokens == 48
    assert result.output_tokens == 24
    assert result.root_lm_calls == 1
    assert result.child_lm_calls == 1
    assert result.delegated_bytes == 64
    assert result.cleanup_confirmed is True
    assert result.resource_shape == (4, 8, 8)
    assert result.trace_id == "tr-001"


def test_api_runner_leaves_failed_trials_explicitly_untraced(tmp_path: Path) -> None:
    case = load_cases(_CASES)[0]
    telemetry = tmp_path / "telemetry.ndjson"
    result = Phase4ApiTrialRunner(
        base_url="http://fake",
        telemetry_path=telemetry,
        transport=_transport(case, telemetry, error=True),
    )(_trial(case.identifier), case)

    assert result.completed is False
    assert result.trace_id is None


def test_api_runner_drops_unbounded_trace_linkage(tmp_path: Path) -> None:
    case = load_cases(_CASES)[0]
    telemetry = tmp_path / "telemetry.ndjson"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/turns"):
            token = request.headers["x-fleet-phase4-trial"]
            telemetry.write_text(
                json.dumps(
                    {
                        "event": "turn_cleanup",
                        "trial": token,
                        "cleanup": True,
                        "created": 1,
                        "deleted": 1,
                        "sandbox_count": 1,
                        "sandbox_seconds": 1,
                        "shape": [4, 8, 8],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            headers, body = _stream(case, trace_id="x" * 300)
            return httpx.Response(200, headers=headers, content=body, request=request)
        if request.url.path == "/api/attachments":
            return httpx.Response(201, json={"id": str(uuid4())}, request=request)
        if request.url.path == "/api/sessions":
            return httpx.Response(201, json={"id": str(uuid4())}, request=request)
        return httpx.Response(404, request=request)

    result = Phase4ApiTrialRunner(
        base_url="http://fake",
        telemetry_path=telemetry,
        transport=httpx.MockTransport(handler),
    )(_trial(case.identifier), case)

    assert result.completed is True
    assert result.trace_id is None


def test_api_runner_accepts_single_field_text_answer(tmp_path: Path) -> None:
    """Real backends commit a text answer; no structured-result chunk exists."""
    case = load_cases(_CASES)[0]
    telemetry = tmp_path / "telemetry.ndjson"
    blob = json.dumps(
        {
            "answer": case.expected_answer,
            "evidence": list(case.required_evidence),
            "uncertainty": case.required_uncertainty,
        }
    )
    split = len(blob) // 2

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/attachments":
            return httpx.Response(201, json={"id": str(uuid4())}, request=request)
        if request.url.path == "/api/sessions":
            return httpx.Response(201, json={"id": str(uuid4())}, request=request)
        if request.url.path.endswith("/turns"):
            token = request.headers["x-fleet-phase4-trial"]
            telemetry.write_text(
                json.dumps(
                    {
                        "event": "turn_cleanup",
                        "trial": token,
                        "cleanup": True,
                        "created": 1,
                        "deleted": 1,
                        "sandbox_count": 1,
                        "sandbox_seconds": 1,
                        "shape": [4, 8, 8],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            chunks: list[dict[str, object]] = [
                {"type": "text-start", "id": "text-1"},
                {"type": "text-delta", "id": "text-1", "delta": blob[:split]},
                {"type": "text-delta", "id": "text-1", "delta": blob[split:]},
                {"type": "text-end", "id": "text-1"},
                {"type": "data-usage", "data": {"usage": _usage()}},
                {"type": "finish", "finishReason": "stop"},
            ]
            body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
            body += "data: [DONE]\n\n"
            headers = {"x-vercel-ai-ui-message-stream": "v1"}
            return httpx.Response(200, headers=headers, content=body.encode(), request=request)
        return httpx.Response(404, request=request)

    result = Phase4ApiTrialRunner(
        base_url="http://fake",
        telemetry_path=telemetry,
        transport=httpx.MockTransport(handler),
    )(_trial(case.identifier), case)

    assert result.completed is True
    assert result.answer == case.expected_answer
    assert result.cited_evidence == case.required_evidence
    assert result.input_tokens == 48
    assert result.cleanup_confirmed is True


def test_trial_records_use_a_repeat_specific_bounded_label() -> None:
    case = load_cases(_CASES)[0]

    assert _record_label(_trial(case.identifier), case) == "D-p4-suitable-01-r1"


def test_api_runner_rejects_partial_stream_and_keeps_cleanup_observable(tmp_path: Path) -> None:
    case = load_cases(_CASES)[0]
    telemetry = tmp_path / "telemetry.ndjson"
    result = Phase4ApiTrialRunner(
        base_url="http://fake",
        telemetry_path=telemetry,
        transport=_transport(case, telemetry, finish=False),
    )(_trial(case.identifier), case)

    assert result.completed is False
    assert result.error_category == "stream_incomplete"
    assert result.cleanup_confirmed is True
    assert result.resource_shape == (4, 8, 8)
    # Settlement telemetry proves the backend admitted and ran the trial,
    # so authorization held even though the client-side parse failed.
    assert result.authorization_confirmed is True


def test_error_observation_keeps_authorization_fail_closed_without_telemetry(tmp_path: Path) -> None:
    case = load_cases(_CASES)[0]
    telemetry = tmp_path / "telemetry.ndjson"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    result = Phase4ApiTrialRunner(
        base_url="http://fake",
        telemetry_path=telemetry,
        transport=httpx.MockTransport(handler),
    )(_trial(case.identifier), case)

    assert result.completed is False
    assert result.authorization_confirmed is False
    assert result.cleanup_confirmed is False
    assert result.error_category == "http_404"


def test_api_runner_rejects_missing_stream_header(tmp_path: Path) -> None:
    case = load_cases(_CASES)[0]
    telemetry = tmp_path / "telemetry.ndjson"
    result = Phase4ApiTrialRunner(
        base_url="http://fake",
        telemetry_path=telemetry,
        transport=_transport(case, telemetry, header=False),
    )(_trial(case.identifier), case)

    assert result.completed is False
    assert result.error_category == "stream_contract"


def test_api_runner_records_missing_optional_telemetry_as_unknown() -> None:
    case = load_cases(_CASES)[0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/attachments":
            return httpx.Response(201, json={"id": str(uuid4())}, request=request)
        if request.url.path == "/api/sessions":
            return httpx.Response(201, json={"id": str(uuid4())}, request=request)
        if request.url.path.endswith("/turns"):
            headers, body = _stream(case)
            return httpx.Response(200, headers=headers, content=body, request=request)
        return httpx.Response(404, request=request)

    result = Phase4ApiTrialRunner(
        base_url="http://fake",
        telemetry_path=None,
        transport=httpx.MockTransport(handler),
    )(_trial(case.identifier), case)

    assert result.completed is True
    assert result.cleanup_confirmed is False
    assert result.sandbox_count is None
    assert result.resource_shape is None
    assert result.error_category == "telemetry_unavailable"


def test_telemetry_suppresses_provider_cleanup_errors_and_keeps_the_flag() -> None:
    cleanup, sandbox_seconds, sandbox_count, shape, category = _telemetry(
        [
            {
                "event": "turn_cleanup",
                "cleanup": False,
                "error_category": "TimeoutError",
            }
        ]
    )

    assert cleanup is False
    assert sandbox_seconds is None
    assert sandbox_count is None
    assert shape is None
    # Provider exception names never become receipt semantics; the cleanup
    # flag carries the verdict while the turn diagnosis flows separately.
    assert category is None


def test_telemetry_keeps_the_harness_defined_unavailable_token() -> None:
    cleanup, _, _, _, category = _telemetry([])

    assert cleanup is False
    assert category == "cleanup_unavailable"


def test_api_runner_never_verifies_a_stream_that_contains_an_error_frame(tmp_path: Path) -> None:
    case = load_cases(_CASES)[0]
    telemetry = tmp_path / "telemetry.ndjson"
    result = Phase4ApiTrialRunner(
        base_url="http://fake",
        telemetry_path=telemetry,
        transport=_transport(case, telemetry, error=True),
    )(_trial(case.identifier), case)

    assert result.completed is False
    # The request was admitted and streamed to a terminal finish, so
    # authorization held; the turn failure is ordinary outcome evidence.
    assert result.authorization_confirmed is True
    assert result.cleanup_confirmed is True
    assert result.error_category == "turn_failed"


def test_api_runner_preserves_turn_diagnosis_when_cleanup_fails(tmp_path: Path) -> None:
    case = load_cases(_CASES)[0]
    telemetry = tmp_path / "telemetry.ndjson"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/attachments":
            return httpx.Response(201, json={"id": str(uuid4())}, request=request)
        if request.url.path == "/api/sessions":
            return httpx.Response(201, json={"id": str(uuid4())}, request=request)
        if request.url.path.endswith("/turns"):
            token = request.headers["x-fleet-phase4-trial"]
            telemetry.write_text(
                json.dumps(
                    {
                        "event": "turn_cleanup",
                        "trial": token,
                        "cleanup": False,
                        "error_category": "TimeoutError",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            headers, body = _stream(case, error=True)
            return httpx.Response(200, headers=headers, content=body, request=request)
        return httpx.Response(404, request=request)

    result = Phase4ApiTrialRunner(
        base_url="http://fake",
        telemetry_path=telemetry,
        transport=httpx.MockTransport(handler),
    )(_trial(case.identifier), case)

    assert result.completed is False
    assert result.cleanup_confirmed is False
    assert result.authorization_confirmed is True
    assert result.error_category == "turn_failed"


def test_api_runner_rejects_cleanup_success_without_resource_telemetry(tmp_path: Path) -> None:
    case = load_cases(_CASES)[0]
    telemetry = tmp_path / "telemetry.ndjson"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/attachments":
            return httpx.Response(201, json={"id": str(uuid4())}, request=request)
        if request.url.path == "/api/sessions":
            return httpx.Response(201, json={"id": str(uuid4())}, request=request)
        if request.url.path.endswith("/turns"):
            token = request.headers["x-fleet-phase4-trial"]
            telemetry.write_text(
                json.dumps({"event": "turn_cleanup", "trial": token, "cleanup": True}) + "\n",
                encoding="utf-8",
            )
            headers, body = _stream(case)
            return httpx.Response(200, headers=headers, content=body, request=request)
        return httpx.Response(404, request=request)

    result = Phase4ApiTrialRunner(
        base_url="http://fake",
        telemetry_path=telemetry,
        transport=httpx.MockTransport(handler),
    )(_trial(case.identifier), case)

    assert result.completed is True
    assert result.cleanup_confirmed is False
    assert result.error_category == "resource_observation_unavailable"


def test_sse_parser_discards_tool_frames_but_requires_one_terminal_finish() -> None:
    chunks, reason = _parse_sse(
        [
            'data: {"type":"tool-output-available","output":{"private":"discard"}}',
            'data: {"type":"finish","finishReason":"stop"}',
            "data: [DONE]",
        ]
    )

    assert [chunk["type"] for chunk in chunks] == ["finish"]
    assert reason == "stop"


def test_sse_parser_rejects_frames_after_finish_or_duplicate_done() -> None:
    with pytest.raises(Phase4ApiClientError, match="terminal"):
        _parse_sse(
            [
                'data: {"type":"finish","finishReason":"stop"}',
                'data: {"type":"data-status","data":{}}',
                "data: [DONE]",
            ]
        )
    with pytest.raises(Phase4ApiClientError, match="terminal"):
        _parse_sse(
            [
                'data: {"type":"finish","finishReason":"stop"}',
                "data: [DONE]",
                "data: [DONE]",
            ]
        )


def test_api_runner_drops_undeclared_citations_for_scorer(tmp_path: Path) -> None:
    from scripts.benchmarks.phase4_campaign import TrialEnvelope, score_trial

    case = load_cases(_CASES)[0]
    telemetry = tmp_path / "telemetry.ndjson"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/attachments":
            return httpx.Response(201, json={"id": str(uuid4())}, request=request)
        if request.url.path == "/api/sessions":
            return httpx.Response(201, json={"id": str(uuid4())}, request=request)
        if request.url.path.endswith("/turns"):
            token = request.headers["x-fleet-phase4-trial"]
            telemetry.write_text(
                json.dumps(
                    {
                        "event": "turn_cleanup",
                        "trial": token,
                        "cleanup": True,
                        "created": 1,
                        "deleted": 1,
                        "sandbox_count": 1,
                        "sandbox_seconds": 1,
                        "shape": [4, 8, 8],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            headers, body = _stream(case)
            frames = [frame for frame in body.decode().split("\n\n") if frame]
            for index, frame in enumerate(frames):
                value = json.loads(frame.removeprefix("data: ").strip())
                if value.get("type") == "data-structured-result":
                    value["data"]["value"]["evidence"] = ["undeclared-source"]
                    frames[index] = f"data: {json.dumps(value)}"
                    break
            chunks = [f"{frame}\n\n" for frame in frames]
            return httpx.Response(200, headers=headers, content="".join(chunks).encode(), request=request)
        return httpx.Response(404, request=request)

    result = Phase4ApiTrialRunner(
        base_url="http://fake",
        telemetry_path=telemetry,
        transport=httpx.MockTransport(handler),
    )(_trial(case.identifier), case)

    # Undeclared citations are model-content facts, not transport failures:
    # the trial completes and the scorer penalizes the missing evidence.
    assert result.completed is True
    assert result.error_category is None
    assert result.cleanup_confirmed is True
    assert "undeclared-source" not in result.cited_evidence
    scored = score_trial(
        case,
        _trial(case.identifier),
        result,
        PublicRateCard(),
        TrialEnvelope(2_000_000, 500_000, 0, 1, 3_600, 5, 4, 8, 8, maximum_lifetime_seconds=3_600),
    )
    assert scored.evidence_valid is False
    assert scored.verified_success is False


def test_prior_incomplete_receipt_is_not_treated_as_zero_spend(tmp_path: Path) -> None:
    receipt = tmp_path / "prior.json"
    receipt.write_text(json.dumps({"observed_spend_usd": None}), encoding="utf-8")

    assert _prior_receipt_spend(receipt) == (None, "unknown")


def test_prior_observed_receipt_spend_is_read_as_a_bounded_amount(tmp_path: Path) -> None:
    receipt = tmp_path / "prior.json"
    receipt.write_text(json.dumps({"observed_spend_usd": "0.125"}), encoding="utf-8")

    assert _prior_receipt_spend(receipt) == (0.125, "observed")


def test_prior_receipt_spend_rejects_non_finite_amount(tmp_path: Path) -> None:
    receipt = tmp_path / "prior.json"
    receipt.write_text(json.dumps({"observed_spend_usd": "1e10000"}), encoding="utf-8")

    assert _prior_receipt_spend(receipt) == (None, "invalid")


def test_prior_receipt_with_rows_bounds_unknown_spend(tmp_path: Path) -> None:
    from scripts.benchmarks import run_phase4_campaign as driver

    receipt = tmp_path / "prior.json"
    receipt.write_text(
        json.dumps(
            {
                "observed_spend_usd": None,
                "rows": [
                    {"observed_cost_usd": "0.00008512"},
                    {"observed_cost_usd": None},
                ],
            }
        ),
        encoding="utf-8",
    )

    spend, status = _prior_receipt_spend(receipt)

    assert status == "bounded_upper"
    assert spend is not None
    assert spend == pytest.approx(float(driver._envelope().upper_bound_usd(PublicRateCard()) + Decimal("0.00008512")))
