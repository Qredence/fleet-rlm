"""Trigger and await the repository's GitHub Actions PyPI release workflow."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


class ReleaseTriggerError(RuntimeError):
    """Raised when the GitHub release workflow cannot be dispatched or completed."""


def _request_json(
    url: str,
    *,
    headers: dict[str, str],
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
            decoded = json.loads(body) if body else {}
    except HTTPError as exc:
        if exc.code == 404:
            return 404, {}
        raise ReleaseTriggerError(f"JSON API request failed with HTTP {exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        status = getattr(exc, "code", "unavailable")
        raise ReleaseTriggerError(f"JSON API request failed with HTTP {status}") from exc

    if not isinstance(decoded, dict):
        raise ReleaseTriggerError("GitHub API returned an unexpected response")
    return response.status, decoded


def _repository(explicit: str | None) -> str:
    if explicit:
        return explicit
    owner = os.getenv("CIRCLE_PROJECT_USERNAME", "").strip()
    name = os.getenv("CIRCLE_PROJECT_REPONAME", "").strip()
    if not owner or not name:
        raise ReleaseTriggerError("CircleCI repository metadata is unavailable")
    return f"{owner}/{name}"


def _existing_release_url(repository: str, requested_version: str) -> str | None:
    """Verify an already-published latest version instead of dispatching it twice."""
    version = requested_version.strip().removeprefix("v")
    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise ReleaseTriggerError("release version must be X.Y.Z with an optional leading v")

    pypi_headers = {"Accept": "application/json", "User-Agent": "fleet-rlm-circleci-pypi-deploy"}
    github_headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "fleet-rlm-circleci-pypi-deploy",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    pypi_root = "https://pypi.org/pypi/fleet-rlm"
    api_root = f"https://api.github.com/repos/{repository}"

    _, project = _request_json(f"{pypi_root}/json", headers=pypi_headers)
    latest_version = project.get("info", {}).get("version")
    version_status, published = _request_json(f"{pypi_root}/{version}/json", headers=pypi_headers)
    if version_status == 404:
        return None
    if latest_version != version:
        raise ReleaseTriggerError(
            f"PyPI already contains {version}, but latest is {latest_version!r}; refusing to redeploy an older release"
        )

    filenames = {
        f"fleet_rlm-{version}-py3-none-any.whl",
        f"fleet_rlm-{version}.tar.gz",
    }
    pypi_digests = {
        item.get("filename"): item.get("digests", {}).get("sha256")
        for item in published.get("urls", [])
        if isinstance(item, dict)
    }
    if any(not isinstance(pypi_digests.get(filename), str) for filename in filenames):
        raise ReleaseTriggerError(f"PyPI release {version} is missing the expected wheel or source distribution")

    release_status, release = _request_json(f"{api_root}/releases/tags/v{version}", headers=github_headers)
    if release_status == 404:
        raise ReleaseTriggerError(f"PyPI release {version} exists, but GitHub release v{version} was not found")
    github_digests = {
        asset.get("name"): asset.get("digest") for asset in release.get("assets", []) if isinstance(asset, dict)
    }
    for filename in filenames:
        if github_digests.get(filename) != f"sha256:{pypi_digests[filename]}":
            raise ReleaseTriggerError(f"GitHub release asset {filename} does not match its PyPI SHA-256")

    release_url = release.get("html_url")
    if not isinstance(release_url, str) or not release_url:
        raise ReleaseTriggerError(f"GitHub release v{version} has no release URL")
    return release_url


def _find_run(
    *,
    api_root: str,
    headers: dict[str, str],
    ref: str,
    expected_sha: str,
    display_title: str,
    deadline: float,
) -> dict[str, Any]:
    query = urlencode({"event": "workflow_dispatch", "branch": ref, "per_page": 100})
    url = f"{api_root}/actions/workflows/release.yml/runs?{query}"
    while time.time() < deadline:
        _, payload = _request_json(url, headers=headers)
        for run in payload.get("workflow_runs", []):
            if not isinstance(run, dict) or run.get("display_title") != display_title:
                continue
            if run.get("head_sha") != expected_sha:
                raise ReleaseTriggerError("dispatched GitHub release resolved to a different source commit")
            return run
        time.sleep(10)
    raise ReleaseTriggerError("timed out waiting for the dispatched GitHub release run")


def _wait_for_run(*, api_root: str, headers: dict[str, str], run: dict[str, Any], deadline: float) -> None:
    url = f"{api_root}/actions/runs/{run['id']}"
    while time.time() < deadline:
        _, payload = _request_json(url, headers=headers)
        status = payload.get("status")
        conclusion = payload.get("conclusion")
        print(f"GitHub release status: {status} ({conclusion or 'pending'})", flush=True)
        if status == "completed":
            if conclusion != "success":
                raise ReleaseTriggerError(f"GitHub release completed with conclusion: {conclusion}")
            return
        time.sleep(20)
    raise ReleaseTriggerError("timed out waiting for the GitHub release run to complete")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="PyPI version accepted by release.yml")
    parser.add_argument("--repository", help="GitHub owner/name; defaults to CircleCI metadata")
    parser.add_argument("--ref", default="main", help="Git ref used for workflow_dispatch")
    parser.add_argument("--expected-sha", help="Full commit SHA validated by CircleCI")
    args = parser.parse_args(argv)

    try:
        if args.expected_sha is not None and re.fullmatch(r"[0-9a-f]{40}", args.expected_sha) is None:
            raise ReleaseTriggerError("expected SHA must be a full lowercase 40-character commit SHA")
        repository = _repository(args.repository)
        api_root = f"https://api.github.com/repos/{repository}"
        existing_release_url = _existing_release_url(repository, args.version)
        if existing_release_url is not None:
            print(f"Existing PyPI release verified: {existing_release_url}", flush=True)
            return 0

        token = os.getenv("GITHUB_TOKEN", "").strip()
        if not token:
            print("GITHUB_TOKEN must be configured in the CircleCI project or context", file=sys.stderr)
            return 1

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "fleet-rlm-circleci-pypi-deploy",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        ref_status, commit = _request_json(f"{api_root}/commits/{quote(args.ref, safe='')}", headers=headers)
        resolved_sha = commit.get("sha")
        if (
            ref_status != 200
            or not isinstance(resolved_sha, str)
            or re.fullmatch(r"[0-9a-f]{40}", resolved_sha) is None
        ):
            raise ReleaseTriggerError("could not resolve the release ref to a full commit SHA")
        expected_sha = args.expected_sha or resolved_sha
        if resolved_sha != expected_sha:
            raise ReleaseTriggerError("release ref does not match the source commit validated by CircleCI")
        dispatch_id = uuid4().hex
        display_title = f"Release {args.version} [{dispatch_id}]"
        started_at = time.time()
        status, _ = _request_json(
            f"{api_root}/actions/workflows/release.yml/dispatches",
            headers=headers,
            method="POST",
            payload={
                "ref": args.ref,
                "inputs": {"version": args.version, "expected_sha": expected_sha, "dispatch_id": dispatch_id},
            },
        )
        if status not in {200, 204}:
            raise ReleaseTriggerError(f"GitHub release dispatch returned HTTP {status}")

        run = _find_run(
            api_root=api_root,
            headers=headers,
            ref=args.ref,
            expected_sha=expected_sha,
            display_title=display_title,
            deadline=time.time() + 300,
        )
        print(f"GitHub release run: {run['html_url']}", flush=True)
        _wait_for_run(
            api_root=api_root,
            headers=headers,
            run=run,
            deadline=started_at + 2700,
        )
    except ReleaseTriggerError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
