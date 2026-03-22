from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.api.routers.ws.artifacts import (
    append_session_artifact,
    is_artifact_tracking_command,
    persist_artifact_metadata,
    track_command_artifact_if_needed,
)
from fleet_rlm.api.routers.ws.failures import PersistenceRequiredError


class _FakeRepository:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def store_artifact(self, request: Any) -> None:
        self.requests.append(request)


class _FailingRepository:
    async def store_artifact(self, request: Any) -> None:
        _ = request
        raise RuntimeError("db unavailable")


def test_is_artifact_tracking_command_matches_supported_commands() -> None:
    assert is_artifact_tracking_command("save_buffer") is True
    assert is_artifact_tracking_command("load_volume") is True
    assert is_artifact_tracking_command("write_to_file") is True
    assert is_artifact_tracking_command("list_files") is False


def test_append_session_artifact_initializes_manifest_and_returns_uri() -> None:
    session_record: dict[str, Any] = {}

    artifact_uri = append_session_artifact(
        session_record=session_record,
        command="save_buffer",
        args={"path": "/tmp/output.txt"},
        result={"saved_path": "/tmp/output.txt"},
    )

    assert artifact_uri == "/tmp/output.txt"
    assert session_record["manifest"]["artifacts"][0]["command"] == "save_buffer"
    assert session_record["manifest"]["artifacts"][0]["path"] == "/tmp/output.txt"


def test_append_session_artifact_prefers_saved_path_over_args_path() -> None:
    session_record: dict[str, Any] = {"manifest": {"artifacts": []}}

    artifact_uri = append_session_artifact(
        session_record=session_record,
        command="write_to_file",
        args={"path": "/tmp/default.txt"},
        result={"saved_path": "/tmp/actual.txt"},
    )

    assert artifact_uri == "/tmp/actual.txt"


def test_persist_artifact_metadata_skips_without_run_id() -> None:
    repository = _FakeRepository()

    asyncio.run(
        persist_artifact_metadata(
            repository=repository,
            identity_rows=SimpleNamespace(tenant_id="tenant-123"),
            session_record={},
            command="save_buffer",
            args={"path": "/tmp/output.txt"},
            artifact_uri="/tmp/output.txt",
        )
    )

    assert repository.requests == []


def test_persist_artifact_metadata_stores_database_request() -> None:
    repository = _FakeRepository()
    run_id = uuid.uuid4()
    step_id = uuid.uuid4()

    asyncio.run(
        persist_artifact_metadata(
            repository=repository,
            identity_rows=SimpleNamespace(tenant_id="tenant-123"),
            session_record={
                "last_run_db_id": str(run_id),
                "last_step_db_id": str(step_id),
            },
            command="save_buffer",
            args={"path": "/tmp/output.txt"},
            artifact_uri="/tmp/output.txt",
        )
    )

    assert len(repository.requests) == 1
    request = repository.requests[0]
    assert request.tenant_id == "tenant-123"
    assert request.run_id == run_id
    assert request.step_id == step_id
    assert request.uri == "/tmp/output.txt"
    assert request.metadata_json == {
        "command": "save_buffer",
        "args": {"path": "/tmp/output.txt"},
    }


def test_track_command_artifact_if_needed_updates_manifest_and_persists() -> None:
    repository = _FakeRepository()
    run_id = uuid.uuid4()
    step_id = uuid.uuid4()
    session_record: dict[str, Any] = {
        "last_run_db_id": str(run_id),
        "last_step_db_id": str(step_id),
    }

    asyncio.run(
        track_command_artifact_if_needed(
            session_record=session_record,
            command="save_buffer",
            args={"path": "/tmp/output.txt"},
            result={"saved_path": "/tmp/output.txt"},
            repository=repository,  # type: ignore[arg-type]
            identity_rows=SimpleNamespace(tenant_id="tenant-123"),
            persistence_required=False,
        )
    )

    assert session_record["manifest"]["artifacts"][0]["path"] == "/tmp/output.txt"
    assert len(repository.requests) == 1


def test_track_command_artifact_if_needed_raises_when_persistence_required() -> None:
    with pytest.raises(
        PersistenceRequiredError, match="Failed to persist artifact metadata"
    ):
        asyncio.run(
            track_command_artifact_if_needed(
                session_record={"last_run_db_id": str(uuid.uuid4())},
                command="save_buffer",
                args={"path": "/tmp/output.txt"},
                result={"saved_path": "/tmp/output.txt"},
                repository=_FailingRepository(),  # type: ignore[arg-type]
                identity_rows=SimpleNamespace(tenant_id="tenant-123"),
                persistence_required=True,
            )
        )
