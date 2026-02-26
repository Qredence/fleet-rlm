"""Reusable DSPy module wrappers for ReAct long-context runtime tasks.

This module provides canonical wrappers around ``dspy.RLM`` so tool handlers
do not recreate RLM instances on every call.
"""

from __future__ import annotations

from typing import Any

import dspy

from .signatures import (
    AnalyzeLongDocument,
    CodeChangePlan,
    ClarificationQuestionSignature,
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


class AnalyzeLongDocumentModule(dspy.Module):
    """Runtime wrapper for ``AnalyzeLongDocument`` RLM execution."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._rlm = dspy.RLM(
            signature=AnalyzeLongDocument,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

    def forward(self, *, document: str, query: str) -> dspy.Prediction:
        return self._rlm(document=document, query=query)


class SummarizeLongDocumentModule(dspy.Module):
    """Runtime wrapper for ``SummarizeLongDocument`` RLM execution."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._rlm = dspy.RLM(
            signature=SummarizeLongDocument,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

    def forward(self, *, document: str, focus: str) -> dspy.Prediction:
        return self._rlm(document=document, focus=focus)


class ExtractFromLogsModule(dspy.Module):
    """Runtime wrapper for ``ExtractFromLogs`` RLM execution."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._rlm = dspy.RLM(
            signature=ExtractFromLogs,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

    def forward(self, *, logs: str, query: str) -> dspy.Prediction:
        return self._rlm(logs=logs, query=query)


class GroundedAnswerWithCitationsModule(dspy.Module):
    """Runtime wrapper for ``GroundedAnswerWithCitations`` RLM execution."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._rlm = dspy.RLM(
            signature=GroundedAnswerWithCitations,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

    def forward(
        self, *, query: str, evidence_chunks: list[str], response_style: str
    ) -> dspy.Prediction:
        return self._rlm(
            query=query,
            evidence_chunks=evidence_chunks,
            response_style=response_style,
        )


class IncidentTriageFromLogsModule(dspy.Module):
    """Runtime wrapper for ``IncidentTriageFromLogs`` RLM execution."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._rlm = dspy.RLM(
            signature=IncidentTriageFromLogs,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

    def forward(
        self, *, logs: str, service_context: str, query: str
    ) -> dspy.Prediction:
        return self._rlm(logs=logs, service_context=service_context, query=query)


class CodeChangePlanModule(dspy.Module):
    """Runtime wrapper for ``CodeChangePlan`` RLM execution."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._rlm = dspy.RLM(
            signature=CodeChangePlan,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

    def forward(
        self, *, task: str, repo_context: str, constraints: str
    ) -> dspy.Prediction:
        return self._rlm(task=task, repo_context=repo_context, constraints=constraints)


class CoreMemoryUpdateProposalModule(dspy.Module):
    """Runtime wrapper for ``CoreMemoryUpdateProposal`` RLM execution."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._rlm = dspy.RLM(
            signature=CoreMemoryUpdateProposal,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

    def forward(self, *, turn_history: str, current_memory: str) -> dspy.Prediction:
        return self._rlm(turn_history=turn_history, current_memory=current_memory)


class VolumeFileTreeModule(dspy.Module):
    """Runtime wrapper for ``VolumeFileTreeSignature`` RLM execution."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._rlm = dspy.RLM(
            signature=VolumeFileTreeSignature,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

    def forward(
        self, *, root_path: str, max_depth: int, include_hidden: bool
    ) -> dspy.Prediction:
        return self._rlm(
            root_path=root_path, max_depth=max_depth, include_hidden=include_hidden
        )


class MemoryActionIntentModule(dspy.Module):
    """Runtime wrapper for ``MemoryActionIntentSignature`` RLM execution."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._rlm = dspy.RLM(
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
        current_tree: list[dict[str, str]],
        policy_constraints: str,
    ) -> dspy.Prediction:
        return self._rlm(
            user_request=user_request,
            current_tree=current_tree,
            policy_constraints=policy_constraints,
        )


class MemoryStructureAuditModule(dspy.Module):
    """Runtime wrapper for ``MemoryStructureAuditSignature`` RLM execution."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._rlm = dspy.RLM(
            signature=MemoryStructureAuditSignature,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

    def forward(
        self, *, tree_snapshot: list[dict[str, str]], usage_goals: str
    ) -> dspy.Prediction:
        return self._rlm(tree_snapshot=tree_snapshot, usage_goals=usage_goals)


class MemoryStructureMigrationPlanModule(dspy.Module):
    """Runtime wrapper for ``MemoryStructureMigrationPlanSignature`` RLM execution."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._rlm = dspy.RLM(
            signature=MemoryStructureMigrationPlanSignature,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

    def forward(
        self, *, audit_findings: list[str], approved_constraints: str
    ) -> dspy.Prediction:
        return self._rlm(
            audit_findings=audit_findings, approved_constraints=approved_constraints
        )


class ClarificationQuestionModule(dspy.Module):
    """Runtime wrapper for ``ClarificationQuestionSignature`` RLM execution."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._rlm = dspy.RLM(
            signature=ClarificationQuestionSignature,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

    def forward(
        self, *, ambiguous_request: str, available_context: str, operation_risk: str
    ) -> dspy.Prediction:
        return self._rlm(
            ambiguous_request=ambiguous_request,
            available_context=available_context,
            operation_risk=operation_risk,
        )
