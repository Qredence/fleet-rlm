"""Fleet's capture policy for MLflow runtime traces."""

import json
import logging
import re
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields, is_dataclass
from typing import Any

import dspy
import mlflow
from dspy.utils.exceptions import LMProviderError
from dspy.utils.usage_tracker import UsageTracker
from mlflow.dspy.callback import MlflowCallback
from mlflow.entities import LiveSpan, SpanType
from mlflow.tracing.constant import SpanAttributeKey
from mlflow.tracing.provider import detach_span_from_context
from pydantic import BaseModel, SecretStr

from .config import Settings
from .opt_datasets import database_url_secrets, scrub_secrets

_PAYLOAD_BYTES = 16 * 1024
_PREVIEW_BYTES = 768
_MAX_DEPTH = 20
_OMITTED = "[omitted]"
_SAFE_PROVIDER_CODES = {
    "content_filter",
    "content_policy_violation",
    "context_length_exceeded",
    "invalid_prompt",
    "invalid_request_error",
    "model_not_found",
    "rate_limit_exceeded",
    "server_error",
    "unsupported_parameter",
    "unsupported_value",
}
_SENSITIVE_KEY = re.compile(
    r"(?:api.?key|.*(?:access|refresh|auth|session|identity|id|bearer).?token|token|"
    r".*password|.*secret|.*credential.*|authorization|proxy.authorization|"
    r"cookie|set.cookie|headers|auth|api.base|base.url|private.key)",
    re.IGNORECASE,
)
_SIGNED_QUERY = re.compile(
    r"(https?://[^?\s\"'<>#]+)\?[^\s\"'<>#]*"
    r"(?:[?&]|(?<=\?))(?:x-amz-[\w-]+|x-goog-[\w-]+|sig|signature)="
    r"[^\s\"'<>#]*",
    re.IGNORECASE,
)
_DATA_URI = re.compile(r"data:[^,\s]{0,128},[^\s\"'<>]+", re.IGNORECASE)
_BINARY_KEYS = {
    "audio",
    "b64_json",
    "base64",
    "bytes",
    "file_data",
    "image_url",
    "inline_data",
    "input_audio",
    "media",
    "raw_payload",
}
_BINARY_TYPES = {
    "audio",
    "document",
    "file",
    "image",
    "image_url",
    "input_audio",
    "input_file",
    "input_image",
    "image_generation_call",
    "video",
}
_SAFE_ATTRIBUTES = {
    "model",
    "model_type",
    "cache",
    "temperature",
    "max_tokens",
    "name",
    SpanAttributeKey.MODEL,
    SpanAttributeKey.MODEL_PROVIDER,
    SpanAttributeKey.MESSAGE_FORMAT,
}
_CONTEXT: ContextVar[tuple[bool, tuple[str, ...]]] = ContextVar(
    "fleet_trace_capture", default=(False, ())
)


@contextmanager
def capture(settings: Settings) -> Generator[None]:
    """Scope capture to one Fleet trace and propagate it to child workers."""
    secrets = database_url_secrets(settings)
    secrets.extend(
        value.get_secret_value()
        for key in Settings.model_fields
        if isinstance(value := getattr(settings, key), SecretStr) and value.get_secret_value()
    )
    token = _CONTEXT.set((not settings.mlflow_full_trace_capture, tuple(secrets)))
    try:
        if settings.mlflow_full_trace_capture:
            yield
        else:
            callbacks = [c for c in dspy.settings.callbacks if not isinstance(c, MlflowCallback)]
            with telemetry_operation("create DSPy callback"):
                callbacks.append(FleetMlflowCallback())
            with dspy.context(callbacks=callbacks):
                yield
    finally:
        _CONTEXT.reset(token)


def bounded_capture() -> bool:
    return _CONTEXT.get()[0]


def _plain(value: Any, depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        return _OMITTED
    if isinstance(value, str):
        return _SIGNED_QUERY.sub(r"\1?[REDACTED]", _DATA_URI.sub(_OMITTED, value))
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, BaseModel):
        return _plain(value.model_dump(), depth + 1)
    if is_dataclass(value) and not isinstance(value, type):
        return _plain({field.name: getattr(value, field.name) for field in fields(value)}, depth)
    if isinstance(value, Mapping):
        if isinstance(kind := value.get("type"), str) and kind.casefold() in _BINARY_TYPES:
            return {"type": kind, "content": _OMITTED}
        return {
            key: _plain(item, depth + 1)
            for key, item in value.items()
            if isinstance(key, str)
            and not _SENSITIVE_KEY.fullmatch(key)
            and key.casefold() not in _BINARY_KEYS
            and not key.lower().startswith(("x-amz-", "x-goog-"))
        }
    if isinstance(value, (tuple, list)):
        return [_plain(item, depth + 1) for item in value]
    return _OMITTED


