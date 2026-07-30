"""Exact public DSPy 3.3.0b1 RLM construction and observation contract."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Any, TypeAlias, TypedDict, cast

import dspy
from dspy.utils.callback import BaseCallback
from pydantic import TypeAdapter
from pydantic_core import PydanticSerializationError

from fleet_rlm.rlm.errors import RLMConfigError
from fleet_rlm.rlm.sanitize import truncate_public_text, validate_declared_public_value

DSPY_VERSION = "3.3.0b1"

JsonValue: TypeAlias = bool | int | float | str | tuple["JsonValue", ...] | Mapping[str, "JsonValue"] | None
ObservedUsageValue: TypeAlias = bool | int | float | str | dict[str, JsonValue] | None
ReasoningObserver: TypeAlias = Callable[[Any], None]


class RLMUsage(TypedDict):
    """Closed public and durable usage observed for one RLM Turn."""

    iterations: int
    observed_lm_usage: dict[str, dict[str, JsonValue]]
    duration_ms: int


class PredictionOutputError(ValueError):
    """Typed, sanitized failure for an invalid native Prediction output."""

    public_message = "Turn output is invalid"
    status = "failed"

    def __init__(self) -> None:
        super().__init__(self.public_message)


class PredictionOutputTooLargeError(PredictionOutputError):
    """Declared Prediction JSON exceeds the Turn commit character budget."""

    public_message = "Turn output is too large"


@dataclass(frozen=True, slots=True)
class TrajectoryStep:
    """One strictly normalized native DSPy REPL interaction."""

    index: int
    reasoning: str
    code: str
    output: str


def normalize_prediction_trajectory(prediction: Any) -> tuple[TrajectoryStep, ...]:
    """Validate the native trajectory without mutating its ``Prediction`` owner.

    DSPy owns the trajectory lifecycle. Fleet only accepts its documented list of
    iteration mappings and turns absent public fields into empty strings for the
    internal reconciliation seam.
    """
    trajectory = getattr(prediction, "trajectory", None)
    if not isinstance(trajectory, Sequence) or isinstance(trajectory, (str, bytes, bytearray)):
        raise PredictionOutputError

    steps: list[TrajectoryStep] = []
    for index, raw in enumerate(trajectory, start=1):
        if not isinstance(raw, Mapping) or any(not isinstance(key, str) for key in raw):
            raise PredictionOutputError
        values: dict[str, str] = {}
        for field in ("reasoning", "code", "output"):
            value = raw.get(field, "")
            if not isinstance(value, str):
                raise PredictionOutputError
            values[field] = value
        steps.append(TrajectoryStep(index, values["reasoning"], values["code"], values["output"]))
    return tuple(steps)


@dataclass(frozen=True, slots=True)
class PredictionResult:
    """Validated declared Signature outputs selected for Turn Commit."""

    display_text: str
    outputs: Mapping[str, JsonValue]
    schema_id: str
    schema_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.display_text, str) or not self.display_text.strip():
            raise PredictionOutputError
        encoded = _strict_json(self.outputs)
        if not isinstance(encoded, Mapping):
            raise PredictionOutputError
        object.__setattr__(self, "outputs", encoded)
        if not self.schema_id or not self.schema_version:
            raise PredictionOutputError


def _strict_json(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise PredictionOutputError
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise PredictionOutputError
        encoded: dict[str, JsonValue] = {cast(str, key): _strict_json(item) for key, item in value.items()}
        return MappingProxyType(encoded)
    if isinstance(value, (list, tuple)):
        return tuple(_strict_json(item) for item in value)
    raise PredictionOutputError


def _plain_json(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, JsonValue], value)
        return {key: _plain_json(item) for key, item in mapping.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


def prediction_result(
    prediction: Any,
    signature: type[dspy.Signature],
    *,
    schema_id: str = "fleet.default",
    schema_version: str = "1",
    max_output_chars: int = 10_000,
) -> PredictionResult:
    """Encode all declared outputs through their annotations, then strict JSON."""
    outputs: dict[str, JsonValue] = {}
    try:
        for name, field in signature.output_fields.items():
            raw = getattr(prediction, name)
            adapter = TypeAdapter(field.rebuild_annotation())
            validated = adapter.validate_python(raw)
            encoded = adapter.dump_python(validated, mode="json")
            outputs[name] = _strict_json(encoded)
    except (AttributeError, TypeError, ValueError, PydanticSerializationError):
        raise PredictionOutputError from None
    display = outputs.get("answer")
    if not isinstance(display, str) or not display.strip():
        raise PredictionOutputError
    result = PredictionResult(display, outputs, schema_id, schema_version)
    plain_outputs = _plain_json(result.outputs)
    encoded = json.dumps(plain_outputs, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(encoded) > max_output_chars:
        raise PredictionOutputTooLargeError
    try:
        validate_declared_public_value(result.outputs)
    except ValueError:
        raise PredictionOutputError from None
    return result


def empty_rlm_usage() -> RLMUsage:
    """Return a canonical empty observation for outcomes without a Prediction."""
    return RLMUsage(iterations=0, observed_lm_usage={}, duration_ms=0)


def _nonnegative_integer(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


_SAFE_USAGE_KEYS = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "cost",
        "input_cost",
        "output_cost",
        "cached",
        "prompt_tokens_details",
        "completion_tokens_details",
        "input_tokens_details",
        "output_tokens_details",
    }
)
_SAFE_USAGE_DETAIL_KEYS = frozenset(
    {
        "audio_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "accepted_prediction_tokens",
        "rejected_prediction_tokens",
        "text_tokens",
        "image_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
)


def _observed_scalar(value: object, *, path: str) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"{path} must contain finite JSON numbers")
        return value
    raise ValueError(f"{path} must contain a scalar usage value")


def _safe_usage_entry(value: object, *, path: str, filter_unknown: bool) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{path} must be an object with string keys")
    usage = cast(Mapping[str, object], value)
    result: dict[str, JsonValue] = {}
    for key, item in usage.items():
        if key not in _SAFE_USAGE_KEYS:
            if filter_unknown:
                continue
            raise ValueError(f"{path}.{key} is not safe observed usage telemetry")
        if key.endswith("_details"):
            if not isinstance(item, Mapping) or any(not isinstance(detail, str) for detail in item):
                raise ValueError(f"{path}.{key} must be an object")
            detail_usage = cast(Mapping[str, object], item)
            details: dict[str, JsonValue] = {}
            for detail, detail_value in detail_usage.items():
                if detail not in _SAFE_USAGE_DETAIL_KEYS:
                    if filter_unknown:
                        continue
                    raise ValueError(f"{path}.{key}.{detail} is not safe observed usage telemetry")
                details[detail] = _observed_scalar(detail_value, path=f"{path}.{key}.{detail}")
            result[key] = details
        else:
            result[key] = _observed_scalar(item, path=f"{path}.{key}")
    return result


def _safe_observed_usage(value: object, *, filter_unknown: bool) -> dict[str, dict[str, JsonValue]]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError("observed_lm_usage must be an object with string keys")
    usage = cast(Mapping[str, object], value)
    result: dict[str, dict[str, JsonValue]] = {}
    for key, item in usage.items():
        entry = _safe_usage_entry(item, path=f"observed_lm_usage.{key}", filter_unknown=filter_unknown)
        if entry or not filter_unknown:
            result[key] = entry
    return result


def validate_rlm_usage(value: Mapping[str, object]) -> RLMUsage:
    """Validate and normalize the exact public/durable RLM usage shape."""
    expected = {"iterations", "observed_lm_usage", "duration_ms"}
    if set(value) != expected:
        raise ValueError("usage must contain exactly iterations, observed_lm_usage, and duration_ms")
    observed = value["observed_lm_usage"]
    if not isinstance(observed, Mapping):
        raise ValueError("observed_lm_usage must be a JSON object")
    normalized = _safe_observed_usage(observed, filter_unknown=False)
    return RLMUsage(
        iterations=_nonnegative_integer(value["iterations"], field="iterations"),
        observed_lm_usage=normalized,
        duration_ms=_nonnegative_integer(value["duration_ms"], field="duration_ms"),
    )


def assert_dspy_version() -> None:
    """Fail composition before resources start when DSPy is not the pinned contract."""
    if dspy.__version__ != DSPY_VERSION:
        raise RuntimeError(f"DSPy {DSPY_VERSION} is required; installed {dspy.__version__}")


@dataclass(frozen=True, slots=True)
class RLMOptions:
    """The three execution limits owned by native ``dspy.RLM``."""

    max_iterations: int = 20
    max_llm_calls: int = 50
    max_output_chars: int = 10_000

    def __post_init__(self) -> None:
        for name, value in (
            ("max_iterations", self.max_iterations),
            ("max_llm_calls", self.max_llm_calls),
            ("max_output_chars", self.max_output_chars),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise RLMConfigError(f"{name} must be a positive integer, got {value!r}")


class _RLMReasoningCallback(BaseCallback):
    """Observe native action lifecycle callbacks without changing predictions.

    DSPy exposes module start/end callback hooks for this lifecycle
    (``dspy/utils/callback.py:65-95``).
    """

    def __init__(self, observer: ReasoningObserver, *, max_chars: int) -> None:
        self._observer = observer
        self._max_chars = max(1, int(max_chars))
        self._iteration = 0
        self._action_spans: dict[str, Any] = {}

    def on_module_start(
        self,
        call_id: str,
        instance: Any,
        inputs: dict[str, Any],
    ) -> None:
        del instance, inputs
        try:
            from fleet_rlm.observability.turn_tracing import start_turn_span

            self._action_spans[call_id] = start_turn_span(
                "RLM.root_action",
                inputs={"iteration": self._iteration + 1},
            )
        except Exception:
            return

    def on_module_end(
        self,
        call_id: str,
        outputs: Any | None,
        exception: Exception | None = None,
    ) -> None:
        action_span = self._action_spans.pop(call_id, None)
        try:
            if exception is not None:
                if action_span is not None:
                    action_span.finish(
                        phase_status="failed",
                        outputs={
                            "action_status": "failed",
                            "failure_category": _trace_failure_category(exception),
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
                    action_span.finish(
                        phase_status="failed",
                        outputs={"action_status": "missing_reasoning"},
                    )
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
            # Circular-import boundary: events imports usage validators from this module.
            from fleet_rlm.rlm.events import RLMReasoning

            self._observer(
                RLMReasoning(
                    truncate_public_text(reasoning, max_len=self._max_chars),
                    self._iteration,
                )
            )
        except Exception:
            return


class _RLMTraceCallback(BaseCallback):
    """Trace root/sub DSPy LM calls through the active Turn span.

    DSPy invokes the public ``on_lm_start``/``on_lm_end`` callback hooks around
    each LM request (``dspy/utils/callback.py:97-123``), and per-context
    callbacks are honored by its settings context (``dspy/dsp/utils/settings.py:216-235``).
    """

    def __init__(self, *, root_lm: Any, sub_lm: Any, recursive_depth: int = 0) -> None:
        self._roles = {id(root_lm): "root", id(sub_lm): "sub"}
        self._recursive_depth = max(0, int(recursive_depth))
        self._call_index = 0
        self._spans: dict[str, tuple[Any, Any, int | None, int]] = {}

    def on_lm_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        role = self._roles.get(id(instance))
        if role is None:
            return
        try:
            from fleet_rlm.observability.turn_tracing import start_turn_span

            model = getattr(instance, "model", "unknown")
            history = getattr(instance, "history", None)
            history_length = len(history) if isinstance(history, Sequence) else None
            self._call_index += 1
            call_index = self._call_index
            span = start_turn_span(
                f"RLM.{role}_lm",
                span_type="LLM",
                inputs={
                    "role": role,
                    "model": str(model),
                    "call_id": call_id,
                    "call_index": call_index,
                    "input_keys": sorted(str(key) for key in inputs)[:32],
                    **_lm_input_profile(inputs),
                    "history_length_before": history_length,
                    "recursive_depth": self._recursive_depth,
                },
            )
            self._spans[call_id] = (instance, span, history_length, call_index)
        except Exception:
            return

    def on_lm_end(
        self,
        call_id: str,
        outputs: dict[str, Any] | None,
        exception: Exception | None = None,
    ) -> None:
        state = self._spans.pop(call_id, None)
        if state is None:
            return
        instance, span, history_length, call_index = state
        usage = _latest_lm_usage(instance, history_length)
        attributes = (
            {"mlflow.chat.tokenUsage": json.dumps(usage, separators=(",", ":"), sort_keys=True)} if usage else None
        )
        response_details = _lm_output_profile(outputs)
        response_details["call_index"] = call_index
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
                    "failure_category": _trace_failure_category(exception),
                    **response_details,
                    **({"token_usage": usage} if usage else {}),
                },
                attributes=attributes,
            )


def _trace_preview(value: object, *, max_chars: int = 900) -> str:
    """Return bounded, redacted model text for an engineering trace preview."""
    from fleet_rlm.rlm.sanitize import sanitize_public_text

    return sanitize_public_text(str(value or ""), max_len=max_chars)


def _lm_input_profile(inputs: Mapping[str, Any]) -> dict[str, JsonValue]:
    """Describe LM context size without retaining the prompt or message body."""
    profile: dict[str, JsonValue] = {}
    prompt = inputs.get("prompt")
    if isinstance(prompt, str):
        profile["prompt_chars"] = len(prompt)
    messages = inputs.get("messages")
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes, bytearray)):
        profile["message_count"] = len(messages)
        profile["message_chars"] = sum(len(str(message)) for message in messages)
    kwargs = inputs.get("kwargs")
    if isinstance(kwargs, Mapping):
        profile["kwargs_keys"] = tuple(sorted(str(key) for key in kwargs)[:32])
    context_chars = sum(
        value for key in ("prompt_chars", "message_chars") if isinstance(value := profile.get(key), int)
    )
    if context_chars:
        profile["context_chars"] = context_chars
    return profile


def _lm_output_profile(outputs: Mapping[str, Any] | None) -> dict[str, JsonValue]:
    """Describe an LM response without storing the provider response body."""
    if not isinstance(outputs, Mapping):
        return {"response_keys": ()}
    profile: dict[str, JsonValue] = {"response_keys": tuple(sorted(str(key) for key in outputs)[:32])}
    response_chars = sum(len(str(value)) for value in outputs.values() if isinstance(value, str))
    if response_chars:
        profile["response_chars"] = response_chars
    return profile


def _latest_lm_usage(instance: Any, history_length: int | None) -> dict[str, JsonValue]:
    """Read one completed LM call's provider usage from DSPy's public history.

    DSPy exposes the completed LM interaction on ``BaseLM.history`` after the
    decorated ``__call__``/``acall`` returns (``dspy/clients/base_lm.py:225-256``).
    ``Prediction.get_lm_usage`` is an aggregate surface, so the callback uses
    the newly appended history entry to keep this span call-specific.
    """
    history = getattr(instance, "history", None)
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes, bytearray)):
        return {}
    start = history_length if history_length is not None else max(0, len(history) - 1)
    for entry in reversed(history[start:]):
        if not isinstance(entry, Mapping):
            continue
        usage = entry.get("usage")
        if not isinstance(usage, Mapping):
            dump = getattr(usage, "model_dump", None)
            usage = dump() if callable(dump) else None
        if not isinstance(usage, Mapping):
            continue
        with contextlib.suppress(ValueError):
            return cast(dict[str, JsonValue], _safe_usage_entry(usage, path="lm_usage", filter_unknown=True))
    return {}


def _trace_failure_category(exc: BaseException) -> str:
    """Resolve failure classification lazily to preserve the package boundary."""
    from fleet_rlm.observability.failure_diagnostics import trace_failure_category

    return trace_failure_category(exc)


def bind_native_rlm_observer(
    rlm: Any,
    observer: ReasoningObserver | None,
    *,
    max_chars: int = 10_000,
) -> None:
    """Attach one run-local callback to the native action predictor."""
    if type(rlm) is not dspy.RLM:
        raise RLMConfigError("reasoning observation requires native dspy.RLM")
    predictor = rlm.generate_action
    if not isinstance(predictor, dspy.Predict):
        # Deterministic tests may replace the predictor with a narrow fake. The
        # production constructor always supplies DSPy's native Predict module.
        return
    predictor.callbacks = [
        callback for callback in predictor.callbacks if not isinstance(callback, _RLMReasoningCallback)
    ]
    if observer is not None:
        predictor.callbacks.append(_RLMReasoningCallback(observer, max_chars=max_chars))


def build_native_rlm(
    *,
    signature: type[dspy.Signature] | str,
    options: RLMOptions,
    tools: Sequence[dspy.Tool] | None = None,
    sub_lm: dspy.LM | None = None,
    interpreter: Any = None,
    verbose: bool = True,
) -> Any:
    """Build one fresh RLM using only the pinned public constructor spelling."""
    return dspy.RLM(
        signature,
        max_iterations=options.max_iterations,
        max_llm_calls=options.max_llm_calls,
        max_output_chars=options.max_output_chars,
        verbose=verbose,
        tools=list(tools) if tools is not None else None,
        sub_lm=sub_lm,
        interpreter=interpreter,
    )


def observed_usage(prediction: Any, *, duration_ms: int) -> RLMUsage:
    """Read conservative usage from public Prediction surfaces without estimates."""
    trajectory = getattr(prediction, "trajectory", None)
    iterations = (
        len(trajectory)
        if isinstance(trajectory, Sequence) and not isinstance(trajectory, (str, bytes, bytearray))
        else 0
    )
    getter = getattr(prediction, "get_lm_usage", None)
    try:
        raw_usage = getter() if callable(getter) else None
    except Exception:
        raw_usage = None
    observed_lm_usage: dict[str, dict[str, JsonValue]] = {}
    if isinstance(raw_usage, Mapping):
        with contextlib.suppress(ValueError):
            observed_lm_usage = _safe_observed_usage(raw_usage, filter_unknown=True)
    return validate_rlm_usage(
        {
            "iterations": iterations,
            "observed_lm_usage": observed_lm_usage,
            "duration_ms": duration_ms,
        }
    )
