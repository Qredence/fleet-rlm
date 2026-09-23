"""Native DSPy RLM construction, LMs, signatures, instructions, and input models."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections.abc import Callable, Generator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID

import dspy
from dspy.utils.exceptions import AdapterParseError, LMRateLimitError, LMServerError, LMTimeoutError, LMTransportError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fleet_rlm.config.settings import LLMRoleSettings, Settings
from fleet_rlm.paths import DEFAULT_VOLUME_MOUNT_PATH, validate_mount_path
from fleet_rlm.rlm.budget import (
    DEFAULT_PARSE_RETRIES,
    AdapterBudget,
    BudgetDimension,
    FinalizationExhausted,
    ProviderAdmission,
    TurnBudget,
)
from fleet_rlm.rlm.compat_3_3_1 import (
    BaseLM,
    Signature,
    _is_empty_adapter_parse,
    _iteration_is_action,
    _iteration_is_final,
    daytona_provider_contract,
)
from fleet_rlm.rlm.result import RLMConfigError, RLMModelBundleError
from fleet_rlm.rlm.submit_validation import is_finalization_action
from fleet_rlm.workspace.models import (
    UNAVAILABLE_WORKSPACE_CAPABILITY,
    WORKSPACE_MEMORY_INJECTION_TAIL_BYTES,
    WorkspaceCapabilityMetadata,
)

if TYPE_CHECKING:
    from fleet_rlm.sessions.context import SessionContextManifest
    from fleet_rlm.sessions.history_transport import CommittedSessionHistory

# ---------------------------------------------------------------------------
# Bounded re-ask adapter for the pinned JSON action protocol
# ---------------------------------------------------------------------------

RETRY_CORRECTION_FIELD = "fleet_retry_correction"
BUDGET_DIRECTIVE_FIELD = "fleet_budget_directive"
WRAP_UP_CORRECTION_FIELD = "fleet_wrap_up_correction"


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


def _budget_directive(remaining: float, *, attempts_exhausted: bool = False, final_iteration: bool = False) -> str:
    seconds = max(0, int(remaining))
    if attempts_exhausted:
        reason = "Exploration attempt budget exhausted"
    elif final_iteration:
        reason = "Final iteration reached"
    else:
        reason = "Time budget nearly exhausted"
    return (
        f"{reason} ({seconds}s remaining). Submit your best-supported answer now "
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

    def __init__(
        self,
        *,
        max_parse_retries: int = DEFAULT_PARSE_RETRIES,
        deadline: float | None = None,
        wrap_up_seconds: float = 0.0,
        budget: TurnBudget | None = None,
    ) -> None:
        super().__init__()
        self._budget = AdapterBudget(
            deadline=deadline,
            reserve_seconds=wrap_up_seconds,
            max_parse_retries=max_parse_retries,
            turn=budget,
        )
        self._explicit_budget = budget is not None

    @property
    def _wrap_up_seconds(self) -> float:
        return self._budget.reserve_seconds

    @property
    def _wrap_up_attempts(self) -> int:
        return self._budget.finalization_used

    def _remaining(self) -> float | None:
        return self._budget.remaining()

    def _lm_for_request(self, lm: BaseLM, *, action: bool, wrap_up: bool) -> BaseLM:
        if isinstance(lm, DeadlineLMProxy) and lm.budget is not None and lm.budget is not self._budget.turn:
            if self._explicit_budget or any(self._budget.turn.snapshot().values()):
                raise ValueError("adapter cannot switch Turn budgets")
            self._budget.turn = lm.budget
        return DeadlineLMProxy.for_adapter(lm, self._budget, action=action, wrap_up=wrap_up)

    def _enter_wrap_up(self, remaining: float, *, rejection_reason: str | None = None) -> None:
        self._budget.enter_wrap_up(remaining, rejection_reason=rejection_reason)

    def wrap_up_summary(self) -> dict[str, Any]:
        return dict(self._budget.wrap_up_summary())

    def repair_summary(self) -> dict[str, Any]:
        """Bounded provider-repair diagnostics for the enclosing Turn span.

        Kept separate from ``wrap_up_summary`` because that contract describes
        the final-answer reserve, not adapter parse-repair activity.
        """
        return {"parse_repairs_used": self._budget.parse_repairs_used}

    def _next_wrap_up_attempt(self, lm: BaseLM) -> None:
        self._budget.reclassify_late_response(can_finalize=isinstance(lm, DeadlineLMProxy) and lm.can_finalize)

    def _wrap_up_required(self, inputs: Mapping[str, Any], remaining: float | None) -> bool:
        return bool(
            remaining is not None
            and self._wrap_up_seconds > 0
            and _iteration_is_action(inputs)
            and (
                remaining <= self._wrap_up_seconds
                or self._budget.turn.exploration_exhausted()
                or _iteration_is_final(inputs)
            )
        )

    def _with_wrap_up_directive(
        self,
        signature: type[Signature],
        inputs: Mapping[str, Any],
        remaining: float,
        *,
        field_name: str | None = None,
    ) -> tuple[type[Signature], dict[str, Any], str]:
        directive = _budget_directive(
            remaining,
            attempts_exhausted=self._budget.turn.exploration_exhausted(),
            final_iteration=_iteration_is_final(inputs),
        )
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
        lm: BaseLM,
        lm_kwargs: dict[str, Any],
        signature: type[Signature],
        inputs: dict[str, Any],
    ) -> Generator[
        tuple[BaseLM, dict[str, Any], type[Signature], dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        lm = self._lm_for_request(lm, action=False, wrap_up=False)
        attempt = 0
        base_signature, base_inputs = signature, dict(inputs)
        wrap_up = False
        request_signature, request_inputs = signature, dict(inputs)
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
                    request_signature, request_inputs, remaining, field_name=directive_field
                )
            call_lm = self._lm_for_request(lm, action=action, wrap_up=wrap_up)
            try:
                response = yield call_lm, dict(lm_kwargs), request_signature, request_inputs
            except (LMTimeoutError, TimeoutError):
                if not wrap_up and action and self._wrap_up_seconds > 0:
                    boundary_remaining = self._remaining()
                    if boundary_remaining is not None and boundary_remaining <= self._wrap_up_seconds:
                        wrap_up = True
                        self._enter_wrap_up(boundary_remaining)
                        request_signature, request_inputs, directive_field = self._with_wrap_up_directive(
                            request_signature, request_inputs, boundary_remaining, field_name=directive_field
                        )
                        continue
                raise
            except AdapterParseError as exc:
                if wrap_up:
                    if not self._budget.can_finalize():
                        raise FinalizationExhausted(
                            "wrap-up finalization attempts exhausted before a parseable action"
                        ) from exc
                    self._budget.set_wrap_up_rejection("unparseable_json")
                    request_signature, request_inputs = self._with_wrap_up_correction(
                        request_signature, request_inputs, reason="unparseable JSON"
                    )
                    continue
                if action and self._wrap_up_seconds > 0:
                    boundary_remaining = self._remaining()
                    if boundary_remaining is not None and boundary_remaining <= self._wrap_up_seconds:
                        wrap_up = True
                        self._enter_wrap_up(boundary_remaining, rejection_reason="unparseable_json")
                        self._next_wrap_up_attempt(call_lm)
                        request_signature, request_inputs, directive_field = self._with_wrap_up_directive(
                            request_signature, request_inputs, boundary_remaining, field_name=directive_field
                        )
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
            if remaining is not None:
                after_response = self._remaining()
                if not wrap_up and action and after_response is not None and after_response <= self._wrap_up_seconds:
                    wrap_up = True
                    self._enter_wrap_up(after_response)
                    self._next_wrap_up_attempt(call_lm)
                    if is_finalization_action(_action_code(response)):
                        return response
                    request_signature, request_inputs, directive_field = self._with_wrap_up_directive(
                        request_signature, request_inputs, after_response, field_name=directive_field
                    )
                if wrap_up and action and not is_finalization_action(_action_code(response)):
                    self._budget.set_wrap_up_rejection("exploration_or_additional_code")
                    if not self._budget.can_finalize():
                        raise FinalizationExhausted("wrap-up finalization attempts exhausted before a compliant SUBMIT")
                    request_signature, request_inputs = self._with_wrap_up_correction(
                        request_signature, request_inputs, reason="exploration or additional code"
                    )
                    continue
                return response
            return response


# ---------------------------------------------------------------------------
# Input Models
# ---------------------------------------------------------------------------


class FleetInputModel(BaseModel):
    """Immutable, closed DTO shared only by the RLM input boundary."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class TurnPreviewInput(FleetInputModel):
    ordinal: int = Field(ge=1)
    role: Literal["user", "assistant"]
    preview: str = Field(max_length=320)


