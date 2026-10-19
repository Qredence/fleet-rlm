"""Offline regression coverage for immutable releases and inspect-before-retry behavior."""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
import zipfile
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
import yaml

from fleet_rlm import release_checks as release

SHA = "a" * 40
VERSION = "0.8.0"


def distributions(directory: Path, *, metadata_version: str = VERSION) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    metadata = f"Metadata-Version: 2.4\nName: fleet-rlm\nVersion: {metadata_version}\n".encode()
    with zipfile.ZipFile(directory / f"fleet_rlm-{VERSION}-py3-none-any.whl", "w") as archive:
        archive.writestr(f"fleet_rlm-{VERSION}.dist-info/METADATA", metadata)
    with tarfile.open(directory / f"fleet_rlm-{VERSION}.tar.gz", "w:gz") as archive:
        member = tarfile.TarInfo(f"fleet_rlm-{VERSION}/PKG-INFO")
        member.size = len(metadata)
        archive.addfile(member, io.BytesIO(metadata))


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "_version", lambda: VERSION)
    monkeypatch.setattr(release, "_git", lambda *_args: SHA)
    monkeypatch.setenv("FLEET_PIPELINE_ID", "pipeline")
    monkeypatch.setenv("CIRCLE_WORKFLOW_ID", "workflow")
    monkeypatch.setenv("CIRCLE_BRANCH", "main")
    monkeypatch.setenv("FLEET_RELEASE_SHA", SHA)
    monkeypatch.setenv("FLEET_RELEASE_VERSION", VERSION)
    monkeypatch.delenv("TWINE_PASSWORD", raising=False)
    monkeypatch.delenv("TWINE_USERNAME", raising=False)
    monkeypatch.setattr(release.subprocess, "run", Mock())
    directory = tmp_path / "dist"
    distributions(directory)
    path = tmp_path / "release/manifest.json"
    release.create_manifest(VERSION, SHA, directory, path)
    return path, directory, json.loads(path.read_text())


def remote_files(manifest):
    return [
        {
            "filename": item["filename"],
            "size": item["size"],
            "digests": {"sha256": item["sha256"]},
            "url": f"https://files.pythonhosted.org/{item['filename']}",
            "yanked": False,
        }
        for item in manifest["artifacts"]
    ]


def test_preflight_accepts_current_checkout():
    sha = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    release.preflight(release._version(), sha)


@pytest.mark.parametrize(
    ("version", "sha", "message"),
    [
        ("0.0.0", SHA, "does not match pyproject"),
        (VERSION, "abc", "full 40-character"),
        (VERSION, "b" * 40, "checked-out HEAD"),
    ],
)
def test_preflight_rejects_bad_identity(bundle, version, sha, message):
    _ = bundle
    with pytest.raises(SystemExit, match=message):
        release.preflight(version, sha)


@pytest.mark.parametrize("branch", ["feature/release", ""])
def test_remote_preflight_rejects_non_main(bundle, monkeypatch, branch):
    _ = bundle
    monkeypatch.setenv("CIRCLE_BRANCH", branch)
    with pytest.raises(SystemExit, match="main branch"):
        release.preflight(VERSION, SHA, remote=True)


def test_main_lineage_is_enforced(bundle, monkeypatch):
    _ = bundle
    monkeypatch.setattr(release.subprocess, "run", Mock(side_effect=subprocess.CalledProcessError(1, "git")))
    with pytest.raises(subprocess.CalledProcessError):
        release.verify_source(VERSION, SHA, main=True)


def test_remote_preflight_rejects_used_version(bundle, monkeypatch):
    _ = bundle
    monkeypatch.setattr(release, "_get", lambda *_args, **_kwargs: {"urls": []})
    with pytest.raises(SystemExit, match="already exists"):
        release.preflight(VERSION, SHA, remote=True)


def test_remote_preflight_rejects_existing_tag(bundle, monkeypatch):
    _ = bundle
    monkeypatch.setattr(release, "_get", Mock(side_effect=[None, None, {"object": {"sha": SHA}}]))
    with pytest.raises(SystemExit, match="tag or GitHub"):
        release.preflight(VERSION, SHA, remote=True)


