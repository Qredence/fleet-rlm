"""Registry-driven DSPy runtime modules for long-context operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, cast

import dspy

from fleet_rlm.runtime.agent.signatures import (
    AnalyzeLongDocument,
    ClarificationQuestionSignature,
    CodeChangePlan,
    CoreMemoryUpdateProposal,
    ExtractFromLogs,
    GroundedAnswerWithCitations,
    IncidentTriageFromLogs,
    MemoryActionIntentSignature,
    MemoryStructureAuditSignature,
    MemoryStructureMigrationPlanSignature,
    RecursiveSubQuerySignature,
    SummarizeLongDocument,
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
    verbose: bool,
    tools: list[Any] | None = None,
) -> dspy.Module:
    """Create a canonical RLM instance for a runtime signature."""

    kwargs: dict[str, Any] = {
        "signature": signature,
        "interpreter": interpreter,
        "max_iterations": max_iterations,
        "max_llm_calls": max_llm_calls,
        "verbose": verbose,
    }
    if tools is not None:
        kwargs["tools"] = tools

    return dspy.RLM(
        **kwargs,
    )


def build_recursive_subquery_rlm(
    *,
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    verbose: bool,
) -> dspy.Module:
    """Build the canonical recursive child-query RLM."""

    return create_runtime_rlm(
        signature=RecursiveSubQuerySignature,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )


@dataclass(frozen=True)
class RuntimeModuleDefinition:
    """Registry entry for a runtime module."""

    signature: type[dspy.Signature]
    class_name: str
    doc: str
    module_class: type[dspy.Module] | None = None


class _RuntimeSignatureModule(dspy.Module):
    """Generic runtime wrapper that forwards keyword arguments into one RLM."""

    signature_cls: type[dspy.Signature]

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._rlm = create_runtime_rlm(
            signature=self.signature_cls,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

    def forward(self, **kwargs: Any) -> dspy.Prediction:
        return self._rlm(**kwargs)


class _RuntimeModuleFactory(Protocol):
    """Callable constructor signature shared by runtime module wrappers."""

    def __call__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> dspy.Module: ...


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
        self._grounded_answer = create_runtime_rlm(
            signature=GroundedAnswerWithCitations,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
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


class MemoryStructureAuditPlanningModule(dspy.Module):
    """Compose a memory-tree snapshot with the audit RLM."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._memory_tree = create_runtime_rlm(
            signature=VolumeFileTreeSignature,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )
        self._memory_structure_audit = create_runtime_rlm(
            signature=MemoryStructureAuditSignature,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
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
        if tree_snapshot is None:
            tree_prediction = self._memory_tree(
                root_path=root_path,
                max_depth=_coerce_bounded_int(
                    max_depth, default=4, minimum=0, maximum=12
                ),
                include_hidden=bool(include_hidden),
            )
            tree_snapshot = list(getattr(tree_prediction, "nodes", []) or [])

        return self._memory_structure_audit(
            tree_snapshot=list(tree_snapshot or []),
            usage_goals=usage_goals,
        )


class MemoryActionPlanningModule(dspy.Module):
    """Compose a memory-tree snapshot with the action-intent RLM."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._memory_tree = create_runtime_rlm(
            signature=VolumeFileTreeSignature,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )
        self._memory_action_intent = create_runtime_rlm(
            signature=MemoryActionIntentSignature,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
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
        if current_tree is None:
            tree_prediction = self._memory_tree(
                root_path=root_path,
                max_depth=_coerce_bounded_int(
                    max_depth, default=4, minimum=0, maximum=12
                ),
                include_hidden=bool(include_hidden),
            )
            current_tree = list(getattr(tree_prediction, "nodes", []) or [])

        return self._memory_action_intent(
            user_request=user_request,
            current_tree=list(current_tree or []),
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
        self._memory_structure_audit = MemoryStructureAuditPlanningModule(
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )
        self._memory_structure_migration_plan = create_runtime_rlm(
            signature=MemoryStructureMigrationPlanSignature,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
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


class ClarificationQuestionPlanningModule(dspy.Module):
    """Compose memory context gathering with clarification-question generation."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._memory_tree = create_runtime_rlm(
            signature=VolumeFileTreeSignature,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )
        self._clarification_questions = create_runtime_rlm(
            signature=ClarificationQuestionSignature,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
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

        context_text = available_context.strip()
        if not context_text:
            tree_prediction = self._memory_tree(
                root_path=root_path,
                max_depth=_coerce_bounded_int(
                    max_depth, default=4, minimum=0, maximum=12
                ),
                include_hidden=bool(include_hidden),
            )
            tree_nodes = list(getattr(tree_prediction, "nodes", []) or [])[:20]
            context_text = f"memory_root={root_path}; nodes_sample={tree_nodes}"

        return self._clarification_questions(
            ambiguous_request=request,
            available_context=context_text,
            operation_risk=risk_norm,
        )


RUNTIME_MODULE_REGISTRY: dict[str, RuntimeModuleDefinition] = {
    "analyze_long_document": RuntimeModuleDefinition(
        signature=AnalyzeLongDocument,
        class_name="AnalyzeLongDocumentModule",
        doc="Runtime wrapper for ``AnalyzeLongDocument`` RLM execution.",
    ),
    "summarize_long_document": RuntimeModuleDefinition(
        signature=SummarizeLongDocument,
        class_name="SummarizeLongDocumentModule",
        doc="Runtime wrapper for ``SummarizeLongDocument`` RLM execution.",
    ),
    "extract_from_logs": RuntimeModuleDefinition(
        signature=ExtractFromLogs,
        class_name="ExtractFromLogsModule",
        doc="Runtime wrapper for ``ExtractFromLogs`` RLM execution.",
    ),
    "grounded_answer": RuntimeModuleDefinition(
        signature=GroundedAnswerWithCitations,
        class_name="GroundedAnswerSynthesisModule",
        doc="Compose chunking + ``GroundedAnswerWithCitations`` into one runtime module.",
        module_class=GroundedAnswerSynthesisModule,
    ),
    "triage_incident_logs": RuntimeModuleDefinition(
        signature=IncidentTriageFromLogs,
        class_name="IncidentTriageFromLogsModule",
        doc="Runtime wrapper for ``IncidentTriageFromLogs`` RLM execution.",
    ),
    "plan_code_change": RuntimeModuleDefinition(
        signature=CodeChangePlan,
        class_name="CodeChangePlanModule",
        doc="Runtime wrapper for ``CodeChangePlan`` RLM execution.",
    ),
    "propose_core_memory_update": RuntimeModuleDefinition(
        signature=CoreMemoryUpdateProposal,
        class_name="CoreMemoryUpdateProposalModule",
        doc="Runtime wrapper for ``CoreMemoryUpdateProposal`` RLM execution.",
    ),
    "memory_tree": RuntimeModuleDefinition(
        signature=VolumeFileTreeSignature,
        class_name="VolumeFileTreeModule",
        doc="Runtime wrapper for ``VolumeFileTreeSignature`` RLM execution.",
    ),
    "memory_action_intent": RuntimeModuleDefinition(
        signature=MemoryActionIntentSignature,
        class_name="MemoryActionPlanningModule",
        doc="Compose tree gathering + ``MemoryActionIntentSignature`` into one runtime module.",
        module_class=MemoryActionPlanningModule,
    ),
    "memory_structure_audit": RuntimeModuleDefinition(
        signature=MemoryStructureAuditSignature,
        class_name="MemoryStructureAuditPlanningModule",
        doc="Compose tree gathering + ``MemoryStructureAuditSignature`` into one runtime module.",
        module_class=MemoryStructureAuditPlanningModule,
    ),
    "memory_structure_migration_plan": RuntimeModuleDefinition(
        signature=MemoryStructureMigrationPlanSignature,
        class_name="MemoryMigrationPlanningModule",
        doc="Compose audit + ``MemoryStructureMigrationPlanSignature`` into one runtime module.",
        module_class=MemoryMigrationPlanningModule,
    ),
    "clarification_questions": RuntimeModuleDefinition(
        signature=ClarificationQuestionSignature,
        class_name="ClarificationQuestionPlanningModule",
        doc="Compose context gathering + ``ClarificationQuestionSignature`` into one runtime module.",
        module_class=ClarificationQuestionPlanningModule,
    ),
}


def _register_runtime_module_classes() -> dict[str, type[dspy.Module]]:
    classes: dict[str, type[dspy.Module]] = {}
    for definition in RUNTIME_MODULE_REGISTRY.values():
        if definition.module_class is not None:
            module_class = definition.module_class
        else:
            module_class = cast(
                type[_RuntimeSignatureModule],
                type(
                    definition.class_name,
                    (_RuntimeSignatureModule,),
                    {
                        "signature_cls": definition.signature,
                        "__doc__": definition.doc,
                    },
                ),
            )
        globals()[definition.class_name] = module_class
        classes[definition.class_name] = module_class
    return classes


RUNTIME_MODULE_CLASSES = _register_runtime_module_classes()
RUNTIME_MODULE_NAMES: frozenset[str] = frozenset(RUNTIME_MODULE_REGISTRY)


def build_runtime_module(
    name: str,
    *,
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    verbose: bool,
) -> dspy.Module:
    """Build a runtime module from the canonical registry."""

    definition = RUNTIME_MODULE_REGISTRY.get(name)
    if definition is None:
        raise ValueError(f"Unknown runtime module: {name}")

    wrapper_class = cast(
        _RuntimeModuleFactory,
        RUNTIME_MODULE_CLASSES[definition.class_name],
    )
    return wrapper_class(
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )
