"""DSPy 3.3.x compatibility, version guard, callbacks, and interpreter contracts.

This module isolates version-specific and private/public DSPy 3.3.1 contracts.
Other modules in ``fleet_rlm.rlm`` depend on this compatibility layer rather
than importing private or version-sensitive DSPy mechanics directly.
"""

from __future__ import annotations

import ast
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
from dspy.clients.base_lm import BaseLM
from dspy.signatures.signature import Signature
from dspy.utils.callback import BaseCallback
from dspy.utils.exceptions import AdapterParseError, LMTimeoutError

from fleet_rlm.json_types import JsonValue
from fleet_rlm.rlm.result import _safe_usage_entry, sanitize_public_text, truncate_public_text

logger = logging.getLogger(__name__)

ReasoningObserver: TypeAlias = Callable[[Any], None]

CERTIFIED_DSPY_VERSION = "3.3.1"

PUBLIC_FINAL_OUTPUT_LABEL = "FINAL submitted"

# Keep this text in one place.  DSPy copies callable metadata into its native
# action Signature exactly once at RLM construction time.
DAYTONA_EXECUTION_INSTRUCTIONS = (
    "Execution runs in isolated Python. The Python namespace persists across actions in one invocation. "
    "Host Tools are callable Python functions through Fleet's local mediation seam. "
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


def copy_output_fields(
    output_fields: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Return an independent copy of signature output metadata for interpreter state."""
    if output_fields is None:
        return None
    return deepcopy(output_fields)


def needs_binding_refresh(
    *,
    desired_generation: int,
    installed_generation: int,
    broker_ready: bool,
) -> bool:
    """Whether interpreter bindings should be refreshed for this action."""
    return desired_generation != installed_generation or not broker_ready


# ---------------------------------------------------------------------------
# Bounded re-ask adapter for the pinned JSON action protocol
# ---------------------------------------------------------------------------

RETRY_CORRECTION_FIELD = "fleet_retry_correction"
BUDGET_DIRECTIVE_FIELD = "fleet_budget_directive"
WRAP_UP_CORRECTION_FIELD = "fleet_wrap_up_correction"

_EMPTY_RESPONSE_MARKER = "The LM returned an empty or null response"

DEFAULT_PARSE_RETRIES = 2


def _retry_correction_feedback(attempt: int, exc: AdapterParseError) -> str:
    """
    Build bounded corrective feedback for one failed action attempt.

    The raw LM response is never echoed back: provider output is untrusted
    prompt-facing text, so only the failure category is described.

    Parameters:
        attempt (int): The retry attempt number, starting at 1.
        exc (AdapterParseError): The parse failure that triggered the retry.

    Returns:
        str: Bounded instruction text for the corrected re-ask.
    """
    message = str(getattr(exc, "message", "") or "")
    if _EMPTY_RESPONSE_MARKER in message:
        return (
            f"Correction (attempt {attempt}): the previous response produced no parseable output. "
            "It was empty or null, typically because generation exhausted the output-token budget "
            "before emitting any text. Respond now with one JSON object containing exactly the "
            "required output fields. Keep reasoning short and do not repeat earlier analysis."
        )
    return (
        f"Correction (attempt {attempt}): the previous response was not a JSON object containing "
        "the required output fields. Respond now with one JSON object containing exactly the "
        "required output fields; no surrounding prose, markdown, or code fences."
    )


def _correction_field_name(signature: type[Signature]) -> str:
    """
    Pick the retry-correction input field name free of caller collisions.

    Parameters:
        signature (type[Signature]): The base signature used by the failed call.

    Returns:
        str: The reserved field name, suffixed per collision until it is free.
    """
    field = RETRY_CORRECTION_FIELD
    suffix = 1
    while field in signature.fields:
        suffix += 1
        field = f"{RETRY_CORRECTION_FIELD}_{suffix}"
    return field


def _retry_call_arguments(
    signature: type[Signature],
    inputs: dict[str, Any],
    attempt: int,
    exc: AdapterParseError,
) -> tuple[type[Signature], dict[str, Any]]:
    """
    Extend one failed action call with a bounded corrective input field.

    The base signature is extended afresh on every retry, so a caller that
    already defines the reserved correction field keeps its own input value
    untouched: the adapter appends the next collision-free correction field
    instead of overwriting caller context.

    Parameters:
        signature (type[Signature]): The base signature used by the failed call;
            never a previously extended retry signature.
        inputs (dict[str, Any]): The inputs used by the failed call; never mutated.
        attempt (int): The retry attempt number, starting at 1.
        exc (AdapterParseError): The parse failure that triggered the retry.

    Returns:
        tuple[type[Signature], dict[str, Any]]: The retry signature and inputs.
    """
    correction_field = _correction_field_name(signature)
    retry_signature = signature.append(
        correction_field,
        dspy.InputField(desc="Bounded corrective feedback for the previous failed attempt; follow it."),
    )
    retry_inputs = dict(inputs)
    retry_inputs[correction_field] = _retry_correction_feedback(attempt, exc)
    return retry_signature, retry_inputs


_SAFE_SUBMIT_CALLS = frozenset(
    {
        "str",
        "repr",
        "int",
        "float",
        "bool",
        "len",
        "min",
        "max",
        "sum",
        "round",
        "sorted",
        "json.dumps",
    }
)

_PYTHON_FENCE_LANGS = frozenset({"", "python", "py"})


def _qualified_ast_name(node: ast.AST) -> str | None:
    """Return a dotted name for a simple AST name/attribute expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_ast_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _strip_action_code_fences(code: str) -> str:
    """Mirror DSPy's public action fence handling for wrap-up validation.

    Native RLM actions are commonly emitted in a Python markdown fence and
    DSPy strips that fence before execution. Validation must inspect the same
    executable text, while still rejecting an explicit non-Python fence.
    """
    text = code.strip()
    if "```" not in text:
        return text
    lines = text.splitlines()
    while len(lines) >= 2 and lines[0].strip() == "```" and lines[-1].strip() == "```":
        lines.pop(0)
        lines.pop()
    text = "\n".join(lines).strip()
    if "```" not in text:
        return text
    fence_start = text.find("```")
    lang_line, separator, remainder = text[fence_start + 3 :].partition("\n")
    if not separator:
        return text
    lang = (lang_line.strip().split(maxsplit=1)[0] if lang_line.strip() else "").lower()
    if lang not in _PYTHON_FENCE_LANGS:
        return text
    block_end = remainder.find("```")
    if block_end == -1:
        return remainder.strip()
    return remainder[:block_end].strip()


def _is_safe_submit_value(node: ast.AST) -> bool:
    """Allow only data expressions that cannot launch another action."""
    if isinstance(node, (ast.Constant, ast.Name)):
        return True
    if isinstance(node, ast.Attribute):
        return not node.attr.startswith("_") and _is_safe_submit_value(node.value)
    if isinstance(node, ast.Subscript):
        return _is_safe_submit_value(node.value) and _is_safe_submit_value(node.slice)
    if isinstance(node, ast.Slice):
        return all(part is None or _is_safe_submit_value(part) for part in (node.lower, node.upper, node.step))
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_safe_submit_value(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            key is not None and _is_safe_submit_value(key) and _is_safe_submit_value(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.Call):
        if _qualified_ast_name(node.func) not in _SAFE_SUBMIT_CALLS:
            return False
        if any(isinstance(argument, ast.Starred) for argument in node.args):
            return False
        if any(keyword.arg is None for keyword in node.keywords):
            return False
        return all(_is_safe_submit_value(argument) for argument in node.args) and all(
            _is_safe_submit_value(keyword.value) for keyword in node.keywords
        )
    if isinstance(node, ast.UnaryOp):
        return _is_safe_submit_value(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_safe_submit_value(node.left) and _is_safe_submit_value(node.right)
    if isinstance(node, ast.BoolOp):
        return all(_is_safe_submit_value(value) for value in node.values)
    if isinstance(node, ast.Compare):
        return _is_safe_submit_value(node.left) and all(_is_safe_submit_value(item) for item in node.comparators)
    if isinstance(node, ast.IfExp):
        return (
            _is_safe_submit_value(node.test) and _is_safe_submit_value(node.body) and _is_safe_submit_value(node.orelse)
        )
    return False


def _is_submit_only_code(code: object) -> bool:
    """Validate the bounded, side-effect-free action allowed in wrap-up mode."""
    if not isinstance(code, str):
        return False
    try:
        module = ast.parse(_strip_action_code_fences(code), mode="exec")
    except SyntaxError:
        return False
    if len(module.body) != 1 or not isinstance(module.body[0], ast.Expr):
        return False
    expression = module.body[0].value
    if (
        not isinstance(expression, ast.Call)
        or not isinstance(expression.func, ast.Name)
        or expression.func.id != "SUBMIT"
    ):
        return False
    if expression.args or any(keyword.arg is None for keyword in expression.keywords):
        return False
    return all(_is_safe_submit_value(keyword.value) for keyword in expression.keywords)


def _iteration_is_action(inputs: Mapping[str, Any]) -> bool:
    """Whether DSPy supplied its native ``generate_action`` iteration marker."""
    value = inputs.get("iteration")
    if not isinstance(value, str):
        return False
    try:
        current, total = (int(part.strip()) for part in value.split("/", 1))
    except (ValueError, TypeError):
        return False
    return current >= 1 and total >= current


def _append_input_field(
    signature: type[Signature],
    inputs: Mapping[str, Any],
    *,
    preferred_name: str,
    description: str,
    value: str,
) -> tuple[type[Signature], dict[str, Any], str]:
    """Append one collision-free input field without mutating caller inputs."""
    field = preferred_name
    suffix = 1
    while field in signature.fields:
        suffix += 1
        field = f"{preferred_name}_{suffix}"
    extended = signature.append(field, dspy.InputField(desc=description))
    extended_inputs = dict(inputs)
    extended_inputs[field] = value
    return extended, extended_inputs, field


def _budget_directive(remaining: float) -> str:
    seconds = max(0, int(remaining))
    return (
        f"Time budget nearly exhausted ({seconds}s remaining). Submit your best-supported answer now "
        "using evidence already gathered. Do not explore, call tools, or execute additional code. "
        "Return exactly one SUBMIT(...) action."
    )


def _wrap_up_correction(reason: str) -> str:
    return (
        "Wrap-up correction: the previous action was not a single compliant SUBMIT call "
        f"({reason}). Use only existing variables and safe serialization; return exactly one SUBMIT(...) action."
    )


def _action_code(response: object) -> object:
    if not isinstance(response, Sequence) or isinstance(response, (str, bytes, bytearray)) or not response:
        return None
    first = response[0]
    return first.get("code") if isinstance(first, Mapping) else None


class FleetJSONAdapter(dspy.JSONAdapter):
    """The pinned JSON action protocol plus a bounded corrective re-ask.

    DSPy 3.3.1 raises ``AdapterParseError`` for an empty or unparseable action
    response without retrying: the legacy ``Retry`` module is removed and
    ``LM.num_retries`` only covers transient provider failures. One provider
    hiccup -- typically reasoning consuming the entire completion budget
    before any output token is emitted -- would otherwise discard a whole
    Turn's trajectory. Following DSPy's own adapter-level fallback precedent
    (``ChatAdapter.use_json_adapter_fallback``, ``dspy/adapters/chat_adapter.py``),
    this subclass keeps the stock ``JSONAdapter`` protocol authoritative and
    only adds a bounded re-ask of the same LM with corrective feedback appended
    as a signature input. The final ``AdapterParseError`` propagates unchanged
    once retries are exhausted, so Fleet's failure mapping stays intact.

    Parameters:
        max_parse_retries: Additional LM attempts after the first failed action
            response. Defaults to ``DEFAULT_PARSE_RETRIES``.
        deadline: Absolute monotonic deadline for this execution, when bound.
        wrap_up_seconds: Reserved final-answer window for native action calls.
    """

    def __init__(
        self,
        *,
        max_parse_retries: int = DEFAULT_PARSE_RETRIES,
        deadline: float | None = None,
        wrap_up_seconds: float = 0.0,
    ) -> None:
        """Initialize the adapter with a bounded retry budget.

        Parameters:
            max_parse_retries (int): Additional attempts after the first failed
                action response; must be a non-negative integer.

        Raises:
            ValueError: If ``max_parse_retries`` is not a non-negative integer.
        """
        if not isinstance(max_parse_retries, int) or isinstance(max_parse_retries, bool) or max_parse_retries < 0:
            raise ValueError(f"max_parse_retries must be a non-negative integer, got {max_parse_retries!r}")
        if deadline is not None and not isinstance(deadline, (int, float)):
            raise ValueError(f"deadline must be numeric or None, got {deadline!r}")
        if not isinstance(wrap_up_seconds, (int, float)) or isinstance(wrap_up_seconds, bool) or wrap_up_seconds < 0:
            raise ValueError(f"wrap_up_seconds must be a non-negative number, got {wrap_up_seconds!r}")
        super().__init__()
        self._max_parse_retries = max_parse_retries
        self._deadline = float(deadline) if deadline is not None else None
        self._wrap_up_seconds = float(wrap_up_seconds)
        self._wrap_up_entered = False
        self._wrap_up_attempts = 0
        self._wrap_up_rejection_reason: str | None = None
        self._wrap_up_remaining_ms: int | None = None

    def _remaining(self) -> float | None:
        if self._deadline is None:
            return None
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Turn deadline exceeded")
        return remaining

    def _enter_wrap_up(self, remaining: float, *, rejection_reason: str | None = None) -> None:
        """Record the first reserve transition and any bounded rejection reason."""
        if not self._wrap_up_entered:
            self._wrap_up_entered = True
            self._wrap_up_remaining_ms = max(0, round(remaining * 1000))
        if rejection_reason is not None:
            self._wrap_up_rejection_reason = rejection_reason

    def wrap_up_summary(self) -> dict[str, Any]:
        """Return bounded engineering metadata for the current adapter call."""
        return {
            "wrap_up_entered": self._wrap_up_entered,
            "wrap_up_attempts": self._wrap_up_attempts,
            "wrap_up_rejection_reason": self._wrap_up_rejection_reason,
            "wrap_up_remaining_ms": self._wrap_up_remaining_ms,
        }

    def _next_wrap_up_attempt(self) -> None:
        """Consume one of the two total final-answer provider attempts."""
        if self._wrap_up_attempts >= 2:
            raise TimeoutError("wrap-up action did not submit before the Turn deadline")
        self._wrap_up_attempts += 1

    def _wrap_up_required(self, inputs: Mapping[str, Any], remaining: float | None) -> bool:
        return bool(
            remaining is not None
            and self._wrap_up_seconds > 0
            and _iteration_is_action(inputs)
            and remaining <= self._wrap_up_seconds
        )

    def _call_kwargs(
        self,
        lm: BaseLM,
        lm_kwargs: Mapping[str, Any],
        *,
        remaining: float | None,
        wrap_up: bool,
        action: bool,
    ) -> dict[str, Any]:
        values = dict(lm_kwargs)
        if remaining is None:
            return values
        available = remaining if wrap_up or not action else remaining - self._wrap_up_seconds
        if available <= 0:
            raise TimeoutError("Turn final-answer reserve exhausted")
        configured = values.get("timeout")
        if configured is None:
            configured = getattr(lm, "kwargs", {}).get("timeout")
        if isinstance(configured, (int, float)) and not isinstance(configured, bool) and configured > 0:
            available = min(float(configured), available)
        values["timeout"] = available
        return values

    def _with_wrap_up_directive(
        self,
        signature: type[Signature],
        inputs: Mapping[str, Any],
        remaining: float,
        *,
        field_name: str | None = None,
    ) -> tuple[type[Signature], dict[str, Any], str]:
        directive = _budget_directive(remaining)
        if field_name is not None and field_name in signature.fields:
            updated = dict(inputs)
            updated[field_name] = directive
            return signature, updated, field_name
        extended, extended_inputs, inserted_field = _append_input_field(
            signature,
            inputs,
            preferred_name=BUDGET_DIRECTIVE_FIELD,
            description="Mandatory final-answer budget directive; follow it exactly.",
            value=directive,
        )
        return extended, extended_inputs, inserted_field

    def _with_wrap_up_correction(
        self,
        signature: type[Signature],
        inputs: Mapping[str, Any],
        *,
        reason: str,
    ) -> tuple[type[Signature], dict[str, Any]]:
        extended, extended_inputs, _ = _append_input_field(
            signature,
            inputs,
            preferred_name=WRAP_UP_CORRECTION_FIELD,
            description="Mandatory correction for the final SUBMIT action; follow it exactly.",
            value=_wrap_up_correction(reason),
        )
        return extended, extended_inputs

    def __call__(
        self,
        lm: BaseLM,
        lm_kwargs: dict[str, Any],
        signature: type[Signature],
        demos: list[dict[str, Any]],
        inputs: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Execute the stock JSONAdapter pipeline with a bounded corrective re-ask.

        Parameters:
            lm (BaseLM): The language model instance to use for generation.
            lm_kwargs (dict[str, Any]): Additional keyword arguments for the LM call.
            signature (type[Signature]): The DSPy signature for this call.
            demos (list[dict[str, Any]]): Few-shot examples included in the prompt.
            inputs (dict[str, Any]): The current input values for this call.

        Returns:
            list[dict[str, Any]]: Parsed responses keyed by the signature output fields.
        """
        attempt = 0
        base_signature = signature
        base_inputs = dict(inputs)
        wrap_up = False
        request_signature = signature
        request_inputs = dict(inputs)
        directive_field: str | None = None
        while True:
            remaining = self._remaining()
            action = _iteration_is_action(base_inputs)
            if action and self._wrap_up_required(base_inputs, remaining):
                wrap_up = True
                assert remaining is not None
                self._enter_wrap_up(remaining)
            if wrap_up and remaining is not None:
                request_signature, request_inputs, directive_field = self._with_wrap_up_directive(
                    request_signature,
                    request_inputs,
                    remaining,
                    field_name=directive_field,
                )
                self._next_wrap_up_attempt()
            call_kwargs = self._call_kwargs(
                lm,
                lm_kwargs,
                remaining=remaining,
                wrap_up=wrap_up,
                action=action,
            )
            try:
                response = super().__call__(lm, call_kwargs, request_signature, demos, request_inputs)
            except (LMTimeoutError, TimeoutError):
                if not wrap_up and action and self._wrap_up_seconds > 0:
                    boundary_remaining = self._remaining()
                    if boundary_remaining is not None and boundary_remaining <= self._wrap_up_seconds:
                        wrap_up = True
                        self._enter_wrap_up(boundary_remaining)
                        request_signature, request_inputs, directive_field = self._with_wrap_up_directive(
                            request_signature,
                            request_inputs,
                            boundary_remaining,
                            field_name=directive_field,
                        )
                        continue
                raise
            except AdapterParseError as exc:
                if wrap_up:
                    if self._wrap_up_attempts >= 2:
                        raise TimeoutError("wrap-up action was not parseable before the Turn deadline") from exc
                    self._wrap_up_rejection_reason = "unparseable_json"
                    request_signature, request_inputs = self._with_wrap_up_correction(
                        request_signature,
                        request_inputs,
                        reason="unparseable JSON",
                    )
                    continue
                if action and self._wrap_up_seconds > 0:
                    # A response can finish after the provider timeout was
                    # reduced to the reserve boundary. Treat that parse
                    # failure as the first wrap-up attempt so its one
                    # corrective re-ask shares the same two-attempt ceiling
                    # as every other final-answer path.
                    boundary_remaining = self._remaining()
                    if boundary_remaining is not None and boundary_remaining <= self._wrap_up_seconds:
                        wrap_up = True
                        self._enter_wrap_up(boundary_remaining, rejection_reason="unparseable_json")
                        self._next_wrap_up_attempt()
                        request_signature, request_inputs, directive_field = self._with_wrap_up_directive(
                            request_signature,
                            request_inputs,
                            boundary_remaining,
                            field_name=directive_field,
                        )
                        request_signature, request_inputs = self._with_wrap_up_correction(
                            request_signature,
                            request_inputs,
                            reason="unparseable JSON",
                        )
                        continue
                if attempt >= self._max_parse_retries:
                    raise
                attempt += 1
                request_signature, request_inputs = _retry_call_arguments(
                    base_signature,
                    base_inputs,
                    attempt,
                    exc,
                )
                continue
            if remaining is not None:
                after_response = self._remaining()
                if not wrap_up and action and after_response is not None and after_response <= self._wrap_up_seconds:
                    wrap_up = True
                    self._enter_wrap_up(after_response)
                    self._next_wrap_up_attempt()
                    if _is_submit_only_code(_action_code(response)):
                        # The late normal response already satisfies the
                        # wrap-up grammar. Execute it as the initial
                        # final-answer attempt instead of spending reserve
                        # time on an unnecessary re-ask.
                        return response
                    request_signature, request_inputs, directive_field = self._with_wrap_up_directive(
                        request_signature,
                        request_inputs,
                        after_response,
                        field_name=directive_field,
                    )
                if wrap_up and action and not _is_submit_only_code(_action_code(response)):
                    self._wrap_up_rejection_reason = "exploration_or_additional_code"
                    if self._wrap_up_attempts >= 2:
                        raise TimeoutError("wrap-up action did not submit before the Turn deadline")
                    request_signature, request_inputs = self._with_wrap_up_correction(
                        request_signature,
                        request_inputs,
                        reason="exploration or additional code",
                    )
                    continue
                return response
            return response

    async def acall(
        self,
        lm: BaseLM,
        lm_kwargs: dict[str, Any],
        signature: type[Signature],
        demos: list[dict[str, Any]],
        inputs: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Execute the stock JSONAdapter pipeline with a bounded corrective re-ask.

        Parameters:
            lm (BaseLM): The language model instance to use for generation.
            lm_kwargs (dict[str, Any]): Additional keyword arguments for the LM call.
            signature (type[Signature]): The DSPy signature for this call.
            demos (list[dict[str, Any]]): Few-shot examples included in the prompt.
            inputs (dict[str, Any]): The current input values for this call.

        Returns:
            list[dict[str, Any]]: Parsed responses keyed by the signature output fields.
        """
        attempt = 0
        base_signature = signature
        base_inputs = dict(inputs)
        wrap_up = False
        request_signature = signature
        request_inputs = dict(inputs)
        directive_field: str | None = None
        while True:
            remaining = self._remaining()
            action = _iteration_is_action(base_inputs)
            if action and self._wrap_up_required(base_inputs, remaining):
                wrap_up = True
                assert remaining is not None
                self._enter_wrap_up(remaining)
            if wrap_up and remaining is not None:
                request_signature, request_inputs, directive_field = self._with_wrap_up_directive(
                    request_signature,
                    request_inputs,
                    remaining,
                    field_name=directive_field,
                )
                self._next_wrap_up_attempt()
            call_kwargs = self._call_kwargs(
                lm,
                lm_kwargs,
                remaining=remaining,
                wrap_up=wrap_up,
                action=action,
            )
            try:
                response = await super().acall(lm, call_kwargs, request_signature, demos, request_inputs)
            except (LMTimeoutError, TimeoutError):
                if not wrap_up and action and self._wrap_up_seconds > 0:
                    boundary_remaining = self._remaining()
                    if boundary_remaining is not None and boundary_remaining <= self._wrap_up_seconds:
                        wrap_up = True
                        self._enter_wrap_up(boundary_remaining)
                        request_signature, request_inputs, directive_field = self._with_wrap_up_directive(
                            request_signature,
                            request_inputs,
                            boundary_remaining,
                            field_name=directive_field,
                        )
                        continue
                raise
            except AdapterParseError as exc:
                if wrap_up:
                    if self._wrap_up_attempts >= 2:
                        raise TimeoutError("wrap-up action was not parseable before the Turn deadline") from exc
                    self._wrap_up_rejection_reason = "unparseable_json"
                    request_signature, request_inputs = self._with_wrap_up_correction(
                        request_signature,
                        request_inputs,
                        reason="unparseable JSON",
                    )
                    continue
                if action and self._wrap_up_seconds > 0:
                    # Keep parse corrections that cross the reserve boundary
                    # inside the same two-attempt wrap-up budget as action
                    # corrections.
                    boundary_remaining = self._remaining()
                    if boundary_remaining is not None and boundary_remaining <= self._wrap_up_seconds:
                        wrap_up = True
                        self._enter_wrap_up(boundary_remaining, rejection_reason="unparseable_json")
                        self._next_wrap_up_attempt()
                        request_signature, request_inputs, directive_field = self._with_wrap_up_directive(
                            request_signature,
                            request_inputs,
                            boundary_remaining,
                            field_name=directive_field,
                        )
                        request_signature, request_inputs = self._with_wrap_up_correction(
                            request_signature,
                            request_inputs,
                            reason="unparseable JSON",
                        )
                        continue
                if attempt >= self._max_parse_retries:
                    raise
                attempt += 1
                request_signature, request_inputs = _retry_call_arguments(
                    base_signature,
                    base_inputs,
                    attempt,
                    exc,
                )
                continue
            if remaining is not None:
                after_response = self._remaining()
                if not wrap_up and action and after_response is not None and after_response <= self._wrap_up_seconds:
                    wrap_up = True
                    self._enter_wrap_up(after_response)
                    self._next_wrap_up_attempt()
                    if _is_submit_only_code(_action_code(response)):
                        return response
                    request_signature, request_inputs, directive_field = self._with_wrap_up_directive(
                        request_signature,
                        request_inputs,
                        after_response,
                        field_name=directive_field,
                    )
                if wrap_up and action and not _is_submit_only_code(_action_code(response)):
                    self._wrap_up_rejection_reason = "exploration_or_additional_code"
                    if self._wrap_up_attempts >= 2:
                        raise TimeoutError("wrap-up action did not submit before the Turn deadline")
                    request_signature, request_inputs = self._with_wrap_up_correction(
                        request_signature,
                        request_inputs,
                        reason="exploration or additional code",
                    )
                    continue
                return response
            return response


class _RLMReasoningCallback(BaseCallback):
    """Observe native action lifecycle callbacks without changing predictions.

    DSPy exposes module start/end callback hooks for this lifecycle
    (``dspy/utils/callback.py:65-95``).
    """

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

    def on_module_start(
        self,
        call_id: str,
        instance: Any,
        inputs: dict[str, Any],
    ) -> None:
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

    def on_module_end(
        self,
        call_id: str,
        outputs: Any | None,
        exception: BaseException | None = None,
    ) -> None:
        action_span = self._action_spans.pop(call_id, None)
        if self._deadline is not None and time.monotonic() >= self._deadline:
            if action_span is not None:
                action_span.finish(
                    phase_status="failed",
                    outputs={"action_status": "deadline_exceeded"},
                )
            return
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
        role = self._roles.get(id(instance))
        if role is None:
            return
        model = "unknown"
        history_length: int | None = None
        self._call_index += 1
        call_index = self._call_index
        try:
            model = getattr(instance, "model", "unknown")
            history = getattr(instance, "history", None)
            history_length = len(history) if isinstance(history, Sequence) else None
        except Exception:
            pass
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
        role = self._roles.get(id(instance), "unknown")
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
        response_details.update(
            {
                "call_index": call_index,
                "wall_time_ms": round((time.perf_counter() - started_at) * 1000, 3),
            }
        )
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
        ):
            value = response_details.get(key)
            if value is not None:
                last_call[key] = value
        failure_outputs: dict[str, JsonValue] = {}
        failure_attributes: dict[str, JsonValue] = {}
        if exception is not None:
            failure_outputs, failure_attributes = _lm_failure_details(exception)
            last_call.update(failure_outputs)
        self._last_call = last_call
        if self._metrics is not None:
            self._metrics.record_lm_call(
                role,
                self._recursive_depth,
                duration_ms=(time.perf_counter() - started_at) * 1000,
                usage=usage,
            )
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
    """
    Create a bounded, sanitized text preview of a value.

    Parameters:
        max_chars (int): Maximum requested length of the preview.

    Returns:
        str: Sanitized text representation of the value, limited to the configured length.
    """
    from fleet_rlm.observability.tracing import trace_preview_limit

    limit = trace_preview_limit(max_chars)
    return sanitize_public_text(str(value or ""), max_len=limit)


def _trace_payload_text(value: object) -> str:
    """Serialize a bounded readable payload without retaining provider objects."""
    try:
        return json.dumps(value, default=str, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _lm_input_profile(
    inputs: Mapping[str, Any],
    *,
    include_previews: bool = True,
) -> dict[str, JsonValue]:
    """
    Summarize the structural characteristics of language-model input context.

    Parameters:
        inputs (Mapping[str, Any]): Language-model input values.
        include_previews (bool): Whether to include bounded prompt and message previews.

    Returns:
        dict[str, JsonValue]: A profile containing available context sizes, message counts,
            keyword keys, and optionally bounded previews.
    """

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
    """Normalize LM callback outputs into a Mapping for profiling.

    Under the certified DSPy 3.3.1 legacy contract, ``on_lm_end`` delivers the
    post-processed outputs (a ``list[str | dict]``), never the raw LiteLLM
    ``ModelResponse``. Raw response-shape probing was removed in the P38
    contraction (P38-RLM-006/011).
    """
    if isinstance(outputs, Mapping):
        return outputs
    if isinstance(outputs, str):
        return {"content": outputs}

    model_dump = getattr(outputs, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:  # pragma: no cover - provider objects vary
            dumped = None
        if isinstance(dumped, Mapping):
            return dumped
    return None


def _lm_output_profile(
    outputs: Any,
    *,
    include_previews: bool = True,
) -> dict[str, JsonValue]:
    """Describe an LM response for tracing.

    Accepts the post-processed callback outputs or a bare string; both are
    normalized via ``_to_output_mapping`` so the profile reflects the real
    payload instead of collapsing to empty keys."""

    mapping = _to_output_mapping(outputs)
    if mapping is None:
        return {"response_keys": ()}
    profile: dict[str, JsonValue] = {"response_keys": tuple(sorted(str(key) for key in mapping)[:32])}
    response_chars = sum(len(str(value)) for value in mapping.values() if isinstance(value, str))
    if response_chars:
        profile["response_chars"] = response_chars
    if mapping and include_previews:
        profile["response_preview"] = _trace_preview(_trace_payload_text(mapping))
    return profile


def _latest_lm_telemetry(
    instance: Any,
    history_length: int | None,
    outputs: object = None,
) -> dict[str, JsonValue]:
    """Retrieve sanitized observed usage for the latest completed LM call.

    Under the certified DSPy 3.3.1 legacy forward contract, the truthful
    per-call usage lives on the LM history entry whose ``outputs`` value is
    the very object delivered to ``on_lm_end`` (P38-RLM-006/011: the typed
    ``LMResponse`` fallback and raw provider-response probing were removed).

    Parameters:
        instance (Any): Language-model instance whose call history is inspected.
        history_length (int | None): Starting history position for entries belonging to the current call.
        outputs (object): Callback output used to identify the matching history entry.

    Returns:
        dict[str, JsonValue]: Allowlisted usage data; empty when unavailable
        (missing usage is unavailable, never zero).
    """
    history = getattr(instance, "history", None)
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes, bytearray)):
        return {}
    start = history_length if history_length is not None else max(0, len(history) - 1)
    candidates = [entry for entry in history[start:] if isinstance(entry, Mapping)]
    matching = [entry for entry in candidates if _history_entry_matches_outputs(entry, outputs)]
    # Concurrent LM calls may append several entries after the same starting
    # index. Attribute telemetry only to the callback's exact returned object;
    # use the sole new entry as a compatibility fallback for synthetic LMs.
    if matching:
        selected = matching
    elif len(candidates) == 1:
        selected = candidates
    else:
        selected = []
    for entry in reversed(selected):
        usage = entry.get("usage")
        if not isinstance(usage, Mapping):
            dump = getattr(usage, "model_dump", None)
            usage = dump() if callable(dump) else None
        if isinstance(usage, Mapping):
            with contextlib.suppress(ValueError):
                return cast(
                    dict[str, JsonValue],
                    _safe_usage_entry(usage, path="lm_usage", filter_unknown=True),
                )
    return {}


def _history_entry_matches_outputs(entry: Mapping[str, Any], outputs: object) -> bool:
    """Match a DSPy 3.3.1 legacy history entry to its callback return value.

    ``BaseLM._process_lm_response`` stores the post-processed outputs in the
    entry and delivers that same object to ``on_lm_end``, so identity matching
    is the certified pairing (``dspy/clients/base_lm.py``).
    """
    if outputs is None:
        return False
    return entry.get("outputs") is outputs


def _mlflow_token_usage(usage: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """
    Map provider-specific token fields to standardized MLflow usage keys.

    Parameters:
        usage (Mapping[str, JsonValue]): Provider-reported token usage values.

    Returns:
        dict[str, JsonValue]: Token usage values keyed by MLflow's standard aggregate names.
    """
    from fleet_rlm.rlm.recursion import normalize_lm_token_usage

    return cast(dict[str, JsonValue], normalize_lm_token_usage(usage))


def _trace_failure_category(exc: BaseException) -> str:
    """Resolve failure classification lazily to preserve the package boundary."""
    from fleet_rlm.observability.diagnostics import trace_failure_category

    return trace_failure_category(exc)


_TRACE_FAILURE_DETAIL_MAX_CHARS = 300


def _lm_failure_details(exception: BaseException) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    """Build bounded, sanitized error detail for a failed LM span and summary.

    Traces such as tr-db96 surfaced a failed Root LM call with an empty status
    message and ``failure_category: unknown``: the span recorded that *a* call
    failed but never *why*. This supplies a bounded, credential-free breakdown
    (exception type, provider status class, and a short sanitized message) so a
    dead model is debuggable from the trace alone.

    Returns a ``(span_failure_outputs, span_failure_attributes)`` pair: the
    classified kinds ride on span attributes (immune to output re-sanitization
    rewriting), while a bounded ``detail`` preview stays in outputs for the UI.
    Everything is derived from ``sanitize_provider_message``-cleaned text —
    raw provider exception text never reaches the trace.
    """
    from fleet_rlm.daytona.errors import (
        classify_provider_error,
        provider_status_code,
        sanitize_provider_message,
    )

    cleaned = sanitize_provider_message(str(exception))
    detail = cleaned[:_TRACE_FAILURE_DETAIL_MAX_CHARS]
    status = provider_status_code(exception)
    status_category = f"{status // 100}xx" if isinstance(status, int) and 100 <= status <= 599 else "none"
    failure_outputs: dict[str, JsonValue] = {
        "failure_category": classify_provider_error(exception),
        "error_kind": type(exception).__name__,
        "provider_status_category": status_category,
    }
    span_failure_attributes: dict[str, JsonValue] = {
        "fleet.error.kind": type(exception).__name__,
        "fleet.error.category": classify_provider_error(exception),
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
    """Attach one run-local callback to the native action predictor.

    ``deadline`` is only an observability guard: late callbacks cannot create
    new Root action spans or publish post-deadline reasoning. The adapter and
    Turn-bound LM enforce the actual execution boundary.
    """
    from fleet_rlm.rlm.result import RLMConfigError

    if type(rlm) is not dspy.RLM:
        raise RLMConfigError("reasoning observation requires native dspy.RLM")
    predictor = getattr(rlm, "generate_action", None)
    if not isinstance(predictor, dspy.Predict):
        return
    callbacks = getattr(predictor, "callbacks", None)
    if isinstance(callbacks, list):
        predictor.callbacks = [callback for callback in callbacks if not isinstance(callback, _RLMReasoningCallback)]
    else:
        predictor.callbacks = []
    if observer is not None:
        predictor.callbacks.append(_RLMReasoningCallback(observer, max_chars=max_chars, deadline=deadline))


def daytona_provider_contract() -> Any:
    """Fail closed if DSPy attempts to construct a production interpreter.

    DSPy reads ``execution_instructions`` from this zero-argument callable while
    constructing its action Signature.  The callable never creates a provider
    resource; production always passes the already-acquired interpreter to
    ``RLM.acall``.
    """
    from fleet_rlm.rlm.result import RLMConfigError

    raise RLMConfigError("native RLM execution requires a caller-owned interpreter")


cast(Any, daytona_provider_contract).execution_instructions = DAYTONA_EXECUTION_INSTRUCTIONS


def wrap_final_output(value: Any) -> FinalOutput:
    """Wrap a SUBMIT payload in the pinned DSPy terminate signal."""
    return FinalOutput(value)


def is_final_output(value: Any) -> bool:
    """Return whether ``execute()`` returned a successful SUBMIT terminate signal."""
    return isinstance(value, FinalOutput)


__all__ = [
    "CERTIFIED_DSPY_VERSION",
    "DAYTONA_EXECUTION_INSTRUCTIONS",
    "PUBLIC_FINAL_OUTPUT_LABEL",
    "CodeExecutionError",
    "CodeInterpreter",
    "CodeInterpreterError",
    "FinalOutput",
    "FleetJSONAdapter",
    "ReasoningObserver",
    "UncertifiedDSpyVersionError",
    "assert_dspy_version",
    "bind_native_rlm_observer",
    "copy_output_fields",
    "daytona_provider_contract",
    "is_final_output",
    "needs_binding_refresh",
    "wrap_final_output",
]
