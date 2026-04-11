"""DSPy-native recursive context-selection helpers for the worker/runtime layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import dspy

from .signatures import AssembleRecursiveWorkspaceContext

_MAX_SUMMARY_CHARS = 800
_MAX_CONTEXT_BUDGET = 2400
_MAX_SELECTED_ITEMS = 4
_MAX_CATALOG_ENTRIES = 6
_MAX_CATALOG_ENTRY_CHARS = 240
_DEFAULT_OMISSION_RATIONALE = (
    "Omit unselected durable memory and verbose traces to stay within budget."
)


def _compact_text(value: Any, *, limit: int = _MAX_SUMMARY_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _catalog_id(entry: str) -> str:
    return str(entry or "").split("|", 1)[0].strip()


def _coerce_string_list(
    value: Any,
    *,
    valid_options: list[str],
    limit: int = _MAX_SELECTED_ITEMS,
) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_items = [str(item or "").strip() for item in value]
    else:
        raw_text = str(value or "").strip()
        raw_items = [part.strip() for part in raw_text.replace("\n", ",").split(",")]

    valid = set(valid_options)
    selected: list[str] = []
    for item in raw_items:
        if not item or item not in valid or item in selected:
            continue
        selected.append(item)
        if len(selected) >= limit:
            return selected

    if selected:
        return selected

    for item in valid_options:
        if item not in selected:
            selected.append(item)
        if len(selected) >= min(limit, len(valid_options)):
            break
    return selected


def _normalize_catalog(
    catalog: list[str], *, limit: int = _MAX_CATALOG_ENTRIES
) -> list[str]:
    normalized: list[str] = []
    for entry in catalog:
        text = _compact_text(entry, limit=_MAX_CATALOG_ENTRY_CHARS)
        if text and text not in normalized:
            normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def _materialize_selected_entries(
    catalog: list[str],
    selected_ids: list[str],
) -> list[str]:
    catalog_by_id = {_catalog_id(entry): entry for entry in catalog if entry}
    return [catalog_by_id[item] for item in selected_ids if item in catalog_by_id]


def _fallback_context_summary(
    *,
    selected_memory_entries: list[str],
    selected_evidence_entries: list[str],
    latest_tool_or_code_result: str,
    context_budget: int,
) -> str:
    parts = [
        "Carry forward only the selected Daytona-backed handles and bounded evidence for the next recursive pass.",
    ]
    if selected_memory_entries:
        parts.append("Memory handles: " + "; ".join(selected_memory_entries))
    if selected_evidence_entries:
        parts.append("Recent evidence: " + "; ".join(selected_evidence_entries))
    if latest_tool_or_code_result:
        parts.append("Latest result: " + latest_tool_or_code_result)
    return _compact_text(
        "\n".join(part for part in parts if part),
        limit=max(400, min(context_budget, _MAX_CONTEXT_BUDGET)),
    )


@dataclass(frozen=True, slots=True)
class RecursiveContextSelectionInputs:
    """Typed input for recursive context assembly."""

    user_request: str
    current_plan: str
    loop_state: str
    working_memory_catalog: list[str]
    recent_sandbox_evidence_catalog: list[str]
    latest_tool_or_code_result: str
    context_budget: int

    def as_kwargs(self) -> dict[str, Any]:
        return {
            "user_request": self.user_request,
            "current_plan": self.current_plan,
            "loop_state": self.loop_state,
            "working_memory_catalog": list(self.working_memory_catalog),
            "recent_sandbox_evidence_catalog": list(
                self.recent_sandbox_evidence_catalog
            ),
            "latest_tool_or_code_result": self.latest_tool_or_code_result,
            "context_budget": self.context_budget,
        }


@dataclass(frozen=True, slots=True)
class RecursiveContextSelectionDecision:
    """Typed normalized output for recursive context assembly."""

    selected_memory_handles: list[str]
    selected_evidence_ids: list[str]
    assembled_context_summary: str
    omission_rationale: str


@dataclass(frozen=True, slots=True)
class MaterializedRecursiveContext:
    """Selected bounded context to feed into reflection and the next retry."""

    working_memory_summary: str
    latest_sandbox_evidence: str
    retry_context: str


class AssembleRecursiveWorkspaceContextModule(dspy.Module):
    """Select bounded Daytona-backed memory/evidence for the next recursive pass."""

    def __init__(self, *, predictor: Any | None = None) -> None:
        super().__init__()
        self.predictor = predictor or dspy.ChainOfThought(
            AssembleRecursiveWorkspaceContext
        )

    def forward(
        self,
        *,
        user_request: str,
        current_plan: str,
        loop_state: str,
        working_memory_catalog: list[str],
        recent_sandbox_evidence_catalog: list[str],
        latest_tool_or_code_result: str,
        context_budget: int,
    ) -> dspy.Prediction:
        prediction = self.predictor(
            user_request=user_request,
            current_plan=current_plan,
            loop_state=loop_state,
            working_memory_catalog=working_memory_catalog,
            recent_sandbox_evidence_catalog=recent_sandbox_evidence_catalog,
            latest_tool_or_code_result=latest_tool_or_code_result,
            context_budget=context_budget,
        )
        return dspy.Prediction(
            **asdict(
                coerce_recursive_context_selection_decision(
                    prediction,
                    working_memory_catalog=working_memory_catalog,
                    recent_sandbox_evidence_catalog=recent_sandbox_evidence_catalog,
                    latest_tool_or_code_result=latest_tool_or_code_result,
                    context_budget=context_budget,
                )
            )
        )


def coerce_recursive_context_selection_decision(
    prediction: Any,
    *,
    working_memory_catalog: list[str],
    recent_sandbox_evidence_catalog: list[str],
    latest_tool_or_code_result: str,
    context_budget: int,
) -> RecursiveContextSelectionDecision:
    """Normalize dict-like or attribute-like recursive context selection output."""

    if isinstance(prediction, dict):
        get_prediction_field = prediction.get
    else:

        def get_prediction_field(name: str, default: Any = None) -> Any:
            return getattr(prediction, name, default)

    normalized_memory_catalog = _normalize_catalog(working_memory_catalog)
    normalized_evidence_catalog = _normalize_catalog(recent_sandbox_evidence_catalog)
    selected_memory_handles = _coerce_string_list(
        get_prediction_field("selected_memory_handles", []),
        valid_options=[_catalog_id(entry) for entry in normalized_memory_catalog],
    )
    selected_evidence_ids = _coerce_string_list(
        get_prediction_field("selected_evidence_ids", []),
        valid_options=[_catalog_id(entry) for entry in normalized_evidence_catalog],
    )
    selected_memory_entries = _materialize_selected_entries(
        normalized_memory_catalog, selected_memory_handles
    )
    selected_evidence_entries = _materialize_selected_entries(
        normalized_evidence_catalog, selected_evidence_ids
    )
    summary_limit = max(400, min(int(context_budget), _MAX_CONTEXT_BUDGET))
    assembled_context_summary = _compact_text(
        get_prediction_field("assembled_context_summary", ""),
        limit=summary_limit,
    )
    if not assembled_context_summary:
        assembled_context_summary = _fallback_context_summary(
            selected_memory_entries=selected_memory_entries,
            selected_evidence_entries=selected_evidence_entries,
            latest_tool_or_code_result=_compact_text(
                latest_tool_or_code_result, limit=300
            ),
            context_budget=summary_limit,
        )
    omission_rationale = (
        _compact_text(
            get_prediction_field("omission_rationale", ""),
            limit=400,
        )
        or _DEFAULT_OMISSION_RATIONALE
    )
    return RecursiveContextSelectionDecision(
        selected_memory_handles=selected_memory_handles,
        selected_evidence_ids=selected_evidence_ids,
        assembled_context_summary=assembled_context_summary,
        omission_rationale=omission_rationale,
    )


def build_recursive_context_selection_inputs(
    *,
    user_request: str,
    current_plan: str,
    latest_result: dict[str, Any],
    runtime_metadata: dict[str, Any] | None,
    recursion_depth: int,
    max_depth: int,
    reflection_passes: int,
    fallback_used: bool,
    context_budget: int,
    interpreter_context_paths: list[str] | None = None,
) -> RecursiveContextSelectionInputs:
    """Build bounded catalogs from Daytona-backed handles and recent evidence only."""

    metadata = runtime_metadata if isinstance(runtime_metadata, dict) else {}
    memory_catalog: list[str] = []
    for key, description in (
        ("volume_name", "Daytona durable volume handle"),
        ("workspace_path", "Active Daytona sandbox workspace path"),
        ("sandbox_id", "Current Daytona sandbox handle"),
        ("memory_handle", "Bounded durable memory handle"),
    ):
        value = str(metadata.get(key, "") or "").strip()
        if value:
            memory_catalog.append(f"{key}={value} | {description}.")
    for path in interpreter_context_paths or []:
        normalized_path = str(path or "").strip()
        if normalized_path:
            memory_catalog.append(
                f"context_path={normalized_path} | Explicit staged context path."
            )

    evidence_catalog: list[str] = []
    trajectory = latest_result.get("trajectory")
    if trajectory:
        evidence_catalog.append(
            f"trajectory | {_compact_text(trajectory, limit=_MAX_CATALOG_ENTRY_CHARS)}"
        )
    for key in ("final_reasoning", "answer", "assistant_response", "error", "status"):
        value = latest_result.get(key)
        if value:
            evidence_catalog.append(
                f"{key} | {_compact_text(value, limit=_MAX_CATALOG_ENTRY_CHARS)}"
            )
    for key in ("runtime_failure_category", "runtime_failure_phase"):
        value = metadata.get(key)
        if value:
            evidence_catalog.append(
                f"{key} | {_compact_text(value, limit=_MAX_CATALOG_ENTRY_CHARS)}"
            )

    normalized_budget = max(400, min(int(context_budget), _MAX_CONTEXT_BUDGET))
    latest_result_summary = (
        _compact_text(
            latest_result.get("answer")
            or latest_result.get("assistant_response")
            or latest_result.get("error")
            or "",
            limit=800,
        )
        or "No recent tool result was recorded."
    )
    return RecursiveContextSelectionInputs(
        user_request=_compact_text(user_request, limit=800),
        current_plan=_compact_text(current_plan, limit=800),
        loop_state=(
            f"recursion_depth={recursion_depth}; "
            f"max_depth={max_depth}; "
            f"reflection_passes={reflection_passes}; "
            f"delegate_lm_fallback={fallback_used}; "
            f"latest_status={latest_result.get('status', 'ok')}"
        ),
        working_memory_catalog=_normalize_catalog(memory_catalog),
        recent_sandbox_evidence_catalog=_normalize_catalog(evidence_catalog),
        latest_tool_or_code_result=latest_result_summary,
        context_budget=normalized_budget,
    )


def materialize_recursive_context(
    *,
    inputs: RecursiveContextSelectionInputs,
    decision: RecursiveContextSelectionDecision,
) -> MaterializedRecursiveContext:
    """Build bounded reflection/retry context from selected handles and evidence only."""

    selected_memory_entries = _materialize_selected_entries(
        inputs.working_memory_catalog,
        decision.selected_memory_handles,
    )
    selected_evidence_entries = _materialize_selected_entries(
        inputs.recent_sandbox_evidence_catalog,
        decision.selected_evidence_ids,
    )
    working_memory_summary = (
        "\n".join(selected_memory_entries)
        or "No additional Daytona-backed memory handles were selected."
    )
    latest_sandbox_evidence = "\n".join(
        part
        for part in (
            decision.assembled_context_summary,
            *selected_evidence_entries,
            (
                f"Omitted context: {decision.omission_rationale}"
                if decision.omission_rationale
                else ""
            ),
        )
        if part
    )
    retry_context = "\n\n".join(
        part
        for part in (
            "Recursive context assembly:",
            decision.assembled_context_summary,
            (
                "Selected Daytona-backed memory handles:\n"
                + "\n".join(f"- {entry}" for entry in selected_memory_entries)
                if selected_memory_entries
                else ""
            ),
            (
                "Selected recent evidence:\n"
                + "\n".join(f"- {entry}" for entry in selected_evidence_entries)
                if selected_evidence_entries
                else ""
            ),
            (
                f"Omitted to stay within budget: {decision.omission_rationale}"
                if decision.omission_rationale
                else ""
            ),
        )
        if part
    )
    return MaterializedRecursiveContext(
        working_memory_summary=working_memory_summary,
        latest_sandbox_evidence=latest_sandbox_evidence
        or "No additional sandbox evidence was selected.",
        retry_context=retry_context,
    )


__all__ = [
    "AssembleRecursiveWorkspaceContextModule",
    "MaterializedRecursiveContext",
    "RecursiveContextSelectionDecision",
    "RecursiveContextSelectionInputs",
    "build_recursive_context_selection_inputs",
    "coerce_recursive_context_selection_decision",
    "materialize_recursive_context",
]
