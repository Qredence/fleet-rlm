"""Tests for GET /api/v1/runs/{id}/steps endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from fleet_rlm.integrations.database import RunStatus, RunStepType
from fleet_rlm.integrations.database.types import IdentityUpsertResult


class _RunStepsRepository:
    """Repository stub with run steps for tests."""

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.workspace_id = uuid.uuid4()
        self.foreign_workspace_id = uuid.uuid4()
        self.calls: list[tuple[str, uuid.UUID, uuid.UUID | None, uuid.UUID | None]] = []
        now = datetime.now(timezone.utc)
        self.run = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            status=RunStatus.COMPLETED,
            created_at=now,
            updated_at=now,
        )
        self.foreign_run = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            workspace_id=self.foreign_workspace_id,
            status=RunStatus.COMPLETED,
            created_at=now,
            updated_at=now,
        )
        self.steps = [
            SimpleNamespace(
                id=uuid.uuid4(),
                run_id=self.run.id,
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                step_index=0,
                step_type=RunStepType.LLM_CALL,
                tool_name=None,
                tokens_in=10,
                tokens_out=5,
                latency_ms=100,
                created_at=now,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                run_id=self.run.id,
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                step_index=1,
                step_type=RunStepType.TOOL_CALL,
                tool_name="search",
                tokens_in=20,
                tokens_out=15,
                latency_ms=200,
                created_at=now,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                run_id=self.run.id,
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                step_index=2,
                step_type=RunStepType.OUTPUT,
                tool_name=None,
                tokens_in=None,
                tokens_out=None,
                latency_ms=50,
                created_at=now,
            ),
        ]

    async def upsert_identity(self, **kwargs) -> IdentityUpsertResult:
        _ = kwargs
        return IdentityUpsertResult(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            workspace_id=self.workspace_id,
        )

    async def get_run(
        self,
        *,
        tenant_id,
        run_id,
        workspace_id=None,
        created_by_user_id=None,
    ):
        self.calls.append(("get_run", run_id, workspace_id, created_by_user_id))
        if (
            tenant_id == self.tenant_id
            and run_id == self.run.id
            and workspace_id == self.workspace_id
        ):
            return self.run
        return None

    async def get_run_steps(
        self,
        *,
        tenant_id,
        run_id,
        workspace_id=None,
        created_by_user_id=None,
        limit=None,
        offset=0,
    ):
        self.calls.append(
            ("get_run_steps", run_id, workspace_id, created_by_user_id, limit, offset)
        )
        if (
            tenant_id == self.tenant_id
            and run_id == self.run.id
            and workspace_id == self.workspace_id
        ):
            return self.steps[offset : offset + limit if limit is not None else None]
        return []

    async def count_run_steps(
        self,
        *,
        tenant_id,
        run_id,
        workspace_id=None,
        created_by_user_id=None,
    ):
        self.calls.append(("count_run_steps", run_id, workspace_id, created_by_user_id))
        if (
            tenant_id == self.tenant_id
            and run_id == self.run.id
            and workspace_id == self.workspace_id
        ):
            return len(self.steps)
        return 0

    async def get_run_steps_paginated(
        self,
        *,
        tenant_id,
        run_id,
        workspace_id=None,
        created_by_user_id=None,
        limit=50,
        offset=0,
    ):
        self.calls.append(
            (
                "get_run_steps_paginated",
                run_id,
                workspace_id,
                created_by_user_id,
                limit,
                offset,
            )
        )
        if (
            tenant_id == self.tenant_id
            and run_id == self.run.id
            and workspace_id == self.workspace_id
        ):
            return self.steps[offset : offset + limit], len(self.steps)
        return [], 0

    async def get_chat_session(self, **kwargs):
        raise NotImplementedError

    async def list_chat_turns(self, **kwargs):
        raise NotImplementedError

    async def archive_chat_session(self, **kwargs):
        raise NotImplementedError

    async def create_dataset(self, request, *, examples):
        raise NotImplementedError


@pytest.fixture
def run_steps_repo(default_client):
    repo = _RunStepsRepository()
    default_client.app.state.server_state.repository = repo
    return repo


def test_get_run_steps_returns_expected_shape(
    default_client,
    auth_headers,
    run_steps_repo,
):
    response = default_client.get(
        f"/api/v1/runs/{run_steps_repo.run.id}/steps",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["offset"] == 0
    assert payload["limit"] == 50
    assert payload["has_more"] is False
    assert len(payload["items"]) == 3

    first = payload["items"][0]
    assert first["step_index"] == 0
    assert first["step_type"] == RunStepType.LLM_CALL.value
    assert first["tool_name"] is None
    assert first["tokens_in"] == 10
    assert first["tokens_out"] == 5
    assert first["latency_ms"] == 100

    second = payload["items"][1]
    assert second["step_index"] == 1
    assert second["step_type"] == RunStepType.TOOL_CALL.value
    assert second["tool_name"] == "search"
    assert second["tokens_in"] == 20
    assert second["tokens_out"] == 15
    assert second["latency_ms"] == 200

    third = payload["items"][2]
    assert third["step_index"] == 2
    assert third["step_type"] == RunStepType.OUTPUT.value
    assert third["tool_name"] is None
    assert third["tokens_in"] is None
    assert third["tokens_out"] is None
    assert third["latency_ms"] == 50
    assert run_steps_repo.calls == [
        (
            "get_run",
            run_steps_repo.run.id,
            run_steps_repo.workspace_id,
            run_steps_repo.user_id,
        ),
        (
            "get_run_steps_paginated",
            run_steps_repo.run.id,
            run_steps_repo.workspace_id,
            run_steps_repo.user_id,
            50,
            0,
        ),
    ]


def test_get_run_steps_pagination(default_client, auth_headers, run_steps_repo):
    response = default_client.get(
        f"/api/v1/runs/{run_steps_repo.run.id}/steps?limit=1&offset=1",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["offset"] == 1
    assert payload["limit"] == 1
    assert payload["has_more"] is True
    assert len(payload["items"]) == 1
    assert payload["items"][0]["step_index"] == 1


def test_get_run_steps_nonexistent_run_returns_404(
    default_client, auth_headers, run_steps_repo
):
    _ = run_steps_repo
    response = default_client.get(
        f"/api/v1/runs/{uuid.uuid4()}/steps",
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_get_run_steps_foreign_workspace_returns_404(
    default_client,
    auth_headers,
    run_steps_repo,
):
    response = default_client.get(
        f"/api/v1/runs/{run_steps_repo.foreign_run.id}/steps",
        headers=auth_headers,
    )

    assert response.status_code == 404


def test_get_run_steps_without_auth_returns_401(staging_client, run_steps_repo):
    staging_client.app.state.server_state.repository = run_steps_repo
    response = staging_client.get(
        f"/api/v1/runs/{run_steps_repo.run.id}/steps",
    )
    assert response.status_code == 401


def test_get_run_steps_invalid_run_id_returns_404(default_client, auth_headers):
    response = default_client.get(
        "/api/v1/runs/not-a-uuid/steps",
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_get_run_steps_invalid_limit_returns_422(
    default_client, auth_headers, run_steps_repo
):
    response = default_client.get(
        f"/api/v1/runs/{run_steps_repo.run.id}/steps?limit=0",
        headers=auth_headers,
    )
    assert response.status_code == 422
