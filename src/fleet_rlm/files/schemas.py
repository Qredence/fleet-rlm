"""Pydantic schemas for staged chat attachments."""

from __future__ import annotations

import json
from typing import Any

from dspy.primitives.sandbox_serializable import SandboxSerializable
from pydantic import BaseModel, Field, field_validator, model_validator

from fleet_rlm.tools.paths import (
    PathSafetyError,
    reject_backslash_paths,
    reject_encoded_traversal_tokens,
    reject_host_drive_paths,
)


class AttachmentRef(BaseModel):
    """Reference to one uploaded or staged user file."""

    id: str = Field(min_length=1, description="Stable attachment identifier.")
    filename: str = Field(min_length=1, description="Display filename (basename only).")
    mime_type: str | None = Field(default=None, description="Best-effort MIME type for the attachment.")
    size_bytes: int = Field(ge=0, description="Attachment size in bytes.")
    checksum: str | None = Field(default=None, description="Optional content checksum when available.")
    staging_path: str | None = Field(
        default=None,
        description="Safe relative staging path under the approved uploads root.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Optional attachment metadata bag.")

    @field_validator("filename")
    @classmethod
    def _filename_must_be_simple(cls, value: str) -> str:
        raw = value.strip()
        if not raw or "/" in raw or "\\" in raw or ".." in raw:
            raise ValueError("filename must be a simple basename")
        return raw

    @field_validator("staging_path")
    @classmethod
    def _staging_path_must_be_safe_relative(cls, value: str | None) -> str | None:
        if value is None:
            return None
        raw = value.strip()
        if not raw:
            return None
        try:
            reject_encoded_traversal_tokens(raw)
            reject_backslash_paths(raw)
            reject_host_drive_paths(raw)
        except PathSafetyError as exc:
            raise ValueError(str(exc)) from exc
        if raw.startswith("/") or ".." in raw.split("/"):
            raise ValueError("staging_path must be a safe relative path")
        return raw


class AttachedFiles(BaseModel, SandboxSerializable):
    """Collection of attachment references for a future chat turn."""

    attachments: list[AttachmentRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _attachment_ids_must_be_unique(self) -> "AttachedFiles":
        ids = [attachment.id for attachment in self.attachments]
        if len(ids) != len(set(ids)):
            raise ValueError("attachment ids must be unique")
        return self

    def sandbox_setup(self) -> str:
        return "import json"

    def to_sandbox(self) -> bytes:
        payload = {
            "attachments": [
                {
                    "id": attachment.id,
                    "filename": attachment.filename,
                    "mime_type": attachment.mime_type,
                    "size_bytes": attachment.size_bytes,
                    "checksum": attachment.checksum,
                    "staging_path": attachment.staging_path,
                    "metadata": dict(attachment.metadata),
                }
                for attachment in self.attachments
            ]
        }
        return json.dumps(payload, sort_keys=True).encode("utf-8")

    def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
        return f"{var_name} = json.loads({data_expr})"

    def rlm_preview(self, max_chars: int = 500) -> str:
        _ = max_chars
        count = len(self.attachments)
        if count == 0:
            return "dict with key 'attachments'; no attached files"
        previews = []
        for attachment in self.attachments[:20]:
            parts = [attachment.filename, f"{attachment.size_bytes} bytes"]
            if attachment.mime_type:
                parts.append(str(attachment.mime_type))
            previews.append(" (".join([parts[0], ", ".join(parts[1:]) + ")"]))
        suffix = "" if count <= 20 else f"; {count - 20} more hidden"
        return "dict with key 'attachments'; metadata only; " + "; ".join(previews) + suffix


__all__ = ["AttachedFiles", "AttachmentRef"]
