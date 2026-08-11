"""Deterministic promoted Memory Candidate behavior through Workspace Memory."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

from fleet_rlm.files.memory_candidates import MemoryCandidate, promote_memory_candidates
from fleet_rlm.files.memory_tools import WorkspaceMemoryToolHost
from fleet_rlm.files.volume_paths import VolumePaths


class _GeneratedWorkspaceProcess:
    def code_run(self, code: str):
        completed = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return SimpleNamespace(exit_code=completed.returncode, result=completed.stdout.strip())


def _store(tmp_path):
    from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore

    volume_root = tmp_path / "volume"
    volume_root.mkdir()
    return (
        DaytonaWorkspaceMemoryStore(
            SimpleNamespace(process=_GeneratedWorkspaceProcess()),
            volume_paths=VolumePaths.from_mount(str(volume_root)),
            max_upload_bytes=262_144,
        ),
        volume_root,
    )


def test_promoted_agent_candidate_becomes_searchable_and_injectable_on_the_next_turn(tmp_path) -> None:
    from fleet_rlm.daytona.workspace_memory import read_workspace_memory_injection_digest

    store, _volume_root = _store(tmp_path)
    candidate = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="Keep operator report evidence compact",
        byte_size=len(b"Keep operator report evidence compact"),
    )

    result = promote_memory_candidates(store=store, candidates=(candidate,), allowed_categories=("Project",))

    assert result.promoted_count == 1
    entries = store.list_entries(limit=16).entries
    assert len(entries) == 1
    assert entries[0].source == "agent_candidate"
    assert entries[0].active is True

    searched = WorkspaceMemoryToolHost(store).as_tools()[4](query="operator report evidence")
    assert searched["count"] == 1
    assert searched["entries"][0]["source"] == "agent_candidate"
    digest = read_workspace_memory_injection_digest(store, request="Show operator report evidence")
    assert "source:agent_candidate" in digest
    assert "operator report evidence compact" in digest

    again = promote_memory_candidates(store=store, candidates=(candidate,), allowed_categories=("Project",))
    assert again.duplicate_count == 1
    assert store.list_entries(limit=16).entries == entries


def test_promotion_supersedes_only_an_active_target_and_updates_the_injection_view(tmp_path) -> None:
    from fleet_rlm.daytona.workspace_memory import read_workspace_memory_injection_digest
    from fleet_rlm.files.memory_models import parse_workspace_memory_lines

    store, volume_root = _store(tmp_path)
    store.append_record(
        "- [2026-08-10T10:00:00Z] **Project** <!-- id:aaaa0001 -->: operator report should be long" + chr(10)
    )
    target = store.list_entries(limit=16).entries[0]
    candidate = MemoryCandidate(
        candidate_id="cand00000002",
        category="Project",
        learning="operator report should stay compact",
        byte_size=len(b"operator report should stay compact"),
        supersedes_id=target.memory_id,
    )

    result = promote_memory_candidates(store=store, candidates=(candidate,), allowed_categories=("Project",))

    assert result.promoted_count == 1
    entries = store.list_entries(limit=16).entries
    by_id = {entry.memory_id: entry for entry in entries}
    assert by_id["aaaa0001"].active is False
    replacement = next(entry for entry in entries if entry.memory_id != "aaaa0001")
    assert replacement.source == "agent_candidate"
    assert replacement.active is True
    assert replacement.supersedes_id == "aaaa0001"

    searched = WorkspaceMemoryToolHost(store).as_tools()[4](query="operator report")
    assert [entry["learning"] for entry in searched["entries"]] == ["operator report should stay compact"]
    digest = read_workspace_memory_injection_digest(store, request="operator report")
    assert "operator report should stay compact" in digest
    assert "operator report should be long" not in digest

    # Physical history stays visible chronologically; only the active view filters it.
    raw = (volume_root / "memory" / "MEMORIES.md").read_text(encoding="utf-8")
    parsed = parse_workspace_memory_lines(raw)
    assert any(line.entry.memory_id == "aaaa0001" for line in parsed if line.entry is not None)

    replay = promote_memory_candidates(store=store, candidates=(candidate,), allowed_categories=("Project",))
    assert replay.duplicate_count == 1
    assert len(store.list_entries(limit=16).entries) == 2

    explicit = WorkspaceMemoryToolHost(store).as_tools()[1](
        key_learning="Author asked to keep report setup minimal",
        category="Preference",
    )
    listed = store.list_entries(limit=16).entries
    assert explicit["memory_id"] in {entry.memory_id for entry in listed}
    assert next(entry for entry in listed if entry.memory_id == explicit["memory_id"]).source == "user_explicit"
