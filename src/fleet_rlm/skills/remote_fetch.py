"""SSRF-safe remote fetching for skill installs."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import ParseResult, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fleet_rlm.skills.errors import SkillRemoteFetchError
from fleet_rlm.skills.schemas import SkillInstallPolicy

_GITHUB_REPO_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)(?:/tree/(?P<ref>[^/]+)(?:/(?P<subpath>.+))?)?/?$"
)
_RAW_GITHUB_RE = re.compile(
    r"^https?://raw\.githubusercontent\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<ref>[^/]+)/(?P<subpath>.+)$"
)


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise SkillRemoteFetchError(
            "Remote skill fetch redirects are not allowed.",
            code="skill_remote_fetch_denied",
        )


_FETCH_OPENER = build_opener(_NoRedirectHandler())


def _host_allowed(hostname: str, allowed_hosts: list[str]) -> bool:
    if not allowed_hosts:
        return True
    normalized = hostname.lower().rstrip(".")
    for entry in allowed_hosts:
        candidate = entry.lower().rstrip(".")
        if normalized == candidate or normalized.endswith(f".{candidate}"):
            return True
    return False


def _resolve_host_ips(hostname: str) -> set[str]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SkillRemoteFetchError("Remote skill fetch failed.", code="skill_remote_fetch_failed") from exc
    return {str(info[4][0]) for info in infos}


def _assert_public_host(hostname: str) -> None:
    for address in _resolve_host_ips(hostname):
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise SkillRemoteFetchError("Remote skill fetch target is not allowed.", code="skill_remote_fetch_denied")


def _validate_url(url: str, policy: SkillInstallPolicy) -> ParseResult:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        raise SkillRemoteFetchError("Only HTTPS remote skill URLs are allowed.", code="skill_remote_fetch_denied")
    if not parsed.hostname:
        raise SkillRemoteFetchError("Remote skill URL is invalid.", code="skill_remote_fetch_failed")
    if not _host_allowed(parsed.hostname, policy.allowed_hosts):
        raise SkillRemoteFetchError("Remote skill host is not allowed.", code="skill_remote_fetch_denied")
    _assert_public_host(parsed.hostname)
    return parsed


def fetch_url_bytes(url: str, *, policy: SkillInstallPolicy, max_bytes: int | None = None) -> bytes:
    _validate_url(url, policy)
    limit = max_bytes if max_bytes is not None else policy.max_url_bytes
    request = Request(url, headers={"User-Agent": "fleet-rlm-skill-install/1.0"})
    try:
        with _FETCH_OPENER.open(request, timeout=30) as response:
            chunks: list[bytes] = []
            total = 0
            while True:
                block = response.read(8192)
                if not block:
                    break
                total += len(block)
                if total > limit:
                    raise SkillRemoteFetchError(
                        "Remote skill content exceeds size limit.", code="skill_remote_fetch_denied"
                    )
                chunks.append(block)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise SkillRemoteFetchError("Remote skill fetch failed.", code="skill_remote_fetch_failed") from exc
    return b"".join(chunks)


def fetch_url_text(url: str, *, policy: SkillInstallPolicy, max_bytes: int | None = None) -> str:
    payload = fetch_url_bytes(url, policy=policy, max_bytes=max_bytes)
    return payload.decode("utf-8")


def fetch_tap_index(url: str, *, policy: SkillInstallPolicy) -> dict[str, Any]:
    text = fetch_url_text(url, policy=policy, max_bytes=policy.max_bundle_bytes)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SkillRemoteFetchError("Remote skill tap index is invalid.", code="skill_remote_fetch_failed") from exc
    if not isinstance(payload, dict):
        raise SkillRemoteFetchError("Remote skill tap index is invalid.", code="skill_remote_fetch_failed")
    return payload


def parse_github_repo_url(url: str) -> tuple[str, str, str, str | None]:
    match = _GITHUB_REPO_RE.match(url.strip()) or _RAW_GITHUB_RE.match(url.strip())
    if not match:
        raise SkillRemoteFetchError("GitHub repository URL is invalid.", code="skill_remote_fetch_failed")
    groups = match.groupdict()
    owner = groups["owner"]
    repo = groups["repo"]
    ref = groups.get("ref") or "main"
    subpath = groups.get("subpath")
    return owner, repo, ref, subpath


def fetch_github_skill_markdown(
    *,
    repo_url: str,
    policy: SkillInstallPolicy,
) -> tuple[str, str, str, str, str | None]:
    owner, repo, ref, subpath = parse_github_repo_url(repo_url)
    skill_path = "SKILL.md" if not subpath else f"{subpath.rstrip('/')}/SKILL.md"
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{skill_path}"
    markdown = fetch_url_text(raw_url, policy=policy)
    return markdown, owner, repo, ref, raw_url


def content_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "content_sha256",
    "fetch_github_skill_markdown",
    "fetch_tap_index",
    "fetch_url_bytes",
    "fetch_url_text",
    "parse_github_repo_url",
]
