"""Prediction validation, output character limits, RLM outcomes, and error taxonomy.

This module is the P46.2 result entry point. It consolidates output validation,
declared Signature field serialization, secret-free sanitization, usage metadata,
outcome recording, and public failure classifications.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

import dspy
from pydantic import TypeAdapter
from pydantic_core import PydanticSerializationError

from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.json_types import JsonValue
from fleet_rlm.runtime.usage import RLMUsage, empty_rlm_usage
from fleet_rlm.workspace.memory import MemoryCandidate

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Error Taxonomy
# ---------------------------------------------------------------------------


class RLMConfigError(ValueError):
    """Base class for Fleet RLM RLM configuration failures."""


class RLMModelBundleError(RLMConfigError):
    """Raised when required model roles are missing or invalid."""


class RunTerminalError(RuntimeError):
    """Base for clean Run termination with a stable public status."""

    status: str = "failed"
    public_message: str = "Turn failed"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.public_message)
        if message:
            self.public_message = message


class RunCancelledError(RunTerminalError):
    status = "cancelled"
    public_message = "Turn cancelled"


class RunNoProgressError(RunTerminalError):
    public_message = "Turn stopped after repeated tool calls made no progress"


class RunIntegrityFailureError(RunTerminalError):
    public_message = "Turn failed because a required workspace update was not completed"


class PredictionOutputError(ValueError):
    """Typed, sanitized failure for an invalid native Prediction output."""

    public_message = "Turn output is invalid"
    status = "failed"

    def __init__(self) -> None:
        super().__init__(self.public_message)


class PredictionOutputTooLargeError(PredictionOutputError):
    """Declared Prediction JSON exceeds the Turn commit character budget.

    Diagnostics are carried as typed, sanitized attributes (never in the public
    message, which is a closed Literal surfaced to operators)."""

    public_message = "Turn output is too large"

    def __init__(
        self,
        *,
        output_chars: int | None = None,
        output_preview: str | None = None,
    ) -> None:
        super().__init__()
        self.output_chars = output_chars
        self.output_preview = output_preview


# ---------------------------------------------------------------------------
# Sanitization & Secret Scrubbing
# ---------------------------------------------------------------------------


# Secrets / credentials
_SECRETISH = re.compile(
    r"(?i)("
    r"api[_-]?key|authorization|bearer\s+\S+|sk-[a-z0-9_-]+|"
    r"password|secret|token|credential|private[_-]?key"
    r")[=:\s]+\S+"
)
_TOKENISH = re.compile(r"(?i)\b(?:bearer\s+[a-z0-9._~+/=-]+|sk-[a-z0-9_-]{6,})")
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "password",
        "private_key",
        "secret",
        "token",
    }
)


def _is_sensitive_key(key: object) -> bool:
    """Recognize exact fields plus common namespaced credential fields."""
    normalized = str(key).strip().lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or normalized.endswith(
        ("_api_key", "_authorization", "_credential", "_password", "_private_key", "_secret", "_token")
    )


# Connection strings / DSNs
_DSNISH = re.compile(
    r"(?i)("
    r"(?:postgres|postgresql|mysql|mongodb|redis|amqp)(?:\+\w+)?://"
    r"[^\s\"']+|"
    r"jdbc:[^\s\"']+"
    r")"
)
# Host paths
_PATHISH = re.compile(
    r"(?i)("
    r"/(?:home|Users|Volumes|private|var|tmp|etc|opt|root|mnt|srv)/\S+|"
    r"[A-Za-z]:\\[^\s]+|"
    r"/home/daytona/\S+"
    r")"
)
# Stack / exception noise
_STACKISH = re.compile(r"(?i)(traceback \(most recent call last\)|File \"[^\"]+\", line \d+)")
# Prompt-ish dumps
_PROMPTISH = re.compile(r"(?i)(system prompt|you are a helpful|<<<instructions>>>|BEGIN SYSTEM)")
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|.)")
_PRIVATE_MARKER = re.compile(r"__FLEET_[A-Z0-9_]+__")
_URLISH = re.compile(r"(?i)\bhttps?://[^\s\"'<>]+")
_UNSAFE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Declared model outputs are never rewritten. These patterns therefore live apart
# from the error/tool-detail redactors above and only identify concrete disclosure
# shapes. Bare credential names and security terminology are deliberately safe.
_DECLARED_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?<![a-z0-9])(?:[a-z0-9]+[_-])*"
    r"(?:api[_-]?key|authorization|password|secret|token|credential|private[_-]?key)\b"
    r"\s*(?:=|:)\s*(?P<value>\"[^\"\r\n]+\"|'[^'\r\n]+'|[^\s,;}\]]+)"
)
_DECLARED_BEARER = re.compile(r"(?i)\bbearer\s+(?P<value>[a-z0-9._~+/=-]+)")
_DECLARED_PROVIDER_TOKEN = re.compile(
    r"(?i)\b(?:"
    r"sk-(?:ant-)?[a-z0-9_-]{6,}|"
    r"AIza[a-z0-9_-]{20,}|"
    r"gh[pousr]_[a-z0-9]{20,}|"
    r"xox[baprs]-[a-z0-9-]{10,}"
    r")\b"
)
_DECLARED_PRIVATE_PATH = re.compile(
    r"(?i)(?:"
    r"/(?:Users|home|root|var|tmp|private)/[^\s\"'`<>]+|"
    r"[A-Za-z]:\\[^\s\"'`<>]+"
    r")"
)
_DECLARED_STACK_DUMP = re.compile(
    r"(?i)(?:"
    r"traceback \(most recent call last\)|"
    r"(?:^|\n)\s*File \"[^\"]+\", line \d+|"
    r"(?:^|\n)\s*at\s+\S+\s+\([^\n]+:\d+(?::\d+)?\)"
    r")"
)
_DECLARED_PROMPT_DUMP = re.compile(
    r"(?is)(?:"
    r"<<<instructions>>>|BEGIN SYSTEM|<\|im_start\|>\s*system|"
    r"^\s*#{1,6}\s*system prompt\s*$|"
    r"\bsystem prompt\s*:\s*(?:\r?\n|.{0,20}\byou\s+are\b)"
    r")",
    re.MULTILINE,
)

_DECLARED_SAFE_PLACEHOLDERS = frozenset(
    {
        "",
        "***",
        "[redacted]",
        "<redacted>",
        "redacted",
        "placeholder",
        "example",
        "unset",
        "not-set",
        "your-api-key",
        "your_api_key",
        "token",
    }
)


def sanitize_public_text(text: str, *, max_len: int = 10_000) -> str:
    """Bound and redact model-authored text intended for public detail or answers."""
    cleaned = _TOKENISH.sub("[redacted]", text)
    cleaned = _SECRETISH.sub("[redacted]", cleaned)
    cleaned = _DSNISH.sub("[redacted-dsn]", cleaned)
    cleaned = _PATHISH.sub("[path]", cleaned)
    cleaned = _PROMPTISH.sub("[redacted-prompt]", cleaned)
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3] + "..."
    return cleaned


def sanitize_repair_text(text: str, *, max_len: int = 512) -> str:
    """Bound repair context while removing private and control-plane content.

    Repair text is sent back into the model, but it is also retained in the
    native trajectory. Keep the useful exception category/message while
    excluding URLs, stack dumps, Fleet framing markers, and terminal control
    sequences from every later projection.
    """
    cleaned = _ANSI_ESCAPE.sub("", text)
    cleaned = _URLISH.sub("[redacted-url]", cleaned)
    cleaned = _PRIVATE_MARKER.sub("[redacted-marker]", cleaned)
    cleaned = _STACKISH.sub("[redacted-traceback]", cleaned)
    cleaned = _UNSAFE_CONTROL.sub(" ", cleaned)
    return sanitize_public_text(cleaned, max_len=max_len)


def truncate_public_text(text: str, *, max_len: int = 10_000) -> str:
    """Bound explicit semantic product text without content-dependent rewriting."""
    limit = max(1, int(max_len))
    if len(text) <= limit:
        return text
    if limit <= 3:
        return "." * limit
    return text[: limit - 3] + "..."


def truncate_head_tail(text: str, *, max_chars: int = 4_000) -> str:
    """Bound large sandbox execution output, keeping head and tail with an omission marker.

    Mirrors DSPy's ``REPLHistory`` truncation semantics so the model sees that
    output was cut and how much. Deliberately does not redact: this text feeds
    the RLM code-repair loop and must stay semantically intact.
    """
    limit = max(1, int(max_chars))
    raw_len = len(text)
    if raw_len <= limit:
        return text
    half = limit // 2
    omitted = raw_len - limit
    return text[:half] + f"\n\n... ({omitted:,} characters omitted) ...\n\n" + text[-half:]


def sanitize_public_value(value: Any, *, max_len: int = 2_000, depth: int = 0) -> Any:
    """Recursively bound and redact JSON-like public detail values."""
    if depth >= 8:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return sanitize_public_text(value, max_len=max_len)
    if isinstance(value, dict):
        return {
            str(key)[:128]: (
                "[redacted]"
                if _is_sensitive_key(key)
                else sanitize_public_value(item, max_len=max_len, depth=depth + 1)
            )
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_public_value(item, max_len=max_len, depth=depth + 1) for item in list(value)[:50]]
    return sanitize_public_text(str(value), max_len=max_len)


def _is_safe_placeholder(value: str) -> bool:
    raw_candidate = value.strip().strip("\"'").strip()
    candidate = raw_candidate.lower()
    if candidate in _DECLARED_SAFE_PLACEHOLDERS:
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", raw_candidate):
        return True
    return bool(
        re.fullmatch(
            r"(?:\$\{?[a-z_][a-z0-9_]*\}?|<[a-z_][a-z0-9_-]*>)",
            candidate,
            flags=re.IGNORECASE,
        )
    )


def _contains_sensitive_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return not _is_safe_placeholder(value)
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def _validate_declared_secret_assignments(text: str) -> None:
    for match in _DECLARED_SECRET_ASSIGNMENT.finditer(text):
        if not _is_safe_placeholder(match.group("value")):
            raise ValueError("declared output contains a sensitive value")


def _validate_declared_bearer_tokens(text: str) -> None:
    for match in _DECLARED_BEARER.finditer(text):
        if not _is_safe_placeholder(match.group("value")):
            raise ValueError("declared output contains a bearer credential")


def _validate_declared_private_paths(text: str) -> None:
    for match in _DECLARED_PRIVATE_PATH.finditer(text):
        path = match.group(0).rstrip(".,;:)]}")
        if path == "/home/daytona/fleet" or path.startswith("/home/daytona/fleet/"):
            continue
        raise ValueError("declared output contains a private host path")


def _validate_declared_text(text: str) -> None:
    _validate_declared_secret_assignments(text)
    _validate_declared_bearer_tokens(text)
    if _DECLARED_PROVIDER_TOKEN.search(text):
        raise ValueError("declared output contains a provider credential")
    if _DSNISH.search(text):
        raise ValueError("declared output contains a connection string")
    _validate_declared_private_paths(text)
    if _DECLARED_STACK_DUMP.search(text):
        raise ValueError("declared output contains a stack dump")
    if _DECLARED_PROMPT_DUMP.search(text):
        raise ValueError("declared output contains a system-prompt dump")


def validate_declared_public_value(value: Any, *, depth: int = 0) -> None:
    """Fail closed when an original declared output contains private material.

    This validator intentionally does not return a transformed value. Callers
    either preserve the accepted semantic output exactly or reject the Turn.
    """
    if depth >= 16:
        raise ValueError("declared output nesting is too deep")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        _validate_declared_text(value)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _is_sensitive_key(key) and _contains_sensitive_value(item):
                raise ValueError("declared output contains a sensitive structured field")
            validate_declared_public_value(item, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            validate_declared_public_value(item, depth=depth + 1)
        return
    raise ValueError("declared output contains a non-JSON value")


# ---------------------------------------------------------------------------
# Prediction & Outcome Validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrajectoryStep:
    """One strictly normalized native DSPy REPL interaction."""

    index: int
    reasoning: str
    code: str
    output: str


def normalize_prediction_trajectory(prediction: Any) -> tuple[TrajectoryStep, ...]:
    """Validate and convert DSPy's public ``Prediction.trajectory`` projection."""
    trajectory = getattr(prediction, "trajectory", None)
    if not isinstance(trajectory, Sequence) or isinstance(trajectory, (str, bytes, bytearray)):
        raise PredictionOutputError

    steps: list[TrajectoryStep] = []
    for index, raw in enumerate(trajectory, start=1):
        if not isinstance(raw, Mapping) or any(not isinstance(key, str) for key in raw):
            raise PredictionOutputError
        values: dict[str, str] = {}
        for step_field in ("reasoning", "code", "output"):
            value = raw.get(step_field, "")
            if not isinstance(value, str):
                raise PredictionOutputError
            values[step_field] = value
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
        if (
            not isinstance(self.schema_id, str)
            or not self.schema_id.strip()
            or not isinstance(self.schema_version, str)
            or not self.schema_version.strip()
        ):
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
            try:
                raw = getattr(prediction, name)
            except AttributeError:
                if field.is_required():
                    raise
                factory = field.default_factory
                raw = cast(Callable[[], Any], factory)() if factory is not None else field.default
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
        preview = sanitize_public_text(result.display_text, max_len=400)
        raise PredictionOutputTooLargeError(
            output_chars=len(encoded),
            output_preview=preview,
        )
    try:
        validate_declared_public_value(result.outputs)
    except ValueError:
        raise PredictionOutputError from None
    return result


