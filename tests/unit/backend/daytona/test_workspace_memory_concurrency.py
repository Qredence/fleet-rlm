"""Cross-process Workspace Memory mutation characterization."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.files.memory_models import (
    WORKSPACE_MEMORY_HEADER,
    format_workspace_memory_v3_record,
)
from fleet_rlm.files.volume_paths import VolumePaths

HEADER = WORKSPACE_MEMORY_HEADER + "\n"


class _MemberResult(SimpleNamespace):
    pass


async def _run_member(
    *,
    volume_root: Path,
    action: str,
    record: str = "",
    memory_id: str = "",
    learning: str = "",
    delay_operation: str,
) -> _MemberResult:
    """
    Run a delayed workspace-memory mutation in a subprocess.
    
    Parameters:
        volume_root (Path): Shared volume used by the subprocess.
        action (str): Mutation to perform: ``append``, ``edit``, or ``delete``.
        record (str): Record content for an append operation.
        memory_id (str): Target record identifier for an edit or delete operation.
        learning (str): Replacement learning content for an edit operation.
        delay_operation (str): Operation phase at which to inject the delay.
    
    Returns:
        _MemberResult: The subprocess return code and captured output.
    """
    volume_root.mkdir(parents=True, exist_ok=True)
    script = textwrap.dedent(
        f"""
        import subprocess, sys, time
        from types import SimpleNamespace
        from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore
        from fleet_rlm.files.volume_paths import VolumePaths

        class RaceProcess:
            def code_run(self, code: str, **_kwargs):
                markers = {{
                    "write": "        if operation == 'write':",
                    "edit_compose": "                payload = ''.join(lines).encode('utf-8')",
                }}
                marker = markers[{delay_operation!r}]
                prefix = "                " if {delay_operation!r} == "edit_compose" else "        "
                assert marker in code, "workspace agent delay marker missing"
                deferred = code.replace(marker, prefix + "time.sleep(0.10)" + chr(10) + marker, 1)
                completed = subprocess.run(
                    [sys.executable, "-c", deferred],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                return SimpleNamespace(exit_code=completed.returncode, result=completed.stdout + completed.stderr)

        store = DaytonaWorkspaceMemoryStore(
            SimpleNamespace(process=RaceProcess()),
            volume_paths=VolumePaths.from_mount({str(volume_root)!r}),
            max_upload_bytes=262_144,
        )
        result = None
        action = {action!r}
        if action == "append":
            result = store.append_record({record!r})
        elif action == "edit":
            result = store.edit_entry({memory_id!r}, {learning!r})
        elif action == "delete":
            result = store.delete_entry({memory_id!r})
        else:
            raise AssertionError(action)
        print({{"result": str(result)}})
        """
    ).lstrip()
    completed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return _MemberResult(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def _write_memory(volume_root: Path, *, records: list[str] | None = None, legacy: bool = False) -> None:
    parent = volume_root if legacy else volume_root / "memory"
    parent.mkdir(parents=True, exist_ok=True)
    content = "" if legacy else HEADER
    content += "".join(records or [])
    (parent / "MEMORIES.md").write_text(content, encoding="utf-8")


def _v3(memory_id: str, learning: str, *, supersedes_id: str | None = None) -> str:
    return format_workspace_memory_v3_record(
        learning,
        "Policy",
        memory_id=memory_id,
        created_at="2026-07-19T09:00:00Z",
        updated_at="2026-07-19T09:00:00Z",
        source="operator_import",
        supersedes_id=supersedes_id,
    )


def _listed(volume: Path):
    from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore

    class LocalProcess:
        def code_run(self, code: str, **_kwargs):
            completed = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True)
            return SimpleNamespace(exit_code=completed.returncode, result=completed.stdout)

    store = DaytonaWorkspaceMemoryStore(
        SimpleNamespace(process=LocalProcess()),
        volume_paths=VolumePaths.from_mount(str(volume)),
        max_upload_bytes=262_144,
    )
    return store.list_entries(limit=256)


@pytest.mark.asyncio
async def test_independent_process_appends_do_not_lose_either_valid_record(tmp_path: Path) -> None:
    volume_root = tmp_path / "volume"
    first, second = await asyncio.gather(
        _run_member(
            volume_root=volume_root, action="append", record=_v3("aaaa0001", "alpha note"), delay_operation="write"
        ),
        _run_member(
            volume_root=volume_root, action="append", record=_v3("bbbb0002", "beta note"), delay_operation="write"
        ),
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert {entry.memory_id for entry in _listed(volume_root).entries} == {"aaaa0001", "bbbb0002"}


@pytest.mark.asyncio
async def test_independent_process_edit_and_delete_are_linearizable(tmp_path: Path) -> None:
    volume_root = tmp_path / "volume"
    _write_memory(volume_root, records=[_v3("aaaa0001", "current policy")])
    results = await asyncio.gather(
        _run_member(
            volume_root=volume_root,
            action="edit",
            memory_id="aaaa0001",
            learning="editor won",
            delay_operation="edit_compose",
        ),
        _run_member(volume_root=volume_root, action="delete", memory_id="aaaa0001", delay_operation="edit_compose"),
    )

    successes = [result.returncode == 0 for result in results]
    assert sum(successes) in (1, 2), [result.stderr for result in results]
    entries = _listed(volume_root).entries if (volume_root / "memory" / "MEMORIES.md").exists() else ()
    if successes == [True, False]:
        assert [entry.learning for entry in entries] == ["editor won"]
    else:
        assert entries == ()


@pytest.mark.asyncio
async def test_independent_process_supersede_supersede_allows_one_active_branch(tmp_path: Path) -> None:
    volume_root = tmp_path / "volume"
    _write_memory(volume_root, records=[_v3("aaaa0001", "baseline policy")])
    results = await asyncio.gather(
        _run_member(
            volume_root=volume_root,
            action="append",
            record=_v3("bbbb0002", "first branch", supersedes_id="aaaa0001"),
            delay_operation="write",
        ),
        _run_member(
            volume_root=volume_root,
            action="append",
            record=_v3("cccc0003", "second branch", supersedes_id="aaaa0001"),
            delay_operation="write",
        ),
    )

    assert sum(result.returncode == 0 for result in results) == 1, [result.stderr for result in results]
    listed = _listed(volume_root)
    winners = [entry for entry in listed.entries if entry.memory_id in ("bbbb0002", "cccc0003")]
    assert len(winners) == 1
    assert listed.entries[0].active is False
    assert winners[0].active is True


@pytest.mark.asyncio
async def test_independent_process_legacy_migration_cannot_duplicate_or_lose_old_content(tmp_path: Path) -> None:
    volume_root = tmp_path / "volume"
    _write_memory(volume_root, legacy=True, records=[_v3("aaaa0001", "legacy operator note")])

    results = await asyncio.gather(
        _run_member(
            volume_root=volume_root, action="append", record=_v3("bbbb0002", "new policy"), delay_operation="write"
        ),
        _run_member(
            volume_root=volume_root, action="append", record=_v3("cccc0003", "other policy"), delay_operation="write"
        ),
    )

    assert all(result.returncode == 0 for result in results), [result.stderr for result in results]
    listed = _listed(volume_root)
    ids = [entry.memory_id for entry in listed.entries]
    assert ids[:1] == ["aaaa0001"]
    assert {entry.memory_id for entry in listed.entries} == {"aaaa0001", "bbbb0002", "cccc0003"}
    assert sum("legacy operator note" in entry.learning for entry in listed.entries) == 1


@pytest.mark.asyncio
async def test_independent_process_edit_and_append_preserve_both_mutations(tmp_path: Path) -> None:
    volume_root = tmp_path / "volume"
    _write_memory(volume_root, records=[_v3("aaaa0001", "current policy")])
    edit_result, append_result = await asyncio.gather(
        _run_member(
            volume_root=volume_root,
            action="edit",
            memory_id="aaaa0001",
            learning="serialized edit",
            delay_operation="edit_compose",
        ),
        _run_member(
            volume_root=volume_root,
            action="append",
            record=_v3("bbbb0002", "serialized append"),
            delay_operation="write",
        ),
    )

    assert edit_result.returncode == 0, edit_result.stderr
    assert append_result.returncode == 0, append_result.stderr
    ids = {entry.memory_id for entry in _listed(volume_root).entries}
    assert ids == {"aaaa0001", "bbbb0002"}
    assert {entry.learning for entry in _listed(volume_root).entries} == {"serialized edit", "serialized append"}


@pytest.mark.asyncio
async def test_independent_process_edits_of_one_record_serialize(tmp_path: Path) -> None:
    volume_root = tmp_path / "volume"
    _write_memory(volume_root, records=[_v3("aaaa0001", "current policy")])
    first, second = await asyncio.gather(
        _run_member(
            volume_root=volume_root,
            action="edit",
            memory_id="aaaa0001",
            learning="first serialized edit",
            delay_operation="edit_compose",
        ),
        _run_member(
            volume_root=volume_root,
            action="edit",
            memory_id="aaaa0001",
            learning="second serialized edit",
            delay_operation="edit_compose",
        ),
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    entries = _listed(volume_root).entries
    assert [entry.memory_id for entry in entries] == ["aaaa0001"]
    assert entries[0].learning in {"first serialized edit", "second serialized edit"}


@pytest.mark.asyncio
async def test_successful_mutation_from_one_process_is_visible_to_the_next_store_read(tmp_path: Path) -> None:
    volume_root = tmp_path / "volume"
    result = await _run_member(
        volume_root=volume_root,
        action="append",
        record=_v3("aaaa0001", "cross process memory"),
        delay_operation="write",
    )
    assert result.returncode == 0, result.stderr
    listed = _listed(volume_root)
    assert listed.entries[0].memory_id == "aaaa0001"
