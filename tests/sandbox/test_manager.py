from __future__ import annotations

import hashlib
import importlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.config import FleetSettings
from fleet_rlm.sandbox import manager as manager_module
from fleet_rlm.sandbox import repl as repl_module
from fleet_rlm.sandbox import workspace_io
from fleet_rlm.sandbox.manager import DaytonaSandboxManager


def test_inventory_and_artifact_collection_record_only_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    spans: dict[str, Any] = {}

    @contextmanager
    def phase(name: str):
        yield SimpleNamespace(set_outputs=lambda value: spans.__setitem__(name, value))

    monkeypatch.setattr(workspace_io, "phase_span", phase)
    monkeypatch.setattr(workspace_io.mlflow, "get_current_active_span", lambda: object())
    monkeypatch.setattr(
        DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: json.dumps(["artifacts/private.txt"])
    )
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, _path, **_kwargs: b"secret")
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager._sandbox = object()

    evidence = manager.execution_evidence()

    assert evidence["artifacts"]["complete"] is True
    assert spans == {
        "sandbox.workspace_inventory": {"files": 1},
        "sandbox.artifact_collection": {"files": 1, "complete": True},
    }
    spans.clear()
    monkeypatch.setattr(workspace_io.mlflow, "get_current_active_span", lambda: None)
    assert manager.list_workspace_files() == ["artifacts/private.txt"]
    assert manager.execution_evidence()["artifacts"]["complete"] is True
    assert not spans


def test_workspace_file_transfer_handles_uploads_larger_than_exec_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    content = b"fleet-data" * 250_000

    class FileSystem:
        uploaded: tuple[bytes, str, int] | None = None

        def upload_file(self, data: bytes, path: str, *, timeout: int) -> None:
            self.uploaded = (data, path, timeout)

        def download_file(self, path: str, timeout: int) -> bytes:
            assert path == "/workspace/files/large.csv"
            assert timeout == 1800
            return content

    filesystem = FileSystem()
    sandbox = SimpleNamespace(state=None, fs=filesystem)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager._client = object()
    manager._sandbox = sandbox

    def remote_exec(_self: DaytonaSandboxManager, sandbox_arg: Any, code: str, **_kwargs) -> str:
        assert sandbox_arg is sandbox
        assert len(code.encode("utf-8")) < 10_000
        if "if not os.path.isfile(target)" in code:
            return json.dumps([{"size": len(content)}])
        return json.dumps([{"size": None}])

    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", remote_exec)

    assert manager.write_workspace_file("files/large.csv", content) == "/workspace/files/large.csv"
    assert filesystem.uploaded == (content, "/workspace/files/large.csv", 1800)
    assert manager.read_workspace_file("files/large.csv", max_bytes=len(content)) == content


@pytest.mark.parametrize("subpath", ["/shared", "shared/../x", "shared//x", "shared/"])
def test_shared_notes_subpath_must_be_a_relative_volume_prefix(subpath: str) -> None:
    with pytest.raises(ValueError):
        FleetSettings(fleet_shared_notes_subpath=subpath)


def test_shared_notes_subpath_defaults_and_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_SHARED_NOTES_SUBPATH", raising=False)
    assert FleetSettings().fleet_shared_notes_subpath == "shared/learned-notes"
    assert FleetSettings(fleet_shared_notes_subpath="").fleet_shared_notes_subpath == ""


def test_root_sandbox_mounts_shared_notes_beside_its_session_subtree() -> None:
    root = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")

    mounts = root.volume_mounts(child=False)
    assert [(mount.mount_path, mount.subpath) for mount in mounts] == [
        ("/workspace", "sessions/session-a"),
        ("/learned", "shared/learned-notes"),
    ]
    assert {mount.volume_id for mount in mounts} == {"volume"}
    child_mounts = root.volume_mounts(child=True)
    assert [(mount.mount_path, mount.subpath) for mount in child_mounts] == [
        ("/workspace", f"agents/session-a/{root.agent_id}")
    ]


def test_shared_notes_mount_is_optional_and_never_reaches_child_sandboxes() -> None:
    plain = DaytonaSandboxManager("key", "org", "volume", "session-a")
    assert [mount.mount_path for mount in plain.volume_mounts(child=False)] == ["/workspace"]
    shared = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    assert [mount.mount_path for mount in shared.volume_mounts(child=True)] == ["/workspace"]


def test_learned_note_lands_as_an_unreviewed_redacted_file_on_the_shared_mount(monkeypatch) -> None:
    uploads: list[tuple[bytes, str]] = []
    commands: list[str] = []
    sandbox = SimpleNamespace(
        fs=SimpleNamespace(upload_file=lambda content, path, **_kwargs: uploads.append((content, path)))
    )
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    monkeypatch.setattr(
        DaytonaSandboxManager, "exec_remote_python", lambda _self, code: commands.append(code) or "True\n"
    )
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)

    path = manager.write_learned_note("turn-0123456789", "Use api_key=hidden\n\nAlways verify digits.", "tr-1")

    assert path == "/learned/session-a-turn0123.md"
    assert uploads[0][1] == path
    assert uploads[0][0].decode("utf-8") == (
        "---\nsession: session-a\nturn: turn0123\ntrace_id: tr-1\nstatus: unreviewed\n"
        "summary: Use api_key=[redacted]\n---\nUse api_key=[redacted]\n\nAlways verify digits.\n"
    )
    assert "/learned" in commands[0]


def test_learned_note_is_skipped_when_the_sandbox_predates_the_mount(monkeypatch) -> None:
    requested: list[bool] = []
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: "False\n")
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: requested.append(True))

    assert manager.write_learned_note("turn-1", "A note.") is None
    assert requested == []


def test_learned_note_write_is_skipped_for_empty_notes_and_disabled_mounts(monkeypatch) -> None:
    monkeypatch.setattr(
        DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: pytest.fail("no sandbox call expected")
    )
    enabled = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    disabled = DaytonaSandboxManager("key", "org", "volume", "session-a")

    assert enabled.write_learned_note("turn-1", "  \n") is None
    assert disabled.write_learned_note("turn-1", "A note.") is None


