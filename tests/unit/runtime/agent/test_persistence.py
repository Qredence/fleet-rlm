"""Unit tests for session persistence helpers.

Covers VAL-PERSIST-001 through VAL-PERSIST-006 from the validation contract:

- VAL-PERSIST-001: DSPy history serialized to Daytona volume
- VAL-PERSIST-002: Session metadata persisted to database
- VAL-PERSIST-003: History restored on session resume
- VAL-PERSIST-004: History file has valid JSON schema (turns, session_id, timestamp)
- VAL-PERSIST-005: Session import/export round-trip
- VAL-PERSIST-006: ChatSessionState (AgentRuntime) uses dspy.History as backing store
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import dspy
import pytest

from fleet_rlm.runtime.agent.persistence import (
    HISTORY_SCHEMA_VERSION,
    REQUIRED_SCHEMA_KEYS,
    deserialize_history,
    export_session,
    history_volume_path,
    import_session,
    persist_history_to_volume,
    persist_session_metadata,
    restore_history_from_volume,
    serialize_history,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_session_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def sample_workspace_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def sample_user_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def sample_tenant_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def sample_history() -> dspy.History:
    return dspy.History(
        messages=[
            {"user_message": "Hello", "response": "Hi there!"},
            {"user_message": "How are you?", "response": "Doing well, thanks!"},
        ]
    )


@pytest.fixture()
def empty_history() -> dspy.History:
    return dspy.History(messages=[])


@pytest.fixture()
def mock_interpreter(sample_workspace_id: str) -> MagicMock:
    """Mock Daytona interpreter with awrite_file and aread_file support."""
    interp = MagicMock()
    interp.volume_mount_path = "/home/daytona/memory"
    interp.workspace_path = "/home/daytona/workspace"
    # Track written content keyed by path
    interp._written: dict[str, str] = {}

    async def _awrite(path: str, content: str) -> str:
        interp._written[path] = content
        return path

    async def _aread(path: str) -> str:
        return interp._written.get(path, "")

    interp.awrite_file = _awrite
    interp.aread_file = _aread
    return interp


@pytest.fixture()
def mock_repository() -> AsyncMock:
    """Mock FleetRepository with upsert_chat_session support."""
    repo = AsyncMock()
    mock_session = MagicMock()
    mock_session.id = uuid.uuid4()
    repo.upsert_chat_session.return_value = mock_session
    return repo


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def test_history_volume_path_structure() -> None:
    """history_volume_path returns the canonical nested path."""
    meta_root = "/home/daytona/memory/meta"
    path = history_volume_path(meta_root, "ws123", "user456", "sess789")
    assert path.startswith(meta_root)
    assert "workspaces/ws123" in path
    assert "users/user456" in path
    assert "react-session-sess789.json" in path
    assert path.endswith(".json")

    path2 = history_volume_path("/meta", "w", "u", "s")
    assert path2.endswith(".json")


# ---------------------------------------------------------------------------
# VAL-PERSIST-004: Valid JSON schema
# ---------------------------------------------------------------------------


def test_serialize_history_schema_and_fields(sample_history: dspy.History, sample_session_id: str) -> None:
    """VAL-PERSIST-004: Serialized payload has correct schema and fields."""
    payload = serialize_history(sample_history, sample_session_id)
    missing = REQUIRED_SCHEMA_KEYS - set(payload.keys())
    assert not missing, f"Missing schema keys: {missing}"
    assert payload["schema_version"] == HISTORY_SCHEMA_VERSION
    assert payload["session_id"] == sample_session_id
    assert isinstance(payload["timestamp"], (int, float))
    assert payload["timestamp"] > 0
    assert isinstance(payload["turns"], list)
    assert len(payload["turns"]) == 2
    for turn in payload["turns"]:
        assert "user_message" in turn
        assert "response" in turn
    dumped = json.dumps(payload)
    assert isinstance(dumped, str)
    loaded = json.loads(dumped)
    assert loaded["session_id"] == sample_session_id


def test_serialize_history_edge_cases(
    empty_history: dspy.History, sample_history: dspy.History, sample_session_id: str
) -> None:
    """VAL-PERSIST-004: Empty history and custom timestamp."""
    payload = serialize_history(empty_history, "sess-empty")
    assert payload["turns"] == []

    ts = 1234567890.0
    payload2 = serialize_history(sample_history, sample_session_id, timestamp=ts)
    assert payload2["timestamp"] == ts


# ---------------------------------------------------------------------------
# Deserialization
# ---------------------------------------------------------------------------


def test_deserialize_history_roundtrip(sample_history: dspy.History, sample_session_id: str) -> None:
    """deserialize_history returns dspy.History with preserved messages."""
    payload = serialize_history(sample_history, sample_session_id)
    restored = deserialize_history(payload)
    assert isinstance(restored, dspy.History)
    msgs = list(restored.messages)
    assert len(msgs) == 2
    assert msgs[0]["user_message"] == "Hello"
    assert msgs[0]["response"] == "Hi there!"


def test_deserialize_history_edge_cases() -> None:
    """deserialize_history handles empty turns and invalid entries."""
    payload = {"schema_version": "1", "session_id": "x", "timestamp": 0.0, "turns": []}
    restored = deserialize_history(payload)
    assert isinstance(restored, dspy.History)
    assert list(restored.messages) == []

    payload2 = {
        "schema_version": "1",
        "session_id": "x",
        "timestamp": 0.0,
        "turns": [None, "bad", {"user_message": "ok", "response": "yes"}],
    }
    restored2 = deserialize_history(payload2)
    assert len(list(restored2.messages)) == 1


# ---------------------------------------------------------------------------
# VAL-PERSIST-001: History serialized to Daytona volume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_history_to_volume_calls_awrite(
    mock_interpreter: MagicMock,
    sample_history: dspy.History,
    sample_workspace_id: str,
    sample_user_id: str,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-001: persist_history_to_volume writes to interpreter."""
    path = await persist_history_to_volume(
        mock_interpreter,
        sample_workspace_id,
        sample_user_id,
        sample_session_id,
        sample_history,
    )
    assert path in mock_interpreter._written
    assert mock_interpreter._written[path]  # non-empty content


