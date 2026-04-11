"""Hosted execution-event helpers owned by the Agent Framework host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


def _string_or_none(value: object) -> str | None:
    return str(value).strip() or None if value else None


@dataclass(frozen=True, slots=True)
class HostedExecutionStateRefs:
    """Lightweight references to Daytona-owned execution state."""

    sandbox_id: str | None = None
    volume_name: str | None = None
    workspace_path: str | None = None
    interpreter_context_id: str | None = None


@dataclass(frozen=True, slots=True)
class HostedExecutionEvent:
    """Normalized hosted execution event plus Daytona-backed state handles."""

    payload: dict[str, Any]
    state_refs: HostedExecutionStateRefs


class HostedExecutionEventRouter(Protocol):
    """Callback used for lightweight host-side execution event fan-out."""

    def __call__(self, event: HostedExecutionEvent) -> None: ...


class HostedExecutionStepBuilder(Protocol):
    """Minimal step-builder surface needed by the hosted REPL bridge."""

    def from_interpreter_hook(self, payload: dict[str, Any]) -> Any: ...


class HostedExecutionStepSink(Protocol):
    """Minimal lifecycle sink surface needed by the hosted REPL bridge."""

    @property
    def run_completed(self) -> bool: ...

    async def emit_step(self, step: Any) -> None: ...

    async def persist_step(self, step: Any) -> None: ...

    def record_persistence_error(self, exc: Exception) -> None: ...


def resolve_hosted_execution_state_refs(
    *, interpreter: Any | None
) -> HostedExecutionStateRefs:
    """Return only lightweight references to Daytona-owned execution state."""

    if interpreter is None:
        return HostedExecutionStateRefs()

    runtime_metadata_fn = getattr(interpreter, "current_runtime_metadata", None)
    runtime_metadata_raw = (
        runtime_metadata_fn() if callable(runtime_metadata_fn) else {}
    )
    runtime_metadata = (
        runtime_metadata_raw if isinstance(runtime_metadata_raw, dict) else {}
    )

    session = getattr(interpreter, "_session", None)
    return HostedExecutionStateRefs(
        sandbox_id=_string_or_none(runtime_metadata.get("sandbox_id"))
        or _string_or_none(getattr(interpreter, "_persisted_sandbox_id", None)),
        volume_name=_string_or_none(runtime_metadata.get("volume_name"))
        or _string_or_none(getattr(interpreter, "volume_name", None))
        or _string_or_none(getattr(interpreter, "_persisted_volume_name", None)),
        workspace_path=_string_or_none(runtime_metadata.get("workspace_path"))
        or _string_or_none(getattr(interpreter, "_persisted_workspace_path", None)),
        interpreter_context_id=_string_or_none(getattr(session, "context_id", None))
        or _string_or_none(getattr(interpreter, "_persisted_context_id", None)),
    )


def normalize_hosted_execution_event(
    payload: dict[str, Any] | None,
    *,
    interpreter: Any | None,
) -> HostedExecutionEvent:
    """Normalize one hosted execution event without copying interpreter state."""

    payload_obj = dict(payload) if isinstance(payload, dict) else {}
    return HostedExecutionEvent(
        payload=payload_obj,
        state_refs=resolve_hosted_execution_state_refs(interpreter=interpreter),
    )
