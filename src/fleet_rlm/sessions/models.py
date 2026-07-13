"""Validated immutable values for Session, Turn input, and History."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal, cast
from uuid import UUID

from fleet_rlm.sessions.committed_turn import CommittedTurn


class TurnInputValidationError(ValueError):
    """Raised when canonical Turn input cannot be bound to a Run claim."""


@dataclass(frozen=True, slots=True)
class TurnAccess:
    """Authenticated tenant/workspace authority for one Turn."""

    user_id: UUID
    workspace_id: UUID


@dataclass(frozen=True, slots=True)
class TurnInput:
    """Version-1 user input bound to Session-scoped idempotency."""

    text: str
    attachment_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise TurnInputValidationError("text must contain a non-whitespace character")
        if len(self.text) > 100_000:
            raise TurnInputValidationError("text must contain at most 100000 characters")
        if len(self.attachment_ids) > 32:
            raise TurnInputValidationError("at most 32 Attachments may be selected")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise TurnInputValidationError("attachment_ids must not contain duplicates")

    @property
    def canonical_json(self) -> str:
        """Return stable versioned JSON for persistence and hashing."""
        return json.dumps(
            {
                "schema_version": 1,
                "text": self.text,
                "attachment_ids": [str(attachment_id) for attachment_id in self.attachment_ids],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def fingerprint(self) -> str:
        """Return the SHA-256 claim binding for this exact ordered input."""
        return sha256(self.canonical_json.encode("utf-8")).hexdigest()


class TurnInputCodec:
    @staticmethod
    def encode(value: TurnInput) -> dict[str, object]:
        return {
            "schema_version": 1,
            "text": value.text,
            "attachment_ids": [str(item) for item in value.attachment_ids],
        }

    @staticmethod
    def decode(value: object) -> TurnInput:
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "text",
            "attachment_ids",
        }:
            raise TurnInputValidationError("stored Turn input is invalid")
        schema_version = value.get("schema_version")
        text = value.get("text")
        attachment_ids = value.get("attachment_ids")
        if (
            schema_version != 1
            or not isinstance(text, str)
            or not isinstance(attachment_ids, list)
            or any(not isinstance(item, str) for item in attachment_ids)
        ):
            raise TurnInputValidationError("stored Turn input is invalid")
        try:
            return TurnInput(
                text,
                tuple(UUID(item) for item in cast(list[str], attachment_ids)),
            )
        except (TypeError, ValueError) as exc:
            raise TurnInputValidationError("stored Turn input is invalid") from exc


@dataclass(frozen=True, slots=True)
class HistoryMessage:
    role: Literal["user", "assistant"]
    content: str


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

    @property
    def content(self) -> str:
        return self.committed.text


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
