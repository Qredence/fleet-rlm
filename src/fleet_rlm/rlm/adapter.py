"""Compatibility boundary for DSPy 3.4.0's JSON action and iteration protocol.

Private upstream assumptions stay here and are certified by native RLM tests.
Provider retries remain owned by stock DSPy LMs.
"""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from typing import Any

import dspy
from dspy import BaseLM, Signature
from dspy.utils.exceptions import AdapterParseError, LMTimeoutError

from fleet_rlm.rlm.budget import DEFAULT_PARSE_RETRIES, AdapterBudget, FinalizationExhausted, TurnBudget
from fleet_rlm.rlm.submit_validation import is_finalization_action

RETRY_CORRECTION_FIELD = "fleet_retry_correction"
BUDGET_DIRECTIVE_FIELD = "fleet_budget_directive"
WRAP_UP_CORRECTION_FIELD = "fleet_wrap_up_correction"
_ALIBABA_DEEPSEEK_MODEL = "openai/deepseek-v4.1-flash"
_EMPTY_RESPONSE_MARKER = "The LM returned an empty or null response"


def _iteration_parts(inputs: Mapping[str, Any]) -> tuple[int, int] | None:
    """Parse DSPy's action iteration marker emitted by ``dspy.RLM``."""
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


def _is_empty_adapter_parse(exc: BaseException) -> bool:
    return _EMPTY_RESPONSE_MARKER in str(getattr(exc, "message", "") or exc)


def _retry_correction_feedback(attempt: int, exc: AdapterParseError) -> str:
    """Build bounded corrective feedback for one failed action attempt."""
    if _is_empty_adapter_parse(exc):
        if attempt >= 3:
            return (
                f"Correction (attempt {attempt}): the previous responses produced no parseable output "
                "because generation exhausted the output-token budget with reasoning prose before "
                "emitting the action. Respond now with ONLY one JSON object containing exactly the "
                "required output fields. Emit zero reasoning text, zero prose, zero markdown, zero "
                "code fences."
            )
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


def _append_input_field(
    signature: type[Signature],
    inputs: Mapping[str, Any],
    *,
    preferred_name: str,
    description: str,
    value: str,
) -> tuple[type[Signature], dict[str, Any], str]:
    field_name, suffix = preferred_name, 1
    while field_name in signature.fields:
        suffix += 1
        field_name = f"{preferred_name}_{suffix}"
    extended = signature.append(field_name, dspy.InputField(desc=description))
    extended_inputs = {**inputs, field_name: value}
    return extended, extended_inputs, field_name


def _retry_call_arguments(
    signature: type[Signature],
    inputs: dict[str, Any],
    attempt: int,
    exc: AdapterParseError,
) -> tuple[type[Signature], dict[str, Any]]:
    retry_signature, retry_inputs, _ = _append_input_field(
        signature,
        inputs,
        preferred_name=RETRY_CORRECTION_FIELD,
        description="Bounded corrective feedback for the previous failed attempt; follow it.",
        value=_retry_correction_feedback(attempt, exc),
    )
    return retry_signature, retry_inputs


def _budget_directive(*, final_iteration: bool = False) -> str:
    reason = "Final iteration reached" if final_iteration else "Wrap-up required"
    return (
        f"{reason}. Submit your best-supported answer now "
        "using evidence already gathered. Do not explore or call tools. "
        "Return data-only answer assignments followed by one SUBMIT(...) call, or just SUBMIT(...). "
        "No imports, print, tool calls, or other statements."
    )


def _wrap_up_correction(reason: str) -> str:
    return (
        "Wrap-up correction: the previous action was not a compliant finalization action "
        f"({reason}). Return data-only answer assignments followed by one SUBMIT(...) call, "
        "or just SUBMIT(...). No imports, print, tool calls, or other statements."
    )


def _action_code(response: object) -> object:
    if not isinstance(response, Sequence) or isinstance(response, (str, bytes, bytearray)) or not response:
        return None
    first = response[0]
    return first.get("code") if isinstance(first, Mapping) else None


