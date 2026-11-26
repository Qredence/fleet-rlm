"""Tests for storage.py multipart abort fix, Daytona Volume helpers, and opt_datasets.py."""

from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from daytona.v2.errors import FileNotFoundError as DaytonaFileNotFoundError
from daytona.v2.errors import (
    ForbiddenError,
    NotFoundError,
    ReadOnlyUnsupportedError,
    SandboxNotRunningError,
    UnsupportedVolumeTypeError,
)
from daytona.v2.types import VolumeMount

from fleet_rlm import interpreter
from fleet_rlm.config import Settings
from fleet_rlm.interpreter import (
    DATASET_VOLUME_MOUNT,
    DatasetVolumeObjectMissing,
    close_dataset_volume_helpers,
    delete_volume_dataset,
    ensure_dataset_volume,
    read_volume_dataset,
    write_volume_dataset,
)
from fleet_rlm.opt_datasets import (
    DatasetValidationError,
    same_db_endpoint,
    scrub_secrets,
    validate_and_summarize_jsonl,
)
from fleet_rlm.storage import ObjectStorage, StorageError, StorageIntegrityError


def _make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "database_url": "postgresql://primary-host/neondb",
        "database_direct_url": "postgresql://primary-host/neondb",
        "daytona_api_key": "offline",
        "daytona_image": "fleet-test-image",
        "daytona_dataset_volume": "fleet-eval-datasets",
        "llm_model": "openai/gpt-4o-mini",
        "sub_llm_model": "openai/gpt-4o-mini",
        "llm_api_key": "offline",
        "s3_endpoint_url": "https://storage.example.test",
        "s3_access_key_id": "fixture-key",
        "s3_secret_access_key": "fixture-secret",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.mark.asyncio
