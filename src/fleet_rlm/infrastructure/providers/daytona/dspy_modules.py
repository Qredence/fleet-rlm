"""DSPy modules used by the Daytona-backed recursive runtime."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import dspy

from .types import RecursiveTaskSpec

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def _collapse_preview(text: str, *, limit: int = 280) -> str:
    collapsed = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip()


def _load_jsonish(raw: Any) -> Any:
    if isinstance(raw, (list, dict)):
        return raw

    text = str(raw or "").strip()
    if not text:
        return None

    candidates = [text]
    candidates.extend(match.group(1).strip() for match in _JSON_BLOCK_RE.finditer(text))

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _normalized_confidence(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, value))


def _normalized_fanout(raw: Any) -> int:
    if raw is None or raw == "":
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def _normalized_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw or "").strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return False


def _normalized_evidence(raw: Any) -> list[dict[str, Any]]:
    payload = _load_jsonish(raw)
    if isinstance(payload, dict):
        payload = payload.get("evidence") or payload.get("items") or [payload]
    if not isinstance(payload, list):
        return []
    evidence: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        evidence.append(
            {
                str(key): value
                for key, value in item.items()
                if key is not None and value is not None
            }
        )
    return evidence


def parse_recursive_task_specs(raw: Any) -> list[RecursiveTaskSpec]:
    payload = _load_jsonish(raw)
    if isinstance(payload, dict):
        payload = (
            payload.get("child_tasks")
            or payload.get("tasks")
            or payload.get("items")
            or []
        )
    if not isinstance(payload, list):
        return []

    parsed: list[RecursiveTaskSpec] = []
    seen: set[tuple[Any, ...]] = set()
    for item in payload:
        try:
            spec = RecursiveTaskSpec.from_raw(item)
        except ValueError:
            continue
        source = spec.source
        key = (
            spec.task,
            spec.label,
            source.source_id,
            source.path,
            source.start_line or source.line,
            source.end_line,
            source.chunk_index,
            source.header,
            source.pattern,
        )
        if key in seen:
            continue
        seen.add(key)
        parsed.append(spec)
    return parsed


class DaytonaConversationGroundingSignature(dspy.Signature):
    """Resolve follow-up references using explicit DSPy conversation history."""

    current_user_request: str = dspy.InputField(
        desc="The latest user request that should be handled inside the Daytona sandbox."
    )
    history: dspy.History = dspy.InputField(
        desc="Prior conversation turns using user_request and assistant_response keys."
    )
    grounded_task: str = dspy.OutputField(
        desc=(
            "A concise standalone task brief for the sandbox that preserves the "
            "user's intent, resolves references to prior turns, and mentions only "
            "relevant prior context."
        )
    )


class DaytonaConversationGroundingModule(dspy.Module):
    """Small DSPy module that turns chat history into a standalone Daytona task."""

    def __init__(self) -> None:
        super().__init__()
        self.predict = dspy.Predict(DaytonaConversationGroundingSignature)

    def forward(
        self,
        *,
        current_user_request: str,
        history: dspy.History,
    ) -> str:
        prediction = self.predict(
            current_user_request=current_user_request,
            history=history,
        )
        return str(getattr(prediction, "grounded_task", "") or "").strip()


class RecursiveTaskDecompositionSignature(dspy.Signature):
    """Turn a parent observation into concrete recursive child task specs."""

    parent_task: str = dspy.InputField(
        desc="The current node task that may need recursive decomposition."
    )
    latest_observation: str = dspy.InputField(
        desc="The latest execution observation produced by the parent node."
    )
    workspace_context_summary: str = dspy.InputField(
        desc="A concise summary of repo/ref/context available to the node."
    )
    existing_child_tasks_json: str = dspy.InputField(
        desc="JSON array of child task specs already issued for this node."
    )
    budget_json: str = dspy.InputField(
        desc="JSON object describing depth, concurrency, and remaining sandbox budget."
    )
    child_tasks_json: str = dspy.OutputField(
        desc=(
            "A JSON array of zero or more child task specs. Each item should match "
            "{task, label?, source?}."
        )
    )
    decision_summary: str = dspy.OutputField(
        desc="A brief explanation of why recursive children were or were not proposed."
    )


class RecursiveSpawnPolicySignature(dspy.Signature):
    """Decide whether a parent observation should recurse at all."""

    parent_task: str = dspy.InputField(
        desc="The current node task that may need recursive decomposition."
    )
    latest_observation: str = dspy.InputField(
        desc="The latest execution observation produced by the parent node."
    )
    workspace_context_summary: str = dspy.InputField(
        desc="A concise summary of repo/ref/context available to the node."
    )
    existing_child_tasks_json: str = dspy.InputField(
        desc="JSON array of child task specs already issued for this node."
    )
    budget_json: str = dspy.InputField(
        desc="JSON object describing depth, concurrency, and remaining sandbox budget."
    )
    should_spawn: str = dspy.OutputField(
        desc="Boolean-like value indicating whether recursive children should be spawned."
    )
    recommended_fanout: str = dspy.OutputField(
        desc="Positive integer fanout recommendation for recursive children."
    )
    rationale: str = dspy.OutputField(
        desc="A brief explanation for the spawn decision."
    )


@dataclass(slots=True)
class RecursiveSpawnPolicyDecision:
    """Parsed spawn policy decision."""

    should_spawn: bool = False
    recommended_fanout: int = 0
    rationale: str = ""


@dataclass(slots=True)
class RecursiveDecompositionDecision:
    """Parsed recursive decomposition decision."""

    tasks: list[RecursiveTaskSpec] = field(default_factory=list)
    decision_summary: str = ""


class RecursiveTaskDecompositionModule(dspy.Module):
    """DSPy-backed recursive decomposition with deterministic parsing fallback."""

    def __init__(self) -> None:
        super().__init__()
        self.predict = dspy.Predict(RecursiveTaskDecompositionSignature)

    def forward(
        self,
        *,
        parent_task: str,
        latest_observation: str,
        workspace_context_summary: str,
        existing_child_tasks_json: str,
        budget_json: str,
    ) -> RecursiveDecompositionDecision:
        prediction = self.predict(
            parent_task=parent_task,
            latest_observation=latest_observation,
            workspace_context_summary=workspace_context_summary,
            existing_child_tasks_json=existing_child_tasks_json,
            budget_json=budget_json,
        )
        return RecursiveDecompositionDecision(
            tasks=parse_recursive_task_specs(
                getattr(prediction, "child_tasks_json", "") or ""
            ),
            decision_summary=str(
                getattr(prediction, "decision_summary", "") or ""
            ).strip(),
        )


class RecursiveSpawnPolicyModule(dspy.Module):
    """DSPy-backed spawn policy gate with deterministic fallback."""

    def __init__(self) -> None:
        super().__init__()
        self.predict = dspy.Predict(RecursiveSpawnPolicySignature)

    def forward(
        self,
        *,
        parent_task: str,
        latest_observation: str,
        workspace_context_summary: str,
        existing_child_tasks_json: str,
        budget_json: str,
    ) -> RecursiveSpawnPolicyDecision:
        prediction = self.predict(
            parent_task=parent_task,
            latest_observation=latest_observation,
            workspace_context_summary=workspace_context_summary,
            existing_child_tasks_json=existing_child_tasks_json,
            budget_json=budget_json,
        )
        should_spawn = _normalized_bool(getattr(prediction, "should_spawn", ""))
        recommended_fanout = _normalized_fanout(
            getattr(prediction, "recommended_fanout", "")
        )
        if not should_spawn or recommended_fanout <= 0:
            should_spawn = False
            recommended_fanout = 0
        return RecursiveSpawnPolicyDecision(
            should_spawn=should_spawn,
            recommended_fanout=recommended_fanout,
            rationale=str(getattr(prediction, "rationale", "") or "").strip(),
        )


class ChildResultSynthesisSignature(dspy.Signature):
    """Normalize a recursive child run result for parent consumption."""

    parent_task: str = dspy.InputField(
        desc="The parent node task that triggered the child execution."
    )
    child_task_json: str = dspy.InputField(desc="JSON object for the child task spec.")
    child_result_text: str = dspy.InputField(
        desc="Raw rendered child result text before normalization."
    )
    child_evidence_json: str = dspy.InputField(
        desc="JSON array describing normalized evidence surfaced by the child run."
    )
    child_status: str = dspy.InputField(desc="The child completion status.")
    child_summary_json: str = dspy.InputField(
        desc="JSON object describing the child rollout summary."
    )
    answer_markdown: str = dspy.OutputField(
        desc="A concise markdown answer that the parent should consume."
    )
    result_preview: str = dspy.OutputField(
        desc="A short preview of the synthesized child result."
    )
    evidence_json: str = dspy.OutputField(
        desc="A JSON array of evidence objects that supported the synthesized answer."
    )
    follow_up_needed: str = dspy.OutputField(
        desc="Boolean-like value indicating whether more child work is needed."
    )
    confidence: str = dspy.OutputField(
        desc="A floating point confidence between 0 and 1."
    )


@dataclass(slots=True)
class SynthesizedChildResult:
    """Parsed child result synthesis output."""

    answer_markdown: str
    result_preview: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    follow_up_needed: bool = False
    confidence: float | None = None


class ChildResultSynthesisModule(dspy.Module):
    """DSPy-backed child result synthesis with deterministic parsing fallback."""

    def __init__(self) -> None:
        super().__init__()
        self.predict = dspy.Predict(ChildResultSynthesisSignature)

    def forward(
        self,
        *,
        parent_task: str,
        child_task_json: str,
        child_result_text: str,
        child_evidence_json: str,
        child_status: str,
        child_summary_json: str,
    ) -> SynthesizedChildResult:
        prediction = self.predict(
            parent_task=parent_task,
            child_task_json=child_task_json,
            child_result_text=child_result_text,
            child_evidence_json=child_evidence_json,
            child_status=child_status,
            child_summary_json=child_summary_json,
        )
        answer_markdown = str(getattr(prediction, "answer_markdown", "") or "").strip()
        result_preview = str(getattr(prediction, "result_preview", "") or "").strip()
        if not result_preview:
            result_preview = _collapse_preview(answer_markdown)
        return SynthesizedChildResult(
            answer_markdown=answer_markdown,
            result_preview=result_preview,
            evidence=_normalized_evidence(getattr(prediction, "evidence_json", "")),
            follow_up_needed=_normalized_bool(
                getattr(prediction, "follow_up_needed", "")
            ),
            confidence=_normalized_confidence(getattr(prediction, "confidence", "")),
        )
