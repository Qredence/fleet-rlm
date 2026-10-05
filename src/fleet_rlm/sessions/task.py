"""Host-authorized, bounded active-task state for a Session."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

import dspy

from fleet_rlm.json_types import JsonValue
from fleet_rlm.paths import VolumePaths
from fleet_rlm.sessions.catalog import SessionCatalog
from fleet_rlm.sessions.errors import SessionError
from fleet_rlm.tool_events import ToolEventView
from fleet_rlm.workspace.storage import WorkspaceVolumeGateway

TASK_CHECKPOINT_VERSION = 1
TASK_CHECKPOINT_MAX_BYTES = 16_384
_MAX_GOAL_CHARS = 4_000
_MAX_LIST_ITEMS = 40
_MAX_ITEM_CHARS = 500
_MAX_SOURCE_REVISIONS = 40


class TaskCheckpointError(SessionError):
    def __init__(self, message: str) -> None:
        self.public_message = message
        super().__init__(message)


class TaskCheckpointMissingError(TaskCheckpointError): ...


class TaskCheckpointCorruptError(TaskCheckpointError): ...


class TaskCheckpointConflictError(TaskCheckpointError): ...


@dataclass(frozen=True, slots=True)
class TaskCheckpoint:
    revision: int
    goal: str
    decisions: tuple[str, ...]
    relevant_paths: tuple[str, ...]
    source_revisions: Mapping[str, str]
    completed_work: tuple[str, ...]
    pending_work: tuple[str, ...]


def task_checkpoint_summary(checkpoint: TaskCheckpoint) -> str:
    parts = [f"Goal: {checkpoint.goal[:500]}"]
    for label, values in (
        ("Pending", checkpoint.pending_work),
        ("Decisions", checkpoint.decisions),
        ("Completed", checkpoint.completed_work),
        ("Paths", checkpoint.relevant_paths),
    ):
        if values:
            parts.append(f"{label}: " + "; ".join(v[:160] for v in values[-4:]))
    if checkpoint.source_revisions:
        parts.append(
            "Source revisions: " + "; ".join(f"{p}: {r}" for p, r in list(checkpoint.source_revisions.items())[:4])
        )
    return "\n".join(parts)[:2048]


class _SessionAuthorization(Protocol):
    async def get(self, session_id: UUID, *, user_id: UUID, workspace_id: UUID) -> object: ...


class SessionTaskService:
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
        if not isinstance(first_request, str) or not first_request.strip():
            raise ValueError("first_request must be a nonempty string")
        goal, pending = _fit_initial_checkpoint_text(first_request)
        await self._authorize(session_id, user_id=user_id, workspace_id=workspace_id)
        path = self._logical_path(session_id)
        async with self._write_lock:
            try:
                stored = await self._volume.read_bytes(workspace_id, path, max_bytes=TASK_CHECKPOINT_MAX_BYTES)
            except FileNotFoundError:
                checkpoint = TaskCheckpoint(1, goal, (), (), {}, (), (pending,))
                await self._write(workspace_id, path, checkpoint)
                return checkpoint
            return _decode(stored)

    async def read(self, session_id: UUID, *, user_id: UUID, workspace_id: UUID) -> TaskCheckpoint:
        await self._authorize(session_id, user_id=user_id, workspace_id=workspace_id)
        try:
            raw = await self._volume.read_bytes(
                workspace_id, self._logical_path(session_id), max_bytes=TASK_CHECKPOINT_MAX_BYTES
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
        return str(self._paths.session_dir(session_id) / "task.json")

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
    return tuple(_bounded_text(v, label, _MAX_ITEM_CHARS, nonempty=True) for v in values)


def _bounded_revisions(values: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(values, Mapping) or len(values) > _MAX_SOURCE_REVISIONS:
        raise ValueError(f"source_revisions must contain at most {_MAX_SOURCE_REVISIONS} entries")
    return {
        _bounded_text(p, "source_revisions path", _MAX_ITEM_CHARS, nonempty=True): _bounded_text(
            r, "source_revisions revision", _MAX_ITEM_CHARS, nonempty=True
        )
        for p, r in values.items()
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
    goal = first_request if len(first_request) <= _MAX_GOAL_CHARS else f"{first_request[: _MAX_GOAL_CHARS - 3]}..."
    pending = goal.strip()
    if len(pending) > _MAX_ITEM_CHARS:
        pending = f"{pending[: _MAX_ITEM_CHARS - 3]}..."

    def _size(g: str, p: str) -> int:
        return len(_encode(TaskCheckpoint(1, g, (), (), {}, (), (p,))))

    if _size(goal, pending) <= TASK_CHECKPOINT_MAX_BYTES:
        return goal, pending

    low, high = 0, min(len(first_request) - 1, _MAX_GOAL_CHARS - 3)
    best = (goal, pending)
    while low <= high:
        mid = (low + high) // 2
        cand_g = f"{first_request[:mid]}..."
        cand_p = cand_g.strip()
        if len(cand_p) > _MAX_ITEM_CHARS:
            cand_p = f"{cand_p[: _MAX_ITEM_CHARS - 3]}..."
        if _size(cand_g, cand_p) <= TASK_CHECKPOINT_MAX_BYTES:
            best = (cand_g, cand_p)
            low = mid + 1
        else:
            high = mid - 1
    return best


_CHECKPOINT_KEYS = frozenset(
    {
        "schema_version",
        "revision",
        "goal",
        "decisions",
        "relevant_paths",
        "source_revisions",
        "completed_work",
        "pending_work",
    }
)


def _decode(raw: bytes) -> TaskCheckpoint:
    try:
        if len(raw) > TASK_CHECKPOINT_MAX_BYTES:
            raise ValueError("oversized")
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != _CHECKPOINT_KEYS:
            raise ValueError("invalid keys")
        ver, rev = value["schema_version"], value["revision"]
        if (
            not isinstance(ver, int)
            or isinstance(ver, bool)
            or ver != TASK_CHECKPOINT_VERSION
            or not _valid_revision(rev)
            or rev < 1
        ):
            raise ValueError("invalid version or revision")
        checkpoint = TaskCheckpoint(
            revision=rev,
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


class _AsyncDispatcher(Protocol):
    def run(self, awaitable: Any, *, deadline: float | None = None, check_authority: Any = None) -> Any: ...


class SessionTaskToolHost:
    def __init__(
        self,
        service: SessionTaskService,
        *,
        session_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
        dispatcher: _AsyncDispatcher,
    ) -> None:
        self._service = service
        self._session_id = session_id
        self._user_id = user_id
        self._workspace_id = workspace_id
        self._dispatcher = dispatcher

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def read_active_task() -> dict[str, object]:
            checkpoint = self._dispatcher.run(
                self._service.read(self._session_id, user_id=self._user_id, workspace_id=self._workspace_id)
            )
            return _tool_payload(checkpoint)

        def update_active_task(
            expected_revision: int,
            goal: str | None = None,
            decisions: list[str] | None = None,
            relevant_paths: list[str] | None = None,
            source_revisions: dict[str, str] | None = None,
            completed_work: list[str] | None = None,
            pending_work: list[str] | None = None,
        ) -> dict[str, object]:
            checkpoint = self._dispatcher.run(
                self._service.update(
                    self._session_id,
                    user_id=self._user_id,
                    workspace_id=self._workspace_id,
                    expected_revision=expected_revision,
                    goal=goal,
                    decisions=decisions,
                    relevant_paths=relevant_paths,
                    source_revisions=source_revisions,
                    completed_work=completed_work,
                    pending_work=pending_work,
                )
            )
            return _tool_payload(checkpoint)

        return (
            dspy.Tool(
                read_active_task,
                name="read_active_task",
                desc="Read the current goal and progress for this Session when continuing prior work.",
                args={},
            ),
            dspy.Tool(
                update_active_task,
                name="update_active_task",
                desc=(
                    "Record active task goal, decisions, relevant paths and source revisions, completed work, "
                    "or pending work. Supply the revision from read_active_task; failed or cancelled work "
                    "remains pending unless this tool explicitly changes it."
                ),
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        def update_input(arguments: Mapping[str, Any]) -> JsonValue:
            result: dict[str, JsonValue] = {}
            revision = arguments.get("expected_revision")
            if isinstance(revision, int) and not isinstance(revision, bool):
                result["expected_revision"] = revision
            for field_name in (
                "goal",
                "decisions",
                "relevant_paths",
                "source_revisions",
                "completed_work",
                "pending_work",
            ):
                val = arguments.get(field_name)
                if isinstance(val, str):
                    result[f"{field_name}_chars"] = len(val)
                elif isinstance(val, (list, dict)):
                    result[f"{field_name}_count"] = len(val)
            return result

        def checkpoint_output(result: object) -> JsonValue:
            if not isinstance(result, Mapping):
                return {}
            payload: dict[str, JsonValue] = {}
            for key in (
                "revision",
                "goal_chars",
                "decisions_count",
                "relevant_paths_count",
                "source_revisions_count",
                "completed_work_count",
                "pending_work_count",
            ):
                val = result.get(key)
                if isinstance(val, int) and not isinstance(val, bool):
                    payload[key] = val
            return payload

        return MappingProxyType(
            {
                "read_active_task": ToolEventView(output_projection=checkpoint_output),
                "update_active_task": ToolEventView(input_projection=update_input, output_projection=checkpoint_output),
            }
        )


def _tool_payload(checkpoint: TaskCheckpoint) -> dict[str, object]:
    return {
        "schema_version": TASK_CHECKPOINT_VERSION,
        "revision": checkpoint.revision,
        "goal": checkpoint.goal,
        "decisions": list(checkpoint.decisions),
        "relevant_paths": list(checkpoint.relevant_paths),
        "source_revisions": dict(checkpoint.source_revisions),
        "completed_work": list(checkpoint.completed_work),
        "pending_work": list(checkpoint.pending_work),
        "goal_chars": len(checkpoint.goal),
        "decisions_count": len(checkpoint.decisions),
        "relevant_paths_count": len(checkpoint.relevant_paths),
        "source_revisions_count": len(checkpoint.source_revisions),
        "completed_work_count": len(checkpoint.completed_work),
        "pending_work_count": len(checkpoint.pending_work),
    }


__all__ = [
    "TASK_CHECKPOINT_MAX_BYTES",
    "TASK_CHECKPOINT_VERSION",
    "SessionTaskService",
    "SessionTaskToolHost",
    "TaskCheckpoint",
    "TaskCheckpointConflictError",
    "TaskCheckpointCorruptError",
    "TaskCheckpointError",
    "TaskCheckpointMissingError",
    "task_checkpoint_summary",
]