def test_cancelled_manager_cannot_restart_remote_execution(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    manager, client = _provision(monkeypatch, tmp_path, service)
    first = manager.ensure_sandbox()
    manager.cancel_turn()
    with pytest.raises(RuntimeError, match="cancelled"):
        manager.ensure_sandbox()
    manager.reset_turn_budget()
    manager.end_turn()
    # The stopped root is discarded, so the next Turn cold-resumes into a fresh sandbox.
    second = manager.ensure_sandbox()
    assert second.id != first.id
    assert len(client.created) == 2


def test_an_unconfirmed_stop_discards_the_root_and_is_not_stopped_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    manager, client = _provision(monkeypatch, tmp_path, service)
    assert manager.ensure_sandbox().id == "sandbox-1"
    stops: list[str] = []

    def refuse(sandbox: Any, **_kwargs: Any) -> None:
        stops.append(sandbox.id)
        raise RuntimeError("provider refused the stop")

    client.stop = refuse
    manager.cancel_turn()
    assert manager.cleanup_outcome == "unconfirmed"
    assert manager.live_sandbox_id is None, "a root whose stop is unconfirmed must be discarded"

    manager.reset_turn_budget()
    manager.end_turn()
    assert stops == ["sandbox-1"], "end_turn must not stop a discarded root again"
    # Discarding is not reuse: the next Turn provisions a new root instead of retrying this one.
    assert manager.ensure_sandbox().id == "sandbox-2"
    assert stops == ["sandbox-1"]


def test_terminal_teardown_stops_the_root_once_and_records_the_outcome() -> None:
    sandbox = SimpleNamespace(id="sandbox-teardown", state="started")
    manager, calls = _manager_with(sandbox)
    events: list[dict[str, Any]] = []
    manager.event_callback = events.append

    manager.cancel_turn()
    assert calls == ["stop"]
    assert manager.cleanup_outcome == "stopped"
    assert manager._contexts == {}, "interpreter contexts are invalid after the root stops"

    # The watchdog, a client disconnect, and the terminal Turn branch may all reach the teardown.
    manager.cancel_turn()
    assert calls == ["stop"], "a second caller must not issue another provider stop"
    assert [event["event"] for event in events] == ["cancellation_requested"]

    # A fresh Turn owns a fresh teardown outcome.
    manager.reset_turn_budget()
    manager.end_turn()
    assert manager.cleanup_outcome is None


def test_a_raising_provider_stop_records_unconfirmed(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(manager_module, "with_retries", lambda call, **_kwargs: call())
    manager = DaytonaSandboxManager("key", "org", "volume", "stop-failure")

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("provider refused the stop")

    manager._sandbox = SimpleNamespace(id="sandbox-stop-failure", state="started")
    manager._client = SimpleNamespace(stop=refuse)
    # Teardown is a finalization seam: no exception escapes, callers read the recorded outcome.
    manager.cancel_turn()
    assert manager.cleanup_outcome == "unconfirmed"


def test_a_raising_notification_never_skips_child_or_root_teardown(caplog: pytest.LogCaptureFixture) -> None:
    sandbox = SimpleNamespace(id="sandbox-notify-failure", state="started")
    manager, calls = _manager_with(sandbox)
    manager._children["child-1"] = SimpleNamespace(shutdown=lambda: calls.append("child_shutdown"))

    def refuse_notification(_event: dict[str, Any]) -> None:
        raise RuntimeError("stream closed; payload=secret")

    manager.event_callback = refuse_notification

    with caplog.at_level("WARNING", logger="fleet_rlm.sandbox.manager"):
        manager.cancel_turn()

    # The notification is a side channel: every other teardown step still ran, in order.
    assert calls == ["child_shutdown", "stop"]
    assert manager.cleanup_outcome == "stopped"
    assert manager._contexts == {}
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "Lifecycle notification cancellation_requested failed" in warnings[0].getMessage()
    assert "secret" not in warnings[0].getMessage()

    # A later caller sees the completed teardown and issues neither a stop nor a second warning.
    caplog.clear()
    with caplog.at_level("WARNING", logger="fleet_rlm.sandbox.manager"):
        manager.cancel_turn()
    assert calls == ["child_shutdown", "stop"]
    assert manager.cleanup_outcome == "stopped"
    assert not [record for record in caplog.records if record.levelname == "WARNING"]


def test_cleanup_stopped_is_cached_only_after_teardown_attempts_have_run() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "cache-order")
    manager._sandbox = SimpleNamespace(id="sandbox-cache-order", state="started")
    observed_at_notification: list[str | None] = []
    observed_at_stop: list[str | None] = []

    manager.event_callback = lambda _event: observed_at_notification.append(manager.cleanup_outcome)

    def stop(*_args: Any, **_kwargs: Any) -> None:
        observed_at_stop.append(manager.cleanup_outcome)

    manager._client = SimpleNamespace(stop=stop)

    manager.cancel_turn()

    assert observed_at_notification == [None], "the notification must not run after a cached outcome"
    assert observed_at_stop == [None], "the outcome is cached only once the stop returned"
    assert manager.cleanup_outcome == "stopped"


def test_a_raising_child_shutdown_still_attempts_the_root_stop() -> None:
    sandbox = SimpleNamespace(id="sandbox-child-failure", state="started")
    manager, calls = _manager_with(sandbox)

    def refuse_shutdown() -> None:
        raise RuntimeError("child shutdown failed")

    manager._children["child-1"] = SimpleNamespace(shutdown=refuse_shutdown)

    manager.cancel_turn()

    assert calls == ["stop"], "the root stop is attempted even when a child refused to shut down"
    assert manager.cleanup_outcome == "unconfirmed"


def _child_with_provider(root: DaytonaSandboxManager, index: int, deleted: list[str]) -> DaytonaSandboxManager:
    """Build a real child manager whose provider deletion is observable and never contacts Daytona."""
    child = DaytonaSandboxManager("key", "org", "volume", "state-test", parent=root)
    child._sandbox = SimpleNamespace(
        id=f"child-{index}",
        state="started",
        process=SimpleNamespace(exec=lambda *_args, **_kwargs: SimpleNamespace(exit_code=0, result="")),
    )
    child._client = SimpleNamespace(delete=lambda sandbox, **_kwargs: deleted.append(sandbox.id))
    root._children[child.agent_id] = child
    return child


def _child_with_blocking_delete(
    root: DaytonaSandboxManager,
    *,
    started: threading.Event,
    release: threading.Event,
    deleted: list[str],
) -> DaytonaSandboxManager:
    """Build a child whose provider delete blocks until the test releases it."""
    child = DaytonaSandboxManager("key", "org", "volume", "state-test", parent=root)
    child._sandbox = SimpleNamespace(
        id="child-blocking",
        state="started",
        process=SimpleNamespace(exec=lambda *_args, **_kwargs: SimpleNamespace(exit_code=0, result="")),
    )

    def delete(sandbox: Any, **_kwargs: Any) -> None:
        deleted.append(sandbox.id)
        started.set()
        release.wait(timeout=10)

    child._client = SimpleNamespace(delete=delete)
    root._children[child.agent_id] = child
    return child


def test_concurrent_child_shutdowns_join_the_same_provider_cleanup() -> None:
    root = DaytonaSandboxManager("key", "org", "volume", "state-test")
    started, release = threading.Event(), threading.Event()
    deleted: list[str] = []
    child = _child_with_blocking_delete(root, started=started, release=release, deleted=deleted)

    first_outcome: list[bool] = []
    first = threading.Thread(target=lambda: first_outcome.append(child.shutdown()), name="normal-child-shutdown")
    first.start()
    assert started.wait(timeout=5), "the first caller must reach provider deletion"

    joined_outcome: list[bool] = []
    second = threading.Thread(target=lambda: joined_outcome.append(child.shutdown()), name="racing-child-shutdown")
    second.start()
    time.sleep(0.1)
    assert joined_outcome == [], "a concurrent caller must not report confirmed before the provider delete returns"

    release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert first_outcome == [True]
    assert joined_outcome == [True], "every caller observes the completed provider result"
    assert deleted == ["child-blocking"], "the provider delete runs exactly once"
    assert root._children == {}


def test_cancellation_cannot_acknowledge_while_a_child_cleanup_is_in_flight() -> None:
    root = DaytonaSandboxManager("key", "org", "volume", "state-test")
    started, release = threading.Event(), threading.Event()
    deleted: list[str] = []
    child = _child_with_blocking_delete(root, started=started, release=release, deleted=deleted)

    normal = threading.Thread(target=child.shutdown, name="normal-child-shutdown")
    normal.start()
    assert started.wait(timeout=5), "normal child completion must be inside provider deletion"

    acknowledged: list[None] = []

    def cancel() -> None:
        root.cancel_turn()
        acknowledged.append(None)

    cancelled = threading.Thread(target=cancel, name="cancel-turn")
    cancelled.start()
    time.sleep(0.1)
    assert acknowledged == [], "cancel_turn must not return before the child's provider cleanup resolves"
    assert root.cleanup_outcome is None

    release.set()
    normal.join(timeout=5)
    cancelled.join(timeout=5)

    assert root.cleanup_outcome == "stopped", "the cancelled Turn observes the joined provider result"
    assert deleted == ["child-blocking"]


def test_cancellation_observes_an_unconfirmed_join_rather_than_an_early_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    # A failed provider delete must surface as unconfirmed; the join must not be skipped or retried
    # outside the caller that observes it.
    monkeypatch.setattr(manager_module, "with_retries", lambda call, **_kwargs: call())
    root = DaytonaSandboxManager("key", "org", "volume", "state-test")
    started, release = threading.Event(), threading.Event()
    attempts: list[str] = []
    child = DaytonaSandboxManager("key", "org", "volume", "state-test", parent=root)
    child._sandbox = SimpleNamespace(
        id="child-failing",
        state="started",
        process=SimpleNamespace(exec=lambda *_args, **_kwargs: SimpleNamespace(exit_code=0, result="")),
    )

    def refuse_delete(sandbox: Any, **_kwargs: Any) -> None:
        attempts.append(sandbox.id)
        started.set()
        release.wait(timeout=10)
        raise RuntimeError("provider delete failed")

    child._client = SimpleNamespace(delete=refuse_delete)
    root._children[child.agent_id] = child

    normal = threading.Thread(target=child.shutdown, name="normal-child-shutdown")
    normal.start()
    assert started.wait(timeout=5)

    acknowledged: list[None] = []

    def cancel() -> None:
        root.cancel_turn()
        acknowledged.append(None)

    cancelled = threading.Thread(target=cancel, name="cancel-turn")
    cancelled.start()
    time.sleep(0.1)
    assert acknowledged == []
    assert root.cleanup_outcome is None

    release.set()
    normal.join(timeout=5)
    cancelled.join(timeout=5)

    assert root.cleanup_outcome == "unconfirmed"
    assert root._children[child.agent_id] is child, "the failed child stays registered for a later attempt"
    assert child._sandbox is not None
    # The normal completion failed and restored the handle, so the joining cancellation makes its
    # own provider attempt rather than reusing the failed result. Both attempts failed.
    assert attempts == ["child-failing", "child-failing"]


def test_a_local_journal_unlink_failure_keeps_provider_confirmation(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import pathlib

    monkeypatch.setattr(manager_module, "with_retries", lambda call, **_kwargs: call())
    root = DaytonaSandboxManager("key", "org", "volume", "state-test")
    deleted: list[str] = []
    child = _child_with_provider(root, 1, deleted)
    original_unlink = pathlib.Path.unlink

    def refuse_journal_unlink(self: pathlib.Path, *args: Any, **kwargs: Any) -> None:
        if ".fleet/allocations" in str(self) and self.suffix == ".json":
            raise OSError("journal is busy")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "unlink", refuse_journal_unlink)

    with caplog.at_level("WARNING", logger="fleet_rlm.sandbox.manager"):
        confirmed = child.shutdown()

    assert confirmed is True, "a local bookkeeping failure never reverses provider confirmation"
    assert deleted == ["child-1"]
    assert child._sandbox is None, "an already-deleted handle is never restored"
    assert root._children == {}
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "Local allocation journal unlink failed" in warnings[0].getMessage()
    assert "journal is busy" not in caplog.text, "the warning stays bounded; no local error text leaks"


def test_a_failed_child_provider_delete_keeps_the_child_registered_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr(manager_module, "with_retries", lambda call, **_kwargs: call())
    root, calls = _manager_with(SimpleNamespace(id="sandbox-retry-root", state="started"))
    attempts: list[str] = []
    child = DaytonaSandboxManager("key", "org", "volume", "state-test", parent=root)
    child._sandbox = SimpleNamespace(
        id="child-retry",
        state="started",
        process=SimpleNamespace(exec=lambda *_args, **_kwargs: SimpleNamespace(exit_code=0, result="")),
    )

    def refuse(_sandbox: Any, **_kwargs: Any) -> None:
        attempts.append("refused")
        raise RuntimeError("provider delete failed")

    child._client = SimpleNamespace(delete=refuse)
    root._children[child.agent_id] = child

    root.cancel_turn()

    assert root.cleanup_outcome == "unconfirmed"
    assert calls == ["stop"]
    assert root._children[child.agent_id] is child, "a genuinely failed cleanup stays registered"
    assert child._sandbox is not None, "the handle is retained for a later attempt"
    assert attempts == ["refused"]

    child._client = SimpleNamespace(delete=lambda sandbox, **_kwargs: attempts.append(sandbox.id))
    assert child.shutdown() is True
    assert root._children == {}


def test_a_raising_shared_callback_never_skips_real_child_provider_cleanup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager, calls = _manager_with(SimpleNamespace(id="sandbox-notify-children", state="started"))
    deleted: list[str] = []
    for index in (1, 2):
        _child_with_provider(manager, index, deleted)

    def refuse_notification(_event: dict[str, Any]) -> None:
        raise RuntimeError("stream closed; payload=secret")

    manager.event_callback = refuse_notification

    with caplog.at_level("WARNING", logger="fleet_rlm.sandbox.manager"):
        manager.cancel_turn()

    # Delivery is a side channel: both children reached provider deletion and the root still stopped.
    assert deleted == ["child-1", "child-2"]
    assert calls == ["stop"]
    assert manager._children == {}
    assert manager.cleanup_outcome == "stopped", "a notification failure is not a provider failure"
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 5, "each refused lifecycle notification logs exactly one warning"
    assert all("Lifecycle notification" in record.getMessage() for record in warnings)
    assert "secret" not in caplog.text


@pytest.mark.parametrize("raises", [False, True], ids=["returns-unconfirmed", "raises"])
def test_a_failing_child_never_skips_its_sibling_or_the_root_stop(raises: bool) -> None:
    manager, calls = _manager_with(SimpleNamespace(id="sandbox-child-failure", state="started"))
    observed: list[str] = []

    def refuse_shutdown() -> bool:
        observed.append("failed-child")
        if raises:
            raise RuntimeError("provider delete failed")
        return False

    def succeed_shutdown() -> bool:
        observed.append("healthy-child")
        return True

    manager._children["child-1"] = SimpleNamespace(shutdown=refuse_shutdown)
    manager._children["child-2"] = SimpleNamespace(shutdown=succeed_shutdown)

    manager.cancel_turn()

    assert observed == ["failed-child", "healthy-child"]
    assert calls == ["stop"], "a failed child must not skip the root stop"
    assert manager.cleanup_outcome == "unconfirmed"


def test_a_successful_turn_keeps_one_warm_root_for_the_next_turn() -> None:
    sandbox = _RemoteSandbox("started")
    manager, calls = _manager_with(sandbox)

    for _turn in range(2):
        manager.reset_turn_budget()
        manager.end_turn()
        assert manager.ensure_sandbox() is sandbox
        assert manager.cleanup_outcome is None

    assert calls == [], "success never starts, stops, or re-provisions the root"


def test_managers_with_the_same_credentials_share_one_daytona_client(monkeypatch: pytest.MonkeyPatch) -> None:

    created: list[tuple[str, str]] = []

    class FakeDaytona:
        def __init__(self, config: Any) -> None:
            created.append((config.api_key, config.organization_id))

    monkeypatch.setattr(manager_module, "Daytona", FakeDaytona)
    monkeypatch.setattr(manager_module, "_DAYTONA_CLIENTS", {})

    first = manager_module._shared_daytona_client("key", "org")
    assert manager_module._shared_daytona_client("key", "org") is first
    assert manager_module._shared_daytona_client("key", "other-org") is not first
    assert created == [("key", "org"), ("key", "other-org")]


class _RemoteSandbox(SimpleNamespace):
    """A sandbox handle whose cached `state` lags the provider until `refresh_data()` is called."""

    def __init__(self, cached: str, *remote: str) -> None:
        super().__init__(id="sandbox-remote", state=cached, refreshes=0)
        self._remote = list(remote)

    def refresh_data(self) -> None:
        self.refreshes += 1
        if self._remote:
            self.state = self._remote.pop(0)


def _manager_with(sandbox: Any, **client_methods: Any) -> tuple[DaytonaSandboxManager, list[str]]:
    calls: list[str] = []
    manager = DaytonaSandboxManager("key", "org", "volume", "state-test")
    manager._sandbox = sandbox
    manager._contexts["main"] = object()
    manager._client = SimpleNamespace(
        start=lambda *_args, **_kwargs: calls.append("start"),
        stop=lambda *_args, **_kwargs: calls.append("stop"),
        **client_methods,
    )
    return manager, calls


def test_ensure_sandbox_refreshes_remote_state_before_deciding_to_restart() -> None:
    # Daytona auto-stops an idle sandbox; the cached handle still says "started".
    sandbox = _RemoteSandbox("started", "stopped")
    manager, calls = _manager_with(sandbox)
    assert manager.ensure_sandbox() is sandbox
    assert sandbox.refreshes == 1 and calls == ["start"]
    assert manager._contexts == {}, "interpreter contexts are invalid after a stop"


def test_ensure_sandbox_waits_for_a_settling_sandbox_before_starting_it(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(manager_module, "_STATE_POLL_SECONDS", 0.0)
    sandbox = _RemoteSandbox("started", "stopping", "stopping", "stopped")
    manager, calls = _manager_with(sandbox)
    assert manager.ensure_sandbox() is sandbox
    assert sandbox.refreshes == 3 and calls == ["start"]


def test_ensure_sandbox_refuses_a_failed_sandbox() -> None:
    sandbox = _RemoteSandbox("started", "error")
    manager, calls = _manager_with(sandbox)
    with pytest.raises(RuntimeError, match="error"):
        manager.ensure_sandbox()
    assert calls == []


def test_ensure_sandbox_does_not_touch_a_running_sandbox() -> None:
    sandbox = _RemoteSandbox("started", "started")
    manager, calls = _manager_with(sandbox)
    assert manager.ensure_sandbox() is sandbox
    assert calls == [] and manager._contexts != {}


@pytest.mark.parametrize("transient", [True, False])
def test_idempotent_daytona_calls_retry_only_transient_failures(
    monkeypatch: pytest.MonkeyPatch, transient: bool
) -> None:
    from daytona.common.errors import DaytonaConnectionError, DaytonaError

    monkeypatch.setattr(repl_module, "_RETRY_BASE_SECONDS", 0.0)
    attempts: list[str] = []

    class FileSystem:
        def upload_file(self, _data: bytes, path: str, *, timeout: int) -> None:  # noqa: ARG002
            attempts.append(path)
            if len(attempts) == 1:
                if transient:
                    raise DaytonaConnectionError("websocket closed")
                raise DaytonaError("no such volume", status_code=400)

    manager = DaytonaSandboxManager("key", "org", "volume", "retry-test")
    manager._client = object()
    manager._sandbox = SimpleNamespace(state=None, fs=FileSystem())
    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", lambda *_a, **_k: json.dumps([{"size": None}]))
    if transient:
        assert manager.write_workspace_file("files/a.txt", b"x") == "/workspace/files/a.txt"
        assert len(attempts) == 2
    else:
        with pytest.raises(DaytonaError, match="no such volume"):
            manager.write_workspace_file("files/a.txt", b"x")
        assert len(attempts) == 1


def test_run_code_transport_failures_are_never_retried() -> None:
    from daytona.common.errors import DaytonaTimeoutError

    attempts: list[int] = []

    def never(*_args: Any, **_kwargs: Any) -> None:
        attempts.append(1)
        raise DaytonaTimeoutError("Execution timed out")

    sandbox = SimpleNamespace(code_interpreter=SimpleNamespace(run_code=never), fs=SimpleNamespace())
    with pytest.raises(DaytonaTimeoutError):
        repl_module.run(sandbox, object(), {"code": "1"}, on_call=lambda _c: {}, timeout=5)
    assert attempts == [1]


def test_should_share_budget_categories_and_recoveries_across_children():
    root = DaytonaSandboxManager("key", "org", "volume", "usage", max_llm_calls=10)
    child = DaytonaSandboxManager("key", "org", "volume", "usage", parent=root)
    root.consume_budget(category="routing")
    root.consume_budget(3, category="skill_selection")
    child.consume_budget(6)
    child.record_sandbox_error()
    child.record_submit_rejection()
    usage = root.turn_usage()
    assert usage == child.turn_usage()
    assert usage == {
        "lm_calls": 10,
        "sandbox_errors": 1,
        "submit_rejections": 1,
        "budget_breakdown": {"generation_query": 6, "routing": 1, "skill_selection": 3, "other_decisions": 0},
    }
    with pytest.raises(RuntimeError, match="budget exhausted"):
        child.consume_budget()
    assert root.turn_usage() == usage
    usage["budget_breakdown"]["routing"] = 99
    assert root.turn_usage()["budget_breakdown"]["routing"] == 1
    root.reset_turn_budget()
    root.end_turn()
    assert root.turn_usage()["submit_rejections"] == root.turn_usage()["lm_calls"] == 0
    assert sum(root.turn_usage()["budget_breakdown"].values()) == 0


@pytest.mark.parametrize("fails", [False, True], ids=["copied", "copy_failure"])
def test_should_trace_child_copy_lineage_and_preserve_failure_cleanup(monkeypatch, fails):
    from contextlib import contextmanager

    import mlflow

    spans = []

    @contextmanager
    def start_span(name, span_type):
        row = {"name": name, "span_type": span_type, "attributes": {}, "status": "running"}
        spans.append(row)
        try:
            yield SimpleNamespace(
                set_attributes=row["attributes"].update,
                set_attribute=lambda k, v: row["attributes"].__setitem__(k, v),
                set_inputs=lambda v: row.__setitem__("inputs", v),
                set_outputs=lambda v: row.__setitem__("outputs", v),
                set_status=lambda v: row.__setitem__("status", v.lower()),
            )
            if row["status"] == "running":
                row["status"] = "ok"
        except BaseException:
            row["status"] = "error"
            raise

    monkeypatch.setattr(mlflow, "start_span", start_span)
    closed = []
    root = DaytonaSandboxManager("secret-api-key", "org", "volume", "copy")
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: ["files/data.txt"])
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "read_workspace_files",
        lambda _self, paths: {path: b"private file content" for path in paths},
    )

    def write(*_args):
        if fails:
            raise ValueError("copy failed")

    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_files", write)
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda self: closed.append(self.agent_id))
    if fails:
        with pytest.raises(ValueError, match="copy failed"):
            root.allocate_child()
    else:
        child = root.allocate_child()
        assert spans[0]["attributes"]["child_agent_id"] == child.agent_id
    copy = next(row for row in spans if row["name"] == "sandbox.input_copy")
    assert copy["attributes"]["parent_agent_id"] == root.agent_id
    assert copy["attributes"]["bytes_read"] == len(b"private file content")
    read = next(row for row in spans if row["name"] == "sandbox.input_read")
    write_span = next(row for row in spans if row["name"] == "sandbox.input_write")
    assert read["outputs"]["completed"] is True
    assert read["attributes"]["files_read"] == 1
    assert read["attributes"]["bytes_read"] == len(b"private file content")
    assert write_span["outputs"]["completed"] is not fails
    assert write_span["attributes"]["bytes_requested"] == len(b"private file content")
    assert copy["outputs"]["completed"] is not fails
    assert copy["status"] == ("error" if fails else "ok")
    assert bool(closed) == fails
    # A failed batch never leaves a partial input receipt behind.
    assert root._child_receipts[next(iter(root._child_receipts))]["inputs"] == (
        {} if fails else {"files/data.txt": hashlib.sha256(b"private file content").hexdigest()}
    )
    assert "private file content" not in json.dumps(spans) and "secret-api-key" not in json.dumps(spans)


