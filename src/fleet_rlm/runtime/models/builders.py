"""Builder functions and concrete module classes for DSPy runtime modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import dspy

from fleet_rlm.runtime.agent.signatures import (
    AssembleRecursiveWorkspaceContext,
    ClarificationQuestionSignature,
    GroundedAnswerWithCitations,
    MemoryActionIntentSignature,
    MemoryStructureAuditSignature,
    MemoryStructureMigrationPlanSignature,
    PlanRecursiveRepair,
    PlanRecursiveSubqueries,
    RecursiveSubQuerySignature,
    ReflectAndReviseWorkspaceStep,
    RLMVariableSignature,
    VerifyRecursiveAggregation,
    VolumeFileTreeSignature,
)
from fleet_rlm.runtime.content.chunking import (
    chunk_by_headers,
    chunk_by_json_keys,
    chunk_by_size,
    chunk_by_timestamps,
)


def create_runtime_rlm(
    *,
    signature: type[dspy.Signature],
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    max_output_chars: int | None = None,
    verbose: bool,
    tools: list[Any] | None = None,
    sub_lm: dspy.LM | None = None,
) -> dspy.Module:
    """Create a canonical RLM instance for a runtime signature."""

    kwargs: dict[str, Any] = {
        "signature": signature,
        "interpreter": interpreter,
        "max_iterations": max_iterations,
        "max_llm_calls": max_llm_calls,
        "verbose": verbose,
    }
    if max_output_chars is not None:
        kwargs["max_output_chars"] = max_output_chars
    if tools is not None:
        kwargs["tools"] = tools
    if sub_lm is not None:
        kwargs["sub_lm"] = sub_lm

    return dspy.RLM(
        **kwargs,
    )


def build_recursive_subquery_rlm(
    *,
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    max_output_chars: int | None = None,
    verbose: bool,
    sub_lm: dspy.LM | None = None,
) -> dspy.Module:
    """Build the canonical recursive child-query RLM."""

    return create_runtime_rlm(
        signature=RecursiveSubQuerySignature,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        max_output_chars=max_output_chars,
        verbose=verbose,
        sub_lm=sub_lm,
    )


# ---------------------------------------------------------------------------
# True-RLM variable-mode execution (Algorithm 1, arXiv 2512.24601v2)
# ---------------------------------------------------------------------------
# dspy.RLM natively:
#   1. Stores all InputField values as REPL variables (_build_variables)
#   2. Shows only metadata (type, length, preview) to the LLM
#   3. Provides llm_query() and SUBMIT() built-in
#   4. Accepts custom tools= for additional callables (sub_rlm, etc.)
# See https://dspy.ai/api/modules/RLM/
# ---------------------------------------------------------------------------

# Threshold (chars) above which rlm_query auto-routes to variable mode
VARIABLE_MODE_THRESHOLD = 32_000

# Lower max_output_chars for variable-mode forces the LLM to use
# variables (peek, grep, sub_rlm) instead of printing large output.
# dspy.RLM's REPLEntry.format() already shows "Output (N chars):" as
# metadata — this keeps it short so the LLM relies on REPL state.
VARIABLE_MODE_MAX_OUTPUT_CHARS = 5_000


class RLMVariableExecutionModule(dspy.Module):
    """Variable-mode RLM wrapper that preserves the caller's signature.

    This thin wrapper:
    1. Collects ``sub_rlm`` / ``sub_rlm_batched`` from the interpreter
       and registers them as ``dspy.Tool`` instances on the inner RLM.
    2. Reuses the requested DSPy signature so cached runtime-module callers
       keep their existing input/output field names.
    3. Relies on ``dspy.RLM``'s native variable handling to store each input
       field as a REPL variable while exposing only metadata/previews to the LM.

    All heavy lifting (REPL loop, metadata display, iteration budget,
    llm_query) is handled by ``dspy.RLM`` itself.
    """

    def __init__(
        self,
        *,
        signature: type[dspy.Signature] = RLMVariableSignature,
        interpreter: Any,
        max_iterations: int = 20,
        max_llm_calls: int = 50,
        verbose: bool = False,
        max_output_chars: int | None = None,
        sub_lm: dspy.LM | None = None,
        extra_tools: list[Any] | None = None,
    ) -> None:
        super().__init__()
        # Gather sub_rlm tools from the interpreter (if it exposes them)
        tools: list[Any] = list(extra_tools or [])
        for attr_name in ("sub_rlm", "sub_rlm_batched"):
            fn = getattr(interpreter, attr_name, None)
            if callable(fn):
                tools.append(fn)

        self._rlm = create_runtime_rlm(
            signature=signature,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            # Use a tighter output limit for variable mode to force the LLM
            # to work through REPL variables rather than printing large output.
            max_output_chars=max_output_chars or VARIABLE_MODE_MAX_OUTPUT_CHARS,
            verbose=verbose,
            tools=tools or None,
            sub_lm=sub_lm,
        )

    def forward(self, **kwargs: Any) -> dspy.Prediction:
        """Run a true-RLM loop while preserving the caller's DSPy fields.

        ``dspy.RLM`` stores each input field as a REPL variable and the
        model writes code to explore those variables before calling
        ``SUBMIT(...)`` with the signature's declared outputs.
        """
        return self._rlm(**kwargs)


def build_variable_mode_rlm(
    *,
    signature: type[dspy.Signature] = RLMVariableSignature,
    interpreter: Any,
    max_iterations: int = 20,
    max_llm_calls: int = 50,
    verbose: bool = False,
    max_output_chars: int | None = None,
    sub_lm: dspy.LM | None = None,
    extra_tools: list[Any] | None = None,
) -> RLMVariableExecutionModule:
    """Factory for the true-RLM variable-mode execution module.

    Use for any task where one or more large inputs should stay in the REPL
    instead of the model context. The LLM sees only metadata and explores
    through code + sub_rlm() recursion.
    """
    return RLMVariableExecutionModule(
        signature=signature,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
        max_output_chars=max_output_chars,
        sub_lm=sub_lm,
        extra_tools=extra_tools,
    )


@dataclass(frozen=True)
class RuntimeModuleBuildConfig:
    """Shared constructor parameters for runtime-module RLMs."""

    interpreter: Any
    max_iterations: int
    max_llm_calls: int
    verbose: bool
    max_output_chars: int | None = None
    sub_lm: dspy.LM | None = None


def build_runtime_module_config(
    *,
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    verbose: bool,
    max_output_chars: int | None = None,
    sub_lm: dspy.LM | None = None,
) -> RuntimeModuleBuildConfig:
    return RuntimeModuleBuildConfig(
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
        max_output_chars=max_output_chars,
        sub_lm=sub_lm,
    )


def _create_configured_runtime_rlm(
    config: RuntimeModuleBuildConfig,
    *,
    signature: type[dspy.Signature],
) -> dspy.Module:
    return create_runtime_rlm(
        signature=signature,
        interpreter=config.interpreter,
        max_iterations=config.max_iterations,
        max_llm_calls=config.max_llm_calls,
        max_output_chars=config.max_output_chars,
        verbose=config.verbose,
        sub_lm=config.sub_lm,
    )


def _normalize_chunk_strategy(strategy: str) -> str:
    normalized = str(strategy).strip().lower().replace("-", "_")
    mapping = {
        "size": "size",
        "headers": "headers",
        "header": "headers",
        "timestamps": "timestamps",
        "timestamp": "timestamps",
        "json": "json_keys",
        "json_keys": "json_keys",
    }
    if normalized not in mapping:
        raise ValueError(
            "Unsupported strategy. Choose one of: size, headers, timestamps, json_keys"
        )
    return mapping[normalized]


def _chunk_document(text: str, strategy: str) -> list[Any]:
    strategy_norm = _normalize_chunk_strategy(strategy)
    if strategy_norm == "size":
        return chunk_by_size(text, size=80_000, overlap=1_000)
    if strategy_norm == "headers":
        return chunk_by_headers(text, pattern=r"^#{1,3} ")
    if strategy_norm == "timestamps":
        return chunk_by_timestamps(text, pattern=r"^\d{4}-\d{2}-\d{2}[T ]")
    return chunk_by_json_keys(text)


def _chunk_to_text(chunk: Any) -> str:
    if isinstance(chunk, str):
        return chunk
    if not isinstance(chunk, dict):
        return str(chunk)
    if "header" in chunk:
        return f"{chunk.get('header', '')}\n{chunk.get('content', '')}".strip()
    if "timestamp" in chunk:
        return str(chunk.get("content", ""))
    if "key" in chunk:
        return f"{chunk.get('key', '')}\n{chunk.get('content', '')}".strip()
    return str(chunk)


def _coerce_bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


class GroundedAnswerSynthesisModule(dspy.Module):
    """Compose chunking + evidence selection before the grounded-answer RLM."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._grounded_answer = _create_configured_runtime_rlm(
            build_runtime_module_config(
                interpreter=interpreter,
                max_iterations=max_iterations,
                max_llm_calls=max_llm_calls,
                verbose=verbose,
            ),
            signature=GroundedAnswerWithCitations,
        )

    def forward(
        self,
        *,
        document: str,
        query: str,
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        response_style: str = "concise",
    ) -> dspy.Prediction:
        max_chunks_int = _coerce_bounded_int(max_chunks, default=24, minimum=1)
        try:
            chunks = _chunk_document(document, chunk_strategy)
        except ValueError as exc:
            return dspy.Prediction(
                answer="",
                citations=[],
                confidence=0,
                coverage_notes=str(exc),
            )

        evidence_chunks = [_chunk_to_text(chunk) for chunk in chunks][:max_chunks_int]
        if not evidence_chunks:
            return dspy.Prediction(
                answer="",
                citations=[],
                confidence=0,
                coverage_notes="No evidence chunks available.",
            )

        return self._grounded_answer(
            query=query,
            evidence_chunks=evidence_chunks,
            response_style=response_style,
        )


