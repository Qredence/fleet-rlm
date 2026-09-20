"""Concrete persistence adapters for Fleet RLM domain interfaces.

Organized into 3 authoritative persistence domains:
1. `sessions.py`: Session catalog, artifacts, attachments, warm pools, sandbox bindings
2. `turns.py`: Run/turn lifecycle, CAS claims, settlement, recovery, liveness
3. `outbox.py`: Memory promotion outbox with CAS claim fencing
"""

from fleet_rlm.persistence.repositories.outbox import (
    ClaimedMemoryPromotionIntent,
    MemoryPromotionOutboxSummary,
    SqlAlchemyMemoryPromotionOutbox,
)
from fleet_rlm.persistence.repositories.sessions import (
    InMemorySessionCatalog,
    SandboxBinding,
    SessionRecord,
    SqlAlchemyArtifactCatalog,
    SqlAlchemyAttachmentCatalog,
    SqlAlchemySandboxBindingStore,
    SqlAlchemySessionCatalog,
)
from fleet_rlm.persistence.repositories.turns import (
    InMemoryRunStateStore,
    ReconciliationSummary,
    SqlAlchemyRunStateStore,
)

__all__ = [
    "ClaimedMemoryPromotionIntent",
    "InMemoryRunStateStore",
    "InMemorySessionCatalog",
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