def test_artifact_execution_is_not_replayed_after_losing_its_response(monkeypatch: pytest.MonkeyPatch) -> None:
    from daytona.common.errors import DaytonaConnectionError

    monkeypatch.setattr(repl_module, "_RETRY_BASE_SECONDS", 0.0)
    executions: list[str] = []
    cancelled = threading.Event()

    def execute(command: str, *, timeout: int | None) -> None:
        assert timeout is not None and 1 <= timeout <= 31
        executions.append(command)
        raise DaytonaConnectionError("response lost after execution")

    manager = DaytonaSandboxManager("key", "org", "volume", "artifact-replay")
    manager._client = object()
    manager._sandbox = SimpleNamespace(state=None, process=SimpleNamespace(exec=execute))
    manager._deadline = time.monotonic() + 60
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda *_args, **_kwargs: b"print(42)")
    monkeypatch.setattr(DaytonaSandboxManager, "cancel_turn", lambda _self: cancelled.set())

    with pytest.raises(repl_module.ExecutionOutcomeUnknownError, match="verification outcome unknown"):
        manager.verify_python_artifact("artifacts/result.py", 30)

    assert len(executions) == 1
    assert cancelled.is_set()


def _run_remote_locally(tmp_path, calls):
    """Execute generated remote code with /workspace and /learned redirected to local stand-ins."""
    import contextlib
    import io

    def run(_self, code: str) -> str:
        calls.append(code)
        local = code.replace("'/workspace'", repr(str(tmp_path / "workspace"))).replace(
            "'/learned'", repr(str(tmp_path / "learned"))
        )
        # A temporary directory is not a mount point; treat the stand-in as mounted.
        local = local.replace("os.path.ismount(", "os.path.isdir(")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(local, {})
        return out.getvalue()

    return run


