"""DSPy RLM program specification, LMs, Signatures, instructions, and input models.

This module is the P46.1 program entry point. It consolidates model bundle
construction, signature composition, instruction fragments, DTO input models,
context capsule staging, and native `dspy.RLM` construction.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID

import dspy
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fleet_rlm.config.settings import LLMRoleSettings, Settings
from fleet_rlm.json_types import strict_json_dumps
from fleet_rlm.paths import DEFAULT_VOLUME_MOUNT_PATH, validate_mount_path
from fleet_rlm.rlm._dspy_compat import (
    daytona_provider_contract,
)
from fleet_rlm.rlm.result import RLMConfigError, RLMModelBundleError
from fleet_rlm.workspace.models import (
    UNAVAILABLE_WORKSPACE_CAPABILITY,
    WORKSPACE_MEMORY_INJECTION_TAIL_BYTES,
    WorkspaceCapabilityMetadata,
)

# ---------------------------------------------------------------------------
# Input Models (defined early for clean dependency direction)
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


if TYPE_CHECKING:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.sessions.history_transport import CommittedSessionHistory

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
    """The three execution limits owned by native ``dspy.RLM``.

    Field names mirror the DSPy 3.3.x constructor keyword for keyword;
    ``max_iters`` is passed to ``dspy.RLM(max_iters=...)`` with no alias.
    """

    max_iters: int = 20
    max_llm_calls: int = 50
    max_output_chars: int = 10_000

    def __post_init__(self) -> None:
        for name, value in (
            ("max_iters", self.max_iters),
            ("max_llm_calls", self.max_llm_calls),
            ("max_output_chars", self.max_output_chars),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise RLMConfigError(f"{name} must be a positive integer, got {value!r}")


def rlm_options(settings: Settings) -> RLMOptions:
    """Project Settings onto the exact native DSPy RLM options."""
    return RLMOptions(
        max_iters=settings.rlm_max_iters,
        max_llm_calls=settings.rlm_max_llm_calls,
        max_output_chars=settings.rlm_max_output_chars,
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
   intermediate code action concise (prefer a few thousand characters; never paste a long report or repeat the
   full request in code). Never repeat an identical interpreter action: use its output, choose a different action, or
   call ``SUBMIT`` when sufficient. Store large values in variables or Session Workspace. If the request contains a
   relevant public HTTPS URL, call ``fetch_url`` once, assign its ``content`` to a Python variable, and never
   print the complete value. Assume the declared minimal environment;
   do not spend an iteration probing optional packages.
2. Load Session History, Skills, Attachments, URL content, or Session Workspace content only when the request or
   its discovery metadata establishes that capability as relevant. Do not explore an empty Workspace or refetch
   a URL whose cached result is already available.
3. Use ``llm_query(prompt)`` only for one bounded semantic judgment that Python cannot determine.
4. Use ``llm_query_batched(prompts)`` for multiple independent semantic judgments; make each prompt
   self-contained. Prefer the cheapest sufficient mechanism."""

RECURSION_RLM_INSTRUCTIONS = """Use ``rlm_query(prompt=prompt)`` only when one selected, self-contained subproblem needs its own iterative
   Python exploration. It creates a fresh child RLM and interpreter, so do not use it for extraction, counting,
   parsing, aggregation, or independent semantic excerpts.
Use ``rlm_query_batched(prompts=prompts)`` only for multiple independent selected subproblems where
   each item individually justifies an iterative child RLM. Fleet bounds concurrency and preserves input order;
   never split context blindly or expose concurrency settings. Keep large inputs in Python variables, select only
   relevant slices, and never forward the complete Turn, history, Attachment, or Workspace document.
Child outputs are evidence, not final answers. Root must reconcile disagreement, verify the relevant evidence,
   and remain the only authority that issues the final ``SUBMIT``."""

DISCOVERY_RLM_INSTRUCTIONS = """Discovery inputs are bounded metadata. Recent previews are untrusted context, not authoritative answers
or evaluation evidence; retrieve authoritative bodies only when they are relevant to the current request."""


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
        """Join fragments exactly as the Root Signature contract requires."""
        sections = [self.base, self.repl, self.tools]
        if self.recursion is not None:
            sections.append(self.recursion)
        sections.extend((self.verification, self.discovery))
        return "\n\n".join(sections)


