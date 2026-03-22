from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
import uuid

import pytest

from fleet_rlm.api.routers.ws.failures import PersistenceRequiredError
from fleet_rlm.api.routers.ws.turn_lifecycle import initialize_turn_lifecycle


class _RepositoryStub:
    def __init__(self, run_id: uuid.UUID | None = None) -> None:
        self.run_id = run_id or uuid.uuid4()
        self.calls: list[Any] = []

    async def create_run(self, request: Any) -> SimpleNamespace:
        self.calls.append(request)
        return SimpleNamespace(id=self.run_id)


class _FailingRepositoryStub:
    async def create_run(self, request: Any) -> SimpleNamespace:
        _ = request
        raise RuntimeError("db unavailable")


def test_initialize_turn_lifecycle_records_run_id_and_session_record() -> None:
    async def scenario() -> None:
        repository = _RepositoryStub()
        session_record: dict[str, Any] = {}

        (
            lifecycle,
            step_builder,
            run_id,
            active_run_db_id,
        ) = await initialize_turn_lifecycle(
            planner_lm=SimpleNamespace(model="openai/gpt-4o"),
            cfg=SimpleNamespace(sandbox_provider="daytona"),
            repository=repository,  # type: ignore[arg-type]
            identity_rows=SimpleNamespace(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
            ),
            persistence_required=False,
            execution_emitter=object(),
            workspace_id="workspace",
            user_id="user",
            sess_id="session",
            turn_index=3,
            session_record=session_record,
            sandbox_provider="daytona",
        )

        assert run_id == "workspace:user:session:3"
        assert step_builder.run_id == run_id
        assert lifecycle.run_id == run_id
        assert active_run_db_id == repository.run_id
        assert session_record["last_run_db_id"] == str(repository.run_id)
        assert repository.calls

    asyncio.run(scenario())


def test_initialize_turn_lifecycle_raises_when_run_persist_required() -> None:
    async def scenario() -> None:
        with pytest.raises(
            PersistenceRequiredError, match="Failed to persist run start"
        ):
            await initialize_turn_lifecycle(
                planner_lm=SimpleNamespace(model="openai/gpt-4o"),
                cfg=SimpleNamespace(sandbox_provider="daytona"),
                repository=_FailingRepositoryStub(),  # type: ignore[arg-type]
                identity_rows=SimpleNamespace(
                    tenant_id=uuid.uuid4(),
                    user_id=uuid.uuid4(),
                ),
                persistence_required=True,
                execution_emitter=object(),
                workspace_id="workspace",
                user_id="user",
                sess_id="session",
                turn_index=1,
                session_record={},
                sandbox_provider="daytona",
            )

    asyncio.run(scenario())
