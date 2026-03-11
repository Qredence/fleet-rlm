"""Internal JSON-line protocol for the Daytona-backed RLM pilot."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

FRAME_PREFIX = "__fleet_rlm_daytona__:"


def _coerce_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items()}


def encode_frame(payload: dict[str, Any]) -> str:
    """Encode a JSON-line frame for the sandbox driver/runtime."""

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
    """Request emitted by sandbox code that needs a host-side callback."""

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


@dataclass(slots=True)
class RunReady:
    """Ready signal for the sandbox-self-orchestrated runtime."""

    message: str = "ready"
    type: str = "run_ready"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RunStartRequest:
    """Request to start one sandbox-self-orchestrated Daytona rollout."""

    request_id: str
    payload: dict[str, Any]
    type: str = "run_start"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunStartRequest":
        return cls(
            request_id=str(payload["request_id"]),
            payload=_coerce_mapping(payload.get("payload", {}) or {}),
        )


@dataclass(slots=True)
class RunCancelRequest:
    """Request to cancel the active sandbox-self-orchestrated run."""

    request_id: str
    type: str = "run_cancel"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunCancelRequest":
        return cls(request_id=str(payload["request_id"]))


@dataclass(slots=True)
class RunEventFrame:
    """Structured streaming event emitted from the sandbox runtime."""

    request_id: str
    kind: str
    text: str = ""
    payload: dict[str, Any] | None = None
    type: str = "run_event"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunEventFrame":
        return cls(
            request_id=str(payload["request_id"]),
            kind=str(payload.get("kind", "") or ""),
            text=str(payload.get("text", "") or ""),
            payload=_coerce_mapping(payload.get("payload", {}) or {}),
        )


@dataclass(slots=True)
class RunResultEnvelope:
    """Terminal successful result emitted by the sandbox runtime."""

    request_id: str
    result: dict[str, Any]
    type: str = "run_result"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunResultEnvelope":
        return cls(
            request_id=str(payload["request_id"]),
            result=_coerce_mapping(payload.get("result", {}) or {}),
        )


@dataclass(slots=True)
class RunErrorEnvelope:
    """Terminal error emitted by the sandbox runtime."""

    request_id: str
    error: str
    category: str | None = None
    type: str = "run_error"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunErrorEnvelope":
        return cls(
            request_id=str(payload["request_id"]),
            error=str(payload.get("error", "") or ""),
            category=str(payload["category"])
            if payload.get("category") is not None
            else None,
        )
