"""Runtime module factory for ReAct agent long-context operations.

This module provides lazy-loading constructors for DSPy modules that handle
long-context operations like document analysis, summarization, and memory management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import dspy

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent


def get_runtime_module(agent: "RLMReActChatAgent", name: str) -> dspy.Module:
    """Return a cached long-context runtime module by name.

    Lazily constructs and caches DSPy modules for long-context operations.
    Modules are created with the agent's interpreter and configuration.

    Args:
        agent: The RLMReActChatAgent instance requesting the module
        name: The module name (e.g., 'analyze_long_document', 'grounded_answer')

    Returns:
        The requested DSPy module instance

    Raises:
        ValueError: If the module name is not recognized
    """
    module = agent._runtime_modules.get(name)
    if module is not None:
        return module

    from .rlm_runtime_modules import (
        AnalyzeLongDocumentModule,
        ClarificationQuestionModule,
        CodeChangePlanModule,
        CoreMemoryUpdateProposalModule,
        ExtractFromLogsModule,
        GroundedAnswerWithCitationsModule,
        IncidentTriageFromLogsModule,
        MemoryActionIntentModule,
        MemoryStructureAuditModule,
        MemoryStructureMigrationPlanModule,
        SummarizeLongDocumentModule,
        VolumeFileTreeModule,
    )

    # Map module names to (class, kwargs) tuples
    constructors: dict[str, tuple[type[dspy.Module], dict[str, Any]]] = {
        "analyze_long_document": (
            AnalyzeLongDocumentModule,
            {},
        ),
        "summarize_long_document": (
            SummarizeLongDocumentModule,
            {},
        ),
        "extract_from_logs": (
            ExtractFromLogsModule,
            {},
        ),
        "grounded_answer": (
            GroundedAnswerWithCitationsModule,
            {},
        ),
        "triage_incident_logs": (
            IncidentTriageFromLogsModule,
            {},
        ),
        "plan_code_change": (
            CodeChangePlanModule,
            {},
        ),
        "propose_core_memory_update": (
            CoreMemoryUpdateProposalModule,
            {},
        ),
        "memory_tree": (
            VolumeFileTreeModule,
            {},
        ),
        "memory_action_intent": (
            MemoryActionIntentModule,
            {},
        ),
        "memory_structure_audit": (
            MemoryStructureAuditModule,
            {},
        ),
        "memory_structure_migration_plan": (
            MemoryStructureMigrationPlanModule,
            {},
        ),
        "clarification_questions": (
            ClarificationQuestionModule,
            {},
        ),
    }

    if name not in constructors:
        raise ValueError(f"Unknown runtime module: {name}")

    module_class, _ = constructors[name]
    # Cast to Any because ty cannot infer that each concrete subclass
    # accepts these keyword arguments through the base type[dspy.Module].
    cls = cast(Any, module_class)
    module: dspy.Module = cls(
        interpreter=agent.interpreter,
        max_iterations=agent.rlm_max_iterations,
        max_llm_calls=agent.rlm_max_llm_calls,
        verbose=agent.verbose,
    )
    agent._runtime_modules[name] = module
    return module


# Frozen set of available runtime module names
RUNTIME_MODULE_NAMES: frozenset[str] = frozenset(
    {
        "analyze_long_document",
        "summarize_long_document",
        "extract_from_logs",
        "grounded_answer",
        "triage_incident_logs",
        "plan_code_change",
        "propose_core_memory_update",
        "memory_tree",
        "memory_action_intent",
        "memory_structure_audit",
        "memory_structure_migration_plan",
        "clarification_questions",
    }
)
