"""RLM unit-test fixtures."""

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
def sample_context(sample_prepared: PreparedChatRuntime) -> ChatExecutionContext:
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
