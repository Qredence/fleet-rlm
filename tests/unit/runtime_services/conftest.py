"""Shared fixtures for stream_turn test modules.

Extracted from the former monolithic ``test_stream_turn.py`` during Phase
2A.2 test/contract cleanup so ``test_stream_turn_legacy_backend.py``,
``test_stream_turn_execution_backend.py``, ``test_stream_turn_controls.py``,
and ``test_stream_turn_errors.py`` can share the same default stub agent and
context fixtures without duplicating them per-file.

Other test modules in this directory (``test_chat_context.py``,
``test_prepare_chat_runtime.py``) define their own local fixtures of the
same name where their needs differ; a fixture defined directly in a test
module always takes precedence over one from this conftest.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.api.auth.types import NormalizedIdentity
from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext, TurnControls
from fleet_rlm.api.runtime_services.chat_runtime import PreparedChatRuntime
from tests.unit.runtime_services.fakes import StubAgent


@pytest.fixture
def stub_agent() -> StubAgent:
    return StubAgent()


@pytest.fixture
def sample_prepared(stub_agent: StubAgent) -> PreparedChatRuntime:
    """Minimal PreparedChatRuntime for stream_turn tests.

    Most legacy-path unit tests pass ``planner_lm`` as ``agent_runtime``
    explicitly to avoid repeating a separate runtime fixture in every case.
    Production callers must pass their context-managed AgentRuntime instead.
    """
    cfg = SimpleNamespace(
        ws_default_workspace_id="default",
        ws_default_user_id="anonymous",
    )
    return PreparedChatRuntime(
        cfg=cfg,  # type: ignore[arg-type]
        planner_lm=stub_agent,
        delegate_lm=None,
        repository=object(),
        persistence=None,
        persistence_required=False,
        identity_rows=None,
    )


@pytest.fixture
def sample_context(
    sample_prepared: PreparedChatRuntime,
) -> ChatExecutionContext:
    """Default ChatExecutionContext with no session and default TurnControls."""
    return ChatExecutionContext(
        prepared=sample_prepared,
        identity=NormalizedIdentity(
            tenant_claim="tenant-abc",
            user_claim="user-xyz",
            email="test@example.com",
        ),
        session_id=None,
        canonical_workspace_id="workspace-abc",
        canonical_user_id="user-xyz",
        owner_tenant_claim="tenant-abc",
        owner_user_claim="user-xyz",
        cancel_flag={"cancelled": False},
        controls=TurnControls(),
    )