class _MemoryTreePrimedModule(dspy.Module):
    """Base helper for modules that prime work with a memory tree snapshot."""

    def __init__(self, *, config: RuntimeModuleBuildConfig) -> None:
        super().__init__()
        self._memory_tree = _create_configured_runtime_rlm(
            config,
            signature=VolumeFileTreeSignature,
        )

    def _resolve_tree_snapshot(
        self,
        *,
        root_path: str,
        max_depth: int,
        include_hidden: bool,
        tree_snapshot: list[Any] | None,
    ) -> list[Any]:
        if tree_snapshot is not None:
            return list(tree_snapshot or [])

        tree_prediction = self._memory_tree(
            root_path=root_path,
            max_depth=_coerce_bounded_int(max_depth, default=4, minimum=0, maximum=12),
            include_hidden=bool(include_hidden),
        )
        return list(getattr(tree_prediction, "nodes", []) or [])

    def _resolve_tree_context(
        self,
        *,
        root_path: str,
        max_depth: int,
        include_hidden: bool,
        available_context: str,
    ) -> str:
        context_text = available_context.strip()
        if context_text:
            return context_text

        tree_nodes = self._resolve_tree_snapshot(
            root_path=root_path,
            max_depth=max_depth,
            include_hidden=include_hidden,
            tree_snapshot=None,
        )[:20]
        return f"memory_root={root_path}; nodes_sample={tree_nodes}"


