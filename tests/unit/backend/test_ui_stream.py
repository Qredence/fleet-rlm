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
