"""Bounded Session metadata passed to one native RLM Turn."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fleet_rlm.sessions.models import SessionHistory

_MAX_RECENT_PREVIEWS = 6
_MAX_PREVIEW_CHARS = 320


@dataclass(frozen=True, slots=True)
class TurnPreview:
    ordinal: int
    role: Literal["user", "assistant"]
    preview: str

    def to_input(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "role": self.role,
            "preview": self.preview,
        }


@dataclass(frozen=True, slots=True)
class SessionContextManifest:
    session_id: UUID
    checkpoint_version: int
    message_count: int
    recent: tuple[TurnPreview, ...]

    def to_input(self) -> dict[str, object]:
        return {
            "session_id": str(self.session_id),
            "checkpoint_version": self.checkpoint_version,
            "message_count": self.message_count,
            "recent": [item.to_input() for item in self.recent],
        }


def build_session_context_manifest(
    session_id: UUID,
    checkpoint_version: int,
    history: SessionHistory,
) -> SessionContextManifest:
    """Project complete committed history into a fixed-size recent manifest."""
    message_count = len(history.messages)
    first_recent = max(0, message_count - _MAX_RECENT_PREVIEWS)
    recent = tuple(
        TurnPreview(
            ordinal=index + 1,
            role=message.role,
            preview=message.content[:_MAX_PREVIEW_CHARS],
        )
        for index, message in enumerate(history.messages[first_recent:], start=first_recent)
    )
    return SessionContextManifest(session_id, checkpoint_version, message_count, recent)
