"""Internal JSON-line protocol for the Daytona-backed RLM pilot."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

FRAME_PREFIX = "__fleet_rlm_daytona__:"


def _coerce_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items()}


def encode_frame(payload: dict[str, Any]) -> str:
    """Encode a JSON-line frame for the sandbox driver."""

    return f"{FRAME_PREFIX}{json.dumps(payload, ensure_ascii=False, default=repr)}"


def decode_frame(line: str) -> dict[str, Any] | None:
    """Decode a protocol frame if the prefix matches."""

    if not line.startswith(FRAME_PREFIX):
        return None
    return _coerce_mapping(json.loads(line[len(FRAME_PREFIX) :]))


@dataclass(slots=True)
class ExecutionRequest:
    """Request to execute one Python code block in the persistent driver."""

    request_id: str
    code: str
    submit_schema: list[dict[str, Any]] | None = None
    type: str = "execute_request"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExecutionResponse:
    """Response for one executed Python code block."""

    request_id: str
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    final_artifact: dict[str, Any] | None = None
    duration_ms: int = 0
    callback_count: int = 0
    type: str = "execute_response"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExecutionResponse":
        return cls(
            request_id=str(payload["request_id"]),
            stdout=str(payload.get("stdout", "") or ""),
            stderr=str(payload.get("stderr", "") or ""),
            error=str(payload["error"]) if payload.get("error") is not None else None,
            final_artifact=payload.get("final_artifact"),
            duration_ms=int(payload.get("duration_ms", 0) or 0),
            callback_count=int(payload.get("callback_count", 0) or 0),
        )


@dataclass(slots=True)
class HostCallbackRequest:
    """Request emitted by sandbox code that needs a host-side recursive call."""

    callback_id: str
    name: str
    payload: dict[str, Any]
    type: str = "host_callback_request"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HostCallbackRequest":
        return cls(
            callback_id=str(payload["callback_id"]),
            name=str(payload["name"]),
            payload=_coerce_mapping(payload.get("payload", {}) or {}),
        )


@dataclass(slots=True)
class HostCallbackResponse:
    """Response from the host back to the sandbox driver."""

    callback_id: str
    ok: bool
    value: Any = None
    error: str | None = None
    type: str = "host_callback_response"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DriverReady:
    """Ready signal emitted once the sandbox driver is running."""

    message: str = "ready"
    type: str = "driver_ready"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ShutdownRequest:
    """Request to stop the sandbox driver."""

    type: str = "shutdown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ShutdownAck:
    """Acknowledgement for a sandbox driver shutdown."""

    type: str = "shutdown_ack"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
