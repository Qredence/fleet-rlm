"""Validated immutable values for Session, Turn input, and History."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Literal, cast
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
        return {
            "schema_version": 2,
            "text": value.text,
            "attachment_ids": [str(item) for item in value.attachment_ids],
            "skill_selections": [
                {
                    "id": str(selection.id),
                    "expected_version": selection.expected_version,
                }
                for selection in value.skill_selections
            ],
        }

    @staticmethod
    def decode(value: object) -> TurnInput:
        if not isinstance(value, dict):
            raise TurnInputValidationError("stored Turn input is invalid")
        stored = cast(dict[object, object], value)
        schema_version = stored.get("schema_version")
        if schema_version == 1:
            return TurnInputCodec._decode_v1(stored)
        if schema_version == 2:
            return TurnInputCodec._decode_v2(stored)
        raise TurnInputValidationError("stored Turn input is invalid")

    @staticmethod
    def _decode_v1(value: dict[object, object]) -> TurnInput:
        if set(value) != {"schema_version", "text", "attachment_ids"}:
            raise TurnInputValidationError("stored Turn input is invalid")
        text = value.get("text")
        attachment_ids = value.get("attachment_ids")
        if (
            not isinstance(text, str)
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

    @staticmethod
    def _decode_v2(value: dict[object, object]) -> TurnInput:
        if set(value) != {"schema_version", "text", "attachment_ids", "skill_selections"}:
            raise TurnInputValidationError("stored Turn input is invalid")
        text = value.get("text")
        attachment_ids = value.get("attachment_ids")
        skill_selections = value.get("skill_selections")
        if (
            not isinstance(text, str)
            or not isinstance(attachment_ids, list)
            or any(not isinstance(item, str) for item in attachment_ids)
            or not isinstance(skill_selections, list)
            or any(
                not isinstance(item, dict)
                or set(item) != {"id", "expected_version"}
                or not isinstance(item.get("id"), str)
                or not isinstance(item.get("expected_version"), str)
                for item in skill_selections
            )
        ):
            raise TurnInputValidationError("stored Turn input is invalid")
        try:
            return TurnInput(
                text,
                tuple(UUID(item) for item in cast(list[str], attachment_ids)),
                tuple(
                    SkillSelectionRef(
                        UUID(cast(str, item["id"])),
                        cast(str, item["expected_version"]),
                    )
                    for item in cast(list[dict[str, object]], skill_selections)
                ),
            )
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
