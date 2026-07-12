"""Atomic local blob store for durable artifacts (offline / Volume host cache)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fleet_rlm_clean.artifacts.errors import ArtifactNotFoundError, ArtifactValidationError
from fleet_rlm_clean.artifacts.models import KIND_EXTENSIONS, ArtifactKind, ArtifactRef
from fleet_rlm_clean.artifacts.safety import (
    encode_content,
    media_type_for,
    parse_kind,
    sanitize_title,
    validate_content_size,
)
from fleet_rlm_clean.daytona.paths import VolumePaths, as_posix


class LocalArtifactStore:
    """Store artifact blobs + metadata under a host root (never exposed publicly)."""

    def __init__(
        self,
        root: Path | str,
        *,
        max_bytes: int,
        volume_paths: VolumePaths | None = None,
    ) -> None:
        self.root = Path(root)
        self.max_bytes = max_bytes
        self._paths = volume_paths or VolumePaths.from_mount()
        self.root.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, artifact_id: UUID) -> Path:
        return self.root / f"{artifact_id}.meta.json"

    def _blob_path(self, artifact_id: UUID) -> Path:
        return self.root / f"{artifact_id}.bin"

    def logical_sandbox_path(
        self,
        *,
        session_id: UUID,
        run_id: UUID,
        artifact_id: UUID,
        kind: ArtifactKind,
    ) -> str:
        """Fleet-controlled Sandbox/Volume path (logical only)."""
        ext = KIND_EXTENSIONS[kind]
        directory = self._paths.run_artifacts_dir(session_id, run_id)
        return as_posix(directory / f"{artifact_id}{ext}")

    def create(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        session_id: UUID,
        run_id: UUID,
        kind: str,
        content: str,
        title: str | None = None,
    ) -> ArtifactRef:
        """Create under a session write guard so concurrent creates stay deterministic.

        Content is stored under a unique artifact id; logical Volume path is
        run-scoped (sessions/{sid}/runs/{rid}/artifacts/{id}.ext).
        """
        # Thread-safe lock for concurrent host-side writers (sync API).
        from threading import Lock

        if not hasattr(self, "_write_lock"):
            self._write_lock = Lock()  # type: ignore[attr-defined]

        with self._write_lock:  # type: ignore[attr-defined]
            return self._create_unlocked(
                user_id=user_id,
                workspace_id=workspace_id,
                session_id=session_id,
                run_id=run_id,
                kind=kind,
                content=content,
                title=title,
            )

    def _create_unlocked(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        session_id: UUID,
        run_id: UUID,
        kind: str,
        content: str,
        title: str | None = None,
    ) -> ArtifactRef:
        parsed_kind = parse_kind(kind)
        safe_title = sanitize_title(title)
        data = encode_content(parsed_kind, content)
        validate_content_size(len(data), max_bytes=self.max_bytes)

        artifact_id = uuid4()
        checksum = hashlib.sha256(data).hexdigest()
        media_type = media_type_for(parsed_kind)
        sandbox_path = self.logical_sandbox_path(
            session_id=session_id,
            run_id=run_id,
            artifact_id=artifact_id,
            kind=parsed_kind,
        )

        blob = self._blob_path(artifact_id)
        meta = self._meta_path(artifact_id)
        fd, tmp_name = tempfile.mkstemp(dir=self.root, prefix=".art-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, blob)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

        record = {
            "id": str(artifact_id),
            "user_id": str(user_id),
            "workspace_id": str(workspace_id),
            "session_id": str(session_id),
            "run_id": str(run_id),
            "kind": parsed_kind,
            "title": safe_title,
            "media_type": media_type,
            "byte_size": len(data),
            "checksum_sha256": checksum,
            # Private fields — never return in API
            "storage_key": f"{artifact_id}.bin",
            "sandbox_path": sandbox_path,
        }
        try:
            meta.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        except Exception:
            try:
                blob.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        return ArtifactRef(
            id=artifact_id,
            session_id=session_id,
            run_id=run_id,
            kind=parsed_kind,
            title=safe_title,
            media_type=media_type,
            byte_size=len(data),
            checksum_sha256=checksum,
        )

    def _load_record(self, artifact_id: UUID) -> dict[str, Any]:
        path = self._meta_path(artifact_id)
        if not path.is_file():
            raise ArtifactNotFoundError("artifact not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def _authorize(self, record: dict[str, Any], *, user_id: UUID, workspace_id: UUID) -> None:
        if UUID(record["user_id"]) != user_id or UUID(record["workspace_id"]) != workspace_id:
            raise ArtifactNotFoundError("artifact not found")

    def get(
        self,
        artifact_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> ArtifactRef:
        record = self._load_record(artifact_id)
        self._authorize(record, user_id=user_id, workspace_id=workspace_id)
        return ArtifactRef(
            id=UUID(record["id"]),
            session_id=UUID(record["session_id"]),
            run_id=UUID(record["run_id"]),
            kind=record["kind"],  # type: ignore[arg-type]
            title=record.get("title"),
            media_type=str(record["media_type"]),
            byte_size=int(record["byte_size"]),
            checksum_sha256=str(record["checksum_sha256"]),
        )

    def read_bytes(
        self,
        artifact_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> bytes:
        self.get(artifact_id, user_id=user_id, workspace_id=workspace_id)
        blob = self._blob_path(artifact_id)
        if not blob.is_file():
            raise ArtifactNotFoundError("artifact not found")
        return blob.read_bytes()

    def sandbox_path_for(
        self,
        artifact_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> str:
        """Return Fleet logical sandbox path after reauth (internal / tools)."""
        record = self._load_record(artifact_id)
        self._authorize(record, user_id=user_id, workspace_id=workspace_id)
        path = record.get("sandbox_path")
        if not path or not isinstance(path, str):
            raise ArtifactValidationError("missing sandbox path metadata")
        return path
