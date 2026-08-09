"""QRE-103 certification for canonical Results and typed live transport parity."""

from __future__ import annotations

import json

from fleet_rlm.api.sse import FLEET_UI_CHUNK_TYPES
from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.sessions.assistant_parts import AssistantPartModelUnion
from fleet_rlm.sessions.committed_turn import CommittedTurn

_ASSISTANT_TYPES = {
    model.model_fields["type"].default
    for model in AssistantPartModelUnion
    for default in (model.model_fields["type"].default,)
}
_TRANSPORT_SEMANTICS = set(FLEET_UI_CHUNK_TYPES)


def test_canonical_result_and_live_transport_have_separate_complete_vocabularies() -> None:
    assert {
        "step",
        "reasoning",
        "code",
        "output",
        "tool_call",
        "skill",
        "attachment",
        "warning",
        "status",
        "artifact",
        "usage",
        "structured_result",
        "text",
    } == _ASSISTANT_TYPES
    assert {
        "reasoning-start",
        "reasoning-delta",
        "reasoning-end",
        "tool-input-available",
        "tool-output-available",
        "tool-output-error",
        "data-artifact",
        "data-usage",
        "data-structured-result",
        "text-start",
        "text-delta",
        "text-end",
    } <= _TRANSPORT_SEMANTICS
    assert CommittedTurn.__module__.startswith("fleet_rlm.sessions.")
    assert "tool_call" not in _TRANSPORT_SEMANTICS
    assert "structured_result" not in _TRANSPORT_SEMANTICS


def test_openapi_covers_the_typed_live_transport_vocabulary_once() -> None:
    schema = create_testing_app().openapi()["components"]["schemas"]["FleetUIMessageChunk"]
    variant_types = [variant["properties"]["type"]["const"] for variant in schema["oneOf"]]
    assert variant_types == list(FLEET_UI_CHUNK_TYPES)
    assert len(variant_types) == len(set(variant_types))
    assert "$ref" not in json.dumps(schema)


def test_projected_live_and_reload_semantics_keep_distinct_adapters() -> None:
    from fleet_rlm.api import sse, ui_message, ui_stream
    from fleet_rlm.sessions import assistant_parts

    assert sse.AISDKUIProjector is not ui_stream.FleetUIChunkModel
    assert ui_message._assistant_part is not None
    assert assistant_parts.AssistantPartModel is not ui_stream.FleetUIChunkModel