def test_turn_inputs_gathers_digests_notes_and_environment_in_one_call(monkeypatch, tmp_path) -> None:
    import hashlib

    workspace = tmp_path / "workspace"
    (workspace / "files").mkdir(parents=True)
    (workspace / "files" / "a.txt").write_bytes(b"alpha")
    (workspace / "outside").mkdir()
    (workspace / "outside" / "secret.txt").write_bytes(b"secret")
    (workspace / "files" / "link.txt").symlink_to(workspace / "outside" / "secret.txt")
    (workspace / "linked-dir").symlink_to(workspace / "outside")
    (tmp_path / "learned").mkdir()
    (tmp_path / "learned" / "s-1.md").write_text("---\nsummary: Use Chudnovsky\n---\nbody\n")
    calls: list[str] = []
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", _run_remote_locally(tmp_path, calls))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")

    inputs = manager.turn_inputs()

    assert len(calls) == 1
    # Symbolic links are skipped and never traversed.
    assert inputs["digests"] == {"files/a.txt": hashlib.sha256(b"alpha").hexdigest()}
    assert inputs["notes"] == [{"file": "s-1.md", "summary": "Use Chudnovsky"}]
    assert inputs["environment"].startswith("Sandbox environment: Python ")
    assert "installed: " in inputs["environment"] and "not installed: " in inputs["environment"]
    assert "set_int_max_str_digits(0)" in inputs["environment"]


def test_workspace_listing_and_digests_share_managed_roots_without_touching_scratch(monkeypatch, tmp_path):
    from fleet_rlm.workspace import _ALLOWED_WORKSPACE_ROOTS

    workspace = tmp_path / "workspace"
    expected = {}
    for root in _ALLOWED_WORKSPACE_ROOTS:
        directory = workspace / root / "nested"
        directory.mkdir(parents=True)
        (directory / "source.txt").write_bytes(b"source")
        expected[f"{root}/nested/source.txt"] = hashlib.sha256(b"source").hexdigest()
    (workspace / "checkout").mkdir()
    scratch = workspace / "checkout" / "db.py"
    scratch.write_bytes(b"unmanaged edits")
    (workspace / "bundle.txt").write_bytes(b"bundle")
    (workspace / "files" / "link").symlink_to(workspace / "checkout", target_is_directory=True)
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", _run_remote_locally(tmp_path, []))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")

    assert manager.list_workspace_files() == sorted(expected)
    assert manager.turn_inputs()["digests"] == expected
    assert scratch.read_bytes() == b"unmanaged edits"
    assert (workspace / "bundle.txt").read_bytes() == b"bundle"


@pytest.mark.parametrize("method", ["list_workspace_files", "turn_inputs"])
@pytest.mark.parametrize("unsafe", ["root-symlink", "backslash", "control"])
def test_workspace_inventory_rejects_unsafe_managed_entries(monkeypatch, tmp_path, method, unsafe):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if unsafe == "root-symlink":
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret").write_bytes(b"secret")
        (workspace / "files").symlink_to(outside, target_is_directory=True)
    else:
        (workspace / "files").mkdir()
        name = "bad\\name" if unsafe == "backslash" else "bad\nname"
        (workspace / "files" / name).write_bytes(b"source")
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", _run_remote_locally(tmp_path, []))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")

    with pytest.raises(ValueError, match=r"workspace|Workspace"):
        getattr(manager, method)()


@pytest.mark.parametrize("response", ["", "{}", "null", '"files/a"', "[1]", '{"files/a": 1}'])
def test_workspace_listing_rejects_malformed_responses(monkeypatch, response):
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda *_args: response)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    with pytest.raises((ValueError, RuntimeError)):
        manager.list_workspace_files()


@pytest.mark.parametrize(
    "response",
    ["", "{}", "null", '{"digests": {"files/a": "short"}}', '{"digests": {"outside/a": "' + "a" * 64 + '"}}'],
)
def test_workspace_digests_reject_malformed_responses(monkeypatch, response):
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda *_args: response)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    with pytest.raises((ValueError, RuntimeError)):
        manager.turn_inputs()


def test_turn_inputs_skips_notes_without_the_shared_mount(monkeypatch, tmp_path) -> None:
    (tmp_path / "workspace").mkdir()
    calls: list[str] = []
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", _run_remote_locally(tmp_path, calls))

    inputs = DaytonaSandboxManager("key", "org", "volume", "session-a").turn_inputs()

    assert inputs["notes"] == [] and inputs["digests"] == {}
    assert "/learned" not in calls[0]


def test_turn_inputs_returns_only_the_newest_bounded_shared_notes(monkeypatch, tmp_path) -> None:
    import os

    (tmp_path / "workspace").mkdir()
    learned = tmp_path / "learned"
    learned.mkdir()
    for index in range(23):
        path = learned / f"note-{index}.md"
        path.write_text(f"---\nsummary: {'x' * 150}\n---\n")
        os.utime(path, (index + 1, index + 1))
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", _run_remote_locally(tmp_path, []))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/notes")

    notes = manager.turn_inputs()["notes"]

    assert [row["file"] for row in notes] == [f"note-{index}.md" for index in range(22, 2, -1)]
    assert all(row["summary"] == "x" * 120 for row in notes)


def test_turn_inputs_preserves_required_inputs_when_optional_notes_are_unreadable(monkeypatch, tmp_path, caplog):
    workspace = tmp_path / "workspace"
    (workspace / "files").mkdir(parents=True)
    (workspace / "files/data.txt").write_bytes(b"source")
    # A directory with a note suffix cannot be opened as a note file.
    (tmp_path / "learned/unreadable.md").mkdir(parents=True)
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", _run_remote_locally(tmp_path, []))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/notes")

    inputs = manager.turn_inputs()

    assert inputs["notes"] == []
    assert inputs["digests"] == {"files/data.txt": hashlib.sha256(b"source").hexdigest()}
    assert inputs["environment"].startswith("Sandbox environment: Python ")
    assert "Shared learned notes were unavailable" in caplog.text


@pytest.mark.parametrize("notes_limit", [0, 51])
def test_turn_inputs_skips_notes_outside_supported_limits(monkeypatch, tmp_path, notes_limit):
    (tmp_path / "workspace").mkdir()
    calls = []
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", _run_remote_locally(tmp_path, calls))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/notes")

    assert manager.turn_inputs(notes_limit=notes_limit)["notes"] == []
    assert "/learned" not in calls[0]


def test_turn_inputs_reject_a_malformed_digest_listing(monkeypatch) -> None:
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "exec_remote_python",
        lambda _self, _code: '{"digests": {"files/a.txt": 3}, "notes": [], "environment": ""}',
    )

    with pytest.raises(RuntimeError, match="Invalid workspace digest"):
        DaytonaSandboxManager("key", "org", "volume", "session-a").turn_inputs()


class _BatchFileSystem:
    """Records Daytona batch transfers; `download_files` serves `contents` keyed by absolute path."""

    def __init__(self, contents: dict[str, bytes]) -> None:
        self.contents = contents
        self.downloads: list[list[str]] = []
        self.uploads: list[list[tuple[str, bytes]]] = []

    def download_files(self, requests, timeout):
        assert timeout == 1800
        self.downloads.append([request.source for request in requests])
        return [SimpleNamespace(source=r.source, result=self.contents.get(r.source), error=None) for r in requests]

    def upload_files(self, uploads, *, timeout):
        assert timeout == 1800
        self.uploads.append([(upload.destination, upload.source) for upload in uploads])


def _batch_manager(monkeypatch, filesystem, sizes):
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    sandbox = SimpleNamespace(fs=filesystem)
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)
    checks: list[str] = []

    def remote_exec(_self, _sandbox, code, **_kwargs):
        checks.append(code)
        return json.dumps([{"size": size} for size in sizes])

    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", remote_exec)
    return manager, checks


def test_batched_transfer_uses_one_check_and_one_transfer_per_side(monkeypatch) -> None:
    files = {f"files/{index}.txt": f"row {index}".encode() for index in range(5)}
    filesystem = _BatchFileSystem({f"/workspace/{path}": data for path, data in files.items()})
    manager, checks = _batch_manager(monkeypatch, filesystem, [len(data) for data in files.values()])

    contents = manager.read_workspace_files(list(files))
    manager.write_workspace_files(contents)

    assert contents == files
    assert len(checks) == 2 and len(filesystem.downloads) == 1 and len(filesystem.uploads) == 1
    assert filesystem.uploads[0] == [(f"/workspace/{path}", data) for path, data in files.items()]
    assert "os.makedirs(parent,exist_ok=True)" in checks[1] and "os.makedirs" not in checks[0]


def test_batched_read_rejects_a_file_that_changed_after_its_check(monkeypatch) -> None:
    filesystem = _BatchFileSystem({"/workspace/files/a.txt": b"grown content"})
    manager, _ = _batch_manager(monkeypatch, filesystem, [3])

    with pytest.raises(RuntimeError, match=r"changed or could not be downloaded: files/a\.txt"):
        manager.read_workspace_files(["files/a.txt"])


def test_batched_read_refuses_the_quota_before_downloading(monkeypatch) -> None:
    filesystem = _BatchFileSystem({})
    manager, _ = _batch_manager(monkeypatch, filesystem, [60, 60])

    with pytest.raises(ValueError, match="transfer quota"):
        manager.read_workspace_files(["files/a", "files/b"], max_total_bytes=100)
    assert filesystem.downloads == []


