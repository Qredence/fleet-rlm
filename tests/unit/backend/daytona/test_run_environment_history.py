"""Daytona's narrow committed-history transport and Run-local attachment copies."""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.paths import VolumePaths
from fleet_rlm.sessions.history import to_canonical_history_records
from fleet_rlm.sessions.history_transport import CommittedSessionHistory
from fleet_rlm.sessions.models import HistoryMessage, SessionHistory, TurnAccess, TurnInput


def _make_claim(*, history_messages: tuple[HistoryMessage, ...] = ()):
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )

    async def not_cancelled() -> bool:
        return False

    return ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("current"),
        SessionHistory(messages=history_messages),
        not_cancelled,
        _RunClaimToken(uuid4(), base_checkpoint_version=2),
    )


def test_turn_preparation_exposes_committed_session_history_builder() -> None:
    """The Session transport owner builds the Daytona representation."""

    from fleet_rlm.sessions import history_transport

    assert callable(history_transport.committed_history_for_claim)


def test_daytona_helper_returns_committed_session_history_not_dspy_history() -> None:
    """The helper returns ``CommittedSessionHistory`` and never ``dspy.History``."""

    from fleet_rlm.sessions.history_transport import committed_history_for_claim

    claim = _make_claim(
        history_messages=(
            HistoryMessage("user", "earlier user request"),
            HistoryMessage("assistant", "earlier assistant answer"),
        )
    )
    history = committed_history_for_claim(claim)

    assert type(history) is CommittedSessionHistory
    # The transport is NOT the in-process ``dspy.History`` type; the
    # Dayona broker requires the ``SandboxSerializable`` wrapper.
    assert not isinstance(history, dspy.History)


def test_daytona_helper_records_equal_canonical_history_records() -> None:
    """The Dayona transport records equal :func:`to_canonical_history_records` output."""

    from fleet_rlm.sessions.history import claimed_history_records
    from fleet_rlm.sessions.history_transport import committed_history_for_claim

    claim = _make_claim(
        history_messages=(
            HistoryMessage("user", "earlier user request"),
            HistoryMessage("assistant", "earlier assistant answer"),
            HistoryMessage("user", "next user request"),
            HistoryMessage("assistant", "next assistant answer"),
        )
    )
    transport = committed_history_for_claim(claim)

    committed_turns, user_requests = claimed_history_records(claim)
    canonical = to_canonical_history_records(committed_turns, user_requests=user_requests)

    # Records are deep-equal and the transport carries them in order.
    assert list(transport.messages) == canonical
    assert [dict(record) for record in transport.messages] == [
        {"request": "earlier user request", "answer": "earlier assistant answer"},
        {"request": "next user request", "answer": "next assistant answer"},
    ]


def test_daytona_helper_skips_orphan_user_messages_without_assistant_answers() -> None:
    """Orphan user messages never pair with the next assistant answer."""

    from fleet_rlm.sessions.history_transport import committed_history_for_claim

    claim = _make_claim(
        history_messages=(
            HistoryMessage("user", "first user request"),
            # No assistant message between the two users; the second user
            # message is the start of the uncommitted Turn, so the first
            # user request is dropped.
            HistoryMessage("user", "second user request"),
            HistoryMessage("assistant", "second assistant answer"),
        )
    )
    transport = committed_history_for_claim(claim)

    assert [dict(record) for record in transport.messages] == [
        {"request": "second user request", "answer": "second assistant answer"}
    ]


def test_daytona_helper_returns_empty_history_for_fresh_session() -> None:
    """A claim with no committed Turns still produces a valid empty transport."""

    from fleet_rlm.sessions.history_transport import committed_history_for_claim

    claim = _make_claim(history_messages=())
    transport = committed_history_for_claim(claim)

    assert type(transport) is CommittedSessionHistory
    assert list(transport.messages) == []


