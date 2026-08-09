"""Daytona mounted-agent seams for the canonical memory/MEMORIES.md store."""

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

from fleet_rlm.files.memory_models import (
    WORKSPACE_MEMORY_BYTE_BUDGET,
    WORKSPACE_MEMORY_HEADER,
    WorkspaceMemoryEntryNotFoundError,
    WorkspaceMemoryRecordError,
    WorkspaceMemoryStoreUnavailableError,
    workspace_memory_record_id,
)
from fleet_rlm.files.volume_paths import VolumePaths

HEADER = WORKSPACE_MEMORY_HEADER + "\n"


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


def _write_store_file(content: bytes, root: Path) -> Path:
    memory_dir = root / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    target = memory_dir / "MEMORIES.md"
    target.write_bytes(content)
    return target


def test_binds_only_the_canonical_memory_subdir_file(tmp_path: Path) -> None:
    store, root, process = _store(tmp_path, max_bytes=128)

    written = store.append_record("- [2026-07-27T11:14:05Z] **General**: hello\n")
    read = store.read_tail(byte_budget=128)

    assert written.entry_bytes + len(HEADER.encode("utf-8")) == written.total_bytes
    assert read.content.endswith(": hello\n")
    target = root / "memory" / "MEMORIES.md"
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == HEADER + "- [2026-07-27T11:14:05Z] **General**: hello\n"
    assert all("relative = 'MEMORIES.md'" in code for code in process.calls)
    # the migration probe reads at the volume root; durable ops stay under memory/
    assert any(f"root = {str(root)!r}" in code for code in process.calls)
    assert any(f"root = {str(root / 'memory')!r}" in code for code in process.calls)


def test_fresh_store_starts_with_the_v2_header(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path)

    store.append_record("- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 -->: first\n")

    assert (root / "memory" / "MEMORIES.md").read_text(encoding="utf-8").startswith(HEADER)


