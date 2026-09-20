"""DSPy 3.3.1 compatibility, version guard, callbacks, and interpreter contracts."""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import TYPE_CHECKING, Any, TypeAlias, cast

if TYPE_CHECKING:
    from fleet_rlm.rlm.recursion import DelegationMetrics

import dspy
from dspy import CodeExecutionError, CodeInterpreter, CodeInterpreterError, FinalOutput
from dspy.clients.base_lm import BaseLM as BaseLM
from dspy.signatures.signature import Signature as Signature
from dspy.utils.callback import BaseCallback
from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.json_types import JsonValue
from fleet_rlm.observability.diagnostics import walk_cause_chain
from fleet_rlm.rlm.result import _safe_usage_entry, truncate_public_text

logger = logging.getLogger(__name__)

ReasoningObserver: TypeAlias = Callable[[Any], None]

CERTIFIED_DSPY_VERSION = "3.3.1"
PUBLIC_FINAL_OUTPUT_LABEL = "FINAL submitted"
_EMPTY_RESPONSE_MARKER = "The LM returned an empty or null response"

DAYTONA_EXECUTION_INSTRUCTIONS = (
    "Execution runs in isolated Python. The Python namespace persists across actions in one invocation. "
    "Host Tools are callable Python functions. "
    "Ordinary stdout is observable. Use the typed keyword `SUBMIT` for final completion."
)


class UncertifiedDSpyVersionError(RuntimeError):
    """Raised when the runtime is not running on the certified DSPy baseline."""


def assert_dspy_version() -> None:
    """Enforce the certified DSPy release; fail fast on any mismatch."""
    version = getattr(dspy, "__version__", None)
    if version != CERTIFIED_DSPY_VERSION:
        truncated = truncate_public_text(str(version or ""), max_len=64)
        raise UncertifiedDSpyVersionError(
            f"Fleet Agent is certified on DSPy {CERTIFIED_DSPY_VERSION}; "
            f"found installed DSPy {truncated!r} (expected exactly DSPy {CERTIFIED_DSPY_VERSION}). "
            f"Run `uv sync` to align dependencies."
        )


def is_native_rlm(value: object) -> bool:
    """Return whether ``value`` is the exact pinned DSPy RLM implementation."""
    return type(value) is dspy.RLM


