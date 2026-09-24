"""Host-authorized, bounded active-task state for a Session.

Task checkpoints live beside the Session workspace on the shared volume.  They
are deliberately outside the mounted ``sessions/<id>/workspace`` directory.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from fleet_rlm.paths import VolumePaths
from fleet_rlm.sessions.catalog import SessionCatalog
from fleet_rlm.sessions.errors import SessionError
from fleet_rlm.workspace.storage import WorkspaceVolumeGateway

TASK_CHECKPOINT_VERSION = 1
TASK_CHECKPOINT_MAX_BYTES = 16_384
_MAX_GOAL_CHARS = 4_000
_MAX_LIST_ITEMS = 40
_MAX_ITEM_CHARS = 500
_MAX_SOURCE_REVISIONS = 40


class TaskCheckpointError(SessionError):
    """Base error for active-task checkpoint failures."""

    def __init__(self, message: str) -> None:
        self.public_message = message
        super().__init__(message)


class TaskCheckpointMissingError(TaskCheckpointError):
    """The Session has not seeded its task checkpoint yet."""


class TaskCheckpointCorruptError(TaskCheckpointError):
    """Stored task checkpoint is malformed or unsupported."""


class TaskCheckpointConflictError(TaskCheckpointError):
    """The checkpoint changed since the caller last read it."""


@dataclass(frozen=True, slots=True)
class TaskCheckpoint:
    """Validated task state returned to an authorized host caller."""

    revision: int
    goal: str
    decisions: tuple[str, ...]
    relevant_paths: tuple[str, ...]
    source_revisions: Mapping[str, str]
    completed_work: tuple[str, ...]
    pending_work: tuple[str, ...]


class _SessionAuthorization(Protocol):
    async def get(self, session_id: UUID, *, user_id: UUID, workspace_id: UUID) -> object: ...


class SessionTaskService:
    """Persist active task state after checking Session and Workspace authority.

    Callers supply authenticated user and workspace identities; the Session
    catalog verifies the tuple before this service derives a volume path.
    """

    def __init__(
        self,
        sessions: SessionCatalog | _SessionAuthorization,
        volume: WorkspaceVolumeGateway,
        paths: VolumePaths,
    ) -> None:
        self._sessions = sessions
        self._volume = volume
        self._paths = paths
        self._write_lock = asyncio.Lock()

    async def seed(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        first_request: str,
    ) -> TaskCheckpoint:
        """Create the initial pending goal once; preserve an existing checkpoint."""
        if not isinstance(first_request, str) or not first_request.strip():
            raise ValueError("first_request must be a nonempty string")
        goal, pending = _fit_initial_checkpoint_text(first_request)
        await self._authorize(session_id, user_id=user_id, workspace_id=workspace_id)
        path = self._logical_path(session_id)
        async with self._write_lock:
            try:
                stored = await self._volume.read_bytes(workspace_id, path, max_bytes=TASK_CHECKPOINT_MAX_BYTES)
            except FileNotFoundError:
                checkpoint = TaskCheckpoint(
                    revision=1,
                    goal=goal,
                    decisions=(),
                    relevant_paths=(),
                    source_revisions={},
                    completed_work=(),
                    pending_work=(pending,),
                )
                await self._write(workspace_id, path, checkpoint)
                return checkpoint
            return _decode(stored)

    async def read(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> TaskCheckpoint:
        await self._authorize(session_id, user_id=user_id, workspace_id=workspace_id)
        try:
            raw = await self._volume.read_bytes(
                workspace_id,
                self._logical_path(session_id),
                max_bytes=TASK_CHECKPOINT_MAX_BYTES,
            )
        except FileNotFoundError as exc:
            raise TaskCheckpointMissingError("task checkpoint has not been seeded") from exc
        return _decode(raw)

    async def update(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        expected_revision: int,
        goal: str | None = None,
        decisions: Sequence[str] | None = None,
        relevant_paths: Sequence[str] | None = None,
        source_revisions: Mapping[str, str] | None = None,
        completed_work: Sequence[str] | None = None,
        pending_work: Sequence[str] | None = None,
    ) -> TaskCheckpoint:
        """Apply a bounded patch only when ``expected_revision`` still matches."""
        if not _valid_revision(expected_revision):
            raise ValueError("expected_revision must be a non-negative integer")
        await self._authorize(session_id, user_id=user_id, workspace_id=workspace_id)
        path = self._logical_path(session_id)
        async with self._write_lock:
            try:
                raw = await self._volume.read_bytes(workspace_id, path, max_bytes=TASK_CHECKPOINT_MAX_BYTES)
            except FileNotFoundError as exc:
                raise TaskCheckpointMissingError("task checkpoint has not been seeded") from exc
            current = _decode(raw)
            if current.revision != expected_revision:
                raise TaskCheckpointConflictError("task checkpoint revision changed")
            checkpoint = TaskCheckpoint(
                revision=current.revision + 1,
                goal=current.goal if goal is None else _bounded_text(goal, "goal", _MAX_GOAL_CHARS, nonempty=True),
                decisions=current.decisions if decisions is None else _bounded_list(decisions, "decisions"),
                relevant_paths=(
                    current.relevant_paths
                    if relevant_paths is None
                    else _bounded_list(relevant_paths, "relevant_paths")
                ),
                source_revisions=(
                    current.source_revisions if source_revisions is None else _bounded_revisions(source_revisions)
                ),
                completed_work=(
                    current.completed_work
                    if completed_work is None
                    else _bounded_list(completed_work, "completed_work")
                ),
                pending_work=(
                    current.pending_work if pending_work is None else _bounded_list(pending_work, "pending_work")
                ),
            )
            await self._write(workspace_id, path, checkpoint)
            return checkpoint

    async def _authorize(self, session_id: UUID, *, user_id: UUID, workspace_id: UUID) -> None:
        record = await self._sessions.get(session_id, user_id=user_id, workspace_id=workspace_id)
        if getattr(record, "id", None) != session_id or getattr(record, "workspace_id", None) != workspace_id:
            raise TaskCheckpointError("Session authority did not match the requested workspace")

    def _logical_path(self, session_id: UUID) -> str:
        path = self._paths.session_dir(session_id) / "task.json"
        return str(path)

    async def _write(self, workspace_id: UUID, path: str, checkpoint: TaskCheckpoint) -> None:
        payload = _encode(checkpoint)
        if len(payload) > TASK_CHECKPOINT_MAX_BYTES:
            raise ValueError("task checkpoint exceeds its byte limit")
        await self._volume.write_bytes(workspace_id, path, payload, max_bytes=TASK_CHECKPOINT_MAX_BYTES)


def _valid_revision(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _bounded_text(value: object, label: str, limit: int, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (nonempty and not value.strip()):
        raise ValueError(f"{label} must be a string of at most {limit} characters")
    return value


def _bounded_list(values: Sequence[str], label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or len(values) > _MAX_LIST_ITEMS:
        raise ValueError(f"{label} must contain at most {_MAX_LIST_ITEMS} items")
    return tuple(_bounded_text(value, label, _MAX_ITEM_CHARS, nonempty=True) for value in values)


def _bounded_revisions(values: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(values, Mapping) or len(values) > _MAX_SOURCE_REVISIONS:
        raise ValueError(f"source_revisions must contain at most {_MAX_SOURCE_REVISIONS} entries")
    return {
        _bounded_text(path, "source_revisions path", _MAX_ITEM_CHARS, nonempty=True): _bounded_text(
            revision, "source_revisions revision", _MAX_ITEM_CHARS, nonempty=True
        )
        for path, revision in values.items()
    }


def _encode(checkpoint: TaskCheckpoint) -> bytes:
    value = {
        "schema_version": TASK_CHECKPOINT_VERSION,
        "revision": checkpoint.revision,
        "goal": checkpoint.goal,
        "decisions": list(checkpoint.decisions),
        "relevant_paths": list(checkpoint.relevant_paths),
        "source_revisions": dict(checkpoint.source_revisions),
        "completed_work": list(checkpoint.completed_work),
        "pending_work": list(checkpoint.pending_work),
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _fit_initial_checkpoint_text(first_request: str) -> tuple[str, str]:
    """Bound seed text by both character limits and the encoded checkpoint size."""
    goal = first_request if len(first_request) <= _MAX_GOAL_CHARS else f"{first_request[: _MAX_GOAL_CHARS - 3]}..."

    def pending_for(value: str) -> str:
        pending = value.strip()
        if len(pending) > _MAX_ITEM_CHARS:
            pending = f"{pending[: _MAX_ITEM_CHARS - 3]}..."
        return pending

    pending = pending_for(goal)

    def encoded_size(candidate_goal: str, candidate_pending: str) -> int:
        return len(
            _encode(
                TaskCheckpoint(
                    revision=1,
                    goal=candidate_goal,
                    decisions=(),
                    relevant_paths=(),
                    source_revisions={},
                    completed_work=(),
                    pending_work=(candidate_pending,),
                )
            )
        )

    if encoded_size(goal, pending) <= TASK_CHECKPOINT_MAX_BYTES:
        return goal, pending

    # Multibyte characters and JSON escaping can make a character-bounded goal
    # exceed the checkpoint's byte limit. Find the longest fitting prefix while
    # accounting for the duplicated pending-work preview as well.
    low = 0
    high = min(len(first_request) - 1, _MAX_GOAL_CHARS - 3)
    best: tuple[str, str] | None = None
    while low <= high:
        prefix_chars = (low + high) // 2
        candidate_goal = f"{first_request[:prefix_chars]}..."
        candidate_pending = pending_for(candidate_goal)
        if encoded_size(candidate_goal, candidate_pending) <= TASK_CHECKPOINT_MAX_BYTES:
            best = candidate_goal, candidate_pending
            low = prefix_chars + 1
        else:
            high = prefix_chars - 1

    if best is None:
        raise ValueError("task checkpoint exceeds its byte limit")
    return best


def _decode(raw: bytes) -> TaskCheckpoint:
    try:
        if len(raw) > TASK_CHECKPOINT_MAX_BYTES:
            raise ValueError("oversized")
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "revision",
            "goal",
            "decisions",
            "relevant_paths",
            "source_revisions",
            "completed_work",
            "pending_work",
        }:
            raise ValueError("invalid keys")
        version = value["schema_version"]
        revision = value["revision"]
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version != TASK_CHECKPOINT_VERSION
            or not _valid_revision(revision)
            or revision < 1
        ):
            raise ValueError("invalid version or revision")
        checkpoint = TaskCheckpoint(
            revision=value["revision"],
            goal=_bounded_text(value["goal"], "goal", _MAX_GOAL_CHARS, nonempty=True),
            decisions=_bounded_list(value["decisions"], "decisions"),
            relevant_paths=_bounded_list(value["relevant_paths"], "relevant_paths"),
            source_revisions=MappingProxyType(_bounded_revisions(value["source_revisions"])),
            completed_work=_bounded_list(value["completed_work"], "completed_work"),
            pending_work=_bounded_list(value["pending_work"], "pending_work"),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise TaskCheckpointCorruptError("stored task checkpoint is invalid") from exc
    if len(_encode(checkpoint)) > TASK_CHECKPOINT_MAX_BYTES:
        raise TaskCheckpointCorruptError("stored task checkpoint exceeds its byte limit")
    return checkpoint


__all__ = [
    "TASK_CHECKPOINT_MAX_BYTES",
    "TASK_CHECKPOINT_VERSION",
    "SessionTaskService",
    "TaskCheckpoint",
    "TaskCheckpointConflictError",
    "TaskCheckpointCorruptError",
    "TaskCheckpointError",
    "TaskCheckpointMissingError",
]
