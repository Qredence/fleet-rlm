"""D1/D2: TurnContextBuilder + offline construction without unittest.mock in production."""

from __future__ import annotations

import inspect
from pathlib import Path
from uuid import uuid4

from fleet_rlm.chat import turn_coordinator as turn_coordinator_mod
from fleet_rlm.chat.commands import ChatTurnCommand
from fleet_rlm.chat.context_builder import (
    OfflineContextBuilder,
    OfflineInterpreter,
    OfflineLM,
    ephemeral_lease,
    rebind_turn_context,
)
from fleet_rlm.rlm.budgets import RLMBudget
from fleet_rlm.rlm.context import RLMTurnContext


def test_turn_coordinator_module_does_not_import_unittest_mock() -> None:
    source = Path(turn_coordinator_mod.__file__).read_text(encoding="utf-8")
    assert "unittest.mock" not in source
    assert "MagicMock" not in source


def test_offline_context_builder_builds_complete_context() -> None:
    cmd = ChatTurnCommand(
        user_id=uuid4(),
        workspace_id=uuid4(),
        session_id=uuid4(),
        message="hello",
    )
    ctx = OfflineContextBuilder().build(cmd)
    assert ctx.request == "hello"
    assert ctx.session_id == cmd.session_id
    assert isinstance(ctx.models.root_lm, OfflineLM)
    assert isinstance(ctx.lease.interpreter, OfflineInterpreter)
    ctx.lease.release()  # idempotent


def test_rebind_turn_context_preserves_hosts_and_applies_claim() -> None:
    cmd = ChatTurnCommand(
        user_id=uuid4(),
        workspace_id=uuid4(),
        session_id=uuid4(),
        message="x",
    )
    base = OfflineContextBuilder().build(cmd)
    host = object()
    base_with_host = RLMTurnContext(
        run_id=base.run_id,
        session_id=base.session_id,
        user_id=base.user_id,
        workspace_id=base.workspace_id,
        request=base.request,
        models=base.models,
        budget=base.budget,
        lease=base.lease,
        skill_tool_host=host,
        file_tool_host=host,
    )
    claim_run = uuid4()
    rebound = rebind_turn_context(
        base_with_host,
        run_id=claim_run,
        history=[{"role": "user", "content": "prior"}],
    )
    assert rebound.run_id == claim_run
    assert rebound.history == [{"role": "user", "content": "prior"}]
    assert rebound.skill_tool_host is host
    assert rebound.file_tool_host is host
    assert rebound.lease is base_with_host.lease
    assert rebound.models is base_with_host.models


def test_rebind_omitted_history_keeps_existing() -> None:
    cmd = ChatTurnCommand(
        user_id=uuid4(),
        workspace_id=uuid4(),
        session_id=uuid4(),
        message="x",
    )
    base = OfflineContextBuilder(budget=RLMBudget(max_iterations=3, max_llm_calls=3, max_output_chars=100)).build(cmd)
    with_history = rebind_turn_context(base, history=["a"])
    kept = rebind_turn_context(with_history, run_id=uuid4())
    assert kept.history == ["a"]


def test_ephemeral_lease_release_calls_shutdown_once() -> None:
    calls: list[str] = []

    class Interp:
        def shutdown(self) -> None:
            calls.append("shutdown")

    lease = ephemeral_lease(Interp())
    lease.release()
    lease.release()
    assert calls == ["shutdown"]


def test_offline_builder_satisfies_protocol_shape() -> None:
    builder = OfflineContextBuilder()
    assert callable(builder.build)
    sig = inspect.signature(builder.build)
    assert "command" in sig.parameters
