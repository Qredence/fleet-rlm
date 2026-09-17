"""SQLAlchemy committed Artifact catalog adapter (consolidated into sessions.py)."""

from __future__ import annotations

from fleet_rlm.persistence.repositories.sessions import (
    CompletedRun,
    SqlAlchemyArtifactCatalog,
    StoredArtifact,
)

__all__ = [
    "CompletedRun",
    "SqlAlchemyArtifactCatalog",
    "StoredArtifact",
]
