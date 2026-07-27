"""Daytona mounted-agent seams for the root Workspace Memory file."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import threading
from contextlib import redirect_stdout, suppress
from io import StringIO
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from fleet_rlm.files.memory_models import WorkspaceMemoryStoreUnavailableError
from fleet_rlm.files.volume_paths import VolumePaths


class LocalProcess:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def code_run(self, code: str):
        self.calls.append(code)
        output = StringIO()
        with redirect_stdout(output), suppress(SystemExit):
            exec(code, {})
        return SimpleNamespace(exit_code=0, result=output.getvalue().strip())


class BoundedSubprocess:
    def __init__(self) -> None:
        self.timed_out = False

    def code_run(self, code: str):
        try:
            completed = subprocess.run(
                [sys.executable, "-c", code],
                check=False,
                capture_output=True,
                text=True,
                timeout=0.5,
            )
        except subprocess.TimeoutExpired:
            self.timed_out = True
            return SimpleNamespace(exit_code=1, result="timed out")
        return SimpleNamespace(exit_code=completed.returncode, result=completed.stdout.strip())


def _store(tmp_path: Path, *, max_bytes: int = 262_144):
    from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore

    root = tmp_path / "volume"
    root.mkdir()
    process = LocalProcess()
    store = DaytonaWorkspaceMemoryStore(
        SimpleNamespace(process=process),
        volume_paths=VolumePaths.from_mount(str(root)),
        max_upload_bytes=max_bytes,
    )
    return store, root, process


def test_binds_only_the_configured_root_memories_file(tmp_path: Path) -> None:
    store, root, process = _store(tmp_path, max_bytes=128)

    written = store.append_record("- [2026-07-27T11:14:05Z] **General**: hello\n")
    read = store.read_tail(byte_budget=128)

    assert written.entry_bytes == written.total_bytes
    assert read.content.endswith("**General**: hello\n")
    assert (root / "MEMORIES.md").is_file()
    assert all("relative = 'MEMORIES.md'" in code for code in process.calls)
    assert all(f"root = {str(root)!r}" in code for code in process.calls)


def test_rejects_any_noncanonical_memory_target(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore

    root = PurePosixPath(tmp_path / "volume")
    unsafe_paths = SimpleNamespace(root=root, memory_file=root / "other.md")

    with pytest.raises(ValueError, match="configured volume root"):
        DaytonaWorkspaceMemoryStore(
            SimpleNamespace(process=LocalProcess()),
            volume_paths=unsafe_paths,  # ty: ignore[invalid-argument-type]
            max_upload_bytes=128,
        )


def test_reads_utf8_tail_without_splitting_multibyte_or_memory_entries(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path, max_bytes=2_000)
    entries = [
        "- [2026-07-27T11:14:05Z] **General**: old old old 😀\n",
        "- [2026-07-27T11:14:06Z] **General**: current é\n",
        "- [2026-07-27T11:14:07Z] **General**: newest 😀\n",
    ]
    (root / "MEMORIES.md").write_text("".join(entries), encoding="utf-8")
    budget = len((entries[1] + entries[2]).encode("utf-8"))

    result = store.read_tail(byte_budget=budget)

    assert result.content == entries[1] + entries[2]
    assert result.truncated is True
    assert result.bytes_returned == budget
    assert result.total_bytes == len("".join(entries).encode("utf-8"))


def test_omits_an_unterminated_torn_final_memory_record(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path, max_bytes=2_000)
    complete = "- [2026-07-27T11:14:05Z] **General**: complete\n"
    torn = "- [2026-07-27T11:14:06Z] **General**: torn"
    (root / "MEMORIES.md").write_text(complete + torn, encoding="utf-8")

    result = store.read_tail(byte_budget=2_000)

    assert result.content == complete
    assert result.bytes_returned == len(complete.encode("utf-8"))
    assert result.total_bytes == len((complete + torn).encode("utf-8"))


def test_omits_a_torn_final_record_with_a_partial_multibyte_suffix(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path, max_bytes=2_000)
    complete = "- [2026-07-27T11:14:05Z] **General**: complete\n"
    torn_prefix = b"- [2026-07-27T11:14:06Z] **General**: torn "
    torn = torn_prefix + b"\xf0\x9f"
    (root / "MEMORIES.md").write_bytes(complete.encode("utf-8") + torn)

    result = store.read_tail(byte_budget=2_000)

    assert result.content == complete
    assert result.bytes_returned == len(complete.encode("utf-8"))
    assert result.total_bytes == len(complete.encode("utf-8")) + len(torn)


def test_rejects_an_append_after_a_torn_memory_record_without_rewriting(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path, max_bytes=2_000)
    torn = b"- [2026-07-27T11:14:05Z] **General**: incomplete"
    memory = root / "MEMORIES.md"
    memory.write_bytes(torn)

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.append_record("- [2026-07-27T11:14:06Z] **General**: later\n")

    assert memory.read_bytes() == torn


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="platform has no mkfifo")
def test_tail_read_rejects_a_fifo_before_opening_it(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore

    root = tmp_path / "volume"
    root.mkdir()
    os.mkfifo(root / "MEMORIES.md")
    process = BoundedSubprocess()
    store = DaytonaWorkspaceMemoryStore(
        SimpleNamespace(process=process),
        volume_paths=VolumePaths.from_mount(str(root)),
        max_upload_bytes=128,
    )

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.read_tail(byte_budget=128)

    assert process.timed_out is False


def test_tail_read_opens_target_nonblocking_before_descriptor_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, root, _process = _store(tmp_path, max_bytes=512)
    (root / "MEMORIES.md").write_text(
        "- [2026-07-27T11:14:05Z] **General**: original\n",
        encoding="utf-8",
    )
    original_open = os.open
    target_flags: int | None = None

    def tracking_open(path, flags, *args, **kwargs):
        nonlocal target_flags
        if path == "MEMORIES.md":
            target_flags = flags
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", tracking_open)

    store.read_tail(byte_budget=128)

    assert target_flags is not None
    assert target_flags & os.O_NONBLOCK


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="platform has no mkfifo")
def test_memory_append_rejects_a_fifo_before_opening_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, root, _process = _store(tmp_path)
    os.mkfifo(root / "MEMORIES.md")
    original_open = os.open
    opened_fifo = False

    def tracking_open(path, flags, *args, **kwargs):
        nonlocal opened_fifo
        if path == "MEMORIES.md":
            opened_fifo = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", tracking_open)

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.append_record("- [2026-07-27T11:14:05Z] **General**: hello\n")

    assert opened_fifo is False


@pytest.mark.parametrize("operation", ["tail_read", "memory_append"])
def test_memory_operations_revalidate_open_file_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    store, root, _process = _store(tmp_path, max_bytes=512)
    memory = root / "MEMORIES.md"
    memory.write_text("- [2026-07-27T11:14:05Z] **General**: original\n", encoding="utf-8")
    replacement = root / "replacement.md"
    replacement.write_text("- [2026-07-27T11:14:06Z] **General**: replacement\n", encoding="utf-8")
    original_open = os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "MEMORIES.md" and not swapped:
            swapped = True
            os.replace(replacement, memory)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        if operation == "tail_read":
            store.read_tail(byte_budget=128)
        else:
            store.append_record("- [2026-07-27T11:14:07Z] **General**: later\n")

    assert swapped is True
    assert memory.read_text(encoding="utf-8") == "- [2026-07-27T11:14:06Z] **General**: replacement\n"


def test_generic_session_workspace_append_keeps_nonnewline_memories_filename_behavior(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS

    volume_root = tmp_path / "volume"
    workspace_root = volume_root / "sessions" / "session" / "workspace"
    workspace_root.mkdir(parents=True)
    workspace = DaytonaSessionWorkspaceFS(
        SimpleNamespace(process=LocalProcess()),
        volume_root=str(volume_root),
        root=str(workspace_root),
        max_file_bytes=128,
    )
    memory_named_file = workspace_root / "MEMORIES.md"
    memory_named_file.write_bytes(b"generic torn")

    entry = workspace.append_text("MEMORIES.md", " continuation")

    assert entry.byte_size == len(b"generic torn continuation")
    assert memory_named_file.read_bytes() == b"generic torn continuation"


def test_generic_session_workspace_append_works_with_write_only_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS

    volume_root = tmp_path / "volume"
    workspace_root = volume_root / "sessions" / "session" / "workspace"
    workspace_root.mkdir(parents=True)
    workspace = DaytonaSessionWorkspaceFS(
        SimpleNamespace(process=LocalProcess()),
        volume_root=str(volume_root),
        root=str(workspace_root),
        max_file_bytes=128,
    )
    target = workspace_root / "write-only.txt"
    target.write_bytes(b"existing")
    original_open = os.open

    def write_only_open(path, flags, *args, **kwargs):
        if path == target.name and flags & os.O_ACCMODE != os.O_WRONLY:
            raise PermissionError("read access denied")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", write_only_open)

    entry = workspace.append_text(target.name, " appended")

    assert entry.byte_size == len(b"existing appended")
    assert target.read_bytes() == b"existing appended"


@pytest.mark.parametrize(
    "payload",
    [
        {"content": "ok", "truncated": 1, "bytes_returned": 2, "total_bytes": 2},
        {"content": "ok", "truncated": False, "bytes_returned": 3, "total_bytes": 3},
        {"content": "ok", "truncated": False, "bytes_returned": True, "total_bytes": 2},
        {"content": "ok", "truncated": False, "bytes_returned": 2, "total_bytes": 1},
        {
            "content": "",
            "truncated": True,
            "bytes_returned": 0,
            "total_bytes": 262_145,
        },
    ],
)
def test_rejects_malformed_remote_tail_responses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    store, _root, _process = _store(tmp_path)
    monkeypatch.setattr(store, "_run", lambda **_kwargs: payload)

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.read_tail(byte_budget=2)


def test_rejects_a_real_memory_file_over_the_configured_cap(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path, max_bytes=64)
    memory = root / "MEMORIES.md"
    memory.write_text(
        "- [2026-07-27T11:14:05Z] **General**: first\n- [2026-07-27T11:14:06Z] **General**: second\n",
        encoding="utf-8",
    )

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.read_tail(byte_budget=64)


@pytest.mark.parametrize(
    "content",
    [
        "- [2026-07-27T11:14:05Z] **General**: valid\nnot a memory record\n",
        f"- [2026-07-27T11:14:05Z] **General**: {'x' * 4_096}\n",
    ],
    ids=["newline-terminated-malformed-record", "oversized-record"],
)
def test_rejects_malformed_complete_records(tmp_path: Path, content: str) -> None:
    encoded = content.encode("utf-8")
    store, root, _process = _store(tmp_path, max_bytes=len(encoded) + 1)
    (root / "MEMORIES.md").write_bytes(encoded)

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.read_tail(byte_budget=min(len(encoded), 262_144))


def test_rejects_append_to_a_newline_terminated_malformed_log(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path, max_bytes=2_000)
    memory = root / "MEMORIES.md"
    malformed = b"not a memory record\n"
    memory.write_bytes(malformed)

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.append_record("- [2026-07-27T11:14:06Z] **General**: later\n")

    assert memory.read_bytes() == malformed


def test_rejects_remote_append_response_over_the_configured_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _root, _process = _store(tmp_path, max_bytes=8)
    monkeypatch.setattr(store, "_run", lambda **_kwargs: {"entry": {"byte_size": 9}})

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.append_record("entry\n")


def test_process_local_lock_serializes_store_instances_and_preserves_the_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.daytona import workspace_memory
    from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore
    from fleet_rlm.files.memory_models import WorkspaceMemoryStoreFullError

    root = tmp_path / "volume"
    root.mkdir()
    paths = VolumePaths.from_mount(str(root))
    record = "- [2026-07-27T11:14:05Z] **G**: one\n"
    cap = len(record.encode("utf-8")) + 1
    stores = [
        DaytonaWorkspaceMemoryStore(SimpleNamespace(), volume_paths=paths, max_upload_bytes=cap),
        DaytonaWorkspaceMemoryStore(SimpleNamespace(), volume_paths=paths, max_upload_bytes=cap),
    ]
    barrier = threading.Barrier(2)
    stored = bytearray()

    def racing_agent(_sandbox, **arguments):
        payload = base64.b64decode(arguments["content_b64"])
        observed_size = len(stored)
        with suppress(threading.BrokenBarrierError):
            barrier.wait(timeout=0.1)
        if observed_size + len(payload) > cap:
            raise ValueError("workspace file exceeds maximum size")
        stored.extend(payload)
        return {"entry": {"byte_size": len(stored)}}

    monkeypatch.setattr(workspace_memory, "run_workspace_agent", racing_agent)
    outcomes: list[object] = []

    def append(store) -> None:
        try:
            outcomes.append(store.append_record(record))
        except Exception as exc:  # assertions below keep concurrent failures visible
            outcomes.append(exc)

    threads = [threading.Thread(target=append, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, WorkspaceMemoryStoreFullError) for outcome in outcomes) == 1
    assert bytes(stored) == record.encode("utf-8")


def test_missing_memory_is_empty_and_append_enforces_total_upload_cap(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path, max_bytes=60)

    assert store.read_tail(byte_budget=20).content == ""
    store.append_record("- [2026-07-27T11:14:05Z] **General**: first\n")

    from fleet_rlm.files.memory_models import WorkspaceMemoryStoreFullError

    with pytest.raises(WorkspaceMemoryStoreFullError):
        store.append_record("- [2026-07-27T11:14:06Z] **General**: second\n")
    assert (root / "MEMORIES.md").read_text(encoding="utf-8").endswith("first\n")


@pytest.mark.parametrize("kind", ["symlink", "directory", "invalid_utf8"])
def test_closed_unavailable_mapping_for_unsafe_or_invalid_memory_file(tmp_path: Path, kind: str) -> None:
    store, root, _process = _store(tmp_path)
    memory = root / "MEMORIES.md"
    if kind == "symlink":
        target = root / "private.txt"
        target.write_text("private", encoding="utf-8")
        memory.symlink_to(target)
    elif kind == "directory":
        memory.mkdir()
    else:
        memory.write_bytes(b"valid\xffinvalid\n")

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.read_tail(byte_budget=128)


def test_provider_failure_is_closed_and_does_not_leak_raw_error(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore

    class FailingProcess:
        def code_run(self, _code: str):
            return SimpleNamespace(exit_code=1, result="provider token leaked")

    root = tmp_path / "volume"
    root.mkdir()
    store = DaytonaWorkspaceMemoryStore(
        SimpleNamespace(process=FailingProcess()),
        volume_paths=VolumePaths.from_mount(str(root)),
        max_upload_bytes=128,
    )

    with pytest.raises(WorkspaceMemoryStoreUnavailableError) as error:
        store.read_tail(byte_budget=128)

    assert "provider token" not in str(error.value)
