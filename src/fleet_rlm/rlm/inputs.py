"""Serialize Turn Context discovery metadata for FleetRLMSignature inputs.

Bodies stay behind Host-Mediated Tools; signature fields are metadata only.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.files.models import AttachmentRef
from fleet_rlm.files.workspace_models import DENO_WORKSPACE_CAPABILITY, WorkspaceCapabilityMetadata
from fleet_rlm.skills.models import SkillCard


def skill_card_metadata(card: SkillCard | Any) -> dict[str, Any]:
    """Public Skill Card keys only — never instructions or resource bodies."""
    return {
        "id": _id_str(getattr(card, "id", "")),
        "name": str(getattr(card, "name", "")),
        "description": str(getattr(card, "description", "")),
        "scope": str(getattr(card, "scope", "")),
        "version": str(getattr(card, "version", "")),
        "trust": str(getattr(card, "trust", "")),
        "affordances": list(getattr(card, "affordances", ()) or ()),
        "resources_available": bool(getattr(card, "resources_available", False)),
        "capability_refs": list(getattr(card, "capability_refs", ()) or ()),
        "task_contract_ref": getattr(card, "task_contract_ref", None),
    }


def attachment_metadata(ref: AttachmentRef | Any) -> dict[str, Any]:
    """Attachment identity + bounded metadata — never bytes or paths."""
    meta: dict[str, Any] = {
        "id": _id_str(getattr(ref, "id", "")),
        "filename": str(getattr(ref, "filename", "")),
        "byte_size": int(getattr(ref, "byte_size", 0) or 0),
    }
    content_type = getattr(ref, "content_type", None)
    if content_type is not None:
        meta["content_type"] = str(content_type)
    checksum = getattr(ref, "checksum_sha256", None)
    if checksum:
        meta["checksum_sha256"] = str(checksum)
    return meta


def build_rlm_input_kwargs(
    *,
    request: str,
    session_context: SessionContextManifest,
    skill_cards: tuple[Any, ...] | list[Any] = (),
    attachments: tuple[Any, ...] | list[Any] = (),
    workspace: WorkspaceCapabilityMetadata = DENO_WORKSPACE_CAPABILITY,
) -> dict[str, Any]:
    """Kwargs for ``rlm.aforward`` / ``forward`` matching FleetRLMSignature."""
    context = session_context.to_input()
    context["workspace"] = workspace.to_input()
    return {
        "request": request,
        "session_context": context,
        "skill_cards": [skill_card_metadata(card) for card in skill_cards],
        "attachments": [attachment_metadata(ref) for ref in attachments],
    }


def _id_str(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    return str(value)
