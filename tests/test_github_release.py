"""Publication only accepts an exact captured release and host canary evidence."""

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.github_release import publish
from scripts.release_checks import CIRCLECI_PROJECT_ID, canary_with_receipt, create_manifest
from scripts.release_notes import prepare_notes, verify_notes


@pytest.mark.parametrize(
    ("version", "sha", "repo"),
    [
        ("--help", "a" * 40, "owner/repo"),
        ("0.9.0", "--help", "owner/repo"),
        ("0.9.0", "a" * 40, "--help"),
        ("0.9.0", "a" * 40, "owner/repo/../../other"),
    ],
)
def test_publication_rejects_unsafe_identifiers_before_side_effects(tmp_path, version, sha, repo):
    with patch("scripts.github_release.subprocess.run") as run, pytest.raises(SystemExit):
        publish(version, sha, tmp_path / "dist", tmp_path / "metadata", tmp_path / "receipt.json", repo)
    run.assert_not_called()


@pytest.fixture
def release_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLE_WORKFLOW_ID", "workflow-test")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "fleet_rlm-0.9.0-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "fleet_rlm-0.9.0.tar.gz").write_bytes(b"sdist")
    create_manifest(dist)
    metadata = tmp_path / "release-metadata"
    metadata.mkdir()
    (metadata / "requirements.txt").write_text("dependency==1\n")
    sha = "a" * 40
    with patch("scripts.release_notes.subprocess.run") as git:
        git.return_value.stdout = "## [0.9.0]\n\n- Reviewed change.\n"
        prepare_notes("0.9.0", sha, dist / "SHA256SUMS", metadata)
        assert git.call_args.args[0] == ["git", "show", f"{sha}:CHANGELOG.md"]
    receipt = tmp_path / "receipt.json"
    with patch("scripts.release_checks.pypi_canary"):
        canary_with_receipt("0.9.0", sha, dist / "SHA256SUMS", metadata, receipt, "fleet-rlm-pypi", "placeholder")
    return dist, metadata, receipt, sha


@pytest.mark.parametrize("filename", ["release-notes.md", "requirements.txt", "metadata.json"])
def test_modified_release_metadata_blocks_publication(release_files, filename):
    dist, metadata, receipt, sha = release_files
    path = metadata / filename
    path.write_text(path.read_text() + "changed")
    with patch("scripts.github_release.gh_json") as api, pytest.raises((SystemExit, ValueError)):
        publish("0.9.0", sha, dist, metadata, receipt, "owner/repo")
    api.assert_not_called()


@pytest.mark.parametrize("field", ["source_sha", "project_id", "workflow_id", "outcome", "manifest_sha256", "checks"])
def test_unrelated_canary_receipt_blocks_publication(release_files, field):
    dist, metadata, receipt, sha = release_files
    result = json.loads(receipt.read_text())
    result[field] = "different"
    receipt.write_text(json.dumps(result))
    with patch("scripts.github_release.gh_json") as api, pytest.raises(SystemExit, match="canary receipt"):
        publish("0.9.0", sha, dist, metadata, receipt, "owner/repo")
    api.assert_not_called()


def test_missing_receipt_blocks_publication(release_files):
    dist, metadata, receipt, sha = release_files
    receipt.unlink()
    with patch("scripts.github_release.gh_json") as api, pytest.raises(FileNotFoundError):
        publish("0.9.0", sha, dist, metadata, receipt, "owner/repo")
    api.assert_not_called()


def test_new_release_uses_captured_notes_and_checksums(release_files):
    dist, metadata, receipt, sha = release_files
    with (
        patch("scripts.github_release.gh_json", return_value=None),
        patch("scripts.github_release.subprocess.run") as gh,
    ):
        publish("0.9.0", sha, dist, metadata, receipt, "owner/repo")
    command = gh.call_args.args[0]
    assert command[:4] == ["gh", "release", "create", "v0.9.0"]
    assert str(dist / "SHA256SUMS") in command
    assert command[command.index("--notes-file") + 1] == str(metadata / "release-notes.md")
    assert command[command.index("--target") + 1] == sha
    assert "--generate-notes" not in command


@pytest.mark.parametrize("conflict", [None, "notes", "assets", "checksum", "tag"])
def test_existing_release_is_noop_only_for_exact_source_notes_and_assets(release_files, conflict):
    dist, metadata, receipt, sha = release_files
    ref = {"object": {"type": "commit", "sha": "b" * 40 if conflict == "tag" else sha}}
    assets = sorted(dist.iterdir())
    release = {
        "draft": False,
        "body": (metadata / "release-notes.md").read_text(),
        "assets": [{"name": p.name} for p in assets],
    }
    if conflict == "notes":
        release["body"] = "different"
    if conflict == "assets":
        release["assets"] = []

    def download(command, **_kwargs):
        assert command[:3] == ["gh", "release", "download"]
        directory = Path(command[command.index("--dir") + 1])
        for asset in assets:
            (directory / asset.name).write_bytes(b"changed" if conflict == "checksum" else asset.read_bytes())

    with (
        patch("scripts.github_release.gh_json", side_effect=[ref, release]),
        patch("scripts.github_release.subprocess.run", side_effect=download),
    ):
        if conflict:
            with pytest.raises(SystemExit):
                publish("0.9.0", sha, dist, metadata, receipt, "owner/repo")
        else:
            publish("0.9.0", sha, dist, metadata, receipt, "owner/repo")


def test_metadata_capture_checks_source_sha(release_files):
    dist, metadata, _receipt, sha = release_files
    assert verify_notes("0.9.0", sha, dist / "SHA256SUMS", metadata)["source_sha"] == sha
    with pytest.raises(SystemExit, match="metadata does not match"):
        verify_notes("0.9.0", "b" * 40, dist / "SHA256SUMS", metadata)
    assert (
        json.loads((metadata / "metadata.json").read_text())["manifest_sha256"]
        == hashlib.sha256((dist / "SHA256SUMS").read_bytes()).hexdigest()
    )


def test_receipt_records_failed_setup_without_reusing_prior_success(release_files):
    dist, metadata, receipt, sha = release_files
    with patch("scripts.release_checks.pypi_canary", side_effect=TimeoutError), pytest.raises(TimeoutError):
        canary_with_receipt("0.9.0", sha, dist / "SHA256SUMS", metadata, receipt, "fleet-rlm-pypi", "placeholder")
    result = json.loads(receipt.read_text())
    assert result["outcome"] == "failed"
    assert result["project_id"] == CIRCLECI_PROJECT_ID