class MemoryStructureAuditPlanningModule(_MemoryTreePrimedModule):
    """Compose a memory-tree snapshot with the audit RLM."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        config = build_runtime_module_config(
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )
        super().__init__(config=config)
        self._memory_structure_audit = _create_configured_runtime_rlm(
            config,
            signature=MemoryStructureAuditSignature,
        )

    def forward(
        self,
        *,
        usage_goals: str = "Keep memory discoverable, consistent, and easy to maintain.",
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
        tree_snapshot: list[Any] | None = None,
    ) -> dspy.Prediction:
        return self._memory_structure_audit(
            tree_snapshot=self._resolve_tree_snapshot(
                root_path=root_path,
                max_depth=max_depth,
                include_hidden=include_hidden,
                tree_snapshot=tree_snapshot,
            ),
            usage_goals=usage_goals,
        )


class MemoryActionPlanningModule(_MemoryTreePrimedModule):
    """Compose a memory-tree snapshot with the action-intent RLM."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        config = build_runtime_module_config(
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )
        super().__init__(config=config)
        self._memory_action_intent = _create_configured_runtime_rlm(
            config,
            signature=MemoryActionIntentSignature,
        )

    def forward(
        self,
        *,
        user_request: str,
        policy_constraints: str = "Prefer non-destructive operations and ask for confirmation on risky actions.",
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
        current_tree: list[Any] | None = None,
    ) -> dspy.Prediction:
        return self._memory_action_intent(
            user_request=user_request,
            current_tree=self._resolve_tree_snapshot(
                root_path=root_path,
                max_depth=max_depth,
                include_hidden=include_hidden,
                tree_snapshot=current_tree,
            ),
            policy_constraints=policy_constraints,
        )