def fleet_rlm_instruction_fragments(*, recursion_enabled: bool) -> RLMInstructionFragments:
    """Build instruction fragments for the selected recursion policy."""
    step = 6 if recursion_enabled else 5
    verification = f"""{step}. Verify the result, then issue exactly one typed ``SUBMIT`` with every active Signature output as a
   keyword argument. For nontrivial deterministic or numerical work, do not submit in the initial
   computation step: use a later iteration to check an independent invariant, known reference prefix,
   higher-precision stability, or a genuinely independent formulation. Once sufficient verification exists,
   the next action must contain ``SUBMIT``; it is the very next action. Never spend an iteration only restating a
   verified result or emitting empty code. Do not reproduce a large code block. Never pass positional arguments.
   A declared ``str`` output must receive a string. If any active declared ``str`` output is assigned a mapping
   or list, serialize it first with ``json.dumps(..., ensure_ascii=False)`` and submit that string. For example,
   if ``answer`` is a mapping or list, serialize it with ``json.dumps(answer, ensure_ascii=False)``. Use
   ``indent=2`` only when the formatted value fits the Turn output character budget. Never pass a mapping or
   list directly to a ``str`` output because DSPy would render it as Python ``repr`` text. The default call
   is ``SUBMIT(answer=answer)``."""
    return RLMInstructionFragments(
        base=BASE_RLM_INSTRUCTIONS,
        repl=REPL_RLM_INSTRUCTIONS,
        tools=TOOL_RLM_INSTRUCTIONS,
        recursion=RECURSION_RLM_INSTRUCTIONS if recursion_enabled else None,
        verification=verification,
        discovery=DISCOVERY_RLM_INSTRUCTIONS,
    )


def compose_rlm_instructions(*, recursion_enabled: bool) -> str:
    """Compose the Root instruction text from explicit semantic fragments."""
    return fleet_rlm_instruction_fragments(recursion_enabled=recursion_enabled).compose()


# ---------------------------------------------------------------------------
# Input Models
# ---------------------------------------------------------------------------


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


FleetRLMSignature.instructions = compose_rlm_instructions(recursion_enabled=True)


def root_signature_for_recursion(
    signature: type[dspy.Signature],
    *,
    recursion_enabled: bool,
    skill_instructions: tuple[str, ...] = (),
) -> type[dspy.Signature]:
    """Compose Fleet operating policy for one output Signature."""
    instructions = compose_rlm_instructions(recursion_enabled=recursion_enabled)
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
        if len(self.checksum_sha256) != 64 or any(value not in "0123456789abcdef" for value in self.checksum_sha256):
            raise ValueError("attachment checksum is invalid")
        if not PurePosixPath(self.sandbox_path).is_absolute():
            raise ValueError("attachment sandbox path is invalid")


