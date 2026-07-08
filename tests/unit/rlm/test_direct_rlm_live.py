"""Live integration smoke for opt-in ``direct_rlm`` execution (Phase 2C).

Skipped unless ``DAYTONA_API_KEY`` and planner LM env vars are configured.
Excluded from default ``make test`` via ``live_daytona`` and ``live_llm`` markers.
"""

from __future__ import annotations

import os
from typing import Any

import dspy
import pytest

from fleet_rlm.api.auth.types import NormalizedIdentity
from fleet_rlm.api.bootstrap import get_planner_lm_from_env
from fleet_rlm.api.config import AppConfig
from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext, TurnControls
from fleet_rlm.api.runtime_services.chat_runtime import PreparedChatRuntime
from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend
from fleet_rlm.api.runtime_services.stream_turn import stream_turn
from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter
from fleet_rlm.runtime.events import RuntimeEventKind

pytestmark = [pytest.mark.live_daytona, pytest.mark.live_llm]


def _have_live_creds() -> bool:
    if not os.environ.get("DAYTONA_API_KEY"):
        return False
    if not os.environ.get("DSPY_LM_MODEL"):
        return False
    return bool(os.environ.get("DSPY_LM_API_KEY") or os.environ.get("OPENAI_API_KEY"))


class _LiveAgentRuntime:
    """Minimal agent-runtime shell exposing the pooled Daytona interpreter."""

    def __init__(self, interpreter: Any) -> None:
        self.interpreter = interpreter
        self.core_memory = ""
        self.history = dspy.History(messages=[])


@pytest.mark.asyncio
@pytest.mark.timeout(180)
async def test_direct_rlm_live_golden_path_reaches_done() -> None:
    """Opt-in ``direct_rlm`` stream reaches TEXT then terminal DONE for a simple turn."""
    if not _have_live_creds():
        pytest.skip("DAYTONA_API_KEY + DSPY_LM_MODEL + DSPY_LM_API_KEY not configured")

    planner_lm = get_planner_lm_from_env()
    if planner_lm is None:
        pytest.skip("planner LM could not be resolved from environment")

    cfg = AppConfig()
    prepared = PreparedChatRuntime(
        cfg=cfg,
        planner_lm=planner_lm,
        delegate_lm=None,
        repository=None,
        persistence=None,
        persistence_required=False,
        identity_rows=None,
    )
    ctx = ChatExecutionContext(
        prepared=prepared,
        identity=NormalizedIdentity(tenant_claim="live", user_claim="rlm"),
        session_id=None,
        canonical_workspace_id="live",
        canonical_user_id="rlm",
        owner_tenant_claim="live",
        owner_user_claim="rlm",
        cancel_flag={"cancelled": False},
        controls=TurnControls(execution_backend=ExecutionBackend.direct_rlm),
    )

    async with DaytonaInterpreter(
        timeout=cfg.timeout,
        volume_name=cfg.volume_name,
        max_llm_calls=cfg.rlm_max_llm_calls,
        rlm_max_iterations=min(6, int(cfg.rlm_max_iterations)),
    ) as interpreter:
        agent_runtime = _LiveAgentRuntime(interpreter)
        events = [
            event
            async for event in stream_turn(
                ctx=ctx,
                agent_runtime=agent_runtime,
                message="What is 2+2?",
            )
        ]

    kinds = [event.kind for event in events]
    assert kinds[-1] == RuntimeEventKind.DONE
    assert RuntimeEventKind.TEXT in kinds
    assert kinds.index(RuntimeEventKind.TEXT) < kinds.index(RuntimeEventKind.DONE)
