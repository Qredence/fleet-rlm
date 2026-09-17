"""Unified Attachment domain for Fleet RLM."""

import sys

from fleet_rlm.attachments.service import (
    DEFAULT_MAX_BYTES,
    AsyncByteSource,
    AttachmentAccess,
    AttachmentBlobGateway,
    AttachmentCatalog,
    AttachmentError,
    AttachmentIntegrityError,
    AttachmentLifecycle,
    AttachmentLifecycleService,
    AttachmentNotFoundError,
    AttachmentPathPolicy,
    AttachmentRef,
    AttachmentRun,
    AttachmentStorageError,
    AttachmentToolHost,
    AttachmentUpload,
    AttachmentValidationError,
    LocalAttachmentBlobGateway,
    LocalAttachmentCatalog,
    LocalAttachmentPathPolicy,
    PreparedAttachment,
    PreparedAttachments,
    RunAttachmentSink,
    StagedAttachment,
    StoredAttachment,
    WorkspaceAttachmentPathPolicy,
    sanitize_filename,
    validate_upload_size,
)

_current_module = sys.modules[__name__]
for _submod in ("errors", "lifecycle", "local_catalog", "models", "paths", "safety", "tools"):
    sys.modules.setdefault(f"fleet_rlm.attachments.{_submod}", _current_module)

__all__ = [
    "DEFAULT_MAX_BYTES",
    "AsyncByteSource",
    "AttachmentAccess",
    "AttachmentBlobGateway",
    "AttachmentCatalog",
    "AttachmentError",
    "AttachmentIntegrityError",
    "AttachmentLifecycle",
    "AttachmentLifecycleService",
    "AttachmentNotFoundError",
    "AttachmentPathPolicy",
    "AttachmentRef",
    "AttachmentRun",
    "AttachmentStorageError",
    "AttachmentToolHost",
    "AttachmentUpload",
    "AttachmentValidationError",
    "LocalAttachmentBlobGateway",
    "LocalAttachmentCatalog",
    "LocalAttachmentPathPolicy",
    "PreparedAttachment",
    "PreparedAttachments",
    "RunAttachmentSink",
    "StagedAttachment",
    "StoredAttachment",
    "WorkspaceAttachmentPathPolicy",
    "sanitize_filename",
    "validate_upload_size",
]
