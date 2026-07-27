"""Deep asynchronous Attachment lifecycle."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from fleet_rlm.files.errors import (
    AttachmentError,
    AttachmentIntegrityError,
    AttachmentStorageError,
    AttachmentValidationError,
)
from fleet_rlm.files.models import (
    AttachmentAccess,
    AttachmentRef,
    AttachmentRun,
    AttachmentUpload,
    PreparedAttachments,
    RunAttachmentSink,
    StagedAttachment,
)
from fleet_rlm.files.safety import sanitize_filename, validate_upload_size


@dataclass(frozen=True, slots=True)
class StoredAttachment:
    """Private catalog value; storage reference never leaves this module."""

    ref: AttachmentRef
    storage_ref: str


class AttachmentCatalog(Protocol):
    async def create(
        self,
        *,
        access: AttachmentAccess,
        ref: AttachmentRef,
        storage_ref: str,
    ) -> None: ...

    async def get_many(
        self,
        *,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
    ) -> tuple[StoredAttachment, ...]: ...


class AttachmentBlobGateway(Protocol):
    async def write(self, workspace_id: UUID, logical_path: str, data: bytes) -> None: ...

    async def read(self, workspace_id: UUID, logical_path: str) -> bytes: ...

    async def remove(self, workspace_id: UUID, logical_path: str) -> None: ...


class AttachmentPathPolicy(Protocol):
    def attachment_blob(self, attachment_id: UUID) -> str: ...

    def run_attachment(
        self,
        run: AttachmentRun,
        attachment_id: UUID,
        filename: str,
    ) -> str: ...


class AttachmentLifecycle(Protocol):
    async def upload(self, access: AttachmentAccess, upload: AttachmentUpload) -> AttachmentRef: ...

    async def metadata(
        self,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
    ) -> tuple[AttachmentRef, ...]: ...

    async def prepare_run(
        self,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
        run: AttachmentRun,
        sink: RunAttachmentSink,
    ) -> PreparedAttachments: ...


class AttachmentLifecycleService:
    """Own upload, authorization, integrity, and Run staging policy."""

    def __init__(
        self,
        *,
        catalog: AttachmentCatalog,
        blobs: AttachmentBlobGateway,
        paths: AttachmentPathPolicy,
        max_bytes: int,
        chunk_bytes: int = 64 * 1024,
    ) -> None:
        if max_bytes <= 0 or chunk_bytes <= 0:
            raise ValueError("Attachment byte limits must be positive")
        self._catalog = catalog
        self._blobs = blobs
        self._paths = paths
        self._max_bytes = max_bytes
        self._chunk_bytes = min(chunk_bytes, max_bytes + 1)

    async def upload(self, access: AttachmentAccess, upload: AttachmentUpload) -> AttachmentRef:
        filename = sanitize_filename(upload.filename)
        data = bytearray()
        try:
            while True:
                chunk = await upload.source.read(self._chunk_bytes)
                if not isinstance(chunk, bytes):
                    raise AttachmentValidationError("invalid upload source")
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > self._max_bytes:
                    raise AttachmentValidationError("Attachment exceeds the configured size limit")
        except AttachmentError:
            raise
        except Exception as exc:
            raise AttachmentStorageError("Attachment storage is unavailable") from exc
        validate_upload_size(len(data), max_bytes=self._max_bytes)
        attachment_id = uuid4()
        ref = AttachmentRef(
            id=attachment_id,
            filename=filename,
            content_type=upload.content_type,
            byte_size=len(data),
            checksum_sha256=hashlib.sha256(data).hexdigest(),
        )
        storage_ref = self._paths.attachment_blob(attachment_id)
        try:
            await self._blobs.write(access.workspace_id, storage_ref, bytes(data))
            await self._catalog.create(access=access, ref=ref, storage_ref=storage_ref)
        except AttachmentError:
            await self._rollback_blob(access.workspace_id, storage_ref)
            raise
        except Exception as exc:
            await self._rollback_blob(access.workspace_id, storage_ref)
            raise AttachmentStorageError("Attachment storage is unavailable") from exc
        return ref

    async def _rollback_blob(self, workspace_id: UUID, storage_ref: str) -> None:
        with suppress(Exception):
            await self._blobs.remove(workspace_id, storage_ref)

    async def metadata(
        self,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
    ) -> tuple[AttachmentRef, ...]:
        stored = await self._stored(access, attachment_ids)
        return tuple(item.ref for item in stored)

    async def _stored(
        self,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
    ) -> tuple[StoredAttachment, ...]:
        ids = tuple(attachment_ids)
        if len(set(ids)) != len(ids):
            raise AttachmentValidationError("Attachment selection contains duplicates")
        if not ids:
            return ()
        try:
            stored = await self._catalog.get_many(access=access, attachment_ids=ids)
        except AttachmentError:
            raise
        except Exception as exc:
            raise AttachmentStorageError("Attachment storage is unavailable") from exc
        by_id = {item.ref.id: item for item in stored}
        if len(by_id) != len(ids) or set(by_id) != set(ids):
            raise AttachmentStorageError("Attachment storage is unavailable")
        return tuple(by_id[attachment_id] for attachment_id in ids)

    async def prepare_run(
        self,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
        run: AttachmentRun,
        sink: RunAttachmentSink,
    ) -> PreparedAttachments:
        stored = await self._stored(access, attachment_ids)
        if not stored:
            return PreparedAttachments(refs=(), staged=())

        validated: list[tuple[StoredAttachment, bytes]] = []
        for item in stored:
            try:
                data = await self._blobs.read(access.workspace_id, item.storage_ref)
            except AttachmentError:
                raise
            except (FileNotFoundError, KeyError) as exc:
                raise AttachmentIntegrityError("Attachment content is unavailable") from exc
            except Exception as exc:
                raise AttachmentStorageError("Attachment storage is unavailable") from exc
            if len(data) != item.ref.byte_size or hashlib.sha256(data).hexdigest() != item.ref.checksum_sha256:
                raise AttachmentIntegrityError("Attachment content failed integrity verification")
            validated.append((item, data))

        staged: list[StagedAttachment] = []
        written: list[str] = []
        try:
            for item, data in validated:
                logical_path = self._paths.run_attachment(
                    run,
                    item.ref.id,
                    item.ref.filename,
                )
                await sink.write_private(logical_path, data)
                written.append(logical_path)
                staged.append(StagedAttachment(attachment_id=item.ref.id, sandbox_path=logical_path))
        except AttachmentError:
            await self._rollback_staged(sink, written)
            raise
        except Exception as exc:
            await self._rollback_staged(sink, written)
            raise AttachmentStorageError("Attachment storage is unavailable") from exc

        return PreparedAttachments(
            refs=tuple(item.ref for item in stored),
            staged=tuple(staged),
        )

    @staticmethod
    async def _rollback_staged(sink: RunAttachmentSink, written: Sequence[str]) -> None:
        for logical_path in reversed(tuple(written)):
            try:
                await sink.remove_private(logical_path)
            except Exception:
                continue
