"""Exact public DSPy 3.3.0b1 RLM construction and observation contract."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Any, TypeAlias, TypedDict, cast

import dspy
from dspy.predict.rlm import _strip_code_fences, logger
from pydantic import TypeAdapter
from pydantic_core import PydanticSerializationError

from fleet_rlm.rlm.errors import RLMConfigError
from fleet_rlm.rlm.sanitize import truncate_public_text, validate_declared_public_value

DSPY_VERSION = "3.3.0b1"

JsonValue: TypeAlias = None | bool | int | float | str | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
ObservedUsageValue: TypeAlias = None | bool | int | float | str | dict[str, JsonValue]
ReasoningObserver: TypeAlias = Any


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
        raise PredictionOutputError
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


_SAFE_USAGE_KEYS = frozenset({
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
})
_SAFE_USAGE_DETAIL_KEYS = frozenset({
    "audio_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "accepted_prediction_tokens",
    "rejected_prediction_tokens",
    "text_tokens",
    "image_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
})


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


_NativeRLM = cast(type[Any], dspy.RLM)


class ObservedRLM(_NativeRLM):
    """Native ``dspy.RLM`` with optional live ``RLMReasoning`` observation."""

    def bind_observer(self, observer: ReasoningObserver | None, *, max_chars: int = 10_000) -> None:
        """Bind one run-local reasoning observer without changing RLM semantics."""
        self._fleet_observer = observer
        self._fleet_observation_max_chars = max(1, int(max_chars))

    def _observe_reasoning(self, prediction: Any, iteration: int) -> None:
        observer = getattr(self, "_fleet_observer", None)
        if observer is None:
            return
        reasoning = getattr(prediction, "reasoning", None)
        if not isinstance(reasoning, str) or not reasoning.strip():
            return
        try:
            # Circular-import boundary: events imports usage validators from this module.
            from fleet_rlm.rlm.events import RLMReasoning

            observer(
                RLMReasoning(
                    truncate_public_text(reasoning, max_len=self._fleet_observation_max_chars),
                    iteration + 1,
                )
            )
        except Exception:  # noqa: BLE001 - observation must never alter execution
            return

    def _execute_iteration(
        self,
        repl: Any,
        variables: Any,
        history: Any,
        iteration: int,
        input_args: dict[str, Any],
        output_field_names: list[str],
    ) -> Any:
        variables_info = [variable.format() for variable in variables]
        action = self.generate_action(
            variables_info=variables_info,
            repl_history=history,
            iteration=f"{iteration + 1}/{self.max_iterations}",
        )
        self._observe_reasoning(action, iteration)
        if self.verbose:
            logger.info(
                f"RLM iteration {iteration + 1}/{self.max_iterations}\n"
                f"Reasoning: {action.reasoning}\nCode:\n{action.code}"
            )
        try:
            code = _strip_code_fences(action.code)
        except SyntaxError as exc:
            code = action.code
            result = f"[Error] {exc}"
            return self._process_execution_result(action, code, result, history, output_field_names)
        result = self._execute_code(repl, code, input_args)
        return self._process_execution_result(action, code, result, history, output_field_names)

    async def _aexecute_iteration(
        self,
        repl: Any,
        variables: Any,
        history: Any,
        iteration: int,
        input_args: dict[str, Any],
        output_field_names: list[str],
    ) -> Any:
        variables_info = [variable.format() for variable in variables]
        pred = await self.generate_action.acall(
            variables_info=variables_info,
            repl_history=history,
            iteration=f"{iteration + 1}/{self.max_iterations}",
        )
        self._observe_reasoning(pred, iteration)
        if self.verbose:
            logger.info(
                f"RLM iteration {iteration + 1}/{self.max_iterations}\nReasoning: {pred.reasoning}\nCode:\n{pred.code}"
            )
        try:
            code = _strip_code_fences(pred.code)
        except SyntaxError as exc:
            code = pred.code
            result = f"[Error] {exc}"
            return self._process_execution_result(pred, code, result, history, output_field_names)
        result = self._execute_code(repl, code, input_args)
        return self._process_execution_result(pred, code, result, history, output_field_names)


def build_native_rlm(
    *,
    signature: type[dspy.Signature] | str,
    options: RLMOptions,
    tools: Sequence[dspy.Tool] | None = None,
    sub_lm: dspy.LM | None = None,
    interpreter: Any = None,
    verbose: bool = True,
) -> ObservedRLM:
    """Build one fresh RLM using only the pinned public constructor spelling."""
    return ObservedRLM(
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
    except Exception:  # noqa: BLE001 - incomplete provider telemetry is represented as empty
        raw_usage = None
    observed_lm_usage: dict[str, dict[str, JsonValue]] = {}
    if isinstance(raw_usage, Mapping):
        try:
            observed_lm_usage = _safe_observed_usage(raw_usage, filter_unknown=True)
        except ValueError:
            pass
    return validate_rlm_usage({
        "iterations": iterations,
        "observed_lm_usage": observed_lm_usage,
        "duration_ms": duration_ms,
    })
