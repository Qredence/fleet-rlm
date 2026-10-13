"""Closed transport-neutral JSON value shapes shared across Fleet RLM modules.

One invariant — the closed JSON value shape — is defined once here and imported
wherever Runtime Events, committed Turn payloads, and RLM usage records need a
canonical `JsonScalar`/`JsonValue` contract. Per-module freeze/thaw rationale
stays in the owning modules; only the type contract centralizes.

The canonical serialization behind persisted digests (idempotency keys, cache
keys, optimization fingerprints) is centralized here too, so each digest site
shares one encoder recipe and names only its byte-level flags.
"""

from __future__ import annotations

import hashlib
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


def canonical_json_bytes(value: object, *, ensure_ascii: bool, allow_nan: bool) -> bytes:
    """Encode one JSON value with sorted keys and compact separators as UTF-8.

    ``ensure_ascii`` and ``allow_nan`` are required keywords because they change
    the persisted bytes. Each call site names the exact choice it has always made
    instead of inheriting ``json.dumps`` defaults.
    """
    return json.dumps(
        value,
        ensure_ascii=ensure_ascii,
        allow_nan=allow_nan,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_json_sha256(value: object, *, ensure_ascii: bool, allow_nan: bool) -> str:
    """Return the lowercase SHA-256 hex digest of ``canonical_json_bytes(value)``."""
    return hashlib.sha256(canonical_json_bytes(value, ensure_ascii=ensure_ascii, allow_nan=allow_nan)).hexdigest()
