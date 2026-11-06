"""Offline release routing and publication-state regressions."""

import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

from scripts import release_automation as automation


@pytest.mark.parametrize("version", ["01.2.3", "1.2.3rc1", "1.2", "1.2.3+local"])
def test_stable_version_rejects_noncanonical(version):
    with pytest.raises(SystemExit):
        automation.stable_version(version)


def test_intent_uses_first_parent_exact_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    for version in ("1.0.0", "1.1.0"):
        Path("pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
        Path("uv.lock").write_text(f'[[package]]\nname = "fleet-rlm"\nversion = "{version}"\n')
        Path("CHANGELOG.md").write_text(f"## [{version}]\n\nRelease notes\n")
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-qm", version], check=True)
    sha = automation.git("rev-parse", "HEAD")
    assert automation.intent({}, "push", "main", sha, enabled=False) == {}
    assert automation.intent({}, "pull_request", "main", sha, enabled=True) == {}
    assert automation.intent({}, "push", "main", sha, enabled=True) == {
        "run_release": True,
        "release_version": "1.1.0",
        "release_sha": sha,
    }
    with pytest.raises(SystemExit, match="exact pipeline"):
        automation.intent({}, "push", "main", "f" * 40, enabled=True)


def test_registry_exists_or_unknown_blocks_publication(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(automation.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(SystemExit, match="recovery"):
        automation.registry_preflight("1.0.0")

    def missing(*_args, **_kwargs):
        raise urllib.error.HTTPError("url", 404, "missing", {}, None)

    monkeypatch.setattr(automation.urllib.request, "urlopen", missing)
    automation.registry_preflight("1.0.0")


def test_deployment_does_not_merge_and_reports_exact_source(tmp_path, monkeypatch):
    calls = []

    def api(path, body):
        calls.append((path, body))
        return {"id": 123}

    monkeypatch.setattr(automation, "github_api", api)
    receipt = tmp_path / "deployment.json"
    automation.deployment_start("1.0.0", "a" * 40, receipt)
    assert calls[0][1]["auto_merge"] is False
    assert calls[0][1]["ref"] == "a" * 40
    assert calls[1][1]["state"] == "in_progress"
    automation.deployment_status(receipt, "success", "validated and released")
    assert calls[-1][1]["auto_inactive"] is True
    assert json.loads(receipt.read_text())["id"] == 123


def test_prepare_preserves_dependencies_and_finalizes_notes(tmp_path, monkeypatch):
    from scripts import github_release

    monkeypatch.chdir(tmp_path)
    Path("pyproject.toml").write_text('[project]\nversion = "1.0.0"\ndependencies = ["example==2.0"]\n')
    Path("uv.lock").write_text(
        '[[package]]\nname = "fleet-rlm"\nversion = "1.0.0"\n[[package]]\nname = "example"\nversion = "2.0"\n'
    )
    Path("CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n<!-- fleet-release:start -->\n"
        f"<!-- sha256:{'a' * 64} -->\n### Fixes\n\n- Correct behavior (#12)\n"
        "<!-- fleet-release:end -->\n\n## [1.0.0]\n\nHistorical notes\n"
    )
    Path("openapi.yaml").write_text("info:\n  version: 1.0.0\n")
    monkeypatch.setattr(
        github_release, "gh_json", lambda path, **_kwargs: [] if "deployments" in path else {"draft": False}
    )
    monkeypatch.setattr(automation.subprocess, "run", lambda *_args, **_kwargs: None)
    automation.prepare("1.1.0")
    project = Path("pyproject.toml").read_text()
    assert 'version = "1.1.0"' in project
    assert '"example==2.0"' in project
    assert 'name = "example"\nversion = "2.0"' in Path("uv.lock").read_text()
    notes = Path("CHANGELOG.md").read_text()
    assert "fleet-release:" not in notes
    assert "sha256:" not in notes
    assert "Historical notes" in notes
    assert "Correct behavior" in automation.extract_notes("1.1.0", notes)
