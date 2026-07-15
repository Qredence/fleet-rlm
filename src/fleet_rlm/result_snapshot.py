"""Private commit-gated typed-result snapshot contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol, cast
from uuid import UUID

from fleet_rlm.rlm.dspy_contract import JsonValue, PredictionResult, validate_rlm_usage

RESULT_SNAPSHOT_SCHEMA_VERSION = 1


class ResultSnapshotSink(Protocol):
    """Optional Run-scoped private sink supplied only by durable environments."""

    def result_path(self, session_id: UUID, run_id: UUID) -> str: ...

    async def write(self, location: str, data: bytes) -> None: ...

    async def remove(self, location: str) -> None: ...


def _mutable_json(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _mutable_json(cast(JsonValue, item)) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable_json(item) for item in value]
    return value


def encode_result_snapshot(
    session_id: UUID,
    run_id: UUID,
    prediction: PredictionResult,
    usage: Mapping[str, object],
) -> bytes:
    """Encode only the closed typed-result derivative as deterministic UTF-8 JSON."""
    exact_usage = validate_rlm_usage(usage)
    value = {
        "schema_version": RESULT_SNAPSHOT_SCHEMA_VERSION,
        "session_id": str(session_id),
        "run_id": str(run_id),
        "contract_id": prediction.schema_id,
        "contract_version": prediction.schema_version,
        "outputs": _mutable_json(prediction.outputs),
        "usage": exact_usage,
    }
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
