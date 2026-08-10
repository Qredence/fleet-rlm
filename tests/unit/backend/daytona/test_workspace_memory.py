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
    parse_workspace_memory_lines,
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

    edited = parse_workspace_memory_lines(record)[0].entry
    assert edited is not None
    assert edited.memory_id == "bbbb0002"
    assert edited.timestamp == "2026-07-27T11:14:06Z"
    assert edited.category == "Ops"
    assert edited.learning == "two revised"
    assert edited.source == "legacy_unknown"
    assert edited.record_version == 3
    assert str(edited.updated_at) >= edited.timestamp
    target = root / "memory" / "MEMORIES.md"
    assert target.read_text(encoding="utf-8") == HEADER + R1 + "human note\n" + record + R3

    # category is optional; identity and provenance stay stable across edits
    record = store.edit_entry("bbbb0002", "two final")
    edited = parse_workspace_memory_lines(record)[0].entry
    assert edited is not None
    assert edited.memory_id == "bbbb0002"
    assert edited.timestamp == "2026-07-27T11:14:06Z"
    assert edited.category == "Ops"
    assert edited.learning == "two final"
    assert edited.source == "legacy_unknown"
    assert edited.record_version == 3

    with pytest.raises(WorkspaceMemoryRecordError):
        store.edit_entry("bbbb0002", " ")
    with pytest.raises(WorkspaceMemoryRecordError):
        store.edit_entry("bbbb0002", "x" * 4_096)


def test_v1_rows_are_pageable_and_upgrade_to_v3_when_edited(tmp_path: Path) -> None:
    v1 = "- [2026-07-27T11:14:09Z] **General**: legacy row\n"
    legacy_id = workspace_memory_record_id("2026-07-27T11:14:09Z", "General", "legacy row")
    store, root = _lifecycle_store(tmp_path, v1 + R1)

    page = store.list_entries(limit=1)
    assert page.entries[0].memory_id == legacy_id
    assert page.truncated is True and page.next_cursor == legacy_id
    assert [entry.memory_id for entry in store.list_entries(after=legacy_id, limit=1).entries] == ["aaaa0001"]

    updated = store.edit_entry(legacy_id, "legacy revised")
    edited = parse_workspace_memory_lines(updated)[0].entry
    assert edited is not None
    assert edited.memory_id == legacy_id
    assert edited.timestamp == "2026-07-27T11:14:09Z"
    assert edited.source == "legacy_unknown"
    assert edited.record_version == 3
    assert updated.endswith(" -->: legacy revised\n")
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