def copy_output_fields(output_fields: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Return an independent copy of signature output metadata for interpreter state."""
    return deepcopy(output_fields) if output_fields is not None else None


def needs_binding_refresh(*, desired_generation: int, installed_generation: int, broker_ready: bool) -> bool:
    """Whether interpreter bindings should be refreshed for this action."""
    return desired_generation != installed_generation or not broker_ready


def _iteration_parts(inputs: Mapping[str, Any]) -> tuple[int, int] | None:
    """Parse DSPy's native ``generate_action`` iteration marker ``current/total``."""
    value = inputs.get("iteration")
    if not isinstance(value, str):
        return None
    try:
        current, total = (int(part.strip()) for part in value.split("/", 1))
    except (ValueError, TypeError):
        return None
    return (current, total) if current >= 1 and total >= current else None


def _iteration_is_action(inputs: Mapping[str, Any]) -> bool:
    return _iteration_parts(inputs) is not None


def _iteration_is_final(inputs: Mapping[str, Any]) -> bool:
    parts = _iteration_parts(inputs)
    return parts is not None and parts[0] == parts[1]


class _RLMReasoningCallback(BaseCallback):
    """Observe native action lifecycle callbacks without changing predictions."""

    def __init__(
        self,
        observer: ReasoningObserver,
        *,
        max_chars: int = 16000,
        deadline: float | None = None,
    ) -> None:
        self._observer = observer
        self._max_chars = max(1, int(max_chars))
        self._deadline = deadline
        self._iteration = 0
        self._action_spans: dict[str, Any] = {}

    def on_module_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        del instance, inputs
        if self._deadline is not None and time.monotonic() >= self._deadline:
            return
        try:
            from fleet_rlm.observability.tracing import start_turn_span

            self._action_spans[call_id] = start_turn_span(
                "RLM.root_action",
                inputs={"iteration": self._iteration + 1},
            )
        except Exception:
            return

    def on_module_end(self, call_id: str, outputs: Any | None, exception: BaseException | None = None) -> None:
        action_span = self._action_spans.pop(call_id, None)
        if self._deadline is not None and time.monotonic() >= self._deadline:
            if action_span is not None:
                action_span.finish(phase_status="failed", outputs={"action_status": "deadline_exceeded"})
            return
        try:
            if exception is not None:
                if action_span is not None:
                    action_span.finish(
                        phase_status="failed",
                        outputs={
                            "action_status": "failed",
                            "failure_category": _trace_failure_category(exception),
                            **_adapter_parse_profile(exception),
                        },
                    )
                return
            if not isinstance(outputs, dspy.Prediction):
                if action_span is not None:
                    action_span.finish(phase_status="failed", outputs={"action_status": "invalid_output"})
                return
            self._iteration += 1
            reasoning = getattr(outputs, "reasoning", None)
            code = getattr(outputs, "code", "")
            if not isinstance(reasoning, str) or not reasoning.strip():
                if action_span is not None:
                    action_span.finish(phase_status="failed", outputs={"action_status": "missing_reasoning"})
                return

            if action_span is not None:
                action_span.finish(
                    phase_status="completed",
                    outputs={
                        "action_status": "parsed",
                        "reasoning_chars": len(reasoning),
                        "code_chars": len(code) if isinstance(code, str) else 0,
                        "reasoning_preview": _trace_preview(reasoning),
                        "code_preview": _trace_preview(code if isinstance(code, str) else ""),
                    },
                )
            from fleet_rlm.rlm.events import RLMReasoning

            self._observer(RLMReasoning(truncate_public_text(reasoning, max_len=self._max_chars), self._iteration))
        except Exception:
            return


class _RLMTraceCallback(BaseCallback):
    """Trace root/sub DSPy LM calls through the active Turn span."""

    def __init__(
        self,
        *,
        root_lm: Any,
        sub_lm: Any,
        recursive_depth: int = 0,
        metrics: DelegationMetrics | None = None,
        deadline: float | None = None,
    ) -> None:
        self._roles = {id(root_lm): "root", id(sub_lm): "sub"}
        self._recursive_depth = max(0, int(recursive_depth))
        self._metrics = metrics
        self._deadline = deadline
        self._call_index = 0
        self._spans: dict[str, tuple[Any, Any, int | None, int, float]] = {}
        self._last_call: dict[str, JsonValue] | None = None

    def on_lm_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        if self._deadline is not None and time.monotonic() >= self._deadline:
            return
        role = self._roles.get(id(getattr(instance, "_fleet_trace_identity", instance)))
        if role is None:
            return
        self._call_index += 1
        call_index = self._call_index
        model = getattr(instance, "model", "unknown")
        history = getattr(instance, "history", None)
        history_length = len(history) if isinstance(history, Sequence) else None
        span = None
        try:
            from fleet_rlm.observability.tracing import start_turn_span

            span = start_turn_span(
                f"RLM.{role}_lm",
                span_type="LLM",
                inputs={
                    "role": role,
                    "model": str(model),
                    "call_id": call_id,
                    "call_index": call_index,
                    "input_keys": tuple(sorted(str(key) for key in inputs)[:32]),
                    **_lm_input_profile(inputs, include_previews=self._recursive_depth == 0),
                    "history_length_before": history_length,
                    "recursive_depth": self._recursive_depth,
                },
            )
        except Exception:
            pass
        self._spans[call_id] = (instance, span, history_length, call_index, time.perf_counter())

    def on_lm_end(
        self,
        call_id: str,
        outputs: dict[str, Any] | None,
        exception: BaseException | None = None,
    ) -> None:
        state = self._spans.pop(call_id, None)
        if state is None:
            return
        instance, span, history_length, call_index, started_at = state
        role = self._roles.get(id(getattr(instance, "_fleet_trace_identity", instance)), "unknown")
        try:
            usage = _latest_lm_telemetry(instance, history_length, outputs)
        except Exception:
            usage = {}
        standard_usage = _mlflow_token_usage(usage)
        attributes = {"mlflow.chat.tokenUsage": standard_usage} if standard_usage else None
        try:
            response_details = _lm_output_profile(outputs, include_previews=self._recursive_depth == 0)
        except Exception:
            response_details = {}
        reasoning_tokens = _reasoning_token_count(usage)
        if reasoning_tokens is not None:
            response_details["reasoning_tokens"] = reasoning_tokens
        duration_ms = (time.perf_counter() - started_at) * 1000
        response_details.update({"call_index": call_index, "wall_time_ms": round(duration_ms, 3)})

        last_call: dict[str, JsonValue] = {
            "role": role,
            "recursive_depth": self._recursive_depth,
            "call_index": call_index,
            "request_status": "failed" if exception is not None else "completed",
        }
        for key in (
            "response_keys",
            "response_chars",
            "wall_time_ms",
            "has_reasoning_content",
            "reasoning_tokens",
        ):
            if (value := response_details.get(key)) is not None:
                last_call[key] = value

        failure_outputs: dict[str, JsonValue] = {}
        failure_attributes: dict[str, JsonValue] = {}
        if exception is not None:
            failure_outputs, failure_attributes = _lm_failure_details(exception)
            last_call.update(failure_outputs)
            ceiling = _lm_max_tokens(instance)
            observed = usage.get("output_tokens", usage.get("completion_tokens"))
            if (
                isinstance(ceiling, int)
                and isinstance(observed, int)
                and not isinstance(observed, bool)
                and observed >= ceiling
            ):
                last_call["truncated"] = True
        self._last_call = last_call

        if self._metrics is not None:
            self._metrics.record_lm_call(role, self._recursive_depth, duration_ms=duration_ms, usage=usage)
        if span is None:
            return
        if exception is None:
            span.finish(
                phase_status="completed",
                outputs={
                    "request_status": "completed",
                    **response_details,
                    **({"token_usage": usage} if usage else {}),
                },
                attributes=attributes,
            )
        else:
            span.finish(
                phase_status="failed",
                outputs={
                    "request_status": "failed",
                    **failure_outputs,
                    **response_details,
                    **({"token_usage": usage} if usage else {}),
                },
                attributes={**(attributes or {}), **failure_attributes},
            )

    def last_call_summary(self) -> dict[str, JsonValue]:
        return dict(self._last_call) if self._last_call is not None else {}


def _trace_preview(value: object, *, max_chars: int = 900) -> str:
    """Create a bounded, sanitized text preview of a value."""
    from fleet_rlm.observability.tracing import trace_preview_limit
    from fleet_rlm.rlm.result import sanitize_trace_text

    return sanitize_trace_text(str(value or ""), max_len=trace_preview_limit(max_chars))


def _trace_payload_text(value: object) -> str:
    try:
        return json.dumps(value, default=str, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _lm_input_profile(inputs: Mapping[str, Any], *, include_previews: bool = True) -> dict[str, JsonValue]:
    """Summarize the structural characteristics of language-model input context."""
    profile: dict[str, JsonValue] = {}
    prompt = inputs.get("prompt")
    if isinstance(prompt, str):
        profile["prompt_chars"] = len(prompt)
        if include_previews:
            profile["prompt_preview"] = _trace_preview(prompt)
    messages = inputs.get("messages")
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes, bytearray)):
        profile["message_count"] = len(messages)
        profile["message_chars"] = sum(len(str(message)) for message in messages)
        if include_previews:
            profile["messages_preview"] = _trace_preview(_trace_payload_text(messages))
    kwargs = inputs.get("kwargs")
    if isinstance(kwargs, Mapping):
        profile["kwargs_keys"] = tuple(sorted(str(key) for key in kwargs)[:32])
    context_chars = sum(
        value for key in ("prompt_chars", "message_chars") if isinstance(value := profile.get(key), int)
    )
    if context_chars:
        profile["context_chars"] = context_chars
    return profile


