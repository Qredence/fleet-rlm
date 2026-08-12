"""Reload UIMessagePart discriminated-union wire fixtures.

Each durable part variant validates under the exact Pydantic model and
serializes back to the same JSON. Part models omit top-level ``None`` fields
so HTTP reload JSON matches lean producer dicts without padding.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from fleet_rlm.api.schemas import UIMessagePart

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ui_message_parts.json"
_ADAPTER = TypeAdapter(UIMessagePart)
_UNION_TYPES = get_args(get_args(UIMessagePart)[0])
_EXPECTED_TYPES = frozenset(member.model_fields["type"].default for member in _UNION_TYPES)


def _fixtures() -> list[dict]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("payload", _fixtures(), ids=lambda item: f"{item['type']}:{item.get('state', '')}")
def test_ui_message_part_fixture_round_trips(payload: dict) -> None:
    validated = _ADAPTER.validate_python(payload)
    assert validated.type == payload["type"]
    # Match SessionTurnPageResponse part serialization (top-level Nones omitted).
    assert _ADAPTER.dump_python(validated, mode="json", by_alias=True) == payload


def test_ui_message_part_fixtures_cover_every_variant() -> None:
    observed = {item["type"] for item in _fixtures()}
    assert observed == _EXPECTED_TYPES


def test_ui_message_part_rejects_unknown_type_and_cross_variant_fields() -> None:
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"type": "data-future", "data": {}})
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"type": "text", "text": "ok", "state": "done", "toolName": "x"})
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"type": "step-start", "text": "nope"})


def test_error_dynamic_tool_omits_null_output_under_http_dump_mode() -> None:
    payload = next(item for item in _fixtures() if item["type"] == "dynamic-tool" and item["state"] == "output-error")
    validated = _ADAPTER.validate_python(payload)
    dumped = _ADAPTER.dump_python(validated, mode="json", by_alias=True)
    assert "output" not in dumped
    assert "errorText" in dumped


def test_session_turn_page_keeps_null_next_cursor() -> None:
    from fleet_rlm.api.schemas import SessionTurnPageResponse, UIMessageResponse

    page = SessionTurnPageResponse(
        items=[
            UIMessageResponse.model_validate(
                {"id": "1", "role": "user", "parts": [{"type": "text", "text": "hi", "state": "done"}]}
            )
        ],
        next_after_sequence=None,
    )
    dumped = page.model_dump(mode="json", by_alias=True)
    assert dumped["next_after_sequence"] is None
    assert dumped["items"][0]["parts"][0] == {"type": "text", "text": "hi", "state": "done"}
