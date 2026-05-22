from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import WebSocketDisconnect

from fleet_rlm.api.events import ExecutionStep
from fleet_rlm.api.runtime_services import chat_persistence as _chat_persistence
from fleet_rlm.api.runtime_services import chat_persistence as persistence_service
from fleet_rlm.api.runtime_services import chat_persistence as ws_persistence
from fleet_rlm.api.runtime_services.chat_persistence import (
    PersistenceRequiredError,
    cancel_task,
    enqueue_latest_nonblocking,
    initialize_turn_lifecycle,
    should_reload_docs_path,
)
from fleet_rlm.integrations.database import RunStatus
from tests.unit.fixtures_daytona import FakeDaytonaStorageSession
from tests.unit.fixtures_ui import FakeChatAgent


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------
class _RecordingInterpreter:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def aexecute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> object:
        self.calls.append(
            {
                "code": code,
                "variables": variables,
                "kwargs": kwargs,
            }
        )
        return self.result


# ---------------------------------------------------------------------------
# Turn lifecycle helpers
# ---------------------------------------------------------------------------
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


# ===========================================================================
# Manifest path / load / save
# ===========================================================================
def test_manifest_path_uses_default_session_id_when_missing() -> None:
    assert (
        _chat_persistence._manifest_path("workspace-123", "user-456", "")
        == "meta/workspaces/workspace-123/users/user-456/react-session-default-session.json"
    )


def test_load_manifest_from_volume_returns_empty_without_interpreter() -> None:
    agent = cast(Any, SimpleNamespace(interpreter=None))

    manifest = asyncio.run(_chat_persistence.load_manifest_from_volume(agent, "workspaces/test/session.json"))

    assert manifest == {}


def test_load_manifest_from_volume_returns_empty_on_invalid_json(
    monkeypatch,
) -> None:
    interpreter = _RecordingInterpreter(SimpleNamespace(output={"text": "{oops"}))
    agent = cast(Any, SimpleNamespace(interpreter=interpreter))

    monkeypatch.setattr(_chat_persistence, "_is_final_output", lambda result: True)

    manifest = asyncio.run(_chat_persistence.load_manifest_from_volume(agent, "workspaces/test/session.json"))

    assert manifest == {}


def test_load_manifest_from_volume_parses_json_payload(monkeypatch) -> None:
    interpreter = _RecordingInterpreter(SimpleNamespace(output={"text": '{"rev": 2}'}))
    agent = cast(Any, SimpleNamespace(interpreter=interpreter))

    monkeypatch.setattr(_chat_persistence, "_is_final_output", lambda result: True)

    manifest = asyncio.run(_chat_persistence.load_manifest_from_volume(agent, "workspaces/test/session.json"))

    assert manifest == {"rev": 2}
    assert interpreter.calls[0]["variables"] == {"path": "workspaces/test/session.json"}


def test_load_manifest_from_volume_uses_daytona_session(monkeypatch) -> None:
    session = FakeDaytonaStorageSession()
    session.file_contents["/home/daytona/memory/meta/workspaces/test/session.json"] = (
        '{"rev": 3, "state": {"ok": true}}'
    )
    agent = cast(
        Any,
        SimpleNamespace(interpreter=SimpleNamespace(volume_mount_path="/home/daytona/memory")),
    )

    async def _fake_get_daytona_session(_agent) -> FakeDaytonaStorageSession:
        return session

    monkeypatch.setattr(_chat_persistence, "_aget_daytona_session", _fake_get_daytona_session)

    manifest = asyncio.run(_chat_persistence.load_manifest_from_volume(agent, "meta/workspaces/test/session.json"))

    assert manifest == {"rev": 3, "state": {"ok": True}}
    assert session.read_calls == ["/home/daytona/memory/meta/workspaces/test/session.json"]


def test_load_manifest_from_volume_does_not_fall_back_to_legacy_path(monkeypatch) -> None:
    session = FakeDaytonaStorageSession()
    session.file_contents["/home/daytona/memory/workspaces/test/session.json"] = '{"rev": 5}'
    agent = cast(
        Any,
        SimpleNamespace(interpreter=SimpleNamespace(volume_mount_path="/home/daytona/memory")),
    )

    async def _fake_get_daytona_session(_agent) -> FakeDaytonaStorageSession:
        return session

    monkeypatch.setattr(_chat_persistence, "_aget_daytona_session", _fake_get_daytona_session)

    manifest = asyncio.run(_chat_persistence.load_manifest_from_volume(agent, "meta/workspaces/test/session.json"))

    assert manifest == {}
    assert session.read_calls == ["/home/daytona/memory/meta/workspaces/test/session.json"]


def test_load_manifest_from_volume_does_not_fall_back_to_legacy_user_memory_path(
    monkeypatch,
) -> None:
    session = FakeDaytonaStorageSession()
    session.file_contents[
        "/home/daytona/memory/workspaces/workspace-1/users/user-1/memory/react-session-session-a.json"
    ] = '{"rev": 7}'
    agent = cast(
        Any,
        SimpleNamespace(interpreter=SimpleNamespace(volume_mount_path="/home/daytona/memory")),
    )

    async def _fake_get_daytona_session(_agent) -> FakeDaytonaStorageSession:
        return session

    monkeypatch.setattr(_chat_persistence, "_aget_daytona_session", _fake_get_daytona_session)

    manifest = asyncio.run(
        _chat_persistence.load_manifest_from_volume(
            agent,
            "meta/workspaces/workspace-1/users/user-1/react-session-session-a.json",
        )
    )

    assert manifest == {}
    assert session.read_calls == [
        "/home/daytona/memory/meta/workspaces/workspace-1/users/user-1/" + "react-session-session-a.json"
    ]


