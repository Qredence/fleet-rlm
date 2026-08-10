"""DSPy Tool adapter for Run-scoped Memory Candidates."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, cast

import dspy

from fleet_rlm.files.memory_candidates import (
    WORKSPACE_MEMORY_CANDIDATE_NAMESPACE,
    MemoryCandidateCollector,
)
from fleet_rlm.files.memory_models import (
    WorkspaceMemoryCategoryError,
    WorkspaceMemoryIdError,
    normalize_workspace_memory_category,
    normalize_workspace_memory_id,
)
from fleet_rlm.rlm.events import JsonValue
from fleet_rlm.rlm.tool_observer import ToolEventView, bound_event_text


class MemoryCandidateToolHost:
    """Optional Root-only `propose_memory` host bound to one Run collector."""

    def __init__(self, candidates: MemoryCandidateCollector) -> None:
        self._candidates = candidates

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def propose_memory(
            key_learning: str,
            category: str,
            supersedes_id: str | None = None,
        ) -> dict[str, object]:
            """Propose one long-lived learning for commit-gated memory review."""
            candidate = self._candidates.propose(
                key_learning=key_learning,
                category=category,
                supersedes_id=supersedes_id,
            )
            return {
                "ok": True,
                "namespace": WORKSPACE_MEMORY_CANDIDATE_NAMESPACE,
                "candidate_id": candidate.candidate_id,
                "category": candidate.category,
                "byte_size": candidate.byte_size,
                "candidate_count": self._candidates.candidate_count,
                "candidate_bytes": self._candidates.candidate_bytes,
                "supersedes": candidate.supersedes_id is not None,
            }

        return (
            dspy.Tool(
                propose_memory,
                name="propose_memory",
                desc=(
                    "Propose one durable cross-session preference, workflow, or project learning for later "
                    "commit-gated promotion. Use only for stable, non-secret evidence likely to remain useful "
                    "beyond this Turn; never for temporary task state, raw documents, credentials, or ordinary "
                    "conversational facts. This does not immediately change Workspace Memory."
                ),
                args={
                    "key_learning": {"type": "string"},
                    "category": {"type": "string"},
                    "supersedes_id": {"type": ["string", "null"]},
                },
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        def propose_input(arguments: Mapping[str, Any]) -> JsonValue:
            raw_learning = arguments.get("key_learning")
            supersedes = arguments.get("supersedes_id")
            return {
                "category": _event_candidate_category(arguments.get("category")),
                "learning_bytes": len(str(raw_learning or "").encode("utf-8")),
                "supersedes": supersedes is not None,
                "supersedes_id": _event_candidate_id(supersedes) if supersedes is not None else None,
            }

        def propose_output(result: object) -> JsonValue:
            if not isinstance(result, Mapping):
                return {}
            values = cast(Mapping[str, JsonValue], result)
            payload: dict[str, JsonValue] = {}
            for field in (
                "ok",
                "namespace",
                "candidate_id",
                "category",
                "byte_size",
                "candidate_count",
                "candidate_bytes",
                "supersedes",
            ):
                if field in values:
                    payload[field] = (
                        bound_event_text(values[field]) if isinstance(values[field], str) else values[field]
                    )
            return payload

        return MappingProxyType(
            {"propose_memory": ToolEventView(input_projection=propose_input, output_projection=propose_output)}
        )


def _event_candidate_category(value: object) -> str:
    try:
        return normalize_workspace_memory_category(value)  # ty: ignore[invalid-argument-type]
    except WorkspaceMemoryCategoryError:
        return "invalid"


def _event_candidate_id(value: object) -> str:
    try:
        return normalize_workspace_memory_id(value) if isinstance(value, str) else "invalid"
    except WorkspaceMemoryIdError:
        return "invalid"


__all__ = ["MemoryCandidateToolHost"]