def _clip(value: str, limit: int) -> str:
    data = value.encode("utf-8", errors="replace")
    if len(data) <= limit:
        return data.decode()
    return data[: limit - 3].decode("utf-8", errors="ignore") + "…"


def preview(text: str) -> str:
    return _clip(scrub_secrets(_plain(text), _CONTEXT.get()[1]), _PREVIEW_BYTES)


def payload(value: Any, extra_secrets: Sequence[str] = ()) -> Any:
    """Keep redacted, JSON-compatible content within the serialized byte limit."""
    try:
        cleaned = scrub_secrets(_plain(value), (*_CONTEXT.get()[1], *extra_secrets))
        encoded = json.dumps(cleaned, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8", errors="replace")) <= _PAYLOAD_BYTES:
            return cleaned
        # The serialized excerpt is escaped again when it becomes a JSON string.
        excerpt = _clip(encoded, _PAYLOAD_BYTES - 100)
        while len(json.dumps(excerpt, ensure_ascii=False).encode()) > _PAYLOAD_BYTES - 64:
            excerpt = _clip(excerpt, len(excerpt.encode()) * 3 // 4)
        return {"excerpt": excerpt, "truncated": True}
    except Exception:
        # A malformed observability payload must never affect execution or export raw data.
        return {"content": _OMITTED}


_ENABLED = ContextVar("fleet_tracing_enabled", default=True)


@contextmanager
def trace_context(
    *, enabled: bool | None = None, tags: dict[str, str] | None = None
) -> Generator[None]:
    stack = ExitStack()
    with telemetry_operation("enter trace context"):
        stack.enter_context(mlflow.tracing.context(enabled=enabled, tags=tags))
    try:
        yield
    finally:
        with telemetry_operation("exit trace context"):
            stack.close()


@contextmanager
def without_tracing() -> Generator[None]:
    token = _ENABLED.set(False)
    try:
        callbacks = [c for c in dspy.settings.callbacks if not isinstance(c, MlflowCallback)]
        with trace_context(enabled=False), dspy.context(callbacks=callbacks):
            yield
    finally:
        _ENABLED.reset(token)


@contextmanager
def telemetry_operation(name: str) -> Generator[None]:
    """Guard a single SDK operation. Application work belongs outside this scope."""
    try:
        yield
    except Exception as error:
        logging.getLogger(__name__).warning("MLflow %s failed (%s)", name, type(error).__name__)


def record[**P](operation: Callable[P, Any], *args: P.args, **kwargs: P.kwargs) -> None:
    with telemetry_operation(getattr(operation, "__name__", "record")):
        operation(*args, **kwargs)


def active_span() -> LiveSpan | None:
    if _ENABLED.get():
        with telemetry_operation("get active span"):
            return mlflow.get_current_active_span()
    return None


@contextmanager
def span(
    name: str,
    *,
    root: bool = False,
    span_type: str = SpanType.CHAIN,
    attributes: dict[str, Any] | None = None,
) -> Generator[LiveSpan | None]:
    if not _ENABLED.get() or (not root and active_span() is None):
        yield None
        return
    stack = ExitStack()
    active = None
    with telemetry_operation("start span"):
        active = stack.enter_context(
            mlflow.start_span(name, span_type=span_type, attributes=attributes)
        )
    try:
        yield active if isinstance(active, LiveSpan) else None
    except BaseException as error:
        if active is not None:
            with telemetry_operation("record span error"):
                active.set_status("ERROR")
                active.set_attribute("error.type", type(error).__name__)
        raise
    finally:
        with telemetry_operation("end span"):
            stack.close()


def _reported_usage(response: Any) -> dict[str, int]:
    usage = (
        response.get("usage") if isinstance(response, Mapping) else getattr(response, "usage", None)
    )
    fields = {
        "input_tokens": "prompt_tokens",
        "output_tokens": "completion_tokens",
        "total_tokens": "total_tokens",
        "cache_read_input_tokens": "cache_read_tokens",
        "cache_creation_input_tokens": "cache_write_tokens",
    }
    totals = {}
    for name, alias in fields.items():
        value = (
            usage.get(name, usage.get(alias))
            if isinstance(usage, Mapping)
            else getattr(usage, name, getattr(usage, alias, None))
        )
        if type(value) is int and value >= 0:
            totals[name] = value
    if "total_tokens" not in totals and {"input_tokens", "output_tokens"} <= totals.keys():
        totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    return totals


def _option_secrets(options: Any, depth: int = 0) -> Generator[str]:
    if depth >= _MAX_DEPTH or not isinstance(options, Mapping):
        return
    for key, value in options.items():
        if not isinstance(key, str):
            continue
        if _SENSITIVE_KEY.fullmatch(key) and isinstance(value, str) and value:
            yield value
            scheme, _, credential = value.partition(" ")
            if scheme.casefold() == "bearer" and credential:
                yield credential
        elif isinstance(value, Mapping):
            yield from _option_secrets(value, depth + 1)


class _ModelUsage(UsageTracker):
    def __init__(self, parent: UsageTracker | None):
        super().__init__()
        self.parent = parent
        self.provider_called = False

    def add_usage(self, lm: str, usage_entry: dict[str, Any]) -> None:
        self.provider_called = True
        super().add_usage(lm, usage_entry)
        if self.parent is not None:
            self.parent.add_usage(lm, usage_entry)


@dataclass
class _ModelCall:
    model: str
    secrets: tuple[str, ...]
    usage: _ModelUsage
    context: AbstractContextManager
    uses_dspy_cache: bool


def _record_provider_error(span: LiveSpan, error: BaseException | None) -> None:
    if not isinstance(error, LMProviderError):
        return
    if isinstance(error.status, int) and 100 <= error.status <= 599:
        span.set_attribute("fleet.model.http_status", error.status)
    if error.provider_code in _SAFE_PROVIDER_CODES:
        span.set_attribute("fleet.model.error_code", error.provider_code)


class FleetMlflowCallback(MlflowCallback):
    """Reuse the MLflow 3.18 DSPy hierarchy and sanitize values before capture."""

    def __init__(self):
        super().__init__()
        self._model_calls: dict[str, _ModelCall] = {}

    def on_lm_start(self, call_id: str, instance: Any, inputs: dict[str, Any]):
        extras = (*_option_secrets(instance.kwargs), *_option_secrets(inputs.get("options")))
        usage = _ModelUsage(dspy.settings.usage_tracker)
        context = dspy.context(usage_tracker=usage)
        cache = inputs.get("cache")
        call = _ModelCall(
            instance.model,
            extras,
            usage,
            context,
            type(instance) is dspy.LM and (instance.cache if cache is None else cache),
        )
        self._model_calls[call_id] = call
        super().on_lm_start(call_id, instance, inputs)
        if call_id in self._call_id_to_span:
            context.__enter__()
        else:
            self._model_calls.pop(call_id, None)

    def _start_span(
        self,
        call_id: str,
        name: str,
        span_type: SpanType,
        inputs: dict[str, Any],
        attributes: dict[str, Any],
    ):
        model = self._model_calls.get(call_id)
        extras = model.secrets if model is not None else ()
        safe_attributes = payload(
            {key: value for key, value in attributes.items() if key in _SAFE_ATTRIBUTES}, extras
        )
        if model:
            inputs = {"prompt": inputs.get("prompt")}
        elif span_type == SpanType.LLM:
            # Predict wraps the model call. Keep model facets and usage on the leaf.
            span_type = SpanType.CHAIN
        detailed = model or span_type in {SpanType.TOOL, SpanType.RETRIEVER}
        return super()._start_span(
            call_id,
            name,
            span_type,
            payload(inputs, extras) if detailed else {},
            safe_attributes,
        )

    def _end_span(
        self,
        call_id: str,
        outputs: Any | None,
        exception: BaseException | None = None,
        attributes: dict[str, Any] | None = None,
    ):
        model = self._model_calls.pop(call_id, None)
        extras = model.secrets if model is not None else ()
        state = self._call_id_to_span.pop(call_id, None)
        if state is None or state.span is None:
            return
        active = state.span
        try:
            if exception is not None:
                active.set_attribute("error.type", type(exception).__name__)
            detailed = model or active.span_type in {SpanType.TOOL, SpanType.RETRIEVER}
            if model:
                _record_provider_error(active, exception)
                response = outputs if outputs is not None else getattr(exception, "partial", None)
                recorded = model.usage.get_total_tokens().get(model.model)
                usage = (
                    _reported_usage({"usage": recorded}) if recorded else _reported_usage(response)
                )
                cached = getattr(response, "cache_hit", False) is True or (
                    model.uses_dspy_cache and exception is None and not model.usage.provider_called
                )
                if cached:
                    active.set_attribute("fleet.model.cache_hit", True)
                if usage and not cached:
                    active.set_attribute(SpanAttributeKey.CHAT_USAGE, usage)
                active.set_attribute("fleet.model.usage_reported", bool(usage) and not cached)
                if not isinstance(response, (str, tuple, list)) and response is not None:
                    fields = ("message", "finish_reason", "model")
                    outputs = {
                        key: response.get(key)
                        if isinstance(response, Mapping)
                        else getattr(response, key, None)
                        for key in fields
                    }
            active.end(
                outputs=payload(outputs, extras) if detailed and outputs is not None else None,
                status="OK" if exception is None else "ERROR",
            )
        finally:
            if model is not None:
                model.context.__exit__(None, None, None)
            if state.token is not None:
                detach_span_from_context(state.token)
