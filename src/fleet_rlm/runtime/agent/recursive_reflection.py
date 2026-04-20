"""DSPy-native recursive reflection helpers for the worker/runtime layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

import dspy

from fleet_rlm.utils.text import compact_text as _compact_text

from .signatures import ReflectAndReviseWorkspaceStep

RecursiveReflectionAction = Literal[
    "recurse",
    "finalize",
    "request_human_review",
    "repair_and_retry",
]

_ALLOWED_ACTIONS: tuple[RecursiveReflectionAction, ...] = (
    "recurse",
    "finalize",
    "request_human_review",
    "repair_and_retry",
)
_MAX_SUMMARY_CHARS = 600


def _coerce_confidence(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(1.0, score))


def _coerce_action(value: Any) -> RecursiveReflectionAction:
    action = str(value or "").strip().lower()
    if action in _ALLOWED_ACTIONS:
        return cast(RecursiveReflectionAction, action)
    return "finalize"


def _format_kv_pairs(source: dict[str, Any], *keys: str) -> list[str]:
    """Return 'key=value' strings from *source* for present, non-empty values."""
    return [f"{key}={value}" for key in keys if (value := source.get(key))]


@dataclass(frozen=True, slots=True)
class WorkspaceReflectionInputs:
    """Typed input for the recursive reflection module."""

    user_request: str
    working_memory_summary: str
    current_plan: str
    latest_sandbox_evidence: str
    latest_tool_or_code_result: str
    loop_state: str

    def as_kwargs(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


@dataclass(frozen=True, slots=True)
class WorkspaceReflectionDecision:
    """Typed normalized output for the recursive reflection module."""

    next_action: RecursiveReflectionAction
    revised_plan: str
    rationale: str
    confidence: float


class ReflectAndReviseWorkspaceStepModule(dspy.Module):
    """Narrow DSPy module for one recursive revise/recurse/finalize decision."""

    def __init__(self, *, predictor: Any | None = None) -> None:
        super().__init__()
        self.predictor = predictor or dspy.ChainOfThought(ReflectAndReviseWorkspaceStep)

    def forward(
        self,
        *,
        user_request: str,
        working_memory_summary: str,
        current_plan: str,
        latest_sandbox_evidence: str,
        latest_tool_or_code_result: str,
        loop_state: str,
    ) -> dspy.Prediction:
        prediction = self.predictor(
            user_request=user_request,
            working_memory_summary=working_memory_summary,
            current_plan=current_plan,
            latest_sandbox_evidence=latest_sandbox_evidence,
            latest_tool_or_code_result=latest_tool_or_code_result,
            loop_state=loop_state,
        )
        return dspy.Prediction(
            next_action=_coerce_action(getattr(prediction, "next_action", "finalize")),
            revised_plan=_compact_text(
                getattr(prediction, "revised_plan", ""), limit=800
            ),
            rationale=_compact_text(getattr(prediction, "rationale", ""), limit=800),
            confidence=_coerce_confidence(getattr(prediction, "confidence", 0.0)),
        )

    async def adecide(
        self,
        inputs: WorkspaceReflectionInputs,
    ) -> WorkspaceReflectionDecision:
        prediction = await self.acall(**inputs.as_kwargs())
        return coerce_workspace_reflection_decision(prediction)


def coerce_workspace_reflection_decision(
    prediction: Any,
) -> WorkspaceReflectionDecision:
    """Normalize dict-like or attribute-like prediction output."""

    if isinstance(prediction, dict):
        get_prediction_field = prediction.get
    else:

        def get_prediction_field(name: str, default: Any = None) -> Any:
            return getattr(prediction, name, default)

    return WorkspaceReflectionDecision(
        next_action=_coerce_action(get_prediction_field("next_action", "finalize")),
        revised_plan=_compact_text(
            get_prediction_field("revised_plan", ""),
            limit=800,
        ),
        rationale=_compact_text(get_prediction_field("rationale", ""), limit=800),
        confidence=_coerce_confidence(get_prediction_field("confidence", 0.0)),
    )


def build_workspace_reflection_inputs(
    *,
    user_request: str,
    current_plan: str,
    latest_result: dict[str, Any],
    runtime_metadata: dict[str, Any] | None,
    recursion_depth: int,
    max_depth: int,
    reflection_passes: int,
    fallback_used: bool,
) -> WorkspaceReflectionInputs:
    """Build reflection inputs from summarized runtime metadata and latest results."""

    metadata = runtime_metadata if isinstance(runtime_metadata, dict) else {}
    verification = latest_result.get("recursive_verification")
    verification_payload = verification if isinstance(verification, dict) else {}
    working_memory_summary = (
        "\n".join(
            _format_kv_pairs(
                metadata,
                "volume_name",
                "workspace_path",
                "sandbox_id",
                "memory_handle",
            )
        )
        or "No durable memory contents were copied; only workspace handles are available."
    )

    latest_evidence = (
        "\n".join(
            part
            for part in (
                _compact_text(latest_result.get("final_reasoning", "")),
                _compact_text(latest_result.get("trajectory", "")),
                (
                    "recursive_verification_status="
                    + _compact_text(
                        verification_payload.get("verification_status", ""),
                        limit=120,
                    )
                    if verification_payload.get("verification_status")
                    else ""
                ),
                (
                    "missing_evidence="
                    + "; ".join(
                        _compact_text(item, limit=120)
                        for item in verification_payload.get("missing_evidence", [])
                        or []
                    )
                    if verification_payload.get("missing_evidence")
                    else ""
                ),
                (
                    "contradictions="
                    + "; ".join(
                        _compact_text(item, limit=120)
                        for item in verification_payload.get("contradictions", []) or []
                    )
                    if verification_payload.get("contradictions")
                    else ""
                ),
                _compact_text(verification_payload.get("verification_rationale", "")),
                _compact_text(metadata.get("runtime_failure_category", "")),
                _compact_text(metadata.get("runtime_failure_phase", "")),
            )
            if part
        )
        or "No additional sandbox evidence was captured."
    )

    latest_result_summary = _compact_text(
        verification_payload.get("verified_summary")
        or latest_result.get("answer")
        or latest_result.get("assistant_response")
        or latest_result.get("error")
        or "",
        limit=800,
    )
    loop_state = (
        f"recursion_depth={recursion_depth}; "
        f"max_depth={max_depth}; "
        f"reflection_passes={reflection_passes}; "
        f"delegate_lm_fallback={fallback_used}; "
        f"latest_status={latest_result.get('status', 'ok')}"
    )
    return WorkspaceReflectionInputs(
        user_request=_compact_text(user_request, limit=800),
        working_memory_summary=working_memory_summary,
        current_plan=_compact_text(current_plan, limit=800),
        latest_sandbox_evidence=latest_evidence,
        latest_tool_or_code_result=latest_result_summary
        or "No recent tool result was recorded.",
        loop_state=loop_state,
    )


def build_recursive_retry_prompt(
    *,
    original_prompt: str,
    original_context: str,
    decision: WorkspaceReflectionDecision,
    assembled_recursive_context: str = "",
) -> tuple[str, str]:
    """Return the prompt/context pair for one follow-up recursive pass."""

    updated_context = "\n\n".join(
        part
        for part in (
            str(assembled_recursive_context or "").strip()
            or str(original_context or "").strip(),
            "Recursive reflection guidance:",
            decision.revised_plan
            or "Re-evaluate the current workspace state before finalizing.",
            (
                "Repair the last attempt using the most recent sandbox evidence, "
                "then retry only the minimum necessary next step."
                if decision.next_action == "repair_and_retry"
                else "Continue with one additional recursive refinement step before finalizing."
            ),
        )
        if part
    )
    return str(original_prompt or "").strip(), updated_context


def append_reflection_rationale(
    result: dict[str, Any],
    decision: WorkspaceReflectionDecision,
) -> dict[str, Any]:
    """Attach reflection rationale without changing the public payload shape."""

    updated = dict(result)
    note = (
        f"Recursive reflection chose {decision.next_action} "
        f"(confidence={decision.confidence:.2f}). {decision.rationale}".strip()
    )
    existing = str(updated.get("final_reasoning", "") or "").strip()
    updated["final_reasoning"] = "\n".join(part for part in (existing, note) if part)
    return updated


__all__ = [
    "RecursiveReflectionAction",
    "ReflectAndReviseWorkspaceStepModule",
    "WorkspaceReflectionDecision",
    "WorkspaceReflectionInputs",
    "append_reflection_rationale",
    "build_recursive_retry_prompt",
    "build_workspace_reflection_inputs",
    "coerce_workspace_reflection_decision",
]
