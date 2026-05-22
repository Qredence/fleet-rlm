"""Tests for Daytona sandbox runtime-service wrappers."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.api.runtime_services.sandboxes import (
    _get_sandbox,
    _list_sandboxes,
    _raise_if_sandbox_inaccessible,
    _sandbox_detail_response,
)


@pytest.mark.asyncio
async def test_get_sandbox_runs_sync_client_call_off_thread() -> None:
    class _Client:
        def __init__(self) -> None:
            self.thread_id: int | None = None

        def get(self, sandbox_id: str) -> dict[str, str]:
            self.thread_id = threading.get_ident()
            return {"id": sandbox_id}

    client = _Client()
    caller_thread_id = threading.get_ident()

    result = await _get_sandbox(client, "sbx-123")

    assert result == {"id": "sbx-123"}
    assert client.thread_id is not None
    assert client.thread_id != caller_thread_id


@pytest.mark.asyncio
async def test_list_sandboxes_keeps_typeerror_fallback_off_thread() -> None:
    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[int, dict[str, Any]]] = []

        def list(self, **kwargs: Any) -> list[str]:
            self.calls.append((threading.get_ident(), dict(kwargs)))
            if "labels" in kwargs:
                raise TypeError("labels unsupported")
            return ["sandbox-a"]

    client = _Client()
    caller_thread_id = threading.get_ident()

    result = await _list_sandboxes(
        client,
        page=2,
        limit=25,
        labels_filter={"fleet_owner": "user-1"},
    )

    assert result == ["sandbox-a"]
    assert len(client.calls) == 2
    assert client.calls[0][1] == {
        "labels": {"fleet_owner": "user-1"},
        "page": 2,
        "limit": 25,
    }
    assert client.calls[1][1] == {"page": 2, "limit": 25}
    assert all(thread_id != caller_thread_id for thread_id, _ in client.calls)


def test_sandbox_detail_redacts_environment_secrets() -> None:
    sandbox = SimpleNamespace(
        id="sbx-1",
        name="owned",
        state="started",
        labels={"fleet_owner": "owner"},
        env={
            "DAYTONA_API_KEY": "daytona-secret-value",
            "DATABASE_URL": "postgres://user:pass@example/db",
            "SAFE_FLAG": "enabled",
            "HEADER": "Authorization: Bearer token-value",
        },
        volumes=[],
    )

    response = _sandbox_detail_response(sandbox)

    assert response.env_vars["DAYTONA_API_KEY"] == "<redacted>"
    assert response.env_vars["SAFE_FLAG"] == "enabled"
    assert "***REDACTED***" in response.env_vars["HEADER"]
    assert "daytona-secret-value" not in response.model_dump_json()
    assert "token-value" not in response.model_dump_json()


def test_sandbox_access_rejects_unlabeled_sandboxes_without_legacy_fallback() -> None:
    sandbox = SimpleNamespace(labels={})

    with pytest.raises(Exception) as exc_info:
        _raise_if_sandbox_inaccessible(
            sandbox,
            owner_labels={"fleet_owner": "owner"},
            allow_unlabeled_legacy=False,
        )

    assert getattr(exc_info.value, "status_code", None) == 404
