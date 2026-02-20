"""Runtime module factory for ReAct agent long-context operations.

This module provides lazy-loading constructors for DSPy modules that handle
long-context operations like document analysis, summarization, and memory management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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

    from ..signatures import (
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

    # Compatibility path: legacy wrapper module class names used in tests
    # and by historical call sites. These wrappers still delegate to dspy.RLM.
    wrapper_names = {
        "analyze_long_document": "AnalyzeLongDocumentModule",
        "summarize_long_document": "SummarizeLongDocumentModule",
        "extract_from_logs": "ExtractFromLogsModule",
        "grounded_answer": "GroundedAnswerWithCitationsModule",
        "triage_incident_logs": "IncidentTriageFromLogsModule",
        "plan_code_change": "CodeChangePlanModule",
        "propose_core_memory_update": "CoreMemoryUpdateProposalModule",
        "memory_tree": "VolumeFileTreeModule",
        "memory_action_intent": "MemoryActionIntentModule",
        "memory_structure_audit": "MemoryStructureAuditModule",
        "memory_structure_migration_plan": "MemoryStructureMigrationPlanModule",
        "clarification_questions": "ClarificationQuestionModule",
    }

    # Map module names to their respective DSPy signatures
    signatures: dict[str, type[dspy.Signature]] = {
        "analyze_long_document": AnalyzeLongDocument,
        "summarize_long_document": SummarizeLongDocument,
        "extract_from_logs": ExtractFromLogs,
        "grounded_answer": GroundedAnswerWithCitations,
        "triage_incident_logs": IncidentTriageFromLogs,
        "plan_code_change": CodeChangePlan,
        "propose_core_memory_update": CoreMemoryUpdateProposal,
        "memory_tree": VolumeFileTreeSignature,
        "memory_action_intent": MemoryActionIntentSignature,
        "memory_structure_audit": MemoryStructureAuditSignature,
        "memory_structure_migration_plan": MemoryStructureMigrationPlanSignature,
        "clarification_questions": ClarificationQuestionSignature,
    }

    if name not in signatures:
        raise ValueError(f"Unknown runtime module: {name}")

    wrapper_class = None
    try:
        from . import rlm_runtime_modules as runtime_mod

        wrapper_class = getattr(runtime_mod, wrapper_names[name], None)
    except Exception:
        wrapper_class = None

    if wrapper_class is not None:
        module = wrapper_class(
            interpreter=agent.interpreter,
            max_iterations=agent.rlm_max_iterations,
            max_llm_calls=agent.rlm_max_llm_calls,
            verbose=agent.verbose,
        )
    else:
        module = dspy.RLM(
            signature=signatures[name],
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