class MemoryMigrationPlanningModule(dspy.Module):
    """Compose memory audit + migration planning into one runtime module."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        config = build_runtime_module_config(
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )
        self._memory_structure_audit = MemoryStructureAuditPlanningModule(
            interpreter=config.interpreter,
            max_iterations=config.max_iterations,
            max_llm_calls=config.max_llm_calls,
            verbose=config.verbose,
        )
        self._memory_structure_migration_plan = _create_configured_runtime_rlm(
            config,
            signature=MemoryStructureMigrationPlanSignature,
        )

    def forward(
        self,
        *,
        approved_constraints: str = "No destructive operation without explicit confirmation and rollback.",
        usage_goals: str = "Keep memory discoverable, consistent, and easy to maintain.",
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
        audit_findings: list[str] | None = None,
    ) -> dspy.Prediction:
        if audit_findings is None:
            audit_prediction = self._memory_structure_audit(
                usage_goals=usage_goals,
                root_path=root_path,
                max_depth=max_depth,
                include_hidden=include_hidden,
            )
            audit_findings = list(getattr(audit_prediction, "issues", []) or [])

        return self._memory_structure_migration_plan(
            audit_findings=list(audit_findings or []),
            approved_constraints=approved_constraints,
        )


class ClarificationQuestionPlanningModule(_MemoryTreePrimedModule):
    """Compose memory context gathering with clarification-question generation."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        config = build_runtime_module_config(
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )
        super().__init__(config=config)
        self._clarification_questions = _create_configured_runtime_rlm(
            config,
            signature=ClarificationQuestionSignature,
        )

    def forward(
        self,
        *,
        request: str,
        operation_risk: str = "medium",
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
        available_context: str = "",
    ) -> dspy.Prediction:
        risk_norm = str(operation_risk).strip().lower()
        if risk_norm not in {"low", "medium", "high"}:
            risk_norm = "medium"

        return self._clarification_questions(
            ambiguous_request=request,
            available_context=self._resolve_tree_context(
                root_path=root_path,
                max_depth=max_depth,
                include_hidden=include_hidden,
                available_context=available_context,
            ),
            operation_risk=risk_norm,
        )


# ---------------------------------------------------------------------------
# Recursive workspace orchestrator (Level 4 RLM)
# ---------------------------------------------------------------------------


