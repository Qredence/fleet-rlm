"""Closed transport-neutral JSON value shapes shared across Fleet RLM modules.

One invariant — the closed JSON value shape — is defined once here and imported
wherever Runtime Events, committed Turn payloads, and RLM usage records need a
canonical `JsonScalar`/`JsonValue` contract. Per-module freeze/thaw rationale
stays in the owning modules; only the type contract centralizes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from math import isfinite
from typing import TypeAlias

JsonScalar: TypeAlias = bool | int | float | str | None
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


def validate_json_value(value: object, *, path: str = "value") -> None:
    """Validate one strict JSON value without coercing or leaking its repr.

    Python's ``json.dumps`` accepts ``NaN`` by default and stringifies mapping
    keys in a few code paths.  Fleet's broker and host Tool seams must reject
    both behaviours so the value seen by native DSPy is the value the host
    actually returned.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise TypeError(f"{path} is not a finite JSON number")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} contains a non-string mapping key")
            validate_json_value(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_json_value(item, path=f"{path}[{index}]")
        return
    raise TypeError(f"{path} contains an unsupported value")


def strict_json_dumps(value: object) -> str:
    """Serialize a validated JSON value with non-standard numbers disabled."""
    validate_json_value(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