def test_rejects_any_noncanonical_memory_target(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore

    root = PurePosixPath(tmp_path / "volume")
    unsafe_paths = SimpleNamespace(root=root, memory_dir=root / "memory", memory_file=root / "other.md")

    with pytest.raises(ValueError, match="configured volume root"):
        DaytonaWorkspaceMemoryStore(
            SimpleNamespace(process=LocalProcess()),
            volume_paths=unsafe_paths,  # ty: ignore[invalid-argument-type]
            max_upload_bytes=128,
        )


def test_migrates_a_legacy_root_memories_file_once(tmp_path: Path) -> None:
    root = tmp_path / "volume"
    root.mkdir()
    legacy = root / "MEMORIES.md"
    legacy.write_bytes(b"- [2026-07-27T11:14:05Z] **General**: legacy\n- [torn final")
    from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore

    store = DaytonaWorkspaceMemoryStore(
        SimpleNamespace(process=LocalProcess()),
        volume_paths=VolumePaths.from_mount(str(root)),
        max_upload_bytes=262_144,
    )

    read = store.read_tail(byte_budget=512)

    # the torn suffix was newline-terminated on import and tolerated on read
    assert read.content == "- [2026-07-27T11:14:05Z] **General**: legacy\n"
    assert read.warnings == 1
    assert not legacy.exists()
    target = root / "memory" / "MEMORIES.md"
    assert target.read_text(encoding="utf-8") == (
        HEADER + "- [2026-07-27T11:14:05Z] **General**: legacy\n- [torn final\n"
    )

    # already migrated: a second read performs no further moves
    store.read_tail(byte_budget=512)
    assert target.exists() and not legacy.exists()


def test_migration_leaves_an_existing_new_store_and_legacy_file_untouched(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path)
    legacy = root / "MEMORIES.md"
    legacy.write_text("- [2026-07-27T11:14:05Z] **General**: legacy\n", encoding="utf-8")
    _write_store_file((HEADER + "- [2026-07-27T11:14:06Z] **General**: current\n").encode("utf-8"), root)

    read = store.read_tail(byte_budget=512)

    # both files exist: never migrate over a canonical store, never lose content
    assert read.content.endswith(": current\n") and "legacy" not in read.content
    assert legacy.read_text(encoding="utf-8").endswith(": legacy\n")


def test_migrates_a_zero_byte_legacy_file(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path)
    (root / "MEMORIES.md").write_bytes(b"")

    read = store.read_tail(byte_budget=512)

    assert read.content == ""
    assert not (root / "MEMORIES.md").exists()
    assert (root / "memory" / "MEMORIES.md").read_text(encoding="utf-8") == HEADER


def test_migrates_a_legacy_file_larger_than_the_read_projection_budget(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path, max_bytes=WORKSPACE_MEMORY_BYTE_BUDGET + 32_768)
    learning = "x" * 3_800
    legacy_body = "".join(f"- [2026-07-27T11:14:{second:02d}Z] **General**: {learning}\n" for second in range(70))
    legacy = root / "MEMORIES.md"
    legacy.write_text(legacy_body, encoding="utf-8")

    result = store.read_tail(byte_budget=512)

    assert result.content == ""
    assert not legacy.exists()
    assert (root / "memory" / "MEMORIES.md").read_bytes() == (HEADER + legacy_body).encode("utf-8")


def test_reads_utf8_tail_without_splitting_multibyte_or_memory_entries(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path, max_bytes=2_000)
    entries = [
        "- [2026-07-27T11:14:05Z] **General**: old old old 😀\n",
        "- [2026-07-27T11:14:06Z] **General**: current é\n",
        "- [2026-07-27T11:14:07Z] **General**: newest 😀\n",
    ]
    _write_store_file((HEADER + "".join(entries)).encode("utf-8"), root)
    budget = len((entries[1] + entries[2]).encode("utf-8"))

    result = store.read_tail(byte_budget=budget)

    assert result.content == entries[1] + entries[2]
    assert result.truncated is True
    assert result.bytes_returned == budget
    assert result.total_bytes == len((HEADER + "".join(entries)).encode("utf-8"))


def test_omits_an_unterminated_torn_final_memory_record(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path, max_bytes=2_000)
    complete = "- [2026-07-27T11:14:05Z] **General**: complete\n"
    torn = "- [2026-07-27T11:14:06Z] **General**: torn"
    _write_store_file((HEADER + complete).encode("utf-8") + torn.encode("utf-8"), root)

    result = store.read_tail(byte_budget=2_000)

    assert result.content == complete
    assert result.bytes_returned == len(complete.encode("utf-8"))
    assert result.total_bytes == len((HEADER + complete).encode("utf-8")) + len(torn)


def test_omits_a_torn_final_record_with_a_partial_multibyte_suffix(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path, max_bytes=2_000)
    complete = "- [2026-07-27T11:14:05Z] **General**: complete\n"
    torn_prefix = b"- [2026-07-27T11:14:06Z] **General**: torn "
    torn = torn_prefix + b"\xf0\x9f"
    _write_store_file((HEADER + complete).encode("utf-8") + torn, root)

    result = store.read_tail(byte_budget=2_000)

    assert result.content == complete
    assert result.bytes_returned == len(complete.encode("utf-8"))
    assert result.total_bytes == len((HEADER + complete).encode("utf-8")) + len(torn)


def test_rejects_an_append_after_a_torn_memory_record_without_rewriting(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path, max_bytes=2_000)
    torn = b"- [2026-07-27T11:14:05Z] **General**: incomplete"
    memory = _write_store_file(HEADER.encode("utf-8") + torn, root)

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.append_record("- [2026-07-27T11:14:06Z] **General**: later\n")

    assert memory.read_bytes() == HEADER.encode("utf-8") + torn


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="platform has no mkfifo")
def test_tail_read_rejects_a_fifo_before_opening_it(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore

    root = tmp_path / "volume"
    (root / "memory").mkdir(parents=True)
    os.mkfifo(root / "memory" / "MEMORIES.md")
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
    _write_store_file(
        (HEADER + "- [2026-07-27T11:14:05Z] **General**: original\n").encode("utf-8"),
        root,
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
def test_legacy_memory_migration_rejects_a_fifo_before_opening_it(tmp_path: Path) -> None:
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


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="platform has no mkfifo")
def test_memory_append_rejects_a_fifo_before_opening_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, root, _process = _store(tmp_path)
    (root / "memory").mkdir(parents=True, exist_ok=True)
    os.mkfifo(root / "memory" / "MEMORIES.md")
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


@pytest.mark.parametrize("operation", ["tail_read", "memory_append", "memory_edit", "memory_delete"])
def test_memory_operations_revalidate_open_file_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    store, root, _process = _store(tmp_path, max_bytes=512)
    memory = _write_store_file(
        (HEADER + "- [2026-07-27T11:14:05Z] **General**: original\n").encode("utf-8"),
        root,
    )
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
        elif operation == "memory_append":
            store.append_record("- [2026-07-27T11:14:07Z] **General**: later\n")
        elif operation == "memory_edit":
            store.edit_entry("aaaa0001", "later")
        else:
            store.delete_entry("aaaa0001")

    assert swapped is True
    assert memory.read_text(encoding="utf-8") == "- [2026-07-27T11:14:06Z] **General**: replacement\n"
    os.replace(memory, replacement)  # restore for a clean teardown on any platform
    os.replace(replacement, memory)


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
    _write_store_file(
        (
            HEADER + "- [2026-07-27T11:14:05Z] **General**: first\n" + "- [2026-07-27T11:14:06Z] **General**: second\n"
        ).encode("utf-8"),
        root,
    )

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.read_tail(byte_budget=64)


def test_tolerantly_skips_malformed_records_with_a_bounded_warning_count(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path)
    oversized = "- [2026-07-27T11:14:05Z] **General**: " + "x" * 4_096 + "\n"
    _write_store_file(
        (HEADER + "- [2026-07-27T11:14:04Z] **General**: valid\n" + "not a memory record\n" + oversized).encode(
            "utf-8"
        ),
        root,
    )

    result = store.read_tail(byte_budget=262_144)

    assert result.content == "- [2026-07-27T11:14:04Z] **General**: valid\n"
    assert result.warnings == 2  # malformed + oversized, human edits never poison the read


def test_appends_survive_a_human_malformed_line_and_preserve_it(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path, max_bytes=2_000)
    memory = _write_store_file((HEADER + "not a memory record\n").encode("utf-8"), root)

    store.append_record("- [2026-07-27T11:14:06Z] **General** <!-- id:ffff9999 -->: later\n")

    content = memory.read_text(encoding="utf-8")
    assert content.startswith(HEADER + "not a memory record\n")
    assert content.endswith(": later\n")


def test_rejects_remote_append_response_over_the_configured_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = "- [2026-07-27T11:14:05Z] **General**: first\n"
    cap = len(record.encode("utf-8")) + len(HEADER.encode("utf-8")) + 8
    store, _root, _process = _store(tmp_path, max_bytes=cap)
    monkeypatch.setattr(store, "_run", lambda **_kwargs: {"entry": {"byte_size": cap + 1}})

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.append_record(record)


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
    record_bytes = len(record.encode("utf-8"))
    cap = record_bytes + len(HEADER.encode("utf-8"))
    stores = [
        DaytonaWorkspaceMemoryStore(SimpleNamespace(), volume_paths=paths, max_upload_bytes=cap),
        DaytonaWorkspaceMemoryStore(SimpleNamespace(), volume_paths=paths, max_upload_bytes=cap),
    ]
    barrier = threading.Barrier(2)
    stored = bytearray()

    def racing_agent(_sandbox, **arguments):
        if arguments["operation"] != "memory_append":
            return {"entry": None}
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
    record = "- [2026-07-27T11:14:05Z] **General**: first\n"
    cap = len(record.encode("utf-8")) + len(HEADER.encode("utf-8"))
    store, root, _process = _store(tmp_path, max_bytes=cap)

    assert store.read_tail(byte_budget=20).content == ""
    store.append_record(record)

    from fleet_rlm.files.memory_models import WorkspaceMemoryStoreFullError

    with pytest.raises(WorkspaceMemoryStoreFullError):
        store.append_record("- [2026-07-27T11:14:06Z] **General**: second\n")
    assert (root / "memory" / "MEMORIES.md").read_text(encoding="utf-8").endswith("first\n")


@pytest.mark.parametrize("kind", ["symlink", "directory", "invalid_utf8"])
def test_closed_unavailable_mapping_for_unsafe_or_invalid_memory_file(tmp_path: Path, kind: str) -> None:
    store, root, _process = _store(tmp_path)
    (root / "memory").mkdir(parents=True, exist_ok=True)
    memory = root / "memory" / "MEMORIES.md"
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


# -- memory lifecycle: list/edit/delete ----------------------------------------

R1 = "- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 -->: one\n"
R2 = "- [2026-07-27T11:14:06Z] **Preference** <!-- id:bbbb0002 -->: two two\n"
R3 = "- [2026-07-27T11:14:07Z] **General** <!-- id:cccc0003 -->: three\n"


def _lifecycle_store(tmp_path: Path, body: str):
    store, root, _process = _store(tmp_path)
    _write_store_file((HEADER + body).encode("utf-8"), root)
    return store, root


def test_list_entries_pages_with_a_memory_id_cursor_and_filters_category(tmp_path: Path) -> None:
    store, _root = _lifecycle_store(tmp_path, R1 + R2 + R3)

    page = store.list_entries(limit=2)
    assert [entry.learning for entry in page.entries] == ["one", "two two"]
    assert page.truncated is True
    assert page.next_cursor == "bbbb0002"
    assert page.warnings == 0

    rest = store.list_entries(after=page.next_cursor, limit=2)
    assert [entry.learning for entry in rest.entries] == ["three"]
    assert rest.truncated is False
    assert rest.next_cursor is None

    general = store.list_entries(limit=10, category="General")
    assert [entry.learning for entry in general.entries] == ["one", "three"]

    with pytest.raises(WorkspaceMemoryEntryNotFoundError):
        store.list_entries(after="dddd0004", limit=2)


def test_list_entries_is_tolerant_and_never_shows_malformed_lines(tmp_path: Path) -> None:
    store, _root = _lifecycle_store(tmp_path, R1 + "human note\n" + R2)

    page = store.list_entries(limit=10)

    assert [entry.memory_id for entry in page.entries] == ["aaaa0001", "bbbb0002"]
    assert page.warnings == 1


def test_torn_final_record_is_skipped_by_list_and_other_lifecycle_ops_work(tmp_path: Path) -> None:
    torn = "- [2026-07-27T11:14:08Z] **General**: torn final"
    store, root = _lifecycle_store(tmp_path, R1 + R2 + torn)

    listed = store.list_entries(limit=10)
    assert [entry.memory_id for entry in listed.entries] == ["aaaa0001", "bbbb0002"]
    assert listed.warnings == 0

    edited = store.edit_entry("aaaa0001", "revised")
    assert "revised" in edited
    assert store.delete_entry("bbbb0002") is True
    content = (root / "memory" / "MEMORIES.md").read_text(encoding="utf-8")
    assert "revised" in content
    assert R2 not in content
    assert content.endswith(torn)


def test_duplicate_v2_records_are_idempotent(tmp_path: Path) -> None:
    store, root, _process = _store(tmp_path)

    store.append_record(R1)
    store.append_record(R1)

    assert [entry.memory_id for entry in store.list_entries(limit=10).entries] == ["aaaa0001"]
    assert (root / "memory" / "MEMORIES.md").read_text(encoding="utf-8") == HEADER + R1


def test_ambiguous_memory_ids_fail_closed_without_rewriting(tmp_path: Path) -> None:
    duplicate = "- [2026-07-27T11:14:08Z] **General** <!-- id:aaaa0001 -->: dup\n"
    body = R1 + "human note\n" + duplicate + R2
    store, root = _lifecycle_store(tmp_path, body)
    target = root / "memory" / "MEMORIES.md"
    before = target.read_bytes()

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.list_entries(limit=10)
    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.edit_entry("aaaa0001", "revised")
    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.delete_entry("aaaa0001")

    assert target.read_bytes() == before


def test_edit_entry_replaces_one_line_preserving_id_and_timestamp(tmp_path: Path) -> None:
    store, root = _lifecycle_store(tmp_path, R1 + "human note\n" + R2 + R3)

    record = store.edit_entry("bbbb0002", "  two   revised ", category="Ops")

    assert record == "- [2026-07-27T11:14:06Z] **Ops** <!-- id:bbbb0002 -->: two revised\n"
    target = root / "memory" / "MEMORIES.md"
    assert target.read_text(encoding="utf-8") == HEADER + R1 + "human note\n" + record + R3

    # category is optional; identity stays stable across edits
    record = store.edit_entry("bbbb0002", "two final")
    assert record == "- [2026-07-27T11:14:06Z] **Ops** <!-- id:bbbb0002 -->: two final\n"

    with pytest.raises(WorkspaceMemoryRecordError):
        store.edit_entry("bbbb0002", " ")
    with pytest.raises(WorkspaceMemoryRecordError):
        store.edit_entry("bbbb0002", "x" * 4_096)


def test_v1_rows_are_pageable_and_upgrade_to_v2_when_edited(tmp_path: Path) -> None:
    v1 = "- [2026-07-27T11:14:09Z] **General**: legacy row\n"
    legacy_id = workspace_memory_record_id("2026-07-27T11:14:09Z", "General", "legacy row")
    store, root = _lifecycle_store(tmp_path, v1 + R1)

    page = store.list_entries(limit=1)
    assert page.entries[0].memory_id == legacy_id
    assert page.truncated is True and page.next_cursor == legacy_id
    assert [entry.memory_id for entry in store.list_entries(after=legacy_id, limit=1).entries] == ["aaaa0001"]

    updated = store.edit_entry(legacy_id, "legacy revised")
    assert updated == f"- [2026-07-27T11:14:09Z] **General** <!-- id:{legacy_id} -->: legacy revised\n"
    assert store.delete_entry(legacy_id) is True
    assert (root / "memory" / "MEMORIES.md").read_text(encoding="utf-8") == HEADER + R1


def test_edits_and_deletes_use_one_workspace_agent_round_trip(tmp_path: Path) -> None:
    store, _root, process = _store(tmp_path)
    store.append_record(R1)
    process.calls.clear()

    store.edit_entry("aaaa0001", "revised")
    assert len(process.calls) == 1
    assert "operation = 'memory_edit'" in process.calls[0]

    process.calls.clear()
    assert store.delete_entry("aaaa0001") is True
    assert len(process.calls) == 1
    assert "operation = 'memory_delete'" in process.calls[0]


def test_edit_and_delete_under_a_missing_store_are_empty_not_found(tmp_path: Path) -> None:
    store, _root, _process = _store(tmp_path)

    assert store.delete_entry("aaaa0001") is False
    with pytest.raises(WorkspaceMemoryEntryNotFoundError):
        store.edit_entry("aaaa0001", "nothing")
    assert store.list_entries(limit=10).entries == ()


# -- per-turn injection digest -------------------------------------------


def test_injection_digest_is_bounded_tolerant_and_cached_30_seconds(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_memory import read_workspace_memory_injection_digest

    store, root, process = _store(tmp_path)
    _write_store_file((HEADER + R1 + "human note\n" + R2).encode("utf-8"), root)
    now = [1_000.0]

    digest = read_workspace_memory_injection_digest(store, clock=lambda: now[0])
    calls_after_first = len(process.calls)
    assert calls_after_first > 0
    assert "one" in digest and "two two" in digest
    assert "human note" not in digest  # tolerant parse drops malformed lines
    assert HEADER not in digest
    assert len(digest.encode("utf-8")) <= 4_096

    # within the 30 s TTL no further sandbox round trip happens
    again = read_workspace_memory_injection_digest(store, clock=lambda: now[0] + 29.9)
    assert again == digest
    assert len(process.calls) == calls_after_first

    # an unchanged tail fingerprint after TTL expiry re-reads without reprocessing
    third = read_workspace_memory_injection_digest(store, clock=lambda: now[0] + 31)
    assert third == digest
    assert len(process.calls) == calls_after_first + 1

    # a mutation invalidates the cache eagerly even inside the TTL window
    store.append_record(R3)
    calls_after_append = len(process.calls)
    fresh = read_workspace_memory_injection_digest(store, clock=lambda: now[0] + 31.1)
    assert len(process.calls) > calls_after_append
    assert "three" in fresh


def test_injection_digest_is_empty_for_missing_or_empty_stores(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_memory import read_workspace_memory_injection_digest

    store, _root, _process = _store(tmp_path)

    assert read_workspace_memory_injection_digest(store) == ""
