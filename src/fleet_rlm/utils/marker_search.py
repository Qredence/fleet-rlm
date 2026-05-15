"""Shared deep-marker search utility for RLM runtime."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from unittest.mock import Mock

# Attributes commonly inspected on prediction-like objects for error markers.
_PREDICTION_ATTRS = frozenset(
    {
        "answer",
        "reasoning",
        "code",
        "trajectory",
        "repl_history",
        "history",
    }
)

# Maximum recursion depth to prevent runaway traversal.
_MAX_DEPTH = 6


def contains_marker(value: Any, marker: str) -> bool:
    """Recursively search *value* for *marker* string.

    Traverses mappings, sequences, object ``__dict__``s, and a curated
    list of object attributes.  Uses an *id* set to avoid re-traversing
    identical objects and to break cycles.

    Args:
        value: Any object to search.
        marker: Substring to look for.

    Returns:
        ``True`` if *marker* is found anywhere inside *value*.
    """
    return _contains_marker(value, marker, _depth=0, _seen=set())


def _contains_marker(
    value: Any,
    marker: str,
    *,
    _depth: int,
    _seen: set[int],
) -> bool:
    if _depth > _MAX_DEPTH:
        return False

    # Fast path: strings are the leaf nodes we actually care about.
    if isinstance(value, str):
        return marker in value

    # Primitives cannot contain the marker.
    if value is None or isinstance(value, (bool, int, float)):
        return False

    # Bytes / bytearray – decode lazily only when needed.
    if isinstance(value, (bytes, bytearray)):
        try:
            return marker in value.decode("utf-8", errors="replace")
        except Exception:
            return False

    # Guard against cycles and re-traversal via identity.
    obj_id = id(value)
    if obj_id in _seen:
        return False
    _seen.add(obj_id)

    # Mappings – search values only (keys are usually identifiers).
    if isinstance(value, Mapping):
        for item in value.values():
            if _contains_marker(item, marker, _depth=_depth + 1, _seen=_seen):
                return True
        return False

    # Sequences (excluding str/bytes handled above).
    if isinstance(value, Sequence):
        for item in value:
            if _contains_marker(item, marker, _depth=_depth + 1, _seen=_seen):
                return True
        return False

    # Generic objects – inspect __dict__ and a curated attr list.
    value_dict = getattr(value, "__dict__", None)
    if isinstance(value_dict, dict):
        filtered = {key: item for key, item in value_dict.items() if key in _PREDICTION_ATTRS}
        if _contains_marker(filtered, marker, _depth=_depth + 1, _seen=_seen):
            return True

    if not isinstance(value, Mock):
        for attr in _PREDICTION_ATTRS:
            try:
                attr_value = getattr(value, attr)
            except Exception:
                continue
            if _contains_marker(attr_value, marker, _depth=_depth + 1, _seen=_seen):
                return True

    return False
