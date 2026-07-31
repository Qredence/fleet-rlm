"""Closed transport-neutral JSON value shapes shared across Fleet RLM modules.

One invariant — the closed JSON value shape — is defined once here and imported
wherever Runtime Events, committed Turn payloads, and RLM usage records need a
canonical `JsonScalar`/`JsonValue` contract. Per-module freeze/thaw rationale
stays in the owning modules; only the type contract centralizes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

JsonScalar: TypeAlias = bool | int | float | str | None
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
