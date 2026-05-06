"""Memory-oriented composite DSPy modules (tree priming, audit, action, migration)."""

from __future__ import annotations

from typing import Any

import dspy

from fleet_rlm.runtime.agent.signatures import (
    ClarificationQuestionSignature,
    MemoryActionIntentSignature,
    MemoryStructureAuditSignature,
    MemoryStructureMigrationPlanSignature,
    VolumeFileTreeSignature,
)
from fleet_rlm.runtime.modules.factory import (
    RuntimeModuleBuildConfig,
    _create_configured_runtime_rlm,
    build_runtime_module_config,
)
from fleet_rlm.runtime.modules.grounded_answer import _coerce_bounded_int


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
        """Run a memory-structure audit against the tree snapshot."""
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
        """Plan a memory action based on the user's request."""
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
        """Plan a memory migration, optionally running an audit first."""
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
        """Generate clarification questions for an ambiguous request."""
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


__all__ = [
    "ClarificationQuestionPlanningModule",
    "MemoryActionPlanningModule",
    "MemoryMigrationPlanningModule",
    "MemoryStructureAuditPlanningModule",
    "_MemoryTreePrimedModule",
]
