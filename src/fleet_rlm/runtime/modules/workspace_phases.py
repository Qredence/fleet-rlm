"""Composable per-phase DSPy modules for the recursive workspace orchestrator.

Each phase of the multi-pass loop — assemble context, plan subqueries,
execute, verify, reflect/revise, repair — is its own small ``dspy.Module``
wrapping one runtime RLM (or, for execution, the sandbox delegation tools).
This keeps every phase independently testable and GEPA-optimizable while the
parent :class:`~fleet_rlm.runtime.modules.workspace.RecursiveWorkspaceModule`
stays a thin orchestrator.
"""

from __future__ import annotations

import json
from typing import Any

import dspy

from fleet_rlm.runtime.agent.signatures import (
    AssembleRecursiveWorkspaceContext,
    PlanRecursiveRepair,
    PlanRecursiveSubqueries,
    ReflectAndReviseWorkspaceStep,
    VerifyRecursiveAggregation,
)
from fleet_rlm.runtime.modules.factory import create_runtime_rlm

_MISSING_SOURCE_FAILURE_MARKERS = (
    "codebase not available",
    "codebase is not available",
    "empty workspace",
    "no source code available",
    "not present in the sandbox",
    "repository files are not present",
    "repository is not present",
    "repository is not cloned",
    "source code is not available",
    "workspace is empty",
)
_SUBQUERY_FAILURE_MARKERS = (
    "adapterparseerror",
    "broker server failed",
    "broker_unavailable",
    "budget_exhausted",
    "child_error",
    "failed to parse the lm response",
    "failed to inject tool",
    "host callback",
    "lm response cannot be serialized to a json object",
    "needs_human_review",
    "null_answer",
    "sandbox limitations",
    "sandbox limit",
    "tool_error",
    "unable to create sandboxes",
    "unverified",
    "expected to find output fields",
    "verification blocked",
    *_MISSING_SOURCE_FAILURE_MARKERS,
)
_SUBQUERY_FAILURE_REASONS = {
    "broker_unavailable",
    "budget_exhausted",
    "child_error",
    "null_answer",
    "tool_error",
    "verification_blocked",
}
_NON_SUFFICIENT_FAILURE_STATUSES = {
    "needs_human_review",
    "tool_error",
    "verification_blocked",
}


# ---------------------------------------------------------------------------
# Pure helpers (no LM calls)
# ---------------------------------------------------------------------------


def compact_for_signature(text: str, max_chars: int = 4_000) -> str:
    """Return a compact preview of *text* suitable for signature inputs."""
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n\n... ({omitted} chars omitted) ...\n\n{tail}"


def requires_current_source_context(*, user_request: str, context: str) -> bool:
    """Return whether old durable evidence is unsafe for this request."""
    text = f"{user_request}\n{context}".lower()
    source_markers = (
        "loaded document",
        "document is located",
        "document alias",
        "document cache",
        "cached document",
        "document_url",
        "document url",
        "http://",
        "https://",
    )
    if any(term in text for term in source_markers):
        return True
    document_terms = ("document", "source", "pdf", "paper", "file")
    return ("alias" in text or "cache" in text) and any(term in text for term in document_terms)


def has_current_source_context(text: str) -> bool:
    """Return whether *text* carries real source content or a sandbox path."""
    stripped = text.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if "--- document fetched from " in lowered:
        return True
    if "is available in this child sandbox at:" in lowered:
        return True
    path_prefixes = ("/", "./", "../", "artifacts/", "memory/", "buffers/", "meta/")
    if "\n" not in stripped and stripped.startswith(path_prefixes):
        return True
    if len(stripped) >= 1_000 and "\n" in stripped:
        return True
    return False


def is_source_analysis_request(*, user_request: str, context: str) -> bool:
    """Return whether the request needs current repository/source evidence."""
    text = f"{user_request}\n{context}".lower()
    source_terms = (
        "architecture",
        "codebase",
        "dependency graph",
        "implementation",
        "module organization",
        "repository",
        "source code",
        "src/",
        "tests/",
    )
    return any(term in text for term in source_terms)


def is_missing_source_failure(
    *,
    user_request: str,
    context: str,
    failure_signals: list[str],
) -> bool:
    """Return whether source-analysis should stop for human review."""
    if not is_source_analysis_request(user_request=user_request, context=context):
        return False
    return any(marker in signal for signal in failure_signals for marker in _MISSING_SOURCE_FAILURE_MARKERS)


