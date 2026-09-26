"""Release tooling: CircleCI-to-GitHub bridge and reproducible archive normalization.

* ``test_circleci_trigger_release.py``: Contracts for the CircleCI-to-GitHub release bridge.
* ``test_normalize_release_artifacts.py``: Reproducible release archive normalization tests.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
import time
import zipfile
from pathlib import Path

import pytest

from scripts import circleci_trigger_release as trigger
from scripts.normalize_release_artifacts import normalize_release_artifacts


# --- from test_circleci_trigger_release.py ----------------------------
@pytest.mark.parametrize("dispatch_status", [200, 204])
def test_main_accepts_documented_dispatch_success_statuses(
    monkeypatch: pytest.MonkeyPatch,
    dispatch_status: int,
) -> None:
    captured: list[dict[str, object]] = []

    def fake_request(
        url: str,
        *,
        headers: dict[str, str],
        method: str = "GET",
        payload: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        captured.append(
            {
                "url": url,
                "headers": headers,
                "method": method,
                "payload": payload,
            }
        )
        return dispatch_status, {}

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(trigger, "_request_json", fake_request)
    monkeypatch.setattr(
        trigger,
        "_find_run",
        lambda **_kwargs: {"id": 1, "html_url": "https://github.com/Qredence/fleet-rlm/actions/runs/1"},
    )
    monkeypatch.setattr(trigger, "_wait_for_run", lambda **_kwargs: None)

    assert (
        trigger.main(
            [
                "--version",
                "0.7.3",
                "--repository",
                "Qredence/fleet-rlm",
                "--ref",
                "main",
            ]
        )
        == 0
    )
    assert captured[0]["url"] == (
        "https://api.github.com/repos/Qredence/fleet-rlm/actions/workflows/release.yml/dispatches"
    )
    assert captured[0]["method"] == "POST"
    assert captured[0]["payload"] == {"ref": "main", "inputs": {"version": "0.7.3"}}


# --- from test_normalize_release_artifacts.py -------------------------
def _write_archives(
    directory: Path,
    *,
    timestamp: int,
    reverse: bool,
    file_mode: int = 0o644,
    dir_mode: int = 0o755,
) -> None:
    directory.mkdir(exist_ok=True)
    wheel = directory / "fleet_rlm-0.7.8-py3-none-any.whl"
    entries = [
        ("fleet_rlm/__init__.py", b"__version__ = '0.7.8'\n"),
        ("fleet_rlm-0.7.8.dist-info/WHEEL", b"Wheel-Version: 1.0\n"),
    ]
    if reverse:
        entries.reverse()
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 14, 12, 0, 0))
            archive.writestr(info, data)

    sdist = directory / "fleet_rlm-0.7.8.tar.gz"
    with (
        sdist.open("wb") as output,
        gzip.GzipFile(fileobj=output, mode="wb", mtime=timestamp) as compressed,
        tarfile.open(fileobj=compressed, mode="w|", format=tarfile.GNU_FORMAT) as archive,
    ):
        directory_member = tarfile.TarInfo("fleet_rlm-0.7.8/fleet_rlm")
        directory_member.type = tarfile.DIRTYPE
        directory_member.mode = dir_mode
        directory_member.mtime = timestamp
        archive.addfile(directory_member)
        for name, data in entries if not reverse else reversed(entries):
            member = tarfile.TarInfo(f"fleet_rlm-0.7.8/{name}")
            member.mode = file_mode
            member.mtime = timestamp
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


def _hashes(directory: Path) -> tuple[str, str]:
    return tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(directory.iterdir()))


def test_normalization_makes_archive_bytes_independent_of_source_metadata(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_archives(first, timestamp=int(time.time()) - 3600, reverse=False, file_mode=0o600, dir_mode=0o700)
    _write_archives(second, timestamp=int(time.time()), reverse=True, file_mode=0o666, dir_mode=0o777)

    normalize_release_artifacts(first, 946684800)
    normalize_release_artifacts(second, 946684800)

    assert _hashes(first) == _hashes(second)


def test_normalized_archives_retain_payloads(tmp_path: Path) -> None:
    _write_archives(tmp_path, timestamp=946684800, reverse=False, file_mode=0o600, dir_mode=0o700)
    normalize_release_artifacts(tmp_path, 946684800)

    with zipfile.ZipFile(tmp_path / "fleet_rlm-0.7.8-py3-none-any.whl") as archive:
        assert archive.read("fleet_rlm/__init__.py") == b"__version__ = '0.7.8'\n"
    with tarfile.open(tmp_path / "fleet_rlm-0.7.8.tar.gz", "r:gz") as archive:
        assert archive.extractfile("fleet_rlm-0.7.8/fleet_rlm/__init__.py").read() == b"__version__ = '0.7.8'\n"
        modes = {member.name: member.mode & 0o777 for member in archive.getmembers()}
        assert modes["fleet_rlm-0.7.8/fleet_rlm"] == 0o755
        assert modes["fleet_rlm-0.7.8/fleet_rlm/__init__.py"] == 0o644
