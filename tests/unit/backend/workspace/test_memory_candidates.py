"""Policy-gated Run-scoped Memory Candidate Tools."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.rlm.events import observe_tool
from fleet_rlm.workspace.memory import (
    WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT,
    WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES,
    WORKSPACE_MEMORY_CANDIDATE_MAX_TOTAL_BYTES,
    MemoryCandidateCollector,
    MemoryCandidatePromotionResult,
    MemoryCandidateToolError,
    MemoryCandidateToolHost,
    promote_memory_candidates,
)


def _collector(**kwargs: Any) -> MemoryCandidateCollector:
    values: dict[str, object] = dict(
        run_id=uuid4(),
        allowed_categories=("Preference", "Project", "Workflow"),
        candidate_id_factory=lambda ordinal: f"cand{ordinal:08d}",
    )
    values.update(kwargs)
    return MemoryCandidateCollector(**cast("dict[str, Any]", values))


def _tool(collector: MemoryCandidateCollector) -> dspy.Tool:
    tools = MemoryCandidateToolHost(collector).as_tools()
    assert len(tools) == 1 and type(tools[0]) is dspy.Tool
    assert tools[0].name == "propose_memory"
    return tools[0]


class _StoreDouble:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def append_record(self, record: str):
        self.calls.append(record)
        raise AssertionError("proposals must not touch Workspace Memory")


def test_memory_candidate_collector_is_immutable_bounded_and_deterministic() -> None:
    run_id = uuid4()
    collector = MemoryCandidateCollector(
        run_id=run_id,
        allowed_categories=(" Preference ", "Project"),
        candidate_id_factory=lambda ordinal: f"cand{ordinal:08d}",
    )

    first = _tool(collector)(
        key_learning="  Prefer" + chr(10) + " polar joins for frame work. ",
        category="Preference",
        supersedes_id="aaaa0001",
    )
    assert first == {
        "ok": True,
        "namespace": "workspace_memory",
        "candidate_id": "cand00000001",
        "category": "Preference",
        "byte_size": len(b"Prefer polar joins for frame work."),
        "candidate_count": 1,
        "candidate_bytes": len(b"Prefer polar joins for frame work."),
        "supersedes": True,
    }

    candidate = collector.drain()[0]
    assert candidate.source == "agent_candidate"
    assert candidate.supersedes_id == "aaaa0001"
    assert candidate.learning == "Prefer polar joins for frame work."
    with pytest.raises(FrozenInstanceError):
        candidate.learning = "changed"  # ty: ignore[invalid-assignment]
    assert collector.drain() == ()


def test_duplicate_candidate_is_idempotent_until_drain() -> None:
    collector = _collector()
    tool = _tool(collector)

    first = tool(key_learning=" remember the stable workflow ", category="Workflow")
    duplicate = tool(key_learning="remember the stable workflow", category="Workflow")

    assert duplicate["candidate_id"] == first["candidate_id"]
    assert duplicate["candidate_count"] == 1
    assert len(collector.drain()) == 1


@pytest.mark.parametrize(
    ("key_learning", "category", "supersedes_id", "message"),
    [
        ("stable", "Project", "nothex", "supersedes id is invalid"),
        ("stable", "Secret Category!", None, "category is invalid"),
        ("", "Project", None, "candidate is invalid"),
        ("x" * (WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES + 1), "Project", None, "allowed byte budget"),
    ],
)
def test_candidate_payload_is_strict(
    key_learning: str,
    category: str,
    supersedes_id: str | None,
    message: str,
) -> None:
    with pytest.raises(MemoryCandidateToolError, match=message) as captured:
        _tool(_collector())(
            key_learning=key_learning,
            category=category,
            supersedes_id=supersedes_id,
        )

    assert captured.value.code in {"invalid_category", "invalid_entry", "invalid_id", "candidate_bytes"}


def test_candidate_category_allowlist_and_count_and_total_budgets() -> None:
    collector = MemoryCandidateCollector(
        run_id=uuid4(),
        allowed_categories=("Project",),
        candidate_id_factory=lambda ordinal: f"cand{ordinal:08d}",
    )
    tool = _tool(collector)

    with pytest.raises(MemoryCandidateToolError, match="not allowed") as captured:
        tool(key_learning="stable preference", category="Preference")
    assert captured.value.code == "policy_denied"

    for index in range(WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT):
        tool(key_learning=f"learning {index}", category="Project")
    with pytest.raises(MemoryCandidateToolError, match="limit") as captured:
        tool(key_learning="one more", category="Project")
    assert captured.value.code == "candidate_limit"

    bounded = MemoryCandidateCollector(run_id=uuid4(), allowed_categories=("Project",))
    tool = _tool(bounded)
    for index in range(WORKSPACE_MEMORY_CANDIDATE_MAX_TOTAL_BYTES // WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES):
        tool(key_learning=str(index) + "x" * (WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES - 1), category="Project")
    with pytest.raises(MemoryCandidateToolError, match="total byte"):
        tool(key_learning="z" + "x" * (WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES - 1), category="Project")


def test_candidate_tool_contract_is_narrow_and_events_do_not_copy_learning() -> None:
    collector = _collector()
    host = MemoryCandidateToolHost(collector)
    tool = host.as_tools()[0]

    desc = tool.desc or ""
    assert tool.args == {
        "key_learning": {"type": "string"},
        "category": {"type": "string"},
        "supersedes_id": {"type": ["string", "null"]},
    }
    assert "does not immediately change Workspace Memory" in desc
    assert "credentials" in desc
    assert "raw documents" in desc
    assert "temporary task state" in desc

    observed: list[object] = []

    def visit(detail: object) -> None:
        observed.append(detail)

    observed_tool = observe_tool(tool, visit, host.event_views()["propose_memory"])
    result = observed_tool(
        key_learning="top secret candidate learning",
        category="Project",
        supersedes_id="aaaa0001",
    )

    assert result["candidate_id"] == "cand00000001"
    rendered = str(observed)
    assert "top secret candidate learning" not in rendered
    assert "cand00000001" in rendered
    assert "learning_bytes" in rendered


def test_candidate_collector_performs_zero_memory_or_store_calls() -> None:
    store = _StoreDouble()
    error = MemoryCandidateToolError("policy_denied", "disabled")
    del error
    tool = _tool(_collector())

    tool(key_learning="stable project workflow", category="Project")

    assert store.calls == []


class _PromotionStore:
    def __init__(self, *entries) -> None:
        self.entries = list(entries)
        self.appended: list[str] = []
        self.fail_next = False
        self.list_calls = 0

    def read_tail(self, *, byte_budget: int):
        from fleet_rlm.workspace.models import WorkspaceMemoryReadResult

        return WorkspaceMemoryReadResult(
            content="", truncated=False, bytes_returned=0, byte_budget=byte_budget, total_bytes=0, warnings=0
        )

    def delete_entry(self, memory_id: str) -> bool:
        del memory_id
        raise AssertionError("promotion tests do not delete")

    def edit_entry(self, memory_id: str, key_learning: str, *, category: str | None = None) -> str:
        del memory_id, key_learning, category
        raise AssertionError("promotion tests do not edit")

    def list_entries(self, *, after: str | None = None, limit: int, category: str | None = None):
        from fleet_rlm.workspace.models import WorkspaceMemoryListResult

        del after
        self.list_calls += 1
        entries = self.entries[:limit]
        if category is not None:
            entries = [entry for entry in entries if entry.category == category]
        return WorkspaceMemoryListResult(entries=tuple(entries), truncated=False, next_cursor=None, warnings=0)

    def append_record(self, record: str):
        from fleet_rlm.workspace.models import WorkspaceMemoryAppendResult, parse_workspace_memory_record

        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("mounted store failed")
        self.appended.append(record)
        self.entries.append(parse_workspace_memory_record(record))
        return WorkspaceMemoryAppendResult(entry_bytes=len(record.encode()), total_bytes=1)


def test_candidate_promotion_writes_fresh_v3_agent_records_and_metadata() -> None:
    from fleet_rlm.workspace.memory import MemoryCandidate
    from fleet_rlm.workspace.models import parse_workspace_memory_record

    candidate = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="maintain compact project reports",
        byte_size=len(b"maintain compact project reports"),
        supersedes_id=None,
    )

    result = promote_memory_candidates(
        store=(store := _PromotionStore()),
        candidates=(candidate,),
        allowed_categories=("Project",),
        clock=lambda: datetime.strptime("2026-08-11T01:02:03", "%Y-%m-%dT%H:%M:%S"),
    )

    assert result.promoted_count == result.proposed_count == 1
    assert result.duplicate_count == result.dropped_count == result.failure_count == 0
    entry = parse_workspace_memory_record(store.appended[0])
    assert entry.source == "agent_candidate"
    assert entry.timestamp == entry.updated_at == "2026-08-11T01:02:03Z"
    assert len(entry.memory_id) == 8 and entry.supersedes_id is None


def test_candidate_promotion_dedupes_exact_active_content_and_repeats() -> None:
    from fleet_rlm.workspace.memory import MemoryCandidate
    from fleet_rlm.workspace.models import WorkspaceMemoryEntry

    active = WorkspaceMemoryEntry(
        memory_id="aaaa0001",
        timestamp="2026-08-10T01:00:00Z",
        updated_at="2026-08-10T01:00:00Z",
        category="Project",
        learning="maintain project reports",
        source="user_explicit",
        record_version=3,
        supersedes_id="bbbb0002",
    )
    candidate = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning=" maintain" + chr(10) + "project reports ",
        byte_size=len(b"maintain project reports"),
        supersedes_id=None,
    )
    store = _PromotionStore(active)

    result = promote_memory_candidates(store=store, candidates=(candidate,), allowed_categories=("Project",))

    assert result == MemoryCandidatePromotionResult(
        proposed_count=1,
        promoted_count=0,
        duplicate_count=1,
        dropped_count=0,
        failure_count=0,
        candidate_bytes=len(b"maintain project reports"),
        reasons=(),
    )
    assert store.appended == []


def test_candidate_promotion_revalidates_current_active_supersession_target() -> None:
    from fleet_rlm.workspace.memory import MemoryCandidate
    from fleet_rlm.workspace.models import WorkspaceMemoryEntry

    target = WorkspaceMemoryEntry(
        memory_id="aaaa0001",
        timestamp="2026-08-10T01:00:00Z",
        updated_at="2026-08-10T01:00:00Z",
        category="Project",
        learning="old report policy",
        source="legacy_unknown",
        record_version=1,
        active=False,
    )
    candidate = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="new report policy",
        byte_size=len(b"new report policy"),
        supersedes_id="aaaa0001",
    )

    result = promote_memory_candidates(
        store=(store := _PromotionStore(target)),
        candidates=(candidate,),
        allowed_categories=("Project",),
    )

    assert result.dropped_count == 1
    assert result.reasons == ("supersedes_not_active",)
    assert store.appended == []


def test_candidate_promotion_drop_and_failure_are_fail_soft_and_bounded() -> None:
    from fleet_rlm.workspace.memory import MemoryCandidate

    denied = MemoryCandidate(
        candidate_id="cand00000001",
        category="Workflow",
        learning="workflow learning",
        byte_size=len(b"workflow learning"),
    )
    failing = MemoryCandidate(
        candidate_id="cand00000002",
        category="Project",
        learning="project learning",
        byte_size=len(b"project learning"),
    )
    accepted = MemoryCandidate(
        candidate_id="cand00000003",
        category="Project",
        learning="another project learning",
        byte_size=len(b"another project learning"),
    )
    store = _PromotionStore()
    store.fail_next = True

    result = promote_memory_candidates(
        store=store,
        candidates=(denied, failing, accepted),
        allowed_categories=("Project",),
    )

    assert result.promoted_count == 1
    assert result.dropped_count == 1
    assert result.failure_count == 1
    assert result.reasons == ("policy_denied", "promotion_failed")
    assert len(store.appended) == 1


def test_candidate_promotion_uses_one_active_snapshot_and_preserves_conflict_detail() -> None:
    from fleet_rlm.workspace.memory import MemoryCandidate
    from fleet_rlm.workspace.models import WorkspaceMemoryConflictError

    class ConflictStore(_PromotionStore):
        def append_record(self, record: str):
            del record
            raise WorkspaceMemoryConflictError("supersedes_not_active")

    candidate = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="stale replacement",
        byte_size=len(b"stale replacement"),
        supersedes_id="aaaa0001",
    )
    store = ConflictStore()

    result = promote_memory_candidates(
        store=store,
        candidates=(candidate, candidate),
        allowed_categories=("Project",),
    )

    assert result.duplicate_count == 1
    assert result.dropped_count == 1
    assert result.reasons == ("supersedes_not_active",)
    assert store.list_calls == 1


def test_candidate_promotion_rejects_overrun_mismatch_and_empty_policy_without_store_access() -> None:
    from fleet_rlm.workspace.memory import MemoryCandidate

    store = _PromotionStore()
    overrun = tuple(
        MemoryCandidate(candidate_id=f"cand{index:08d}", category="Project", learning=f"learning {index}", byte_size=1)
        for index in range(17)
    )

    result = promote_memory_candidates(store=store, candidates=overrun, allowed_categories=("Project",))
    assert result.dropped_count == 17 and result.reasons == ("candidate_limit",)
    assert store.list_calls == 0 and store.appended == []

    mismatched = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="ten chars",
        byte_size=1,
    )
    result = promote_memory_candidates(store=store, candidates=(mismatched,), allowed_categories=("Project",))
    assert result.dropped_count == 1 and result.reasons == ("invalid_entry",)
    assert store.list_calls == 0 and store.appended == []

    result = promote_memory_candidates(store=store, candidates=(mismatched,), allowed_categories=())
    assert result.dropped_count == 1 and result.reasons == ("policy_denied",)
    assert store.list_calls == 0 and store.appended == []


def test_candidate_promotion_classifies_store_full_and_stops_the_tail() -> None:
    from fleet_rlm.workspace.memory import MemoryCandidate
    from fleet_rlm.workspace.models import WorkspaceMemoryStoreFullError

    class FullStore(_PromotionStore):
        def append_record(self, record: str):
            del record
            raise WorkspaceMemoryStoreFullError

    candidates = tuple(
        MemoryCandidate(
            candidate_id=f"cand{index:08d}",
            category="Project",
            learning=f"learning {index}",
            byte_size=len(f"learning {index}".encode()),
        )
        for index in range(3)
    )

    result = promote_memory_candidates(
        store=(store := FullStore()),
        candidates=candidates,
        allowed_categories=("Project",),
    )

    assert result.failure_count == len(candidates)
    assert result.reasons == ("store_full",)
    assert store.list_calls == 1


def test_candidate_promotion_preserves_append_time_conflict_and_continues() -> None:
    from fleet_rlm.workspace.memory import MemoryCandidate
    from fleet_rlm.workspace.models import WorkspaceMemoryConflictError, WorkspaceMemoryEntry

    class RacingConflictStore(_PromotionStore):
        def append_record(self, record: str):
            if "first promotion" in record:
                raise WorkspaceMemoryConflictError("supersedes_not_active")
            return super().append_record(record)

    target = WorkspaceMemoryEntry(
        memory_id="aaaa0001",
        timestamp="2026-08-10T01:00:00Z",
        updated_at="2026-08-10T01:00:00Z",
        category="Project",
        learning="old replacement",
        source="legacy_unknown",
        record_version=1,
    )
    conflict = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="first promotion",
        byte_size=len(b"first promotion"),
        supersedes_id="aaaa0001",
    )
    accepted = MemoryCandidate(
        candidate_id="cand00000002",
        category="Project",
        learning="second promotion",
        byte_size=len(b"second promotion"),
    )

    result = promote_memory_candidates(
        store=(store := RacingConflictStore(target)),
        candidates=(conflict, accepted),
        allowed_categories=("Project",),
    )

    assert result.promoted_count == 1
    assert result.dropped_count == 1
    assert result.reasons == ("supersedes_not_active",)
    assert len(store.appended) == 1


def test_intent_builder_and_post_commit_promotion_mint_identical_records() -> None:
    """Both promotion paths share one validation + record-minting pipeline (P33 fold)."""
    from datetime import UTC, datetime

    from fleet_rlm.workspace.memory import MemoryCandidate, build_memory_promotion_intents

    def clock() -> datetime:
        return datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)

    candidate = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="shared minting stays byte-identical",
        byte_size=len(b"shared minting stays byte-identical"),
    )
    intents = build_memory_promotion_intents(
        run_id=uuid4(),
        candidates=(candidate,),
        allowed_categories=("Project",),
        clock=clock,
    )
    store = _PromotionStore()
    result = promote_memory_candidates(
        store=store,
        candidates=(candidate,),
        allowed_categories=("Project",),
        clock=clock,
    )

    assert result.promoted_count == 1
    assert store.appended == [intents[0].record_text]
    assert intents[0].memory_id in store.appended[0]
