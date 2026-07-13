"""Serialize Turn Context discovery metadata for FleetRLMSignature inputs.

Bodies stay behind Host-Mediated Tools; signature fields are metadata only.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import dspy

from fleet_rlm.files.models import AttachmentRef
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


def empty_history() -> dspy.History:
    return dspy.History(messages=[])


def resolve_history(history: Any) -> dspy.History:
    if isinstance(history, dspy.History):
        return history
    if history is None:
        return empty_history()
    messages = getattr(history, "messages", None)
    if isinstance(messages, list):
        normalized: list[dict[str, Any]] = []
        for item in messages:
            if isinstance(item, dict):
                normalized.append(dict(item))
        return dspy.History(messages=normalized)
    return empty_history()


def build_rlm_input_kwargs(
    *,
    request: str,
    history: Any = None,
    session_summary: str = "",
    skill_cards: tuple[Any, ...] | list[Any] = (),
    attachments: tuple[Any, ...] | list[Any] = (),
) -> dict[str, Any]:
    """Kwargs for ``rlm.aforward`` / ``forward`` matching FleetRLMSignature.

    History is passed as a plain message list (sandbox-safe). Callers may still
    supply ``dspy.History``; it is normalized here.
    """
    resolved = resolve_history(history)
    return {
        "request": request,
        "history": [dict(item) for item in list(resolved.messages or [])],
        "session_summary": session_summary or "",
        "skill_cards": [skill_card_metadata(card) for card in skill_cards],
        "attachments": [attachment_metadata(ref) for ref in attachments],
    }


def _id_str(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    return str(value)
