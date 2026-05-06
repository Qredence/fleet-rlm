"""Compatibility re-exports for websocket types.

Primary definitions now live in:

- ``fleet_rlm.api.runtime_services.chat_runtime`` — agent protocols and session types
- ``fleet_rlm.api.routers.ws.stream`` — streaming event types
- ``fleet_rlm.api.routers.ws.turn_setup`` — Daytona request types
"""

from __future__ import annotations

from fleet_rlm.api.routers.ws.stream import WorkspaceEvent, WorkspaceTaskRequest
from fleet_rlm.api.routers.ws.turn_setup import (
    DaytonaChatRequestOptions,
    normalize_daytona_chat_request,
    prepare_daytona_workspace_for_turn,
)
from fleet_rlm.api.runtime_services.chat_runtime import (
    ChatAgentProtocol,
    LocalPersistFn,
    MaintenanceInterpreterProtocol,
    PreStreamSetupFn,
    SessionContext,
    StreamEventLike,
)

__all__ = [
    "ChatAgentProtocol",
    "DaytonaChatRequestOptions",
    "LocalPersistFn",
    "MaintenanceInterpreterProtocol",
    "PreStreamSetupFn",
    "SessionContext",
    "StreamEventLike",
    "WorkspaceEvent",
    "WorkspaceTaskRequest",
    "normalize_daytona_chat_request",
    "prepare_daytona_workspace_for_turn",
]
