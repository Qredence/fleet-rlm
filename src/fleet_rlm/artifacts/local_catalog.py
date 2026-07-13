"""Atomic local blob store for durable artifacts (catalog + Volume promote)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

from fleet_rlm.artifacts.errors import ArtifactNotFoundError, ArtifactValidationError
from fleet_rlm.artifacts.models import KIND_EXTENSIONS, ArtifactAccess, ArtifactKind, ArtifactRef
from fleet_rlm.artifacts.reader import StoredArtifact
from fleet_rlm.artifacts.safety import (
    encode_content,
    media_type_for,
    parse_kind,
    sanitize_title,
    validate_content_size,
)
from fleet_rlm.daytona.paths import VolumePaths, as_posix
from fleet_rlm.daytona.volume_fs import VolumeBlobFs


class LocalArtifactCatalog:
    """Store artifact catalog under a host root; promote bytes into Volume scope.

    Host ``root`` is a hermetic offline catalog. When ``volume_fs`` is set, durable
    blob+meta are written under Workspace Volume Scope (``artifacts/{id}/``) and
    the run-scoped logical sandbox path.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        max_bytes: int,
        volume_paths: VolumePaths | None = None,
        volume_fs: VolumeBlobFs | None = None,
    ) -> None:
        self.root = Path(root)
        self.max_bytes = max_bytes
        self._paths = volume_paths or VolumePaths.from_mount()
        self._volume_fs = volume_fs
        self._write_lock = Lock()
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
        run-scoped (sessions/{sid}/runs/{rid}/artifacts/{id}.ext) with a durable
        workspace copy under artifacts/{id}/ when volume_fs is configured.
        """
        with self._write_lock:
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
        durable_blob = as_posix(self._paths.artifact_blob_path(artifact_id))
        durable_meta = as_posix(self._paths.artifact_meta_path(artifact_id))

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
            "volume_blob_path": durable_blob,
        }
        try:
            meta.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        except Exception:
            try:
                blob.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        if self._volume_fs is not None:
            self._volume_fs.write_bytes(sandbox_path, data)
            self._volume_fs.write_bytes(durable_blob, data)
            self._volume_fs.write_bytes(
                durable_meta,
                (json.dumps(record, indent=2) + "\n").encode("utf-8"),
            )

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
        record = self._load_record(artifact_id)
        self._authorize(record, user_id=user_id, workspace_id=workspace_id)
        volume_blob = record.get("volume_blob_path")
        if self._volume_fs is not None and isinstance(volume_blob, str) and self._volume_fs.exists(volume_blob):
            return self._volume_fs.read_bytes(volume_blob)
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

    def durable_volume_blob_path(
        self,
        artifact_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> str:
        """Internal: Fleet durable Artifact path under Workspace Volume Scope."""
        record = self._load_record(artifact_id)
        self._authorize(record, user_id=user_id, workspace_id=workspace_id)
        path = record.get("volume_blob_path")
        if isinstance(path, str) and path:
            return path
        return as_posix(self._paths.artifact_blob_path(artifact_id))


class LocalArtifactReaderCatalog:
    """Async ArtifactReader catalog adapter over the hermetic local catalog."""

    def __init__(self, store: LocalArtifactCatalog) -> None:
        self._store = store

    async def get(self, *, access: ArtifactAccess, artifact_id: UUID) -> StoredArtifact:
        ref = self._store.get(
            artifact_id,
            user_id=access.user_id,
            workspace_id=access.workspace_id,
        )
        storage_ref = f"{access.user_id}:{access.workspace_id}:{artifact_id}"
        return StoredArtifact(ref=ref, storage_ref=storage_ref)


class LocalArtifactBlobGateway:
    """Resolve opaque local Artifact references without exposing host paths."""

    def __init__(self, store: LocalArtifactCatalog) -> None:
        self._store = store

    async def read(self, workspace_id: UUID, logical_path: str) -> bytes:
        _user, encoded_workspace, encoded_id = logical_path.split(":", 2)
        if UUID(encoded_workspace) != workspace_id:
            raise ArtifactNotFoundError("artifact not found")
        return self._store.read_bytes(
            UUID(encoded_id),
            user_id=UUID(_user),
            workspace_id=workspace_id,
        )
