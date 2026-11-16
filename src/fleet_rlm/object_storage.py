"""Host-only immutable file publication through an S3-compatible private bucket."""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from botocore.config import Config

from fleet_rlm.config import FleetSettings
from fleet_rlm.workspace import MAX_WORKSPACE_FILE_BYTES, _workspace_relative_path


class ObjectStorage:
    """Publish and verify bytes before any database row advertises them."""

    def __init__(self, settings: FleetSettings) -> None:
        import boto3

        if not settings.fleet_storage_endpoint or not settings.fleet_storage_bucket:
            raise ValueError("Published file storage is not configured")
        self.bucket = settings.fleet_storage_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.fleet_storage_endpoint,
            region_name=settings.fleet_storage_region,
            aws_access_key_id=settings.fleet_storage_access_key,
            aws_secret_access_key=settings.fleet_storage_secret_key.get_secret_value(),
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=10,
                read_timeout=30,
                retries={"max_attempts": 2},
            ),
        )

    def upload(
        self,
        session_id: str,
        path: Path,
        *,
        filename: str,
        origin: str,
        turn_id: str | None = None,
        workspace_path: str = "",
    ) -> dict[str, Any]:
        """Upload immutable bytes and read them back to verify size and digest."""
        size = path.stat().st_size
        if size > MAX_WORKSPACE_FILE_BYTES:
            raise ValueError("Published file exceeds the 25 MB limit")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        file_id = uuid.uuid4().hex
        workspace_path = _workspace_relative_path(workspace_path or f"files/{file_id}/{filename}")
        key = f"sessions/{session_id}/{file_id}/{digest}"
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.client.upload_file(
            str(path), self.bucket, key, ExtraArgs={"ContentType": media_type, "Metadata": {"sha256": digest}}
        )
        # Verify actual stored bytes, not ETag (which is not a SHA-256 contract).
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        try:
            downloaded_size = 0
            downloaded_hash = hashlib.sha256()
            for chunk in iter(lambda: body.read(1024 * 1024), b""):
                downloaded_size += len(chunk)
                if downloaded_size > size:
                    raise RuntimeError("Published object integrity verification failed")
                downloaded_hash.update(chunk)
        finally:
            body.close()
        if downloaded_size != size or downloaded_hash.hexdigest() != digest:
            raise RuntimeError("Published object integrity verification failed")
        return dict(
            file_id=file_id,
            turn_id=turn_id,
            origin=origin,
            filename=filename,
            media_type=media_type,
            size_bytes=size,
            sha256=digest,
            bucket=self.bucket,
            object_key=key,
            workspace_path=workspace_path,
        )

    def download_url(self, file: dict[str, Any]) -> str:
        """Issue a short-lived private object download."""
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": file["bucket"],
                "Key": file["object_key"],
                "ResponseContentDisposition": "attachment; filename*=UTF-8''" + quote(file["filename"]),
            },
            ExpiresIn=300,
        )

    def download(self, file: dict[str, Any], path: Path) -> None:
        """Download and verify immutable content before staging it for execution."""
        self.client.download_file(file["bucket"], file["object_key"], str(path))
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if path.stat().st_size != file["size_bytes"] or digest != file["sha256"]:
            raise RuntimeError("Stored file integrity verification failed")

    def collect_orphans(self, store: Any, *, grace_hours: int = 24) -> int:
        """Remove old, unreferenced Fleet uploads; never inspect another application's bucket."""
        from datetime import UTC, datetime, timedelta

        if grace_hours < 24:
            raise ValueError("Object cleanup requires a grace period of at least 24 hours")
        cutoff = datetime.now(UTC) - timedelta(hours=grace_hours)
        removed = 0
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix="sessions/"):
            candidates = [item["Key"] for item in page.get("Contents", []) if item["LastModified"] < cutoff]
            if not candidates:
                continue
            with store.pool.connection() as connection:
                referenced = {
                    row[0]
                    for row in connection.execute(
                        "SELECT object_key FROM files WHERE bucket=%s AND object_key=ANY(%s::text[])",
                        (self.bucket, candidates),
                    ).fetchall()
                }
            for key in candidates:
                if key not in referenced:
                    self.client.delete_object(Bucket=self.bucket, Key=key)
                    removed += 1
        return removed