@pytest.mark.asyncio
async def test_run_attachment_copy_uses_local_scratch_with_parent_directories() -> None:
    from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher
    from fleet_rlm.daytona.turn_environment import _DaytonaRunSink
    from tests.support.workspace_storage import daytona_host_io_for_test_sandbox

    class Fs:
        def __init__(self) -> None:
            self.directories = {"/tmp/fleet"}
            self.files: dict[str, bytes] = {}

        async def create_folder(self, path: str, _mode: str) -> None:
            assert str(PurePosixPath(path).parent) in self.directories
            self.directories.add(path)

        async def upload_file(self, data: bytes, path: str) -> None:
            self.files[path] = bytes(data)

        async def download_file(self, path: str) -> bytes:
            return self.files[path]

        async def delete_file(self, path: str) -> None:
            self.files.pop(path)

    run_id = uuid4()
    attachment_id = uuid4()
    fs = Fs()
    fs.directories.add(f"/tmp/fleet/{run_id}")
    sandbox = SimpleNamespace(fs=fs)
    paths = VolumePaths.from_mount("/volume")
    dispatcher = SyncBridgeDispatcher()
    dispatcher.set_loop(asyncio.get_running_loop())
    host_io = daytona_host_io_for_test_sandbox(
        sandbox,
        workspace_id=uuid4(),
        dispatcher=dispatcher,
        volume_root=str(paths.mount_path),
        max_file_bytes=1_000_000,
    )
    sink = _DaytonaRunSink(
        sandbox,
        dispatcher=dispatcher,
        paths=paths,
        host_io=host_io,
        run_id=run_id,
    )
    path = f"/tmp/fleet/{run_id}/attachments/{attachment_id}/notes.txt"

    await sink.write_private(path, b"body")
    assert await sink.read(path, max_bytes=4) == b"body"
    assert f"/tmp/fleet/{run_id}/attachments/{attachment_id}" in fs.directories
    await sink.remove_private(path)
    assert path not in fs.files


@pytest.mark.asyncio
async def test_run_sink_routes_sync_storage_to_host_or_private_scratch() -> None:
    from fleet_rlm.daytona.turn_environment import _DaytonaRunSink

    class SandboxFs:
        def __init__(self) -> None:
            self.files: dict[str, bytes] = {}

        async def upload_file(self, data: bytes, path: str) -> None:
            self.files[path] = bytes(data)

        async def download_file(self, path: str) -> bytes:
            return self.files[path]

        async def delete_file(self, path: str) -> None:
            self.files.pop(path, None)

    class HostFs:
        def __init__(self) -> None:
            self.files: dict[str, bytes] = {}

        def write_bytes(self, path: str, data: bytes, *, max_bytes: int | None = None) -> None:
            assert max_bytes is None or len(data) <= max_bytes
            self.files[path] = bytes(data)

        def read_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
            value = self.files[path]
            return value if max_bytes is None else value[:max_bytes]

        def exists(self, path: str) -> bool:
            return path in self.files

        def remove_bytes(self, path: str) -> None:
            self.files.pop(path, None)

        def remove(self, path: str) -> None:
            self.remove_bytes(path)

    run_id = uuid4()
    sandbox_fs = SandboxFs()
    host_fs = HostFs()
    from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher

    dispatcher = SyncBridgeDispatcher()
    dispatcher.set_loop(asyncio.get_running_loop())
    sink = _DaytonaRunSink(
        SimpleNamespace(fs=sandbox_fs),
        dispatcher=dispatcher,
        paths=VolumePaths.from_mount("/volume"),
        host_io=SimpleNamespace(volume_fs=host_fs),
        run_id=run_id,
    )
    scratch_path = f"/tmp/fleet/{run_id}/attachments/source.txt"
    host_path = "/volume/sessions/session/runs/run/result.json"

    await asyncio.to_thread(sink.volume_fs.write_bytes, scratch_path, b"scratch")
    await asyncio.to_thread(sink.volume_fs.write_bytes, host_path, b"host")

    assert scratch_path in sandbox_fs.files
    assert host_fs.files == {host_path: b"host"}
    assert await asyncio.to_thread(sink.volume_fs.read_bytes, scratch_path) == b"scratch"
    assert await asyncio.to_thread(sink.volume_fs.read_bytes, host_path) == b"host"