class WorkspaceCapabilityInput(FleetInputModel):
    available: bool
    root: Literal["."]
    instructions: str


class WorkspaceMemoryInput(FleetInputModel):
    """Bounded untrusted Workspace Memory tail injected at Turn start."""

    tail: str = Field(min_length=1, max_length=WORKSPACE_MEMORY_INJECTION_TAIL_BYTES)


class SessionContextInput(FleetInputModel):
    session_id: UUID
    checkpoint_version: int = Field(ge=0)
    message_count: int = Field(ge=0)
    recent: tuple[TurnPreviewInput, ...] = Field(max_length=6)
    workspace: WorkspaceCapabilityInput
    workspace_memory: WorkspaceMemoryInput | None = None
    active_task: str | None = Field(default=None, max_length=2048)


class SkillCardInput(FleetInputModel):
    id: UUID
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=512)
    scope: Literal["system"]
    version: str = Field(min_length=1, max_length=64)
    trust: Literal["system"]
    affordances: tuple[str, ...] = Field(max_length=8)
    resources_available: bool


class AttachmentInput(FleetInputModel):
    id: UUID
    filename: str = Field(min_length=1, max_length=255)
    content_type: str | None = None
    byte_size: int = Field(ge=0)
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_LEGACY_LLM_API_KEY_ENV = "FLEET_OPENAI_API_KEY"
_AI_GATEWAY_PATH = "/ai-gateway/openai/v1"
_MAX_REQUEST_CHARS = 100_000
_MAX_CONTEXT_ATTACHMENT_COUNT = 32
_MAX_PREVIEW_CHARS = 500