class FleetJSONAdapter(dspy.JSONAdapter):
    """The pinned JSON action protocol plus a bounded corrective re-ask."""

    def _prepare_response_format(self, lm: Any, lm_kwargs: dict[str, Any], signature: Signature) -> None:
        # The selected DashScope endpoint accepted json_object in a live
        # provider probe but rejected json_schema. DSPy's model catalog does
        # not advertise either mode, so request only the proven wire format.
        base_url = str(getattr(lm, "kwargs", {}).get("api_base", "")).rstrip("/")
        if getattr(lm, "model", None) == _ALIBABA_DEEPSEEK_MODEL and base_url.endswith("/compatible-mode/v1"):
            lm_kwargs["response_format"] = {"type": "json_object"}
            return
        super()._prepare_response_format(lm, lm_kwargs, signature)

    def __init__(
        self,
        *,
        max_parse_retries: int = DEFAULT_PARSE_RETRIES,
        budget: TurnBudget | None = None,
        root_finalization: bool = True,
    ) -> None:
        super().__init__()
        self._budget = AdapterBudget(max_parse_retries=max_parse_retries, turn=budget)
        self._root_finalization = root_finalization

    @property
    def _wrap_up_attempts(self) -> int:
        return self._budget.finalization_used

    def _lm_for_request(self, lm: Any) -> Any:
        # The Turn-scoped LM is already an isolated copy owned by this Turn, so
        # the call view is the LM itself and no per-request wrapper is created.
        return lm

    def _enter_wrap_up(self, *, rejection_reason: str | None = None) -> None:
        self._budget.enter_wrap_up(rejection_reason=rejection_reason)

    def wrap_up_summary(self) -> dict[str, Any]:
        return dict(self._budget.wrap_up_summary())

    def repair_summary(self) -> dict[str, Any]:
        """Bounded provider-repair diagnostics for the enclosing Turn span.

        Kept separate from ``wrap_up_summary`` because that contract describes
        the final-answer reserve, not adapter parse-repair activity.
        """
        return {"parse_repairs_used": self._budget.parse_repairs_used}

    def _next_wrap_up_attempt(self) -> None:
        self._budget.reclassify_late_response(can_finalize=self._root_finalization)

    def _wrap_up_required(self, inputs: Mapping[str, Any]) -> bool:
        """Wrap up on the final iteration."""
        return _iteration_is_action(inputs) and _iteration_is_final(inputs)

    def _with_wrap_up_directive(
        self,
        signature: type[Signature],
        inputs: Mapping[str, Any],
        *,
        field_name: str | None = None,
    ) -> tuple[type[Signature], dict[str, Any], str]:
        directive = _budget_directive(final_iteration=_iteration_is_final(inputs))
        if field_name is not None and field_name in signature.fields:
            updated = dict(inputs)
            updated[field_name] = directive
            return signature, updated, field_name
        return _append_input_field(
            signature,
            inputs,
            preferred_name=BUDGET_DIRECTIVE_FIELD,
            description="Mandatory final-answer budget directive; follow it exactly.",
            value=directive,
        )

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
        machine = self._repair_steps(lm, lm_kwargs, signature, inputs)
        try:
            request = next(machine)
            while True:
                call_lm, call_kwargs, request_signature, request_inputs = request
                try:
                    response = super().__call__(call_lm, call_kwargs, request_signature, demos, request_inputs)
                except (AdapterParseError, LMTimeoutError, TimeoutError) as exc:
                    try:
                        request = machine.throw(exc)
                    except StopIteration as done:
                        return done.value
                else:
                    try:
                        request = machine.send(response)
                    except StopIteration as done:
                        return done.value
        finally:
            machine.close()

    async def acall(
        self,
        lm: BaseLM,
        lm_kwargs: dict[str, Any],
        signature: type[Signature],
        demos: list[dict[str, Any]],
        inputs: dict[str, Any],
    ) -> list[dict[str, Any]]:
        machine = self._repair_steps(lm, lm_kwargs, signature, inputs)
        try:
            request = next(machine)
            while True:
                call_lm, call_kwargs, request_signature, request_inputs = request
                try:
                    response = await super().acall(call_lm, call_kwargs, request_signature, demos, request_inputs)
                except (AdapterParseError, LMTimeoutError, TimeoutError) as exc:
                    try:
                        request = machine.throw(exc)
                    except StopIteration as done:
                        return done.value
                else:
                    try:
                        request = machine.send(response)
                    except StopIteration as done:
                        return done.value
        finally:
            machine.close()

    def _repair_steps(
        self,
        lm: Any,
        lm_kwargs: dict[str, Any],
        signature: type[Signature],
        inputs: dict[str, Any],
    ) -> Generator[
        tuple[Any, dict[str, Any], type[Signature], dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        """Serve one action, applying bounded parse repair and wrap-up correction.

        Wrap-up is keyed to the RLM iteration count rather than a wall-clock
        reserve: it begins on the final iteration.
        """
        lm = self._lm_for_request(lm)
        attempt = 0
        base_signature, base_inputs = signature, dict(inputs)
        wrap_up = False
        request_signature, request_inputs = signature, dict(inputs)
        directive_field: str | None = None
        while True:
            action = _iteration_is_action(base_inputs)
            if action and not wrap_up and self._wrap_up_required(base_inputs):
                wrap_up = True
                self._enter_wrap_up()
            if wrap_up:
                request_signature, request_inputs, directive_field = self._with_wrap_up_directive(
                    request_signature, request_inputs, field_name=directive_field
                )
            try:
                response = yield lm, dict(lm_kwargs), request_signature, request_inputs
            except AdapterParseError as exc:
                if wrap_up:
                    if not self._budget.can_finalize():
                        raise FinalizationExhausted(
                            "wrap-up finalization attempts exhausted before a parseable action"
                        ) from exc
                    self._budget.set_wrap_up_rejection("unparseable_json")
                    # Consume a finalization slot for the correction call. DSPy
                    # admission used to charge this implicitly; without it the
                    # loop would re-ask the provider forever.
                    self._next_wrap_up_attempt()
                    request_signature, request_inputs = self._with_wrap_up_correction(
                        request_signature, request_inputs, reason="unparseable JSON"
                    )
                    continue
                if not self._budget.can_repair(attempt):
                    raise
                attempt += 1
                # A repair re-ask is a full provider call. Record it so the Turn
                # span can report how much of the output budget was spent
                # recovering an action that could not be parsed.
                self._budget.note_parse_repair()
                request_signature, request_inputs = _retry_call_arguments(base_signature, base_inputs, attempt, exc)
                continue
            if wrap_up and action and not is_finalization_action(_action_code(response)):
                self._budget.set_wrap_up_rejection("exploration_or_additional_code")
                if not self._budget.can_finalize():
                    raise FinalizationExhausted("wrap-up finalization attempts exhausted before a compliant SUBMIT")
                self._next_wrap_up_attempt()
                request_signature, request_inputs = self._with_wrap_up_correction(
                    request_signature, request_inputs, reason="exploration or additional code"
                )
                continue
            return response
