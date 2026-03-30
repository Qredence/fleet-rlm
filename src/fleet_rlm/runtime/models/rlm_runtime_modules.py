"""Registry-driven DSPy runtime modules for long-context operations."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, MutableMapping, Protocol, cast

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


@dataclass(frozen=True)
class RuntimeModuleBuildConfig:
    """Shared constructor parameters for runtime-module RLMs."""

    interpreter: Any
    max_iterations: int
    max_llm_calls: int
    verbose: bool


def build_runtime_module_config(
    *,
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    verbose: bool,
) -> RuntimeModuleBuildConfig:
    return RuntimeModuleBuildConfig(
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
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
        verbose=config.verbose,
    )


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
        self._rlm = _create_configured_runtime_rlm(
            build_runtime_module_config(
                interpreter=interpreter,
                max_iterations=max_iterations,
                max_llm_calls=max_llm_calls,
                verbose=verbose,
            ),
            signature=self.signature_cls,
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


RUNTIME_MODULE_NAMES: frozenset[str] = frozenset(RUNTIME_MODULE_REGISTRY)


@lru_cache(maxsize=None)
def _signature_runtime_module_class(
    class_name: str,
    signature: type[dspy.Signature],
    doc: str,
) -> type[dspy.Module]:
    return cast(
        type[dspy.Module],
        type(
            class_name,
            (_RuntimeSignatureModule,),
            {
                "signature_cls": signature,
                "__doc__": doc,
            },
        ),
    )


def runtime_module_class(definition: RuntimeModuleDefinition) -> type[dspy.Module]:
    """Return the concrete module class for one registry definition."""
    if definition.module_class is not None:
        module_class = definition.module_class
    else:
        module_class = _signature_runtime_module_class(
            definition.class_name,
            definition.signature,
            definition.doc,
        )
    globals().setdefault(definition.class_name, module_class)
    return module_class


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
        runtime_module_class(definition),
    )
    return wrapper_class(
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )


def get_or_build_runtime_module(
    cache: MutableMapping[str, dspy.Module],
    name: str,
    *,
    config: RuntimeModuleBuildConfig,
) -> dspy.Module:
    """Return a cached runtime module, building it on first access."""
    module = cache.get(name)
    if module is not None:
        return module

    module = build_runtime_module(
        name,
        interpreter=config.interpreter,
        max_iterations=config.max_iterations,
        max_llm_calls=config.max_llm_calls,
        verbose=config.verbose,
    )
    cache[name] = module
    return module
