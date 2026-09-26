"""DSPy tool adapter for a host-authorized Session task checkpoint."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

import dspy

from fleet_rlm.json_types import JsonValue
from fleet_rlm.sessions.task import TASK_CHECKPOINT_VERSION, SessionTaskService, TaskCheckpoint
from fleet_rlm.tool_events import ToolEventView


class _AsyncDispatcher(Protocol):
    def run(self, awaitable: Any, *, deadline: float | None = None, check_authority: Any = None) -> Any: ...


class SessionTaskToolHost:
    """Bind one authorized Session task service to synchronous DSPy Tools."""

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
            """Read the bounded active task checkpoint for this Session."""
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
            """Update active task state using the revision returned by its last read."""
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
                value = arguments.get(field_name)
                if isinstance(value, str):
                    result[f"{field_name}_chars"] = len(value)
                elif isinstance(value, (list, dict)):
                    result[f"{field_name}_count"] = len(value)
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
                value = result.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    payload[key] = value
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
