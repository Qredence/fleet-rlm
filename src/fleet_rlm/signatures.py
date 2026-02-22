"""Compatibility re-export module for DSPy signatures.

Canonical signatures are split into production/runtime (`signatures_prod`) and
demo/example (`signatures_demo`) modules. This file remains as a compatibility
shim to avoid breaking imports while call sites migrate incrementally.
"""

from .signatures_demo import (
    ExtractAPIEndpoints,
    ExtractArchitecture,
    ExtractWithCustomTool,
    FindErrorPatterns,
)
from .signatures_prod import (
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

__all__ = [
    "ExtractArchitecture",
    "ExtractAPIEndpoints",
    "FindErrorPatterns",
    "ExtractWithCustomTool",
    "AnalyzeLongDocument",
    "SummarizeLongDocument",
    "ExtractFromLogs",
    "GroundedAnswerWithCitations",
    "IncidentTriageFromLogs",
    "CodeChangePlan",
    "CoreMemoryUpdateProposal",
    "VolumeFileTreeSignature",
    "MemoryActionIntentSignature",
    "MemoryStructureAuditSignature",
    "MemoryStructureMigrationPlanSignature",
    "ClarificationQuestionSignature",
]
