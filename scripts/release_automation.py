"""Prepare and route exact-source releases without invoking live Fleet services."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from scripts.release_notes import extract_notes


def stable_version(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
        raise SystemExit(
            "Automatic releases require canonical stable major.minor.patch; use the manual prerelease workflow."
        )
    return tuple(map(int, value.split(".")))


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, text=True, capture_output=True).stdout.strip()


def intent(parameters: dict, event: str, branch: str, sha: str, *, enabled: bool) -> dict:
    result = dict(parameters)
    if (
        not enabled
        or event != "push"
        or branch != "main"
        or any(result.get(key) for key in ("run_release", "run_pypi_recovery", "run_python314"))
    ):
        return result
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or git("rev-parse", "HEAD") != sha:
        raise SystemExit("Automatic release source must match the exact pipeline checkout.")
    current = tomllib.loads(git("show", f"{sha}:pyproject.toml"))["project"]["version"]
    previous = tomllib.loads(git("show", f"{sha}^1:pyproject.toml"))["project"]["version"]
    if current == previous:
        return result
    if stable_version(current) <= stable_version(previous):
        raise SystemExit("Automatic release version must increase.")
    extract_notes(current, git("show", f"{sha}:CHANGELOG.md"))
    lock = tomllib.loads(git("show", f"{sha}:uv.lock"))
    if [p["version"] for p in lock["package"] if p["name"] == "fleet-rlm"] != [current]:
        raise SystemExit("Release version does not match uv.lock.")
    result.update(run_release=True, release_version=current, release_sha=sha)
    return result


def registry_preflight(version: str) -> None:
    from urllib.parse import quote

    request = urllib.request.Request(f"https://pypi.org/pypi/fleet-rlm/{quote(version, safe='')}/json")
    try:
        with urllib.request.urlopen(request, timeout=20):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return
        raise SystemExit(f"Registry availability check failed: HTTP {exc.code}") from exc
    raise SystemExit("Version already exists on PyPI; inspect publication and use no-upload recovery.")


def prepare(version: str) -> None:
    target = stable_version(version)
    paths = [Path(name) for name in ("pyproject.toml", "uv.lock", "CHANGELOG.md", "openapi.yaml")]
    original = {path: path.read_bytes() for path in paths}
    project = tomllib.loads(paths[0].read_text())
    old = project["project"]["version"]
    if target <= stable_version(old):
        raise SystemExit("Prepared version must increase.")
    from scripts.github_release import gh_json

    previous_release = gh_json(f"repos/Qredence/fleet-rlm/releases/tags/v{old}", missing_ok=True)
    if previous_release is None or previous_release.get("draft") or previous_release.get("prerelease"):
        raise SystemExit("Previous stable release is not complete; inspect publication and recovery first.")
    deployments = gh_json("repos/Qredence/fleet-rlm/deployments?environment=pypi&per_page=100")
    matching = [item for item in deployments if item.get("payload", {}).get("version") == old]
    if matching:
        statuses = gh_json(f"repos/Qredence/fleet-rlm/deployments/{matching[0]['id']}/statuses")
        if not statuses or statuses[0].get("state") != "success":
            raise SystemExit("Previous PyPI deployment is unresolved; complete recovery before preparing a release.")
    notes = paths[2].read_text()
    if re.search(rf"^## \[{re.escape(version)}\]", notes, re.MULTILINE):
        raise SystemExit("Target changelog section already exists.")
    notes = notes.replace("<!-- fleet-release:start -->", "").replace("<!-- fleet-release:end -->", "")
    notes = re.sub(r"<!-- sha256:[0-9a-f]{64} -->\n?", "", notes)
    heading = f"## [Unreleased]\n\n## [{version}] - {datetime.date.today().isoformat()}"
    notes, count = re.subn(r"^## \[Unreleased\]", lambda _: heading, notes, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit("Missing Unreleased section.")
    extract_notes(version, notes)
    lock = tomllib.loads(paths[1].read_text())
    if [p["version"] for p in lock["package"] if p["name"] == "fleet-rlm"] != [old]:
        raise SystemExit("Existing lock version is inconsistent.")
    project_text, count = re.subn(
        r'(?m)^(version\s*=\s*)"' + re.escape(old) + r'"',
        lambda match: f'{match[1]}"{version}"',
        paths[0].read_text(),
        count=1,
    )
    if count != 1:
        raise SystemExit("Could not update project version.")
    lock_text = (
        paths[1]
        .read_text()
        .replace(
            f'name = "fleet-rlm"\nversion = "{old}"',
            f'name = "fleet-rlm"\nversion = "{version}"',
            1,
        )
    )
    updated_lock = tomllib.loads(lock_text)
    for package in updated_lock["package"]:
        if package["name"] == "fleet-rlm":
            package["version"] = old
    if updated_lock != lock:
        raise SystemExit("Preparation would change dependencies.")
    try:
        paths[0].write_text(project_text)
        paths[1].write_text(lock_text)
        paths[2].write_text(notes)
        subprocess.run(["uv", "run", "--locked", "python", "scripts/openapi_contract.py", "generate"], check=True)
    except BaseException:
        for path, data in original.items():
            path.write_bytes(data)
        raise


def github_api(path: str, body: dict) -> dict:
    result = subprocess.run(
        ["gh", "api", "--method", "POST", path, "--input", "-"],
        input=json.dumps(body),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise SystemExit(f"GitHub deployment request failed: {path}")
    return json.loads(result.stdout)


def deployment_start(version: str, sha: str, output: Path) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise SystemExit("Deployment requires full source SHA.")
    deployment = github_api(
        "repos/Qredence/fleet-rlm/deployments",
        {
            "ref": sha,
            "environment": "pypi",
            "auto_merge": False,
            "required_contexts": [],
            "production_environment": True,
            "description": f"Publish fleet-rlm {version}",
            "payload": {"version": version, "source_sha": sha},
        },
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"id": deployment["id"], "version": version, "sha": sha}))
    deployment_status(output, "in_progress", "publication")


def deployment_status(path: Path, state: str, phase: str) -> None:
    deployment = json.loads(path.read_text())
    if not isinstance(deployment["id"], int):
        raise SystemExit("Invalid deployment receipt.")
    github_api(
        f"repos/Qredence/fleet-rlm/deployments/{deployment['id']}/statuses",
        {
            "state": state,
            "description": phase[:140],
            "log_url": os.environ.get("CIRCLE_BUILD_URL", ""),
            "environment_url": f"https://pypi.org/project/fleet-rlm/{deployment['version']}/",
            "auto_inactive": state == "success",
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    route = commands.add_parser("intent")
    for name in ("event", "branch", "sha", "parameters", "output"):
        route.add_argument(f"--{name}", required=True)
    route.add_argument("--enabled", action="store_true")
    for name in ("prepare", "registry-preflight"):
        commands.add_parser(name).add_argument("--version", required=True)
    start = commands.add_parser("deployment-start")
    for name in ("version", "sha", "output"):
        start.add_argument(f"--{name}", required=True)
    status = commands.add_parser("deployment-status")
    status.add_argument("--deployment", required=True)
    status.add_argument("--state", required=True, choices=["success", "failure", "error", "in_progress"])
    status.add_argument("--phase", required=True)
    args = parser.parse_args()
    if args.command == "intent":
        output = intent(
            json.loads(Path(args.parameters).read_text()), args.event, args.branch, args.sha, enabled=args.enabled
        )
        Path(args.output).write_text(json.dumps(output))
    elif args.command == "prepare":
        prepare(args.version)
    elif args.command == "registry-preflight":
        registry_preflight(args.version)
    elif args.command == "deployment-start":
        deployment_start(args.version, args.sha, Path(args.output))
    else:
        deployment_status(Path(args.deployment), args.state, args.phase)


if __name__ == "__main__":
    main()