@dataclass(frozen=True, slots=True)
class ResultContract:
    """Output validation contract declared for one execution."""

    signature: type[dspy.Signature]
    schema_id: str = "fleet.default"
    schema_version: str = "1"
    max_output_chars: int = 10_000


def validate_prediction(
    prediction: Any,
    contract: ResultContract,
) -> PredictionResult:
    """Validate a native DSPy Prediction against a declared ResultContract."""
    return prediction_result(
        prediction,
        contract.signature,
        schema_id=contract.schema_id,
        schema_version=contract.schema_version,
        max_output_chars=contract.max_output_chars,
    )


# ---------------------------------------------------------------------------
# Usage Metadata
# ---------------------------------------------------------------------------


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
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
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


def _safe_usage_details(value: object, *, path: str, filter_unknown: bool) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        dump = getattr(value, "model_dump", None)
        value = dump() if callable(dump) else value
    if not isinstance(value, Mapping) or any(not isinstance(detail, str) for detail in value):
        raise ValueError(f"{path} must be an object")

    detail_usage = cast(Mapping[str, object], value)
    details: dict[str, JsonValue] = {}
    for detail, detail_value in detail_usage.items():
        if detail not in _SAFE_USAGE_DETAIL_KEYS:
            if filter_unknown:
                continue
            raise ValueError(f"{path}.{detail} is not safe observed usage telemetry")
        details[detail] = _observed_scalar(detail_value, path=f"{path}.{detail}")
    return details


