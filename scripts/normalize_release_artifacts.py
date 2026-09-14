#!/usr/bin/env python3
"""Normalize wheel and source archives for reproducible release identities."""

from __future__ import annotations

import argparse
import gzip
import os
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path


class ArtifactNormalizationError(ValueError):
    """A release archive cannot be normalized safely."""


def _epoch(value: str | int | None) -> int:
    try:
        parsed = int(value if value is not None else os.environ.get("SOURCE_DATE_EPOCH", "0"))
    except (TypeError, ValueError) as exc:
        raise ArtifactNormalizationError("release artifact epoch must be an integer") from exc
    if parsed < 0:
        raise ArtifactNormalizationError("release artifact epoch must not be negative")
    return parsed


def _replace(path: Path, writer) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as output:
            writer(output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _zip_datetime(epoch: int) -> tuple[int, int, int, int, int, int]:
    current = datetime.fromtimestamp(epoch, UTC)
    year = max(1980, min(current.year, 2107))
    return year, current.month, current.day, current.hour, current.minute, current.second // 2 * 2


def _normalize_wheel(path: Path, epoch: int) -> None:
    with zipfile.ZipFile(path, "r") as source:
        entries = [(info.filename, source.read(info), info.is_dir()) for info in source.infolist()]

    date_time = _zip_datetime(epoch)

    def write(output) -> None:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data, is_dir in sorted(entries, key=lambda item: item[0]):
                info = zipfile.ZipInfo(name, date_time=date_time)
                info.create_system = 3
                info.external_attr = (0o40755 if is_dir else 0o100644) << 16
                info.compress_type = zipfile.ZIP_STORED if is_dir else zipfile.ZIP_DEFLATED
                archive.writestr(info, data)

    _replace(path, write)


def _normalize_sdist(path: Path, epoch: int) -> None:
    with tarfile.open(path, "r:gz") as source:
        entries = []
        for member in source.getmembers():
            data = source.extractfile(member).read() if member.isfile() else None
            entries.append((member, data))

    def write(output) -> None:
        with (
            gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=epoch) as compressed,
            tarfile.open(fileobj=compressed, mode="w|", format=tarfile.GNU_FORMAT) as archive,
        ):
            for original, data in sorted(entries, key=lambda item: item[0].name):
                member = tarfile.TarInfo(original.name)
                member.mode = original.mode
                member.type = original.type
                member.linkname = original.linkname
                member.size = len(data) if data is not None else 0
                member.mtime = epoch
                member.uid = 0
                member.gid = 0
                member.uname = ""
                member.gname = ""
                member.pax_headers = {}
                archive.addfile(member, None if data is None else _BytesReader(data))

    _replace(path, write)


class _BytesReader:
    """Small file-like adapter that avoids a temporary extracted file."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def normalize_release_artifacts(dist_dir: Path, epoch: str | int | None = None) -> tuple[Path, Path]:
    """Rewrite the single wheel and sdist in ``dist_dir`` with deterministic metadata.

    Both archives are replaced in place and their paths are returned. An
    invalid epoch or an archive inventory other than exactly one of each raises
    :class:`ArtifactNormalizationError`.
    """
    wheels = sorted(dist_dir.glob("fleet_rlm-*.whl"))
    sdists = sorted(dist_dir.glob("fleet_rlm-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ArtifactNormalizationError(
            f"expected exactly one wheel and one sdist in {dist_dir}, found {len(wheels)} and {len(sdists)}"
        )
    resolved_epoch = _epoch(epoch)
    _normalize_wheel(wheels[0], resolved_epoch)
    _normalize_sdist(sdists[0], resolved_epoch)
    return wheels[0], sdists[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--epoch", type=int)
    args = parser.parse_args(argv)
    try:
        wheel, sdist = normalize_release_artifacts(args.dist_dir, args.epoch)
    except (ArtifactNormalizationError, OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    print(f"OK: normalized release archives: {wheel.name}, {sdist.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