@pytest.mark.parametrize("bad", ["files/link.txt", "linked-dir/secret.txt"])
def test_batched_metadata_check_refuses_symbolic_links(monkeypatch, tmp_path, bad) -> None:
    import contextlib
    import io

    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "ok.txt").write_bytes(b"ok")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_bytes(b"secret")
    (tmp_path / "files" / "link.txt").symlink_to(tmp_path / "outside" / "secret.txt")
    (tmp_path / "linked-dir").symlink_to(tmp_path / "outside")

    def run_locally(_self, _sandbox, code, **_kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(code.replace("os.path.realpath('/workspace')", f"os.path.realpath({str(tmp_path)!r})"), {})
        return out.getvalue()

    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", run_locally)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")

    assert manager._workspace_files_metadata(object(), ["files/ok.txt"], create_parents=False) == [{"size": 2}]
    with pytest.raises(ValueError, match="symbolic link"):
        manager._workspace_files_metadata(object(), ["files/ok.txt", bad], create_parents=False)


def test_empty_batched_write_makes_no_remote_call(monkeypatch) -> None:
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: pytest.fail("no sandbox call"))

    DaytonaSandboxManager("key", "org", "volume", "session-a").write_workspace_files({})


# --- Turn snapshot preflight -------------------------------------------------


class _SnapshotDTO(SimpleNamespace):
    """The subset of the provider snapshot DTO that preflight reads."""


class _RecordingSnapshotService:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[str] = []

    def get(self, name: str) -> Any:
        self.calls.append(name)
        if self.error is not None:
            raise self.error
        return self.result


class _RecordingClient:
    """A Daytona client stub that records snapshot lookups and sandbox creates."""

    def __init__(self, service: _RecordingSnapshotService) -> None:
        self.snapshot = service
        self.created: list[Any] = []

    def create(self, params: Any) -> Any:
        self.created.append(params)
        return SimpleNamespace(id=f"sandbox-{len(self.created)}", state="started")

    def list(self, _query: Any) -> list[Any]:
        return []

    def stop(self, _sandbox: Any, **_kwargs: Any) -> None:
        return None


@pytest.fixture(autouse=True)
def clear_snapshot_preflight_cache():

    from fleet_rlm.config import get_settings

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    snapshot_module.clear_preflight_cache()
    get_settings.cache_clear()
    yield
    snapshot_module.clear_preflight_cache()
    get_settings.cache_clear()


def _provision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, service: _RecordingSnapshotService, **manager_values: Any
) -> tuple[DaytonaSandboxManager, _RecordingClient]:
    monkeypatch.chdir(tmp_path)
    client = _RecordingClient(service)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", **manager_values)
    manager._client = client
    return manager, client


def test_manager_snapshot_defaults_to_repo_owned_name() -> None:
    from fleet_rlm.sandbox.snapshots import BUILD_COMMAND, DEFAULT_DEFINITION, SNAPSHOT_NAME

    assert SNAPSHOT_NAME == "fleet-rlm-python313-v14"
    resources = DEFAULT_DEFINITION.resources()
    assert (resources.cpu, resources.memory, resources.disk) == (4, 8, 8)
    assert DEFAULT_DEFINITION.python_version == "3.13"
    packages = dict(DEFAULT_DEFINITION.packages)
    assert packages["numpy"] == "2.5.1"
    assert packages["pandas"] == "3.0.5"
    assert {"matplotlib", "pillow", "openpyxl", "pypdf", "uv", "pytest", "ruff"} <= packages.keys()
    assert not {"dspy", "gepa", "markitdown", "scipy", "scikit-learn", "pyarrow"} & packages.keys()
    assert "/setup" in BUILD_COMMAND
    root = DaytonaSandboxManager("key", "org", "volume", "session-a")
    assert root.snapshot == SNAPSHOT_NAME
    explicit = DaytonaSandboxManager("key", "org", "volume", "session-a", snapshot="fleet-rlm-python313-v12")
    assert explicit.snapshot == "fleet-rlm-python313-v12"


@pytest.mark.parametrize(
    "change",
    [
        {"name": "fleet-rlm-python313-v11"},
        {"cpu": 2},
        {"memory": 4},
        {"disk": 8, "memory": 8, "cpu": 4, "python_version": "3.12"},
        {"packages": (("numpy", "2.5.0"), ("pandas", "3.0.5"), ("mpmath", "1.4.1"))},
    ],
)
def test_snapshot_digest_changes_per_field_and_ignores_package_order(change: dict[str, Any]) -> None:
    import dataclasses

    from fleet_rlm.sandbox.snapshots import DEFAULT_DEFINITION

    baseline = DEFAULT_DEFINITION.digest()
    assert dataclasses.replace(DEFAULT_DEFINITION, **change).digest() != baseline
    reordered = dataclasses.replace(DEFAULT_DEFINITION, packages=tuple(reversed(DEFAULT_DEFINITION.packages)))
    assert reordered.digest() == baseline


def test_preflight_accept_and_cache_once_per_credentials(monkeypatch, tmp_path) -> None:
    from fleet_rlm.sandbox.snapshots import SNAPSHOT_NAME

    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    first, first_client = _provision(monkeypatch, tmp_path, service)
    root_sandbox = first.ensure_sandbox()
    assert root_sandbox.id == "sandbox-1"
    assert first_client.created[0].snapshot == SNAPSHOT_NAME
    assert service.calls == [SNAPSHOT_NAME]
    assert first.snapshot_identity is not None
    assert first.snapshot_identity.as_dict() == {
        "name": SNAPSHOT_NAME,
        "id": "snap-1",
        "cpu": 4,
        "mem": 8,
        "disk": 8,
        "definition_digest": first.snapshot_identity.definition_digest,
    }

    second, second_client = _provision(monkeypatch, tmp_path, service)
    assert second.ensure_sandbox().id == "sandbox-1"
    assert len(second_client.created) == 1
    assert service.calls == [SNAPSHOT_NAME], "the process cache must reuse the resolution"


@pytest.mark.parametrize("state", ["missing", "inactive", "pending", "building", "error"])
def test_preflight_unusable_snapshot_refuses_before_creation(monkeypatch, tmp_path, state) -> None:
    from fleet_rlm.sandbox.snapshots import BUILD_COMMAND, SNAPSHOT_NAME, SnapshotUnavailableError

    service = _RecordingSnapshotService(
        None if state == "missing" else _SnapshotDTO(id="snap-1", state=state, cpu=4, mem=8, disk=8)
    )
    manager, client = _provision(monkeypatch, tmp_path, service)
    with pytest.raises(SnapshotUnavailableError) as error:
        manager.ensure_sandbox()
    message = str(error.value)
    assert SNAPSHOT_NAME in message
    assert f"state={state}" in message
    if state == "inactive":
        assert "Activate snapshot" in message
    elif state in {"pending", "building"}:
        assert "wait for it to become active" in message
    else:
        assert BUILD_COMMAND in message
    assert snapshot_error_is_single_error(message)
    assert client.created == []
    assert manager._sandbox is None


def test_preflight_unusable_snapshot_treats_provider_error_as_missing(monkeypatch, tmp_path) -> None:
    from daytona import DaytonaNotFoundError

    from fleet_rlm.sandbox.snapshots import SnapshotUnavailableError

    service = _RecordingSnapshotService(error=DaytonaNotFoundError("fleet-rlm-python313-v10"))
    manager, client = _provision(monkeypatch, tmp_path, service)
    with pytest.raises(SnapshotUnavailableError, match="state=missing"):
        manager.ensure_sandbox()
    assert client.created == []


def test_preflight_surfaces_provider_failures_and_refresh_evicts_stale_cache() -> None:

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    active = _SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8)

    class Service:
        result: Any = active
        error: Exception | None = None

        def get(self, _name: str) -> Any:
            if self.error is not None:
                raise self.error
            return self.result

    service = Service()
    first = snapshot_module.preflight(service, api_key="key", org_id="org")
    assert first.id == "snap-1"
    service.error = TimeoutError("Daytona temporarily unavailable")
    with pytest.raises(snapshot_module.SnapshotUnavailableError, match="Check Daytona access"):
        snapshot_module.preflight(service, api_key="key", org_id="org", refresh=True)
    with pytest.raises(snapshot_module.SnapshotUnavailableError, match="Check Daytona access"):
        snapshot_module.preflight(service, api_key="key", org_id="org")


def test_custom_snapshot_does_not_claim_default_recipe() -> None:
    from fleet_rlm.sandbox.snapshots import preflight

    identity = preflight(
        _RecordingSnapshotService(_SnapshotDTO(id="custom-id", state="active", cpu=4, mem=8, disk=8)),
        name="team-custom-image",
        api_key="custom-key",
        org_id="org",
    )
    assert identity.definition_digest is None
    assert identity.provenance()["snapshot_definition_digest"] is None


def test_provider_build_metadata_is_hashed_and_not_copied_to_provenance() -> None:
    from datetime import UTC, datetime

    from daytona_api_client import BuildInfo

    from fleet_rlm.sandbox.snapshots import preflight

    dto = _SnapshotDTO(
        id="snap-1",
        state="active",
        cpu=4,
        mem=8,
        disk=8,
        image_name="registry.example/fleet@sha256:abc",
        build_info=BuildInfo(
            dockerfile_content="FROM python:3.13",
            context_hashes=["sha256:def"],
            snapshot_ref="provider-build-1",
            created_at=datetime(2026, 10, 23, tzinfo=UTC),
            updated_at=datetime(2026, 10, 23, tzinfo=UTC),
        ),
    )
    identity = preflight(_RecordingSnapshotService(dto), api_key="build-key", org_id="org")
    assert identity.build_digest is not None
    assert "dockerfile_content" not in identity.provenance()
    dto.build_info.dockerfile_content = "FROM python:3.12"
    changed = preflight(_RecordingSnapshotService(dto), api_key="build-key", org_id="org", refresh=True)
    assert changed.build_digest != identity.build_digest


