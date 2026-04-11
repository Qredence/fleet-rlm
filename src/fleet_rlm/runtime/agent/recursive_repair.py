"""DSPy-native recursive repair planning helpers for the worker/runtime layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

import dspy

from .signatures import PlanRecursiveRepair

RecursiveRepairMode = Literal[
    "targeted_repair",
    "bounded_repair_loop",
    "needs_more_recursion",
    "needs_human_review",
    "no_repair",
]

_ALLOWED_MODES: tuple[RecursiveRepairMode, ...] = (
    "targeted_repair",
    "bounded_repair_loop",
    "needs_more_recursion",
    "needs_human_review",
    "no_repair",
)
_MAX_REQUEST_CHARS = 800
_MAX_CONTEXT_CHARS = 1200
_MAX_SUMMARY_CHARS = 900
_MAX_SIGNAL_CHARS = 900
_MAX_TARGET_CHARS = 220
_MAX_STEP_CHARS = 220
_MAX_STEPS = 4
_MAX_SUBQUERIES = 3
_MAX_SUBQUERY_CHARS = 240
_MAX_REPAIR_BUDGET = 3
_DEFAULT_REPAIR_TARGET = (
    "Inspect the narrow failing workspace step before broadening recursion."
)
_DEFAULT_REPAIR_RATIONALE = (
    "Keep semantic repair planning in DSPy while Python/runtime executes only a "
    "bounded Daytona-backed repair attempt."
)


def _compact_text(value: Any, *, limit: int = _MAX_SUMMARY_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _coerce_mode(value: Any) -> RecursiveRepairMode:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in _ALLOWED_MODES:
        return cast(RecursiveRepairMode, normalized)
    return "no_repair"


def _coerce_string_list(
    value: Any,
    *,
    limit: int,
    item_limit: int,
) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_items = [str(item or "").strip() for item in value]
    else:
        raw_text = str(value or "").strip()
        raw_items = [
            part.strip()
            for part in raw_text.replace("\r", "\n").replace("\n", ",").split(",")
        ]

    selected: list[str] = []
    for item in raw_items:
        compact_item = _compact_text(item, limit=item_limit)
        if not compact_item or compact_item in selected:
            continue
        selected.append(compact_item)
        if len(selected) >= limit:
            break
    return selected


def _fallback_steps(
    *,
    repair_target: str,
    latest_failure_signals: str,
) -> list[str]:
    steps = [
        f"Inspect the narrow repair target: {repair_target}",
        "Apply only the minimum bounded repair inside the existing Daytona-backed loop.",
    ]
    if latest_failure_signals:
        steps.append(
            _compact_text(
                f"Re-check the latest failure signals after the repair: {latest_failure_signals}",
                limit=_MAX_STEP_CHARS,
            )
        )
    return [_compact_text(step, limit=_MAX_STEP_CHARS) for step in steps[:_MAX_STEPS]]


@dataclass(frozen=True, slots=True)
class RecursiveRepairInputs:
    """Typed summary-only input for recursive repair planning."""

    user_request: str
    assembled_recursive_context: str
    verification_summary: str
    latest_sandbox_evidence: str
    latest_failure_signals: str
    repair_budget: int

    def as_kwargs(self) -> dict[str, Any]:
        return {
            "user_request": self.user_request,
            "assembled_recursive_context": self.assembled_recursive_context,
            "verification_summary": self.verification_summary,
            "latest_sandbox_evidence": self.latest_sandbox_evidence,
            "latest_failure_signals": self.latest_failure_signals,
            "repair_budget": self.repair_budget,
        }


@dataclass(frozen=True, slots=True)
class RecursiveRepairDecision:
    """Typed normalized output for recursive repair planning."""

    repair_mode: RecursiveRepairMode
    repair_target: str
    repair_steps: list[str]
    repair_subqueries: list[str]
    repair_rationale: str


class PlanRecursiveRepairModule(dspy.Module):
    """Plan a bounded recursive repair without owning execution or batching."""

    def __init__(self, *, predictor: Any | None = None) -> None:
        super().__init__()
        self.predictor = predictor or dspy.ChainOfThought(PlanRecursiveRepair)

    def forward(
        self,
        *,
        user_request: str,
        assembled_recursive_context: str,
        verification_summary: str,
        latest_sandbox_evidence: str,
        latest_failure_signals: str,
        repair_budget: int,
    ) -> dspy.Prediction:
        """Normalize summary-only repair inputs into a bounded repair decision.

        All string inputs should already be compact Daytona-backed summaries rather
        than raw durable memory or full execution state payloads.
        """
        prediction = self.predictor(
            user_request=user_request,
            assembled_recursive_context=assembled_recursive_context,
            verification_summary=verification_summary,
            latest_sandbox_evidence=latest_sandbox_evidence,
            latest_failure_signals=latest_failure_signals,
            repair_budget=repair_budget,
        )
        return dspy.Prediction(
            **asdict(
                coerce_recursive_repair_decision(
                    prediction,
                    latest_failure_signals=latest_failure_signals,
                    repair_budget=repair_budget,
                )
            )
        )


def coerce_recursive_repair_decision(
    prediction: Any,
    *,
    latest_failure_signals: str,
    repair_budget: int,
) -> RecursiveRepairDecision:
    """Normalize dict-like or attribute-like recursive repair output."""

    if isinstance(prediction, dict):
        get_prediction_field = prediction.get
    else:

        def get_prediction_field(name: str, default: Any = None) -> Any:
            return getattr(prediction, name, default)

    normalized_budget = max(1, min(int(repair_budget), _MAX_REPAIR_BUDGET))
    repair_target = (
        _compact_text(
            get_prediction_field("repair_target", ""), limit=_MAX_TARGET_CHARS
        )
        or _DEFAULT_REPAIR_TARGET
    )
    repair_steps = _coerce_string_list(
        get_prediction_field("repair_steps", []),
        limit=normalized_budget + 1,
        item_limit=_MAX_STEP_CHARS,
    )
    if not repair_steps:
        repair_steps = _fallback_steps(
            repair_target=repair_target,
            latest_failure_signals=_compact_text(
                latest_failure_signals, limit=_MAX_STEP_CHARS
            ),
        )
    repair_subqueries = _coerce_string_list(
        get_prediction_field("repair_subqueries", []),
        limit=min(normalized_budget, _MAX_SUBQUERIES),
        item_limit=_MAX_SUBQUERY_CHARS,
    )
    return RecursiveRepairDecision(
        repair_mode=_coerce_mode(get_prediction_field("repair_mode", "no_repair")),
        repair_target=repair_target,
        repair_steps=repair_steps,
        repair_subqueries=repair_subqueries,
        repair_rationale=_compact_text(
            get_prediction_field("repair_rationale", ""),
            limit=800,
        )
        or _DEFAULT_REPAIR_RATIONALE,
    )


def build_recursive_repair_inputs(
    *,
    user_request: str,
    assembled_recursive_context: str,
    latest_result: dict[str, Any],
    runtime_metadata: dict[str, Any] | None,
    reflection_decision: Any,
    repair_budget: int,
    recursion_depth: int,
    max_depth: int,
) -> RecursiveRepairInputs:
    """Build summary-only recursive repair inputs from Daytona-backed worker state."""

    metadata = runtime_metadata if isinstance(runtime_metadata, dict) else {}
    verification_payload = latest_result.get("recursive_verification")
    verification = (
        verification_payload if isinstance(verification_payload, dict) else {}
    )

    verification_parts = [
        _compact_text(verification.get("verified_summary", ""), limit=320),
        (
            "verification_status="
            + _compact_text(verification.get("verification_status", ""), limit=120)
            if verification.get("verification_status")
            else ""
        ),
        _compact_text(verification.get("verification_rationale", ""), limit=240),
        (
            "reflection_next_action="
            + _compact_text(getattr(reflection_decision, "next_action", ""), limit=120)
            if getattr(reflection_decision, "next_action", "")
            else ""
        ),
        _compact_text(getattr(reflection_decision, "rationale", ""), limit=240),
        (
            "reflection_revised_plan="
            + _compact_text(getattr(reflection_decision, "revised_plan", ""), limit=240)
            if getattr(reflection_decision, "revised_plan", "")
            else ""
        ),
        _compact_text(latest_result.get("final_reasoning", ""), limit=240),
    ]
    failure_parts = [
        (
            "missing_evidence="
            + "; ".join(
                _compact_text(item, limit=120)
                for item in verification.get("missing_evidence", []) or []
            )
            if verification.get("missing_evidence")
            else ""
        ),
        (
            "contradictions="
            + "; ".join(
                _compact_text(item, limit=120)
                for item in verification.get("contradictions", []) or []
            )
            if verification.get("contradictions")
            else ""
        ),
        (
            "latest_error=" + _compact_text(latest_result.get("error", ""), limit=180)
            if latest_result.get("error")
            else ""
        ),
        (
            "runtime_failure_category="
            + _compact_text(metadata.get("runtime_failure_category", ""), limit=120)
            if metadata.get("runtime_failure_category")
            else ""
        ),
        (
            "runtime_failure_phase="
            + _compact_text(metadata.get("runtime_failure_phase", ""), limit=120)
            if metadata.get("runtime_failure_phase")
            else ""
        ),
        f"latest_status={_compact_text(latest_result.get('status', 'ok'), limit=40)}",
        (
            f"recursion_depth={recursion_depth}; max_depth={max_depth}; "
            f"repair_budget={max(1, min(int(repair_budget), _MAX_REPAIR_BUDGET))}"
        ),
    ]
    evidence_parts: list[str] = []
    for key in (
        "volume_name",
        "workspace_path",
        "sandbox_id",
        "memory_handle",
        "runtime_failure_category",
        "runtime_failure_phase",
    ):
        value = str(metadata.get(key, "") or "").strip()
        if value:
            evidence_parts.append(f"{key}={value}")
    trajectory = latest_result.get("trajectory")
    if trajectory:
        evidence_parts.append(
            "trajectory=" + _compact_text(trajectory, limit=_MAX_STEP_CHARS)
        )
    answer_summary = latest_result.get("answer") or latest_result.get(
        "assistant_response"
    )
    if answer_summary:
        evidence_parts.append(
            "latest_result=" + _compact_text(answer_summary, limit=_MAX_STEP_CHARS)
        )

    return RecursiveRepairInputs(
        user_request=_compact_text(user_request, limit=_MAX_REQUEST_CHARS),
        assembled_recursive_context=_compact_text(
            assembled_recursive_context
            or "No additional recursive context was assembled for repair planning.",
            limit=_MAX_CONTEXT_CHARS,
        ),
        verification_summary=_compact_text(
            "\n".join(part for part in verification_parts if part)
            or "Verification did not produce additional repair guidance.",
            limit=_MAX_SUMMARY_CHARS,
        ),
        latest_sandbox_evidence=_compact_text(
            "; ".join(evidence_parts)
            or "No fresh sandbox evidence was captured for recursive repair planning.",
            limit=_MAX_SUMMARY_CHARS,
        ),
        latest_failure_signals=_compact_text(
            "\n".join(part for part in failure_parts if part)
            or "No explicit failure signals were captured.",
            limit=_MAX_SIGNAL_CHARS,
        ),
        repair_budget=max(1, min(int(repair_budget), _MAX_REPAIR_BUDGET)),
    )


def build_recursive_repair_retry_context(
    *,
    original_context: str,
    assembled_recursive_context: str,
    decision: RecursiveRepairDecision,
) -> str:
    """Build a bounded repair execution context for the next Daytona-backed pass."""

    parts = [
        str(assembled_recursive_context or "").strip()
        or str(original_context or "").strip(),
        "Recursive repair plan:",
        f"repair_mode={decision.repair_mode}",
        f"repair_target={decision.repair_target}",
        "repair_steps=" + "; ".join(decision.repair_steps),
        (
            "repair_subqueries=" + "; ".join(decision.repair_subqueries)
            if decision.repair_subqueries
            else ""
        ),
        f"repair_rationale={decision.repair_rationale}",
        (
            "Apply only the bounded repair plan in Python/runtime inside the current Daytona-backed loop."
        ),
    ]
    return "\n".join(part for part in parts if part)


def append_recursive_repair_summary(
    result: dict[str, Any],
    decision: RecursiveRepairDecision,
) -> dict[str, Any]:
    """Attach internal recursive-repair metadata and rationale."""

    updated = dict(result)
    updated["recursive_repair"] = asdict(decision)
    note_parts = [
        f"Recursive repair planning chose {decision.repair_mode}.",
        f"Repair target: {decision.repair_target}",
        decision.repair_rationale,
    ]
    if decision.repair_steps:
        note_parts.append("Repair steps: " + "; ".join(decision.repair_steps))
    if decision.repair_subqueries:
        note_parts.append("Repair subqueries: " + "; ".join(decision.repair_subqueries))
    existing = str(updated.get("final_reasoning", "") or "").strip()
    updated["final_reasoning"] = "\n".join(
        part for part in (existing, " ".join(note_parts).strip()) if part
    )
    return updated


__all__ = [
    "PlanRecursiveRepairModule",
    "RecursiveRepairDecision",
    "RecursiveRepairInputs",
    "RecursiveRepairMode",
    "append_recursive_repair_summary",
    "build_recursive_repair_inputs",
    "build_recursive_repair_retry_context",
    "coerce_recursive_repair_decision",
]
