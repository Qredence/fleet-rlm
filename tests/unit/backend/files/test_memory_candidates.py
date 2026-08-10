"""Policy-gated Run-scoped Memory Candidate Tools."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any, cast
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.files.memory_candidate_tools import MemoryCandidateToolHost
from fleet_rlm.files.memory_candidates import (
    WORKSPACE_MEMORY_CANDIDATE_MAX_COUNT,
    WORKSPACE_MEMORY_CANDIDATE_MAX_LEARNING_BYTES,
    WORKSPACE_MEMORY_CANDIDATE_MAX_TOTAL_BYTES,
    MemoryCandidateCollector,
    MemoryCandidateToolError,
)
from fleet_rlm.rlm.tool_observer import observe_tool


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
