"""Bounded Tool event projections shared by workspace host tool hosts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from fleet_rlm.json_types import JsonValue
from fleet_rlm.tool_events import bound_event_text

_EVENT_TEXT_MAX_CHARS = 256


def project_event_fields(result: object, fields: tuple[str, ...]) -> JsonValue:
    """Keep only the allowlisted result fields, bounding each string value."""
    if not isinstance(result, Mapping):
        return {}
    values = cast(Mapping[str, JsonValue], result)
    return {
        key: bound_event_text(values[key], max_chars=_EVENT_TEXT_MAX_CHARS)
        if isinstance(values[key], str)
        else values[key]
        for key in fields
        if key in values
    }


__all__ = ["project_event_fields"]