def test_preflight_expires_success_after_sixty_seconds(monkeypatch) -> None:

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    now = [100.0]
    monkeypatch.setattr(snapshot_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    service = _RecordingSnapshotService(_SnapshotDTO(id="old", state="active", cpu=4, mem=8, disk=8))
    first = snapshot_module.preflight(service, api_key="key", org_id="org")
    service.result = _SnapshotDTO(id="new", state="active", cpu=4, mem=8, disk=8)
    now[0] += 59
    assert snapshot_module.preflight(service, api_key="key", org_id="org") is first
    now[0] += 1
    assert snapshot_module.preflight(service, api_key="key", org_id="org").id == "new"
    assert len(service.calls) == 2


@pytest.mark.parametrize("operation", ["clear", "refresh"])
@pytest.mark.parametrize("old_failure", [False, True])
def test_new_preflight_does_not_join_or_get_overwritten_by_old_flight(operation, old_failure) -> None:

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    class Service:
        def get(self, name: str) -> Any:
            calls.append(name)
            if len(calls) == 1:
                started.set()
                assert release.wait(5)
                if old_failure:
                    raise TimeoutError("old lookup failed")
                return _SnapshotDTO(id="old", state="active", cpu=4, mem=8, disk=8)
            return _SnapshotDTO(id="new", state="active", cpu=4, mem=8, disk=8)

    service = Service()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(snapshot_module.preflight, service, api_key="key", org_id="org")
        assert started.wait(5)
        try:
            if operation == "clear":
                snapshot_module.clear_preflight_cache()
            current = snapshot_module.preflight(service, api_key="key", org_id="org", refresh=operation == "refresh")
            assert current.id == "new"
        finally:
            release.set()
        if old_failure:
            with pytest.raises(snapshot_module.SnapshotUnavailableError):
                first.result(timeout=5)
        else:
            assert first.result(timeout=5).id == "old"
    assert snapshot_module.preflight(service, api_key="key", org_id="org") is current
    assert len(calls) == 2


def test_concurrent_failed_preflight_releases_waiters_and_can_retry(monkeypatch) -> None:

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    started = threading.Event()
    joined = threading.Event()
    release = threading.Event()

    class Service:
        calls = 0

        def get(self, _name: str) -> Any:
            self.calls += 1
            if self.calls == 1:
                started.set()
                assert release.wait(5)
                raise TimeoutError("provider unavailable")
            return _SnapshotDTO(id="retry", state="active", cpu=4, mem=8, disk=8)

    service = Service()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(snapshot_module.preflight, service, api_key="key", org_id="org")
        assert started.wait(5)
        flight = next(iter(snapshot_module._INFLIGHT.values()))
        original_wait = flight.event.wait

        def observe_wait(timeout=None):
            joined.set()
            return original_wait(timeout)

        monkeypatch.setattr(flight.event, "wait", observe_wait)
        second = pool.submit(snapshot_module.preflight, service, api_key="key", org_id="org")
        try:
            assert joined.wait(5)
        finally:
            release.set()
        for future in (first, second):
            with pytest.raises(snapshot_module.SnapshotUnavailableError, match="Check Daytona access"):
                future.result(timeout=5)
    assert service.calls == 1
    assert snapshot_module.preflight(service, api_key="key", org_id="org").id == "retry"


@pytest.mark.parametrize("status", [401, 404, 500])
def test_preflight_classifies_daytona_errors_without_exposing_the_provider_message(status) -> None:

    from daytona import DaytonaError, DaytonaNotFoundError

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    error_class = DaytonaNotFoundError if status == 404 else DaytonaError
    service = _RecordingSnapshotService(error=error_class("private provider detail", status_code=status))
    with pytest.raises(snapshot_module.SnapshotUnavailableError) as raised:
        snapshot_module.preflight(service, api_key="key", org_id="org")
    assert "private provider detail" not in str(raised.value)
    assert ("state=missing" in str(raised.value)) is (status == 404)


@pytest.mark.parametrize(("cpu", "mem", "disk"), [(3, 8, 8), (4, 6, 8), (4, 8, 7)])
def test_preflight_resources_enforce_floors(monkeypatch, tmp_path, cpu, mem, disk) -> None:
    from fleet_rlm.sandbox.snapshots import BUILD_COMMAND, SNAPSHOT_NAME, SnapshotUnavailableError

    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=cpu, mem=mem, disk=disk))
    manager, client = _provision(monkeypatch, tmp_path, service)
    with pytest.raises(SnapshotUnavailableError) as error:
        manager.ensure_sandbox()
    message = str(error.value)
    assert SNAPSHOT_NAME in message
    assert f"cpu={cpu}" in message and f"mem={mem}" in message and f"disk={disk}" in message
    assert "cpu>=4" in message and "mem>=8" in message and "disk>=8" in message
    assert BUILD_COMMAND in message
    assert client.created == []

    boundary, boundary_client = _provision(
        monkeypatch,
        tmp_path,
        _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8)),
    )
    assert boundary.ensure_sandbox().id == "sandbox-1"
    assert len(boundary_client.created) == 1


def test_concurrent_preflight_shares_single_inflight_lookup() -> None:

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    first_started = threading.Event()
    second_arrived = threading.Event()
    calls: list[str] = []
    dto = _SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8)

    class BlockingService:
        def get(self, name: str):
            calls.append(name)
            if len(calls) == 1:
                first_started.set()
                assert second_arrived.wait(5)
            return dto

    service = BlockingService()

    def second_racer():
        assert first_started.wait(5)
        second_arrived.set()
        return snapshot_module.preflight(service, name=snapshot_module.SNAPSHOT_NAME, api_key="key", org_id="org")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            snapshot_module.preflight, service, name=snapshot_module.SNAPSHOT_NAME, api_key="key", org_id="org"
        )
        second = pool.submit(second_racer)
        assert first.result(timeout=5) is second.result(timeout=5)
    assert calls == [snapshot_module.SNAPSHOT_NAME]


def test_build_and_verify_snapshot_credential_resolution(monkeypatch, tmp_path) -> None:

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    monkeypatch.chdir(tmp_path)
    for name in ("FLEET_DAYTONA_API_KEY", "FLEET_DAYTONA_ORG_ID", "DAYTONA_API_KEY", "DAYTONA_ORG_ID", "FLEET_CONFIG"):
        monkeypatch.delenv(name, raising=False)

    instances: list[Any] = []
    creations: list[tuple[Any, dict[str, Any]]] = []

    class FakeDaytona:
        def __init__(self, config: Any) -> None:
            self.config = config
            self._before = len(creations)
            self.snapshot = SimpleNamespace(
                get=lambda _name: (
                    _SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8)
                    if len(creations) > self._before
                    else None
                ),
                create=lambda params, **kwargs: creations.append((params, kwargs)),
            )
            instances.append(self)

    monkeypatch.setattr(snapshot_module, "Daytona", FakeDaytona)

    with pytest.raises(ValueError, match="FLEET_DAYTONA_API_KEY"):
        snapshot_module.build_snapshot()

    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "fleet-key")
    monkeypatch.setenv("FLEET_DAYTONA_ORG_ID", "fleet-org")
    identity = snapshot_module.build_snapshot(api_key="  explicit-key  ", org_id="  explicit-org  ")
    assert identity.name == snapshot_module.SNAPSHOT_NAME
    assert instances[-1].config.api_key == "explicit-key"
    assert instances[-1].config.organization_id == "explicit-org"
    assert creations[-1][1]["timeout"] == 1200
    assert callable(creations[-1][1]["on_logs"])
    recipe = creations[-1][0].image.dockerfile()
    assert "@sha256:" in recipe and "--require-hashes" in recipe
    assert "pnpm@" in recipe and "typescript@" in recipe

    snapshot_module.build_snapshot(api_key="explicit-key", org_id="explicit-org", timeout_seconds=45, on_logs=None)
    assert creations[-1][1] == {"timeout": 45, "on_logs": None}

    with pytest.raises(ValueError, match="FLEET_DAYTONA_API_KEY"):
        snapshot_module.build_snapshot(api_key="   ", org_id="explicit-org")


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_build_snapshot_rejects_unbounded_or_invalid_timeouts_before_contacting_daytona(timeout, monkeypatch):

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    monkeypatch.setattr(snapshot_module, "Daytona", lambda *_args: pytest.fail("No provider call expected"))
    with pytest.raises(ValueError, match="finite and positive"):
        snapshot_module.build_snapshot(timeout_seconds=timeout)


def test_child_and_snapshot_provisioning_skips_turn_preflight(monkeypatch, tmp_path) -> None:
    from fleet_rlm.sandbox.snapshots import SNAPSHOT_NAME

    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    root, root_client = _provision(monkeypatch, tmp_path, service)
    root.ensure_sandbox()

    child_service = _RecordingSnapshotService(_SnapshotDTO(id="child-snap", state="active", cpu=2, mem=4, disk=8))
    child_client = _RecordingClient(child_service)
    child = DaytonaSandboxManager(
        "key",
        "org",
        "volume",
        "session-a",
        parent=root,
        snapshot="fleet-rlm-python313-child-v5",
    )
    child._client = child_client

    assert child.ensure_sandbox().id == "sandbox-1"
    assert [params.snapshot for params in child_client.created] == ["fleet-rlm-python313-child-v5"]
    assert child.snapshot_identity is None
    assert child_service.calls == []
    assert service.calls == [SNAPSHOT_NAME]
    assert root_client.created[0].snapshot == SNAPSHOT_NAME


def test_snapshot_provenance_is_the_preflight_identity(monkeypatch, tmp_path) -> None:
    from fleet_rlm.sandbox.snapshots import DEFAULT_DEFINITION, SNAPSHOT_NAME

    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    manager, _client = _provision(monkeypatch, tmp_path, service)
    assert manager.snapshot_provenance() == {}, "nothing is recorded before the provider resolves the snapshot"

    manager.ensure_sandbox()

    assert manager.snapshot_provenance() == {
        "snapshot_name": SNAPSHOT_NAME,
        "snapshot_id": "snap-1",
        "snapshot_cpu": 4,
        "snapshot_mem": 8,
        "snapshot_disk": 8,
        "snapshot_definition_digest": DEFAULT_DEFINITION.digest(),
    }
    assert service.calls == [SNAPSHOT_NAME]


def test_snapshot_resolution_callback_fires_before_provisioning(monkeypatch, tmp_path) -> None:
    """A caller records provenance the moment preflight resolves it, before creating anything."""
    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    observed: dict[str, Any] = {}

    def record(identity: Any) -> None:
        observed["identity"] = identity
        observed["created_when_resolved"] = list(client.created)
        observed["provenance_when_resolved"] = manager.snapshot_provenance()

    manager, client = _provision(monkeypatch, tmp_path, service, snapshot_resolved_callback=record)
    assert manager.snapshot_provenance() == {}

    manager.ensure_sandbox()

    assert observed["created_when_resolved"] == [], "the identity is published before the provider create"
    assert observed["identity"] is manager.snapshot_identity
    assert observed["provenance_when_resolved"] == manager.snapshot_provenance()
    assert observed["provenance_when_resolved"]["snapshot_id"] == "snap-1"


def test_child_snapshot_resolution_never_invokes_the_callback(monkeypatch, tmp_path) -> None:
    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    root, _root_client = _provision(monkeypatch, tmp_path, service)
    root.ensure_sandbox()
    resolved: list[Any] = []
    child = DaytonaSandboxManager(
        "key",
        "org",
        "volume",
        "session-a",
        parent=root,
        snapshot_resolved_callback=resolved.append,
    )
    child._client = _RecordingClient(_RecordingSnapshotService())

    child.ensure_sandbox()

    assert resolved == [], "a child inherits the Turn snapshot and resolves no identity of its own"


