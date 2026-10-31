"""Validation for Fleet's JSON envelopes inside native FastAPI SSE frames."""

from __future__ import annotations

import math
from typing import Any, Literal

from fastapi.sse import ServerSentEvent
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)


class _OpenPayload(BaseModel):
    """Known terminal vocabulary with room for additive and historical result fields."""

    model_config = ConfigDict(extra="allow")


class _TurnEnvironment(_OpenPayload):
    """Provider and deadline fields shared by successful and failed outcomes."""

    snapshot_name: str | None = None
    snapshot_id: str | None = None
    snapshot_cpu: int | None = None
    snapshot_mem: int | None = None
    snapshot_disk: int | None = None
    snapshot_definition_digest: str | None = None
    snapshot_build_digest: str | None = None
    deadline_source: str | None = None
    effective_seconds: float | None = None
    root_sandbox_id: str | None = None
    ttl_minutes: int | None = None
    auto_stop_interval: int | None = None
    maximum_lifespan_minutes: int | None = None
    provider_owned: bool | None = None


class TurnCompletePayload(_TurnEnvironment):
    """Canonical successful completion, retaining historical absence and nulls."""

    event: Literal["turn_complete"]
    turn_id: str | None = None
    answer: str | None = None
    route: str | None = None
    learned_skill_note: str | None = None
    artifacts: list[str] | None = None
    lm_calls: int | None = None
    sandbox_errors: int | None = None
    submit_rejections: int | None = None
    budget_breakdown: dict[str, Any] | None = None
    outcome: Literal["completed"] | None = None
    duration_seconds: float | None = None
    trace_id: str | None = None
    cleanup: Literal["not_required", "stopped", "unconfirmed"] | None = None


class ErrorPayload(_TurnEnvironment):
    """Sanitized execution failure or bounded idempotency refusal."""

    event: Literal["error"]
    turn_id: str | None = None
    outcome: Literal["failed", "cancelled"] | None = None
    duration_seconds: float | None = None
    request_id: str | None = None
    error: str | None = None
    cleanup: Literal["not_required", "stopped", "unconfirmed"] | None = None
    trace_id: str | None = None


class TurnCancelledPayload(_TurnEnvironment):
    """Sanitized cancellation outcome."""

    event: Literal["turn_cancelled"]
    turn_id: str | None = None
    outcome: Literal["cancelled"] | None = None
    duration_seconds: float | None = None
    request_id: str | None = None
    error: str | None = None
    cleanup: Literal["not_required", "stopped", "unconfirmed"] | None = None
    trace_id: str | None = None


class DonePayload(BaseModel):
    """The final marker has no application payload."""

    model_config = ConfigDict(extra="forbid")

    event: Literal["done"]


class DynamicPayload(BaseModel):
    """Bounded open payload for nonterminal status, activity, and progress events."""

    model_config = ConfigDict(extra="allow")

    event: StrictStr = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def bounded_fields(self) -> DynamicPayload:
        """Keep open progress/activity events small and disallow reserved outcomes."""
        if self.event in _TERMINAL_PAYLOADS:
            raise ValueError("reserved terminal event must use its named payload contract")
        if len(self.model_extra or {}) > 100:
            raise ValueError("SSE event has too many flattened fields")
        if any(len(key) > 200 for key in (self.model_extra or {})):
            raise ValueError("SSE event field name is too long")
        return self


_TERMINAL_PAYLOADS: dict[str, TypeAdapter[Any]] = {
    "turn_complete": TypeAdapter(TurnCompletePayload),
    "error": TypeAdapter(ErrorPayload),
    "turn_cancelled": TypeAdapter(TurnCancelledPayload),
    "done": TypeAdapter(DonePayload),
}
_ENVELOPE_KEYS = frozenset(
    {
        "version",
        "event",
        "session_id",
        "turn_id",
        "agent_id",
        "parent_agent_id",
        "sandbox_id",
        "seq",
        "timestamp",
        "payload",
    }
)


class FleetEnvelope(BaseModel):
    """Complete version-1 JSON envelope validated before native SSE framing."""

    model_config = ConfigDict(extra="allow")

    version: Literal[1]
    event: StrictStr = Field(min_length=1, max_length=80)
    session_id: StrictStr
    turn_id: StrictStr
    agent_id: StrictStr | None
    parent_agent_id: StrictStr | None
    sandbox_id: StrictStr | None
    seq: StrictInt = Field(ge=1)
    timestamp: float = Field(strict=True, allow_inf_nan=False)
    payload: dict[str, Any]

    @field_validator("payload")
    @classmethod
    def bound_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Bound nested event fields before allowing open extension data."""
        if len(value) > 100:
            raise ValueError("SSE payload has too many fields")
        if any(len(key) > 200 for key in value):
            raise ValueError("SSE payload field name is too long")
        return value

    @model_validator(mode="after")
    def validate_event_payload_and_flattening(self) -> FleetEnvelope:
        """Validate reserved event data and the Envelope.wrap mirror exactly."""
        # Envelope.wrap spreads the application payload at the top level as well as
        # nesting it. Requiring that exact relationship catches malformed wire data
        # without filtering any supported historical or additive payload field.
        extra = self.model_extra or {}
        flattened: dict[str, Any] = {}
        for key, value in self.payload.items():
            if key in _ENVELOPE_KEYS:
                # Envelope.wrap spreads the payload after its identity fields, so
                # activity callbacks can legitimately mirror agent/sandbox identity
                # in both positions. The wrapper itself and the event discriminator
                # cannot be application payload fields.
                if key in {"event", "payload"} or not _same_json_value(getattr(self, key), value):
                    raise ValueError("SSE payload overwrites an envelope field")
                flattened[key] = getattr(self, key)
            elif key not in extra or not _same_json_value(extra[key], value):
                raise ValueError("SSE envelope fields do not match the nested payload")
            else:
                flattened[key] = extra[key]
        if not _same_json_value(flattened, self.payload):
            raise ValueError("SSE envelope fields do not match the nested payload")

        reserved = _TERMINAL_PAYLOADS.get(self.event)
        candidate = {"event": self.event, **self.payload}
        if reserved is not None:
            # Reserved outcomes are always validated by their named schema. In
            # particular, a malformed completion cannot fall through to progress.
            reserved.validate_python(candidate, strict=True)
        else:
            DynamicPayload.model_validate(candidate)
        return self


def _same_json_value(left: Any, right: Any) -> bool:
    """Compare mirrored data while treating duplicated non-finite floats alike."""
    if left is right:
        return True
    if isinstance(left, float) and isinstance(right, float):
        if math.isnan(left) and math.isnan(right):
            return True
        return left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same_json_value(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same_json_value(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def frame_envelope(wire: dict[str, Any]) -> ServerSentEvent:
    """Validate all Fleet JSON fields, then use FastAPI's native SSE serializer."""
    try:
        envelope = FleetEnvelope.model_validate(wire)
    except ValidationError:
        # Pydantic errors include rejected input values. Do not expose payloads or
        # exception details through the already-open stream's error logging path.
        raise RuntimeError("Turn event contract validation failed") from None
    data = envelope.model_dump(mode="json", exclude_unset=True)
    return ServerSentEvent(data=data, id=str(envelope.seq))