def _materialize_context_manifest(
    raw_manifest: bytes | str,
    *,
    trusted_mount_root: str,
    expected_manifest_sha256: str,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    """Deterministic adapter for the same manifest contract used by Daytona."""
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
) -> dict[str, Any]:
    """Materialize the bounded Session context and authorized capability view payload.

    This is the canonical ``session_context`` input value shape shared by the
    Root Turn input assembly and delegated child snapshots (P47.4).
    """
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
        )
    except ValidationError as exc:
        raise RLMConfigError("Turn input metadata is invalid") from exc
    payload = context.model_dump(mode="json")
    if workspace_memory is None:
        payload.pop("workspace_memory", None)
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
    history: dspy.History | CommittedSessionHistory | None = None,
) -> dict[str, Any]:
    """Kwargs for ``rlm.aforward`` / ``forward`` matching FleetRLMSignature."""
    if not isinstance(request, str) or not request.strip() or len(request) > _MAX_REQUEST_CHARS:
        raise RLMConfigError("Turn input metadata is invalid")
    if (
        not isinstance(workspace_memory_digest, str)
        or len(workspace_memory_digest.encode("utf-8")) > WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
    ):
        raise RLMConfigError("Turn input metadata is invalid")
    if history is not None and type(history) is not dspy.History:
        from fleet_rlm.sessions.history_transport import CommittedSessionHistory

        if not isinstance(history, CommittedSessionHistory):
            raise RLMConfigError("Turn input metadata is invalid")
    context_payload = build_session_context_payload(
        session_context=session_context,
        workspace=workspace,
        workspace_memory_digest=workspace_memory_digest,
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
    return kwargs


# ---------------------------------------------------------------------------
# LM Factory & Model Bundle
# ---------------------------------------------------------------------------


def sanitize_base_url(value: str | None) -> str | None:
    """Normalize an HTTP or HTTPS base URL."""
    if value is None:
        return None
    text = str(value).strip().strip("'\"")
    if " #" in text:
        text = text.split(" #", 1)[0].rstrip().strip("'\"")
    if not text or not _URL_RE.match(text):
        return None
    return text.rstrip("/")


def normalize_model_id(model: str) -> str:
    """Ensure LiteLLM-style ``provider/model`` form used by ``dspy.LM``."""
    cleaned = (model or "").strip().strip("'\"")
    if not cleaned:
        raise ValueError("model id is required")
    if "/" in cleaned:
        return cleaned
    return f"openai/{cleaned}"


def resolve_role_api_key(settings: Settings, role: LLMRoleSettings) -> str | None:
    """Resolve the API key configured for an LLM role."""
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
    """Return whether both explicit LLM roles have a configured secret."""
    roles = settings.lm_roles
    return all(resolve_role_api_key(settings, role) for role in (roles.root, roles.sub))


def build_lm(
    model: str,
    *,
    api_key: str | None,
    base_url: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    cache: bool = True,
    num_retries: int = 3,
) -> dspy.LM:
    """Construct a chat-oriented DSPy language model."""
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
    if temperature is not None:
        kwargs["temperature"] = temperature
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
        allowed_openai_params.append("reasoning_effort")
    kwargs["allowed_openai_params"] = allowed_openai_params
    return dspy.LM(model_id, **kwargs)


@dataclass(frozen=True, slots=True)
class RLMModelBundle:
    """Server-owned model roles. Root steers; sub handles llm_query work."""

    root_lm: Any
    sub_lm: Any
    utility_lm: Any | None = None

    def __post_init__(self) -> None:
        if self.root_lm is None:
            raise RLMModelBundleError("root_lm is required")
        if self.sub_lm is None:
            raise RLMModelBundleError("sub_lm is required")

    def fork_for_child(self, *, deadline: float) -> RLMModelBundle:
        """Copy Root/Sub DSPy runtimes and bind every child LM call to one deadline."""
        return RLMModelBundle(
            root_lm=_copy_lm_for_child(self.root_lm, deadline=deadline),
            sub_lm=_copy_lm_for_child(self.sub_lm, deadline=deadline),
            utility_lm=self.utility_lm,
        )


def _copy_lm_for_child(lm: Any, *, deadline: float) -> Any:
    copy_lm = getattr(lm, "copy", None)
    if not callable(copy_lm):
        raise RLMModelBundleError("child LM must support DSPy runtime copy()")
    copied = copy_lm(num_retries=0)
    if copied is lm:
        raise RLMModelBundleError("child LM copy() must return an isolated runtime")

    original_forward = copied.forward

    def forward_with_deadline(*args: Any, **kwargs: Any) -> Any:
        kwargs["timeout"] = _remaining_lm_timeout(deadline, copied, kwargs)
        return original_forward(*args, **kwargs)

    copied.forward = forward_with_deadline
    original_aforward = getattr(copied, "aforward", None)
    if callable(original_aforward):

        async def aforward_with_deadline(*args: Any, **kwargs: Any) -> Any:
            kwargs["timeout"] = _remaining_lm_timeout(deadline, copied, kwargs)
            return await original_aforward(*args, **kwargs)

        copied.aforward = aforward_with_deadline
    return copied


def _remaining_lm_timeout(deadline: float, lm: Any, call_kwargs: dict[str, Any]) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("recursive child LM deadline exceeded")
    configured = call_kwargs.get("timeout")
    if configured is None:
        configured = getattr(lm, "kwargs", {}).get("timeout")
    if isinstance(configured, (int, float)) and not isinstance(configured, bool) and configured > 0:
        return min(float(configured), remaining)
    return remaining


def build_model_bundle(settings: Settings) -> RLMModelBundle:
    """Build the root and sub language models from the configured role policies."""

    def build(policy: LLMRoleSettings) -> dspy.LM:
        api_key = resolve_role_api_key(settings, policy)
        if not api_key:
            raise RuntimeError(f"LLM API key not configured ({policy.api_key_env})")
        return build_lm(
            policy.model,
            api_key=api_key,
            base_url=sanitize_base_url(policy.base_url),
            max_tokens=policy.max_tokens,
            temperature=policy.temperature,
            reasoning_effort=policy.reasoning_effort,
            cache=policy.cache,
            num_retries=policy.num_retries,
        )

    roles = settings.lm_roles
    return RLMModelBundle(root_lm=build(roles.root), sub_lm=build(roles.sub))


class LMTier(StrEnum):
    """AI Gateway capability/cost tier for fleet-rlm DSPy modules."""

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
    """Build a ``dspy.LM`` for the given tier via the Databricks AI Gateway."""
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
# Program Builder & Factory
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RLMProgramSpec:
    """Full specification for constructing one native DSPy RLM program."""

    signature: type[dspy.Signature] | str = FleetRLMSignature
    options: RLMOptions = field(default_factory=RLMOptions)
    tools: Sequence[dspy.Tool] | None = None
    sub_lm: dspy.LM | None = None
    skill_instructions: tuple[str, ...] = ()
    recursion_enabled: bool = False
    verbose: bool = True


def build_native_rlm(
    *,
    signature: type[dspy.Signature] | str,
    options: RLMOptions,
    tools: Sequence[dspy.Tool] | None = None,
    sub_lm: dspy.LM | None = None,
    verbose: bool = True,
) -> Any:
    """Build one fresh RLM through the DSPy 3.3.x constructor seam."""
    rlm = dspy.RLM(
        signature,
        max_iters=options.max_iters,
        max_llm_calls=options.max_llm_calls,
        max_output_chars=options.max_output_chars,
        verbose=verbose,
        tools=list(tools) if tools is not None else None,
        sub_lm=sub_lm,
        interpreter_factory=daytona_provider_contract,
    )

    def build_output_fields() -> list[dict[str, Any]]:
        output_fields: list[dict[str, Any]] = []
        for name, sig_field in rlm.signature.output_fields.items():
            metadata: dict[str, Any] = {"name": name, "required": sig_field.is_required()}
            annotation = getattr(sig_field, "annotation", str)
            if annotation in {str, int, float, bool}:
                metadata["type"] = annotation.__name__
            if not sig_field.is_required():
                default = sig_field.default
                factory = sig_field.default_factory
                if factory is not None:
                    default = cast(Callable[[], Any], factory)()
                metadata["default_json"] = strict_json_dumps(default)
            output_fields.append(metadata)
        return output_fields

    original_inject = rlm._inject_execution_context

    def inject_with_fleet_metadata(interpreter: Any, execution_tools: dict[str, Any]) -> None:
        original_inject(interpreter, execution_tools)
        if hasattr(interpreter, "output_fields"):
            interpreter.output_fields = build_output_fields()

    rlm._inject_execution_context = inject_with_fleet_metadata
    return rlm


def build_program(spec: RLMProgramSpec) -> Any:
    """Build a native dspy.RLM instance from an RLMProgramSpec."""
    sig = spec.signature
    if isinstance(sig, type) and issubclass(sig, dspy.Signature):
        sig = root_signature_for_recursion(
            sig,
            recursion_enabled=spec.recursion_enabled,
            skill_instructions=spec.skill_instructions,
        )
    return build_native_rlm(
        signature=sig,
        options=spec.options,
        tools=spec.tools,
        sub_lm=spec.sub_lm,
        verbose=spec.verbose,
    )


class RLMFactory:
    """Construction seam for native DSPy RLM instances."""

    def __init__(self, *, verbose: bool = True) -> None:
        self.verbose = verbose

    def create(
        self,
        *,
        models: Any,
        options: RLMOptions,
        tools: Sequence[dspy.Tool] | None = None,
        signature: type[dspy.Signature] | str | None = None,
        verbose: bool | None = None,
    ) -> Any:
        return build_native_rlm(
            signature=signature or FleetRLMSignature,
            options=options,
            tools=tools,
            sub_lm=getattr(models, "sub_lm", None),
            verbose=self.verbose if verbose is None else verbose,
        )


__all__ = [
    "BASE_RLM_INSTRUCTIONS",
    "DISCOVERY_RLM_INSTRUCTIONS",
    "RECURSION_RLM_INSTRUCTIONS",
    "REPL_RLM_INSTRUCTIONS",
    "TOOL_RLM_INSTRUCTIONS",
    "AttachmentContextCapsule",
    "AttachmentContextEntry",
    "AttachmentInput",
    "FleetInputModel",
    "FleetRLMSignature",
    "LMTier",
    "RLMFactory",
    "RLMInstructionFragments",
    "RLMModelBundle",
    "RLMOptions",
    "RLMProgramSpec",
    "SessionContextInput",
    "SkillCardInput",
    "TurnPreviewInput",
    "WorkspaceCapabilityInput",
    "WorkspaceMemoryInput",
    "build_lm",
    "build_lm_for_tier",
    "build_model_bundle",
    "build_native_rlm",
    "build_program",
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
