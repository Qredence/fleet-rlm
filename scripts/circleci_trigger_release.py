"""Trigger and await the repository's GitHub Actions PyPI release workflow."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        status = getattr(exc, "code", "unavailable")
        raise ReleaseTriggerError(f"GitHub API request failed with HTTP {status}") from exc

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


def _find_run(
    *,
    api_root: str,
    headers: dict[str, str],
    ref: str,
    started_at: float,
    deadline: float,
) -> dict[str, Any]:
    url = f"{api_root}/actions/workflows/release.yml/runs?event=workflow_dispatch&branch={ref}&per_page=20"
    while time.time() < deadline:
        _, payload = _request_json(url, headers=headers)
        candidates = []
        for run in payload.get("workflow_runs", []):
            if not isinstance(run, dict) or "created_at" not in run:
                continue
            created_at = datetime.fromisoformat(str(run["created_at"]).replace("Z", "+00:00"))
            if created_at.timestamp() >= started_at - 10:
                candidates.append(run)
        if candidates:
            return max(candidates, key=lambda run: int(run["id"]))
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
    args = parser.parse_args(argv)

    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        print("GITHUB_TOKEN must be configured in the CircleCI project or context", file=sys.stderr)
        return 1

    try:
        repository = _repository(args.repository)
        api_root = f"https://api.github.com/repos/{repository}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "fleet-rlm-circleci-pypi-deploy",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        started_at = time.time()
        status, _ = _request_json(
            f"{api_root}/actions/workflows/release.yml/dispatches",
            headers=headers,
            method="POST",
            payload={"ref": args.ref, "inputs": {"version": args.version}},
        )
        if status not in {200, 204}:
            raise ReleaseTriggerError(f"GitHub release dispatch returned HTTP {status}")

        run = _find_run(
            api_root=api_root,
            headers=headers,
            ref=args.ref,
            started_at=started_at,
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
