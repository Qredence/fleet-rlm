"""Registry-driven DSPy runtime modules for long-context operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import dspy

from .signatures import (
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
        class_name="GroundedAnswerWithCitationsModule",
        doc="Runtime wrapper for ``GroundedAnswerWithCitations`` RLM execution.",
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
        class_name="MemoryActionIntentModule",
        doc="Runtime wrapper for ``MemoryActionIntentSignature`` RLM execution.",
    ),
    "memory_structure_audit": RuntimeModuleDefinition(
        signature=MemoryStructureAuditSignature,
        class_name="MemoryStructureAuditModule",
        doc="Runtime wrapper for ``MemoryStructureAuditSignature`` RLM execution.",
    ),
    "memory_structure_migration_plan": RuntimeModuleDefinition(
        signature=MemoryStructureMigrationPlanSignature,
        class_name="MemoryStructureMigrationPlanModule",
        doc="Runtime wrapper for ``MemoryStructureMigrationPlanSignature`` RLM execution.",
    ),
    "clarification_questions": RuntimeModuleDefinition(
        signature=ClarificationQuestionSignature,
        class_name="ClarificationQuestionModule",
        doc="Runtime wrapper for ``ClarificationQuestionSignature`` RLM execution.",
    ),
}


def _register_runtime_module_classes() -> dict[str, type[_RuntimeSignatureModule]]:
    classes: dict[str, type[_RuntimeSignatureModule]] = {}
    for definition in RUNTIME_MODULE_REGISTRY.values():
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
        type[_RuntimeSignatureModule],
        RUNTIME_MODULE_CLASSES[definition.class_name],
    )
    return wrapper_class(
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )
