"""impl-18 cancel/timeout/budget + impl-19 public redaction."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.chat.turn_coordinator import ephemeral_lease
from fleet_rlm.config import Settings
from fleet_rlm.rlm.budgets import RLMBudget
from fleet_rlm.rlm.cancel import (
    RunCancelRegistry,
    get_run_cancel_registry,
    set_run_cancel_registry,
)
from fleet_rlm.rlm.context import RLMTurnContext
from fleet_rlm.rlm.errors import TurnBudgetExhausted, TurnCancelled, TurnTimeout
from fleet_rlm.rlm.events import RuntimeEventKind
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.rlm.sanitize import sanitize_public_error


@pytest.fixture(autouse=True)
def _fresh_cancel_registry() -> Any:
    reg = RunCancelRegistry()
    set_run_cancel_registry(reg)
    yield reg
    set_run_cancel_registry(RunCancelRegistry())


def test_sanitize_redacts_secrets_dsns_paths_stacks() -> None:
    assert "[redacted]" in sanitize_public_error("api_key=sk-abc123secret")
    assert "[redacted-dsn]" in sanitize_public_error("failed postgres://user:pass@host:5432/db")
    assert "[path]" in sanitize_public_error("open /Users/zocho/secret.txt")
    assert sanitize_public_error('Traceback (most recent call last):\n  File "x.py"') == "Turn failed"
    assert sanitize_public_error(TurnCancelled()) == "Turn cancelled"
    assert sanitize_public_error(TurnTimeout()) == "Turn timed out"
    assert sanitize_public_error(TurnBudgetExhausted()) == "Turn budget exhausted"


def test_cancel_registry_idempotent() -> None:
    reg = get_run_cancel_registry()
    rid = uuid4()
    assert reg.request_cancel(rid) is True
    assert reg.request_cancel(rid) is False
    assert reg.is_cancelled(rid) is True
    reg.clear(rid)
    assert reg.is_cancelled(rid) is False


def test_api_cancel_requires_identity_and_ownership() -> None:
    app = create_app(settings=Settings(auth_mode="dev"))
    client = TestClient(app)
    user, ws = uuid4(), uuid4()
    headers = {
        "X-Fleet-User-Id": str(user),
        "X-Fleet-Workspace-Id": str(ws),
    }
    run_id = uuid4()
    # Unbound run → not found
    assert client.post(f"/api/runs/{run_id}/cancel", headers=headers).status_code == 404

    get_run_cancel_registry().bind(run_id, user_id=user, workspace_id=ws, session_id=uuid4())
    r1 = client.post(f"/api/runs/{run_id}/cancel", headers=headers)
    assert r1.status_code == 200
    body = r1.json()
    assert body["cancelled"] is True
    assert body["already_cancelled"] is False

    r2 = client.post(f"/api/runs/{run_id}/cancel", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["already_cancelled"] is True

    # neon mode without bearer
    app_neon = create_app(
        settings=Settings(
            auth_mode="neon",
            neon_auth_url="https://example.test/neondb/auth",
        )
    )
    r3 = TestClient(app_neon).post(f"/api/runs/{run_id}/cancel")
    assert r3.status_code == 401
    assert r3.json()["detail"] == "authentication required"


@pytest.mark.asyncio
async def test_runner_honors_cancel_before_execute() -> None:
    run_id = uuid4()
    get_run_cancel_registry().request_cancel(run_id)

    class Factory:
        def create(self, **kwargs: Any) -> Any:
            raise AssertionError("factory should not run when cancelled")

    context = RLMTurnContext(
        run_id=run_id,
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        request="x",
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        budget=RLMBudget(max_wall_seconds=30),
        lease=ephemeral_lease(MagicMock()),
    )
    stream = RLMRunner(factory=Factory()).stream(context)
    events = [e async for e in stream]
    assert RuntimeEventKind.ERROR not in {e.kind for e in events}
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "cancelled"
    assert stream.outcome.public_error_message


@pytest.mark.asyncio
async def test_runner_timeout_maps_to_stable_status() -> None:
    class SlowFactory:
        def create(self, **kwargs: Any) -> Any:
            def slow_rlm(*, request: str, **_kwargs: Any) -> Any:
                import time

                time.sleep(2)
                return MagicMock(answer="late")

            return slow_rlm

    context = RLMTurnContext(
        run_id=uuid4(),
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        request="x",
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        budget=RLMBudget(max_wall_seconds=1),
        lease=ephemeral_lease(MagicMock()),
    )
    stream = RLMRunner(factory=SlowFactory()).stream(context)
    _ = [e async for e in stream]
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "timeout"


@pytest.mark.asyncio
async def test_runner_budget_error_maps_to_budget_exhausted() -> None:
    class BudgetFactory:
        def create(self, **kwargs: Any) -> Any:
            def boom(*, request: str, **_kwargs: Any) -> Any:
                raise TurnBudgetExhausted()

            return boom

    context = RLMTurnContext(
        run_id=uuid4(),
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        request="x",
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        budget=RLMBudget(max_wall_seconds=30),
        lease=ephemeral_lease(MagicMock()),
    )
    stream = RLMRunner(factory=BudgetFactory()).stream(context)
    _ = [e async for e in stream]
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "budget_exhausted"
    assert stream.outcome.public_error_message == "Turn budget exhausted"


@pytest.mark.asyncio
async def test_failed_turn_does_not_advance_checkpoint() -> None:
    """finish_failed_run path: ERROR terminal does not call commit_completed_turn."""
    from datetime import UTC, datetime

    from fleet_rlm.chat.commands import ChatTurnCommand
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.sessions.checkpoints import TurnClaim
    from fleet_rlm.sessions.models import SessionRecord, SessionSnapshot

    calls: dict[str, int] = {"append": 0, "fail": 0}
    owner = uuid4()
    ws = uuid4()
    sid = uuid4()

    class Store:
        async def load(self, session_id: Any) -> SessionSnapshot:
            return SessionSnapshot(
                session=SessionRecord(
                    id=session_id,
                    user_id=owner,
                    workspace_id=ws,
                    status="active",
                    title="t",
                    checkpoint_version=3,
                    created_at=datetime.now(UTC),
                ),
                turns=(),
            )

        async def claim_turn(self, session_id: Any, **kwargs: Any) -> TurnClaim:
            return TurnClaim(
                run_id=kwargs.get("run_id") or uuid4(),
                base_checkpoint_version=3,
                replay=False,
            )

        async def commit_completed_turn(self, *a: Any, **k: Any) -> None:
            calls["append"] += 1

        async def finish_failed_run(self, *a: Any, **k: Any) -> None:
            calls["fail"] += 1

    class FailRunner:
        def stream(self, context: Any) -> Any:
            from fleet_rlm.rlm.events import EventRecorder
            from fleet_rlm.rlm.outcome import TurnExecutionOutcome
            from fleet_rlm.rlm.runner import TurnEventStream

            async def _agen() -> Any:
                rec = EventRecorder(run_id=context.run_id, session_id=context.session_id)
                yield rec.emit(RuntimeEventKind.RUN_STARTED, {})

            return TurnEventStream(
                _agen(),
                outcome=TurnExecutionOutcome(
                    terminal_status="cancelled",
                    public_error_message="Turn cancelled",
                ),
            )

    def builder(cmd: ChatTurnCommand) -> RLMTurnContext:
        return RLMTurnContext(
            run_id=uuid4(),
            session_id=cmd.session_id,
            user_id=cmd.user_id,
            workspace_id=cmd.workspace_id,
            request=cmd.message,
            models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
            budget=RLMBudget(),
            lease=ephemeral_lease(MagicMock()),
        )

    coord = TurnCoordinator(
        runner=FailRunner(),  # type: ignore[arg-type]
        context_builder=builder,
        session_repository=Store(),  # type: ignore[arg-type]
    )
    events = [
        e async for e in coord.stream(ChatTurnCommand(user_id=owner, workspace_id=ws, session_id=sid, message="hi"))
    ]
    assert events[-1].kind == RuntimeEventKind.ERROR
    assert calls["append"] == 0
    assert calls["fail"] == 1
