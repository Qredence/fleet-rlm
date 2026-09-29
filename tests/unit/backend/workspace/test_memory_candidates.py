"""Policy-gated Run-scoped Memory Candidate Tools."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.workspace.memory import (
    WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT,
    WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES,
    WORKSPACE_MEMORY_CANDIDATE_MAX_TOTAL_BYTES,
    MemoryCandidateCollector,
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