def _safe_usage_value(key: str, value: object, *, path: str, filter_unknown: bool) -> JsonValue:
    if key.endswith("_details"):
        return _safe_usage_details(value, path=f"{path}.{key}", filter_unknown=filter_unknown)
    return _observed_scalar(value, path=f"{path}.{key}")


def _safe_usage_entry(value: object, *, path: str, filter_unknown: bool) -> dict[str, JsonValue]:
    """
    Validate and normalize an observed usage mapping for safe telemetry.

    Parameters:
        value (object): Usage data to validate.
        path (str): Location used in validation error messages.
        filter_unknown (bool): Whether to omit unrecognized usage fields instead of raising an error.

    Returns:
        dict[str, JsonValue]: A validated usage mapping containing only allowed JSON-compatible values.
    """
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{path} must be an object with string keys")
    usage = cast(Mapping[str, object], value)
    result: dict[str, JsonValue] = {}
    for key, item in usage.items():
        if key not in _SAFE_USAGE_KEYS:
            if filter_unknown:
                continue
            raise ValueError(f"{path}.{key} is not safe observed usage telemetry")
        result[key] = _safe_usage_value(key, item, path=path, filter_unknown=filter_unknown)
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


_RLM_EXTRACTION_FALLBACK_REASONING = "Extract forced final output"


