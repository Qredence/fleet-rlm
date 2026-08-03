"""Fail-soft per-Turn MLflow root spans for engineering observability.

Must never affect Turn outcomes. When disabled or when MLflow is unavailable,
``turn_trace`` yields a no-op handle with ``trace_id=None``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any, cast
from uuid import UUID

logger = logging.getLogger(__name__)

_MAX_TRACE_TEXT_CHARS = 1_000


def trace_preview_limit(default: int = _MAX_TRACE_TEXT_CHARS) -> int:
    """Return the configured readable preview bound, or the local default."""
    try:
        from fleet_rlm.observability.tracing import trace_content_max_chars

        return trace_content_max_chars()
    except Exception:
        return default


def _trace_value(value: object) -> object:
    """
    Sanitize and bound a value for safe inclusion in engineering traces.

    Parameters:
        value (object): The value to sanitize for tracing.

    Returns:
        object: A bounded sanitized value, the original primitive value, or the value's type name.
    """
    if isinstance(value, str):
        from fleet_rlm.rlm.sanitize import sanitize_public_text

        return sanitize_public_text(value, max_len=trace_preview_limit())
    if isinstance(value, Mapping):
        from fleet_rlm.rlm.sanitize import sanitize_public_value

        normalized = {str(key): _trace_value(item) for key, item in list(value.items())[:32]}
        return sanitize_public_value(normalized, max_len=trace_preview_limit())
    if isinstance(value, (list, tuple)):
        from fleet_rlm.rlm.sanitize import sanitize_public_value

        normalized = [_trace_value(item) for item in value[:32]]
        return sanitize_public_value(normalized, max_len=trace_preview_limit())
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return type(value).__name__


def _trace_content_preview(value: object) -> str:
    """Return a safe trace-level preview even if policy lookup fails."""
    try:
        from fleet_rlm.observability.tracing import trace_content_preview

        return trace_content_preview(value)
    except Exception:
        return "[redacted]"


def _trace_mapping(values: Mapping[str, object]) -> dict[str, object]:
    """Convert trace data to a sanitized dictionary, returning an empty dictionary if conversion fails."""
    sanitized = _trace_value(values)
    if isinstance(sanitized, dict):
        return cast(dict[str, object], sanitized)
    return {}


def _runtime_detail_payload(detail: object) -> tuple[dict[str, object], str]:
    """Project one public Runtime Event detail into bounded trace fields.

    Runtime Events are the transport-neutral public evidence stream. Keeping
    this projection typed prevents arbitrary provider payloads or private
    callback state from becoming trace content while still making reasoning,
    generated code, tool activity, progress, and the final answer inspectable.
    """
    from fleet_rlm.rlm.events import (
        ArtifactCreated,
        AttachmentRead,
        RLMCode,
        RLMOutput,
        RLMReasoning,
        RunCancelled,
        RunCompleted,
        RunFailed,
        RunStarted,
        RunTimedOut,
        SkillActivated,
        SkillLoaded,
        Status,
        StepFinished,
        StepStarted,
        StructuredResult,
        TextCompleted,
        TextDelta,
        ToolCompleted,
        ToolFailed,
        ToolStarted,
        Usage,
        WarningEvent,
    )

    if isinstance(detail, RunStarted):
        return {"delivery": detail.delivery, "trace_id": detail.trace_id}, "completed"
    if isinstance(detail, Status):
        return {"phase": detail.phase, "status": detail.status, "message": detail.message}, "completed"
    if isinstance(detail, StepStarted):
        return {"step": detail.step}, "completed"
    if isinstance(detail, StepFinished):
        return {"step": detail.step, "duration_ms": detail.duration_ms}, "completed"
    if isinstance(detail, RLMReasoning):
        return {"step": detail.step, "reasoning": detail.text}, "completed"
    if isinstance(detail, RLMCode):
        return {"step": detail.step, "code": detail.code}, "completed"
    if isinstance(detail, RLMOutput):
        return {"step": detail.step, "output": detail.output}, "completed"
    if isinstance(detail, ToolStarted):
        return {
            "tool_call_id": detail.tool_call_id,
            "tool_name": detail.tool_name,
            "tool_input": detail.input,
        }, "completed"
    if isinstance(detail, ToolCompleted):
        return {
            "tool_call_id": detail.tool_call_id,
            "tool_name": detail.tool_name,
            "tool_output": detail.output,
        }, "completed"
    if isinstance(detail, ToolFailed):
        return {
            "tool_call_id": detail.tool_call_id,
            "tool_name": detail.tool_name,
            "error": detail.error,
        }, "failed"
    if isinstance(detail, SkillActivated):
        return {
            "skill_id": detail.skill_id,
            "name": detail.name,
            "version": detail.version,
            "trust": detail.trust,
            "affordances": detail.affordances,
        }, "completed"
    if isinstance(detail, SkillLoaded):
        return {
            "skill_id": detail.skill_id,
            "name": detail.name,
            "version": detail.version,
        }, "completed"
    if isinstance(detail, AttachmentRead):
        return {
            "attachment_id": str(detail.attachment_id),
            "filename": detail.filename,
            "byte_size": detail.byte_size,
        }, "completed"
    if isinstance(detail, WarningEvent):
        return {"message": detail.message, "code": detail.code}, "completed"
    if isinstance(detail, ArtifactCreated):
        return {
            "artifact_id": str(detail.artifact_id),
            "artifact_kind": detail.artifact_kind,
            "title": detail.title,
            "media_type": detail.media_type,
            "byte_size": detail.byte_size,
            "checksum_sha256": detail.checksum_sha256,
        }, "completed"
    if isinstance(detail, Usage):
        return {"usage": detail.value}, "completed"
    if isinstance(detail, StructuredResult):
        return {
            "schema_id": detail.schema_id,
            "schema_version": detail.schema_version,
            "result": detail.value,
        }, "completed"
    if isinstance(detail, TextDelta):
        return {"text_delta": detail.text}, "completed"
    if isinstance(detail, TextCompleted):
        return {"answer": detail.text}, "completed"
    if isinstance(detail, RunCompleted):
        return {
            "checkpoint_version": detail.checkpoint_version,
            "delivery": detail.delivery,
            "duration_ms": detail.duration_ms,
            "trace_id": detail.trace_id,
        }, "completed"
    if isinstance(detail, RunFailed):
        return {
            "failure_category": detail.code,
            "message": detail.message,
            "duration_ms": detail.duration_ms,
        }, "failed"
    if isinstance(detail, RunCancelled):
        return {"message": detail.message, "duration_ms": detail.duration_ms}, "failed"
    if isinstance(detail, RunTimedOut):
        return {"message": detail.message, "duration_ms": detail.duration_ms}, "failed"
    return {"detail_type": type(detail).__name__}, "completed"


def trace_runtime_detail(detail: object, *, sequence: int | None = None) -> None:
    """Record one bounded public Runtime Event as a nested MLflow progress span.

    The hook is intentionally attached to ``EventRecorder`` so live events,
    reconciled trajectory events, and committed final answer events share one
    trace projection. It records explicit public evidence only; hidden model
    chain-of-thought and arbitrary provider payloads are never projected.
    """
    if not _fleet_trace_active.get():
        return
    try:
        kind = str(getattr(detail, "kind", "unknown"))
        allowed_kind_characters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        if not kind or any(character not in allowed_kind_characters for character in kind):
            kind = "unknown"
        outputs, phase_status = _runtime_detail_payload(detail)
        handle = start_turn_span(f"Turn.progress.{kind}", inputs={"sequence": sequence, "kind": kind})
        handle.finish(phase_status=phase_status, outputs=outputs)
    except Exception:
        logger.debug("Runtime Event trace projection failed; continuing", exc_info=True)


_LOCAL_BYOK_USER = "fleet-local"
_SPAN_NAME = "fleet_turn"
try:
    _FLEET_APP_VERSION = package_version("fleet-rlm")
except PackageNotFoundError:
    _FLEET_APP_VERSION = "unknown"
_current_trace_id: ContextVar[str | None] = ContextVar("fleet_mlflow_trace_id", default=None)
_current_trace_failed: ContextVar[bool] = ContextVar("fleet_mlflow_trace_failed", default=False)
# True only while a fleet_turn root span is open. Phase spans gate on this so
# tracing-disabled turns never import or touch MLflow at all.
_fleet_trace_active: ContextVar[bool] = ContextVar("fleet_turn_trace_active", default=False)


@dataclass(frozen=True, slots=True)
class TraceHandle:
    """Public-safe handle for an optional active Turn trace."""

    trace_id: str | None


def _set_current_trace_state(state: str) -> None:
    """Persist a terminal MLflow trace state without affecting the Turn."""
    try:
        import mlflow

        trace_update = getattr(mlflow, "update_current_trace", None)
        if callable(trace_update):
            trace_update(state=state)
    except Exception:
        logger.debug("MLflow trace state update failed; continuing", exc_info=True)


def annotate_trace_io(
    *,
    request: str,
    response_text: str | None = None,
    response_outputs: dict[str, object] | None = None,
    failed: bool = False,
) -> None:
    """
    Annotate the active trace with sanitized request and response data.

    Parameters:
        request: The request content to record.
        response_text: Optional response text to record.
        response_outputs: Optional named response values to record.
        failed: Whether to mark the active trace as failed.
    """
    try:
        import mlflow

        span = mlflow.get_current_active_span()
        if span is None:
            return

        span.set_inputs({"request": _trace_value(request)})

        response: dict[str, object] = {}
        if response_text is not None:
            response["answer"] = _trace_value(response_text)
        if response_outputs is not None:
            for key in ("answer", "final_reasoning"):
                if key in response_outputs:
                    response[key] = _trace_value(response_outputs[key])

        span.set_outputs(response or {"answer": response_text or ""})
        trace_update = getattr(mlflow, "update_current_trace", None)
        if callable(trace_update):
            preview_kwargs: dict[str, object] = {
                "request_preview": _trace_content_preview(request),
            }
            if response_text is not None:
                preview_kwargs["response_preview"] = _trace_content_preview(response_text)
            trace_update(**preview_kwargs)
        if failed:
            _current_trace_failed.set(True)
            _set_current_trace_state("ERROR")
            try:
                span.set_status("ERROR")
            except Exception:
                logger.debug("annotate_trace_io status update failed; continuing", exc_info=True)
    except Exception:
        logger.debug("annotate_trace_io failed; continuing without root span I/O", exc_info=True)


def current_turn_trace_id() -> str | None:
    """Return the active Turn trace id for this context, if any."""
    return _current_trace_id.get()


@dataclass(slots=True)
class TraceSpanHandle:
    """Fail-soft lifecycle handle for a bounded nested MLflow span.

    The handle supports callbacks whose start and end hooks are separate
    invocations. It never exposes raw exception details to MLflow and never
    lets tracing failures affect the owning Turn.
    """

    _span_context: Any | None = None
    _span: Any | None = None
    outputs: dict[str, object] = field(default_factory=dict)
    _closed: bool = False

    def set_outputs(self, outputs: Mapping[str, object]) -> None:
        try:
            self.outputs.update(dict(outputs))
        except Exception:
            logger.debug("trace span output accumulation failed; continuing", exc_info=True)

    def finish(
        self,
        *,
        phase_status: str,
        outputs: Mapping[str, object] | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        """Close the span with bounded outputs and a sanitized status."""
        if self._closed:
            return
        self._closed = True
        if outputs is not None:
            self.set_outputs(outputs)
        if self._span is None or self._span_context is None:
            return
        try:
            self._span.set_outputs({**_trace_mapping(self.outputs), "phase_status": phase_status})
        except Exception:
            logger.debug("trace span output annotation failed; continuing", exc_info=True)
        if attributes:
            try:
                setter = getattr(self._span, "set_attributes", None)
                if callable(setter):
                    setter(_trace_mapping(attributes))
            except Exception:
                logger.debug("trace span attribute annotation failed; continuing", exc_info=True)
        if phase_status != "completed":
            try:
                self._span.set_status("ERROR")
            except Exception:
                logger.debug("trace span status annotation failed; continuing", exc_info=True)
        try:
            # Do not pass provider exceptions to MLflow: their messages can
            # contain prompts, generated code, or gateway response bodies.
            self._span_context.__exit__(None, None, None)
        except BaseException:
            logger.debug("trace span close failed; continuing", exc_info=True)


# Kept as an alias for the existing phase-span test and call-site vocabulary.
PhaseSpanHandle = TraceSpanHandle


def start_turn_span(
    name: str,
    *,
    inputs: Mapping[str, object],
    span_type: str = "CHAIN",
) -> TraceSpanHandle:
    """Start a bounded nested span when a ``fleet_turn`` trace is active.

    MLflow's manual span API is used because DSPy callback start/end hooks are
    separate events and cannot be represented by a single ``with`` body.
    """
    handle = TraceSpanHandle()
    if not _fleet_trace_active.get():
        return handle

    try:
        import mlflow
        from mlflow.entities import SpanType

        active_span = mlflow.get_current_active_span()
        if active_span is None:
            return handle
        span_context = mlflow.start_span(
            name=name,
            span_type=getattr(SpanType, span_type, SpanType.CHAIN),
        )
        span = span_context.__enter__()
    except Exception:
        logger.debug("MLflow lifecycle span setup failed; continuing", exc_info=True)
        return handle

    handle._span_context = span_context
    handle._span = span
    try:
        span.set_inputs(_trace_mapping(inputs))
    except Exception:
        logger.debug("trace span input annotation failed; continuing", exc_info=True)
    return handle


@contextmanager
def turn_phase_span(name: str, *, inputs: Mapping[str, object]) -> Iterator[PhaseSpanHandle]:
    """Record one bounded, nested Turn phase without affecting its outcome.

    The caller supplies bounded operational metadata and sanitized previews
    when step-level debugging needs them. Unbounded prompts, generated
    programs, interpreter output, and sensitive values must never be attached.
    Yields a ``PhaseSpanHandle`` so callers can attach bounded outputs at exit
    time.
    Outside an active ``fleet_turn`` trace this is a no-op that never imports
    MLflow, keeping tracing-disabled turns free of any MLflow footprint.
    """
    handle = start_turn_span(name, inputs=inputs)
    try:
        yield handle
    except BaseException:
        handle.finish(phase_status="failed")
        raise
    else:
        handle.finish(phase_status="completed")


@contextmanager
def turn_trace(
    session_id: UUID,
    run_id: UUID,
    *,
    enabled: bool,
    expose_trace_id: bool = True,
) -> Iterator[TraceHandle]:
    """
    Open a root ``fleet_turn`` span for a live Turn when tracing is enabled.

    Parameters:
        session_id (UUID): Identifier for the session associated with the Turn.
        run_id (UUID): Identifier for the run associated with the Turn.
        enabled (bool): Whether to enable tracing.
        expose_trace_id (bool): Whether to expose the active trace identifier in the yielded handle.

    Yields:
        TraceHandle: Handle containing the trace identifier when available and exposure is enabled;
            otherwise, a no-op handle.
    """
    if not enabled:
        yield TraceHandle(trace_id=None)
        return

    token = _current_trace_id.set(None)
    failed_token = _current_trace_failed.set(False)
    active_token: Token[bool] | None = None
    try:
        try:
            import mlflow
            from mlflow.entities import SpanType
        except Exception:
            logger.warning("MLflow import failed for turn span; continuing without traces", exc_info=True)
            yield TraceHandle(trace_id=None)
            return

        try:
            span_context = mlflow.start_span(
                name=_SPAN_NAME,
                span_type=SpanType.CHAIN,
                log_level="INFO",
            )
            span = span_context.__enter__()
        except Exception:
            logger.warning("MLflow turn span setup failed; continuing without traces", exc_info=True)
            yield TraceHandle(trace_id=None)
            return

        active_token = _fleet_trace_active.set(True)
        try:
            mlflow.update_current_trace(
                session_id=str(session_id),
                user=_LOCAL_BYOK_USER,
                tags={
                    "fleet.run_id": str(run_id),
                    "fleet.session_id": str(session_id),
                },
                metadata={
                    "fleet.run_id": str(run_id),
                    "fleet.app_version": _FLEET_APP_VERSION,
                },
            )
        except Exception:
            logger.warning("MLflow update_current_trace failed; continuing", exc_info=True)
        trace_id: str | None = None
        try:
            raw = mlflow.get_last_active_trace_id() or getattr(span, "request_id", None)
            if raw is not None:
                trace_id = str(raw)
                if expose_trace_id:
                    _current_trace_id.set(trace_id)
        except Exception:
            logger.warning("MLflow get_last_active_trace_id failed; continuing", exc_info=True)

        try:
            yield TraceHandle(trace_id=trace_id if expose_trace_id else None)
        except BaseException as exc:
            _current_trace_failed.set(True)
            _set_current_trace_state("ERROR")
            try:
                span_context.__exit__(type(exc), exc, exc.__traceback__)
            except BaseException:
                logger.warning("MLflow turn span teardown failed; continuing", exc_info=True)
            raise
        else:
            _set_current_trace_state("ERROR" if _current_trace_failed.get() else "OK")
            try:
                span_context.__exit__(None, None, None)
            except BaseException:
                logger.warning("MLflow turn span teardown failed; continuing", exc_info=True)
    finally:
        if active_token is not None:
            _fleet_trace_active.reset(active_token)
        _current_trace_failed.reset(failed_token)
        _current_trace_id.reset(token)
