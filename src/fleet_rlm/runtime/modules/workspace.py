"""Multi-pass recursive workspace orchestrator (Level 4 RLM)."""

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
from fleet_rlm.runtime.modules.evidence import EvidenceSink
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


class RecursiveWorkspaceModule(dspy.Module):
    """Multi-pass recursive orchestrator using the workspace signatures.

    Runs a bounded loop: assemble context → plan decomposition → execute
    subqueries → verify → reflect (finalize / recurse / repair). Each
    sub-module is a ``dspy.RLM`` so the LLM writes code at every step.

    Evidence is persisted across passes via an injected :class:`EvidenceSink`.
    Pass ``evidence_sink=None`` (the default) to skip persistence — this
    matches the behaviour of runs without a host repository attached.  The
    production adapter is
    ``fleet_rlm.integrations.daytona.evidence_bridge.DaytonaEvidenceSink``.
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
        self._adapter_fallback = adapter_fallback

        rlm_kwargs: dict[str, Any] = {
            "interpreter": interpreter,
            "max_iterations": max_iterations,
            "max_llm_calls": max_llm_calls,
            "verbose": verbose,
            "sub_lm": sub_lm,
        }

        self._assembler = create_runtime_rlm(signature=AssembleRecursiveWorkspaceContext, **rlm_kwargs)
        self._planner = create_runtime_rlm(signature=PlanRecursiveSubqueries, **rlm_kwargs)
        self._verifier = create_runtime_rlm(signature=VerifyRecursiveAggregation, **rlm_kwargs)
        self._reflector = create_runtime_rlm(signature=ReflectAndReviseWorkspaceStep, **rlm_kwargs)
        self._repairer = create_runtime_rlm(signature=PlanRecursiveRepair, **rlm_kwargs)

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
        requires_current_source = self._requires_current_source_context(
            user_request=user_request,
            context=context,
        )
        has_current_source = self._has_current_source_context(context)

        for pass_idx in range(self.max_passes):
            loop_state = self._loop_state_summary(pass_idx, repair_count)

            memory_catalog = (
                working_memory_catalog
                if working_memory_catalog is not None
                else ([] if requires_current_source and not has_current_source else self._fetch_memory_catalog())
            )
            compact_latest = self._compact_for_signature(latest_result)
            assemble = self._assembler(
                user_request=user_request,
                current_plan=plan,
                loop_state=loop_state,
                working_memory_catalog=memory_catalog,
                recent_sandbox_evidence_catalog=evidence_catalog,
                latest_tool_or_code_result=compact_latest,
                context_budget=self.context_budget_chars,
            )
            assembled_context = str(getattr(assemble, "assembled_context_summary", ""))
            if (
                requires_current_source
                and not has_current_source
                and not self._has_current_source_context(assembled_context)
            ):
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

            compact_latest = self._compact_for_signature(latest_result)
            decomposition = self._planner(
                user_request=user_request,
                assembled_recursive_context=assembled_context,
                current_plan=plan,
                loop_state=loop_state,
                latest_sandbox_evidence=compact_latest,
                subquery_budget=self.subquery_budget,
            )
            subqueries = list(getattr(decomposition, "subqueries", []) or [])
            mode = str(getattr(decomposition, "decomposition_mode", "single_pass"))
            aggregation_plan = str(getattr(decomposition, "aggregation_plan", ""))

            if not subqueries:
                subqueries = [user_request]
            outputs = self._execute_subqueries(subqueries, assembled_context, mode)
            latest_result = "\n---\n".join(outputs)
            # Refresh compact_latest so the verifier and reflector operate on the
            # evidence produced in *this* pass, not the stale pre-execution snapshot.
            compact_latest = self._compact_for_signature(latest_result)
            failure_signals = self._classify_subquery_failures(outputs)

            self._store_pass_evidence(pass_idx, outputs)
            evidence_catalog = [f"pass_{pass_idx}_output_{i}" for i in range(len(outputs))]

            compact_outputs = [self._compact_for_signature(o) for o in outputs]
            verification = self._call_with_fallback(
                self._verifier,
                user_request=user_request,
                assembled_recursive_context=assembled_context,
                decomposition_plan_summary=aggregation_plan,
                collected_subquery_outputs=compact_outputs,
                latest_sandbox_evidence=compact_latest,
                candidate_answer=latest_result,
            )
            status = str(getattr(verification, "verification_status", "sufficient"))
            verified_summary = str(getattr(verification, "verified_summary", latest_result))
            failure_signals = self._merge_failure_signals(
                failure_signals,
                self._classify_verification_failures(
                    status=status,
                    verified_summary=verified_summary,
                    missing_evidence=list(getattr(verification, "missing_evidence", []) or []),
                    contradictions=list(getattr(verification, "contradictions", []) or []),
                    rationale=str(getattr(verification, "verification_rationale", "")),
                ),
            )
            if self._is_missing_source_failure(
                user_request=user_request,
                context=assembled_context,
                failure_signals=failure_signals,
            ):
                return dspy.Prediction(
                    answer=self._append_failure_signals(verified_summary, failure_signals),
                    passes=pass_idx + 1,
                    status="needs_human_review",
                    missing=["current_source_evidence"],
                )
            if failure_signals and status == "sufficient":
                status = "needs_repair" if repair_count < self.max_repair_attempts else "needs_more_recursion"
                verified_summary = self._append_failure_signals(verified_summary, failure_signals)

            if status == "sufficient":
                # Prefer the raw subquery output when it contains more detail
                # than the verifier's summary (which may be a prose description).
                if self._classify_subquery_failures([latest_result]):
                    verified_summary = self._append_failure_signals(
                        verified_summary,
                        self._classify_subquery_failures([latest_result]),
                    )
                else:
                    best_answer = latest_result if len(latest_result) > len(verified_summary) else verified_summary
                    return dspy.Prediction(
                        answer=best_answer,
                        passes=pass_idx + 1,
                        status="sufficient",
                    )

            reflection = self._call_with_fallback(
                self._reflector,
                user_request=user_request,
                working_memory_summary=assembled_context,
                current_plan=plan,
                latest_sandbox_evidence=compact_latest,
                latest_tool_or_code_result=self._compact_for_signature(verified_summary),
                loop_state=loop_state,
            )
            action = str(getattr(reflection, "next_action", "finalize"))
            plan = str(getattr(reflection, "revised_plan", plan))
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
                    missing=list(getattr(verification, "missing_evidence", [])),
                )
            if action == "repair_and_retry" and repair_count < self.max_repair_attempts:
                repair = self._repairer(
                    user_request=user_request,
                    assembled_recursive_context=assembled_context,
                    verification_summary=self._compact_for_signature(verified_summary),
                    latest_sandbox_evidence=compact_latest,
                    latest_failure_signals=self._format_failure_signals(
                        failure_signals,
                        list(getattr(verification, "contradictions", []) or []),
                    ),
                    repair_budget=max(1, self.subquery_budget // 2),
                )
                repair_queries = list(getattr(repair, "repair_subqueries", []) or [])
                if repair_queries:
                    repair_outputs = self._execute_subqueries(repair_queries, assembled_context, "serial")
                    latest_result = "\n---\n".join(repair_outputs)
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

    def _compact_for_signature(self, text: str, max_chars: int = 4_000) -> str:
        """Return a compact preview of *text* suitable for signature inputs."""
        if len(text) <= max_chars:
            return text
        head = text[: max_chars // 2]
        tail = text[-max_chars // 2 :]
        omitted = len(text) - len(head) - len(tail)
        return f"{head}\n\n... ({omitted} chars omitted) ...\n\n{tail}"

    def _requires_current_source_context(self, *, user_request: str, context: str) -> bool:
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

    def _has_current_source_context(self, text: str) -> bool:
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

    def _is_source_analysis_request(self, *, user_request: str, context: str) -> bool:
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

    def _is_missing_source_failure(
        self,
        *,
        user_request: str,
        context: str,
        failure_signals: list[str],
    ) -> bool:
        """Return whether source-analysis should stop for human review."""
        if not self._is_source_analysis_request(user_request=user_request, context=context):
            return False
        return any(marker in signal for signal in failure_signals for marker in _MISSING_SOURCE_FAILURE_MARKERS)

    def _call_with_fallback(self, module: dspy.Module, **kwargs) -> dspy.Prediction:
        """Call a sub-module, retrying with ChatAdapter if JSONAdapter fails to parse."""
        try:
            return module(**kwargs)
        except Exception as exc:
            if not self._adapter_fallback or "Adapter" not in type(exc).__name__ and "Adapter" not in str(exc):
                raise
            fallback = dspy.ChatAdapter()
            with dspy.context(adapter=fallback):
                return module(**kwargs)

    def _execute_subqueries(
        self,
        subqueries: list[str],
        context: str,
        mode: str,
    ) -> list[str]:
        from fleet_rlm.runtime.tools.rlm_delegate import (
            delegate_to_rlm,
            delegate_to_rlm_batched,
        )

        if mode == "fan_out" and len(subqueries) > 1:
            result = delegate_to_rlm_batched(queries=subqueries, context=context, interpreter=self.interpreter)
            if result.get("status") == "ok":
                return [self._format_successful_subquery_output(r) for r in result.get("results", [])]
            return [
                json.dumps(
                    {
                        "status": result.get("status", "error"),
                        "results": result.get("results", []),
                        "errors": result.get("errors", "execution failed"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            ]

        outputs: list[str] = []
        for query in subqueries:
            result = delegate_to_rlm(query=query, context=context, interpreter=self.interpreter)
            if result.get("status") == "ok":
                outputs.append(self._format_successful_subquery_output(result))
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

    def _format_successful_subquery_output(self, result: Any) -> str:
        if not isinstance(result, dict):
            return str(result)
        answer = str(result.get("answer", ""))
        if not result.get("degraded"):
            return answer
        return json.dumps(
            {
                "status": "ok",
                "answer": answer,
                "degraded": True,
                "reason": str(result.get("degradation_reason", "degraded")),
                "error": str(result.get("degradation_error", "")),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _classify_subquery_failures(self, outputs: list[str]) -> list[str]:
        signals: list[str] = []
        for index, output in enumerate(outputs):
            text = str(output)
            normalized = text.lower()
            for marker in _SUBQUERY_FAILURE_MARKERS:
                if marker in normalized:
                    signals.append(f"output[{index}]:{marker}")
            parsed = self._parse_subquery_output_json(text)
            if isinstance(parsed, dict):
                status = str(parsed.get("status", "")).lower()
                if status == "error":
                    signals.append(f"output[{index}]:status=error")
                if "error" in parsed and not status:
                    signals.append(f"output[{index}]:error")
                self._collect_failure_reasons(parsed, index, signals)
            elif isinstance(parsed, list):
                self._collect_failure_reasons(parsed, index, signals)
            else:
                for reason in _SUBQUERY_FAILURE_REASONS:
                    if reason in normalized:
                        signals.append(f"output[{index}]:reason={reason}")
        return sorted(dict.fromkeys(signals))

    def _parse_subquery_output_json(self, text: str) -> Any:
        stripped = text.strip()
        if not stripped or stripped[0] not in "[{":
            return None
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None

    def _collect_failure_reasons(self, value: Any, output_index: int, signals: list[str]) -> None:
        if isinstance(value, dict):
            reason = str(value.get("reason", "")).lower()
            if reason in _SUBQUERY_FAILURE_REASONS:
                signals.append(f"output[{output_index}]:reason={reason}")
            status = str(value.get("status", "")).lower()
            if status == "error":
                signals.append(f"output[{output_index}]:status=error")
            if "error" in value and not reason and status != "ok":
                signals.append(f"output[{output_index}]:error")
            for key in ("errors", "results"):
                self._collect_failure_reasons(value.get(key), output_index, signals)
            return
        if isinstance(value, list):
            for item in value:
                self._collect_failure_reasons(item, output_index, signals)

    def _classify_verification_failures(
        self,
        *,
        status: str,
        verified_summary: str,
        missing_evidence: list[str],
        contradictions: list[str],
        rationale: str,
    ) -> list[str]:
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

    def _merge_failure_signals(self, first: list[str], second: list[str]) -> list[str]:
        return sorted(dict.fromkeys([*first, *second]))

    def _append_failure_signals(self, summary: str, signals: list[str]) -> str:
        joined = ", ".join(signals)
        return f"{summary}\n\nSubquery failure signals detected; verifier success was not accepted: {joined}"

    def _format_failure_signals(self, failure_signals: list[str], contradictions: list[str]) -> str:
        payload = {
            "subquery_failure_signals": failure_signals,
            "verification_contradictions": contradictions,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

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
