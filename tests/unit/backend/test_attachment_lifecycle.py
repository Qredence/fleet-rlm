"""Attachment lifecycle public seam."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest


class _Source:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._chunks.pop(0) if self._chunks else b""


@dataclass
class _Catalog:
    calls: list[tuple[str, object]]
    stored: dict[UUID, object] = field(default_factory=dict)

    async def create(self, *, access: object, ref: object, storage_ref: str) -> None:
        self.calls.append(("catalog", (access, ref, storage_ref)))

    async def get_many(self, *, access: object, attachment_ids: object) -> tuple[object, ...]:
        from fleet_rlm.files.errors import AttachmentNotFoundError

        ids = tuple(attachment_ids)  # type: ignore[arg-type]
        self.calls.append(("metadata", (access, ids)))
        try:
            return tuple(self.stored[attachment_id] for attachment_id in reversed(ids))
        except KeyError as exc:
            raise AttachmentNotFoundError("Attachment not found") from exc


@dataclass
class _Blobs:
    calls: list[tuple[str, object]]
    stored: dict[str, bytes] = field(default_factory=dict)

    async def write(self, workspace_id: UUID, logical_path: str, data: bytes) -> None:
        self.calls.append(("blob", (workspace_id, logical_path, data)))
        self.stored[logical_path] = data

    async def read(self, workspace_id: UUID, logical_path: str) -> bytes:
        self.calls.append(("read", (workspace_id, logical_path)))
        return self.stored[logical_path]

    async def remove(self, workspace_id: UUID, logical_path: str) -> None:
        self.calls.append(("remove-blob", (workspace_id, logical_path)))
        self.stored.pop(logical_path, None)


class _Paths:
    def attachment_blob(self, attachment_id: UUID) -> str:
        return f"private/attachments/{attachment_id}.bin"

    def run_attachment(self, run: object, attachment_id: UUID, filename: str) -> str:
        return f"runs/{run.session_id}/{run.run_id}/attachments/{attachment_id}/{filename}"  # type: ignore[attr-defined]


@dataclass
class _Sink:
    calls: list[tuple[str, object]]
    stored: dict[str, bytes] = field(default_factory=dict)
    fail_after: int | None = None

    async def write_private(self, logical_path: str, data: bytes) -> None:
        completed = sum(name == "stage" for name, _ in self.calls)
        if self.fail_after is not None and completed >= self.fail_after:
            raise RuntimeError("provider detail must not escape")
        self.calls.append(("stage", (logical_path, data)))
        self.stored[logical_path] = data

    async def remove_private(self, logical_path: str) -> None:
        self.calls.append(("remove", logical_path))
        self.stored.pop(logical_path, None)


@pytest.mark.asyncio
async def test_upload_streams_bounded_bytes_before_creating_metadata() -> None:
    from fleet_rlm.files.lifecycle import AttachmentLifecycleService
    from fleet_rlm.files.models import AttachmentAccess, AttachmentUpload

    calls: list[tuple[str, object]] = []
    source = _Source([b"abc", b"def", b""])
    access = AttachmentAccess(user_id=uuid4(), workspace_id=uuid4())
    module = AttachmentLifecycleService(
        catalog=_Catalog(calls),
        blobs=_Blobs(calls),
        paths=_Paths(),
        max_bytes=8,
        chunk_bytes=3,
    )

    ref = await module.upload(
        access,
        AttachmentUpload(filename=" report.txt ", content_type="text/plain", source=source),
    )

    assert ref.filename == "report.txt"
    assert ref.byte_size == 6
    assert ref.checksum_sha256 == "bef57ec7f53a6d40beb640a780a639c83bc29ac8a9816f1fc6c5c6dcd93c4721"
    assert source.read_sizes == [3, 3, 3]
    assert [name for name, _ in calls] == ["blob", "catalog"]
    assert "private" not in repr(ref)


@pytest.mark.asyncio
async def test_upload_rolls_back_blob_when_catalog_create_fails() -> None:
    from fleet_rlm.files.errors import AttachmentStorageError
    from fleet_rlm.files.lifecycle import AttachmentLifecycleService
    from fleet_rlm.files.models import AttachmentAccess, AttachmentUpload

    class FailingCatalog(_Catalog):
        async def create(self, *, access: object, ref: object, storage_ref: str) -> None:
            del access, ref, storage_ref
            raise RuntimeError("database unavailable")

    calls: list[tuple[str, object]] = []
    blobs = _Blobs(calls)
    access = AttachmentAccess(user_id=uuid4(), workspace_id=uuid4())
    module = AttachmentLifecycleService(catalog=FailingCatalog(calls), blobs=blobs, paths=_Paths(), max_bytes=8)

    with pytest.raises(AttachmentStorageError):
        await module.upload(access, AttachmentUpload("report.txt", "text/plain", _Source([b"abc", b""])))

    assert not blobs.stored
    assert [name for name, _ in calls] == ["blob", "remove-blob"]


@pytest.mark.asyncio
async def test_metadata_authorizes_one_batch_and_returns_request_order() -> None:
    from fleet_rlm.files.errors import AttachmentValidationError
    from fleet_rlm.files.lifecycle import AttachmentLifecycleService, StoredAttachment
    from fleet_rlm.files.models import AttachmentAccess, AttachmentRef

    first_id, second_id = uuid4(), uuid4()
    first = AttachmentRef(first_id, "first.txt", "text/plain", 1, "a" * 64)
    second = AttachmentRef(second_id, "second.txt", "text/plain", 2, "b" * 64)
    calls: list[tuple[str, object]] = []
    catalog = _Catalog(
        calls,
        {
            first_id: StoredAttachment(first, "private/first"),
            second_id: StoredAttachment(second, "private/second"),
        },
    )
    module = AttachmentLifecycleService(
        catalog=catalog,
        blobs=_Blobs(calls),
        paths=_Paths(),
        max_bytes=8,
    )
    access = AttachmentAccess(user_id=uuid4(), workspace_id=uuid4())

    assert await module.metadata(access, (first_id, second_id)) == (first, second)
    assert await module.metadata(access, ()) == ()
    with pytest.raises(AttachmentValidationError):
        await module.metadata(access, (first_id, first_id))

    assert [name for name, _ in calls] == ["metadata"]


@pytest.mark.asyncio
async def test_prepare_run_reauthorizes_verifies_and_stages_in_request_order() -> None:
    from fleet_rlm.files.lifecycle import AttachmentLifecycleService, StoredAttachment
    from fleet_rlm.files.models import AttachmentAccess, AttachmentRef, AttachmentRun

    first_id, second_id = uuid4(), uuid4()
    first_data, second_data = b"a", b"bc"
    first = AttachmentRef(
        first_id,
        "first.txt",
        "text/plain",
        len(first_data),
        "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
    )
    second = AttachmentRef(
        second_id,
        "second.txt",
        "text/plain",
        len(second_data),
        "1e0bbd6c686ba050b8eb03ffeedc64fdc9d80947fce821abbe5d6dc8d252c5ac",
    )
    calls: list[tuple[str, object]] = []
    catalog = _Catalog(
        calls,
        {
            first_id: StoredAttachment(first, "private/first"),
            second_id: StoredAttachment(second, "private/second"),
        },
    )
    blobs = _Blobs(calls, {"private/first": first_data, "private/second": second_data})
    sink = _Sink(calls)
    module = AttachmentLifecycleService(catalog=catalog, blobs=blobs, paths=_Paths(), max_bytes=8)
    access = AttachmentAccess(user_id=uuid4(), workspace_id=uuid4())
    run = AttachmentRun(session_id=uuid4(), run_id=uuid4())

    prepared = await module.prepare_run(access, (second_id, first_id), run, sink)

    assert prepared.refs == (second, first)
    assert [item.attachment_id for item in prepared.staged] == [second_id, first_id]
    assert [name for name, _ in calls] == ["metadata", "read", "read", "stage", "stage"]
    assert all(path.startswith(f"runs/{run.session_id}/{run.run_id}/") for path in sink.stored)


@pytest.mark.asyncio
async def test_prepare_run_rolls_back_staged_paths_when_a_later_write_fails() -> None:
    from fleet_rlm.files.errors import AttachmentStorageError
    from fleet_rlm.files.lifecycle import AttachmentLifecycleService, StoredAttachment
    from fleet_rlm.files.models import AttachmentAccess, AttachmentRef, AttachmentRun

    ids = (uuid4(), uuid4())
    refs = (
        AttachmentRef(
            ids[0], "a.txt", "text/plain", 1, "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
        ),
        AttachmentRef(
            ids[1], "b.txt", "text/plain", 1, "3e23e8160039594a33894f6564e1b1348bbd7a0088d42c4acb73eeaed59c009d"
        ),
    )
    calls: list[tuple[str, object]] = []
    catalog = _Catalog(
        calls,
        {
            ids[0]: StoredAttachment(refs[0], "private/a"),
            ids[1]: StoredAttachment(refs[1], "private/b"),
        },
    )
    blobs = _Blobs(calls, {"private/a": b"a", "private/b": b"b"})
    sink = _Sink(calls, fail_after=1)
    module = AttachmentLifecycleService(catalog=catalog, blobs=blobs, paths=_Paths(), max_bytes=8)

    with pytest.raises(AttachmentStorageError, match="unavailable"):
        await module.prepare_run(
            AttachmentAccess(user_id=uuid4(), workspace_id=uuid4()),
            ids,
            AttachmentRun(session_id=uuid4(), run_id=uuid4()),
            sink,
        )

    assert sink.stored == {}
    assert [name for name, _ in calls][-2:] == ["stage", "remove"]
