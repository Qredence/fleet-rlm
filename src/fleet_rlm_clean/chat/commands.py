"""Application commands for chat turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class ChatTurnCommand:
    """Validated turn intent after auth/identity resolution."""

    user_id: UUID
    workspace_id: UUID
    message: str
    session_id: UUID = field(default_factory=uuid4)
    attachment_ids: tuple[UUID, ...] = ()
    idempotency_key: str = ""
