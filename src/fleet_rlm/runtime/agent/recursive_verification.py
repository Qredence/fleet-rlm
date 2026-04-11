"""DSPy-native recursive verification helpers for bounded decomposition results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

import dspy

from .signatures import VerifyRecursiveAggregation

RecursiveVerificationStatus = Literal[
    "sufficient",
    "needs_repair",
    "needs_more_recursion",
    "needs_human_review",
]

_ALLOWED_STATUSES: tuple[RecursiveVerificationStatus, ...] = (
    "sufficient",
    "needs_repair",
    "needs_more_recursion",
    "needs_human_review",
)
_MAX_REQUEST_CHARS = 800
_MAX_CONTEXT_CHARS = 1200
_MAX_PLAN_CHARS = 900
_MAX_EVIDENCE_CHARS = 900
_MAX_OUTPUT_ROWS = 4
_MAX_OUTPUT_ROW_CHARS = 320
_MAX_LIST_ITEMS = 4
_MAX_LIST_ITEM_CHARS = 180
_DEFAULT_VERIFIED_SUMMARY = (
    "Python/runtime aggregated the bounded decomposition results, but semantic "
    "verification did not produce a stronger synthesis."
)
_DEFAULT_VERIFICATION_RATIONALE = (
    "Use the bounded verification summary to guide recursive reflection without "
    "moving durable memory or execution state out of Daytona."
)


def _compact_text(value: Any, *, limit: int = _MAX_REQUEST_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _coerce_status(value: Any) -> RecursiveVerificationStatus:
    status = str(value or "").strip().lower().replace("-", "_")
    if status in _ALLOWED_STATUSES:
        return cast(RecursiveVerificationStatus, status)
    return "sufficient"


def _coerce_string_list(value: Any, *, limit: int = _MAX_LIST_ITEMS) -> list[str]:
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
        compact_item = _compact_text(item, limit=_MAX_LIST_ITEM_CHARS)
        if not compact_item or compact_item in selected:
            continue
        selected.append(compact_item)
        if len(selected) >= limit:
            break
    return selected


@dataclass(frozen=True, slots=True)
class RecursiveVerificationInputs:
    """Typed summary-only input for recursive verification."""

    user_request: str
    assembled_recursive_context: str
    decomposition_plan_summary: str
    collected_subquery_outputs: list[str]
    latest_sandbox_evidence: str

    def as_kwargs(self) -> dict[str, Any]:
        return {
            "user_request": self.user_request,
            "assembled_recursive_context": self.assembled_recursive_context,
            "decomposition_plan_summary": self.decomposition_plan_summary,
            "collected_subquery_outputs": list(self.collected_subquery_outputs),
            "latest_sandbox_evidence": self.latest_sandbox_evidence,
        }


@dataclass(frozen=True, slots=True)
class RecursiveVerificationDecision:
    """Typed normalized output for recursive verification."""

    verification_status: RecursiveVerificationStatus
    missing_evidence: list[str]
    contradictions: list[str]
    verified_summary: str
    verification_rationale: str


class VerifyRecursiveAggregationModule(dspy.Module):
    """Semantically verify bounded decomposition-driven recursive results."""

    def __init__(self, *, predictor: Any | None = None) -> None:
        super().__init__()
        self.predictor = predictor or dspy.ChainOfThought(VerifyRecursiveAggregation)

    def forward(
        self,
        *,
        user_request: str,
        assembled_recursive_context: str,
        decomposition_plan_summary: str,
        collected_subquery_outputs: list[str],
        latest_sandbox_evidence: str,
    ) -> dspy.Prediction:
        prediction = self.predictor(
            user_request=user_request,
            assembled_recursive_context=assembled_recursive_context,
            decomposition_plan_summary=decomposition_plan_summary,
            collected_subquery_outputs=collected_subquery_outputs,
            latest_sandbox_evidence=latest_sandbox_evidence,
        )
        return dspy.Prediction(
            **asdict(
                coerce_recursive_verification_decision(
                    prediction,
                    fallback_summary=_fallback_verified_summary(
                        collected_subquery_outputs=collected_subquery_outputs
                    ),
                )
            )
        )


def _fallback_verified_summary(*, collected_subquery_outputs: list[str]) -> str:
    if collected_subquery_outputs:
        return _compact_text(
            "\n".join(collected_subquery_outputs),
            limit=800,
        )
    return _DEFAULT_VERIFIED_SUMMARY


def coerce_recursive_verification_decision(
    prediction: Any,
    *,
    fallback_summary: str,
) -> RecursiveVerificationDecision:
    """Normalize dict-like or attribute-like recursive verification output."""

    if isinstance(prediction, dict):
        get_prediction_field = prediction.get
    else:

        def get_prediction_field(name: str, default: Any = None) -> Any:
            return getattr(prediction, name, default)

    return RecursiveVerificationDecision(
        verification_status=_coerce_status(
            get_prediction_field("verification_status", "sufficient")
        ),
        missing_evidence=_coerce_string_list(
            get_prediction_field("missing_evidence", [])
        ),
        contradictions=_coerce_string_list(get_prediction_field("contradictions", [])),
        verified_summary=_compact_text(
            get_prediction_field("verified_summary", ""),
            limit=800,
        )
        or _compact_text(fallback_summary, limit=800)
        or _DEFAULT_VERIFIED_SUMMARY,
        verification_rationale=_compact_text(
            get_prediction_field("verification_rationale", ""),
            limit=800,
        )
        or _DEFAULT_VERIFICATION_RATIONALE,
    )


def build_recursive_verification_inputs(
    *,
    user_request: str,
    assembled_recursive_context: str,
    decomposition_decision: Any,
    results: list[dict[str, Any]],
    runtime_metadata: dict[str, Any] | None,
    interpreter_context_paths: list[str] | None = None,
) -> RecursiveVerificationInputs:
    """Build bounded verification inputs from Python/runtime aggregation only."""

    metadata = runtime_metadata if isinstance(runtime_metadata, dict) else {}
    plan_parts = [
        f"decomposition_mode={getattr(decomposition_decision, 'decomposition_mode', 'single_pass')}",
        f"batching_strategy={getattr(decomposition_decision, 'batching_strategy', 'serial')}",
        f"aggregation_plan={_compact_text(getattr(decomposition_decision, 'aggregation_plan', ''), limit=320)}",
        f"decomposition_rationale={_compact_text(getattr(decomposition_decision, 'decomposition_rationale', ''), limit=320)}",
    ]
    output_rows: list[str] = []
    for index, item in enumerate(results[:_MAX_OUTPUT_ROWS], start=1):
        output_rows.append(
            _compact_text(
                "\n".join(
                    part
                    for part in (
                        f"[{index}] {str(item.get('subquery', '') or '').strip()}",
                        f"status={str(item.get('status', 'ok') or 'ok').strip()}",
                        f"answer={str(item.get('answer') or item.get('assistant_response') or '').strip()}",
                        f"reasoning={str(item.get('final_reasoning', '') or '').strip()}",
                    )
                    if part
                ),
                limit=_MAX_OUTPUT_ROW_CHARS,
            )
        )

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
    for path in interpreter_context_paths or []:
        normalized_path = str(path or "").strip()
        if normalized_path:
            evidence_parts.append(f"context_path={normalized_path}")

    return RecursiveVerificationInputs(
        user_request=_compact_text(user_request, limit=_MAX_REQUEST_CHARS),
        assembled_recursive_context=_compact_text(
            assembled_recursive_context
            or "No additional recursive context was assembled for verification.",
            limit=_MAX_CONTEXT_CHARS,
        ),
        decomposition_plan_summary=_compact_text(
            "\n".join(part for part in plan_parts if part),
            limit=_MAX_PLAN_CHARS,
        ),
        collected_subquery_outputs=output_rows
        or ["No bounded subquery outputs were collected for verification."],
        latest_sandbox_evidence=_compact_text(
            "; ".join(evidence_parts)
            or "No fresh sandbox evidence was captured for recursive verification.",
            limit=_MAX_EVIDENCE_CHARS,
        ),
    )


def append_recursive_verification_summary(
    result: dict[str, Any],
    decision: RecursiveVerificationDecision,
) -> dict[str, Any]:
    """Return a copy of ``result`` with internal recursive-verification metadata.

    This helper adds a top-level ``recursive_verification`` field containing the
    serialized :class:`RecursiveVerificationDecision` for downstream runtime or
    delegate-result consumers, and appends a human-readable verification note to
    ``final_reasoning``. It therefore does change the shape of the returned
    mapping at this internal layer.
    """
    updated = dict(result)
    updated["recursive_verification"] = asdict(decision)
    note_parts = [
        "Recursive verification assessed the aggregated subquery results as "
        f"{decision.verification_status}.",
        decision.verification_rationale,
    ]
    if decision.missing_evidence:
        note_parts.append("Missing evidence: " + "; ".join(decision.missing_evidence))
    if decision.contradictions:
        note_parts.append("Contradictions: " + "; ".join(decision.contradictions))
    if decision.verified_summary:
        note_parts.append("Verified summary: " + decision.verified_summary)
    note = " ".join(part.strip() for part in note_parts if part).strip()
    existing = str(updated.get("final_reasoning", "") or "").strip()
    updated["final_reasoning"] = "\n".join(part for part in (existing, note) if part)
    return updated


__all__ = [
    "RecursiveVerificationDecision",
    "RecursiveVerificationInputs",
    "RecursiveVerificationStatus",
    "VerifyRecursiveAggregationModule",
    "append_recursive_verification_summary",
    "build_recursive_verification_inputs",
    "coerce_recursive_verification_decision",
]