def test_child_snapshot_provenance_is_not_invented(monkeypatch, tmp_path) -> None:
    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    root, _root_client = _provision(monkeypatch, tmp_path, service)
    root.ensure_sandbox()
    child = DaytonaSandboxManager("key", "org", "volume", "session-a", parent=root)
    child._client = _RecordingClient(_RecordingSnapshotService())
    child.ensure_sandbox()
    assert child.snapshot_provenance() == {}
    assert root.snapshot_provenance()["snapshot_name"]


def snapshot_error_is_single_error(message: str) -> bool:
    return message.count("Snapshot '") == 1


def _ready_manager(*, deadline: float | None, turn_timeout_seconds: int = 900) -> DaytonaSandboxManager:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-bound", turn_timeout_seconds=turn_timeout_seconds)
    manager._client = object()
    manager._sandbox = SimpleNamespace(id="sandbox-bound", state="started")
    manager._contexts = {"ns": object()}
    manager._deadline = deadline
    return manager


def _ready_interpreter() -> Any:
    return SimpleNamespace(ns_id="ns", answer_host_call=lambda _call: {"value": 1})


def _record_host_call_bound(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:

    captured: dict[str, Any] = {}

    def fake_run(_sandbox, _context, _payload, **kwargs):
        captured.update(kwargs)
        return {"stdout": "", "stderr": "", "final": None, "has_value": False, "value": None}

    monkeypatch.setattr(repl_module, "run", fake_run)
    return captured


def test_the_host_call_bound_is_the_remaining_turn_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _record_host_call_bound(monkeypatch)
    manager = _ready_manager(deadline=time.monotonic() + 120)

    manager.run_namespace(_ready_interpreter(), {"code": "1"})

    assert 0 < captured["timeout"] <= 120, "the bound follows the deadline, not a fixed cutoff"


def test_a_deadline_free_turn_sends_no_host_call_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _record_host_call_bound(monkeypatch)
    manager = _ready_manager(deadline=None, turn_timeout_seconds=0)

    manager.run_namespace(_ready_interpreter(), {"code": "1"})

    assert captured["timeout"] is None, "a deadline-free Turn must not invent a positive bound"


def test_an_expired_deadline_stops_the_turn_instead_of_waiting() -> None:
    manager = _ready_manager(deadline=time.monotonic() - 5)
    with pytest.raises(TimeoutError, match="deadline exceeded"):
        manager.run_namespace(_ready_interpreter(), {"code": "1"})


def test_run_namespace_opens_one_execution_token_and_closes_it_when_the_execution_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    captured = _record_host_call_bound(monkeypatch)
    observed: list[bool] = []

    def fake_run(_sandbox, _context, _payload, **kwargs):
        observed.append(kwargs["fence"].is_open())
        captured.update(kwargs)
        return {"stdout": "", "stderr": "", "final": None, "has_value": False, "value": None}

    monkeypatch.setattr(repl_module, "run", fake_run)
    manager = _ready_manager(deadline=time.monotonic() + 120)

    manager.run_namespace(_ready_interpreter(), {"code": "1"})

    assert observed == [True], "a live execution may publish"
    assert not captured["fence"].is_open(), "the execution's window ends with the execution"
    assert manager._execution_fences == set()


def test_a_cancelled_execution_token_is_never_reopened_by_the_next_turn() -> None:
    manager = _ready_manager(deadline=time.monotonic() + 120)
    manager._client = SimpleNamespace(stop=lambda *_args, **_kwargs: None)

    # An execution that is still in flight when the Turn is cancelled keeps its token for good.
    abandoned = manager._open_execution_fence()
    assert abandoned.is_open()

    manager.cancel_turn()
    manager.reset_turn_budget()
    try:
        assert not manager._cancelled.is_set(), "the next Turn clears the shared cancellation Event"
        assert not abandoned.is_open(), "the originating execution stays fenced after the reset"

        live = manager._open_execution_fence()
        assert live is not abandoned and live.is_open(), "a new execution gets its own live token"
        manager._release_execution_fence(live)
    finally:
        manager.end_turn()


def test_an_abandoned_worker_cannot_publish_after_cancellation_and_the_next_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:

    manager = _ready_manager(deadline=time.monotonic() + 120)
    manager._client = SimpleNamespace(stop=lambda *_args, **_kwargs: None)

    class RecordingFs:
        def __init__(self) -> None:
            self.uploads: list[str] = []

        def upload_file(self, data: bytes, path: str, timeout: int | None = None) -> None:  # noqa: ARG002
            self.uploads.append(path)

    sandbox = SimpleNamespace(fs=RecordingFs())
    abandoned = manager._open_execution_fence()
    protocol = repl_module._Protocol(sandbox, lambda _call: {"value": "late answer"}, fence=abandoned)

    # The cancelled Turn acknowledges and the next Turn resets the reusable cancellation Event.
    manager.cancel_turn()
    manager.reset_turn_budget()
    try:
        with caplog.at_level("WARNING", logger="fleet_rlm.sandbox.repl"):
            protocol._answer({"id": "call-abandoned"})
        assert sandbox.fs.uploads == [], "a previous execution cannot publish into the next Turn"
        assert len([record for record in caplog.records if "Fenced" in record.getMessage()]) == 1
        assert "late answer" not in caplog.text
    finally:
        manager.end_turn()


def test_a_late_fence_is_attributed_to_the_execution_that_opened_it(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _record_host_call_bound(monkeypatch)
    manager = _ready_manager(deadline=time.monotonic() + 120)
    originating: list[dict[str, Any]] = []
    later: list[dict[str, Any]] = []
    manager.event_callback = originating.append

    manager.run_namespace(_ready_interpreter(), {"code": "1"})

    # The next Turn installs its own emitter and clears the shared cancellation state.
    manager.reset_turn_budget()
    manager.event_callback = later.append
    try:
        captured["on_fence"]()
    finally:
        manager.event_callback = None

    assert [event["event"] for event in originating] == ["fenced"]
    assert originating[0]["namespace"] == "ns"
    assert later == [], "a late fence never surfaces on the current Turn's stream"


def test_a_fence_whose_channel_is_closed_logs_one_bounded_warning(caplog: pytest.LogCaptureFixture) -> None:
    manager = _ready_manager(deadline=time.monotonic() + 120)

    def closed(_event: dict[str, Any]) -> None:
        raise RuntimeError("stream is closed")

    manager.event_callback = closed
    fence = manager._open_execution_fence()
    with caplog.at_level("WARNING", logger="fleet_rlm.sandbox.manager"):
        manager._report_fence(fence, "ns")  # must not raise into the host-call worker

    warnings = [record for record in caplog.records if "could not be delivered" in record.getMessage()]
    assert len(warnings) == 1
    assert "stream is closed" not in caplog.text


def test_a_live_execution_token_allows_a_successful_publication() -> None:

    class RecordingFs:
        def __init__(self) -> None:
            self.uploads: list[str] = []

        def upload_file(self, data: bytes, path: str, timeout: int | None = None) -> None:  # noqa: ARG002
            self.uploads.append(path)

    sandbox = SimpleNamespace(fs=RecordingFs())
    protocol = repl_module._Protocol(sandbox, lambda _call: {"value": 1}, fence=repl_module.ExecutionFence())

    protocol._answer({"id": "call-live"})

    assert sandbox.fs.uploads == ["/tmp/fleet/replies/call-live.json"]


def test_a_deadline_free_turn_arms_no_watchdog_and_owns_no_deadline() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-deadline-free", turn_timeout_seconds=0)
    manager.reset_turn_budget(deadline_seconds=None, deadline_source="request")
    try:
        assert manager._watchdog is None, "Timer(0) must be impossible"
        assert manager._deadline is None
        assert manager.turn_active is True, "a deadline-free Turn still owns itself"
        assert not manager._cancelled.is_set(), "a deadline-free Turn is not cancelled at start"
        assert manager.deadline_provenance() == {"deadline_source": "request", "effective_seconds": None}
        assert manager.remaining_turn_seconds() is None
    finally:
        manager.end_turn()
    assert manager.turn_active is False


def test_child_deadline_inherits_the_parents_absolute_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _ready_manager(deadline=time.monotonic() + 120)
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: None)
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: [])

    child = manager._allocate_child()
    grandchild = child._allocate_child()

    assert child._deadline == manager._deadline, "a child never receives a longer deadline"
    assert grandchild._deadline == manager._deadline

    manager._deadline = None
    deadline_free_child = manager._allocate_child()
    assert deadline_free_child._deadline is None


def test_a_stale_watchdog_cannot_cancel_a_later_turn() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-stale-watchdog")
    manager.reset_turn_budget(deadline_seconds=900, deadline_source="request")
    first_generation = manager._turn_generation
    first_watchdog = manager._watchdog
    assert first_watchdog is not None
    try:
        manager.reset_turn_budget(deadline_seconds=900, deadline_source="request")
        assert manager._turn_generation > first_generation
        # Fire the previous Turn's callback exactly as its timer thread would.
        first_watchdog.function()
        assert not manager._cancelled.is_set(), "a stale watchdog is a no-op for the current Turn"
    finally:
        manager.end_turn()


def test_watchdog_cancellation_cannot_cross_a_turn_reset(monkeypatch: pytest.MonkeyPatch) -> None:

    manager = DaytonaSandboxManager("key", "org", "volume", "session-racing-watchdog")
    manager.reset_turn_budget(deadline_seconds=900, deadline_source="request")
    generation = manager._turn_generation
    entered = threading.Event()
    release = threading.Event()
    original = DaytonaSandboxManager.request_cancellation

    def cancel(self: DaytonaSandboxManager) -> bool:
        entered.set()
        assert release.wait(5)
        return original(self)

    monkeypatch.setattr(DaytonaSandboxManager, "request_cancellation", cancel)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            watchdog = pool.submit(manager._expire_generation, generation)
            try:
                assert entered.wait(5)
                reset = pool.submit(manager.reset_turn_budget, deadline_seconds=None)
                with pytest.raises(TimeoutError):
                    reset.result(timeout=0.05)
            finally:
                release.set()
            watchdog.result(timeout=5)
            reset.result(timeout=5)
        assert manager._turn_generation > generation
        assert manager.turn_active
        assert not manager._cancelled.is_set(), "the watchdog belongs only to the preceding Turn"
    finally:
        manager.end_turn()


