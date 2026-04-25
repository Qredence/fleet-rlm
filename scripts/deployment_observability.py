"""Build release observability summaries and emit optional PostHog deploy markers."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class SummaryLink:
    label: str
    url: str
    description: str


@dataclass(frozen=True)
class AnnotationResult:
    status: str
    detail: str


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _is_public_http_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False

    host = parsed.hostname.lower()
    if host in {"localhost", "0.0.0.0"}:
        return False

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return not host.endswith(".local")

    return not (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _github_run_url() -> str | None:
    server = _clean(os.getenv("GITHUB_SERVER_URL"))
    repository = _clean(os.getenv("GITHUB_REPOSITORY"))
    run_id = _clean(os.getenv("GITHUB_RUN_ID"))
    if not (server and repository and run_id):
        return None
    return f"{server}/{repository}/actions/runs/{run_id}"


def _github_release_url(release_tag: str | None) -> str | None:
    server = _clean(os.getenv("GITHUB_SERVER_URL"))
    repository = _clean(os.getenv("GITHUB_REPOSITORY"))
    if not (server and repository and release_tag):
        return None
    return f"{server}/{repository}/releases/tag/{release_tag}"


def _release_links(args: argparse.Namespace) -> list[SummaryLink]:
    links: list[SummaryLink] = []
    if args.package_url:
        links.append(
            SummaryLink(
                label="Package",
                url=args.package_url,
                description=f"{args.package_name} package page",
            )
        )

    release_url = args.release_url or _github_release_url(args.release_tag)
    if release_url:
        links.append(
            SummaryLink(
                label="GitHub release",
                url=release_url,
                description="Release notes and uploaded artifacts",
            )
        )

    run_url = args.run_url or _github_run_url()
    if run_url:
        links.append(
            SummaryLink(
                label="Workflow run",
                url=run_url,
                description="Deployment execution logs and artifacts",
            )
        )

    return links


def _observability_links(args: argparse.Namespace) -> list[SummaryLink]:
    links: list[SummaryLink] = []

    configured_links = [
        (
            "Metrics dashboard",
            _clean(os.getenv("DEPLOYMENT_METRICS_URL")),
            "Primary metrics and latency dashboards",
        ),
        (
            "Alert dashboard",
            _clean(os.getenv("DEPLOYMENT_ALERTS_URL")),
            "Active alerts and on-call triage surface",
        ),
        (
            "PostHog dashboard",
            _clean(os.getenv("DEPLOYMENT_POSTHOG_DASHBOARD_URL"))
            or _clean(os.getenv("POSTHOG_HOST")),
            "Product analytics and deploy marker stream",
        ),
        (
            "Healthcheck",
            _clean(os.getenv("DEPLOYMENT_HEALTHCHECK_URL")) or args.healthcheck_url,
            "Live health endpoint for the deployed service",
        ),
    ]

    for label, url, description in configured_links:
        if _is_public_http_url(url):
            links.append(SummaryLink(label=label, url=url, description=description))

    return links


def emit_posthog_deploy_marker(
    *,
    environment: str,
    package_name: str,
    package_url: str | None,
    release_tag: str | None,
    release_version: str | None,
) -> AnnotationResult:
    """Emit a release deployment marker to PostHog when credentials are configured."""
    api_key = _clean(os.getenv("POSTHOG_API_KEY"))
    host = _clean(os.getenv("POSTHOG_HOST")) or "https://eu.i.posthog.com"

    if not api_key:
        return AnnotationResult(
            status="skipped",
            detail="POSTHOG_API_KEY is not configured for release workflows.",
        )

    if not _is_public_http_url(host):
        return AnnotationResult(
            status="skipped",
            detail="POSTHOG_HOST is missing or points at a non-public endpoint.",
        )

    payload = {
        "api_key": api_key,
        "event": "fleet_rlm_release_deployed",
        "distinct_id": os.getenv("POSTHOG_DISTINCT_ID", "fleet-rlm-release"),
        "properties": {
            "environment": environment,
            "package_name": package_name,
            "package_url": package_url,
            "release_tag": release_tag,
            "release_version": release_version,
            "github_repository": _clean(os.getenv("GITHUB_REPOSITORY")),
            "github_run_url": _github_run_url(),
            "github_release_url": _github_release_url(release_tag),
            "git_sha": _clean(os.getenv("GITHUB_SHA")),
            "deployed_at": datetime.now(UTC).isoformat(),
            "source": "github-actions",
        },
    }

    request = Request(
        url=f"{host.rstrip('/')}/capture/",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=10) as response:
            status_code = getattr(response, "status", None) or response.getcode()
    except Exception as exc:
        return AnnotationResult(
            status="failed",
            detail=f"PostHog capture request failed: {exc}",
        )

    if status_code >= 400:
        return AnnotationResult(
            status="failed",
            detail=f"PostHog capture endpoint returned HTTP {status_code}.",
        )

    return AnnotationResult(
        status="sent",
        detail="PostHog deploy marker emitted successfully.",
    )


def build_summary(
    *,
    args: argparse.Namespace,
    posthog_result: AnnotationResult | None,
) -> str:
    """Render the GitHub Actions Markdown step summary for a release deploy."""
    lines = [
        f"## Deployment observability ({args.environment})",
        "",
        "### Release surfaces",
    ]

    release_links = _release_links(args)
    if release_links:
        lines.extend(
            [
                f"- [{link.label}]({link.url}) — {link.description}"
                for link in release_links
            ]
        )
    else:
        lines.append("- No release links were available for this run.")

    lines.extend(["", "### Where to watch deploy impact"])
    observability_links = _observability_links(args)
    if observability_links:
        lines.extend(
            [
                f"- [{link.label}]({link.url}) — {link.description}"
                for link in observability_links
            ]
        )
    else:
        lines.extend(
            [
                "- Configure one or more of `DEPLOYMENT_METRICS_URL`, "
                "`DEPLOYMENT_ALERTS_URL`, `DEPLOYMENT_POSTHOG_DASHBOARD_URL`, or "
                "`DEPLOYMENT_HEALTHCHECK_URL` in GitHub Actions variables to link this "
                "summary to live deploy-impact surfaces.",
            ]
        )

    lines.extend(["", "### Monitoring annotations"])
    if posthog_result is None:
        lines.append("- PostHog deploy marker was not requested for this run.")
    else:
        lines.append(
            f"- PostHog deploy marker: **{posthog_result.status}** — "
            f"{posthog_result.detail}"
        )

    if args.smoke_checks:
        lines.extend(["", "### Smoke checks"])
        lines.extend([f"- {item}" for item in args.smoke_checks])

    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish deployment observability links and annotations for releases."
    )
    parser.add_argument("--environment", required=True)
    parser.add_argument("--package-name", default="fleet-rlm")
    parser.add_argument("--package-url")
    parser.add_argument("--release-tag")
    parser.add_argument("--release-version")
    parser.add_argument("--release-url")
    parser.add_argument("--run-url")
    parser.add_argument("--healthcheck-url")
    parser.add_argument("--annotate-posthog", action="store_true")
    parser.add_argument("--step-summary")
    parser.add_argument(
        "--smoke-check",
        action="append",
        dest="smoke_checks",
        default=[],
        help="Human-readable smoke-check result to include in the summary.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the deploy observability summary CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    posthog_result = None
    if args.annotate_posthog:
        posthog_result = emit_posthog_deploy_marker(
            environment=args.environment,
            package_name=args.package_name,
            package_url=args.package_url,
            release_tag=args.release_tag,
            release_version=args.release_version,
        )

    summary = build_summary(args=args, posthog_result=posthog_result)
    sys.stdout.write(summary)

    if args.step_summary:
        Path(args.step_summary).write_text(summary, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
