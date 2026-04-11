"""DSPy-native recursive decomposition helpers for the worker/runtime layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

import dspy

from .signatures import PlanRecursiveSubqueries

RecursiveDecompositionMode = Literal["single_pass", "fan_out"]
_ALLOWED_MODES: tuple[RecursiveDecompositionMode, ...] = (
    "single_pass",
    "fan_out",
)
_MAX_SUMMARY_CHARS = 800
_MAX_CONTEXT_CHARS = 1200
_MAX_SUBQUERY_CHARS = 320
_MAX_SUBQUERY_BUDGET = 4
_DEFAULT_BATCHING_STRATEGY = "serial"
_DEFAULT_AGGREGATION_PLAN = (
    "Aggregate the bounded subquery results in Python without moving durable memory "
    "or execution state into orchestration state."
)
_DEFAULT_DECOMPOSITION_RATIONALE = (
    "Keep the next recursive pass bounded and let Python manage batching, parsing, "
    "and aggregation inside the Daytona-backed worker."
)


def _compact_text(value: Any, *, limit: int = _MAX_SUMMARY_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _coerce_mode(value: Any, *, subquery_count: int) -> RecursiveDecompositionMode:
    mode = str(value or "").strip().lower().replace("-", "_")
    if mode in _ALLOWED_MODES:
        return cast(RecursiveDecompositionMode, mode)
    return "fan_out" if subquery_count > 1 else "single_pass"


def _coerce_batching_strategy(value: Any, *, subquery_count: int) -> str:
    strategy = str(value or "").strip().lower().replace("-", "_")
    normalized = {
        "single": "serial",
        "single_pass": "serial",
        "serial": "serial",
        "sequential": "serial",
        "batch": "batched",
        "batched": "batched",
        "parallel": "batched",
        "parallelizable": "batched",
    }.get(strategy)
    if normalized:
        return normalized
    return "batched" if subquery_count > 1 else _DEFAULT_BATCHING_STRATEGY


def _coerce_subqueries(
    value: Any,
    *,
    fallback_query: str,
    subquery_budget: int,
) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_items = [str(item or "").strip() for item in value]
    else:
        raw_text = str(value or "").strip()
        raw_items = [
            part.strip()
            for part in raw_text.replace("\r", "\n").replace("\n", ",").split(",")
        ]

    normalized_budget = max(1, min(int(subquery_budget), _MAX_SUBQUERY_BUDGET))
    subqueries: list[str] = []
    for item in raw_items:
        compact_item = _compact_text(item, limit=_MAX_SUBQUERY_CHARS)
        if not compact_item or compact_item in subqueries:
            continue
        subqueries.append(compact_item)
        if len(subqueries) >= normalized_budget:
            break

    if subqueries:
        return subqueries
    return [_compact_text(fallback_query, limit=_MAX_SUBQUERY_CHARS)]


@dataclass(frozen=True, slots=True)
class RecursiveDecompositionInputs:
    """Typed input for recursive decomposition planning."""

    user_request: str
    assembled_recursive_context: str
    current_plan: str
    loop_state: str
    latest_sandbox_evidence: str
    subquery_budget: int

    def as_kwargs(self) -> dict[str, Any]:
        return {
            "user_request": self.user_request,
            "assembled_recursive_context": self.assembled_recursive_context,
            "current_plan": self.current_plan,
            "loop_state": self.loop_state,
            "latest_sandbox_evidence": self.latest_sandbox_evidence,
            "subquery_budget": self.subquery_budget,
        }


@dataclass(frozen=True, slots=True)
class RecursiveDecompositionDecision:
    """Typed normalized output for recursive decomposition planning."""

    decomposition_mode: RecursiveDecompositionMode
    subqueries: list[str]
    batching_strategy: str
    aggregation_plan: str
    decomposition_rationale: str


class PlanRecursiveSubqueriesModule(dspy.Module):
    """Narrow DSPy module for recursive decomposition into bounded subqueries."""

    def __init__(self, *, predictor: Any | None = None) -> None:
        super().__init__()
        self.predictor = predictor or dspy.ChainOfThought(PlanRecursiveSubqueries)

    def forward(
        self,
        *,
        user_request: str,
        assembled_recursive_context: str,
        current_plan: str,
        loop_state: str,
        latest_sandbox_evidence: str,
        subquery_budget: int,
    ) -> dspy.Prediction:
        prediction = self.predictor(
            user_request=user_request,
            assembled_recursive_context=assembled_recursive_context,
            current_plan=current_plan,
            loop_state=loop_state,
            latest_sandbox_evidence=latest_sandbox_evidence,
            subquery_budget=subquery_budget,
        )
        return dspy.Prediction(
            **asdict(
                coerce_recursive_decomposition_decision(
                    prediction,
                    fallback_query=user_request,
                    subquery_budget=subquery_budget,
                )
            )
        )


def coerce_recursive_decomposition_decision(
    prediction: Any,
    *,
    fallback_query: str,
    subquery_budget: int,
) -> RecursiveDecompositionDecision:
    """Normalize dict-like or attribute-like recursive decomposition output."""

    if isinstance(prediction, dict):
        get_prediction_field = prediction.get
    else:

        def get_prediction_field(name: str, default: Any = None) -> Any:
            return getattr(prediction, name, default)

    subqueries = _coerce_subqueries(
        get_prediction_field("subqueries", []),
        fallback_query=fallback_query,
        subquery_budget=subquery_budget,
    )
    return RecursiveDecompositionDecision(
        decomposition_mode=_coerce_mode(
            get_prediction_field("decomposition_mode", "single_pass"),
            subquery_count=len(subqueries),
        ),
        subqueries=subqueries,
        batching_strategy=_coerce_batching_strategy(
            get_prediction_field("batching_strategy", _DEFAULT_BATCHING_STRATEGY),
            subquery_count=len(subqueries),
        ),
        aggregation_plan=_compact_text(
            get_prediction_field("aggregation_plan", ""),
            limit=600,
        )
        or _DEFAULT_AGGREGATION_PLAN,
        decomposition_rationale=_compact_text(
            get_prediction_field("decomposition_rationale", ""),
            limit=600,
        )
        or _DEFAULT_DECOMPOSITION_RATIONALE,
    )


def build_recursive_decomposition_inputs(
    *,
    user_request: str,
    current_plan: str,
    assembled_recursive_context: str,
    runtime_metadata: dict[str, Any] | None,
    recursion_depth: int,
    max_depth: int,
    fallback_used: bool,
    subquery_budget: int,
    interpreter_context_paths: list[str] | None = None,
) -> RecursiveDecompositionInputs:
    """Build summary-only decomposition inputs from Daytona-backed handles/state."""

    metadata = runtime_metadata if isinstance(runtime_metadata, dict) else {}
    context_parts = [str(assembled_recursive_context or "").strip()]
    evidence_parts: list[str] = []
    for key in ("volume_name", "workspace_path", "sandbox_id", "memory_handle"):
        value = str(metadata.get(key, "") or "").strip()
        if value:
            evidence_parts.append(f"{key}={value}")
    for path in interpreter_context_paths or []:
        normalized_path = str(path or "").strip()
        if normalized_path:
            evidence_parts.append(f"context_path={normalized_path}")
    if evidence_parts:
        context_parts.append(
            "Available Daytona-backed handles: " + "; ".join(evidence_parts)
        )

    return RecursiveDecompositionInputs(
        user_request=_compact_text(user_request, limit=800),
        assembled_recursive_context=_compact_text(
            "\n".join(part for part in context_parts if part),
            limit=_MAX_CONTEXT_CHARS,
        )
        or "No additional recursive context was assembled for this pass.",
        current_plan=_compact_text(current_plan or user_request, limit=800),
        loop_state=(
            f"recursion_depth={recursion_depth}; "
            f"max_depth={max_depth}; "
            f"delegate_lm_fallback={fallback_used}; "
            f"subquery_budget={max(1, min(int(subquery_budget), _MAX_SUBQUERY_BUDGET))}"
        ),
        latest_sandbox_evidence=_compact_text(
            "; ".join(evidence_parts),
            limit=800,
        )
        or "No fresh sandbox evidence is available before this recursive pass.",
        subquery_budget=max(1, min(int(subquery_budget), _MAX_SUBQUERY_BUDGET)),
    )


__all__ = [
    "PlanRecursiveSubqueriesModule",
    "RecursiveDecompositionDecision",
    "RecursiveDecompositionInputs",
    "RecursiveDecompositionMode",
    "build_recursive_decomposition_inputs",
    "coerce_recursive_decomposition_decision",
]