def test_save_manifest_to_volume_returns_none_without_interpreter() -> None:
    agent = cast(Any, SimpleNamespace(interpreter=None))

    saved_path = asyncio.run(
        _chat_persistence.save_manifest_to_volume(
            agent,
            "workspaces/test/session.json",
            {"rev": 1},
        )
    )

    assert saved_path is None


def test_save_manifest_to_volume_returns_saved_path(monkeypatch) -> None:
    interpreter = _RecordingInterpreter(SimpleNamespace(output={"saved_path": "workspaces/test/session.json"}))
    agent = cast(Any, SimpleNamespace(interpreter=interpreter))

    monkeypatch.setattr(_chat_persistence, "_is_final_output", lambda result: True)

    saved_path = asyncio.run(
        _chat_persistence.save_manifest_to_volume(
            agent,
            "workspaces/test/session.json",
            {"rev": 1},
        )
    )

    assert saved_path == "workspaces/test/session.json"
    assert interpreter.calls[0]["variables"] == {
        "path": "workspaces/test/session.json",
        "payload": '{"rev": 1}',
    }


def test_save_manifest_to_volume_uses_daytona_session(monkeypatch) -> None:
    session = FakeDaytonaStorageSession()
    agent = cast(
        Any,
        SimpleNamespace(interpreter=SimpleNamespace(volume_mount_path="/home/daytona/memory")),
    )

    async def _fake_get_daytona_session(_agent) -> FakeDaytonaStorageSession:
        return session

    monkeypatch.setattr(_chat_persistence, "_aget_daytona_session", _fake_get_daytona_session)

    saved_path = asyncio.run(
        _chat_persistence.save_manifest_to_volume(
            agent,
            "meta/workspaces/test/session.json",
            {"rev": 4},
        )
    )

    assert saved_path == "/home/daytona/memory/meta/workspaces/test/session.json"
    assert session.write_calls == [
        (
            "/home/daytona/memory/meta/workspaces/test/session.json",
            '{"rev": 4}',
        )
    ]


# ===========================================================================
# Turn lifecycle
# ===========================================================================
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
        with pytest.raises(PersistenceRequiredError, match="Failed to persist run start"):
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


# ===========================================================================
# Session persistence / lifecycle manager
# ===========================================================================
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
            session_cache=state,
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
        lifecycle._persist_worker_task = asyncio.create_task(lifecycle._persist_worker())

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


def test_lifecycle_without_repository_does_not_start_persist_worker() -> None:
    class _RecordingEmitter:
        def __init__(self) -> None:
            self.events: list[Any] = []

        async def emit(self, event: Any) -> None:
            self.events.append(event)

    async def scenario() -> None:
        emitter = _RecordingEmitter()
        lifecycle = ws_persistence.ExecutionLifecycleManager(
            run_id="run-1",
            workspace_id="workspace-1",
            user_id="user-1",
            session_id="session-1",
            execution_emitter=emitter,
            step_builder=SimpleNamespace(),
            repository=None,
            identity_rows=None,
            active_run_db_id=None,
            strict_persistence=False,
            session_record={},
        )

        await lifecycle.emit_started()
        await lifecycle.persist_step(
            ExecutionStep(
                id="step-1",
                type="tool",
                label="step 1",
                timestamp=1.0,
            )
        )
        await lifecycle.complete_run(RunStatus.COMPLETED)

        assert lifecycle._persist_worker_task is None
        assert lifecycle._persist_queue is None
        assert [event.type for event in emitter.events] == [
            "execution_started",
            "execution_completed",
        ]
        assert [event.sequence for event in emitter.events] == [1, 2]
        assert all(event.timestamp is not None for event in emitter.events)

    asyncio.run(scenario())


# ===========================================================================
# Task control
# ===========================================================================
def test_should_reload_docs_path_dedupes_same_path() -> None:
    assert should_reload_docs_path(None, None) is False
    assert should_reload_docs_path(None, "") is False
    assert should_reload_docs_path(None, "docs/a.txt") is True
    assert should_reload_docs_path("docs/a.txt", "docs/a.txt") is False
    assert should_reload_docs_path("docs/a.txt", "docs/b.txt") is True


def test_enqueue_latest_nonblocking_drops_oldest_when_full() -> None:
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=2)

    assert enqueue_latest_nonblocking(queue, 1) is True
    assert enqueue_latest_nonblocking(queue, 2) is True
    assert enqueue_latest_nonblocking(queue, 3) is True

    assert queue.get_nowait() == 2
    assert queue.get_nowait() == 3


def test_cancel_task_handles_none() -> None:
    asyncio.run(cancel_task(None))


def test_cancel_task_handles_already_completed() -> None:
    async def scenario() -> None:
        async def done() -> None:
            return None

        task = asyncio.create_task(done())
        _ = await task
        await cancel_task(cast(asyncio.Task[object], task))

    asyncio.run(scenario())


def test_cancel_task_swallows_websocket_disconnect() -> None:
    async def scenario() -> None:
        async def disconnect_on_cancel() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError as exc:
                raise WebSocketDisconnect(code=1001) from exc

        task = asyncio.create_task(disconnect_on_cancel())
        await asyncio.sleep(0)
        await cancel_task(cast(asyncio.Task[object], task))

    asyncio.run(scenario())


def test_cancel_task_reraises_unexpected_exceptions() -> None:
    async def scenario() -> None:
        async def fail_on_cancel() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError as exc:
                raise RuntimeError("boom") from exc

        task = asyncio.create_task(fail_on_cancel())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="boom"):
            await cancel_task(cast(asyncio.Task[object], task))

    asyncio.run(scenario())
