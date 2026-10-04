"""Release tooling: CircleCI-to-GitHub bridge and reproducible archive normalization.

* ``test_circleci_trigger_release.py``: Contracts for the CircleCI-to-GitHub release bridge.
* ``test_normalize_release_artifacts.py``: Reproducible release archive normalization tests.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

import pytest
import yaml

from scripts import circleci_trigger_release as trigger
from scripts.validate_release import artifact_normalize_release_artifacts as normalize_release_artifacts

REPO_ROOT = Path(__file__).resolve().parents[3]
CIRCLECI_CONFIG = REPO_ROOT / ".circleci" / "config.yml"
RELEASE_VALIDATOR = REPO_ROOT / "scripts" / "validate_release.py"


def test_release_normalize_subcommand_help_is_inert() -> None:
    result = subprocess.run(
        [sys.executable, str(RELEASE_VALIDATOR), "normalize", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--dist-dir" in result.stdout
    assert "--epoch" in result.stdout


# --- from test_circleci_trigger_release.py ----------------------------
@pytest.mark.parametrize("dispatch_status", [200, 204])
def test_main_accepts_documented_dispatch_success_statuses(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
        if method == "GET":
            return 200, {"sha": "a" * 40}
        return dispatch_status, {}

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(trigger, "_existing_release_url", lambda *_args: None)
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
    dispatch = captured[1]
    assert dispatch["url"] == "https://api.github.com/repos/Qredence/fleet-rlm/actions/workflows/release.yml/dispatches"
    assert dispatch["method"] == "POST"
    payload = dispatch["payload"]
    assert isinstance(payload, dict)
    assert payload["ref"] == "main"
    assert payload["inputs"]["version"] == "0.7.3"
    assert payload["inputs"]["expected_sha"] == "a" * 40
    assert len(payload["inputs"]["dispatch_id"]) == 32
    assert "https://github.com/Qredence/fleet-rlm/actions/runs/1" in capsys.readouterr().out


@pytest.mark.parametrize("asset_hash_matches", [True, False])
def test_main_verifies_existing_latest_pypi_release_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    asset_hash_matches: bool,
) -> None:
    digest_wheel = "a" * 64
    digest_sdist = "b" * 64
    responses = {
        "https://pypi.org/pypi/fleet-rlm/json": (200, {"info": {"version": "0.7.10"}}),
        "https://pypi.org/pypi/fleet-rlm/0.7.10/json": (
            200,
            {
                "urls": [
                    {"filename": "fleet_rlm-0.7.10-py3-none-any.whl", "digests": {"sha256": digest_wheel}},
                    {"filename": "fleet_rlm-0.7.10.tar.gz", "digests": {"sha256": digest_sdist}},
                ]
            },
        ),
        "https://api.github.com/repos/Qredence/fleet-rlm/releases/tags/v0.7.10": (
            200,
            {
                "html_url": "https://github.com/Qredence/fleet-rlm/releases/tag/v0.7.10",
                "assets": [
                    {
                        "name": "fleet_rlm-0.7.10-py3-none-any.whl",
                        "digest": f"sha256:{digest_wheel if asset_hash_matches else 'c' * 64}",
                    },
                    {"name": "fleet_rlm-0.7.10.tar.gz", "digest": f"sha256:{digest_sdist}"},
                ],
            },
        ),
    }
    requested_urls: list[str] = []

    def fake_request(url: str, **_kwargs: object) -> tuple[int, dict[str, object]]:
        requested_urls.append(url)
        return responses[url]

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(trigger, "_request_json", fake_request)

    result = trigger.main(["--version", "v0.7.10", "--repository", "Qredence/fleet-rlm"])

    if asset_hash_matches:
        assert result == 0
        assert "https://github.com/Qredence/fleet-rlm/releases/tag/v0.7.10" in capsys.readouterr().out
        assert len(requested_urls) == 3
    else:
        assert result == 1
        assert "does not match its PyPI SHA-256" in capsys.readouterr().err
        assert len(requested_urls) == 3


@pytest.mark.parametrize("expected_sha", ["b" * 40, "short-sha"])
def test_bridge_rejects_invalid_or_changed_commit_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], expected_sha: str
) -> None:
    def request(_url: str, **kwargs: object) -> tuple[int, dict[str, object]]:
        assert kwargs.get("method", "GET") == "GET", "must not dispatch a mismatched release"
        return 200, {"sha": "a" * 40}

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(trigger, "_existing_release_url", lambda *_args: None)
    monkeypatch.setattr(trigger, "_request_json", request)
    assert (
        trigger.main(["--version", "0.7.3", "--repository", "Qredence/fleet-rlm", "--expected-sha", expected_sha]) == 1
    )
    message = "full lowercase" if expected_sha == "short-sha" else "does not match"
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("resolved_sha", ["a" * 40, "b" * 40])
def test_find_run_matches_dispatch_identity_and_commit(monkeypatch: pytest.MonkeyPatch, resolved_sha: str) -> None:
    title = "Release 0.7.3 [unique-dispatch]"
    target = {"id": 1, "display_title": title, "head_sha": resolved_sha}
    runs = [
        {"id": 99, "display_title": "Release 0.7.3 [another-dispatch]", "head_sha": "a" * 40},
        {"id": 100, "display_title": "Release 0.7.3 [manual]", "head_sha": "a" * 40},
        target,
    ]
    monkeypatch.setattr(trigger, "_request_json", lambda *_args, **_kwargs: (200, {"workflow_runs": runs}))
    kwargs = {
        "api_root": "https://api.github.com/repos/Qredence/fleet-rlm",
        "headers": {},
        "ref": "main",
        "expected_sha": "a" * 40,
        "display_title": title,
        "deadline": time.time() + 10,
    }
    if resolved_sha == "a" * 40:
        assert trigger._find_run(**kwargs) is target
    else:
        with pytest.raises(trigger.ReleaseTriggerError, match="different source commit"):
            trigger._find_run(**kwargs)


@pytest.mark.parametrize("expected", ["", "matching", "different", "invalid"])
def test_release_preflight_checks_source_and_supports_manual_dispatch(tmp_path: Path, expected: str) -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/release.yml").read_text())
    source = next(step for step in workflow["jobs"]["preflight"]["steps"] if step.get("id") == "source")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    values = {"": "", "matching": sha, "different": "0" * 40, "invalid": "short"}
    output = tmp_path / "github-output"
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", source["run"]],
        cwd=REPO_ROOT,
        env={**os.environ, "EXPECTED_SHA": values[expected], "RESOLVED_SHA": sha, "GITHUB_OUTPUT": str(output)},
        text=True,
        capture_output=True,
    )
    if expected in ("", "matching"):
        assert result.returncode == 0, result.stderr
        assert output.read_text() == f"source_sha={sha}\n"
    else:
        assert result.returncode != 0
        assert not output.exists()


@pytest.mark.parametrize("tag_state", ["matching", "different", "absent"])
def test_release_preflight_rejects_existing_tag_on_another_commit(tmp_path: Path, tag_state: str) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "--quiet")
    git(
        "-c",
        "user.name=CI test",
        "-c",
        "user.email=ci@example.invalid",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "first",
    )
    if tag_state != "absent":
        git("-c", "user.name=CI test", "-c", "user.email=ci@example.invalid", "tag", "-a", "v0.7.3", "-m", "release")
    if tag_state == "different":
        git(
            "-c",
            "user.name=CI test",
            "-c",
            "user.email=ci@example.invalid",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "second",
        )
    git("remote", "add", "origin", str(tmp_path))
    sha = git("rev-parse", "HEAD")
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/release.yml").read_text())
    step = next(
        s for s in workflow["jobs"]["preflight"]["steps"] if s.get("name") == "Verify existing release tag source"
    )
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "SOURCE_SHA": sha, "RELEASE_TAG": "v0.7.3"},
        text=True,
        capture_output=True,
    )
    if tag_state == "different":
        assert result.returncode != 0
        assert "different source commit" in result.stderr
    else:
        assert result.returncode == 0, result.stderr


def test_release_downstream_checkouts_use_verified_commit() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/release.yml").read_text())
    for name, job in workflow["jobs"].items():
        for step in job["steps"]:
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                expected = "${{ github.sha }}" if name == "preflight" else "${{ needs.preflight.outputs.source_sha }}"
                assert step["with"]["ref"] == expected
                if name != "preflight":
                    assert "preflight" in job["needs"]


def _circleci_run_step(job: dict[str, object], name: str) -> dict[str, object]:
    steps = job["steps"]
    assert isinstance(steps, list)
    for step in steps:
        if isinstance(step, dict) and isinstance(step.get("run"), dict) and step["run"].get("name") == name:
            return step["run"]
    raise AssertionError(f"CircleCI run step {name!r} not found")


@pytest.mark.parametrize(
    ("step_name", "status", "when"),
    [
        ("Update deploy to SUCCESS", "SUCCESS", "on_success"),
        ("Update planned deploy to FAILED", "FAILED", "on_fail"),
    ],
)
def test_circleci_pypi_marker_terminal_status_follows_publish_result(
    step_name: str,
    status: str,
    when: str,
) -> None:
    config = yaml.safe_load(CIRCLECI_CONFIG.read_text(encoding="utf-8"))
    job = config["jobs"]["deploy-pypi"]

    plan = _circleci_run_step(job, "Plan a PyPI deploy")
    running = _circleci_run_step(job, "Mark PyPI publish as running")
    publish = _circleci_run_step(job, "Trigger and wait for GitHub Actions release")
    terminal = _circleci_run_step(job, step_name)

    assert "release plan pypi-publish" in plan["command"]
    assert '"pypi"' in plan["command"]
    assert '"fleet-rlm"' in plan["command"]
    assert '"<< pipeline.parameters.release_version >>"' in plan["command"]
    assert running["command"] == "circleci run release update pypi-publish --status=RUNNING"
    assert "circleci_trigger_release.py" in publish["command"]
    assert terminal["command"] == f"circleci run release update pypi-publish --status={status}"
    assert terminal["when"] == when


def test_circleci_pypi_marker_records_cancellation_without_success() -> None:
    config = yaml.safe_load(CIRCLECI_CONFIG.read_text(encoding="utf-8"))
    cancel_job = config["jobs"]["cancel-deploy-pypi"]
    cancel_step = _circleci_run_step(cancel_job, "Update planned PyPI publish to CANCELED")
    cancel_workflow = next(
        item["cancel-deploy-pypi"]
        for item in config["workflows"]["ci"]["jobs"]
        if isinstance(item, dict) and "cancel-deploy-pypi" in item
    )

    assert cancel_step["command"] == "circleci run release update pypi-publish --status=CANCELED"
    assert cancel_workflow["requires"] == [{"deploy-pypi": ["canceled"]}]


@pytest.mark.parametrize("path", ["deploy.yml", "rollback.yml"])
def test_circleci_pypi_pipeline_definitions_do_not_enable_release_validation(path: str) -> None:
    config_path = CIRCLECI_CONFIG.with_name(path)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    release_jobs = [job for job in config["jobs"].values() if isinstance(job, dict) and job.get("type") == "release"]

    assert release_jobs
    assert all("validation" not in job for job in release_jobs)


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
