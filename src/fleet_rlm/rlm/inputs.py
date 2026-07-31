"""Serialize Turn Context discovery metadata for FleetRLMSignature inputs.

Bodies stay behind Host-Mediated Tools; signature fields are metadata only.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID

import dspy
from pydantic import ValidationError

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.files.workspace_models import UNAVAILABLE_WORKSPACE_CAPABILITY, WorkspaceCapabilityMetadata
from fleet_rlm.rlm.errors import RLMConfigError
from fleet_rlm.rlm.input_models import (
    AttachmentInput,
    SessionContextInput,
    SkillCardInput,
    TurnPreviewInput,
    WorkspaceCapabilityInput,
)

_MAX_REQUEST_CHARS = 100_000
_MAX_SERIALIZABLE_ATTACHMENT_BYTES = 256 * 1024
_MAX_SERIALIZABLE_ATTACHMENT_COUNT = 8
_MAX_PREVIEW_CHARS = 500


@dataclass(frozen=True, slots=True)
class AttachmentSandboxPayload(dspy.SandboxSerializable):
    """Bounded immutable attachment bytes explicitly prepared for one RLM.

    Normal attachment discovery stays metadata-only and is served by host
    Tools. This value is an opt-in escape hatch for a caller that has already
    authorized a small immutable payload for the current Turn. DSPy injects
    the decoded value into the persistent interpreter; it never crosses the
    public Runtime Event boundary.
    """

    attachment_id: UUID
    filename: str
    content_type: str | None
    data: bytes

    def __post_init__(self) -> None:
        if not self.filename or len(self.filename) > 255:
            raise ValueError("attachment filename is invalid")
        if len(self.data) > _MAX_SERIALIZABLE_ATTACHMENT_BYTES:
            raise ValueError("attachment payload is too large for sandbox serialization")

    def sandbox_setup(self) -> str:
        return "import base64, json"

    def to_sandbox(self) -> bytes:
        payload = {
            "id": str(self.attachment_id),
            "filename": self.filename,
            "content_type": self.content_type,
            "data": base64.b64encode(self.data).decode("ascii"),
        }
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        # Return ASCII so DSPy keeps the expression as a string. The
        # assignment then performs the only decode inside the sandbox.
        return base64.b64encode(encoded)

    def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
        return (
            f"{var_name} = json.loads(base64.b64decode({data_expr}).decode('utf-8'))\n"
            f"{var_name}['data'] = base64.b64decode({var_name}['data'])"
        )

    def rlm_preview(self, max_chars: int = _MAX_PREVIEW_CHARS) -> str:
        preview = (
            f"prepared attachment {self.filename!r} ({self.content_type or 'application/octet-stream'}, "
            f"{len(self.data)} bytes)"
        )
        return preview[: max(1, min(max_chars, _MAX_PREVIEW_CHARS))]


def _attachment_payload_bundle(payloads: Sequence[AttachmentSandboxPayload]) -> dspy.SandboxSerializable:
    """Serialize several prepared payloads through one DSPy input variable."""

    if not payloads:
        raise ValueError("at least one attachment payload is required")
    if len(payloads) > _MAX_SERIALIZABLE_ATTACHMENT_COUNT:
        raise ValueError("too many attachment payloads for sandbox serialization")

    @dataclass(frozen=True, slots=True)
    class _Bundle(dspy.SandboxSerializable):
        values: tuple[AttachmentSandboxPayload, ...]

        def sandbox_setup(self) -> str:
            return "import base64, json"

        def to_sandbox(self) -> bytes:
            encoded = json.dumps(
                [json.loads(base64.b64decode(value.to_sandbox()).decode("utf-8")) for value in self.values],
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
            return base64.b64encode(encoded)

        def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
            return (
                f"{var_name} = json.loads(base64.b64decode({data_expr}).decode('utf-8'))\n"
                f"for _item in {var_name}: _item['data'] = base64.b64decode(_item['data'])"
            )

        def rlm_preview(self, max_chars: int = _MAX_PREVIEW_CHARS) -> str:
            preview = ", ".join(value.rlm_preview(120) for value in self.values)
            return ("prepared attachment payloads: " + preview)[: max(1, min(max_chars, _MAX_PREVIEW_CHARS))]

    return _Bundle(tuple(payloads))


def build_rlm_input_kwargs(
    *,
    request: str,
    session_context: SessionContextManifest,
    skill_cards: tuple[Any, ...] | list[Any] = (),
    attachments: tuple[Any, ...] | list[Any] = (),
    attachment_payloads: Sequence[AttachmentSandboxPayload] = (),
    workspace: WorkspaceCapabilityMetadata = UNAVAILABLE_WORKSPACE_CAPABILITY,
) -> dict[str, Any]:
    """Kwargs for ``rlm.aforward`` / ``forward`` matching FleetRLMSignature."""
    if not isinstance(request, str) or not request.strip() or len(request) > _MAX_REQUEST_CHARS:
        raise RLMConfigError("Turn input metadata is invalid")
    try:
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
        )
        cards = tuple(
            SkillCardInput(
                id=card.id,
                name=card.name,
                description=card.description,
                scope="system",
                version=card.version,
                trust="system",
                affordances=(),
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
    if attachment_payloads:
        attachment_value = _attachment_payload_bundle(tuple(attachment_payloads))
    return {
        "request": request,
        "session_context": context.model_dump(mode="json"),
        "skill_cards": [item.model_dump(mode="json") for item in cards],
        "attachments": attachment_value,
    }


__all__ = ["AttachmentSandboxPayload", "build_rlm_input_kwargs"]
