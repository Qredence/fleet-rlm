"""Serialize bounded metadata and authorized Volume context for DSPy RLM."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, cast
from uuid import UUID

import dspy
from pydantic import ValidationError

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.files.memory_models import WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
from fleet_rlm.files.volume_paths import DEFAULT_VOLUME_MOUNT_PATH, validate_mount_path
from fleet_rlm.files.workspace_models import UNAVAILABLE_WORKSPACE_CAPABILITY, WorkspaceCapabilityMetadata
from fleet_rlm.rlm.errors import RLMConfigError
from fleet_rlm.rlm.input_models import (
    AttachmentInput,
    SessionContextInput,
    SkillCardInput,
    TurnPreviewInput,
    WorkspaceCapabilityInput,
    WorkspaceMemoryInput,
)

_MAX_REQUEST_CHARS = 100_000
_MAX_CONTEXT_ATTACHMENT_COUNT = 32
_MAX_PREVIEW_CHARS = 500


@dataclass(frozen=True, slots=True)
class AttachmentContextEntry:
    """Private host-generated descriptor for one staged immutable Attachment."""

    attachment_id: UUID
    filename: str
    content_type: str | None
    byte_size: int
    checksum_sha256: str
    sandbox_path: str

    def __post_init__(self) -> None:
        if not self.filename or len(self.filename) > 255:
            raise ValueError("attachment filename is invalid")
        if self.byte_size <= 0:
            raise ValueError("attachment byte size is invalid")
        if len(self.checksum_sha256) != 64 or any(value not in "0123456789abcdef" for value in self.checksum_sha256):
            raise ValueError("attachment checksum is invalid")
        if not PurePosixPath(self.sandbox_path).is_absolute():
            raise ValueError("attachment sandbox path is invalid")


def _materialize_context_manifest(
    raw_manifest: bytes | str,
    *,
    trusted_mount_root: str,
    expected_manifest_sha256: str,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    """Deterministic adapter for the same manifest contract used by Daytona."""
    try:
        raw = raw_manifest.encode("utf-8") if isinstance(raw_manifest, str) else bytes(raw_manifest)
        if hashlib.sha256(raw).hexdigest() != expected_manifest_sha256:
            raise ValueError
        manifest = json.loads(raw.decode("utf-8"))
        mount_root = os.path.realpath(str(trusted_mount_root))
        if os.path.realpath(str(manifest["mount_root"])) != mount_root:
            raise ValueError
        entries = list(manifest["entries"])
    except Exception as exc:
        raise ValueError("context manifest is invalid") from exc
    values: list[dict[str, Any]] = []
    accesses: list[str] = []
    for entry in entries:
        try:
            path = os.path.realpath(str(entry["sandbox_path"]))
            expected_size = int(entry["byte_size"])
            expected_sha = str(entry["checksum_sha256"])
            if os.path.commonpath((mount_root, path)) != mount_root or path == mount_root:
                raise ValueError
            with open(path, "rb") as handle:
                body = handle.read(expected_size + 1)
            if len(body) != expected_size or hashlib.sha256(body).hexdigest() != expected_sha:
                raise ValueError
            try:
                data: str | bytes = body.decode("utf-8")
                encoding = "utf-8"
                if "\x00" in data:
                    raise UnicodeDecodeError("utf-8", body, 0, 1, "nul")
            except UnicodeDecodeError:
                data = body
                encoding = "bytes"
            attachment_id = str(entry["attachment_id"])
            values.append(
                {
                    "id": attachment_id,
                    "filename": str(entry["filename"]),
                    "content_type": entry.get("content_type"),
                    "byte_size": expected_size,
                    "data": data,
                    "encoding": encoding,
                }
            )
            accesses.append(attachment_id)
        except Exception as exc:
            raise ValueError("prepared context failed integrity verification") from exc
    return values, tuple(accesses)


@dataclass(frozen=True, slots=True)
class AttachmentContextCapsule(dspy.SandboxSerializable):
    """Compact manifest for authorized immutable context already staged in a Volume."""

    entries: tuple[AttachmentContextEntry, ...]
    mount_root: str = DEFAULT_VOLUME_MOUNT_PATH

    def __post_init__(self) -> None:
        mount = validate_mount_path(self.mount_root)
        object.__setattr__(self, "mount_root", str(mount))
        if not self.entries or len(self.entries) > _MAX_CONTEXT_ATTACHMENT_COUNT:
            raise ValueError("attachment context count is invalid")
        for entry in self.entries:
            path = PurePosixPath(entry.sandbox_path)
            if not path.is_relative_to(mount) or path == mount:
                raise ValueError("attachment sandbox path is outside the mounted Volume")

    def sandbox_setup(self) -> str:
        return ""

    def to_sandbox(self) -> bytes:
        payload = {
            "mount_root": self.mount_root,
            "entries": [
                {
                    "attachment_id": str(entry.attachment_id),
                    "filename": entry.filename,
                    "content_type": entry.content_type,
                    "byte_size": entry.byte_size,
                    "checksum_sha256": entry.checksum_sha256,
                    "sandbox_path": entry.sandbox_path,
                }
                for entry in self.entries
            ],
        }
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
        return (
            "try:\n"
            f"    {var_name} = _fleet_load_context_manifest({data_expr})\n"
            "finally:\n"
            "    del _fleet_load_context_manifest"
        )

    def rlm_preview(self, max_chars: int = _MAX_PREVIEW_CHARS) -> str:
        preview = "prepared immutable context in attachments (one text item is also context): " + ", ".join(
            f"{entry.filename!r} ({entry.content_type or 'application/octet-stream'}, {entry.byte_size} bytes)"
            for entry in self.entries
        )
        return preview[: max(1, min(max_chars, _MAX_PREVIEW_CHARS))]


def build_rlm_input_kwargs(
    *,
    request: str,
    session_context: SessionContextManifest,
    skill_cards: tuple[Any, ...] | list[Any] = (),
    attachments: tuple[Any, ...] | list[Any] = (),
    attachment_context: AttachmentContextCapsule | None = None,
    workspace: WorkspaceCapabilityMetadata = UNAVAILABLE_WORKSPACE_CAPABILITY,
    workspace_memory_digest: str = "",
) -> dict[str, Any]:
    """Kwargs for ``rlm.aforward`` / ``forward`` matching FleetRLMSignature."""
    if not isinstance(request, str) or not request.strip() or len(request) > _MAX_REQUEST_CHARS:
        raise RLMConfigError("Turn input metadata is invalid")
    if (
        not isinstance(workspace_memory_digest, str)
        or len(workspace_memory_digest.encode("utf-8")) > WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
    ):
        raise RLMConfigError("Turn input metadata is invalid")
    try:
        workspace_memory = WorkspaceMemoryInput(tail=workspace_memory_digest) if workspace_memory_digest else None
        context = SessionContextInput(
            session_id=session_context.session_id,
            checkpoint_version=session_context.checkpoint_version,
            message_count=session_context.message_count,
            recent=tuple(
                TurnPreviewInput(
                    ordinal=item.ordinal,
                    role=item.role,
                    preview=item.preview,
                )
                for item in session_context.recent
            ),
            workspace=WorkspaceCapabilityInput(
                available=workspace.available,
                root=cast(Literal["."], workspace.root),
                instructions=workspace.instructions,
            ),
            workspace_memory=workspace_memory,
        )
        cards = tuple(
            SkillCardInput(
                id=card.id,
                name=card.name,
                description=card.description,
                scope="system",
                version=card.version,
                trust="system",
                affordances=(),
                resources_available=card.resources_available,
            )
            for card in skill_cards
        )
        attachment_inputs = tuple(
            AttachmentInput(
                id=ref.attachment_id,
                filename=ref.filename,
                content_type=ref.content_type,
                byte_size=ref.byte_size,
                checksum_sha256=ref.checksum_sha256,
            )
            for ref in attachments
        )
    except ValidationError as exc:
        raise RLMConfigError("Turn input metadata is invalid") from exc
    attachment_value: object = [item.model_dump(mode="json", exclude_none=True) for item in attachment_inputs]
    if attachment_context is not None:
        attachment_value = attachment_context
    context_payload = context.model_dump(mode="json")
    if workspace_memory is None:
        context_payload.pop("workspace_memory", None)
    return {
        "request": request,
        "session_context": context_payload,
        "skill_cards": [item.model_dump(mode="json") for item in cards],
        "attachments": attachment_value,
    }


__all__ = ["AttachmentContextCapsule", "AttachmentContextEntry", "build_rlm_input_kwargs"]
