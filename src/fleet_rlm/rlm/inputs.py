"""Serialize Turn Context discovery metadata for FleetRLMSignature inputs.

Bodies stay behind Host-Mediated Tools; signature fields are metadata only.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import ValidationError

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.files.workspace_models import DENO_WORKSPACE_CAPABILITY, WorkspaceCapabilityMetadata
from fleet_rlm.rlm.errors import RLMConfigError
from fleet_rlm.rlm.input_models import (
    AttachmentInput,
    SessionContextInput,
    SkillCardInput,
    TurnPreviewInput,
    WorkspaceCapabilityInput,
)

_MAX_REQUEST_CHARS = 100_000


def build_rlm_input_kwargs(
    *,
    request: str,
    session_context: SessionContextManifest,
    skill_cards: tuple[Any, ...] | list[Any] = (),
    attachments: tuple[Any, ...] | list[Any] = (),
    workspace: WorkspaceCapabilityMetadata = DENO_WORKSPACE_CAPABILITY,
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
    return {
        "request": request,
        "session_context": context.model_dump(mode="json"),
        "skill_cards": [item.model_dump(mode="json") for item in cards],
        "attachments": [item.model_dump(mode="json", exclude_none=True) for item in attachment_inputs],
    }
