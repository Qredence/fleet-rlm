"""Concrete persistence adapters for Fleet RLM domain interfaces.

Organized into 3 authoritative persistence domains:
1. `sessions.py`: Session catalog, artifacts, attachments, warm pools, sandbox bindings
2. `run_state.py`: Run/turn lifecycle, CAS claims, settlement, recovery, liveness
3. `outbox.py`: Memory promotion outbox with CAS claim fencing
"""

from fleet_rlm.persistence.repositories.outbox import (
    ClaimedMemoryPromotionIntent,
    MemoryPromotionOutboxSummary,
    SqlAlchemyMemoryPromotionOutbox,
)
from fleet_rlm.persistence.repositories.run_state import (
    ReconciliationSummary,
    SqlAlchemyRunStateStore,
)
from fleet_rlm.persistence.repositories.sessions import (
    SandboxBinding,
    SessionRecord,
    SqlAlchemyArtifactCatalog,
    SqlAlchemyAttachmentCatalog,
    SqlAlchemySandboxBindingStore,
    SqlAlchemySessionCatalog,
)

__all__ = [
    "ClaimedMemoryPromotionIntent",
    "MemoryPromotionOutboxSummary",
    "ReconciliationSummary",
    "SandboxBinding",
    "SessionRecord",
    "SqlAlchemyArtifactCatalog",
    "SqlAlchemyAttachmentCatalog",
    "SqlAlchemyMemoryPromotionOutbox",
    "SqlAlchemyRunStateStore",
    "SqlAlchemySandboxBindingStore",
    "SqlAlchemySessionCatalog",
]