# ---------------------------------------------------------------------------
# Execution options & Program Specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RLMOptions:
    """Native DSPy limits plus the Fleet-owned public result limit."""

    max_iters: int = 20
    max_llm_calls: int = 50
    max_output_chars: int = 10_000
    max_final_output_chars: int = 10_000

    def __post_init__(self) -> None:
        for name, value in (
            ("max_iters", self.max_iters),
            ("max_llm_calls", self.max_llm_calls),
            ("max_output_chars", self.max_output_chars),
            ("max_final_output_chars", self.max_final_output_chars),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise RLMConfigError(f"{name} must be a positive integer, got {value!r}")


def rlm_options(settings: Settings) -> RLMOptions:
    return RLMOptions(
        max_iters=settings.rlm_max_iters,
        max_llm_calls=settings.rlm_max_llm_calls,
        max_output_chars=settings.rlm_max_output_chars,
        max_final_output_chars=settings.rlm_max_final_output_chars,
    )


# ---------------------------------------------------------------------------
# Instructions & Signatures
# ---------------------------------------------------------------------------

BASE_RLM_INSTRUCTIONS = """Recursive turn: choose the smallest sufficient execution path, verify, and submit.

``dspy.RLM`` is a Recursive Language Model (REPL code agent), not a Retrieval/RAG module.
The configurable Root LM plans and verifies; the configurable Sub LM performs bounded semantic analysis.
Neither model substitutes for deterministic computation in the REPL."""

REPL_RLM_INSTRUCTIONS = """Follow this order and stop as soon as the request is answered with sufficient evidence:"""

TOOL_RLM_INSTRUCTIONS = """1. Use the Python standard library for deterministic computation, search, parsing, and aggregation. Keep each
   intermediate code action concise (prefer a few thousand characters; never paste a long report or the complete
   request as unused text). When the request specifies exact Python statements or Sub-LM prompt strings, emit those
   statements in that order with those strings unchanged; do not omit listed accumulator updates or rewrite the
   prompts. Never repeat an identical interpreter action: use its output, choose a different action, or
   call ``SUBMIT`` when sufficient. Store large values in variables or Session Workspace. If the request contains a
   relevant public HTTPS URL, call ``fetch_url`` once. For inline sources, assign ``content`` to a Python
   variable; for large sources, use the returned ``workspace_path`` with bounded Workspace reads. Never print
   the complete value. Validate the result is a mapping and handle either ``content`` or a workspace reference.
   Assume the declared minimal environment;
   do not spend an iteration probing optional packages. For high-precision numerical work, use the smallest
   sufficient precision (target index plus a small guard band), reuse computed variables across iterations,
   and never recompute a cached prefix. If the completed answer would exceed the declared inline output budget,
   call ``create_artifact(kind="markdown", content=full_report, title=...)`` once, require ``ok == True``, then
   ``SUBMIT`` a concise executive summary. The Artifact is the complete durable answer; do not paste it inline.
2. Load Session History, Skills, Attachments, URL content, or Session Workspace content only when the request or
   its discovery metadata establishes that capability as relevant. Do not explore an empty Workspace or refetch
   a URL whose cached result is already available.
3. Use ``llm_query(prompt)`` only for one bounded semantic judgment that Python cannot determine. If the request
   already specifies the prompt string, pass that string unchanged.
4. Use ``llm_query_batched(prompts)`` for multiple independent semantic judgments. When composing prompts, make each
   self-contained. When the request already specifies the prompt strings, pass them unchanged and in the given order.
   Prefer the cheapest sufficient mechanism."""

# Fleet-provided recursion, URL-fetch, and Workspace tools require executable
# host bindings. A remote Sandbox currently receives source and serializable
# values only, so those Fleet tools must not be advertised when bindings are
# unavailable. This does not suppress DSPy's native semantic tools: dspy.RLM
# adds them independently through its action template and execution context.
TOOL_RLM_INSTRUCTIONS_NO_DISPATCH = """1. Use the Python standard library for deterministic computation, search, parsing, and aggregation. Keep each
   intermediate code action concise (prefer a few thousand characters; never paste a long report or the complete
   request as unused text). When the request specifies exact Python statements, emit those statements in that order
   unchanged. Never repeat an identical interpreter action: use its output, choose a different action, or call
   ``SUBMIT`` when sufficient. Store large values in variables. Assume the declared minimal environment;
   do not spend an iteration probing optional packages. For high-precision numerical work, use the smallest
   sufficient precision (target index plus a small guard band), reuse computed variables across iterations,
   and never recompute a cached prefix.
2. Load Session History, Skills, or Attachments only when the request or its discovery metadata establishes that
   capability as relevant.
3. This runtime dispatches no Fleet recursion, URL-fetch, or Workspace host tool. Do not probe for
   ``rlm_query``, ``rlm_query_batched``, ``fetch_url``, or Workspace tools. Answer from the request text,
   the Sandbox filesystem, and deterministic Python; if the request demands one of those Fleet capabilities,
   say so plainly in the ``answer`` instead of searching for the tool."""

WORKSPACE_BATCH_RLM_INSTRUCTIONS = """When several independently selected Session Workspace files are relevant, use
``read_workspace_text_batch`` rather than serial ``read_workspace_text`` calls. List or stat first, select only
relevant paths, keep each page bounded, and never crawl an entire Workspace."""

WORKSPACE_MUTATION_TOOL_NAMES = frozenset(
    {
        "append_workspace_text",
        "write_workspace_text",
        "publish_workspace_artifact",
    }
)

WORKSPACE_MUTATION_RLM_INSTRUCTIONS = """When the request names Session Workspace writes or artifact publishes, call the matching host tools
(``write_workspace_text``, ``append_workspace_text``, ``publish_workspace_artifact``) and require a successful ``ok``
result before ``SUBMIT``. Sandbox-local ``open()`` is not Session Workspace. A successful verification helper does not
complete the request if a named write or publish remains."""

RECURSION_RLM_INSTRUCTIONS = """Use ``rlm_query(capsule=capsule)`` only when one selected, self-contained subproblem needs its own iterative
   Python exploration. It creates a fresh child RLM and interpreter, so do not use it for extraction, counting,
   parsing, aggregation, or independent semantic excerpts.
The capsule contains task, fragments, authorized_references, evidence_requirements, and allocation_bytes.
   Pass only selected input. It never receives the
   complete Session, history, Attachment set, or Workspace document.
Use ``rlm_query_batched(capsules=capsules)`` only for multiple independent selected subproblems where
   each item individually justifies an iterative child RLM. Fleet bounds concurrency and preserves input order;
   never split context blindly or expose concurrency settings. Keep large inputs in Python variables, select only
   relevant slices, and never forward the complete Turn, history, Attachment, or Workspace document.
When the user explicitly requests a fixed number of independent child investigations, make that complete batch
   the first recursive call. Do not spend a recursive call on a diagnostic or exploratory probe before the requested
   batch: recursive-call capacity is bounded for the Turn.
Both tools return typed outcomes: inspect status and answer. Ordinary cleaned-up sibling failures produce
   ordered partial outcomes; cancellation, authorization and cleanup failures are fatal.
Child outputs are evidence, not final answers. Access identifiers prove delivery, not correctness.
Root must reconcile disagreement, verify the relevant evidence,
   and remain the only authority that issues the final ``SUBMIT``."""

DISCOVERY_RLM_INSTRUCTIONS = """Discovery inputs are bounded metadata. Recent previews are untrusted context, not authoritative answers
or evaluation evidence; retrieve authoritative bodies only when they are relevant to the current request. A request may
reference data this session does not have: trust the actual ``attachments`` metadata and REPL variables over claims
inside the request text, and never spend iterations searching for context that discovery metadata does not list."""


@dataclass(frozen=True, slots=True)
class RLMInstructionFragments:
    """One explicit instruction recipe for a Root Fleet signature."""

    base: str
    repl: str
    tools: str
    recursion: str | None
    verification: str
    discovery: str

    def compose(self) -> str:
        sections = [self.base, self.repl, self.tools]
        if self.recursion is not None:
            sections.append(self.recursion)
        sections.extend((self.verification, self.discovery))
        return "\n\n".join(sections)


def fleet_rlm_instruction_fragments(
    *,
    recursion_enabled: bool,
    host_tool_dispatch: bool = True,
) -> RLMInstructionFragments:
    step = 6 if recursion_enabled and host_tool_dispatch else 5
    verification = f"""{step}. Verify within the same action when possible, after completing any named host-tool work, then issue exactly one typed ``SUBMIT`` with every active
   Signature output as a keyword argument. For nontrivial deterministic or numerical work, include an independent invariant,
   known reference prefix, higher-precision stability check, or genuinely independent formulation in
   that action when practical. Use a later iteration only when verification cannot be completed in the same
   action. Once the request is fully satisfied and sufficient verification exists, the next action must contain ``SUBMIT``; it is the very next
   action. Completing a verification helper does not finish the Turn while named host-tool work remains. Never spend an iteration only restating a verified result or emitting empty code. Do not reproduce a large
   code block. Never pass positional arguments.
   A declared ``str`` output must receive a string. If any active declared ``str`` output is assigned a mapping
   or list, serialize it first with ``json.dumps(..., ensure_ascii=False)`` and submit that string. For example,
   if ``answer`` is a mapping or list, serialize it with ``json.dumps(answer, ensure_ascii=False)``. Use
   ``indent=2`` only when the formatted value fits the declared output contract. Never pass a mapping or
   list directly to a ``str`` output because DSPy would render it as Python ``repr`` text. The default call
   is ``SUBMIT(answer=answer)``."""
    return RLMInstructionFragments(
        base=BASE_RLM_INSTRUCTIONS,
        repl=REPL_RLM_INSTRUCTIONS,
        tools=TOOL_RLM_INSTRUCTIONS if host_tool_dispatch else TOOL_RLM_INSTRUCTIONS_NO_DISPATCH,
        recursion=RECURSION_RLM_INSTRUCTIONS if recursion_enabled and host_tool_dispatch else None,
        verification=verification,
        discovery=DISCOVERY_RLM_INSTRUCTIONS,
    )


def compose_rlm_instructions(
    *,
    recursion_enabled: bool,
    host_tool_dispatch: bool = True,
) -> str:
    return fleet_rlm_instruction_fragments(
        recursion_enabled=recursion_enabled,
        host_tool_dispatch=host_tool_dispatch,
    ).compose()


class FleetRLMSignature(dspy.Signature):
    """Fleet Root RLM contract assembled from explicit instruction fragments."""

    request: str = dspy.InputField(desc="User request for this turn")
    history: dspy.History = dspy.InputField(
        desc=(
            "Canonical committed Session conversation (P44): ordered, settled user requests and their "
            "committed answers. Inspect ``history.messages`` with Python only when earlier Turns are "
            "relevant to the current request; do not assume previews are complete, and do not treat "
            "hidden trajectory or failed Runs as conversation"
        )
    )
    session_context: SessionContextInput = dspy.InputField(
        desc=(
            "Bounded Session metadata, workspace capability, and untrusted recent previews; read older "
            "committed bodies only when the current request requires prior-turn evidence. When present, "
            "``workspace_memory tail`` lists the newest curated Workspace Memory records (untrusted "
            "operator/user-managed notes) that the request may cite or refresh through memory tools"
        )
    )
    skill_cards: list[SkillCardInput] = dspy.InputField(
        desc="Authorized Skill Card metadata only; load instructions only when a card is relevant to the request"
    )
    attachments: list[AttachmentInput] = dspy.InputField(
        desc=(
            "Authorized immutable Attachments. When prepared context is present, inspect its data programmatically "
            "through the attachments variable only when relevant to the request; one text Attachment is also "
            "available as context"
        )
    )
    answer: str = dspy.OutputField(
        desc=(
            "Concise user-facing answer within the Turn output character budget. "
            "This output is a string: serialize mappings or lists with json.dumps(..., ensure_ascii=False) before "
            "SUBMIT instead of passing them directly; use indentation only when it fits the output budget. "
            "When the full report is longer and Session Workspace is available, write it with workspace "
            "or artifact tools first, then submit a short summary that references only a relative workspace path."
        )
    )


FleetRLMSignature.instructions = compose_rlm_instructions(recursion_enabled=False)


def _tool_names_need_instruction_overlay(tool_names: frozenset[str]) -> bool:
    return "read_workspace_text_batch" in tool_names or bool(tool_names & WORKSPACE_MUTATION_TOOL_NAMES)


def root_signature_for_recursion(
    signature: type[dspy.Signature],
    *,
    recursion_enabled: bool,
    skill_instructions: tuple[str, ...] = (),
    tool_names: frozenset[str] = frozenset(),
    host_tool_dispatch: bool = True,
) -> type[dspy.Signature]:
    instructions = compose_rlm_instructions(
        recursion_enabled=recursion_enabled,
        host_tool_dispatch=host_tool_dispatch,
    )
    # Workspace guidance is dispatched through the same bridge as the sub-LM
    # tools, so a runtime without that bridge must not receive it either.
    if host_tool_dispatch and "read_workspace_text_batch" in tool_names:
        instructions += "\n\n" + WORKSPACE_BATCH_RLM_INSTRUCTIONS
    if host_tool_dispatch and tool_names & WORKSPACE_MUTATION_TOOL_NAMES:
        instructions += "\n\n" + WORKSPACE_MUTATION_RLM_INSTRUCTIONS
    if skill_instructions:
        instructions += "\n\n" + "\n\n".join(skill_instructions)
    return signature.with_instructions(instructions)


# ---------------------------------------------------------------------------
# Attachment Context Capsule & Input Kwargs Builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttachmentContextEntry:
    """Private host-generated descriptor for one staged immutable Attachment."""

    attachment_id: UUID
    filename: str
    content_type: str | None
    byte_size: int
    checksum_sha256: str
    sandbox_path: str

    def __post_init__(self) -> None:
        if not self.filename or len(self.filename) > 255:
            raise ValueError("attachment filename is invalid")
        if self.byte_size <= 0:
            raise ValueError("attachment byte size is invalid")
        if len(self.checksum_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.checksum_sha256):
            raise ValueError("attachment checksum is invalid")
        if not PurePosixPath(self.sandbox_path).is_absolute():
            raise ValueError("attachment sandbox path is invalid")


def _materialize_context_manifest(
    raw_manifest: bytes | str,
    *,
    trusted_mount_root: str,
    expected_manifest_sha256: str,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    try:
        raw = raw_manifest.encode("utf-8") if isinstance(raw_manifest, str) else bytes(raw_manifest)
        if hashlib.sha256(raw).hexdigest() != expected_manifest_sha256:
            raise ValueError
        manifest = json.loads(raw.decode("utf-8"))
        mount_root = os.path.realpath(str(trusted_mount_root))
        if os.path.realpath(str(manifest["mount_root"])) != mount_root:
            raise ValueError
        entries = list(manifest["entries"])
    except Exception as exc:
        raise ValueError("context manifest is invalid") from exc

    values: list[dict[str, Any]] = []
    accesses: list[str] = []
    for entry in entries:
        try:
            path = os.path.realpath(str(entry["sandbox_path"]))
            expected_size = int(entry["byte_size"])
            expected_sha = str(entry["checksum_sha256"])
            if os.path.commonpath((mount_root, path)) != mount_root or path == mount_root:
                raise ValueError
            with open(path, "rb") as handle:
                body = handle.read(expected_size + 1)
            if len(body) != expected_size or hashlib.sha256(body).hexdigest() != expected_sha:
                raise ValueError
            try:
                data: str | bytes = body.decode("utf-8")
                encoding = "utf-8"
                if "\x00" in data:
                    raise UnicodeDecodeError("utf-8", body, 0, 1, "nul")
            except UnicodeDecodeError:
                data = body
                encoding = "bytes"
            attachment_id = str(entry["attachment_id"])
            values.append(
                {
                    "id": attachment_id,
                    "filename": str(entry["filename"]),
                    "content_type": entry.get("content_type"),
                    "byte_size": expected_size,
                    "data": data,
                    "encoding": encoding,
                }
            )
            accesses.append(attachment_id)
        except Exception as exc:
            raise ValueError("prepared context failed integrity verification") from exc
    return values, tuple(accesses)


@dataclass(frozen=True, slots=True)
class AttachmentContextCapsule(dspy.SandboxSerializable):
    """Compact manifest for authorized immutable context already staged in a Volume."""

    entries: tuple[AttachmentContextEntry, ...]
    mount_root: str = DEFAULT_VOLUME_MOUNT_PATH

    def __post_init__(self) -> None:
        mount = validate_mount_path(self.mount_root)
        object.__setattr__(self, "mount_root", str(mount))
        if not self.entries or len(self.entries) > _MAX_CONTEXT_ATTACHMENT_COUNT:
            raise ValueError("attachment context count is invalid")
        for entry in self.entries:
            path = PurePosixPath(entry.sandbox_path)
            if not path.is_relative_to(mount) or path == mount:
                raise ValueError("attachment sandbox path is outside the mounted Volume")

    def sandbox_setup(self) -> str:
        return ""

    def to_sandbox(self) -> bytes:
        payload = {
            "mount_root": self.mount_root,
            "entries": [
                {
                    "attachment_id": str(entry.attachment_id),
                    "filename": entry.filename,
                    "content_type": entry.content_type,
                    "byte_size": entry.byte_size,
                    "checksum_sha256": entry.checksum_sha256,
                    "sandbox_path": entry.sandbox_path,
                }
                for entry in self.entries
            ],
        }
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
        return (
            "try:\n"
            f"    {var_name} = _fleet_load_context_manifest({data_expr})\n"
            "finally:\n"
            "    del _fleet_load_context_manifest"
        )

    def rlm_preview(self, max_chars: int = _MAX_PREVIEW_CHARS) -> str:
        preview = "prepared immutable context in attachments (one text item is also context): " + ", ".join(
            f"{entry.filename!r} ({entry.content_type or 'application/octet-stream'}, {entry.byte_size} bytes)"
            for entry in self.entries
        )
        return preview[: max(1, min(max_chars, _MAX_PREVIEW_CHARS))]


def build_session_context_payload(
    *,
    session_context: SessionContextManifest,
    workspace: WorkspaceCapabilityMetadata,
    workspace_memory_digest: str = "",
    active_task_summary: str = "",
) -> dict[str, Any]:
    try:
        workspace_memory = WorkspaceMemoryInput(tail=workspace_memory_digest) if workspace_memory_digest else None
        context = SessionContextInput(
            session_id=session_context.session_id,
            checkpoint_version=session_context.checkpoint_version,
            message_count=session_context.message_count,
            recent=tuple(
                TurnPreviewInput(
                    ordinal=item.ordinal,
                    role=item.role,
                    preview=item.preview,
                )
                for item in session_context.recent
            ),
            workspace=WorkspaceCapabilityInput(
                available=workspace.available,
                root=cast(Literal["."], workspace.root),
                instructions=workspace.instructions,
            ),
            workspace_memory=workspace_memory,
            active_task=active_task_summary or None,
        )
    except ValidationError as exc:
        raise RLMConfigError("Turn input metadata is invalid") from exc
    payload = context.model_dump(mode="json")
    if workspace_memory is None:
        payload.pop("workspace_memory", None)
    if not active_task_summary:
        payload.pop("active_task", None)
    return payload


def build_rlm_input_kwargs(
    *,
    request: str,
    session_context: SessionContextManifest,
    skill_cards: tuple[Any, ...] | list[Any] = (),
    attachments: tuple[Any, ...] | list[Any] = (),
    attachment_context: AttachmentContextCapsule | None = None,
    workspace: WorkspaceCapabilityMetadata = UNAVAILABLE_WORKSPACE_CAPABILITY,
    workspace_memory_digest: str = "",
    active_task_summary: str = "",
    history: dspy.History | CommittedSessionHistory | None = None,
    signature: type[dspy.Signature] | None = None,
) -> dict[str, Any]:
    if not isinstance(request, str) or not request.strip() or len(request) > _MAX_REQUEST_CHARS:
        raise RLMConfigError("Turn input metadata is invalid")
    if (
        not isinstance(workspace_memory_digest, str)
        or len(workspace_memory_digest.encode("utf-8")) > WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
    ):
        raise RLMConfigError("Turn input metadata is invalid")
    if not isinstance(active_task_summary, str) or len(active_task_summary) > 2048:
        raise RLMConfigError("Turn input metadata is invalid")
    if history is not None and type(history) is not dspy.History:
        from fleet_rlm.sessions.history_transport import CommittedSessionHistory

        if not isinstance(history, CommittedSessionHistory):
            raise RLMConfigError("Turn input metadata is invalid")
    context_payload = build_session_context_payload(
        session_context=session_context,
        workspace=workspace,
        workspace_memory_digest=workspace_memory_digest,
        active_task_summary=active_task_summary,
    )
    try:
        cards = tuple(
            SkillCardInput(
                id=card.id,
                name=card.name,
                description=card.description,
                scope="system",
                version=card.version,
                trust="system",
                affordances=tuple(card.affordances),
                resources_available=card.resources_available,
            )
            for card in skill_cards
        )
        attachment_inputs = tuple(
            AttachmentInput(
                id=ref.attachment_id,
                filename=ref.filename,
                content_type=ref.content_type,
                byte_size=ref.byte_size,
                checksum_sha256=ref.checksum_sha256,
            )
            for ref in attachments
        )
    except ValidationError as exc:
        raise RLMConfigError("Turn input metadata is invalid") from exc
    attachment_value: object = [item.model_dump(mode="json", exclude_none=True) for item in attachment_inputs]
    if attachment_context is not None:
        attachment_value = attachment_context
    kwargs: dict[str, Any] = {
        "request": request,
        "session_context": context_payload,
        "skill_cards": [item.model_dump(mode="json") for item in cards],
        "attachments": attachment_value,
    }
    if history is not None:
        kwargs["history"] = history
    if signature is not None:
        input_fields = getattr(signature, "input_fields", None)
        if isinstance(input_fields, Mapping):
            kwargs = {name: value for name, value in kwargs.items() if name in input_fields}
    return kwargs


# ---------------------------------------------------------------------------
# LM Factory & Model Bundle
# ---------------------------------------------------------------------------


def sanitize_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().strip("'\"")
    if " #" in text:
        text = text.split(" #", 1)[0].rstrip().strip("'\"")
    return text.rstrip("/") if text and _URL_RE.match(text) else None


def normalize_model_id(model: str) -> str:
    cleaned = (model or "").strip().strip("'\"")
    if not cleaned:
        raise ValueError("model id is required")
    return cleaned if "/" in cleaned else f"openai/{cleaned}"


def resolve_role_api_key(settings: Settings, role: LLMRoleSettings) -> str | None:
    value = os.environ.get(role.api_key_env)
    if value is None:
        value = settings._dotenv_values.get(role.api_key_env)
    value = (value or "").strip()
    if value:
        return value
    if role.api_key_env == _LEGACY_LLM_API_KEY_ENV and settings.llm_api_key is not None:
        return settings.llm_api_key.get_secret_value().strip() or None
    return None


def has_llm_credentials(settings: Settings) -> bool:
    roles = settings.lm_roles
    return all(resolve_role_api_key(settings, role) for role in (roles.root, roles.sub))


def build_lm(
    model: str,
    *,
    api_key: str | None,
    base_url: str | None = None,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    cache: bool = True,
    num_retries: int = 3,
) -> dspy.LM:
    model_id = normalize_model_id(model)
    allowed_openai_params: list[str] = []
    kwargs: dict[str, Any] = {
        "model_type": "chat",
        "cache": cache,
        "num_retries": num_retries,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["api_base"] = base_url
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds
    if temperature is not None:
        kwargs["temperature"] = temperature
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
        allowed_openai_params.append("reasoning_effort")
    kwargs["allowed_openai_params"] = allowed_openai_params
    return dspy.LM(model_id, **kwargs)


@dataclass(frozen=True, slots=True)
class RLMModelBundle:
    """Server-owned model roles. Root plans/verifies; sub handles llm_query."""

    root_lm: Any
    sub_lm: Any
    utility_lm: Any | None = None
    deadline: float | None = field(default=None, repr=False, compare=False)
    reserve_seconds: float = field(default=0.0, repr=False, compare=False)
    budget: TurnBudget | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.root_lm is None:
            raise RLMModelBundleError("root_lm is required")
        if self.sub_lm is None:
            raise RLMModelBundleError("sub_lm is required")

    def bind_turn_deadline(
        self, *, deadline: float, reserve_seconds: float = 0.0, budget: TurnBudget | None = None
    ) -> RLMModelBundle:
        if (
            not isinstance(deadline, (int, float))
            or isinstance(deadline, bool)
            or (not math.isfinite(deadline) and deadline != math.inf)
        ):
            raise ValueError("deadline must be finite or positive infinity")
        if (
            not isinstance(reserve_seconds, (int, float))
            or isinstance(reserve_seconds, bool)
            or not math.isfinite(reserve_seconds)
            or reserve_seconds < 0
        ):
            raise ValueError("reserve_seconds must be finite and nonnegative")
        norm_reserve = float(reserve_seconds)
        turn_budget = budget or (TurnBudget(deadline=deadline) if math.isfinite(deadline) else None)
        root_lm = (
            _copy_lm_for_deadline(self.root_lm, deadline=deadline, budget=turn_budget)
            if _supports_turn_lm_copy(self.root_lm)
            else self.root_lm
        )
        sub_lm = (
            _copy_lm_for_deadline(
                self.sub_lm,
                deadline=deadline,
                reserve_seconds=norm_reserve,
                budget=turn_budget,
                can_finalize=False,
            )
            if _supports_turn_lm_copy(self.sub_lm)
            else self.sub_lm
        )
        return RLMModelBundle(
            root_lm=root_lm,
            sub_lm=sub_lm,
            utility_lm=self.utility_lm,
            deadline=deadline,
            reserve_seconds=norm_reserve,
            budget=turn_budget,
        )

    def fork_for_child(self, *, deadline: float) -> RLMModelBundle:
        reserve = max(0.0, self.reserve_seconds)
        return RLMModelBundle(
            root_lm=_copy_lm_for_deadline(
                self.root_lm,
                deadline=deadline,
                error_message="recursive child LM deadline exceeded",
                budget=self.budget,
                can_finalize=False,
            ),
            sub_lm=_copy_lm_for_deadline(
                self.sub_lm,
                deadline=deadline,
                reserve_seconds=reserve,
                error_message="recursive child LM deadline exceeded",
                budget=self.budget,
                can_finalize=False,
            ),
            utility_lm=self.utility_lm,
            deadline=deadline,
            reserve_seconds=reserve,
            budget=self.budget,
        )


_RETRYABLE_LM_ERRORS = (LMRateLimitError, LMServerError, LMTimeoutError, LMTransportError)


def _supports_turn_lm_copy(lm: Any) -> bool:
    copy_lm = getattr(lm, "copy", None)
    if not callable(copy_lm):
        return False
    dummy_lm = getattr(getattr(dspy, "utils", None), "DummyLM", None)
    return not (
        isinstance(dummy_lm, type)
        and isinstance(lm, dummy_lm)
        and getattr(type(lm), "copy", None) is not dspy.BaseLM.copy
    )


def _positive_timeout(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    timeout = float(value)
    return timeout if math.isfinite(timeout) and timeout > 0 else None


def _configured_lm_timeout(lm: Any) -> float | None:
    stored = _positive_timeout(getattr(lm, "_fleet_role_timeout", None))
    if stored is not None:
        return stored
    wrapped = lm.wrapped if isinstance(lm, DeadlineLMProxy) else lm
    return _positive_timeout(getattr(wrapped, "kwargs", {}).get("timeout"))


def _apply_role_timeout(lm: Any, role_timeout: float | None) -> None:
    if role_timeout is not None and isinstance(getattr(lm, "kwargs", None), dict):
        lm.kwargs["timeout"] = role_timeout


class DeadlineLMProxy(dspy.BaseLM):
    """Turn-owned DSPy LM proxy with one retry owner and deadline bounding."""

    _fleet_trace_identity: Any

    def __init__(
        self,
        wrapped: Any,
        *,
        deadline: float | None,
        reserve_seconds: float,
        retries: int,
        error_message: str,
        budget: TurnBudget | None = None,
        admission: ProviderAdmission | None = None,
        can_finalize: bool = True,
        role_timeout: float | None = None,
    ) -> None:
        super().__init__(
            model=getattr(wrapped, "model", "test/deadline"),
            model_type=getattr(wrapped, "model_type", "chat"),
            cache=getattr(wrapped, "cache", False),
            callbacks=getattr(wrapped, "callbacks", None),
            num_retries=0,
        )
        self.wrapped = wrapped
        resolved_role_timeout = _positive_timeout(role_timeout) or _configured_lm_timeout(wrapped)
        _apply_role_timeout(wrapped, resolved_role_timeout)
        self.kwargs = dict(getattr(wrapped, "kwargs", {}))
        self.history = getattr(wrapped, "history", [])
        self._fleet_deadline = deadline
        self._fleet_reserve_seconds = reserve_seconds
        self._fleet_retry_budget = retries
        self._deadline_error_message = error_message
        self.budget = budget
        self.admission = admission
        self.can_finalize = can_finalize
        self._fleet_role_timeout = resolved_role_timeout

    def __getattr__(self, name: str) -> Any:
        wrapped = self.__dict__.get("wrapped")
        if wrapped is None:
            raise AttributeError(name)
        return getattr(wrapped, name)

    @property
    def supports_function_calling(self) -> bool:
        return bool(getattr(self.wrapped, "supports_function_calling", False))

    @property
    def supports_response_schema(self) -> bool:
        return bool(getattr(self.wrapped, "supports_response_schema", False))

    @property
    def supports_reasoning(self) -> bool:
        return bool(getattr(self.wrapped, "supports_reasoning", False))

    @property
    def supported_params(self) -> set[str]:
        return set(getattr(self.wrapped, "supported_params", ()))

    def copy(self, **kwargs: Any) -> Any:
        kwargs["num_retries"] = 0
        copied = type(self)(
            self.wrapped.copy(**kwargs),
            deadline=self._fleet_deadline,
            reserve_seconds=self._fleet_reserve_seconds,
            retries=self._fleet_retry_budget,
            error_message=self._deadline_error_message,
            budget=self.budget,
            admission=self.admission,
            can_finalize=self.can_finalize,
            role_timeout=self._fleet_role_timeout,
        )
        if "_fleet_trace_identity" in vars(self):
            copied._fleet_trace_identity = self._fleet_trace_identity
        return copied

    @classmethod
    def for_adapter(cls, lm: Any, budget: AdapterBudget, *, action: bool, wrap_up: bool) -> DeadlineLMProxy:
        if isinstance(lm, cls):
            if lm.budget is not None and lm.budget is not budget.turn:
                raise RLMModelBundleError("adapter and LM must share the Turn budget")
            wrapped = lm.wrapped
            deadline, reserve = lm._fleet_deadline, lm._fleet_reserve_seconds
            retries, can_finalize = lm._fleet_retry_budget, lm.can_finalize
        else:
            wrapped = lm.copy(num_retries=0) if isinstance(lm, dspy.LM) else lm
            deadline, reserve, retries, can_finalize = budget.deadline, 0.0, getattr(lm, "num_retries", 0), True
        if type(retries) is not int or retries < 0:
            retries = 0
        view = cls(
            wrapped,
            deadline=deadline,
            reserve_seconds=reserve,
            retries=retries,
            error_message="Turn LM deadline exceeded",
            budget=budget.turn,
            admission=ProviderAdmission(budget, action, wrap_up, can_finalize),
            can_finalize=can_finalize,
            role_timeout=_configured_lm_timeout(lm),
        )
        view._fleet_trace_identity = getattr(lm, "_fleet_trace_identity", lm)
        return view

    def dump_state(self) -> dict[str, Any]:
        return self.wrapped.dump_state()

    def _attempt_kwargs(
        self,
        kwargs: dict[str, Any],
        *,
        call_deadline: float | None = None,
    ) -> tuple[dict[str, Any], float]:
        bounded = dict(kwargs)
        now = time.monotonic()
        available = _remaining_lm_timeout(
            self._fleet_deadline,
            self,
            bounded,
            reserve_seconds=self._fleet_reserve_seconds,
            error_message=self._deadline_error_message,
            now=now,
        )
        if call_deadline is not None:
            attempt_window = call_deadline - now
            if attempt_window <= 0:
                role_ceiling = _positive_timeout(self._fleet_role_timeout)
                ceiling_text = f"{role_ceiling:g}s" if role_ceiling is not None else "configured"
                raise TimeoutError(
                    f"LM retry window exhausted: the previous attempt consumed the {ceiling_text} "
                    "role timeout (llm.<role>.timeout_seconds), leaving no time for a retry"
                )
            available = min(available, attempt_window)
        if available <= 0:
            raise TimeoutError(self._deadline_error_message)
        if self.admission is not None:
            available = min(available, self.admission.reserve())
        elif self.budget is not None:
            available = min(available, self.budget.reserve(BudgetDimension.PROVIDER_ATTEMPTS))
        if math.isfinite(available):
            bounded["timeout"] = available
        return bounded, now

    def _retry_call_deadline(self, now: float, timeout: object) -> float | None:
        bounded = _positive_timeout(timeout)
        return now + bounded if bounded is not None else None

    def forward(self, *args: Any, **kwargs: Any) -> Any:
        call_deadline: float | None = None
        for attempt in range(self._fleet_retry_budget + 1):
            bounded, now = self._attempt_kwargs(kwargs, call_deadline=call_deadline)
            if call_deadline is None:
                call_deadline = self._retry_call_deadline(now, bounded.get("timeout"))
            try:
                return self.wrapped.forward(*args, **bounded)
            except _RETRYABLE_LM_ERRORS:
                if attempt == self._fleet_retry_budget:
                    raise
        raise AssertionError("provider retry loop exhausted")

    async def aforward(self, *args: Any, **kwargs: Any) -> Any:
        call_deadline: float | None = None
        for attempt in range(self._fleet_retry_budget + 1):
            bounded, now = self._attempt_kwargs(kwargs, call_deadline=call_deadline)
            if call_deadline is None:
                call_deadline = self._retry_call_deadline(now, bounded.get("timeout"))
            try:
                return await self.wrapped.aforward(*args, **bounded)
            except _RETRYABLE_LM_ERRORS:
                if attempt == self._fleet_retry_budget:
                    raise
        raise AssertionError("provider retry loop exhausted")


def _copy_lm_for_deadline(
    lm: Any,
    *,
    deadline: float,
    reserve_seconds: float = 0.0,
    error_message: str = "Turn LM deadline exceeded",
    budget: TurnBudget | None = None,
    can_finalize: bool = True,
) -> Any:
    copy_lm = getattr(lm, "copy", None)
    if not callable(copy_lm):
        raise RLMModelBundleError("deadline-bound LM must support DSPy runtime copy()")
    retry_budget = getattr(lm, "_fleet_retry_budget", getattr(lm, "num_retries", 0))
    if not isinstance(retry_budget, int) or isinstance(retry_budget, bool) or retry_budget < 0:
        retry_budget = 0
    role_timeout = _configured_lm_timeout(lm)
    copied = lm.wrapped.copy(num_retries=0) if isinstance(lm, DeadlineLMProxy) else copy_lm(num_retries=0)
    if copied is lm:
        raise RLMModelBundleError("deadline-bound LM copy() must return an isolated runtime")

    return DeadlineLMProxy(
        copied,
        deadline=deadline,
        reserve_seconds=reserve_seconds,
        retries=retry_budget,
        error_message=error_message,
        budget=budget if budget is not None else getattr(lm, "budget", None),
        can_finalize=can_finalize,
        role_timeout=role_timeout,
    )


def _copy_lm_for_child(lm: Any, *, deadline: float) -> Any:
    return _copy_lm_for_deadline(
        lm,
        deadline=deadline,
        error_message="recursive child LM deadline exceeded",
    )


def _remaining_lm_timeout(
    deadline: float | None,
    lm: Any,
    call_kwargs: dict[str, Any],
    *,
    reserve_seconds: float = 0.0,
    error_message: str = "Turn LM deadline exceeded",
    now: float | None = None,
) -> float:
    if deadline is not None and (
        not isinstance(deadline, (int, float))
        or isinstance(deadline, bool)
        or (not math.isfinite(deadline) and deadline != math.inf)
    ):
        raise ValueError("deadline must be finite, positive infinity, or None")
    if (
        not isinstance(reserve_seconds, (int, float))
        or isinstance(reserve_seconds, bool)
        or not math.isfinite(reserve_seconds)
        or reserve_seconds < 0
    ):
        raise ValueError("reserve_seconds must be finite and nonnegative")
    clock = time.monotonic() if now is None else now
    remaining = math.inf if deadline is None else deadline - clock
    available = remaining - float(reserve_seconds)
    if available <= 0:
        raise TimeoutError(error_message)
    configured = _positive_timeout(call_kwargs.get("timeout")) or _configured_lm_timeout(lm)
    role_timeout = _configured_lm_timeout(lm)
    if role_timeout is not None:
        configured = role_timeout if configured is None else min(configured, role_timeout)
    return min(configured, available) if configured is not None else available


def build_model_bundle(settings: Settings) -> RLMModelBundle:
    def build(policy: LLMRoleSettings) -> dspy.LM:
        api_key = resolve_role_api_key(settings, policy)
        if not api_key:
            raise RuntimeError(f"LLM API key not configured ({policy.api_key_env})")
        return build_lm(
            policy.model,
            api_key=api_key,
            base_url=sanitize_base_url(policy.base_url),
            max_tokens=policy.max_tokens,
            timeout_seconds=policy.timeout_seconds,
            temperature=policy.temperature,
            reasoning_effort=policy.reasoning_effort,
            cache=policy.cache,
            num_retries=policy.num_retries,
        )

    roles = settings.lm_roles
    return RLMModelBundle(root_lm=build(roles.root), sub_lm=build(roles.sub))


class LMTier(StrEnum):
    FRONTIER = "frontier"
    WORKER = "worker"
    FAST = "fast"


_TIER_MODELS: dict[LMTier, list[str]] = {
    LMTier.FRONTIER: [
        "system.ai.claude-opus-4-8",
        "system.ai.gpt-5-6-sol",
    ],
    LMTier.WORKER: [
        "system.ai.gpt-5-6-terra",
        "system.ai.glm-5-2",
        "system.ai.gpt-5-6-luna",
    ],
    LMTier.FAST: [
        "uscentral.default.deepseek-v4-flash",
        "system.ai.gpt-oss-120b",
        "system.ai.gemini-3-1-flash-lite",
        "uscentral.default.nemotron-3-ultra-free",
        "uscentral.default.qwen3-7-max-2026-05-20",
        "uscentral.default.glm-5-1",
    ],
}


def build_lm_for_tier(
    tier: LMTier,
    *,
    workspace_url: str,
    api_key: str,
    preference: int = 0,
    max_tokens: int | None = None,
    cache: bool = True,
    num_retries: int = 3,
) -> dspy.LM:
    models = _TIER_MODELS[tier]
    model_uc = models[preference % len(models)]
    base = f"{workspace_url.rstrip('/')}{_AI_GATEWAY_PATH}"
    return build_lm(
        model_uc,
        api_key=api_key,
        base_url=base,
        max_tokens=max_tokens,
        cache=cache,
        num_retries=num_retries,
    )


# ---------------------------------------------------------------------------
# Native program construction
# ---------------------------------------------------------------------------

_DSPY_BUILTIN_TOOLS = frozenset({"llm_query", "llm_query_batched", "print", "SUBMIT"})


def _native_tools(tools: Sequence[dspy.Tool | Callable[..., Any]] | None) -> list[dspy.Tool]:
    """Normalize and validate the explicitly authorized model-facing tools."""
    normalized: list[dspy.Tool] = []
    names: set[str] = set()
    for value in tools or ():
        tool = value if isinstance(value, dspy.Tool) else dspy.Tool(value)
        name = tool.name
        if not isinstance(name, str) or not name.isidentifier() or name in _DSPY_BUILTIN_TOOLS:
            raise RLMConfigError("tool name conflicts with the DSPy execution namespace")
        if name in names:
            raise RLMConfigError("duplicate Fleet tool name")
        names.add(name)
        normalized.append(tool)
    return normalized


def build_native_rlm(
    *,
    signature: type[dspy.Signature] | str = FleetRLMSignature,
    options: RLMOptions,
    tools: Sequence[dspy.Tool | Callable[..., Any]] | None = None,
    sub_lm: dspy.LM | None = None,
    skill_instructions: Sequence[str] = (),
    recursion_enabled: bool = False,
    host_tool_dispatch: bool = True,
    interpreter_factory: Callable[[], Any] = daytona_provider_contract,
    verbose: bool = True,
) -> Any:
    """Construct one fresh native DSPy RLM from its invocation inputs.

    Permission selection happens before this call; tool callables still enforce
    authorization at execution.  There is intentionally no Fleet program
    specification, catalog, or factory between callers and ``dspy.RLM``.
    """
    native_tools = _native_tools(tools)
    resolved_signature = signature
    tool_names = frozenset(str(tool.name) for tool in native_tools)
    if (
        isinstance(signature, type)
        and issubclass(signature, dspy.Signature)
        and (
            recursion_enabled
            or skill_instructions
            or not host_tool_dispatch
            or _tool_names_need_instruction_overlay(tool_names)
        )
    ):
        resolved_signature = root_signature_for_recursion(
            signature,
            recursion_enabled=recursion_enabled,
            skill_instructions=tuple(skill_instructions),
            tool_names=tool_names,
            host_tool_dispatch=host_tool_dispatch,
        )
    rlm = dspy.RLM(
        resolved_signature,
        max_iters=options.max_iters,
        max_llm_calls=options.max_llm_calls,
        max_output_chars=options.max_output_chars,
        verbose=verbose,
        tools=native_tools,
        sub_lm=sub_lm,
        interpreter_factory=interpreter_factory,
    )
    if set(rlm.tools) != set(tool_names) or set(rlm.tools) & _DSPY_BUILTIN_TOOLS:
        raise RLMConfigError("constructed DSPy tool namespace differs from the authorized tools")
    return rlm


__all__ = [
    "BASE_RLM_INSTRUCTIONS",
    "DISCOVERY_RLM_INSTRUCTIONS",
    "RECURSION_RLM_INSTRUCTIONS",
    "REPL_RLM_INSTRUCTIONS",
    "TOOL_RLM_INSTRUCTIONS",
    "TOOL_RLM_INSTRUCTIONS_NO_DISPATCH",
    "AttachmentContextCapsule",
    "AttachmentContextEntry",
    "AttachmentInput",
    "FleetInputModel",
    "FleetRLMSignature",
    "LMTier",
    "RLMInstructionFragments",
    "RLMModelBundle",
    "RLMOptions",
    "SessionContextInput",
    "SkillCardInput",
    "TurnPreviewInput",
    "WorkspaceCapabilityInput",
    "WorkspaceMemoryInput",
    "build_lm",
    "build_lm_for_tier",
    "build_model_bundle",
    "build_native_rlm",
    "build_rlm_input_kwargs",
    "build_session_context_payload",
    "compose_rlm_instructions",
    "fleet_rlm_instruction_fragments",
    "has_llm_credentials",
    "normalize_model_id",
    "resolve_role_api_key",
    "root_signature_for_recursion",
    "sanitize_base_url",
]