class RecursiveWorkspaceModule(dspy.Module):
    """Multi-pass recursive orchestrator using the workspace signatures.

    Runs a bounded loop: assemble context → plan decomposition → execute
    subqueries → verify → reflect (finalize / recurse / repair). Each
    sub-module is a ``dspy.RLM`` so the LLM writes code at every step.
    Evidence is persisted across passes via the host-mediated NeonDB bridge.
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
    ) -> None:
        super().__init__()
        self.interpreter = interpreter
        self.max_passes = max_passes
        self.max_repair_attempts = max_repair_attempts
        self.subquery_budget = subquery_budget
        self.context_budget_chars = context_budget_chars

        rlm_kwargs: dict[str, Any] = {
            "interpreter": interpreter,
            "max_iterations": max_iterations,
            "max_llm_calls": max_llm_calls,
            "verbose": verbose,
            "sub_lm": sub_lm,
        }

        self._assembler = create_runtime_rlm(
            signature=AssembleRecursiveWorkspaceContext, **rlm_kwargs
        )
        self._planner = create_runtime_rlm(
            signature=PlanRecursiveSubqueries, **rlm_kwargs
        )
        self._verifier = create_runtime_rlm(
            signature=VerifyRecursiveAggregation, **rlm_kwargs
        )
        self._reflector = create_runtime_rlm(
            signature=ReflectAndReviseWorkspaceStep, **rlm_kwargs
        )
        self._repairer = create_runtime_rlm(signature=PlanRecursiveRepair, **rlm_kwargs)

    def forward(
        self,
        *,
        user_request: str,
        context: str = "",
        working_memory_catalog: list[str] | None = None,
    ) -> dspy.Prediction:
        plan = f"Initial: {user_request}"
        evidence_catalog: list[str] = []
        latest_result = context
        repair_count = 0

        for pass_idx in range(self.max_passes):
            loop_state = self._loop_state_summary(pass_idx, repair_count)

            memory_catalog = working_memory_catalog or self._fetch_memory_catalog()
            assemble = self._assembler(
                user_request=user_request,
                current_plan=plan,
                loop_state=loop_state,
                working_memory_catalog=memory_catalog,
                recent_sandbox_evidence_catalog=evidence_catalog,
                latest_tool_or_code_result=latest_result,
                context_budget=self.context_budget_chars,
            )
            assembled_context = str(getattr(assemble, "assembled_context_summary", ""))

            decomposition = self._planner(
                user_request=user_request,
                assembled_recursive_context=assembled_context,
                current_plan=plan,
                loop_state=loop_state,
                latest_sandbox_evidence=latest_result,
                subquery_budget=self.subquery_budget,
            )
            subqueries = list(getattr(decomposition, "subqueries", []) or [])
            mode = str(getattr(decomposition, "decomposition_mode", "single_pass"))
            aggregation_plan = str(getattr(decomposition, "aggregation_plan", ""))

            if not subqueries:
                subqueries = [user_request]
            outputs = self._execute_subqueries(subqueries, assembled_context, mode)
            latest_result = "\n---\n".join(outputs)

            self._store_pass_evidence(pass_idx, outputs)
            evidence_catalog = [
                f"pass_{pass_idx}_output_{i}" for i in range(len(outputs))
            ]

            verification = self._verifier(
                user_request=user_request,
                assembled_recursive_context=assembled_context,
                decomposition_plan_summary=aggregation_plan,
                collected_subquery_outputs=outputs,
                latest_sandbox_evidence=latest_result,
            )
            status = str(getattr(verification, "verification_status", "sufficient"))
            verified_summary = str(
                getattr(verification, "verified_summary", latest_result)
            )

            if status == "sufficient":
                return dspy.Prediction(
                    answer=verified_summary,
                    passes=pass_idx + 1,
                    status="sufficient",
                )

            reflection = self._reflector(
                user_request=user_request,
                working_memory_summary=assembled_context,
                current_plan=plan,
                latest_sandbox_evidence=latest_result,
                latest_tool_or_code_result=verified_summary,
                loop_state=loop_state,
            )
            action = str(getattr(reflection, "next_action", "finalize"))
            plan = str(getattr(reflection, "revised_plan", plan))

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
                    verification_summary=verified_summary,
                    latest_sandbox_evidence=latest_result,
                    latest_failure_signals=str(
                        getattr(verification, "contradictions", [])
                    ),
                    repair_budget=max(1, self.subquery_budget // 2),
                )
                repair_queries = list(getattr(repair, "repair_subqueries", []) or [])
                if repair_queries:
                    repair_outputs = self._execute_subqueries(
                        repair_queries, assembled_context, "serial"
                    )
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

    def _execute_subqueries(
        self,
        subqueries: list[str],
        context: str,
        mode: str,
    ) -> list[str]:
        from fleet_rlm.runtime.tools.rlm_delegate import (
            _delegate_interpreter,
            delegate_to_rlm,
            delegate_to_rlm_batched,
            set_delegate_interpreter,
        )

        if mode == "fan_out" and len(subqueries) > 1:
            token = set_delegate_interpreter(self.interpreter)
            try:
                result = delegate_to_rlm_batched(queries=subqueries, context=context)
            finally:
                _delegate_interpreter.reset(token)
            if result.get("status") == "ok":
                return [r.get("answer", "") for r in result.get("results", [])]
            return [str(result.get("errors", "execution failed"))]

        outputs: list[str] = []
        for query in subqueries:
            token = set_delegate_interpreter(self.interpreter)
            try:
                result = delegate_to_rlm(query=query, context=context)
            finally:
                _delegate_interpreter.reset(token)
            outputs.append(result.get("answer", result.get("error", "")))
        return outputs

    def _fetch_memory_catalog(self) -> list[str]:
        from fleet_rlm.integrations.daytona.evidence_bridge import list_evidence

        result = list_evidence(self.interpreter, scope="run", limit=50)
        return [
            f"{item['scope_id']}:{item['kind']}" for item in result.get("items", [])
        ]

    def _store_pass_evidence(self, pass_idx: int, outputs: list[str]) -> None:
        from fleet_rlm.integrations.daytona.evidence_bridge import store_evidence

        for i, output in enumerate(outputs):
            store_evidence(
                self.interpreter,
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
