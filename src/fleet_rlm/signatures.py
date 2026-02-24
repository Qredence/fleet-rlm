"""Re-export module for DSPy signatures.

Production/runtime signatures live in ``signatures_prod``. This file
re-exports them for convenience so callers can ``from .signatures import …``.
"""

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
