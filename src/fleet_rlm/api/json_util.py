"""Deep JSON normalization shared by the two AI SDK UI projection paths."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def to_plain_json(value: Any) -> Any:
    """Recursively convert mappings and sequences to plain dicts/lists.

    Runtime Event payloads carry frozen ``MappingProxyType`` values and tuples;
    both live and reload projections must normalize them to JSON-safe
    containers before they are serialized for the TUI.
    """
    if isinstance(value, Mapping):
        return {str(key): to_plain_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_plain_json(item) for item in value]
    return value