def _to_output_mapping(outputs: Any) -> Mapping[str, Any] | None:
    if isinstance(outputs, Mapping):
        return outputs
    if isinstance(outputs, str):
        return {"content": outputs}
    if isinstance(outputs, Sequence) and not isinstance(outputs, (str, bytes, bytearray)):
        merged: dict[str, Any] = {}
        for item in outputs:
            if isinstance(item, str):
                existing = merged.get("content")
                merged["content"] = item if not isinstance(existing, str) else existing + item
            elif isinstance(item, Mapping):
                for key, value in item.items():
                    merged[str(key)] = value
        return merged or None
    model_dump = getattr(outputs, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
            if isinstance(dumped, Mapping):
                return dumped
        except Exception:
            pass
    return None


def _lm_output_profile(outputs: Any, *, include_previews: bool = True) -> dict[str, JsonValue]:
    """Describe an LM response for tracing."""
    mapping = _to_output_mapping(outputs)
    if mapping is None:
        return {"response_keys": ()}
    profile: dict[str, JsonValue] = {"response_keys": tuple(sorted(str(key) for key in mapping)[:32])}
    response_chars = sum(len(str(value)) for value in mapping.values() if isinstance(value, str))
    if response_chars:
        profile["response_chars"] = response_chars
    reasoning = mapping.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        profile["has_reasoning_content"] = True
    if mapping and include_previews:
        profile["response_preview"] = _trace_preview(_trace_payload_text(mapping))
    return profile


def _mapping_from_usage_value(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value) if value else None
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            dumped = dump()
            if isinstance(dumped, Mapping) and dumped:
                return dict(dumped)
        except Exception:
            pass
    raw = getattr(value, "__dict__", None)
    return dict(raw) if isinstance(raw, dict) and raw else None


def _usage_from_history_entry(entry: Mapping[str, Any]) -> Mapping[str, Any] | None:
    usage = _mapping_from_usage_value(entry.get("usage"))
    if usage:
        return usage
    response = entry.get("response")
    if response is None:
        return None
    nested = getattr(response, "usage", None)
    if nested is None and isinstance(response, Mapping):
        nested = response.get("usage")
    return _mapping_from_usage_value(nested)


def _reasoning_token_count(usage: Mapping[str, Any]) -> int | None:
    value = usage.get("reasoning_tokens")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    details = usage.get("completion_tokens_details")
    if isinstance(details, Mapping):
        nested = details.get("reasoning_tokens")
        if isinstance(nested, int) and not isinstance(nested, bool) and nested >= 0:
            return nested
    return None


def _is_empty_adapter_parse(exc: BaseException) -> bool:
    return _EMPTY_RESPONSE_MARKER in str(getattr(exc, "message", "") or exc)


def _adapter_parse_profile(exc: BaseException) -> dict[str, JsonValue]:
    parse_error = next((item for item in walk_cause_chain(exc) if isinstance(item, AdapterParseError)), None)
    if parse_error is None:
        return {}
    text = str(getattr(parse_error, "lm_response", "") or "")
    profile: dict[str, JsonValue] = {
        "parse_failure_kind": "empty" if _is_empty_adapter_parse(parse_error) else "non_object_json",
        "lm_response_chars": len(text),
    }
    if "reasoning_content" in text:
        profile["has_reasoning_content"] = True
    return profile


def _lm_max_tokens(instance: Any) -> int | None:
    kwargs = getattr(instance, "kwargs", None)
    if not isinstance(kwargs, Mapping):
        return None
    value = kwargs.get("max_tokens")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _latest_lm_telemetry(instance: Any, history_length: int | None, outputs: object = None) -> dict[str, JsonValue]:
    history = getattr(instance, "history", None)
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes, bytearray)):
        return {}
    start = history_length if history_length is not None else max(0, len(history) - 1)
    candidates = [entry for entry in history[start:] if isinstance(entry, Mapping)]
    matching = [entry for entry in candidates if outputs is not None and entry.get("outputs") is outputs]
    selected = matching or (candidates if len(candidates) == 1 else [])
    for entry in reversed(selected):
        usage = _usage_from_history_entry(entry)
        if not isinstance(usage, Mapping) or not usage:
            continue
        with contextlib.suppress(ValueError):
            observed = {k: v for k, v in usage.items() if v is not None}
            sanitized = _safe_usage_entry(observed, path="lm_usage", filter_unknown=True)
            if sanitized:
                return cast(dict[str, JsonValue], sanitized)
    return {}


