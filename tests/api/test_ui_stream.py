"""Typed live Fleet UI stream transport and trace-id projection contracts.

* ``test_ui_stream.py``: Typed live Fleet UI stream transport contract tests.
* ``test_sse_trace_id.py``: SSE projection of optional operator-facing MLflow trace ids.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from fleet_rlm.api.sse import AISDKUIProjector
from fleet_rlm.api.ui_stream import FleetUIMessageChunkAdapter, fleet_ui_chunk_payload
from fleet_rlm.rlm.events import EventRecorder, RunCompleted, RunStarted
from tests.support.testing_app import create_testing_app

# --- from test_ui_stream.py -------------------------------------------
_FIXTURE = (
    Path(__file__).resolve().parents[2] / "tools" / "fleet-tui" / "src" / "tests" / "fixtures" / "turn-stream.jsonl"
)


def _fixture_chunks() -> list[dict[str, object]]:
    return [json.loads(line) for line in _FIXTURE.read_text(encoding="utf-8").splitlines() if line != "[DONE]"]


def test_every_golden_chunk_validates_as_a_typed_transport_chunk() -> None:
    chunks = _fixture_chunks()
    assert chunks
    for chunk in chunks:
        assert fleet_ui_chunk_payload(chunk) == chunk
        FleetUIMessageChunkAdapter.validate_python(chunk, strict=False)


def test_typed_transport_chunks_reject_unknown_and_malformed_variants() -> None:
    with pytest.raises(ValidationError):
        FleetUIMessageChunkAdapter.validate_python({"type": "data-future", "data": {}}, strict=False)
    with pytest.raises(ValidationError):
        FleetUIMessageChunkAdapter.validate_python({"type": "turn_start"}, strict=False)
    with pytest.raises(ValidationError):
        FleetUIMessageChunkAdapter.validate_python(
            {
                "type": "turn_start",
                "runId": "24868d66-eb2e-4c14-8da1-889cd9c0a8ff",
                "extra": True,
            },
            strict=False,
        )


def test_openapi_stream_schema_is_derived_as_one_inline_discriminated_contract() -> None:
    schema = create_testing_app().openapi()["components"]["schemas"]["FleetUIMessageChunk"]
    assert "oneOf" in schema
    assert "$defs" not in schema
    assert "discriminator" not in schema
    by_type = {variant["properties"]["type"]["const"]: variant for variant in schema["oneOf"]}
    assert list(by_type) == list(
        (
            "turn_start",
            "turn_status",
            "step_start",
            "step_finish",
            "reasoning",
            "code",
            "output",
            "tool_call",
            "tool_result",
            "text",
            "skill",
            "child_progress",
            "attachment",
            "warning",
            "artifact",
            "usage",
            "structured_result",
            "turn_finish",
            "turn_cancelled",
            "turn_error",
        )
    )
    assert "$ref" not in json.dumps(schema)


def test_serializer_returns_canonical_model_dump_not_the_original_mapping() -> None:
    payload = {
        "type": "turn_start",
        "run_id": "24868d66-eb2e-4c14-8da1-889cd9c0a8ff",
        "delivery": "live",
        "trace_id": "tr-1",
    }
    emitted = fleet_ui_chunk_payload(payload)
    assert emitted == {
        "type": "turn_start",
        "runId": "24868d66-eb2e-4c14-8da1-889cd9c0a8ff",
        "delivery": "live",
        "traceId": "tr-1",
    }
    assert "run_id" not in emitted
    assert "trace_id" not in emitted


def test_serializer_canonicalizes_snake_case_tool_field_names() -> None:
    payload = {
        "type": "tool_result",
        "tool_call_id": "call-1",
        "error": "sandbox unavailable",
    }
    emitted = fleet_ui_chunk_payload(payload)
    assert emitted == {
        "type": "tool_result",
        "toolCallId": "call-1",
        "error": "sandbox unavailable",
    }
    assert "tool_call_id" not in emitted


def test_known_optional_nulls_use_established_omit_none_serialization() -> None:
    payload = {
        "type": "code",
        "stream_id": "code-1",
        "code": "print(1)",
        "step": 1,
        "final": True,
    }
    assert fleet_ui_chunk_payload(payload) == {
        "type": "code",
        "streamId": "code-1",
        "code": "print(1)",
        "step": 1,
        "isDelta": False,
        "final": True,
    }


def test_declared_dynamic_json_boundaries_remain_intentionally_extensible() -> None:
    payloads = (
        {
            "type": "tool_call",
            "toolCallId": "call-1",
            "toolName": "execute",
            "input": {"futureToolInput": [1, True, None]},
        },
        {
            "type": "tool_result",
            "toolCallId": "call-1",
            "output": {"futureToolOutput": {"status": "ok"}},
        },
        {"type": "usage", "iterations": 1, "usage": {"futureUsageRecord": {"root": [1, 2]}}},
        {
            "type": "structured_result",
            "schemaId": "answer",
            "schemaVersion": "1",
            "value": {"future": [1, {"x": True}]},
        },
    )
    for payload in payloads:
        assert fleet_ui_chunk_payload(json.loads(json.dumps(payload))) == payload


# --- from test_sse_trace_id.py ----------------------------------------
def test_start_and_finish_include_trace_id_when_present() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = AISDKUIProjector()
    start = projector.project(recorder.record(RunStarted(delivery="live", trace_id="tr-abc")))
    finish = projector.project(recorder.record(RunCompleted(checkpoint_version=1, delivery="live", trace_id="tr-abc")))
    assert start[0]["traceId"] == "tr-abc"
    assert finish[-1]["traceId"] == "tr-abc"


def test_start_omits_trace_id_when_absent() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    payloads = AISDKUIProjector().project(recorder.record(RunStarted(delivery="live")))
    assert "traceId" not in payloads[0]
