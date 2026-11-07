"""Publish captured release notes and verified assets, refusing conflicting retries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from scripts.release_checks import _validate_version, verify_canary_receipt, verify_manifest
from scripts.release_notes import verify_notes


def gh_json(path: str, *, missing_ok: bool = False):
    result = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=False)
    if result.returncode:
        if missing_ok and "HTTP 404" in result.stderr:
            return None
        raise SystemExit(f"GitHub API request failed: {path} (exit {result.returncode})")
    return json.loads(result.stdout)


def publish(version: str, source_sha: str, dist: Path, metadata_dir: Path, receipt: Path, repo: str) -> None:
    _validate_version(version)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", source_sha):
        raise SystemExit("release publication requires a full source SHA")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*", repo):
        raise SystemExit("release repository must be an owner/name pair")
    verify_manifest(dist)
    verify_notes(version, source_sha, dist / "SHA256SUMS", metadata_dir)
    verify_canary_receipt(version, source_sha, dist / "SHA256SUMS", receipt)
    notes = (metadata_dir / "release-notes.md").read_text()
    assets = sorted([*dist.glob("fleet_rlm-*.whl"), *dist.glob("fleet_rlm-*.tar.gz"), dist / "SHA256SUMS"])
    tag = f"v{version}"
    ref = gh_json(f"repos/{repo}/git/ref/tags/{tag}", missing_ok=True)
    if ref is not None:
        target = ref["object"]
        for _ in range(8):
            if target["type"] == "commit":
                break
            if target["type"] != "tag":
                raise SystemExit("release tag does not point to a commit")
            target = gh_json(f"repos/{repo}/git/tags/{target['sha']}")["object"]
        if target["type"] != "commit" or target["sha"].lower() != source_sha.lower():
            raise SystemExit("release tag points to a different source commit")
    release = gh_json(f"repos/{repo}/releases/tags/{tag}", missing_ok=True)
    if release is not None:
        if ref is None or release["draft"] or release["body"].strip() != notes.strip():
            raise SystemExit("existing GitHub release conflicts with captured notes or tag")
        if sorted(asset["name"] for asset in release["assets"]) != sorted(asset.name for asset in assets):
            raise SystemExit("existing GitHub release assets differ; inspect and recover manually")
        with tempfile.TemporaryDirectory(prefix="fleet-github-release-") as temp:
            subprocess.run(["gh", "release", "download", tag, "--repo", repo, "--dir", temp], check=True)
            for asset in assets:
                if (
                    hashlib.sha256((Path(temp) / asset.name).read_bytes()).digest()
                    != hashlib.sha256(asset.read_bytes()).digest()
                ):
                    raise SystemExit(f"existing GitHub release asset checksum mismatch: {asset.name}")
        return
    command = [
        "gh",
        "release",
        "create",
        tag,
        *(str(asset) for asset in assets),
        "--repo",
        repo,
        "--target",
        source_sha,
        "--title",
        f"Fleet RLM Lite v{version}",
        "--notes-file",
        str(metadata_dir / "release-notes.md"),
    ]
    # The accepted release versions include rc/a/b/dev releases.
    if re.search(r"(?:a|b|rc|dev)\d*", version.split("+", 1)[0]):
        command.append("--prerelease")
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--metadata", type=Path, default=Path("release-metadata"))
    parser.add_argument("--receipt", type=Path, default=Path("canary-results/receipt.json"))
    parser.add_argument("--repo", default="Qredence/fleet-rlm")
    args = parser.parse_args()
    if not os.environ.get("GH_TOKEN"):
        raise SystemExit("GH_TOKEN is required from the restricted release context")
    publish(args.version, args.sha, args.dist, args.metadata, args.receipt, args.repo)


if __name__ == "__main__":
    main()