def _parse_subquery_output_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _collect_failure_reasons(value: Any, output_index: int, signals: list[str]) -> None:
    if isinstance(value, dict):
        reason = str(value.get("reason", "")).lower()
        if reason in _SUBQUERY_FAILURE_REASONS:
            signals.append(f"output[{output_index}]:reason={reason}")
        status = str(value.get("status", "")).lower()
        if status == "error" or status in _NON_SUFFICIENT_FAILURE_STATUSES:
            signals.append(f"output[{output_index}]:status={status}")
        if "error" in value and not reason and status != "ok":
            signals.append(f"output[{output_index}]:error")
        for key in ("errors", "results", "reviews"):
            _collect_failure_reasons(value.get(key), output_index, signals)
        return
    if isinstance(value, list):
        for item in value:
            _collect_failure_reasons(item, output_index, signals)


def classify_subquery_failures(outputs: list[str]) -> list[str]:
    """Extract normalized failure signals from raw subquery outputs."""
    signals: list[str] = []
    for index, output in enumerate(outputs):
        text = str(output)
        normalized = text.lower()
        for marker in _SUBQUERY_FAILURE_MARKERS:
            if marker in normalized:
                signals.append(f"output[{index}]:{marker}")
        parsed = _parse_subquery_output_json(text)
        if isinstance(parsed, dict):
            status = str(parsed.get("status", "")).lower()
            if status == "error" or status in _NON_SUFFICIENT_FAILURE_STATUSES:
                signals.append(f"output[{index}]:status={status}")
            if "error" in parsed and not status:
                signals.append(f"output[{index}]:error")
            _collect_failure_reasons(parsed, index, signals)
        elif isinstance(parsed, list):
            _collect_failure_reasons(parsed, index, signals)
        else:
            for reason in _SUBQUERY_FAILURE_REASONS:
                if reason in normalized:
                    signals.append(f"output[{index}]:reason={reason}")
    return sorted(dict.fromkeys(signals))


def classify_verification_failures(
    *,
    status: str,
    verified_summary: str,
    missing_evidence: list[str],
    contradictions: list[str],
    rationale: str,
) -> list[str]:
    """Extract normalized failure signals from a verification result."""
    normalized_status = status.strip().lower()
    signals: list[str] = []
    if normalized_status in _NON_SUFFICIENT_FAILURE_STATUSES:
        signals.append(f"verification:status={normalized_status}")
    text_parts = [verified_summary, rationale, *missing_evidence, *contradictions]
    text = "\n".join(str(part) for part in text_parts if part)
    for marker in _SUBQUERY_FAILURE_MARKERS:
        if marker in text.lower():
            signals.append(f"verification:{marker}")
    return sorted(dict.fromkeys(signals))


def merge_failure_signals(first: list[str], second: list[str]) -> list[str]:
    """Merge two failure signal lists, deduplicated and sorted."""
    return sorted(dict.fromkeys([*first, *second]))


def append_failure_signals(summary: str, signals: list[str]) -> str:
    """Append a failure-signal footer to a verified summary."""
    joined = ", ".join(signals)
    return f"{summary}\n\nSubquery failure signals detected; verifier success was not accepted: {joined}"