def _history_entry_matches_outputs(entry: Mapping[str, Any], outputs: object) -> bool:
    return outputs is not None and entry.get("outputs") is outputs


def _mlflow_token_usage(usage: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    from fleet_rlm.rlm.recursion import normalize_lm_token_usage

    return cast(dict[str, JsonValue], normalize_lm_token_usage(usage))


def _trace_failure_category(exc: BaseException) -> str:
    from fleet_rlm.observability.diagnostics import trace_failure_category

    return trace_failure_category(exc)


_TRACE_FAILURE_DETAIL_MAX_CHARS = 300


def _lm_failure_details(exception: BaseException) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    from fleet_rlm.daytona.errors import (
        classify_provider_error,
        provider_status_code,
        sanitize_provider_message,
    )

    cleaned = sanitize_provider_message(str(exception))
    detail = cleaned[:_TRACE_FAILURE_DETAIL_MAX_CHARS]
    status = provider_status_code(exception)
    status_category = f"{status // 100}xx" if isinstance(status, int) and 100 <= status <= 599 else "none"
    category = classify_provider_error(exception)
    kind = type(exception).__name__

    failure_outputs: dict[str, JsonValue] = {
        "failure_category": category,
        "error_kind": kind,
        "provider_status_category": status_category,
    }
    span_failure_attributes: dict[str, JsonValue] = {
        "fleet.error.kind": kind,
        "fleet.error.category": category,
        "fleet.error.status": status_category,
    }
    if detail:
        failure_outputs["detail"] = detail
        span_failure_attributes["fleet.error.detail"] = detail
    return failure_outputs, span_failure_attributes


def bind_native_rlm_observer(
    rlm: Any,
    observer: ReasoningObserver | None,
    *,
    max_chars: int = 10_000,
    deadline: float | None = None,
) -> None:
    from fleet_rlm.rlm.result import RLMConfigError

    if not is_native_rlm(rlm):
        raise RLMConfigError("reasoning observation requires native dspy.RLM")
    predictor = getattr(rlm, "generate_action", None)
    if not isinstance(predictor, dspy.Predict):
        return
    callbacks = getattr(predictor, "callbacks", None)
    predictor.callbacks = (
        [cb for cb in callbacks if not isinstance(cb, _RLMReasoningCallback)] if isinstance(callbacks, list) else []
    )
    if observer is not None:
        predictor.callbacks.append(_RLMReasoningCallback(observer, max_chars=max_chars, deadline=deadline))


def daytona_provider_contract() -> Any:
    from fleet_rlm.rlm.result import RLMConfigError

    raise RLMConfigError("native RLM execution requires a caller-owned interpreter")


cast(Any, daytona_provider_contract).execution_instructions = DAYTONA_EXECUTION_INSTRUCTIONS


def wrap_final_output(value: Any) -> FinalOutput:
    return FinalOutput(value)


def is_final_output(value: Any) -> bool:
    return isinstance(value, FinalOutput)


__all__ = [
    "CERTIFIED_DSPY_VERSION",
    "DAYTONA_EXECUTION_INSTRUCTIONS",
    "PUBLIC_FINAL_OUTPUT_LABEL",
    "CodeExecutionError",
    "CodeInterpreter",
    "CodeInterpreterError",
    "FinalOutput",
    "ReasoningObserver",
    "UncertifiedDSpyVersionError",
    "assert_dspy_version",
    "bind_native_rlm_observer",
    "copy_output_fields",
    "daytona_provider_contract",
    "is_final_output",
    "is_native_rlm",
    "needs_binding_refresh",
    "wrap_final_output",
]
