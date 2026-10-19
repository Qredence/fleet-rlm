"""Verify, publish, and recover immutable Fleet RLM release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import venv
import zipfile
from email.parser import Parser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

PACKAGE = "fleet-rlm"
REPOSITORY = "Qredence/fleet-rlm"
PROJECT_ID = "122b2cc9-5a9c-4b3b-88c6-9276bd5d5d98"
RELEASE_APPROVER_ID = "bc6f50e9-09b0-43f9-8549-55c942fd3f84"
REGISTRIES = {"testpypi": "https://test.pypi.org", "pypi": "https://pypi.org"}


def _version() -> str:
    return tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def _digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _artifact_paths(directory: Path) -> list[Path]:
    # uv build creates this marker; it is never release content or an upload input.
    return sorted(path for path in directory.iterdir() if path.name != ".gitignore")


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _get(url: str, *, headers: dict[str, str] | None = None, absent: bool = False) -> Any:
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.get(url, headers=headers)
        if absent and response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()


def verify_source(version: str, sha: str, *, main: bool = False) -> None:
    """Bind a release to its checkout and, in CI, the main branch lineage."""
    if version != _version():
        raise SystemExit(f"requested version {version!r} does not match pyproject.toml ({_version()})")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise SystemExit("--sha must be a full 40-character source commit SHA")
    if _git("rev-parse", "HEAD").lower() != sha.lower():
        raise SystemExit("requested source SHA does not match checked-out HEAD")
    if main:
        if os.environ.get("CIRCLE_BRANCH") != "main" or os.environ.get("CIRCLE_TAG"):
            raise SystemExit("releases require the main branch, without a tag")
        _git("fetch", "--no-tags", "origin", "main:refs/remotes/origin/main")
        subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], check=True)
        _git("diff", "--exit-code", "HEAD", "--")


def preflight(version: str, sha: str, *, remote: bool = False) -> None:
    """Reject mismatched source and, for fresh CI releases, used versions/tags."""
    verify_source(version, sha, main=remote)
    if remote:
        for registry in REGISTRIES.values():
            if _get(f"{registry}/pypi/{PACKAGE}/{version}/json", absent=True) is not None:
                raise SystemExit("release version already exists in a registry; use recovery, not a fresh build")
        for suffix in (f"git/ref/tags/v{version}", f"releases/tags/v{version}"):
            if _get(f"https://api.github.com/repos/{REPOSITORY}/{suffix}", absent=True) is not None:
                raise SystemExit("release tag or GitHub release already exists")
    sys.stdout.write(f"Release preflight passed for {PACKAGE} {version} at {sha}\n")


def verify_artifacts(version: str, directory: Path) -> None:
    """Require one correctly named wheel and sdist with matching embedded metadata."""
    if str(Version(version)) != version:
        raise SystemExit("release version must be canonical")
    wheels = list(directory.glob(f"fleet_rlm-{version}-*.whl"))
    sdists = list(directory.glob(f"fleet_rlm-{version}.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1 or set(_artifact_paths(directory)) != set(wheels + sdists):
        raise SystemExit("expected one matching wheel and sdist, with no unexpected files")
    if any(path.is_symlink() or not path.is_file() for path in wheels + sdists):
        raise SystemExit("release artifacts must be regular files")
    name, embedded_version, _, _ = parse_wheel_filename(wheels[0].name)
    if canonicalize_name(name) != PACKAGE or embedded_version != Version(version):
        raise SystemExit("wheel filename identity mismatch")
    with zipfile.ZipFile(wheels[0]) as archive:
        paths = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(paths) != 1:
            raise SystemExit("expected exactly one wheel METADATA")
        wheel_metadata = archive.read(paths[0]).decode()
    with tarfile.open(sdists[0]) as archive:
        paths = [member for member in archive.getmembers() if member.name == f"fleet_rlm-{version}/PKG-INFO"]
        if len(paths) != 1 or not paths[0].isfile():
            raise SystemExit("expected one root sdist PKG-INFO")
        source = archive.extractfile(paths[0])
        if source is None:
            raise SystemExit("sdist metadata is missing")
        sdist_metadata = source.read().decode()
    for content in (wheel_metadata, sdist_metadata):
        metadata = Parser().parsestr(content)
        if canonicalize_name(metadata.get("Name", "")) != PACKAGE or metadata.get("Version") != version:
            raise SystemExit("embedded package metadata mismatch")


def create_manifest(version: str, sha: str, directory: Path, output: Path) -> None:
    """Write the immutable source and artifact identity outside the distribution directory."""
    verify_source(version, sha)
    verify_artifacts(version, directory)
    if output.exists() or output.resolve().is_relative_to(directory.resolve()):
        raise SystemExit("manifest must be new and outside dist")
    manifest = {
        "schema_version": 1,
        "package": PACKAGE,
        "version": version,
        "source_sha": sha.lower(),
        "lock_sha256": _digest(Path("uv.lock")),
        "pipeline_id": os.environ.get("FLEET_PIPELINE_ID", "local"),
        "workflow_id": os.environ.get("CIRCLE_WORKFLOW_ID", "local"),
        "artifacts": [
            {"filename": path.name, "size": path.stat().st_size, "sha256": _digest(path)}
            for path in _artifact_paths(directory)
        ],
    }
    _write(output, manifest)
    output.with_name("SHA256SUMS").write_text(
        "".join(f"{item['sha256']}  {item['filename']}\n" for item in manifest["artifacts"])
    )


def verify_manifest(path: Path, directory: Path, *, ci: bool = False) -> dict[str, Any]:
    """Verify source, lockfile, distribution metadata and every persisted artifact byte."""
    manifest = json.loads(path.read_text())
    if manifest.get("schema_version") != 1 or manifest.get("package") != PACKAGE:
        raise SystemExit("unsupported release manifest")
    verify_source(manifest["version"], manifest["source_sha"], main=ci)
    if manifest["lock_sha256"] != _digest(Path("uv.lock")):
        raise SystemExit("lockfile identity mismatch")
    if ci and (not os.environ.get("FLEET_PIPELINE_ID") or manifest["pipeline_id"] != os.environ["FLEET_PIPELINE_ID"]):
        raise SystemExit("release pipeline identity mismatch")
    if ci and (
        manifest["source_sha"] != os.environ.get("FLEET_RELEASE_SHA", "").lower()
        or manifest["version"] != os.environ.get("FLEET_RELEASE_VERSION")
    ):
        raise SystemExit("requested release inputs do not match manifest")
    verify_artifacts(manifest["version"], directory)
    actual = [
        {"filename": file.name, "size": file.stat().st_size, "sha256": _digest(file)}
        for file in _artifact_paths(directory)
    ]
    if manifest["artifacts"] != actual:
        raise SystemExit("artifact hash or size mismatch")
    return manifest


def registry_files(manifest: dict[str, Any], registry: str) -> dict[str, dict[str, Any]]:
    """Inspect registry metadata; only a version-level 404 counts as absent."""
    data = _get(f"{REGISTRIES[registry]}/pypi/{PACKAGE}/{manifest['version']}/json", absent=True)
    if data is None:
        return {}
    expected = {item["filename"]: item for item in manifest["artifacts"]}
    files = {}
    for item in data["urls"]:
        filename = item["filename"]
        wanted = expected.get(filename)
        if (
            wanted is None
            or filename in files
            or item["digests"]["sha256"] != wanted["sha256"]
            or item["size"] != wanted["size"]
            or item.get("yanked", False)
        ):
            raise SystemExit("registry artifact identity mismatch or yanked release")
        files[filename] = item
    return files


def wait_registry(manifest: dict[str, Any], registry: str) -> dict[str, dict[str, Any]]:
    """Wait at most five minutes for exact registry visibility; other errors fail closed."""
    deadline = time.monotonic() + 300
    while True:
        files = registry_files(manifest, registry)
        if len(files) == len(manifest["artifacts"]):
            return files
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SystemExit("registry indexing timed out")
        time.sleep(min(10, remaining))


def _circle_jobs(workflow: str, token: str) -> list[dict[str, Any]]:
    items = []
    url = f"https://circleci.com/api/v2/workflow/{workflow}/job"
    while True:
        data = _get(url, headers={"Circle-Token": token})
        items.extend(data["items"])
        if not data.get("next_page_token"):
            return items
        url = str(
            httpx.URL(
                f"https://circleci.com/api/v2/workflow/{workflow}/job", params={"page-token": data["next_page_token"]}
            )
        )


def approval_evidence(manifest: dict[str, Any]) -> dict[str, Any]:
    """Read server-owned approval and workflow identity immediately before production publication."""
    token = os.environ.get("CIRCLECI_READ_TOKEN")
    workflow = os.environ.get("CIRCLE_WORKFLOW_ID")
    if not token or not workflow:
        raise SystemExit("CIRCLECI_READ_TOKEN and workflow identity are required for approval evidence")

    def inspect(workflow_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        data = _get(f"https://circleci.com/api/v2/workflow/{workflow_id}", headers={"Circle-Token": token})
        if data["project_slug"] != "circleci/SomxVLJYMpo86z6XUrZBUq/3F8H681druYB2ErY5Af6Cf":
            raise SystemExit("approval project identity mismatch")
        if data["pipeline_id"] != manifest["pipeline_id"]:
            raise SystemExit("approval pipeline identity mismatch")
        return data, [job for job in _circle_jobs(workflow_id, token) if job["name"] == "approve-pypi"]

    _, approvals = inspect(workflow)
    approved_workflow = workflow
    original = manifest.get("workflow_id")
    # A failed-job rerun may omit the successful hold. Recover only the build
    # workflow's approval, in the same pipeline, never a cancelled or different one.
    if not approvals and original and original != workflow:
        original_data, approvals = inspect(original)
        if original_data.get("status") not in {"failed", "success"}:
            raise SystemExit("original workflow is not eligible for approval recovery")
        approved_workflow = original
    if (
        len(approvals) != 1
        or approvals[0]["status"] != "success"
        or approvals[0].get("approved_by") != RELEASE_APPROVER_ID
    ):
        raise SystemExit("successful production approval by the authorized release approver is required")
    return {
        "workflow_id": workflow,
        "approval_workflow_id": approved_workflow,
        "pipeline_id": manifest["pipeline_id"],
        "approval": approvals[0],
    }


def publish(path: Path, directory: Path, registry: str, receipt: Path) -> None:
    """Upload only missing files after identity checks; never blanket-retry an ambiguous upload."""
    manifest = verify_manifest(path, directory, ci=True)
    if os.environ.get("TWINE_PASSWORD") or os.environ.get("TWINE_USERNAME"):
        raise SystemExit("publishing requires OIDC, not configured Twine credentials")
    evidence = approval_evidence(manifest) if registry == "pypi" else None
    if evidence is not None:
        _write(receipt.with_name("approval.json"), evidence)
    files = registry_files(manifest, registry)
    missing = [str(directory / item["filename"]) for item in manifest["artifacts"] if item["filename"] not in files]
    if missing:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "twine",
                "upload",
                "--non-interactive",
                "--repository-url",
                f"{REGISTRIES[registry]}/legacy/",
                *missing,
            ],
            check=True,
        )
    verified = wait_registry(manifest, registry)
    _write(receipt, {"registry": registry, "manifest_sha256": _digest(path), "files": verified, "approval": evidence})


def download_registry(manifest: dict[str, Any], registry: str, directory: Path) -> Path:
    """Download and hash exact registry distributions without using a package-index resolver."""
    files = wait_registry(manifest, registry)
    directory.mkdir(parents=True, exist_ok=True)
    for wanted in manifest["artifacts"]:
        item = files[wanted["filename"]]
        url = urlsplit(item["url"])
        if url.scheme != "https" or url.hostname not in {"files.pythonhosted.org", "test-files.pythonhosted.org"}:
            raise SystemExit("untrusted registry download origin")
        target = directory / wanted["filename"]
        with (
            httpx.stream("GET", item["url"], timeout=30, follow_redirects=False) as response,
            target.open("wb") as output,
        ):
            response.raise_for_status()
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > wanted["size"]:
                    raise SystemExit("registry download exceeds expected size")
                output.write(chunk)
        if target.stat().st_size != wanted["size"] or _digest(target) != wanted["sha256"]:
            raise SystemExit("downloaded artifact hash or size mismatch")
    verify_artifacts(manifest["version"], directory)
    return next(directory.glob("*.whl"))


def smoke(version: str, wheel: Path, requirements: Path | None) -> None:
    """Install an exact local wheel and exercise both CLIs without live service initialization."""
    with tempfile.TemporaryDirectory(prefix="fleet-release-smoke-") as temp:
        env_dir = Path(temp) / "venv"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        python = env_dir / "bin/python"
        pip = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
        if requirements is not None:
            subprocess.run([*pip, "--requirement", str(requirements.resolve())], check=True)
        subprocess.run([*pip, "--no-deps", str(wheel.resolve())], check=True)
        for name in ("fleet", "fleet-rlm"):
            subprocess.run([str(env_dir / "bin" / name), "--help"], check=True)
        subprocess.run(
            [
                str(python),
                "-c",
                "from importlib.metadata import version; import sys; "
                "assert version('fleet-rlm') == sys.argv[1]; from fleet_rlm.api.app import create_app; "
                "app=create_app(initialize_db=False, configure_observability=False); "
                "schema=app.openapi(); assert schema['info']['title']=='Fleet-RLM Lite API'; "
                "assert '/health' in schema['paths']",
                version,
            ],
            check=True,
        )


def registry_smoke(path: Path, directory: Path, registry: str, requirements: Path, receipt: Path) -> None:
    """Verify both remote distributions and smoke-test the downloaded wheel."""
    manifest = verify_manifest(path, directory)
    with tempfile.TemporaryDirectory(prefix="fleet-registry-") as temp:
        wheel = download_registry(manifest, registry, Path(temp))
        smoke(manifest["version"], wheel, requirements)
    _write(receipt, {"registry": registry, "manifest_sha256": _digest(path), "python": sys.version, "status": "passed"})


def _gh(*args: str, token: str) -> str:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GH_APP_") and key not in {"GH_TOKEN", "GITHUB_TOKEN", "CIRCLECI_READ_TOKEN"}
    }
    env["GH_TOKEN"] = token
    return subprocess.run(["gh", *args], env=env, check=True, capture_output=True, text=True).stdout


def github_release(path: Path, directory: Path, receipt: Path) -> None:
    """Finalize a release, or verify an existing one, without replacing tags or assets."""
    manifest = verify_manifest(path, directory, ci=True)
    from fleet_rlm.release_auth import github_app_token

    with github_app_token() as token:
        _finalize_github_release(path, directory, receipt, manifest, token)


def _finalize_github_release(path: Path, directory: Path, receipt: Path, manifest: dict[str, Any], token: str) -> None:
    tag = f"v{manifest['version']}"
    # A non-404 API failure must never be interpreted as permission to create another release.
    headers = {"Authorization": f"Bearer {token}"}
    base = f"https://api.github.com/repos/{REPOSITORY}"
    existing = _get(f"{base}/releases/tags/{tag}", headers=headers, absent=True)
    ref = _get(f"{base}/git/ref/tags/{tag}", headers=headers, absent=True)
    if ref is not None:
        obj = ref["object"]
        if obj["type"] != "commit" or obj["sha"] != manifest["source_sha"]:
            raise SystemExit("existing release tag identity mismatch")
    expected = {item["filename"]: item["sha256"] for item in manifest["artifacts"]}
    expected[path.name] = _digest(path)
    sums = path.with_name("SHA256SUMS")
    wanted_sums = "".join(f"{item['sha256']}  {item['filename']}\n" for item in manifest["artifacts"])
    if sums.read_text() != wanted_sums:
        raise SystemExit("checksum listing identity mismatch")
    expected[sums.name] = _digest(sums)
    if existing is None:
        _gh(
            "release",
            "create",
            tag,
            *[str(directory / item["filename"]) for item in manifest["artifacts"]],
            str(path),
            str(sums),
            "--repo",
            REPOSITORY,
            "--target",
            manifest["source_sha"],
            "--title",
            f"Fleet RLM Lite {tag}",
            "--generate-notes",
            "--notes",
            f"Source: https://github.com/{REPOSITORY}/commit/{manifest['source_sha']}\n"
            f"Validation: https://app.circleci.com/pipelines/workflows/{manifest['workflow_id']}\n"
            "See the attached manifest and SHA256SUMS for artifact identity.",
            *(["--verify-tag"] if ref is not None else []),
            token=token,
        )
        existing = _get(f"{base}/releases/tags/{tag}", headers=headers)
        ref = _get(f"{base}/git/ref/tags/{tag}", headers=headers)
    if ref is None or ref["object"]["sha"] != manifest["source_sha"] or existing["draft"] or existing["prerelease"]:
        raise SystemExit("GitHub release identity mismatch")
    assets = {asset["name"]: asset for asset in existing["assets"]}
    if not set(assets).issubset(expected) or len(assets) != len(existing["assets"]):
        raise SystemExit("GitHub release has unexpected assets")
    with tempfile.TemporaryDirectory(prefix="fleet-github-") as temp:
        if assets:
            _gh("release", "download", tag, "--repo", REPOSITORY, "--dir", temp, token=token)
        if any(_digest(Path(temp) / name) != expected[name] for name in assets):
            raise SystemExit("GitHub release asset hash mismatch")
    local_assets = {item["filename"]: directory / item["filename"] for item in manifest["artifacts"]}
    local_assets.update({path.name: path, sums.name: sums})
    missing = [str(local_assets[name]) for name in expected if name not in assets]
    if missing:
        _gh("release", "upload", tag, *missing, "--repo", REPOSITORY, token=token)
        # Download again rather than trusting upload success or a registry-supplied digest.
        refreshed = _get(f"{base}/releases/tags/{tag}", headers=headers)
        if sorted(asset["name"] for asset in refreshed["assets"]) != sorted(expected):
            raise SystemExit("GitHub release assets remain incomplete")
        with tempfile.TemporaryDirectory(prefix="fleet-github-") as temp:
            _gh("release", "download", tag, "--repo", REPOSITORY, "--dir", temp, token=token)
            if any(_digest(Path(temp) / name) != digest for name, digest in expected.items()):
                raise SystemExit("GitHub release asset hash mismatch")
    _write(receipt, {"manifest_sha256": _digest(path), "tag": tag, "url": existing["html_url"], "status": "verified"})


def activation_check(check: str, receipt: Path) -> None:
    """Exercise authorization and token scope without any publication or release creation."""
    if os.environ.get("CIRCLE_BRANCH") != "main" or os.environ.get("CIRCLE_TAG"):
        raise SystemExit("activation evidence checks require main")
    pipeline = os.environ.get("FLEET_PIPELINE_ID")
    if not pipeline:
        raise SystemExit("activation checks require pipeline identity")
    if check == "approval":
        evidence = approval_evidence({"pipeline_id": pipeline})
    else:
        from fleet_rlm.release_auth import github_app_token

        with github_app_token():
            pass
        evidence = {"repository": REPOSITORY, "permissions": {"contents": "write"}, "token_revoked": True}
    _write(receipt, {"check": check, "pipeline_id": pipeline, "evidence": evidence, "status": "passed"})


def main() -> None:
    """Run release checks; network writes require the explicit publish/finalize subcommands."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    activation = commands.add_parser("activation-check")
    activation.add_argument("--check", choices=("approval", "github"), required=True)
    activation.add_argument("--receipt", type=Path, required=True)
    check = commands.add_parser("preflight")
    check.add_argument("--version", required=True)
    check.add_argument("--sha", required=True)
    check.add_argument("--remote", action="store_true")
    artifacts = commands.add_parser("verify-artifacts")
    artifacts.add_argument("--version", required=True)
    artifacts.add_argument("--dist", type=Path, required=True)
    create = commands.add_parser("manifest")
    create.add_argument("--version", required=True)
    create.add_argument("--sha", required=True)
    create.add_argument("--dist", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    for name in ("verify-manifest", "publish", "registry-smoke", "github-release"):
        command = commands.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--dist", type=Path, required=True)
        if name in {"publish", "registry-smoke"}:
            command.add_argument("--registry", choices=REGISTRIES, required=True)
        if name != "verify-manifest":
            command.add_argument("--receipt", type=Path, required=True)
        if name == "registry-smoke":
            command.add_argument("--requirements", type=Path, required=True)
    local_smoke = commands.add_parser("smoke")
    local_smoke.add_argument("--version", required=True)
    local_smoke.add_argument("--wheel", type=Path, required=True)
    local_smoke.add_argument("--requirements", type=Path)
    args = parser.parse_args()
    if args.command == "activation-check":
        activation_check(args.check, args.receipt)
    elif args.command == "preflight":
        preflight(args.version, args.sha, remote=args.remote)
    elif args.command == "verify-artifacts":
        verify_artifacts(args.version, args.dist)
    elif args.command == "manifest":
        create_manifest(args.version, args.sha, args.dist, args.output)
    elif args.command == "verify-manifest":
        verify_manifest(args.manifest, args.dist)
    elif args.command == "publish":
        publish(args.manifest, args.dist, args.registry, args.receipt)
    elif args.command == "registry-smoke":
        registry_smoke(args.manifest, args.dist, args.registry, args.requirements, args.receipt)
    elif args.command == "github-release":
        github_release(args.manifest, args.dist, args.receipt)
    else:
        smoke(args.version, args.wheel, args.requirements)


if __name__ == "__main__":
    main()