def test_a_watchdog_cannot_cancel_a_turn_that_already_ended() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-ended-watchdog")
    manager.reset_turn_budget(deadline_seconds=900, deadline_source="request")
    watchdog = manager._watchdog
    assert watchdog is not None
    manager.end_turn()

    # Timer.cancel() cannot revoke a callback that already started on its thread.
    watchdog.function()

    assert not manager._cancelled.is_set(), "a finished Turn must keep its warm root"
    assert manager.cleanup_outcome is None


@pytest.mark.parametrize(("remaining", "expected"), [(None, None), (120.9, 120), (1.5, 1)])
def test_host_call_bounds_truncate_whole_seconds(remaining: float | None, expected: int | None) -> None:
    from fleet_rlm.sandbox.manager import _host_call_bound

    assert _host_call_bound(remaining) == expected


@pytest.mark.parametrize("remaining", [0.4, 0.0, -2.0])
def test_a_sub_second_remainder_never_becomes_a_host_call_bound(remaining: float) -> None:
    from fleet_rlm.sandbox.manager import _host_call_bound

    # 0 means "no timeout" on the transport, so it cannot stand in; any whole positive second is
    # wider than the remainder. The helper must refuse both instead of widening the deadline.
    with pytest.raises(ValueError, match="one whole second"):
        _host_call_bound(remaining)


def test_a_sub_second_remainder_stops_the_turn_before_any_host_call(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _record_host_call_bound(monkeypatch)
    manager = _ready_manager(deadline=time.monotonic() + 0.4)

    with pytest.raises(TimeoutError, match="deadline exceeded"):
        manager.run_namespace(_ready_interpreter(), {"code": "1"})

    assert captured == {}, "no execution and therefore no host call may start"
    assert manager._cancelled.is_set(), "expiry marks the Turn cancelled, so it is stored as cancelled"
    with pytest.raises(RuntimeError, match="Turn cancelled"):
        manager.check_turn()


def test_a_whole_second_remainder_still_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _record_host_call_bound(monkeypatch)
    manager = _ready_manager(deadline=time.monotonic() + 120.9)

    manager.run_namespace(_ready_interpreter(), {"code": "1"})

    assert captured["timeout"] == 120, "whole seconds truncate; the bound never exceeds the remainder"


def test_a_threshold_straddling_clock_is_a_deadline_cancellation_not_a_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    # The two former reads: admission sees 1.5 s left and passes, the bound samples 0.4 s
    # later. A single sample must drive both, so the Turn is a deadline cancellation and
    # no execution, host call, ValueError, or transport uncertainty is produced.
    captured = _record_host_call_bound(monkeypatch)
    manager = _ready_manager(deadline=101.5)
    monkeypatch.setattr(manager_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(DaytonaSandboxManager, "remaining_turn_seconds", lambda _self: 0.4)

    with pytest.raises(TimeoutError, match="deadline exceeded"):
        manager.run_namespace(_ready_interpreter(), {"code": "1"})

    assert captured == {}, "a Turn without a representable bound starts no execution or host call"
    assert manager._cancelled.is_set(), "expiry marks the Turn cancelled, so it is stored as cancelled"
    with pytest.raises(RuntimeError, match="Turn cancelled"):
        manager.check_turn()


@pytest.mark.parametrize("sampled", [1.0, 1.5])
def test_one_remainder_sample_drives_both_admission_and_the_bound(
    monkeypatch: pytest.MonkeyPatch, sampled: float
) -> None:
    captured = _record_host_call_bound(monkeypatch)
    samples: list[float] = []

    def sample(_self: DaytonaSandboxManager) -> float:
        samples.append(sampled)
        return sampled

    monkeypatch.setattr(DaytonaSandboxManager, "remaining_turn_seconds", sample)
    manager = _ready_manager(deadline=time.monotonic() + 120)

    manager.run_namespace(_ready_interpreter(), {"code": "1"})

    assert samples == [sampled], "the remainder is sampled once per execution"
    assert captured["timeout"] == 1, "at least one second remains representable and truncates to one"


def _record_terminal_cancellations(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Count every cancel_turn() invocation without changing the teardown it performs."""
    invocations: list[str] = []
    original = DaytonaSandboxManager.cancel_turn

    def recording(self: DaytonaSandboxManager) -> None:
        invocations.append("cancel_turn")
        original(self)

    monkeypatch.setattr(DaytonaSandboxManager, "cancel_turn", recording)
    return invocations


def _cancellable_manager(*, turn_timeout_seconds: int = 900) -> tuple[DaytonaSandboxManager, list[str]]:
    manager = DaytonaSandboxManager("key", "org", "volume", "single-cancel", turn_timeout_seconds=turn_timeout_seconds)
    stops: list[str] = []
    manager._sandbox = SimpleNamespace(id="sandbox-single-cancel", state="started")
    manager._contexts = {"ns": object()}
    manager._client = SimpleNamespace(stop=lambda *_args, **_kwargs: stops.append("stop"))
    return manager, stops


def test_an_unknown_execution_outcome_issues_exactly_one_terminal_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    invocations = _record_terminal_cancellations(monkeypatch)
    manager, stops = _cancellable_manager()

    def unknown_outcome(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise repl_module.ExecutionOutcomeUnknownError("transport lost after execution started")

    monkeypatch.setattr(repl_module, "run", unknown_outcome)

    with pytest.raises(repl_module.ExecutionOutcomeUnknownError):
        manager.run_namespace(_ready_interpreter(), {"code": "1"})
    # The engine's terminal branch arrives after the transport path already claimed teardown.
    assert manager.request_cancellation() is False

    assert invocations == ["cancel_turn"], "an unknown outcome invokes cancel_turn exactly once"
    assert stops == ["stop"], "the provider teardown still runs once"
    assert manager.cleanup_outcome == "stopped"


def test_concurrent_terminal_cancellation_callers_cannot_both_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocations = _record_terminal_cancellations(monkeypatch)
    manager, _stops = _cancellable_manager()
    stop_entered = threading.Event()
    release_stop = threading.Event()
    stops: list[str] = []

    def slow_stop(*_args: Any, **_kwargs: Any) -> None:
        stop_entered.set()
        assert release_stop.wait(timeout=5)
        stops.append("stop")

    manager._client = SimpleNamespace(stop=slow_stop)
    outcomes: list[bool] = []

    def caller() -> None:
        outcomes.append(manager.request_cancellation())

    owner = threading.Thread(target=caller, name="cancel-owner")
    joiner = threading.Thread(target=caller, name="cancel-joiner")
    owner.start()
    assert stop_entered.wait(timeout=5)
    joiner.start()
    # The joiner must wait for the owner's provider stop rather than start a second teardown.
    assert invocations == ["cancel_turn"]
    release_stop.set()
    owner.join(timeout=5)
    joiner.join(timeout=5)

    assert not owner.is_alive() and not joiner.is_alive()
    assert sorted(outcomes) == [False, True], "exactly one caller owns the teardown"
    assert invocations == ["cancel_turn"]
    assert stops == ["stop"]


def test_a_late_cancellation_caller_after_acknowledgment_does_not_cancel_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocations = _record_terminal_cancellations(monkeypatch)
    manager, stops = _cancellable_manager()

    assert manager.request_cancellation() is True
    # A watchdog, disconnect, or terminal branch arriving after acknowledgment finds it taken.
    assert manager.request_cancellation() is False

    assert invocations == ["cancel_turn"]
    assert stops == ["stop"]
    assert manager.cleanup_outcome == "stopped"


def test_the_deadline_watchdog_claims_the_same_single_teardown(monkeypatch: pytest.MonkeyPatch) -> None:
    invocations = _record_terminal_cancellations(monkeypatch)
    manager, stops = _cancellable_manager()
    manager.reset_turn_budget()
    try:
        watchdog = manager._watchdog
        assert watchdog is not None
        # Fire the armed callback exactly as the timer thread would.
        watchdog.function()
        # The terminal Turn branch joining afterwards must not issue a second cancellation.
        assert manager.request_cancellation() is False
    finally:
        manager.end_turn()

    assert invocations == ["cancel_turn"]
    assert stops == ["stop"]
    assert manager.cleanup_outcome == "stopped"


def test_a_new_turn_owns_a_new_teardown_after_a_previous_acknowledgment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocations = _record_terminal_cancellations(monkeypatch)
    manager, stops = _cancellable_manager()
    manager.reset_turn_budget()
    manager.end_turn()

    assert manager.request_cancellation() is True
    assert manager.request_cancellation() is False

    manager.reset_turn_budget()
    # The previous teardown discarded its root, so a cold-resumed Turn hands over a new handle.
    manager._sandbox = SimpleNamespace(id="sandbox-next-turn", state="started")
    try:
        assert manager.cleanup_outcome is None
        assert manager.request_cancellation() is True
    finally:
        manager.end_turn()

    assert invocations == ["cancel_turn", "cancel_turn"], "each Turn owns exactly one invocation"
    assert stops == ["stop", "stop"]


def test_provider_bounds_are_reported_as_provider_owned_and_never_rewritten() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager._sandbox = SimpleNamespace(
        id="sandbox-1",
        ttl_minutes=1440,
        auto_stop_interval=60,
        maximum_lifespan_minutes=2880,
    )

    bounds = manager.provider_bounds()

    assert bounds == {
        "ttl_minutes": 1440,
        "auto_stop_interval": 60,
        "maximum_lifespan_minutes": 2880,
        "provider_owned": True,
    }


def test_provider_bounds_report_requested_ttl_and_an_unknown_lifespan_before_provisioning() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")

    assert manager.provider_bounds() == {
        "ttl_minutes": 120,
        "auto_stop_interval": 30,
        "maximum_lifespan_minutes": None,
        "provider_owned": True,
    }
    child = DaytonaSandboxManager("key", "org", "volume", "session-a", parent=manager)
    assert child.provider_bounds()["ttl_minutes"] == 30


def test_turn_scope_owns_an_unowned_turn_and_is_inert_inside_one() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-scope")

    with manager.turn_scope(deadline_seconds=5, deadline_source="request"):
        assert manager.turn_active
        assert manager.deadline_provenance()["effective_seconds"] == 5
    assert not manager.turn_active

    # A Turn an outer owner started stays that owner's: a nested scope neither resets nor ends it.
    manager.reset_turn_budget(deadline_seconds=None, deadline_source="request")
    try:
        with manager.turn_scope(deadline_seconds=5, deadline_source="request"):
            assert manager.turn_active
            assert manager.deadline_provenance()["effective_seconds"] is None
        assert manager.turn_active
    finally:
        manager.end_turn()
    assert not manager.turn_active
