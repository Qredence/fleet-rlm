"""Workspace Agent host/protocol boundary.

The nested ``runtime`` module is a packaged stdlib-only artifact executed only
inside Daytona.  Host code should import transport helpers from ``client`` and
source/checksum helpers from ``protocol``.
"""

from __future__ import annotations

from fleet_rlm.daytona.workspace_agent.client import (
    _AGENT_SESSIONS,
    WorkspaceAgentMetrics,
    _agent_session,
    _WorkspaceAgentSession,
    decode_workspace_agent_response,
    drop_workspace_agent_session,
    run_workspace_agent,
    run_workspace_agent_async,
    workspace_agent_metrics,
)
from fleet_rlm.daytona.workspace_agent.protocol import (
    WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S,
    WORKSPACE_AGENT_INSTALL_PATH,
    WORKSPACE_AGENT_MODULE_NAME,
    WORKSPACE_AGENT_PROTOCOL_VERSION,
    WORKSPACE_AGENT_REQUEST_MAX_BYTES,
    WORKSPACE_AGENT_RESPONSE_MAX_BYTES,
    WORKSPACE_AGENT_SUPPORTED_OPERATIONS,
    WorkspaceAgentConflictError,
    WorkspaceAgentProtocolError,
    WorkspaceAgentStorageError,
    build_installed_workspace_agent_source,
    build_workspace_agent_code,
    build_workspace_agent_request_code,
    workspace_agent_runtime_checksum,
    workspace_agent_runtime_source,
)

__all__ = [
    "WORKSPACE_AGENT_CODE_RUN_TIMEOUT_S",
    "WORKSPACE_AGENT_INSTALL_PATH",
    "WORKSPACE_AGENT_MODULE_NAME",
    "WORKSPACE_AGENT_PROTOCOL_VERSION",
    "WORKSPACE_AGENT_REQUEST_MAX_BYTES",
    "WORKSPACE_AGENT_RESPONSE_MAX_BYTES",
    "WORKSPACE_AGENT_SUPPORTED_OPERATIONS",
    "_AGENT_SESSIONS",
    "WorkspaceAgentConflictError",
    "WorkspaceAgentMetrics",
    "WorkspaceAgentProtocolError",
    "WorkspaceAgentStorageError",
    "_WorkspaceAgentSession",
    "_agent_session",
    "build_installed_workspace_agent_source",
    "build_workspace_agent_code",
    "build_workspace_agent_request_code",
    "decode_workspace_agent_response",
    "drop_workspace_agent_session",
    "run_workspace_agent",
    "run_workspace_agent_async",
    "workspace_agent_metrics",
    "workspace_agent_runtime_checksum",
    "workspace_agent_runtime_source",
]
