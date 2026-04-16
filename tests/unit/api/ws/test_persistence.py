from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from fleet_rlm.api.events import ExecutionStep
from fleet_rlm.api.runtime_services import chat_persistence as ws_persistence
from fleet_rlm.api.runtime_services import chat_persistence as persistence_service
from fleet_rlm.integrations.database import RunStatus
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


def test_complete_run_drains_batched_steps_before_shutdown() -> None:
    class _RecordingRepository:
        def __init__(self) -> None:
            self.step_requests: list[Any] = []
            self.status_updates: list[dict[str, Any]] = []

        async def append_step(self, request: Any) -> Any:
            self.step_requests.append(request)
            return SimpleNamespace(id=len(self.step_requests))

        async def update_run_status(
            self,
            *,
            tenant_id: str,
            run_id: int,
            status: RunStatus,
            error_json: dict[str, Any] | None,
        ) -> None:
            self.status_updates.append(
                {
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "status": status,
                    "error_json": error_json,
                }
            )

    class _RecordingEmitter:
        def __init__(self) -> None:
            self.events: list[Any] = []

        async def emit(self, event: Any) -> None:
            self.events.append(event)

    async def scenario() -> None:
        repository = _RecordingRepository()
        emitter = _RecordingEmitter()
        lifecycle = ws_persistence.ExecutionLifecycleManager(
            run_id="run-1",
            workspace_id="workspace-1",
            user_id="user-1",
            session_id="session-1",
            execution_emitter=emitter,
            step_builder=SimpleNamespace(),
            repository=repository,
            identity_rows=SimpleNamespace(tenant_id="tenant-1"),
            active_run_db_id=7,
            strict_persistence=False,
            session_record={},
        )
        lifecycle._persist_queue = asyncio.Queue(maxsize=512)
        await lifecycle._persist_queue.put(
            ExecutionStep(
                id="step-1",
                type="tool",
                label="step 1",
                timestamp=1.0,
            )
        )
        await lifecycle._persist_queue.put(
            ExecutionStep(
                id="step-2",
                type="tool",
                label="step 2",
                timestamp=2.0,
            )
        )
        await lifecycle._persist_queue.put(None)
        lifecycle._persist_worker_task = asyncio.create_task(
            lifecycle._persist_worker()
        )

        await asyncio.wait_for(lifecycle.complete_run(RunStatus.COMPLETED), timeout=1.0)

        assert [request.step_index for request in repository.step_requests] == [1, 2]
        assert [request.run_id for request in repository.step_requests] == [7, 7]
        assert lifecycle._persist_worker_task is None
        assert lifecycle._persist_queue is None
        assert repository.status_updates == [
            {
                "tenant_id": "tenant-1",
                "run_id": 7,
                "status": RunStatus.COMPLETED,
                "error_json": None,
            }
        ]
        assert emitter.events[-1].type == "execution_completed"

    asyncio.run(scenario())