@pytest.mark.asyncio
async def test_persist_history_to_volume_writes_valid_json(
    mock_interpreter: MagicMock,
    sample_history: dspy.History,
    sample_workspace_id: str,
    sample_user_id: str,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-001: Written content is valid JSON with required keys."""
    path = await persist_history_to_volume(
        mock_interpreter,
        sample_workspace_id,
        sample_user_id,
        sample_session_id,
        sample_history,
    )
    content = mock_interpreter._written[path]
    data = json.loads(content)
    missing = REQUIRED_SCHEMA_KEYS - set(data.keys())
    assert not missing, f"Written JSON missing keys: {missing}"


@pytest.mark.asyncio
async def test_persist_history_to_volume_path_contains_session_id(
    mock_interpreter: MagicMock,
    sample_history: dspy.History,
    sample_workspace_id: str,
    sample_user_id: str,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-001: Path written contains the session_id."""
    path = await persist_history_to_volume(
        mock_interpreter,
        sample_workspace_id,
        sample_user_id,
        sample_session_id,
        sample_history,
    )
    assert sample_session_id in path


@pytest.mark.asyncio
async def test_persist_history_to_volume_fallback_sync(
    sample_history: dspy.History,
    sample_workspace_id: str,
    sample_user_id: str,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-001: Falls back to sync write_file when awrite_file absent."""
    written: dict[str, str] = {}
    interp = MagicMock()
    interp.volume_mount_path = "/home/daytona/memory"

    def _write(path: str, content: str) -> str:
        written[path] = content
        return path

    # No awrite_file attribute — only sync
    del interp.awrite_file
    interp.write_file = _write

    await persist_history_to_volume(
        interp,
        sample_workspace_id,
        sample_user_id,
        sample_session_id,
        sample_history,
    )
    assert len(written) == 1


@pytest.mark.asyncio
async def test_persist_history_to_volume_raises_when_no_write_method(
    sample_history: dspy.History,
    sample_workspace_id: str,
    sample_user_id: str,
    sample_session_id: str,
) -> None:
    """VAL-BACKEND-PERSIST-001: Raises RuntimeError when no write method exists."""
    interp = MagicMock()
    interp.volume_mount_path = "/home/daytona/memory"
    del interp.awrite_file
    del interp.write_file

    with pytest.raises(RuntimeError, match="write"):
        await persist_history_to_volume(
            interp,
            sample_workspace_id,
            sample_user_id,
            sample_session_id,
            sample_history,
        )


# ---------------------------------------------------------------------------
# VAL-PERSIST-003: History restored on session resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_history_from_volume_returns_history(
    mock_interpreter: MagicMock,
    sample_history: dspy.History,
    sample_workspace_id: str,
    sample_user_id: str,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-003: restore_history_from_volume returns dspy.History."""
    await persist_history_to_volume(
        mock_interpreter,
        sample_workspace_id,
        sample_user_id,
        sample_session_id,
        sample_history,
    )
    restored = await restore_history_from_volume(
        mock_interpreter,
        sample_workspace_id,
        sample_user_id,
        sample_session_id,
    )
    assert isinstance(restored, dspy.History)


@pytest.mark.asyncio
async def test_restore_history_from_volume_preserves_turns(
    mock_interpreter: MagicMock,
    sample_history: dspy.History,
    sample_workspace_id: str,
    sample_user_id: str,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-003: Restored history has the same turns."""
    await persist_history_to_volume(
        mock_interpreter,
        sample_workspace_id,
        sample_user_id,
        sample_session_id,
        sample_history,
    )
    restored = await restore_history_from_volume(
        mock_interpreter,
        sample_workspace_id,
        sample_user_id,
        sample_session_id,
    )
    assert restored is not None
    msgs = list(restored.messages)
    assert len(msgs) == 2
    assert msgs[0]["user_message"] == "Hello"


@pytest.mark.asyncio
async def test_restore_history_from_volume_returns_none_when_missing(
    mock_interpreter: MagicMock,
    sample_workspace_id: str,
    sample_user_id: str,
) -> None:
    """VAL-PERSIST-003: Returns None when no file exists on the volume."""
    restored = await restore_history_from_volume(
        mock_interpreter,
        sample_workspace_id,
        sample_user_id,
        "nonexistent-session",
    )
    assert restored is None


@pytest.mark.asyncio
async def test_restore_history_from_volume_returns_none_on_bad_json(
    sample_workspace_id: str,
    sample_user_id: str,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-003: Returns None when file content is invalid JSON."""
    interp = MagicMock()
    interp.volume_mount_path = "/home/daytona/memory"

    async def _aread(path: str) -> str:
        return "not valid json!!!"

    interp.aread_file = _aread

    restored = await restore_history_from_volume(interp, sample_workspace_id, sample_user_id, sample_session_id)
    assert restored is None


@pytest.mark.parametrize(
    "bad_content",
    ["null", "[]", '"string"', "42"],
)
@pytest.mark.asyncio
async def test_restore_history_from_volume_returns_none_for_non_dict_json(
    bad_content: str,
    sample_workspace_id: str,
    sample_user_id: str,
    sample_session_id: str,
) -> None:
    """VAL-BACKEND-PERSIST-002: Returns None for non-dict JSON payloads."""
    interp = MagicMock()
    interp.volume_mount_path = "/home/daytona/memory"

    async def _aread(path: str) -> str:
        return bad_content

    interp.aread_file = _aread

    restored = await restore_history_from_volume(interp, sample_workspace_id, sample_user_id, sample_session_id)
    assert restored is None


# ---------------------------------------------------------------------------
# VAL-PERSIST-002: Session metadata persisted to DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_session_metadata_calls_upsert(
    mock_repository: AsyncMock,
    sample_workspace_id: str,
    sample_user_id: str,
    sample_session_id: str,
    sample_tenant_id: str,
) -> None:
    """VAL-PERSIST-002: persist_session_metadata calls repository.upsert_chat_session."""
    result = await persist_session_metadata(
        mock_repository,
        workspace_id=sample_workspace_id,
        user_id=sample_user_id,
        session_id=sample_session_id,
        tenant_id=sample_tenant_id,
        title="Test session",
    )
    assert result is not None
    mock_repository.upsert_chat_session.assert_called_once()


@pytest.mark.asyncio
async def test_persist_session_metadata_passes_correct_ids(
    mock_repository: AsyncMock,
    sample_workspace_id: str,
    sample_user_id: str,
    sample_session_id: str,
    sample_tenant_id: str,
) -> None:
    """VAL-PERSIST-002: UpsertRequest is constructed with correct UUIDs."""
    import uuid as _uuid

    from fleet_rlm.integrations.database.repository_chat import ChatSessionUpsertRequest

    await persist_session_metadata(
        mock_repository,
        workspace_id=sample_workspace_id,
        user_id=sample_user_id,
        session_id=sample_session_id,
        tenant_id=sample_tenant_id,
    )
    call_args = mock_repository.upsert_chat_session.call_args
    request: ChatSessionUpsertRequest = call_args[0][0]
    assert request.tenant_id == _uuid.UUID(sample_tenant_id)
    assert request.workspace_id == _uuid.UUID(sample_workspace_id)
    assert request.user_id == _uuid.UUID(sample_user_id)
    assert request.session_id == _uuid.UUID(sample_session_id)


@pytest.mark.asyncio
async def test_persist_session_metadata_noop_when_repository_none() -> None:
    """VAL-PERSIST-002: Returns None gracefully when repository is None."""
    result = await persist_session_metadata(
        None,
        workspace_id="ws",
        user_id="u",
        session_id="s",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )
    assert result is None


@pytest.mark.asyncio
async def test_persist_session_metadata_noop_on_invalid_uuid(
    mock_repository: AsyncMock,
) -> None:
    """VAL-PERSIST-002: Returns None when tenant_id is not a valid UUID."""
    result = await persist_session_metadata(
        mock_repository,
        workspace_id="not-a-uuid",
        user_id="also-not",
        session_id="bad",
        tenant_id="bad-uuid",
    )
    assert result is None
    mock_repository.upsert_chat_session.assert_not_called()


# ---------------------------------------------------------------------------
# VAL-PERSIST-005: Session import/export round-trip
# ---------------------------------------------------------------------------


def _make_runtime(monkeypatch: Any, *, interpreter: Any | None = None) -> Any:
    """Construct a minimal AgentRuntime with mocked LLM components."""
    from fleet_rlm.runtime.agent.runtime import AgentRuntime

    def fake_forward(self, chat_history, user_message, **kwargs):
        return dspy.Prediction(response="fake")

    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.FleetAgent.forward", fake_forward)
    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.runtime.discover_tools",
        lambda: [],
    )
    return AgentRuntime(interpreter=interpreter)


def test_export_session_produces_valid_schema(
    monkeypatch: pytest.MonkeyPatch,
    sample_history: dspy.History,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-005: export_session produces a dict with required schema keys."""
    runtime = _make_runtime(monkeypatch)
    runtime.history = sample_history
    payload = export_session(runtime, sample_session_id)
    missing = REQUIRED_SCHEMA_KEYS - set(payload.keys())
    assert not missing, f"Missing keys: {missing}"


def test_export_session_encodes_turns(
    monkeypatch: pytest.MonkeyPatch,
    sample_history: dspy.History,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-005: export_session encodes all history turns."""
    runtime = _make_runtime(monkeypatch)
    runtime.history = sample_history
    payload = export_session(runtime, sample_session_id)
    assert len(payload["turns"]) == 2


def test_export_session_includes_core_memory(
    monkeypatch: pytest.MonkeyPatch,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-005: export_session includes core_memory."""
    runtime = _make_runtime(monkeypatch)
    runtime.core_memory["test_key"] = "test_value"
    payload = export_session(runtime, sample_session_id)
    assert "core_memory" in payload
    assert payload["core_memory"].get("test_key") == "test_value"


def test_import_session_restores_history(
    monkeypatch: pytest.MonkeyPatch,
    sample_history: dspy.History,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-005: import_session restores history into runtime."""
    runtime = _make_runtime(monkeypatch)
    runtime.history = sample_history
    exported = export_session(runtime, sample_session_id)

    # Reset and reimport
    runtime.history = dspy.History(messages=[])
    result = import_session(runtime, exported)

    assert result["status"] == "ok"
    assert result["history_turns"] == 2
    assert isinstance(runtime.history, dspy.History)
    msgs = list(runtime.history.messages)
    assert msgs[0]["user_message"] == "Hello"


def test_import_session_replaces_stale_core_memory_keys(
    monkeypatch: pytest.MonkeyPatch,
    sample_session_id: str,
) -> None:
    runtime = _make_runtime(monkeypatch)
    runtime.core_memory["stale"] = "old"

    exported = export_session(runtime, sample_session_id)
    exported["core_memory"] = {"fresh": "new"}

    import_session(runtime, exported)

    assert runtime.core_memory["fresh"] == "new"
    assert runtime.core_memory["persona"]
    assert "stale" not in runtime.core_memory


def test_import_session_restores_loaded_documents(
    monkeypatch: pytest.MonkeyPatch,
    sample_session_id: str,
) -> None:
    runtime = _make_runtime(monkeypatch)
    runtime.loaded_document_paths = ["/docs/a.md"]

    exported = export_session(runtime, sample_session_id)
    runtime.loaded_document_paths = ["/docs/stale.md"]

    import_session(runtime, exported)

    assert runtime.loaded_document_paths == ["/docs/a.md"]


def test_import_session_returns_correct_session_id(
    monkeypatch: pytest.MonkeyPatch,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-005: import_session result contains the session_id."""
    runtime = _make_runtime(monkeypatch)
    exported = export_session(runtime, sample_session_id)
    result = import_session(runtime, exported)
    assert result["session_id"] == sample_session_id


def test_export_import_round_trip_preserves_all_turns(
    monkeypatch: pytest.MonkeyPatch,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-005: Full round-trip preserves all turns."""
    runtime = _make_runtime(monkeypatch)
    runtime.history = dspy.History(
        messages=[
            {"user_message": "Turn 1", "response": "Reply 1"},
            {"user_message": "Turn 2", "response": "Reply 2"},
            {"user_message": "Turn 3", "response": "Reply 3"},
        ]
    )
    exported = export_session(runtime, sample_session_id)
    runtime.history = dspy.History(messages=[])
    import_session(runtime, exported)

    msgs = list(runtime.history.messages)
    assert len(msgs) == 3
    assert msgs[2]["user_message"] == "Turn 3"
    assert msgs[2]["response"] == "Reply 3"


def test_agent_runtime_export_session_method(
    monkeypatch: pytest.MonkeyPatch,
    sample_history: dspy.History,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-005: AgentRuntime.export_session delegates to persistence module."""
    runtime = _make_runtime(monkeypatch)
    runtime.history = sample_history
    payload = runtime.export_session(sample_session_id)
    assert payload["session_id"] == sample_session_id
    assert len(payload["turns"]) == 2


def test_agent_runtime_import_session_method(
    monkeypatch: pytest.MonkeyPatch,
    sample_history: dspy.History,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-005: AgentRuntime.import_session restores state."""
    runtime = _make_runtime(monkeypatch)
    runtime.history = sample_history
    exported = runtime.export_session(sample_session_id)

    runtime.history = dspy.History(messages=[])
    result = runtime.import_session(exported)
    assert result["status"] == "ok"
    assert result["history_turns"] == 2


def test_agent_runtime_export_import_threads_daytona_state(
    monkeypatch: pytest.MonkeyPatch,
    sample_session_id: str,
) -> None:
    class _FakeInterpreter:
        def __init__(self) -> None:
            self.imported: dict[str, Any] | None = None

        def export_session_state(self) -> dict[str, Any]:
            return {"daytona": {"sandbox_id": "sbx-1"}}

        def import_session_state(self, state: dict[str, Any]) -> None:
            self.imported = state

    interpreter = _FakeInterpreter()
    runtime = _make_runtime(monkeypatch, interpreter=interpreter)
    runtime._db_session_id = sample_session_id

    exported = runtime.export_session_state()
    assert exported["daytona"]["sandbox_id"] == "sbx-1"

    runtime.import_session_state(exported)
    assert interpreter.imported == exported


def test_agent_runtime_reset_clears_session_local_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _make_runtime(monkeypatch)
    runtime.history = dspy.History(messages=[{"user_message": "hi"}])
    runtime.core_memory["stale"] = "old"
    runtime.loaded_document_paths = ["/docs/a.md"]
    runtime.batch_concurrency = 8

    result = runtime.reset(clear_sandbox_buffers=False)

    assert result == {"status": "ok", "buffers_cleared": False}
    assert list(runtime.history.messages) == []
    assert "stale" not in runtime.core_memory
    assert runtime.core_memory["persona"]
    assert runtime.loaded_document_paths == []
    assert runtime.batch_concurrency is None


# ---------------------------------------------------------------------------
# VAL-PERSIST-006: ChatSessionState uses dspy.History as backing store
# ---------------------------------------------------------------------------


def test_agent_runtime_history_is_dspy_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-PERSIST-006: AgentRuntime.history is a dspy.History instance."""
    runtime = _make_runtime(monkeypatch)
    assert isinstance(runtime.history, dspy.History)


def test_agent_runtime_history_updated_after_chat_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-PERSIST-006: history remains dspy.History after chat_turn accumulation."""
    runtime = _make_runtime(monkeypatch)
    runtime.chat_turn("hello")
    assert isinstance(runtime.history, dspy.History)


def test_agent_runtime_history_is_dspy_history_after_import(
    monkeypatch: pytest.MonkeyPatch,
    sample_history: dspy.History,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-006: history remains dspy.History after import_session."""
    runtime = _make_runtime(monkeypatch)
    runtime.history = sample_history
    exported = runtime.export_session(sample_session_id)
    runtime.import_session(exported)
    assert isinstance(runtime.history, dspy.History)


# ---------------------------------------------------------------------------
# Integration-style: persist + restore round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_volume_persist_restore_round_trip(
    mock_interpreter: MagicMock,
    sample_history: dspy.History,
    sample_workspace_id: str,
    sample_user_id: str,
    sample_session_id: str,
) -> None:
    """VAL-PERSIST-001 + VAL-PERSIST-003: Full volume round-trip."""
    await persist_history_to_volume(
        mock_interpreter,
        sample_workspace_id,
        sample_user_id,
        sample_session_id,
        sample_history,
    )
    restored = await restore_history_from_volume(
        mock_interpreter,
        sample_workspace_id,
        sample_user_id,
        sample_session_id,
    )
    assert isinstance(restored, dspy.History)
    original_msgs = list(sample_history.messages)
    restored_msgs = list(restored.messages)
    assert len(restored_msgs) == len(original_msgs)
    for orig, rest in zip(original_msgs, restored_msgs):
        assert orig["user_message"] == rest["user_message"]
        assert orig["response"] == rest["response"]
