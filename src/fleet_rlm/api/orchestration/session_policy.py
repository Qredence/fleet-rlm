"""Compatibility session-policy shim delegating to outer orchestration."""

from __future__ import annotations

from typing import Any

from fleet_rlm.orchestration_app import (
    SessionSwitchOutcome,
    switch_orchestration_session,
)

from ..dependencies import ServerState
from ..routers.ws.types import ChatAgentProtocol, LocalPersistFn


async def switch_execution_session(
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
) -> SessionSwitchOutcome:
    """Delegate legacy websocket session switching to orchestration_app."""

    return await switch_orchestration_session(
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
