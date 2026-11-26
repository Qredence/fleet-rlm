"""Host-only storage for committed workspace files."""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from contextlib import closing, suppress
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .config import Settings


class StorageError(RuntimeError):
    """A workspace object could not be read or written."""


class StorageObjectMissing(StorageError):
    """The object named by the current file record no longer exists."""


class StorageIntegrityError(StorageError):
    """The returned bytes differ from the committed file record."""


async def _transfer[T](
    operation: Callable[[], T], *, on_cancel: Callable[[T], Awaitable[None]] | None = None
) -> T:
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # The SDK uses blocking I/O. Keep the caller's spool and write fence alive
        # until the request settles, even when the HTTP client has disconnected.
        settlement = asyncio.gather(task, return_exceptions=True)
        while not settlement.done():
            with suppress(asyncio.CancelledError):
                await asyncio.shield(settlement)
        result = settlement.result()[0]
        if on_cancel is not None and not isinstance(result, BaseException):
            await on_cancel(result)
        raise
    except ClientError as error:
        if error.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
            raise StorageObjectMissing("Workspace object not found") from error
        raise StorageError("Workspace storage request failed") from error
    except BotoCoreError as error:
        raise StorageError("Workspace storage request failed") from error


class ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        if (
            not settings.s3_endpoint_url
            or not settings.s3_access_key_id
            or not settings.s3_secret_access_key
        ):
            raise ValueError("Configure the Neon Object Storage endpoint and credentials")
        self.bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=10,
                read_timeout=30,
                retries={"mode": "standard", "total_max_attempts": 2},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def prefix(session_id: UUID) -> str:
        return f"sessions/{session_id}/"

    @classmethod
    def new_key(cls, session_id: UUID) -> str:
        return f"{cls.prefix(session_id)}{uuid4().hex}"

    @staticmethod
    def optimization_artifact_key(job_id: UUID, target: str) -> str:
        return f"optimizations/artifacts/{job_id}/{target}.json"

    @staticmethod
    def optimization_dataset_key(dataset_id: UUID) -> str:
        return f"optimizations/datasets/{dataset_id}.jsonl"

    async def put_object(self, key: str, path: Path) -> tuple[str, int]:
        """Upload and SHA-256 verify a non-session object without a cleanup lease callback."""

        async def _noop_register(_upload_id: str) -> None:
            return None

        return await self.put(key, path, register=_noop_register)

    async def delete_object(self, key: str) -> None:
        """Delete a single object from the workspace bucket."""
        await _transfer(lambda: self._client.delete_object(Bucket=self.bucket, Key=key))

    async def put(
        self, key: str, path: Path, *, register: Callable[[str], Awaitable[None]]
    ) -> tuple[str, int]:
        def begin_upload() -> tuple[str, str, int]:
            with path.open("rb") as body:
                digest = hashlib.file_digest(body, "sha256").hexdigest()
                size = body.tell()
            upload = self._client.create_multipart_upload(
                Bucket=self.bucket, Key=key, Metadata={"sha256": digest}
            )
            return upload["UploadId"], digest, size

        async def abort_initialization(result: tuple[str, str, int]) -> None:
            with suppress(StorageError):
                await _transfer(lambda: self._abort_upload(key, result[0]))

        upload_id, digest, size = await _transfer(begin_upload, on_cancel=abort_initialization)
        try:
            # A lease holder cannot write data until its abortable upload ID is durable.
            await register(upload_id)
        except (Exception, asyncio.CancelledError):
            with suppress(StorageError):
                await _transfer(lambda: self._abort_upload(key, upload_id))
            raise

        def write() -> None:
            with path.open("rb") as body:
                part = self._client.upload_part(
                    Bucket=self.bucket,
                    Key=key,
                    UploadId=upload_id,
                    PartNumber=1,
                    Body=body,
                    ContentLength=size,
                )
            self._client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": [{"ETag": part["ETag"], "PartNumber": 1}]},
            )
            self._read_verified(key, sha256=digest, size=size)

        await _transfer(write)
        return digest, size

    async def download(self, key: str, path: Path, *, sha256: str, size: int) -> None:
        def read() -> None:
            with path.open("wb") as output:
                self._read_verified(key, sha256=sha256, size=size, output=output)

        await _transfer(read)

    def _read_verified(
        self, key: str, *, sha256: str, size: int, output: BinaryIO | None = None
    ) -> None:
        item = self._client.get_object(Bucket=self.bucket, Key=key)
        with closing(item["Body"]) as body:
            if item["ContentLength"] != size:
                raise StorageIntegrityError("Workspace object size does not match its record")
            digest = hashlib.sha256()
            length = 0
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                digest.update(chunk)
                length += len(chunk)
                if output is not None:
                    output.write(chunk)
            if length != size or digest.hexdigest() != sha256:
                raise StorageIntegrityError("Workspace object hash does not match its record")

    def _abort_upload(self, key: str, upload_id: str) -> None:
        try:
            self._client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
        except ClientError as error:
            if error.response["Error"]["Code"] not in {"NoSuchUpload", "404", "NotFound"}:
                raise

    async def reclaim(self, key: str, upload_id: str | None) -> None:
        def delete() -> None:
            if upload_id is not None:
                self._abort_upload(key, upload_id)
            self._client.delete_object(Bucket=self.bucket, Key=key)

        await _transfer(delete)

    async def delete_session(self, session_id: UUID) -> None:
        def delete() -> None:
            prefix = self.prefix(session_id)
            while uploads := self._client.list_multipart_uploads(
                Bucket=self.bucket, Prefix=prefix, MaxUploads=1000
            ).get("Uploads"):
                for upload in uploads:
                    self._abort_upload(upload["Key"], upload["UploadId"])
            while True:
                page = self._client.list_objects_v2(Bucket=self.bucket, Prefix=prefix, MaxKeys=1000)
                keys = [{"Key": item["Key"]} for item in page.get("Contents", ())]
                if not keys:
                    return
                result = self._client.delete_objects(
                    Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True}
                )
                if result.get("Errors"):
                    raise StorageError("Some workspace objects could not be deleted")

        await _transfer(delete)