def test_injection_digest_is_bounded_tolerant_deterministic_and_query_sensitive(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_memory import read_workspace_memory_injection_digest

    store, root, process = _store(tmp_path)
    _write_store_file((HEADER + R1 + "human note\n" + R2).encode("utf-8"), root)

    digest = read_workspace_memory_injection_digest(store, request="first two")
    calls_after_first = len(process.calls)
    assert calls_after_first > 0
    assert "one" in digest and "two two" in digest
    assert "human note" not in digest  # tolerant parse drops malformed lines
    assert HEADER not in digest
    assert len(digest.encode("utf-8")) <= 4_096

    again = read_workspace_memory_injection_digest(store, request="first two")
    assert again == digest
    assert len(process.calls) > calls_after_first  # no query-stale root digest cache

    store.append_record(R3)
    fresh = read_workspace_memory_injection_digest(store, request="three")
    assert "three" in fresh


def test_relevant_old_memory_is_injected_with_recent_context_under_the_budget(tmp_path: Path) -> None:
    """A preferred older record survives outside the newest 4 KiB tail."""
    from fleet_rlm.daytona.workspace_memory import read_workspace_memory_injection_digest
    from fleet_rlm.files.memory_models import workspace_memory_record_id

    old_ts = "2026-07-20T10:00:00Z"
    old_category = "Preference"
    old_learning = "Prefers polars for large dataframe joins."
    old_id = workspace_memory_record_id(old_ts, old_category, old_learning)
    old_record = f"- [{old_ts}] **{old_category}** <!-- id:{old_id} -->: {old_learning}\n"
    recent_records = []
    for index in range(80):
        ts = f"2026-07-27T12:{index // 60:02d}:{index % 60:02d}Z"
        learning = f"Routine unassociated workspace note {index:03d}."
        rid = workspace_memory_record_id(ts, "Ops", learning)
        recent_records.append(f"- [{ts}] **Ops** <!-- id:{rid} -->: {learning}\n")
    store, root, process = _store(tmp_path)
    _write_store_file((HEADER + old_record + "".join(recent_records)).encode("utf-8"), root)
    recent_fallback = store.read_tail(byte_budget=4_096).content

    matching = read_workspace_memory_injection_digest(store, request="Which dataframe engine should we use for joins?")
    unrelated = read_workspace_memory_injection_digest(store, request="Summarize the deployment log.")
    calls_before_repeat = len(process.calls)
    repeated = read_workspace_memory_injection_digest(store, request="Which dataframe engine should we use for joins?")

    assert len(process.calls) - calls_before_repeat == 2
    assert old_record in matching
    assert old_record not in recent_fallback
    assert old_record not in unrelated
    assert recent_records[-1] in matching
    assert repeated == matching
    assert len(matching.encode("utf-8")) <= 4_096
    assert old_id in matching
    assert process.calls


def test_relevance_injection_search_list_edit_forget_agree_on_valid_records(tmp_path: Path) -> None:
    """P13 deterministic corpus: old relevant preference plus noise and malformed rows."""
    from fleet_rlm.daytona.workspace_memory import read_workspace_memory_injection_digest
    from fleet_rlm.files.memory_models import WORKSPACE_MEMORY_MAX_LIST_LIMIT, workspace_memory_record_id
    from fleet_rlm.files.memory_tools import WorkspaceMemoryToolHost

    old_ts = "2026-07-19T09:00:00Z"
    older = "Prefers polars for dataframe joins and concise reports."
    old_id = workspace_memory_record_id(old_ts, "Preference", older)
    old_record = f"- [{old_ts}] **Preference** <!-- id:{old_id} -->: {older}\n"
    records = [old_record]
    for index in range(48):
        ts = f"2026-07-26T14:{index // 60:02d}:{index % 60:02d}Z"
        learning = f"Unrelated deployment and UI note {index:03d}."
        rid = workspace_memory_record_id(ts, "Ops", learning)
        records.append(f"- [{ts}] **Ops** <!-- id:{rid} -->: {learning}\n")
    body = HEADER + "".join(records) + "garbage human edit\n"
    store, root, _process = _store(tmp_path)
    _write_store_file(body.encode("utf-8"), root)
    host = WorkspaceMemoryToolHost(store)
    tools = {str(tool.name): tool for tool in host.as_tools()}

    search = tools["search_memories"](query="polars dataframe joins", category="Preference", limit=2)
    assert [entry["id"] for entry in search["entries"]] == [old_id]
    recent_fallback = store.read_tail(byte_budget=4_096)
    assert older not in recent_fallback.content
    inject = read_workspace_memory_injection_digest(store, request="How should the dataframe join be written?")
    assert old_record in inject
    assert "garbage human edit" not in inject
    assert records[-1] in inject

    listed = store.list_entries(limit=WORKSPACE_MEMORY_MAX_LIST_LIMIT)
    assert len(listed.entries) == len(records)
    assert listed.warnings == 1
    edited = tools["edit_memory"](
        memory_id=old_id, key_learning="Prefers polars for dataframe joins and shorter reports."
    )
    assert edited["ok"] is True
    mutated = read_workspace_memory_injection_digest(store, request="polars dataframe joins")
    assert "shorter reports." in mutated
    forgotten = tools["forget"](memory_id=old_id)
    assert forgotten["removed"] is True
    assert old_id not in read_workspace_memory_injection_digest(store, request="polars dataframe joins")


def test_append_writes_v3_over_historical_v1_and_v2_without_rewriting_old_rows(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from fleet_rlm.files.memory_models import format_workspace_memory_record

    store, root, _process = _store(tmp_path)  # type: ignore[name-defined]
    v1 = "- [2026-07-20T09:00:00Z] **General**: old operator note\n"
    _write_store_file((HEADER + v1 + R1).encode("utf-8"), root)
    record, category = format_workspace_memory_record(
        "New explicit preference", "Preference", timestamp=datetime.now(UTC)
    )

    result = store.append_record(record)
    body = (root / "memory" / "MEMORIES.md").read_text(encoding="utf-8")

    assert category == "Preference"
    assert result.entry_bytes == len(record.encode("utf-8"))
    assert body == HEADER + v1 + R1 + record
    parsed = parse_workspace_memory_lines(body)
    assert [line.entry.record_version for line in parsed if line.entry is not None] == [1, 2, 3]
    assert parsed[-1].entry.source == "user_explicit"


def test_v3_relevant_injection_preserves_provenance_and_legacy_version_ratings(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_memory import read_workspace_memory_injection_digest
    from fleet_rlm.files.memory_models import format_workspace_memory_v3_record

    old = format_workspace_memory_v3_record(
        "Superseding release policy",
        "Policy",
        memory_id="dddd0004",
        created_at="2026-07-19T09:00:00Z",
        updated_at="2026-07-27T10:30:00Z",
        source="operator_import",
    )
    store, root, _process = _store(tmp_path)
    _write_store_file((HEADER + old).encode("utf-8"), root)

    digest = read_workspace_memory_injection_digest(store, request="superseding release policy")

    assert old in digest
    assert "source:operator_import" in digest
    assert "updated:2026-07-27T10:30:00Z" in digest


def test_workspace_agent_edits_and_deletes_v3_without_losing_provenance(tmp_path: Path) -> None:
    from fleet_rlm.files.memory_models import format_workspace_memory_v3_record

    v3 = format_workspace_memory_v3_record(
        "Keep provenance through targeting",
        "Policy",
        memory_id="dddd0004",
        created_at="2026-07-19T09:00:00Z",
        updated_at="2026-07-27T10:30:00Z",
        source="operator_import",
    )
    store, root, _process = _store(tmp_path)
    _write_store_file((HEADER + v3).encode("utf-8"), root)

    updated = store.edit_entry("dddd0004", "must not downgrade")
    edited = parse_workspace_memory_lines(updated)[0].entry
    assert edited is not None
    assert edited.timestamp == "2026-07-19T09:00:00Z"
    assert edited.source == "operator_import"
    assert edited.record_version == 3
    assert str(edited.updated_at) >= edited.timestamp
    assert store.delete_entry("dddd0004") is True
    assert "dddd0004" not in (root / "memory" / "MEMORIES.md").read_text(encoding="utf-8")


def test_workspace_agent_edit_preserves_v3_supersession_and_mixed_file_bytes(tmp_path: Path) -> None:
    from fleet_rlm.files.memory_models import format_workspace_memory_v3_record

    older_id = workspace_memory_record_id("2026-07-18T08:00:00Z", "Policy", "older policy")
    v2 = "- [2026-07-20T09:00:00Z] **General** <!-- id:bbbb0002 -->: historical v2\n"
    v3 = format_workspace_memory_v3_record(
        "Superseding memory with an update link",
        "Policy",
        memory_id="dddd0004",
        created_at="2026-07-19T09:00:00Z",
        updated_at="2026-07-27T10:30:00Z",
        source="operator_import",
        supersedes_id=older_id,
    )
    store, root, _process = _store(tmp_path)
    before = HEADER + v2 + v3
    _write_store_file(before.encode("utf-8"), root)

    updated = store.edit_entry("dddd0004", "Superseding memory with a preserved link")
    edited = parse_workspace_memory_lines(updated)[0].entry

    assert edited is not None
    assert edited.timestamp == "2026-07-19T09:00:00Z"
    assert edited.source == "operator_import"
    assert edited.supersedes_id == older_id
    assert str(edited.updated_at) > "2026-07-27T10:30:00Z"
    after = (root / "memory" / "MEMORIES.md").read_text(encoding="utf-8")
    assert after.startswith(HEADER + v2)
    assert edited.learning in after
    assert "supersedes:aaaa" not in after


def test_v3_memory_id_participates_in_append_collision_targeting(tmp_path: Path) -> None:
    from fleet_rlm.files.memory_models import format_workspace_memory_v3_record, workspace_memory_record_id

    created = "2026-07-19T09:00:00Z"
    learning = "Shared id collision check"
    old_id = workspace_memory_record_id(created, "Policy", learning)
    v3 = format_workspace_memory_v3_record(
        learning,
        "Policy",
        memory_id=old_id,
        created_at=created,
        updated_at="2026-07-27T10:30:00Z",
        source="user_explicit",
    )
    store, root, _process = _store(tmp_path)
    _write_store_file((HEADER + v3).encode("utf-8"), root)
    v2 = f"- [{created}] **Policy** <!-- id:{old_id} -->: {learning} changed\n"

    with pytest.raises(WorkspaceMemoryStoreUnavailableError):
        store.append_record(v2)
    assert (root / "memory" / "MEMORIES.md").read_text(encoding="utf-8") == HEADER + v3


def test_search_failure_or_no_match_uses_the_recency_only_fallback(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_memory import read_workspace_memory_injection_digest

    store, root, _process = _store(tmp_path)
    _write_store_file((HEADER + R1 + R2).encode("utf-8"), root)
    fallback = read_workspace_memory_injection_digest(store, request="nothing matches this unique phrase")
    assert "one" in fallback and "two two" in fallback

    class _FailingSearchStore:
        def __getattr__(self, name: str) -> object:
            return getattr(store, name)

        def list_entries(self, **kwargs: object) -> object:
            del kwargs
            raise RuntimeError("search unavailable")

    degraded = read_workspace_memory_injection_digest(_FailingSearchStore(), request="polars")
    assert degraded == fallback


def test_injection_digest_is_empty_for_missing_or_empty_stores(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_memory import read_workspace_memory_injection_digest

    store, _root, _process = _store(tmp_path)

    assert read_workspace_memory_injection_digest(store) == ""
