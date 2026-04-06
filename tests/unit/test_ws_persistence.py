from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from fleet_rlm.api.routers.ws import persistence as ws_persistence
from fleet_rlm.api.runtime_services import chat_persistence as persistence_service
from tests.ui.fixtures_ui import FakeChatAgent


def test_ensure_manifest_shape_initializes_expected_collections() -> None:
    manifest = ws_persistence.ensure_manifest_shape({})

    assert manifest == {
        "logs": [],
        "memory": [],
        "generated_docs": [],
        "artifacts": [],
        "metadata": {},
    }


def test_update_manifest_from_exported_state_increments_revision_and_metadata(
    monkeypatch,
) -> None:
    monkeypatch.setattr(persistence_service, "now_iso", lambda: "2026-03-21T00:00:00Z")
    manifest: dict[str, Any] = {"rev": "2", "artifacts": [{"id": "a1"}]}
    exported_state = {
        "history": [{"user_request": "hello", "assistant_response": "hi"}],
        "documents": {"active": "content"},
    }

    previous_rev, next_rev = ws_persistence.update_manifest_from_exported_state(
        manifest=manifest,
        exported_state=exported_state,
        latest_user_message="Please audit this.",
    )

    assert (previous_rev, next_rev) == (2, 3)
    assert manifest["rev"] == 3
    assert manifest["generated_docs"] == ["active"]
    assert manifest["state"] == exported_state
    assert manifest["logs"][0]["user_message"] == "Please audit this."
    assert manifest["memory"][0]["content"] == "Please audit this."
    assert manifest["metadata"] == {
        "updated_at": "2026-03-21T00:00:00Z",
        "history_turns": 1,
        "document_count": 1,
        "artifact_count": 1,
    }


def test_persist_session_state_updates_cache_and_saves_manifest(monkeypatch) -> None:
    agent = FakeChatAgent()
    agent._session_state = {
        "history": [{"user_request": "u", "assistant_response": "a"}],
        "documents": {"active": "hello"},
    }
    state = SimpleNamespace(sessions={})
    session_record = {
        "key": "tenant:user:session",
        "session_id": "session",
        "manifest": {"rev": 0},
        "session": {},
    }
    saved: dict[str, Any] = {}
    memory_calls: list[dict[str, Any]] = []

    async def _fake_load_manifest(_agent, _path: str) -> dict[str, Any]:
        return {"rev": 0}

    async def _fake_save_manifest(_agent, path: str, manifest: dict[str, Any]) -> str:
        saved["path"] = path
        saved["manifest"] = dict(manifest)
        return path

    async def _fake_persist_memory_item_if_needed(**kwargs: Any) -> None:
        memory_calls.append(kwargs)

    monkeypatch.setattr(
        persistence_service,
        "load_manifest_from_volume",
        _fake_load_manifest,
    )
    monkeypatch.setattr(
        persistence_service,
        "save_manifest_to_volume",
        _fake_save_manifest,
    )
    monkeypatch.setattr(
        persistence_service,
        "persist_memory_item_if_needed",
        _fake_persist_memory_item_if_needed,
    )

    asyncio.run(
        ws_persistence.persist_session_state(
            state=state,
            agent=agent,
            session_record=session_record,
            active_manifest_path="workspaces/test/session.json",
            active_run_db_id=None,
            interpreter=object(),
            repository=None,
            identity_rows=None,
            persistence_required=False,
            include_volume_save=True,
            latest_user_message="hello",
        )
    )

    assert state.sessions["tenant:user:session"] is session_record
    assert session_record["session"]["state"] == agent._session_state
    assert session_record["session"]["session_id"] == "session"
    assert session_record["manifest"]["rev"] == 1
    assert saved["path"] == "workspaces/test/session.json"
    assert saved["manifest"]["state"] == agent._session_state
    assert memory_calls == [
        {
            "repository": None,
            "identity_rows": None,
            "active_run_db_id": None,
            "latest_user_message": "hello",
            "persistence_required": False,
        }
    ]
