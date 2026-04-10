"""Worker-request helpers for websocket transport."""

from __future__ import annotations

from collections.abc import Callable

from fleet_rlm.worker import WorkspaceTaskRequest

from .turn_setup import PreparedStreamingTurn
from .types import ChatAgentProtocol


def build_workspace_task_request(
    *,
    agent: ChatAgentProtocol,
    prepared_turn: PreparedStreamingTurn,
    cancel_check: Callable[[], bool],
) -> WorkspaceTaskRequest:
    """Build the worker request for one websocket message turn."""

    return WorkspaceTaskRequest(
        agent=agent,
        message=prepared_turn.message,
        execution_mode=prepared_turn.execution_mode,
        trace=prepared_turn.trace,
        docs_path=prepared_turn.docs_path,
        repo_url=prepared_turn.repo_url,
        repo_ref=prepared_turn.repo_ref,
        context_paths=(
            list(prepared_turn.context_paths)
            if prepared_turn.context_paths is not None
            else None
        ),
        batch_concurrency=prepared_turn.batch_concurrency,
        workspace_id=prepared_turn.workspace_id,
        cancel_check=cancel_check,
        prepare=prepared_turn.prepare_worker,
    )