def format_failure_signals(failure_signals: list[str], contradictions: list[str]) -> str:
    """Serialize failure signals for the repair planner input."""
    payload = {
        "subquery_failure_signals": failure_signals,
        "verification_contradictions": contradictions,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def call_with_adapter_fallback(module: dspy.Module, *, enabled: bool = True, **kwargs: Any) -> dspy.Prediction:
    """Call a sub-module, retrying with ChatAdapter if JSONAdapter fails to parse."""
    try:
        return module(**kwargs)
    except Exception as exc:
        if not enabled or "Adapter" not in type(exc).__name__ and "Adapter" not in str(exc):
            raise
        fallback = dspy.ChatAdapter()
        with dspy.context(adapter=fallback):
            return module(**kwargs)


# ---------------------------------------------------------------------------
# Phase modules
# ---------------------------------------------------------------------------


class AssembleContextPhase(dspy.Module):
    """Assemble bounded recursive context from memory and recent evidence."""

    def __init__(self, **rlm_kwargs: Any) -> None:
        super().__init__()
        self.assemble = create_runtime_rlm(signature=AssembleRecursiveWorkspaceContext, **rlm_kwargs)

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
        result = self.assemble(
            user_request=user_request,
            current_plan=current_plan,
            loop_state=loop_state,
            working_memory_catalog=working_memory_catalog,
            recent_sandbox_evidence_catalog=recent_sandbox_evidence_catalog,
            latest_tool_or_code_result=latest_tool_or_code_result,
            context_budget=context_budget,
        )
        return dspy.Prediction(assembled_context=str(getattr(result, "assembled_context_summary", "")))


class PlanSubqueriesPhase(dspy.Module):
    """Decompose the request into bounded subqueries with an aggregation plan."""

    def __init__(self, **rlm_kwargs: Any) -> None:
        super().__init__()
        self.plan = create_runtime_rlm(signature=PlanRecursiveSubqueries, **rlm_kwargs)

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
        result = self.plan(
            user_request=user_request,
            assembled_recursive_context=assembled_recursive_context,
            current_plan=current_plan,
            loop_state=loop_state,
            latest_sandbox_evidence=latest_sandbox_evidence,
            subquery_budget=subquery_budget,
        )
        subqueries = list(getattr(result, "subqueries", []) or [])
        if not subqueries:
            subqueries = [user_request]
        return dspy.Prediction(
            subqueries=subqueries,
            decomposition_mode=str(getattr(result, "decomposition_mode", "single_pass")),
            aggregation_plan=str(getattr(result, "aggregation_plan", "")),
        )


class ExecuteSubqueriesPhase(dspy.Module):
    """Execute subqueries through sandbox child-RLM delegation."""

    def __init__(self, *, interpreter: Any) -> None:
        super().__init__()
        self.interpreter = interpreter

    def forward(self, *, subqueries: list[str], context: str, mode: str) -> dspy.Prediction:
        outputs = self._execute(subqueries, context, mode)
        return dspy.Prediction(
            outputs=outputs,
            failure_signals=classify_subquery_failures(outputs),
        )

    def _execute(self, subqueries: list[str], context: str, mode: str) -> list[str]:
        from fleet_rlm.runtime.tools.rlm_delegate import (
            delegate_to_rlm,
            delegate_to_rlm_batched,
        )

        if mode == "fan_out" and len(subqueries) > 1:
            result = delegate_to_rlm_batched(queries=subqueries, context=context, interpreter=self.interpreter)
            if result.get("status") in {"ok", "needs_human_review"}:
                outputs = [self._format_successful_output(r) for r in result.get("results", [])]
                outputs.extend(
                    self._format_successful_output({"status": "needs_human_review", "degraded": True, **r})
                    for r in result.get("reviews", [])
                    if isinstance(r, dict)
                )
                return outputs
            payload = {
                "status": result.get("status", "error"),
                "results": result.get("results", []),
                "errors": result.get("errors", "execution failed"),
            }
            if result.get("reviews"):
                payload["reviews"] = result.get("reviews", [])
            return [json.dumps(payload, ensure_ascii=False, sort_keys=True)]

        outputs: list[str] = []
        for query in subqueries:
            result = delegate_to_rlm(query=query, context=context, interpreter=self.interpreter)
            if result.get("status") in {"ok", "needs_human_review"}:
                outputs.append(self._format_successful_output(result))
            else:
                outputs.append(
                    json.dumps(
                        {
                            "status": result.get("status", "error"),
                            "reason": result.get("reason", "child_error"),
                            "error": result.get("error", "execution failed"),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
        return outputs

    def _format_successful_output(self, result: Any) -> str:
        if not isinstance(result, dict):
            return str(result)
        answer = str(result.get("answer", ""))
        if not result.get("degraded"):
            return answer
        return json.dumps(
            {
                "status": str(result.get("status", "ok")),
                "answer": answer,
                "degraded": True,
                "reason": str(result.get("reason", result.get("degradation_reason", "degraded"))),
                "error": str(result.get("error", result.get("degradation_error", ""))),
            },
            ensure_ascii=False,
            sort_keys=True,
        )


class VerifyAggregationPhase(dspy.Module):
    """Verify aggregated subquery evidence against the user request."""

    def __init__(self, *, adapter_fallback: bool = True, **rlm_kwargs: Any) -> None:
        super().__init__()
        self.verify = create_runtime_rlm(signature=VerifyRecursiveAggregation, **rlm_kwargs)
        self._adapter_fallback = adapter_fallback

    def forward(
        self,
        *,
        user_request: str,
        assembled_recursive_context: str,
        decomposition_plan_summary: str,
        collected_subquery_outputs: list[str],
        latest_sandbox_evidence: str,
        candidate_answer: str,
    ) -> dspy.Prediction:
        result = call_with_adapter_fallback(
            self.verify,
            enabled=self._adapter_fallback,
            user_request=user_request,
            assembled_recursive_context=assembled_recursive_context,
            decomposition_plan_summary=decomposition_plan_summary,
            collected_subquery_outputs=collected_subquery_outputs,
            latest_sandbox_evidence=latest_sandbox_evidence,
            candidate_answer=candidate_answer,
        )
        status = str(getattr(result, "verification_status", "sufficient"))
        verified_summary = str(getattr(result, "verified_summary", candidate_answer))
        missing_evidence = list(getattr(result, "missing_evidence", []) or [])
        contradictions = list(getattr(result, "contradictions", []) or [])
        rationale = str(getattr(result, "verification_rationale", ""))
        return dspy.Prediction(
            verification_status=status,
            verified_summary=verified_summary,
            missing_evidence=missing_evidence,
            contradictions=contradictions,
            failure_signals=classify_verification_failures(
                status=status,
                verified_summary=verified_summary,
                missing_evidence=missing_evidence,
                contradictions=contradictions,
                rationale=rationale,
            ),
        )


class ReflectAndRevisePhase(dspy.Module):
    """Decide the next action (finalize / recurse / repair) and revise the plan."""

    def __init__(self, *, adapter_fallback: bool = True, **rlm_kwargs: Any) -> None:
        super().__init__()
        self.reflect = create_runtime_rlm(signature=ReflectAndReviseWorkspaceStep, **rlm_kwargs)
        self._adapter_fallback = adapter_fallback

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
        result = call_with_adapter_fallback(
            self.reflect,
            enabled=self._adapter_fallback,
            user_request=user_request,
            working_memory_summary=working_memory_summary,
            current_plan=current_plan,
            latest_sandbox_evidence=latest_sandbox_evidence,
            latest_tool_or_code_result=latest_tool_or_code_result,
            loop_state=loop_state,
        )
        return dspy.Prediction(
            next_action=str(getattr(result, "next_action", "finalize")),
            revised_plan=str(getattr(result, "revised_plan", current_plan)),
        )


class PlanRepairPhase(dspy.Module):
    """Plan targeted repair subqueries from failure signals."""

    def __init__(self, **rlm_kwargs: Any) -> None:
        super().__init__()
        self.repair = create_runtime_rlm(signature=PlanRecursiveRepair, **rlm_kwargs)

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
        result = self.repair(
            user_request=user_request,
            assembled_recursive_context=assembled_recursive_context,
            verification_summary=verification_summary,
            latest_sandbox_evidence=latest_sandbox_evidence,
            latest_failure_signals=latest_failure_signals,
            repair_budget=repair_budget,
        )
        return dspy.Prediction(repair_subqueries=list(getattr(result, "repair_subqueries", []) or []))


__all__ = [
    "AssembleContextPhase",
    "ExecuteSubqueriesPhase",
    "PlanRepairPhase",
    "PlanSubqueriesPhase",
    "ReflectAndRevisePhase",
    "VerifyAggregationPhase",
    "append_failure_signals",
    "call_with_adapter_fallback",
    "classify_subquery_failures",
    "classify_verification_failures",
    "compact_for_signature",
    "format_failure_signals",
    "has_current_source_context",
    "is_missing_source_failure",
    "is_source_analysis_request",
    "merge_failure_signals",
    "requires_current_source_context",
    "_MISSING_SOURCE_FAILURE_MARKERS",
    "_NON_SUFFICIENT_FAILURE_STATUSES",
    "_SUBQUERY_FAILURE_MARKERS",
    "_SUBQUERY_FAILURE_REASONS",
]
