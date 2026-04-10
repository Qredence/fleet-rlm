"""Websocket chat session switching and state restoration."""

from __future__ import annotations

from typing import Any

from fleet_rlm.agent_host.sessions import (
    OrchestrationSessionContext,
    switch_orchestration_session,
)

from ...dependencies import ServerState
from .types import ChatAgentProtocol, LocalPersistFn


async def switch_session_if_needed(
    *,
    state: ServerState,
    agent: ChatAgentProtocol,
    interpreter: object | None,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    owner_tenant_claim: str,
    owner_user_claim: str,
    active_key: str | None,
    session_record: dict[str, Any] | None,
    last_loaded_docs_path: str | None,
    local_persist: LocalPersistFn,
) -> tuple[str, str, dict[str, Any], str | None, OrchestrationSessionContext]:
    """Switch and restore session state when session identity changed."""
    outcome = await switch_orchestration_session(
        state=state,
        agent=agent,
        interpreter=interpreter,
        workspace_id=workspace_id,
        user_id=user_id,
        sess_id=sess_id,
        owner_tenant_claim=owner_tenant_claim,
        owner_user_claim=owner_user_claim,
        active_key=active_key,
        session_record=session_record,
        last_loaded_docs_path=last_loaded_docs_path,
        local_persist=local_persist,
    )
    return (
        outcome.key,
        outcome.manifest_path,
        outcome.session_record,
        outcome.last_loaded_docs_path,
        outcome.orchestration_session,
    )
