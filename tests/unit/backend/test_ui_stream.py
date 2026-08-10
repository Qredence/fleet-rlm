"""Typed live Fleet UI stream transport contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from fleet_rlm.api.ui_stream import FleetUIMessageChunkAdapter, fleet_ui_chunk_payload
from fleet_rlm.composition.testing import create_testing_app

_FIXTURE = (
    Path(__file__).resolve().parents[3] / "tools" / "fleet-tui" / "src" / "tests" / "fixtures" / "turn-stream.jsonl"
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
        FleetUIMessageChunkAdapter.validate_python({"type": "text-delta", "id": "text-1"}, strict=False)
    with pytest.raises(ValidationError):
        FleetUIMessageChunkAdapter.validate_python(
            {
                "type": "start",
                "messageId": "24868d66-eb2e-4c14-8da1-889cd9c0a8ff",
                "messageMetadata": {},
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
            "start",
            "start-step",
            "finish-step",
            "reasoning-start",
            "reasoning-delta",
            "reasoning-end",
            "data-status",
            "data-skill",
            "data-rlm-code",
            "data-rlm-output",
            "tool-input-available",
            "tool-output-available",
            "tool-output-error",
            "data-attachment",
            "data-warning",
            "data-artifact",
            "data-usage",
            "data-structured-result",
            "text-start",
            "text-delta",
            "text-end",
            "finish",
            "abort",
            "error",
        )
    )
    assert "$ref" not in json.dumps(schema)


def test_serializer_returns_canonical_model_dump_not_the_original_mapping() -> None:
    payload = {
        "type": "start",
        "message_id": "24868d66-eb2e-4c14-8da1-889cd9c0a8ff",
        "message_metadata": {"delivery": "live", "policy": {"count": 1}},
    }
    emitted = fleet_ui_chunk_payload(payload)
    assert emitted == {
        "type": "start",
        "messageId": "24868d66-eb2e-4c14-8da1-889cd9c0a8ff",
        "messageMetadata": {"delivery": "live", "policy": {"count": 1}},
    }
    assert "message_id" not in emitted
    assert "message_metadata" not in emitted

    payload["message_metadata"].clear()
    assert emitted["messageMetadata"]["delivery"] == "live"


def test_serializer_canonicalizes_snake_case_tool_field_names() -> None:
    payload = {
        "type": "tool-output-error",
        "tool_call_id": "call-1",
        "error_text": "sandbox unavailable",
        "provider_executed": False,
    }
    emitted = fleet_ui_chunk_payload(payload)
    assert emitted == {
        "type": "tool-output-error",
        "toolCallId": "call-1",
        "errorText": "sandbox unavailable",
        "providerExecuted": False,
    }
    assert all(key not in emitted for key in ("tool_call_id", "error_text", "provider_executed"))


def test_known_optional_nulls_use_established_omit_none_serialization() -> None:
    payload = {
        "type": "data-rlm-code",
        "id": "code-1",
        "data": {"code": "print(1)", "step": 1, "stream_id": None, "is_delta": None, "is_final": True},
    }
    assert fleet_ui_chunk_payload(payload) == {
        "type": "data-rlm-code",
        "id": "code-1",
        "data": {"code": "print(1)", "step": 1, "is_final": True},
    }


@pytest.mark.parametrize(
    "core_payload",
    (
        {"type": "data-status", "data": {"phase": "execution", "status": "running"}},
        {"type": "data-skill", "data": {"skill_id": "skill-1", "name": "skill", "version": "1"}},
        {"type": "data-rlm-code", "data": {"code": "print(1)"}},
        {"type": "data-rlm-output", "data": {"output": "1"}},
        {
            "type": "data-attachment",
            "data": {
                "attachment_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "filename": "input.txt",
            },
        },
        {"type": "data-warning", "data": {"message": "warn"}},
        {
            "type": "data-artifact",
            "data": {
                "artifact_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "artifact_kind": "markdown",
            },
        },
        {"type": "data-usage", "data": {"usage": {"iterations": 1}}},
        {
            "type": "data-structured-result",
            "data": {"schema_id": "answer", "schema_version": "1", "value": 7},
        },
    ),
)
def test_nested_data_payloads_are_closed_to_undeclared_fields(core_payload: dict[str, object]) -> None:
    payload = json.loads(json.dumps(core_payload))
    payload["data"]["unknownNested"] = True
    with pytest.raises(ValidationError):
        FleetUIMessageChunkAdapter.validate_python(payload, strict=False)
    with pytest.raises(ValidationError):
        fleet_ui_chunk_payload(payload)


def test_declared_dynamic_json_boundaries_remain_intentionally_extensible() -> None:
    payloads = (
        {
            "type": "start",
            "messageId": "24868d66-eb2e-4c14-8da1-889cd9c0a8ff",
            "messageMetadata": {"futureDeliveryDetail": [1, {"policy": "opaque"}]},
        },
        {
            "type": "tool-input-available",
            "toolCallId": "call-1",
            "toolName": "execute",
            "input": {"futureToolInput": [1, True, None]},
        },
        {
            "type": "tool-output-available",
            "toolCallId": "call-1",
            "output": {"futureToolOutput": {"status": "ok"}},
        },
        {"type": "data-usage", "data": {"usage": {"futureUsageRecord": {"root": [1, 2]}}}},
        {
            "type": "data-structured-result",
            "data": {
                "schema_id": "answer",
                "schema_version": "1",
                "value": {"future": [1, {"x": True}]},
            },
        },
    )
    for payload in payloads:
        assert fleet_ui_chunk_payload(json.loads(json.dumps(payload))) == payload
