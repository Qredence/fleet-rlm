"""SQLAlchemy metadata adapters for Attachments (consolidated into sessions.py)."""

from __future__ import annotations

from fleet_rlm.persistence.repositories.sessions import (
    SqlAlchemyAttachmentCatalog,
    StoredAttachment,
)

__all__ = ["SqlAlchemyAttachmentCatalog", "StoredAttachment"]