async def test_storage_put_object_aborts_failed_multipart_and_cleans_corrupt_object(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = b'{"task": "hello", "needs_skill": false}\n'
    digest = hashlib.sha256(content).hexdigest()
    source = tmp_path / "dataset.jsonl"
    source.write_bytes(content)

    sdk = Mock(
        create_multipart_upload=Mock(return_value={"UploadId": "up-1"}),
        upload_part=Mock(
            side_effect=ClientError(
                {"Error": {"Code": "InternalError", "Message": "boom"}}, "UploadPart"
            )
        ),
        complete_multipart_upload=Mock(),
        abort_multipart_upload=Mock(),
        delete_object=Mock(),
        get_object=Mock(),
    )
    monkeypatch.setattr("fleet_rlm.storage.boto3.client", Mock(return_value=sdk))
    storage = ObjectStorage(_make_settings())

    # 1. upload_part failure must abort the multipart upload
    with pytest.raises(StorageError):
        await storage.put_object("optimizations/datasets/test.jsonl", source)
    sdk.abort_multipart_upload.assert_called_once_with(
        Bucket=storage.bucket, Key="optimizations/datasets/test.jsonl", UploadId="up-1"
    )
    sdk.delete_object.assert_not_called()

    # 2. complete_multipart_upload failure must also abort the multipart upload
    sdk.abort_multipart_upload.reset_mock()
    sdk.create_multipart_upload.return_value = {"UploadId": "up-2"}
    sdk.upload_part.side_effect = None
    sdk.upload_part.return_value = {"ETag": "etag-2"}
    sdk.complete_multipart_upload.side_effect = ClientError(
        {"Error": {"Code": "InternalError", "Message": "complete failed"}},
        "CompleteMultipartUpload",
    )
    with pytest.raises(StorageError):
        await storage.put_object("optimizations/datasets/test2.jsonl", source)
    sdk.abort_multipart_upload.assert_called_once_with(
        Bucket=storage.bucket, Key="optimizations/datasets/test2.jsonl", UploadId="up-2"
    )

    # 3. Integrity verification failure after completion deletes the corrupt object
    sdk.abort_multipart_upload.reset_mock()
    sdk.delete_object.reset_mock()
    sdk.create_multipart_upload.return_value = {"UploadId": "up-3"}
    sdk.complete_multipart_upload.side_effect = None
    sdk.get_object.return_value = {
        "Body": StreamingBody(BytesIO(b"x" * len(content)), len(content)),
        "ContentLength": len(content),
    }
    with pytest.raises(StorageIntegrityError):
        await storage.put_object("optimizations/datasets/test3.jsonl", source)
    sdk.delete_object.assert_called_once_with(
        Bucket=storage.bucket, Key="optimizations/datasets/test3.jsonl"
    )

    # 4. Happy path succeeds cleanly
    sdk.delete_object.reset_mock()
    sdk.get_object.return_value = {
        "Body": StreamingBody(BytesIO(content), len(content)),
        "ContentLength": len(content),
    }
    assert await storage.put_object("optimizations/datasets/ok.jsonl", source) == (
        digest,
        len(content),
    )


class _FakeVolumeSandbox:
    def __init__(self, sandbox_id: str, store: dict[str, bytes]) -> None:
        self.id = sandbox_id
        self.state = "started"
        self.wait = AsyncMock()
        self.store = store
        self.atomic_calls: list[bool] = []
        self.fs = SimpleNamespace(
            mkdir=AsyncMock(),
            write_bytes=self._write_bytes,
            read_bytes=self._read_bytes,
            remove=self._remove,
        )

    async def _write_bytes(self, path: str, content: bytes, *, atomic: bool = False) -> None:
        self.atomic_calls.append(atomic)
        self.store[path] = bytes(content)

    async def _read_bytes(self, path: str, *, max_response_bytes: int | None = None) -> bytes:
        if path not in self.store:
            raise DaytonaFileNotFoundError("not found")
        data = self.store[path]
        assert max_response_bytes is None or len(data) <= max_response_bytes
        return data

    async def _remove(self, path: str, *, recursive: bool = False) -> None:
        if path not in self.store:
            raise DaytonaFileNotFoundError("not found")
        del self.store[path]


@pytest.mark.asyncio
async def test_daytona_bucket_volume_ephemeral_read_only_and_caching() -> None:
    await close_dataset_volume_helpers()
    settings = _make_settings()
    files: dict[str, bytes] = {}
    created_sandboxes: list[_FakeVolumeSandbox] = []
    create_kwargs_log: list[dict[str, Any]] = []

    async def fake_create(image: str, **kwargs: Any) -> _FakeVolumeSandbox:
        assert image == settings.daytona_image
        create_kwargs_log.append(kwargs)
        sb = _FakeVolumeSandbox(f"sb-{len(created_sandboxes) + 1}", files)
        created_sandboxes.append(sb)
        return sb

    volumes_api = SimpleNamespace(
        get=AsyncMock(side_effect=NotFoundError("missing")),
        create=AsyncMock(return_value=SimpleNamespace(id="vol-bucket-1", type="bucket")),
    )
    daytona = SimpleNamespace(
        v2=SimpleNamespace(
            volumes=volumes_api,
            create=AsyncMock(side_effect=fake_create),
            delete=AsyncMock(),
        )
    )

    try:
        # Prefers bucket and caches result
        assert await ensure_dataset_volume(settings, daytona=daytona) == ("vol-bucket-1", "bucket")
        assert await ensure_dataset_volume(settings, daytona=daytona) == ("vol-bucket-1", "bucket")
        volumes_api.create.assert_awaited_once_with(name="fleet-eval-datasets", type="bucket")

        # Write mounts bucket read-write with air-gapped network policy and zero secrets
        vol_name, rel = await write_volume_dataset(
            settings, "datasets/sample.jsonl", b'{"task":"a"}\n', daytona=daytona
        )
        assert (vol_name, rel) == ("fleet-eval-datasets", "datasets/sample.jsonl")
        write_kw = create_kwargs_log[0]
        assert write_kw["network_policy"] == {"block_all": True}
        assert "env" not in write_kw and "secrets" not in write_kw
        assert write_kw["volumes"] == [
            VolumeMount(volume_id="vol-bucket-1", mount_path=DATASET_VOLUME_MOUNT, read_only=None)
        ]
        assert daytona.v2.delete.await_count == 1

        # Read mounts bucket read_only=True and deletes ephemeral sandbox afterward
        payload = await read_volume_dataset(settings, "datasets/sample.jsonl", daytona=daytona)
        assert payload == b'{"task":"a"}\n'
        read_kw = create_kwargs_log[1]
        assert read_kw["volumes"] == [
            VolumeMount(volume_id="vol-bucket-1", mount_path=DATASET_VOLUME_MOUNT, read_only=True)
        ]
        assert read_kw["network_policy"] == {"block_all": True}
        assert "env" not in read_kw and "secrets" not in read_kw
        assert daytona.v2.delete.await_count == 2

        # Delete removes file and is idempotent
        await delete_volume_dataset(settings, "datasets/sample.jsonl", daytona=daytona)
        await delete_volume_dataset(settings, "datasets/sample.jsonl", daytona=daytona)
        assert daytona.v2.delete.await_count == 4

        with pytest.raises(DatasetVolumeObjectMissing):
            await read_volume_dataset(settings, "datasets/sample.jsonl", daytona=daytona)

        # Path traversal is rejected before touching Daytona
        with pytest.raises(ValueError, match="Invalid dataset volume path"):
            await read_volume_dataset(settings, "../etc/passwd", daytona=daytona)
    finally:
        await close_dataset_volume_helpers()


@pytest.mark.asyncio
async def test_daytona_bucket_fallbacks_atomic_and_readonly() -> None:
    await close_dataset_volume_helpers()
    settings = _make_settings(daytona_dataset_volume="bucket-fallback-vol")
    files: dict[str, bytes] = {}
    create_calls = 0

    async def fake_create(_image: str, **kwargs: Any) -> _FakeVolumeSandbox:
        nonlocal create_calls
        create_calls += 1
        mount = kwargs["volumes"][0]
        if mount.read_only is True:
            raise ReadOnlyUnsupportedError("ro unsupported")
        sb = _FakeVolumeSandbox(f"sb-fb-{create_calls}", files)
        original_write = sb._write_bytes

        async def write_with_atomic_err(path: str, content: bytes, *, atomic: bool = False) -> None:
            if atomic:
                pytest.fail("Bucket mounts require a direct write to the dataset key")
            await original_write(path, content, atomic=False)

        sb.fs.write_bytes = write_with_atomic_err
        return sb

    daytona = SimpleNamespace(
        v2=SimpleNamespace(
            volumes=SimpleNamespace(
                get=AsyncMock(return_value=SimpleNamespace(id="vol-b2", type="bucket")),
            ),
            create=AsyncMock(side_effect=fake_create),
            delete=AsyncMock(),
        )
    )
    try:
        await write_volume_dataset(settings, "a.jsonl", b"hello", daytona=daytona)
        assert await read_volume_dataset(settings, "a.jsonl", daytona=daytona) == b"hello"
    finally:
        await close_dataset_volume_helpers()


@pytest.mark.asyncio
async def test_daytona_disk_volume_fallback_reuses_and_reaps_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await close_dataset_volume_helpers()
    monkeypatch.setattr(interpreter, "_DISK_HELPER_IDLE_SECONDS", 0.05)
    settings = _make_settings(daytona_dataset_volume="disk-fallback-vol")
    files: dict[str, bytes] = {}
    created: list[_FakeVolumeSandbox] = []

    async def fake_create(_image: str, **kwargs: Any) -> _FakeVolumeSandbox:
        assert kwargs["network_policy"] == {"block_all": True}
        assert "env" not in kwargs and "secrets" not in kwargs
        assert kwargs["volumes"] == [
            VolumeMount(volume_id="vol-disk-1", mount_path=DATASET_VOLUME_MOUNT, read_only=None)
        ]
        sb = _FakeVolumeSandbox(f"disk-sb-{len(created) + 1}", files)
        created.append(sb)
        return sb

    async def create_volume(*, name: str, type: str | None = None):
        if type == "bucket":
            raise UnsupportedVolumeTypeError("bucket not enabled in region")
        assert type == "disk"
        return SimpleNamespace(id="vol-disk-1", type="disk")

    daytona = SimpleNamespace(
        v2=SimpleNamespace(
            volumes=SimpleNamespace(
                get=AsyncMock(side_effect=NotFoundError("missing")),
                create=AsyncMock(side_effect=create_volume),
            ),
            create=AsyncMock(side_effect=fake_create),
            delete=AsyncMock(),
        )
    )

    try:
        assert await ensure_dataset_volume(settings, daytona=daytona) == ("vol-disk-1", "disk")
        await write_volume_dataset(settings, "ds.jsonl", b"row1\n", daytona=daytona)
        assert await read_volume_dataset(settings, "ds.jsonl", daytona=daytona) == b"row1\n"
        # Same disk helper sandbox was reused across write and read
        assert len(created) == 1
        assert daytona.v2.delete.await_count == 0

        # Wait for idle reaper to delete helper sandbox automatically
        await asyncio.sleep(0.12)
        assert daytona.v2.delete.await_count == 1

        # Next call allocates a fresh helper; if it dies with SandboxNotRunningError, retries once
        await write_volume_dataset(settings, "ds2.jsonl", b"row2\n", daytona=daytona)
        assert len(created) == 2

        async def dead_read(*_a: Any, **_k: Any) -> bytes:
            raise SandboxNotRunningError("stopped")

        created[1].fs.read_bytes = dead_read
        assert await read_volume_dataset(settings, "ds2.jsonl", daytona=daytona) == b"row2\n"
        assert len(created) == 3
    finally:
        await close_dataset_volume_helpers()
    assert daytona.v2.delete.await_count == 3


@pytest.mark.asyncio
async def test_daytona_disk_fallback_on_forbidden() -> None:
    await close_dataset_volume_helpers()
    settings = _make_settings(daytona_dataset_volume="forbidden-bucket-vol")

    async def create_volume(*, name: str, type: str | None = None):
        if type == "bucket":
            raise ForbiddenError("bucket forbidden")
        return SimpleNamespace(id="vol-disk-fb", type="disk")

    daytona = SimpleNamespace(
        v2=SimpleNamespace(
            volumes=SimpleNamespace(
                get=AsyncMock(side_effect=NotFoundError("missing")),
                create=AsyncMock(side_effect=create_volume),
            ),
        )
    )
    try:
        assert await ensure_dataset_volume(settings, daytona=daytona) == ("vol-disk-fb", "disk")
    finally:
        await close_dataset_volume_helpers()


def test_opt_datasets_validation_secret_scan_scrub_and_endpoint_normalization() -> None:
    # Endpoint normalization (default port 5432 + trailing '.')
    assert same_db_endpoint(
        "postgresql://u:p@ep-abc.neon.tech./neondb",
        "postgresql+psycopg://other:pw@EP-ABC.NEON.TECH:5432/neondb/",
    )
    assert not same_db_endpoint(
        "postgresql://u:p@ep-abc.neon.tech:5433/neondb",
        "postgresql://u:p@ep-abc.neon.tech/neondb",
    )
    settings = _make_settings(database_url="postgresql://u:p@primary-host.neon.tech/neondb")
    assert same_db_endpoint("postgresql://u:p@PRIMARY-HOST.NEON.TECH.:5432/neondb", settings)

    # Expanded secret scanning and recursive scrubbing
    secret_samples = [
        "napi_1234567890abcdef",
        "AKIAIOSFODNN7EXAMPLE",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
        "https://alice:supersecret@proxy.example.com/path",
        "xai-abcdef1234567890",
        "gsk_1234567890abcdefghij",
        "https://api.example.com/v1?api_key=my_secret_token_99",
    ]
    for sample in secret_samples:
        bad_jsonl = (
            f'{{"task": "{sample.replace(chr(10), " ")}", "worth_saving": false}}\n'
            '{"task": "second row", "worth_saving": true}\n'
        ).encode()
        with pytest.raises(DatasetValidationError, match="credentials or API keys"):
            validate_and_summarize_jsonl(bad_jsonl, "memory")

    nested = {
        "error": "failed with napi_1234567890abcdef and custom-token-xyz",
        "items": [
            {"url": "https://user:pw@host.test/a?token=abc123", "ok": True},
            ("xai-1234567890abcdef", 42),
        ],
    }
    scrubbed = scrub_secrets(nested, extra_secrets=["custom-token-xyz"])
    assert "napi_" not in scrubbed["error"]
    assert "custom-token-xyz" not in scrubbed["error"]
    assert "pw" not in scrubbed["items"][0]["url"]
    assert "abc123" not in scrubbed["items"][0]["url"]
    assert scrubbed["items"][0]["ok"] is True
    assert scrubbed["items"][1] == ("[REDACTED]", 42)

    # Minimum row counts: 2 for skills/memory, 3 for rlm
    with pytest.raises(DatasetValidationError, match="at least 2"):
        validate_and_summarize_jsonl(b'{"task": "only one", "worth_saving": false}\n', "memory")
    with pytest.raises(DatasetValidationError, match="at least 3"):
        validate_and_summarize_jsonl(
            b'{"task": "t1", "answer": "a1"}\n{"task": "t2", "answer": "a2"}\n',
            "rlm",
        )

    # RLM schema rejects expected_answer without answer or valid verifier, and checks skill_names
    with pytest.raises(DatasetValidationError, match="answer"):
        validate_and_summarize_jsonl(
            b'{"task": "t1", "expected_answer": "42"}\n'
            b'{"task": "t2", "answer": "ok"}\n'
            b'{"task": "t3", "answer": "ok"}\n',
            "rlm",
        )
    with pytest.raises(DatasetValidationError, match="unknown skill"):
        validate_and_summarize_jsonl(
            b'{"task": "t1", "answer": "a", "skill_names": ["missing-skill"]}\n'
            b'{"task": "t2", "answer": "b"}\n'
            b'{"task": "t3", "answer": "c"}\n',
            "rlm",
            available_skills={"data-analysis"},
        )

    # Memory validates existing_memories structure
    with pytest.raises(DatasetValidationError, match="existing_memories"):
        validate_and_summarize_jsonl(
            b'{"task": "t1", "worth_saving": true, "existing_memories": [{"scope": "bad"}]}\n'
            b'{"task": "t2", "worth_saving": false}\n',
            "memory",
        )

    # Split/lineage leakage check
    with pytest.raises(DatasetValidationError, match="leaks across multiple dataset splits"):
        validate_and_summarize_jsonl(
            b'{"task": "t1", "worth_saving": true, "split": "train", "lineage": "grp1"}\n'
            b'{"task": "t2", "worth_saving": false, "split": "holdout", "lineage": "grp1"}\n',
            "memory",
        )

    # Built-in eval datasets all pass canonical validation
    eval_dir = Path(__file__).resolve().parents[1] / "eval_datasets"
    for target, filename in (
        ("skills", "skills.jsonl"),
        ("memory", "memory.jsonl"),
        ("rlm", "rlm.jsonl"),
    ):
        raw = (eval_dir / filename).read_bytes()
        rows, summary = validate_and_summarize_jsonl(raw, target, strict_secrets=False)  # type: ignore[arg-type]
        assert len(rows) == summary["row_count"]
        assert len(rows) >= (3 if target == "rlm" else 2)
