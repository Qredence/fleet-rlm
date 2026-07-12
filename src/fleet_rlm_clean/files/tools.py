"""Host-mediated attachment and artifact tools for dspy.RLM."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from fleet_rlm_clean.artifacts.models import KIND_EXTENSIONS, ArtifactCandidate
from fleet_rlm_clean.artifacts.safety import (
    encode_content,
    media_type_for,
    parse_kind,
    sanitize_title,
    validate_content_size,
)
from fleet_rlm_clean.daytona.paths import VolumePaths, as_posix
from fleet_rlm_clean.daytona.volume_fs import VolumeBlobFs
from fleet_rlm_clean.files.models import AttachmentRef, StagedAttachment


class FileToolHost:
    """Bound file tools for one turn: reauthorize every call; track safe public events."""

    def __init__(
        self,
        *,
        attachments: tuple[AttachmentRef, ...],
        staged_attachments: tuple[StagedAttachment, ...],
        volume_fs: VolumeBlobFs,
        user_id: UUID,
        workspace_id: UUID,
        session_id: UUID,
        run_id: UUID,
        max_attachment_reads: int = 16,
        max_artifact_creates: int = 8,
        max_artifact_bytes: int = 10 * 1024 * 1024,
        volume_paths: VolumePaths | None = None,
    ) -> None:
        self._attachments = {ref.id: ref for ref in attachments}
        self._staged = {item.attachment_id: item for item in staged_attachments}
        self._volume_fs = volume_fs
        self._user_id = user_id
        self._workspace_id = workspace_id
        self._session_id = session_id
        self._run_id = run_id
        self._max_attachment_reads = max(0, int(max_attachment_reads))
        self._max_artifact_creates = max(0, int(max_artifact_creates))
        self._max_artifact_bytes = max(1, int(max_artifact_bytes))
        self._paths = volume_paths or VolumePaths.from_mount()
        self._read_count = 0
        self._create_count = 0
        self._pending_events: list[dict[str, Any]] = []
        self._artifact_candidates: list[ArtifactCandidate] = []

    def drain_public_events(self) -> list[dict[str, Any]]:
        """Return and clear ledger entries with ``event_kind`` + safe payload fields."""
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    def drain_artifact_candidates(self) -> tuple[ArtifactCandidate, ...]:
        candidates = tuple(self._artifact_candidates)
        self._artifact_candidates.clear()
        return candidates

    def read_attachment(self, attachment_id: str) -> dict[str, Any]:
        """Return attachment body after reauth. Public event omits content."""
        if self._read_count >= self._max_attachment_reads:
            return {"ok": False, "error": "budget_exceeded"}
        try:
            aid = UUID(str(attachment_id).strip())
        except (ValueError, AttributeError, TypeError):
            return {"ok": False, "error": "invalid_id"}
        ref = self._attachments.get(aid)
        staged = self._staged.get(aid)
        if ref is None or staged is None:
            return {"ok": False, "error": "not_found"}
        try:
            data = self._volume_fs.read_bytes(staged.sandbox_path)
        except Exception:  # noqa: BLE001 - provider details never reach the model
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
        """Stage a private Artifact Candidate. Turn Commit owns publication."""
        if self._create_count >= self._max_artifact_creates:
            return {"ok": False, "error": "budget_exceeded"}
        try:
            parsed_kind = parse_kind(kind)
            safe_title = sanitize_title(title)
            data = encode_content(parsed_kind, content)
            validate_content_size(len(data), max_bytes=self._max_artifact_bytes)
            artifact_id = uuid4()
            extension = KIND_EXTENSIONS[parsed_kind]
            staging_path = as_posix(
                self._paths.run_artifacts_dir(self._session_id, self._run_id) / f"{artifact_id}{extension}"
            )
            durable_path = as_posix(self._paths.artifact_blob_path(artifact_id))
            self._volume_fs.write_bytes(staging_path, data)
            candidate = ArtifactCandidate(
                id=artifact_id,
                user_id=self._user_id,
                workspace_id=self._workspace_id,
                session_id=self._session_id,
                run_id=self._run_id,
                kind=parsed_kind,
                title=safe_title,
                media_type=media_type_for(parsed_kind),
                byte_size=len(data),
                checksum_sha256=hashlib.sha256(data).hexdigest(),
                staging_path=staging_path,
                durable_path=durable_path,
            )
        except ValueError as exc:
            return {"ok": False, "error": "validation", "message": str(exc)[:200]}
        except Exception:  # noqa: BLE001 - never leak internals to model
            return {"ok": False, "error": "validation"}

        self._create_count += 1
        self._artifact_candidates.append(candidate)
        return {
            "ok": True,
            "artifact_candidate_id": str(candidate.id),
            "kind": candidate.kind,
            "title": candidate.title,
            "byte_size": candidate.byte_size,
            "checksum_sha256": candidate.checksum_sha256,
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
            """Stage a text/markdown/json Artifact Candidate for Turn Commit."""
            return self.create_artifact(kind, content, title=title)

        return (read_attachment, create_artifact)