def rlm_termination_mode(prediction: Any) -> str:
    """Classify one completed RLM prediction's termination mode.

    DSPy's forced final-output extraction lands on the reserved
    ``final_reasoning`` marker; its presence means the RLM could not settle
    through typed SUBMIT payloads, so the fallback is named explicitly rather
    than re-derived with the magic string at each call site.
    """
    if getattr(prediction, "final_reasoning", None) == _RLM_EXTRACTION_FALLBACK_REASONING:
        return "native_extraction_fallback"
    return "typed_submit"


# ---------------------------------------------------------------------------
# RLM Outcome Types
# ---------------------------------------------------------------------------

TerminalStatus: TypeAlias = Literal["completed", "cancelled", "timeout", "failed"]

ExecutionDetail: TypeAlias = Any


@dataclass(frozen=True, slots=True)
class RLMOutcome:
    """Runner result after non-terminal observations; lifecycle owns settlement."""

    terminal_status: TerminalStatus
    prediction: PredictionResult | None = None
    usage: RLMUsage = field(default_factory=empty_rlm_usage)
    artifact_candidates: tuple[ArtifactCandidate, ...] = ()
    memory_candidates: tuple[MemoryCandidate, ...] = ()
    execution_details: tuple[ExecutionDetail, ...] = ()
    public_error_message: str | None = None
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        usage = validate_rlm_usage(self.usage)
        object.__setattr__(self, "usage", MappingProxyType(usage))
        if self.terminal_status == "completed" and self.public_error_message is not None:
            raise ValueError("a successful outcome cannot contain a public error")
        if (self.terminal_status == "completed") != (self.prediction is not None):
            raise ValueError("only a successful outcome must contain a prediction")
        if self.terminal_status != "completed" and self.memory_candidates:
            raise ValueError("only a successful outcome may carry Memory Candidates")

    @property
    def succeeded(self) -> bool:
        return self.terminal_status == "completed"


__all__ = [
    "ExecutionDetail",
    "PredictionOutputError",
    "PredictionOutputTooLargeError",
    "PredictionResult",
    "RLMConfigError",
    "RLMModelBundleError",
    "RLMOutcome",
    "RLMUsage",
    "ResultContract",
    "RunCancelledError",
    "RunIntegrityFailureError",
    "RunNoProgressError",
    "RunTerminalError",
    "TerminalStatus",
    "TrajectoryStep",
    "empty_rlm_usage",
    "normalize_prediction_trajectory",
    "observed_usage",
    "prediction_result",
    "rlm_termination_mode",
    "sanitize_public_text",
    "sanitize_public_value",
    "sanitize_repair_text",
    "truncate_head_tail",
    "truncate_public_text",
    "validate_declared_public_value",
    "validate_prediction",
    "validate_rlm_usage",
]
