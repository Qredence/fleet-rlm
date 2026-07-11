"""SSE serialization for clean-backend RuntimeEvent envelopes."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any
from uuid import UUID

from fleet_rlm_clean.rlm.events import RuntimeEvent


def _json_default(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    msg = f"Object of type {type(value).__name__} is not JSON serializable"
    raise TypeError(msg)


def _event_to_public_dict(event: RuntimeEvent) -> dict[str, Any]:
    return {
        "schema_version": event.schema_version,
        "event_id": event.event_id,
        "run_id": event.run_id,
        "session_id": event.session_id,
        "sequence": event.sequence,
        "timestamp": event.timestamp,
        "kind": event.kind.value,
        "payload": dict(event.payload),
    }


class SSEProjector:
    """Project RuntimeEvents into SSE ``data:`` lines without FastAPI imports."""

    def project(self, events: Iterable[RuntimeEvent]) -> Iterator[str]:
        """Yield one SSE data frame per event."""
        for event in events:
            payload = json.dumps(_event_to_public_dict(event), default=_json_default)
            yield f"data: {payload}\n\n"

    def keepalive(self) -> str:
        """Return an SSE comment that does not consume a sequence number."""
        return ": keepalive\n\n"
