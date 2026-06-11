"""Multi-pass recursive workspace orchestrator (Level 4 RLM).

The orchestrator is a thin loop over composable per-phase modules from
:mod:`.workspace_phases`: assemble context → plan subqueries → execute →
verify → reflect (finalize / recurse / repair).  Each phase is its own
``dspy.Module`` so it can be tested and GEPA-optimized independently.
"""

from __future__ import annotations

from typing import Any

import dspy

from fleet_rlm.runtime.modules.evidence import EvidenceSink
from fleet_rlm.runtime.modules.workspace_phases import (
    _MISSING_SOURCE_FAILURE_MARKERS,
    _NON_SUFFICIENT_FAILURE_STATUSES,
    _SUBQUERY_FAILURE_MARKERS,
    _SUBQUERY_FAILURE_REASONS,
    AssembleContextPhase,
    ExecuteSubqueriesPhase,
    PlanRepairPhase,
    PlanSubqueriesPhase,
    ReflectAndRevisePhase,
    VerifyAggregationPhase,
    append_failure_signals,
    classify_subquery_failures,
    compact_for_signature,
    format_failure_signals,
    has_current_source_context,
    is_missing_source_failure,
    merge_failure_signals,
    requires_current_source_context,
)


class RecursiveWorkspaceModule(dspy.Module):
    """Multi-pass recursive orchestrator using the workspace signatures.

    Runs a bounded loop: assemble context → plan decomposition → execute
    subqueries → verify → reflect (finalize / recurse / repair). Each phase
    is a composable ``dspy.Module`` backed by ``dspy.RLM``, so the LLM writes
    code at every step.

    Evidence is persisted across passes via an injected :class:`EvidenceSink`.
    Pass ``evidence_sink=None`` (the default) to skip persistence — this
    matches the behaviour of runs without a host repository attached.  The
    production adapter is
    ``fleet_rlm.integrations.daytona.isolation.DaytonaEvidenceSink``.
    """

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int = 20,
        max_llm_calls: int = 50,
        max_passes: int = 3,
        max_repair_attempts: int = 2,
        subquery_budget: int = 4,
        context_budget_chars: int = 32_000,
        verbose: bool = False,
        sub_lm: dspy.LM | None = None,
        evidence_sink: EvidenceSink | None = None,
        adapter_fallback: bool = True,
    ) -> None:
        super().__init__()
        self.interpreter = interpreter
        self.max_passes = max_passes
        self.max_repair_attempts = max_repair_attempts
        self.subquery_budget = subquery_budget
        self.context_budget_chars = context_budget_chars
        self._evidence = evidence_sink

        rlm_kwargs: dict[str, Any] = {
            "interpreter": interpreter,
            "max_iterations": max_iterations,
            "max_llm_calls": max_llm_calls,
            "verbose": verbose,
            "sub_lm": sub_lm,
        }

        self.assemble_phase = AssembleContextPhase(**rlm_kwargs)
        self.plan_phase = PlanSubqueriesPhase(**rlm_kwargs)
        self.execute_phase = ExecuteSubqueriesPhase(interpreter=interpreter)
        self.verify_phase = VerifyAggregationPhase(adapter_fallback=adapter_fallback, **rlm_kwargs)
        self.reflect_phase = ReflectAndRevisePhase(adapter_fallback=adapter_fallback, **rlm_kwargs)
        self.repair_phase = PlanRepairPhase(**rlm_kwargs)

    def forward(
        self,
        *,
        user_request: str,
        context: str = "",
        working_memory_catalog: list[str] | None = None,
    ) -> dspy.Prediction:
        """Run the multi-pass recursive workspace loop."""
        plan = f"Initial: {user_request}"
        evidence_catalog: list[str] = []
        latest_result = context
        repair_count = 0
        requires_current_source = requires_current_source_context(user_request=user_request, context=context)
        has_current_source = has_current_source_context(context)

        for pass_idx in range(self.max_passes):
            loop_state = self._loop_state_summary(pass_idx, repair_count)
            memory_catalog = (
                working_memory_catalog
                if working_memory_catalog is not None
                else ([] if requires_current_source and not has_current_source else self._fetch_memory_catalog())
            )

            assembled_context = str(
                self.assemble_phase(
                    user_request=user_request,
                    current_plan=plan,
                    loop_state=loop_state,
                    working_memory_catalog=memory_catalog,
                    recent_sandbox_evidence_catalog=evidence_catalog,
                    latest_tool_or_code_result=compact_for_signature(latest_result),
                    context_budget=self.context_budget_chars,
                ).assembled_context
            )
            if requires_current_source and not has_current_source and not has_current_source_context(assembled_context):
                return dspy.Prediction(
                    answer=(
                        "Current document/source content was not available to the "
                        "recursive workspace. Use delegate_to_rlm(document_url=...) "
                        "or pass the actual document text/sandbox path as context."
                    ),
                    passes=pass_idx + 1,
                    status="needs_human_review",
                    missing=["current_document_source"],
                )

            # If the assembler failed to build useful context (e.g. due to
            # ChatAdapter parse errors inside its REPL loop), fall back to
            # including user_request verbatim so child RLMs are not context-starved.
            if len(assembled_context) < 64 and len(user_request) <= self.context_budget_chars:
                assembled_context = user_request

            decomposition = self.plan_phase(
                user_request=user_request,
                assembled_recursive_context=assembled_context,
                current_plan=plan,
                loop_state=loop_state,
                latest_sandbox_evidence=compact_for_signature(latest_result),
                subquery_budget=self.subquery_budget,
            )

            execution = self.execute_phase(
                subqueries=list(decomposition.subqueries),
                context=assembled_context,
                mode=str(decomposition.decomposition_mode),
            )
            outputs = list(execution.outputs)
            latest_result = "\n---\n".join(outputs)
            compact_latest = compact_for_signature(latest_result)
            failure_signals = list(execution.failure_signals)

            self._store_pass_evidence(pass_idx, outputs)
            evidence_catalog = [f"pass_{pass_idx}_output_{i}" for i in range(len(outputs))]

            verification = self.verify_phase(
                user_request=user_request,
                assembled_recursive_context=assembled_context,
                decomposition_plan_summary=str(decomposition.aggregation_plan),
                collected_subquery_outputs=[compact_for_signature(o) for o in outputs],
                latest_sandbox_evidence=compact_latest,
                candidate_answer=latest_result,
            )
            status = str(verification.verification_status)
            verified_summary = str(verification.verified_summary)
            failure_signals = merge_failure_signals(failure_signals, list(verification.failure_signals))

            terminal = self._terminal_prediction(
                user_request=user_request,
                assembled_context=assembled_context,
                pass_idx=pass_idx,
                latest_result=latest_result,
                verified_summary=verified_summary,
                failure_signals=failure_signals,
                missing_evidence=list(verification.missing_evidence),
            )
            if terminal is not None:
                return terminal
            if failure_signals and status == "sufficient":
                status = "needs_repair" if repair_count < self.max_repair_attempts else "needs_more_recursion"
                verified_summary = append_failure_signals(verified_summary, failure_signals)

            if status == "sufficient":
                # Prefer the raw subquery output when it contains more detail
                # than the verifier's summary (which may be a prose description).
                late_signals = classify_subquery_failures([latest_result])
                if late_signals:
                    verified_summary = append_failure_signals(verified_summary, late_signals)
                else:
                    best_answer = latest_result if len(latest_result) > len(verified_summary) else verified_summary
                    return dspy.Prediction(
                        answer=best_answer,
                        passes=pass_idx + 1,
                        status="sufficient",
                    )

            reflection = self.reflect_phase(
                user_request=user_request,
                working_memory_summary=assembled_context,
                current_plan=plan,
                latest_sandbox_evidence=compact_latest,
                latest_tool_or_code_result=compact_for_signature(verified_summary),
                loop_state=loop_state,
            )
            action = str(reflection.next_action)
            plan = str(reflection.revised_plan)
            if failure_signals:
                action = "repair_and_retry" if repair_count < self.max_repair_attempts else "request_human_review"

            if action == "finalize":
                return dspy.Prediction(
                    answer=verified_summary,
                    passes=pass_idx + 1,
                    status="finalized",
                )
            if action == "request_human_review":
                return dspy.Prediction(
                    answer=verified_summary,
                    passes=pass_idx + 1,
                    status="needs_human_review",
                    missing=list(verification.missing_evidence),
                )
            if action == "repair_and_retry" and repair_count < self.max_repair_attempts:
                repair = self.repair_phase(
                    user_request=user_request,
                    assembled_recursive_context=assembled_context,
                    verification_summary=compact_for_signature(verified_summary),
                    latest_sandbox_evidence=compact_latest,
                    latest_failure_signals=format_failure_signals(
                        failure_signals,
                        list(verification.contradictions),
                    ),
                    repair_budget=max(1, self.subquery_budget // 2),
                )
                repair_queries = list(repair.repair_subqueries)
                if repair_queries:
                    repair_execution = self.execute_phase(
                        subqueries=repair_queries,
                        context=assembled_context,
                        mode="serial",
                    )
                    latest_result = "\n---\n".join(repair_execution.outputs)
                repair_count += 1
                continue

        return dspy.Prediction(
            answer=latest_result,
            passes=self.max_passes,
            status="budget_exhausted",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _terminal_prediction(
        self,
        *,
        user_request: str,
        assembled_context: str,
        pass_idx: int,
        latest_result: str,
        verified_summary: str,
        failure_signals: list[str],
        missing_evidence: list[str],
    ) -> dspy.Prediction | None:
        """Return a human-review prediction when failure signals demand one."""
        if is_missing_source_failure(
            user_request=user_request,
            context=assembled_context,
            failure_signals=failure_signals,
        ):
            return dspy.Prediction(
                answer=append_failure_signals(verified_summary, failure_signals),
                passes=pass_idx + 1,
                status="needs_human_review",
                missing=["current_source_evidence"],
            )
        if any("status=needs_human_review" in signal for signal in failure_signals):
            subquery_review = any(
                signal.startswith("output[") and "status=needs_human_review" in signal for signal in failure_signals
            )
            review_summary = latest_result if subquery_review and latest_result else verified_summary
            return dspy.Prediction(
                answer=append_failure_signals(review_summary, failure_signals),
                passes=pass_idx + 1,
                status="needs_human_review",
                missing=missing_evidence,
            )
        return None

    def _fetch_memory_catalog(self) -> list[str]:
        if self._evidence is None:
            return []
        result = self._evidence.list_items(scope="run", limit=50)
        return [f"{item['scope_id']}:{item['kind']}" for item in result.get("items", [])]

    def _store_pass_evidence(self, pass_idx: int, outputs: list[str]) -> None:
        if self._evidence is None:
            return
        for i, output in enumerate(outputs):
            self._evidence.store(
                key=f"pass_{pass_idx}_output_{i}",
                content=output[:10_000],
                kind="context",
                scope="run",
                tags=[f"pass:{pass_idx}", "orchestrator"],
            )

    def _loop_state_summary(self, pass_idx: int, repair_count: int) -> str:
        return (
            f"pass={pass_idx + 1}/{self.max_passes}, "
            f"repairs={repair_count}/{self.max_repair_attempts}, "
            f"subquery_budget={self.subquery_budget}"
        )


__all__ = [
    "RecursiveWorkspaceModule",
    "_MISSING_SOURCE_FAILURE_MARKERS",
    "_NON_SUFFICIENT_FAILURE_STATUSES",
    "_SUBQUERY_FAILURE_MARKERS",
    "_SUBQUERY_FAILURE_REASONS",
]
