"""Host-mediated attachment and artifact tools for dspy.RLM."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any
from uuid import UUID

from fleet_rlm_clean.artifacts.errors import ArtifactValidationError
from fleet_rlm_clean.artifacts.store import LocalArtifactStore
from fleet_rlm_clean.files.errors import AttachmentNotFoundError
from fleet_rlm_clean.files.uploads import LocalAttachmentStore


class FileToolHost:
    """Bound file tools for one turn: reauthorize every call; track safe public events."""

    def __init__(
        self,
        *,
        attachment_store: LocalAttachmentStore,
        artifact_store: LocalArtifactStore,
        user_id: UUID,
        workspace_id: UUID,
        session_id: UUID,
        run_id: UUID,
        max_attachment_reads: int = 16,
        max_artifact_creates: int = 8,
    ) -> None:
        self._attachments = attachment_store
        self._artifacts = artifact_store
        self._user_id = user_id
        self._workspace_id = workspace_id
        self._session_id = session_id
        self._run_id = run_id
        self._max_attachment_reads = max(0, int(max_attachment_reads))
        self._max_artifact_creates = max(0, int(max_artifact_creates))
        self._read_count = 0
        self._create_count = 0
        self._pending_events: list[dict[str, Any]] = []

    def drain_public_events(self) -> list[dict[str, Any]]:
        """Return and clear ledger entries with ``event_kind`` + safe payload fields."""
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    def read_attachment(self, attachment_id: str) -> dict[str, Any]:
        """Return attachment body after reauth. Public event omits content."""
        if self._read_count >= self._max_attachment_reads:
            return {"ok": False, "error": "budget_exceeded"}
        try:
            aid = UUID(str(attachment_id).strip())
        except (ValueError, AttributeError, TypeError):
            return {"ok": False, "error": "invalid_id"}
        try:
            ref = self._attachments.get(
                aid, user_id=self._user_id, workspace_id=self._workspace_id
            )
            data = self._attachments.read_bytes(
                aid, user_id=self._user_id, workspace_id=self._workspace_id
            )
        except AttachmentNotFoundError:
            return {"ok": False, "error": "not_found"}

        self._read_count += 1
        self._pending_events.append(
            {
                "event_kind": "attachment.read",
                "attachment_id": str(ref.id),
                "filename": ref.filename,
                "byte_size": ref.byte_size,
            }
        )

        # Prefer UTF-8 text; binary as base64
        try:
            text = data.decode("utf-8")
            if "\x00" in text:
                raise UnicodeDecodeError("utf-8", data, 0, 1, "nul")
            return {
                "ok": True,
                "attachment_id": str(ref.id),
                "filename": ref.filename,
                "content_type": ref.content_type,
                "content": text,
                "encoding": "utf-8",
            }
        except UnicodeDecodeError:
            return {
                "ok": True,
                "attachment_id": str(ref.id),
                "filename": ref.filename,
                "content_type": ref.content_type,
                "content_base64": base64.b64encode(data).decode("ascii"),
                "encoding": "base64",
            }

    def create_artifact(
        self,
        kind: str,
        content: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Create durable artifact after identity-bound store write. No paths in result."""
        if self._create_count >= self._max_artifact_creates:
            return {"ok": False, "error": "budget_exceeded"}
        try:
            ref = self._artifacts.create(
                user_id=self._user_id,
                workspace_id=self._workspace_id,
                session_id=self._session_id,
                run_id=self._run_id,
                kind=kind,
                content=content,
                title=title,
            )
        except ArtifactValidationError as exc:
            return {"ok": False, "error": "validation", "message": str(exc)[:200]}
        except Exception:  # noqa: BLE001 - never leak internals to model
            return {"ok": False, "error": "validation"}

        self._create_count += 1
        self._pending_events.append(
            {
                "event_kind": "artifact.created",
                "artifact_id": str(ref.id),
                "kind": ref.kind,
                "title": ref.title,
                "byte_size": ref.byte_size,
                "checksum_sha256": ref.checksum_sha256,
            }
        )
        return {
            "ok": True,
            "artifact_id": str(ref.id),
            "kind": ref.kind,
            "title": ref.title,
            "byte_size": ref.byte_size,
            "checksum_sha256": ref.checksum_sha256,
        }

    def as_tool_callables(self) -> tuple[Callable[..., Any], ...]:
        """Named callables suitable for dspy.RLM tools=."""

        def read_attachment(attachment_id: str) -> dict[str, Any]:
            """Read an authorized attachment by opaque ID (host rechecks every call)."""
            return self.read_attachment(attachment_id)

        def create_artifact(
            kind: str,
            content: str,
            title: str | None = None,
        ) -> dict[str, Any]:
            """Create a durable text/markdown/json artifact for this turn."""
            return self.create_artifact(kind, content, title=title)

        return (read_attachment, create_artifact)