def test_remote_preflight_accepts_absent_version(bundle, monkeypatch):
    _ = bundle
    get = Mock(return_value=None)
    monkeypatch.setattr(release, "_get", get)
    release.preflight(VERSION, SHA, remote=True)
    assert get.call_count == 4


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_http_errors_never_mean_absent(monkeypatch, status):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
    original = httpx.Client
    monkeypatch.setattr(release.httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(httpx.HTTPStatusError):
        release._get("https://pypi.org/pypi/fleet-rlm/0/json", absent=True)


def test_version_404_means_absent(monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(404, request=request))
    original = httpx.Client
    monkeypatch.setattr(release.httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    assert release._get("https://pypi.org/pypi/fleet-rlm/0/json", absent=True) is None


def test_network_timeout_propagates(bundle, monkeypatch):
    _ = bundle
    monkeypatch.setattr(release, "_get", Mock(side_effect=httpx.ReadTimeout("timed out")))
    with pytest.raises(httpx.ReadTimeout):
        release.preflight(VERSION, SHA, remote=True)


@pytest.mark.parametrize("mutation", ["missing", "extra", "metadata", "symlink"])
def test_distribution_identity(bundle, mutation):
    _, directory, _ = bundle
    wheel = next(directory.glob("*.whl"))
    if mutation == "missing":
        wheel.unlink()
    elif mutation == "extra":
        (directory / "other.whl").touch()
    elif mutation == "metadata":
        distributions(directory, metadata_version="9.9.9")
    else:
        saved = directory.parent / "saved.whl"
        wheel.rename(saved)
        wheel.symlink_to(saved)
    with pytest.raises(SystemExit):
        release.verify_artifacts(VERSION, directory)


def test_manifest_is_immutable_and_outside_dist(bundle):
    path, directory, _ = bundle
    with pytest.raises(SystemExit, match="new and outside"):
        release.create_manifest(VERSION, SHA, directory, path)
    with pytest.raises(SystemExit, match="new and outside"):
        release.create_manifest(VERSION, SHA, directory, directory / "manifest.json")


def test_manifest_detects_tampering(bundle):
    path, directory, _ = bundle
    wheel = next(directory.glob("*.whl"))
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("unexpected.py", "altered")
    with pytest.raises(SystemExit, match="hash or size"):
        release.verify_manifest(path, directory)


def test_manifest_binds_pipeline_and_lock(bundle, monkeypatch):
    path, directory, manifest = bundle
    monkeypatch.setenv("FLEET_PIPELINE_ID", "other")
    with pytest.raises(SystemExit, match="pipeline identity"):
        release.verify_manifest(path, directory, ci=True)
    manifest["lock_sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(SystemExit, match="lockfile"):
        release.verify_manifest(path, directory)


@pytest.mark.parametrize("mutation", ["hash", "size", "extra", "duplicate", "yanked"])
def test_registry_rejects_conflicting_evidence(bundle, monkeypatch, mutation):
    _, _, manifest = bundle
    files = remote_files(manifest)
    if mutation == "hash":
        files[0]["digests"]["sha256"] = "0" * 64
    elif mutation == "size":
        files[0]["size"] += 1
    elif mutation == "extra":
        files[0]["filename"] = "other.whl"
    elif mutation == "duplicate":
        files.append(files[0])
    else:
        files[0]["yanked"] = True
    monkeypatch.setattr(release, "_get", lambda *_args, **_kwargs: {"urls": files})
    with pytest.raises(SystemExit, match="identity mismatch"):
        release.registry_files(manifest, "testpypi")


def test_indexing_wait_is_bounded(bundle, monkeypatch):
    _, _, manifest = bundle
    monkeypatch.setattr(release, "registry_files", lambda *_args: {})
    monkeypatch.setattr(release.time, "monotonic", Mock(side_effect=[0, 300]))
    with pytest.raises(SystemExit, match="indexing timed out"):
        release.wait_registry(manifest, "testpypi")


def test_delayed_indexing(bundle, monkeypatch):
    _, _, manifest = bundle
    final = {item["filename"]: item for item in remote_files(manifest)}
    monkeypatch.setattr(release, "registry_files", Mock(side_effect=[{}, final]))
    sleep = Mock()
    monkeypatch.setattr(release.time, "sleep", sleep)
    assert release.wait_registry(manifest, "testpypi") == final
    sleep.assert_called_once()


@pytest.mark.parametrize("present", [0, 1, 2])
def test_publish_uploads_only_missing_original_files(bundle, monkeypatch, present):
    path, directory, manifest = bundle
    remote = remote_files(manifest)
    monkeypatch.setattr(release, "registry_files", lambda *_args: {item["filename"]: item for item in remote[:present]})
    monkeypatch.setattr(release, "wait_registry", lambda *_args: remote)
    run = Mock()
    monkeypatch.setattr(release.subprocess, "run", run)
    monkeypatch.setattr(release, "verify_source", Mock())
    receipt = path.parent / "receipt.json"
    release.publish(path, directory, "testpypi", receipt)
    if present == 2:
        run.assert_not_called()
    else:
        command = run.call_args.args[0]
        assert command[-(2 - present) :] == [str(directory / item["filename"]) for item in remote[present:]]
        assert "--skip-existing" not in command
    assert json.loads(receipt.read_text())["manifest_sha256"] == release._digest(path)


def test_unknown_upload_outcome_is_not_retried(bundle, monkeypatch):
    path, directory, _ = bundle
    monkeypatch.setattr(release, "verify_source", Mock())
    monkeypatch.setattr(release, "registry_files", lambda *_args: {})
    run = Mock(side_effect=subprocess.CalledProcessError(1, "twine"))
    monkeypatch.setattr(release.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        release.publish(path, directory, "testpypi", path.parent / "receipt.json")
    run.assert_called_once()


@pytest.mark.parametrize("status,approver", [("on_hold", None), ("failed", "user"), ("success", None)])
def test_approval_requires_server_owned_success_and_identity(bundle, monkeypatch, status, approver):
    _, _, manifest = bundle
    monkeypatch.setenv("CIRCLECI_READ_TOKEN", "test-only")
    monkeypatch.setattr(
        release,
        "_get",
        lambda *_args, **_kwargs: {
            "pipeline_id": "pipeline",
            "project_slug": "circleci/SomxVLJYMpo86z6XUrZBUq/3F8H681druYB2ErY5Af6Cf",
        },
    )
    monkeypatch.setattr(
        release, "_circle_jobs", lambda *_args: [{"name": "approve-pypi", "status": status, "approved_by": approver}]
    )
    with pytest.raises(SystemExit, match="approval"):
        release.approval_evidence(manifest)


def test_production_cannot_publish_without_approval(bundle, monkeypatch):
    path, directory, _ = bundle
    monkeypatch.delenv("CIRCLECI_READ_TOKEN", raising=False)
    monkeypatch.setattr(release, "verify_source", Mock())
    registry = Mock()
    monkeypatch.setattr(release, "registry_files", registry)
    with pytest.raises(SystemExit, match="approval evidence"):
        release.publish(path, directory, "pypi", path.parent / "receipt.json")
    registry.assert_not_called()


def test_approval_receipt_preserves_actor(bundle, monkeypatch):
    _, _, manifest = bundle
    monkeypatch.setenv("CIRCLECI_READ_TOKEN", "test-only")
    monkeypatch.setattr(
        release,
        "_get",
        lambda *_args, **_kwargs: {
            "pipeline_id": "pipeline",
            "project_slug": "circleci/SomxVLJYMpo86z6XUrZBUq/3F8H681druYB2ErY5Af6Cf",
        },
    )
    monkeypatch.setattr(
        release,
        "_circle_jobs",
        lambda *_args: [{"name": "approve-pypi", "status": "success", "approved_by": release.RELEASE_APPROVER_ID}],
    )
    assert release.approval_evidence(manifest)["approval"]["approved_by"] == release.RELEASE_APPROVER_ID


def test_downloads_hash_actual_bytes(bundle, monkeypatch):
    _, directory, manifest = bundle
    files = {item["filename"]: item for item in remote_files(manifest)}
    monkeypatch.setattr(release, "wait_registry", lambda *_args: files)

    def stream(method, url, **_kwargs):
        return nullcontext(
            httpx.Response(
                200, content=(directory / url.rsplit("/", 1)[1]).read_bytes(), request=httpx.Request(method, url)
            )
        )

    monkeypatch.setattr(release.httpx, "stream", stream)
    wheel = release.download_registry(manifest, "testpypi", directory.parent / "download")
    assert release._digest(wheel) == release._digest(next(directory.glob("*.whl")))


def test_download_rejects_corrupted_bytes(bundle, monkeypatch):
    _, directory, manifest = bundle
    monkeypatch.setattr(
        release, "wait_registry", lambda *_args: {item["filename"]: item for item in remote_files(manifest)}
    )
    monkeypatch.setattr(
        release.httpx,
        "stream",
        lambda method, url, **_kwargs: nullcontext(
            httpx.Response(200, content=b"bad", request=httpx.Request(method, url))
        ),
    )
    with pytest.raises(SystemExit, match="downloaded artifact"):
        release.download_registry(manifest, "testpypi", directory.parent / "download")


def test_exact_wheel_smoke_has_no_index_fallback(bundle, monkeypatch):
    _, directory, _ = bundle
    run = Mock()
    monkeypatch.setattr(release.subprocess, "run", run)
    monkeypatch.setattr(release.venv.EnvBuilder, "create", Mock())
    wheel = next(directory.glob("*.whl"))
    release.smoke(VERSION, wheel, None)
    commands = [call.args[0] for call in run.call_args_list]
    install = commands[0]
    assert install[-2:] == ["--no-deps", str(wheel.resolve())]
    assert all("--extra-index-url" not in command and "--index-url" not in command for command in commands)
    assert {Path(command[0]).name for command in commands} >= {"fleet", "fleet-rlm"}


def test_ci_quality_and_release_dependencies():
    config = yaml.safe_load(Path(".circleci/config.yml").read_text())
    assert config["parameters"]["run_release"]["default"] is False
    entries = config["workflows"]["required-ci"]["jobs"]
    jobs = {next(iter(item)): next(iter(item.values())) for item in entries if isinstance(item, dict)}
    assert set(jobs["quality-gate"]["requires"]) == {
        "tests-python-3.11",
        "tests-python-3.12",
        "tests-python-3.13",
        "static",
        "security",
    }
    assert jobs["approve-pypi"]["type"] == "approval"
    assert jobs["publish-pypi"]["requires"] == ["approve-pypi"]
    assert jobs["publish-pypi"]["serial-group"].endswith("/fleet-pypi")
    for name in ("release-preflight", "publish-testpypi", "publish-pypi", "github-release"):
        assert 'pipeline.git.branch == "main"' in jobs[name]["filters"]
        assert 'pipeline.event.name == "api"' in jobs[name]["filters"]
    contexts = {name: options["context"] for name, options in jobs.items() if "context" in options}
    assert set(contexts) == {"publish-testpypi", "publish-pypi", "github-release"}
    assert config["jobs"]["tests"]["steps"][-2]["store_test_results"]["path"] == "test-results"


@pytest.fixture
def github_bundle(bundle, monkeypatch):
    path, directory, manifest = bundle
    monkeypatch.setattr(release, "verify_source", Mock())
    from fleet_rlm import release_auth

    monkeypatch.setattr(release_auth, "github_app_token", lambda: nullcontext("test-only"))
    local = {item["filename"]: (directory / item["filename"]).read_bytes() for item in manifest["artifacts"]}
    local.update({path.name: path.read_bytes(), "SHA256SUMS": path.with_name("SHA256SUMS").read_bytes()})
    state = {"assets": dict(local), "sha": SHA, "exists": True}
    calls = []

    def get(url, **_kwargs):
        if "/git/ref/" in url:
            return {"object": {"type": "commit", "sha": state["sha"]}}
        if "/releases?" in url:
            return state.get("listed_releases", [])
        if not state["exists"]:
            return None
        return {
            "tag_name": "v0.8.0",
            "assets": [{"name": name} for name in state["assets"]],
            "draft": False,
            "prerelease": False,
            "html_url": "https://github.com/example/release",
        }

    def gh(*args, **_kwargs):
        calls.append(args)
        if args[1] == "download":
            destination = Path(args[args.index("--dir") + 1])
            for name, content in state["assets"].items():
                (destination / name).write_bytes(content)
        elif args[1] == "upload":
            for filename in args[3 : args.index("--repo")]:
                state["assets"][Path(filename).name] = Path(filename).read_bytes()
        elif args[1] == "create":
            state["exists"] = True
            state["assets"] = dict(local)
        return ""

    monkeypatch.setattr(release, "_get", get)
    monkeypatch.setattr(release, "_gh", gh)
    return path, directory, state, calls


def test_github_finalization_is_idempotent(github_bundle):
    path, directory, _, calls = github_bundle
    receipt = path.parent / "github.json"
    release.github_release(path, directory, receipt)
    assert json.loads(receipt.read_text())["status"] == "verified"
    assert [call[1] for call in calls] == ["download"]


def test_github_recovers_missing_assets_without_overwrite(github_bundle):
    path, directory, state, calls = github_bundle
    del state["assets"]["SHA256SUMS"]
    release.github_release(path, directory, path.parent / "github.json")
    uploads = [call for call in calls if call[1] == "upload"]
    assert len(uploads) == 1
    assert uploads[0][3 : uploads[0].index("--repo")] == (str(path.with_name("SHA256SUMS")),)
    assert not any("--clobber" in call for call in calls)


@pytest.mark.parametrize("conflict", ["sha", "bytes", "extra"])
def test_github_conflicts_stop_before_writes(github_bundle, conflict):
    path, directory, state, calls = github_bundle
    if conflict == "sha":
        state["sha"] = "b" * 40
    elif conflict == "bytes":
        state["assets"]["manifest.json"] = b"wrong"
    else:
        state["assets"]["extra.txt"] = b"unexpected"
    with pytest.raises(SystemExit, match=r"identity mismatch|hash mismatch|unexpected assets"):
        release.github_release(path, directory, path.parent / "github.json")
    assert not any(call[1] in {"create", "upload"} for call in calls)


def test_github_recovers_exact_tag_without_release(github_bundle):
    path, directory, state, calls = github_bundle
    state["exists"] = False
    release.github_release(path, directory, path.parent / "github.json")
    creates = [call for call in calls if call[1] == "create"]
    assert len(creates) == 1 and "--verify-tag" in creates[0]


def test_github_draft_release_prevents_duplicate_creation(github_bundle):
    path, directory, state, calls = github_bundle
    state["exists"] = False
    state["listed_releases"] = [{"tag_name": "v0.8.0", "draft": True, "prerelease": False}]

    with pytest.raises(SystemExit, match="draft or prerelease"):
        release.github_release(path, directory, path.parent / "github.json")

    assert not any(call[1] == "create" for call in calls)


def test_manifest_rejects_changed_release_inputs(bundle, monkeypatch):
    path, directory, _ = bundle
    monkeypatch.setenv("FLEET_RELEASE_SHA", "b" * 40)
    with pytest.raises(SystemExit, match="inputs do not match"):
        release.verify_manifest(path, directory, ci=True)


def test_long_lived_credentials_are_not_a_publishing_fallback(bundle, monkeypatch):
    path, directory, _ = bundle
    monkeypatch.setattr(release, "verify_source", Mock())
    monkeypatch.setenv("TWINE_PASSWORD", "test-only")
    with pytest.raises(SystemExit, match="requires OIDC"):
        release.publish(path, directory, "testpypi", path.parent / "receipt.json")


def test_approval_from_another_pipeline_is_rejected(bundle, monkeypatch):
    _, _, manifest = bundle
    monkeypatch.setenv("CIRCLECI_READ_TOKEN", "test-only")
    monkeypatch.setattr(
        release,
        "_get",
        lambda *_args, **_kwargs: {
            "pipeline_id": "other",
            "project_slug": "circleci/SomxVLJYMpo86z6XUrZBUq/3F8H681druYB2ErY5Af6Cf",
        },
    )
    with pytest.raises(SystemExit, match="pipeline identity"):
        release.approval_evidence(manifest)


def test_ci_interpreter_selection_overrides_developer_pin():
    config = yaml.safe_load(Path(".circleci/config.yml").read_text())
    assert config["executors"]["python"]["environment"]["UV_PYTHON"] == "<< parameters.version >>"


def test_manifest_normalizes_uppercase_source_sha(bundle):
    path, directory, _ = bundle
    new_path = path.parent / "uppercase/manifest.json"
    release.create_manifest(VERSION, SHA.upper(), directory, new_path)
    assert json.loads(new_path.read_text())["source_sha"] == SHA


def test_uv_build_marker_is_not_part_of_release_identity(bundle):
    path, directory, _ = bundle
    (directory / ".gitignore").write_text("*\n")
    release.verify_manifest(path, directory)
    new_path = path.parent / "with-marker/manifest.json"
    release.create_manifest(VERSION, SHA, directory, new_path)
    assert len(json.loads(new_path.read_text())["artifacts"]) == 2


@pytest.mark.parametrize("actor", ["other-user", "", None])
def test_unapproved_actor_is_rejected_before_any_registry_access(bundle, monkeypatch, actor):
    path, directory, _ = bundle
    monkeypatch.setenv("CIRCLECI_READ_TOKEN", "test-only")
    monkeypatch.setattr(release, "verify_source", Mock())
    monkeypatch.setattr(
        release,
        "_get",
        lambda *_args, **_kwargs: {
            "pipeline_id": "pipeline",
            "project_slug": "circleci/SomxVLJYMpo86z6XUrZBUq/3F8H681druYB2ErY5Af6Cf",
        },
    )
    monkeypatch.setattr(
        release, "_circle_jobs", lambda *_args: [{"name": "approve-pypi", "status": "success", "approved_by": actor}]
    )
    registry = Mock()
    monkeypatch.setattr(release, "registry_files", registry)
    with pytest.raises(SystemExit, match="authorized release approver"):
        release.publish(path, directory, "pypi", path.parent / "receipt.json")
    registry.assert_not_called()


@pytest.mark.parametrize("status", ["failed", "success", "canceled", "running"])
def test_failed_job_rerun_recovers_only_eligible_original_approval(bundle, monkeypatch, status):
    _, _, manifest = bundle
    manifest["workflow_id"] = "original"
    monkeypatch.setenv("CIRCLECI_READ_TOKEN", "test-only")
    monkeypatch.setattr(
        release,
        "_get",
        lambda *_args, **_kwargs: {
            "pipeline_id": "pipeline",
            "status": status,
            "project_slug": "circleci/SomxVLJYMpo86z6XUrZBUq/3F8H681druYB2ErY5Af6Cf",
        },
    )
    monkeypatch.setattr(
        release,
        "_circle_jobs",
        lambda workflow, _token: (
            []
            if workflow == "workflow"
            else [{"name": "approve-pypi", "status": "success", "approved_by": release.RELEASE_APPROVER_ID}]
        ),
    )
    if status in {"failed", "success"}:
        assert release.approval_evidence(manifest)["approval_workflow_id"] == "original"
    else:
        with pytest.raises(SystemExit, match="not eligible"):
            release.approval_evidence(manifest)


def test_duplicate_approvals_are_rejected(bundle, monkeypatch):
    _, _, manifest = bundle
    monkeypatch.setenv("CIRCLECI_READ_TOKEN", "test-only")
    monkeypatch.setattr(
        release,
        "_get",
        lambda *_args, **_kwargs: {
            "pipeline_id": "pipeline",
            "project_slug": "circleci/SomxVLJYMpo86z6XUrZBUq/3F8H681druYB2ErY5Af6Cf",
        },
    )
    monkeypatch.setattr(
        release,
        "_circle_jobs",
        lambda *_args: [{"name": "approve-pypi", "status": "success", "approved_by": release.RELEASE_APPROVER_ID}] * 2,
    )
    with pytest.raises(SystemExit, match="authorized release approver"):
        release.approval_evidence(manifest)


def test_activation_workflow_cannot_publish():
    config = yaml.safe_load(Path(".circleci/config.yml").read_text())
    assert config["parameters"]["run_activation_checks"]["default"] is False
    workflow = config["workflows"]["activation-checks"]
    assert "not pipeline.parameters.run_release" in workflow["when"]
    names = {next(iter(entry)) for entry in workflow["jobs"]}
    assert names == {"context-probe", "approve-pypi", "activation-approval", "activation-github"}
    assert "not pipeline.parameters.run_activation_checks" in config["workflows"]["required-ci"]["when"]
    assert config["jobs"]["context-probe"]["steps"][0]["run"]["command"] == "true"


def test_activation_probe_rejects_non_main_before_authentication(bundle, monkeypatch):
    path, _, _ = bundle
    monkeypatch.setenv("CIRCLE_BRANCH", "feature/probe")
    with pytest.raises(SystemExit, match="require main"):
        release.activation_check("approval", path.parent / "probe.json")
