"""Registry mapping names to runtime module definitions and build helpers."""

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
    SummarizeLongDocument,
    VolumeFileTreeSignature,
)
from fleet_rlm.runtime.models.builders import (
    ClarificationQuestionPlanningModule,
    GroundedAnswerSynthesisModule,
    MemoryActionPlanningModule,
    MemoryMigrationPlanningModule,
    MemoryStructureAuditPlanningModule,
    RLMVariableExecutionModule,
    RuntimeModuleBuildConfig,
    _create_configured_runtime_rlm,
    build_runtime_module_config,
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


@dataclass(frozen=True)
class RuntimeModuleDefinition:
    """Registry entry for a runtime module."""

    signature: type[dspy.Signature]
    class_name: str
    doc: str
    module_class: type[dspy.Module] | None = None
    variable_mode: bool = False
    """When True, ``build_runtime_module`` wraps this entry in
    ``RLMVariableExecutionModule`` instead of the generic
    ``_RuntimeSignatureModule``.  Use for long-context signatures whose
    primary input should be a REPL variable (Algorithm 1 pattern)."""


RUNTIME_MODULE_REGISTRY: dict[str, RuntimeModuleDefinition] = {
    "analyze_long_document": RuntimeModuleDefinition(
        signature=AnalyzeLongDocument,
        class_name="AnalyzeLongDocumentModule",
        doc="Runtime wrapper for ``AnalyzeLongDocument`` RLM execution.",
        variable_mode=True,
    ),
    "summarize_long_document": RuntimeModuleDefinition(
        signature=SummarizeLongDocument,
        class_name="SummarizeLongDocumentModule",
        doc="Runtime wrapper for ``SummarizeLongDocument`` RLM execution.",
        variable_mode=True,
    ),
    "extract_from_logs": RuntimeModuleDefinition(
        signature=ExtractFromLogs,
        class_name="ExtractFromLogsModule",
        doc="Runtime wrapper for ``ExtractFromLogs`` RLM execution.",
        variable_mode=True,
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
    """Build a runtime module from the canonical registry.

    When the definition has ``variable_mode=True``, returns an
    ``RLMVariableExecutionModule`` that leverages ``dspy.RLM``'s native
    REPL variable injection (Algorithm 1, arXiv 2512.24601v2).
    """

    definition = RUNTIME_MODULE_REGISTRY.get(name)
    if definition is None:
        raise ValueError(f"Unknown runtime module: {name}")

    if definition.variable_mode:
        return RLMVariableExecutionModule(
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

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
