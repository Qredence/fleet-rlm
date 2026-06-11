"""Runtime DSPy execution modules — the canonical home for all agent runtime modules.

This package replaces the older ``runtime.models`` package layout, which misleadingly
named DSPy execution modules as "models." All public symbols are re-exported here
for convenience.

Architecture:
    - ``escalating`` — ChainOfThought→RLM auto-escalating unified agent module
    - ``evidence`` — EvidenceSink protocol
    - ``factory`` — low-level module construction helpers
    - ``grounded_answer`` — chunking + grounded-answer synthesis
    - ``memory`` — memory-tree priming and audit/action/migration/clarification
    - ``registry`` — name→definition mapping and cached build helpers
    - ``skill_selection`` — proactive skill selection and context injection
    - ``workspace`` — multi-pass recursive workspace orchestrator

Large inputs are passed to ``dspy.RLM`` as ``SandboxSerializable`` models from
:mod:`fleet_rlm.runtime.sandbox_types`; dspy injects them into the sandbox
natively, so no variable-mode wrapper is needed.
"""

from fleet_rlm.runtime.modules.escalating import EscalatingFleetModule
from fleet_rlm.runtime.modules.evidence import EvidenceSink
from fleet_rlm.runtime.modules.factory import (
    VARIABLE_MODE_MAX_OUTPUT_CHARS,
    VARIABLE_MODE_THRESHOLD,
    RuntimeModuleBuildConfig,
    build_recursive_subquery_rlm,
    build_runtime_module_config,
    create_runtime_rlm,
    interpreter_delegation_tools,
)
from fleet_rlm.runtime.modules.grounded_answer import (
    GroundedAnswerSynthesisModule,
    _chunk_document,
    _chunk_to_text,
    _coerce_bounded_int,
    _normalize_chunk_strategy,
)
from fleet_rlm.runtime.modules.memory import (
    ClarificationQuestionPlanningModule,
    MemoryActionPlanningModule,
    MemoryMigrationPlanningModule,
    MemoryStructureAuditPlanningModule,
)
from fleet_rlm.runtime.modules.registry import (
    RUNTIME_MODULE_NAMES,
    RUNTIME_MODULE_REGISTRY,
    RuntimeModuleDefinition,
    build_runtime_module,
    get_or_build_runtime_module,
    runtime_module_class,
)
from fleet_rlm.runtime.modules.skill_selection import (
    AVAILABLE_SKILLS,
    SkillSelectionModule,
)
from fleet_rlm.runtime.modules.workspace import (
    _MISSING_SOURCE_FAILURE_MARKERS,
    _NON_SUFFICIENT_FAILURE_STATUSES,
    _SUBQUERY_FAILURE_MARKERS,
    _SUBQUERY_FAILURE_REASONS,
    RecursiveWorkspaceModule,
)

__all__ = [
    # Escalating
    "EscalatingFleetModule",
    # Evidence
    "EvidenceSink",
    # Factory
    "RuntimeModuleBuildConfig",
    "VARIABLE_MODE_MAX_OUTPUT_CHARS",
    "VARIABLE_MODE_THRESHOLD",
    "build_recursive_subquery_rlm",
    "build_runtime_module_config",
    "create_runtime_rlm",
    "interpreter_delegation_tools",
    # Grounded Answer
    "GroundedAnswerSynthesisModule",
    "_chunk_document",
    "_chunk_to_text",
    "_coerce_bounded_int",
    "_normalize_chunk_strategy",
    # Memory
    "ClarificationQuestionPlanningModule",
    "MemoryActionPlanningModule",
    "MemoryMigrationPlanningModule",
    "MemoryStructureAuditPlanningModule",
    # Registry
    "RUNTIME_MODULE_NAMES",
    "RUNTIME_MODULE_REGISTRY",
    "RuntimeModuleDefinition",
    "build_runtime_module",
    "get_or_build_runtime_module",
    "runtime_module_class",
    # Skill Selection
    "AVAILABLE_SKILLS",
    "SkillSelectionModule",
    # Workspace
    "RecursiveWorkspaceModule",
    "_MISSING_SOURCE_FAILURE_MARKERS",
    "_NON_SUFFICIENT_FAILURE_STATUSES",
    "_SUBQUERY_FAILURE_MARKERS",
    "_SUBQUERY_FAILURE_REASONS",
]
