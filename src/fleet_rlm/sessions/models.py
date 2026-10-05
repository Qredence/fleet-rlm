"""Validated immutable values for Session, Turn input, and History."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID

from fleet_rlm.sessions.committed_turn import CommittedTurn
from fleet_rlm.skills.models import SkillSelectionRef


class TurnInputValidationError(ValueError):
    """Raised when canonical Turn input cannot be bound to a Run claim."""


@dataclass(frozen=True, slots=True)
class TurnAccess:
    """Authenticated tenant/workspace authority for one Turn."""

    user_id: UUID
    workspace_id: UUID


@dataclass(frozen=True, slots=True)
class TurnInput:
    """Version-2 user input bound to Session-scoped idempotency."""

    text: str
    attachment_ids: tuple[UUID, ...] = ()
    skill_selections: tuple[SkillSelectionRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise TurnInputValidationError("text must contain a non-whitespace character")
        if len(self.text) > 100_000:
            raise TurnInputValidationError("text must contain at most 100000 characters")
        if len(self.attachment_ids) > 32:
            raise TurnInputValidationError("at most 32 Attachments may be selected")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise TurnInputValidationError("attachment_ids must not contain duplicates")
        if len(self.skill_selections) > 4:
            raise TurnInputValidationError("at most 4 Skills may be selected")
        selection_ids = [selection.id for selection in self.skill_selections]
        if len(set(selection_ids)) != len(selection_ids):
            raise TurnInputValidationError("skill_selections must not contain duplicate ids")

    @property
    def canonical_json(self) -> str:
        """Return stable versioned JSON for persistence and hashing."""
        return json.dumps(
            {
                "schema_version": 2,
                "text": self.text,
                "attachment_ids": [str(attachment_id) for attachment_id in self.attachment_ids],
                "skill_selections": [
                    {
                        "id": str(selection.id),
                        "expected_version": selection.expected_version,
                    }
                    for selection in self.skill_selections
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def fingerprint(self) -> str:
        """Return the SHA-256 claim binding for this exact ordered input."""
        return sha256(self.canonical_json.encode("utf-8")).hexdigest()

    @property
    def acceptable_fingerprints(self) -> frozenset[str]:
        """Return fingerprints accepted for durable baseline replay.

        The canonical database baseline originally wrote v1 inputs without
        Skill selections. An otherwise identical v2 request with no selections
        must replay that supported row rather than report an idempotency conflict.
        """
        values = {self.fingerprint}
        if not self.skill_selections:
            legacy_json = json.dumps(
                {
                    "schema_version": 1,
                    "text": self.text,
                    "attachment_ids": [str(attachment_id) for attachment_id in self.attachment_ids],
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            values.add(sha256(legacy_json.encode("utf-8")).hexdigest())
        return frozenset(values)


class TurnInputCodec:
    @staticmethod
    def encode(value: TurnInput) -> dict[str, object]:
        return json.loads(value.canonical_json)

    @staticmethod
    def decode(value: object) -> TurnInput:
        if not isinstance(value, dict):
            raise TurnInputValidationError("stored Turn input is invalid")
        ver = value.get("schema_version")
        text = value.get("text")
        att = value.get("attachment_ids")
        if (
            ver not in (1, 2)
            or not isinstance(text, str)
            or not isinstance(att, list)
            or any(not isinstance(i, str) for i in att)
        ):
            raise TurnInputValidationError("stored Turn input is invalid")
        try:
            att_ids = tuple(UUID(i) for i in att)
            if ver == 1:
                if set(value) != {"schema_version", "text", "attachment_ids"}:
                    raise TurnInputValidationError("stored Turn input is invalid")
                return TurnInput(text, att_ids)
            if set(value) != {"schema_version", "text", "attachment_ids", "skill_selections"}:
                raise TurnInputValidationError("stored Turn input is invalid")
            skills = value.get("skill_selections")
            if not isinstance(skills, list):
                raise TurnInputValidationError("stored Turn input is invalid")
            selections: list[SkillSelectionRef] = []
            for s in skills:
                if not isinstance(s, dict) or set(s) != {"id", "expected_version"}:
                    raise TurnInputValidationError("stored Turn input is invalid")
                s_id, s_ver = s.get("id"), s.get("expected_version")
                if not isinstance(s_id, str) or not isinstance(s_ver, str):
                    raise TurnInputValidationError("stored Turn input is invalid")
                selections.append(SkillSelectionRef(UUID(s_id), s_ver))
            return TurnInput(text, att_ids, tuple(selections))
        except (TypeError, ValueError) as exc:
            raise TurnInputValidationError("stored Turn input is invalid") from exc


@dataclass(frozen=True, slots=True)
class HistoryMessage:
    role: Literal["user", "assistant"]
    content: str
    # The originating durable result is checkpoint metadata, not part of the
    # public message projection.  Keeping it here lets canonical model-facing
    # History exclude failure tombstones without losing the bounded audit pair
    # exposed by Session History and turn listing.
    committed_turn: CommittedTurn | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class SessionHistory:
    messages: tuple[HistoryMessage, ...] = ()


@dataclass(frozen=True, slots=True)
class UserTurnRecord:
    id: UUID
    session_id: UUID
    sequence: int
    input: TurnInput
    run_id: UUID


@dataclass(frozen=True, slots=True)
class AssistantTurnRecord:
    id: UUID
    session_id: UUID
    sequence: int
    committed: CommittedTurn
    run_id: UUID


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: UUID
    user_id: UUID
    workspace_id: UUID
    status: str
    title: str
    checkpoint_version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SequenceCursor:
    """An actual append-only Turn sequence cursor, never an offset."""

    after_sequence: int | None = None

    def __post_init__(self) -> None:
        if self.after_sequence is not None and (
            not isinstance(self.after_sequence, int) or isinstance(self.after_sequence, bool) or self.after_sequence < 0
        ):
            raise ValueError("after_sequence must be a non-negative integer")

    def next_after_sequence(self, last_sequence: int) -> int:
        if not isinstance(last_sequence, int) or isinstance(last_sequence, bool) or last_sequence < 1:
            raise ValueError("last_sequence must be a positive integer")
        if self.after_sequence is not None and last_sequence <= self.after_sequence:
            raise ValueError("next sequence must advance the cursor")
        return last_sequence


@dataclass(frozen=True, slots=True)
class SessionPage:
    items: tuple[SessionRecord, ...]
    total: int


@dataclass(frozen=True, slots=True)
class SessionTurnPage:
    items: tuple[UserTurnRecord | AssistantTurnRecord, ...]
    next_after_sequence: int | None


class SessionCatalog(Protocol):
    async def create(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        title: str,
    ) -> SessionRecord: ...

    async def list(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        status: str | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> SessionPage: ...

    async def get(self, session_id: UUID, *, user_id: UUID, workspace_id: UUID) -> SessionRecord: ...

    async def update(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        title: str | None,
        status: str | None,
    ) -> SessionRecord: ...

    async def archive(self, session_id: UUID, *, user_id: UUID, workspace_id: UUID) -> SessionRecord: ...

    async def turns(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        cursor: SequenceCursor,
        limit: int,
    ) -> SessionTurnPage: ...


class SessionError(RuntimeError):
    """Base error for Session domain failures."""


class SessionNotFoundError(SessionError):
    """Raised when a session id cannot be loaded."""


class SessionAccessDeniedError(SessionError):
    """Caller is not allowed to access the session (map publicly to not-found)."""


class SessionRetirementPendingError(SessionError):
    """Raised when a Session is archived durably but provider retirement failed."""

    def __init__(self, session_id: UUID) -> None:
        self.session_id = session_id
        super().__init__(f"session retirement is pending for {session_id}")
