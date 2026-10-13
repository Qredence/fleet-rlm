"""Bounded HTTP capture and rendered-text evidence storage in the Root Sandbox.

Every connection resolves its host once, rejects any non-global answer and
connects to that checked address (HTTPS keeps the original name for SNI and
certificate checks), so a DNS answer cannot change between check and use.
This is not network containment: provider egress enforcement stays outside
this helper's guarantees.
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import http.client
import ipaddress
import json
import os
import shutil
import socket
import ssl
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

MAX_RESPONSE_BYTES = 2_000_000
MAX_TEXT_BYTES = 1_000_000
TIMEOUT_S = 20


def resolve_public(host: str, port: int) -> str:
    """Resolve ``host`` once and return the checked address to connect to."""
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("local targets are not allowed")
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("non-public targets are not allowed")
    return str(addresses[0][4][0])


def public_url(url: str) -> str:
    """Reject credentials, local names and any non-global resolved address."""
    if len(url) > 4096:
        raise ValueError("URL exceeds limit")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("one public HTTP(S) URL without credentials is required")
    resolve_public(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    return url


class PinnedHTTPConnection(http.client.HTTPConnection):
    """Connect only to the address checked by this connection's own lookup."""

    def connect(self) -> None:
        address = resolve_public(self.host, self.port)
        self.sock = socket.create_connection((address, self.port), self.timeout)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Pinned HTTPS: TLS still verifies the certificate for the original host name."""

    def __init__(self, host: str, port: int | None = None, *, context: ssl.SSLContext | None = None, **kwargs):
        self._pinned_context = context or ssl.create_default_context()
        super().__init__(host, port, context=self._pinned_context, **kwargs)

    def connect(self) -> None:
        address = resolve_public(self.host, self.port)
        sock = socket.create_connection((address, self.port), self.timeout)
        self.sock = self._pinned_context.wrap_socket(sock, server_hostname=self.host)


class PinnedHTTPHandler(HTTPHandler):
    def http_open(self, req):
        return self.do_open(PinnedHTTPConnection, req)


class PinnedHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(PinnedHTTPSConnection, req, context=ssl.create_default_context())


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    if not value.strip().isdecimal():
        raise ValueError("invalid Content-Length; no complete evidence saved")
    return int(value)


def _decode(body: bytes, charset: str | None) -> str:
    try:
        codecs.lookup(charset or "utf-8")
    except LookupError:
        charset = "utf-8"
    return body.decode(charset or "utf-8", errors="replace")


class PublicRedirects(HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, public_url(newurl))


def http_text(url: str) -> tuple[str, str, str, bool]:
    """Fetch bounded HTML or source text, checking redirects before requesting them."""
    from bs4 import BeautifulSoup

    opener = build_opener(ProxyHandler({}), PublicRedirects(), PinnedHTTPHandler(), PinnedHTTPSHandler())
    deadline = time.monotonic() + TIMEOUT_S
    request = Request(public_url(url), headers={"User-Agent": "Fleet-public-page/1"})
    with opener.open(request, timeout=TIMEOUT_S) as response:
        final_url = public_url(response.url)
        content_type = response.headers.get_content_type()
        is_json = content_type == "application/json" or (
            content_type.startswith("application/") and content_type.endswith("+json")
        )
        if content_type not in {"text/html", "text/plain"} and not is_json:
            raise ValueError("only HTML, plain text, or JSON documents are supported")
        expected_size = _content_length(response.headers.get("Content-Length"))
        if expected_size is not None and expected_size > MAX_RESPONSE_BYTES:
            raise ValueError("HTTP response exceeds limit; no complete evidence saved")
        chunks = []
        size = 0
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("HTTP retrieval deadline exceeded")
            # read1 performs one underlying read rather than filling 64 KiB
            # indefinitely from a trickle. One idle-timeout overshoot is possible.
            chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ValueError("HTTP response exceeds limit; no complete evidence saved")
            if time.monotonic() > deadline:
                raise TimeoutError("HTTP retrieval deadline exceeded")
            if not chunk:
                break
            chunks.append(chunk)
        if expected_size is not None and size != expected_size:
            raise ValueError("incomplete HTTP response; no complete evidence saved")
        body = _decode(b"".join(chunks), response.headers.get_content_charset())
        if content_type == "text/plain" or is_json:
            return body, final_url, "", False
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    has_scripts = bool(soup.find("script"))
    for element in soup(["script", "style", "noscript", "template"]):
        element.decompose()
    text = soup.get_text("\n", strip=True)
    needs_browser = has_scripts and (
        len(text) < 200 or any(marker in text.lower() for marker in ("enable javascript", "javascript required"))
    )
    return text, final_url, title, needs_browser


def retrieve(url: str) -> dict[str, object]:
    """Capture static text; return needs_browser guidance for JavaScript shells."""
    scratch = Path(os.environ["FLEET_RUN_SCRATCH"])
    if not scratch.is_absolute() or not scratch.is_dir():
        raise ValueError("FLEET_RUN_SCRATCH must be an existing absolute directory")
    print("browse: HTTP retrieval started", flush=True)
    text, final_url, title, needs_browser = http_text(url)
    return {**save_evidence(text, requested_url=url, final_url=final_url, title=title), "needs_browser": needs_browser}


def save_evidence(
    text: str, *, requested_url: str, final_url: str, title: str = "", rendered: bool = False
) -> dict[str, object]:
    """Save all available extracted text, never label a filtered preview complete."""
    scratch = Path(os.environ["FLEET_RUN_SCRATCH"])
    if not scratch.is_absolute() or not scratch.is_dir():
        raise ValueError("FLEET_RUN_SCRATCH must be an existing absolute directory")
    public_url(requested_url)
    public_url(final_url)
    method = "browser" if rendered else "http"
    encoded = text.encode("utf-8")
    if not text.strip() or len(encoded) > MAX_TEXT_BYTES:
        raise ValueError("empty or oversized text; no complete evidence saved")
    if len(title.encode("utf-8")) > 4096:
        raise ValueError("page title exceeds limit")
    directory = Path(tempfile.mkdtemp(prefix="public-page-", dir=scratch))
    text_path = directory / "page.txt"
    provenance_path = directory / "provenance.json"
    provenance = {
        "requested_url": requested_url,
        "final_url": final_url,
        "title": title,
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
        "method": method,
        "completeness": {
            "extraction_complete": True,
            "scope": "rendered-body-at-retrieval" if method == "browser" else "static-response-text",
            "truncated": False,
            "dynamic_content_complete": False if method == "browser" else None,
        },
        "text_path": str(text_path),
        "provenance_path": str(provenance_path),
    }
    try:
        text_path.write_bytes(encoded)
        provenance_path.write_text(json.dumps(provenance, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(directory)
        raise
    print(f"browse: evidence saved ({len(encoded)} UTF-8 bytes); read stored text in bounded slices", flush=True)
    return provenance


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args()
    result = retrieve(args.url)
    print(json.dumps({"text_path": result["text_path"], "provenance_path": result["provenance_path"]}), flush=True)
