"""Unified Attachment domain: validation, storage, lifecycle, and DSPy tool host."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

import dspy

from fleet_rlm.json_types import JsonValue
from fleet_rlm.tool_events import ToolEventView, bound_event_text
from fleet_rlm.workspace.paths import VolumePaths, as_posix
from fleet_rlm.workspace.storage import VolumeBlobFs

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AttachmentError(RuntimeError):
    """Base attachment error."""


class AttachmentNotFoundError(AttachmentError):
    """Missing or unauthorized attachment (do not distinguish for clients)."""


class AttachmentValidationError(AttachmentError):
    """Rejected filename, size, or content."""


class AttachmentIntegrityError(AttachmentError):
    """Authorized durable bytes are absent or contradict their metadata."""


class AttachmentStorageError(AttachmentError):
    """A required catalog, durable blob, or Run sink is unavailable."""


# ---------------------------------------------------------------------------
# Safety & Validation
# ---------------------------------------------------------------------------

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._ -]{1,255}$")
DEFAULT_MAX_BYTES = 10 * 1024 * 1024


def sanitize_filename(filename: str) -> str:
    raw = (filename or "").strip()
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        raise AttachmentValidationError("invalid filename")
    name = PurePosixPath(raw).name
    if not name or name in {".", ".."} or not _SAFE_NAME.match(name):
        raise AttachmentValidationError("invalid filename")
    if name.startswith("."):
        raise AttachmentValidationError("hidden filenames are not allowed")
    return name


def validate_upload_size(size: int, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    if size < 0:
        raise AttachmentValidationError("negative size")
    if size > max_bytes:
        raise AttachmentValidationError(f"file exceeds max size of {max_bytes} bytes")
    if size == 0:
        raise AttachmentValidationError("empty file")


# ---------------------------------------------------------------------------
# Models & Protocols
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttachmentAccess:
    user_id: UUID
    workspace_id: UUID


class AsyncByteSource(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True, slots=True)
class AttachmentUpload:
    filename: str
    content_type: str | None
    source: AsyncByteSource


@dataclass(frozen=True, slots=True)
class AttachmentRun:
    session_id: UUID
    run_id: UUID


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """Safe metadata returned to API clients."""

    id: UUID
    filename: str
    content_type: str | None
    byte_size: int
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class StagedAttachment:
    """Logical Sandbox/Volume path for RLM tools (Fleet-controlled only)."""

    attachment_id: UUID
    sandbox_path: str


@dataclass(frozen=True, slots=True)
class PreparedAttachment:
    """Authorized, integrity-checked Attachment metadata ready for one Run."""

    attachment_id: UUID
    filename: str
    content_type: str | None
    byte_size: int
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedAttachments:
    refs: tuple[AttachmentRef, ...]
    staged: tuple[StagedAttachment, ...]


class RunAttachmentSink(Protocol):
    async def write_private(self, logical_path: str, data: bytes) -> None: ...

    async def remove_private(self, logical_path: str) -> None: ...


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
    async def write_bytes(self, workspace_id: UUID, logical_path: str, data: bytes) -> None: ...

    async def read_bytes(self, workspace_id: UUID, logical_path: str) -> bytes: ...

    async def remove_bytes(self, workspace_id: UUID, logical_path: str) -> None: ...


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


# ---------------------------------------------------------------------------
# Path Policies
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LocalAttachmentPathPolicy:
    """Opaque host-relative references for local Attachment catalogs."""

    root: Path

    def attachment_blob(self, attachment_id: UUID) -> str:
        return f"{attachment_id}.bin"

    def run_attachment(self, run: AttachmentRun, attachment_id: UUID, filename: str) -> str:
        del run, filename
        return f"{attachment_id}.bin"


@dataclass(frozen=True, slots=True)
class WorkspaceAttachmentPathPolicy:
    """Logical paths under the validated Workspace Volume layout."""

    paths: VolumePaths

    def attachment_blob(self, attachment_id: UUID) -> str:
        return as_posix(self.paths.attachment_blob_path(attachment_id))

    def run_attachment(self, run: AttachmentRun, attachment_id: UUID, filename: str) -> str:
        return as_posix(
            self.paths.run_attachment_file(
                run.session_id,
                run.run_id,
                attachment_id,
                filename,
            )
        )


# ---------------------------------------------------------------------------
# Local Storage / Catalog Adapters
# ---------------------------------------------------------------------------


class LocalAttachmentBlobGateway:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, storage_ref: str) -> Path:
        path = (self.root / storage_ref).resolve()
        if path.parent != self.root.resolve() or path.suffix != ".bin":
            raise ValueError("invalid Attachment storage reference")
        return path

    async def write_bytes(self, workspace_id: UUID, logical_path: str, data: bytes) -> None:
        del workspace_id
        self._path(logical_path).write_bytes(data)

    async def read_bytes(self, workspace_id: UUID, logical_path: str) -> bytes:
        del workspace_id
        try:
            return self._path(logical_path).read_bytes()
        except FileNotFoundError as exc:
            raise AttachmentNotFoundError("attachment not found") from exc

    async def remove_bytes(self, workspace_id: UUID, logical_path: str) -> None:
        del workspace_id
        self._path(logical_path).unlink(missing_ok=True)


class LocalAttachmentCatalog:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, attachment_id: UUID) -> Path:
        return self.root / f"{attachment_id}.json"

    async def create(self, *, access: AttachmentAccess, ref: AttachmentRef, storage_ref: str) -> None:
        self._path(ref.id).write_text(
            json.dumps(
                {
                    "id": str(ref.id),
                    "user_id": str(access.user_id),
                    "workspace_id": str(access.workspace_id),
                    "filename": ref.filename,
                    "content_type": ref.content_type,
                    "byte_size": ref.byte_size,
                    "checksum_sha256": ref.checksum_sha256,
                    "storage_ref": storage_ref,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    async def get_many(
        self,
        *,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
    ) -> tuple[StoredAttachment, ...]:
        values: list[StoredAttachment] = []
        for attachment_id in attachment_ids:
            try:
                record = json.loads(self._path(attachment_id).read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise AttachmentNotFoundError("attachment not found") from exc
            if record["user_id"] != str(access.user_id) or record["workspace_id"] != str(access.workspace_id):
                raise AttachmentNotFoundError("attachment not found")
            values.append(
                StoredAttachment(
                    ref=AttachmentRef(
                        id=attachment_id,
                        filename=record["filename"],
                        content_type=record.get("content_type"),
                        byte_size=int(record["byte_size"]),
                        checksum_sha256=record["checksum_sha256"],
                    ),
                    storage_ref=record["storage_ref"],
                )
            )
        return tuple(values)


# ---------------------------------------------------------------------------
# Lifecycle Service
# ---------------------------------------------------------------------------


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
                    raise AttachmentValidationError(f"file exceeds max size of {self._max_bytes} bytes")
        except AttachmentValidationError:
            raise
        except Exception as exc:
            raise AttachmentStorageError("failed to read upload payload") from exc

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
        written = False
        try:
            await self._blobs.write_bytes(access.workspace_id, storage_ref, bytes(data))
            written = True
            await self._catalog.create(access=access, ref=ref, storage_ref=storage_ref)
            return ref
        except (AttachmentError, ValueError):
            if written:
                with suppress(Exception):
                    await self._blobs.remove_bytes(access.workspace_id, storage_ref)
            raise
        except Exception as exc:
            if written:
                with suppress(Exception):
                    await self._blobs.remove_bytes(access.workspace_id, storage_ref)
            raise AttachmentStorageError("failed to persist attachment") from exc

    async def metadata(
        self,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
    ) -> tuple[AttachmentRef, ...]:
        if len(set(attachment_ids)) != len(attachment_ids):
            raise AttachmentValidationError("duplicate attachment requested")
        if not attachment_ids:
            return ()
        stored = await self._catalog.get_many(access=access, attachment_ids=attachment_ids)
        if len(stored) != len(attachment_ids):
            raise AttachmentNotFoundError("one or more attachments were not found")
        by_id = {item.ref.id: item.ref for item in stored}
        return tuple(by_id[aid] for aid in attachment_ids)

    async def prepare_run(
        self,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
        run: AttachmentRun,
        sink: RunAttachmentSink,
    ) -> PreparedAttachments:
        if len(set(attachment_ids)) != len(attachment_ids):
            raise AttachmentValidationError("duplicate attachment requested")
        if not attachment_ids:
            return PreparedAttachments((), ())
        try:
            stored = await self._catalog.get_many(access=access, attachment_ids=attachment_ids)
        except AttachmentError:
            raise
        except Exception as exc:
            raise AttachmentStorageError("failed to load attachment catalog") from exc

        if len(stored) != len(attachment_ids):
            raise AttachmentNotFoundError("one or more attachments were not found")

        by_id = {item.ref.id: item for item in stored}
        blobs: list[tuple[UUID, str, bytes]] = []
        for aid in attachment_ids:
            item = by_id[aid]
            logical_path = self._paths.run_attachment(run, item.ref.id, item.ref.filename)
            try:
                data = await self._blobs.read_bytes(access.workspace_id, item.storage_ref)
            except AttachmentError:
                raise
            except Exception as exc:
                raise AttachmentStorageError("failed to read attachment blob") from exc

            digest = hashlib.sha256(data).hexdigest()
            if len(data) != item.ref.byte_size or not hmac.compare_digest(digest, item.ref.checksum_sha256):
                raise AttachmentIntegrityError(f"attachment integrity check failed: {item.ref.id}")

            blobs.append((aid, logical_path, data))

        staged: list[StagedAttachment] = []
        try:
            for aid, logical_path, data in blobs:
                await sink.write_private(logical_path, data)
                staged.append(StagedAttachment(aid, logical_path))
        except Exception as exc:
            for item in staged:
                with suppress(Exception):
                    await sink.remove_private(item.sandbox_path)
            raise AttachmentStorageError("attachment sink is unavailable") from exc

        return PreparedAttachments(tuple(by_id[aid].ref for aid in attachment_ids), tuple(staged))


# ---------------------------------------------------------------------------
# DSPy Tool Host
# ---------------------------------------------------------------------------

_EVENT_TEXT_MAX_CHARS = 256


def _project_fields(result: object, fields: tuple[str, ...]) -> JsonValue:
    if not isinstance(result, Mapping):
        return {}
    values = cast(Mapping[str, JsonValue], result)
    return {
        key: bound_event_text(values[key], max_chars=_EVENT_TEXT_MAX_CHARS)
        if isinstance(values[key], str)
        else values[key]
        for key in fields
        if key in values
    }


class AttachmentToolHost:
    """Bound Attachment reads for one Run; reauthorizes every call."""

    def __init__(
        self,
        *,
        attachments: tuple[AttachmentRef, ...],
        staged_attachments: tuple[StagedAttachment, ...],
        volume_fs: VolumeBlobFs,
    ) -> None:
        self._attachments = {ref.id: ref for ref in attachments}
        self._staged = {item.attachment_id: item for item in staged_attachments}
        self._volume_fs = volume_fs
        self._pending_events: list[dict[str, Any]] = []

    def drain_public_events(self) -> list[dict[str, Any]]:
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    def record_attachment_accesses(self, attachment_ids: tuple[str, ...]) -> None:
        seen: set[UUID] = set()
        for value in attachment_ids:
            try:
                attachment_id = UUID(str(value))
            except (TypeError, ValueError, AttributeError):
                continue
            if attachment_id in seen:
                continue
            ref = self._attachments.get(attachment_id)
            if ref is None or attachment_id not in self._staged:
                continue
            seen.add(attachment_id)
            self._pending_events.append(
                {
                    "event_kind": "attachment.read",
                    "attachment_id": str(ref.id),
                    "filename": ref.filename,
                    "byte_size": ref.byte_size,
                }
            )

    async def aclose(self) -> None:
        return None

    def read_attachment(self, attachment_id: str) -> dict[str, Any]:
        try:
            aid = UUID(str(attachment_id).strip())
        except (ValueError, AttributeError, TypeError):
            return {"ok": False, "error": "invalid_id"}
        ref = self._attachments.get(aid)
        staged = self._staged.get(aid)
        if ref is None or staged is None:
            return {"ok": False, "error": "not_found"}
        try:
            data = self._volume_fs.read_bytes(staged.sandbox_path, use_cache=False)
            if (
                not isinstance(ref.checksum_sha256, str)
                or len(data) != ref.byte_size
                or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), ref.checksum_sha256.lower())
            ):
                return {"ok": False, "error": "not_found"}
        except Exception:
            return {"ok": False, "error": "not_found"}

        self._pending_events.append(
            {
                "event_kind": "attachment.read",
                "attachment_id": str(ref.id),
                "filename": ref.filename,
                "byte_size": ref.byte_size,
            }
        )

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

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        def read_attachment(attachment_id: str) -> dict[str, Any]:
            return self.read_attachment(attachment_id)

        return (
            dspy.Tool(
                read_attachment,
                name="read_attachment",
                desc=(
                    "Read one immutable authorized Attachment by opaque identity only when its advertised "
                    "metadata is relevant to the current request."
                ),
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        def read_input(arguments: Mapping[str, Any]) -> JsonValue:
            return {"attachment_id": bound_event_text(arguments.get("attachment_id"))}

        return MappingProxyType(
            {
                "read_attachment": ToolEventView(
                    input_projection=read_input,
                    output_projection=lambda result: _project_fields(
                        result,
                        ("ok", "error", "attachment_id", "filename", "content_type", "encoding"),
                    ),
                )
            }
        )


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
