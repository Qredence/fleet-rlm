"""Bounded, read-only public-page evidence retrieval inside the Root Sandbox.

URL/DNS checks are best effort, not network containment (DNS rebinding and
provider egress enforcement remain outside this helper's guarantees).
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shutil
import socket
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_RESPONSE_BYTES = 2_000_000
MAX_TEXT_BYTES = 1_000_000
TIMEOUT_S = 20


def public_url(url: str) -> str:
    """Reject credentials, local names and any non-global resolved address."""
    if len(url) > 4096:
        raise ValueError("URL exceeds limit")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("one public HTTP(S) URL without credentials is required")
    host = parts.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("local targets are not allowed")
    addresses = socket.getaddrinfo(
        host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM
    )
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("non-public targets are not allowed")
    return url


class PublicRedirects(HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, public_url(newurl))


def http_text(url: str) -> tuple[str, str, str, bool]:
    """Fetch bounded HTML, checking redirects before requesting them."""
    from bs4 import BeautifulSoup

    opener = build_opener(ProxyHandler({}), PublicRedirects())
    deadline = time.monotonic() + TIMEOUT_S
    request = Request(public_url(url), headers={"User-Agent": "Fleet-public-page/1"})
    with opener.open(request, timeout=TIMEOUT_S) as response:
        final_url = public_url(response.url)
        if response.headers.get_content_type() not in {"text/html", "text/plain"}:
            raise ValueError("only HTML or plain text pages are supported")
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
        body = b"".join(chunks).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        if response.headers.get_content_type() == "text/plain":
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


def browser_text(url: str) -> tuple[str, str, str]:
    """Render one page in a fresh context; no profiles, clicks or downloads."""
    from playwright.sync_api import Error, sync_playwright

    public_url(url)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True, timeout=TIMEOUT_S * 1000)
        try:
            context = browser.new_context(accept_downloads=False, service_workers="block")
            try:
                requests = 0

                def route_request(route):
                    nonlocal requests
                    requests += 1
                    request = route.request
                    try:
                        public_url(request.url)
                    except (ValueError, OSError):
                        route.abort()
                        return
                    if (
                        requests > 100
                        or request.method not in {"GET", "HEAD"}
                        or request.resource_type in {"image", "media", "font"}
                    ):
                        route.abort()
                        return
                    # Chromium follows redirect chains without invoking the
                    # routing handler again. Never hand it a redirect response:
                    # the HTTP-first stage resolves checked public redirects.
                    try:
                        response = route.fetch(max_redirects=0, timeout=TIMEOUT_S * 1000)
                    except Error:
                        route.abort()
                        return
                    try:
                        if 300 <= response.status < 400:
                            route.abort()
                        else:
                            route.fulfill(response=response)
                    finally:
                        response.dispose()

                context.route("**/*", route_request)
                context.route_web_socket("**/*", lambda websocket: websocket.close())
                page = context.new_page()
                page.set_default_timeout(TIMEOUT_S * 1000)
                print("browse: browser navigation started", flush=True)
                response = page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_S * 1000)
                if response is None or response.status >= 400:
                    raise ValueError("browser navigation did not return a successful page")
                # A bounded settle window, not a claim that all dynamic content loaded.
                page.wait_for_timeout(1000)
                final_url = public_url(page.url)
                # Playwright evaluate has no execution timeout. Chromium's
                # Runtime.evaluate bounds this extraction even if a getter stalls.
                session = context.new_cdp_session(page)
                try:
                    extracted = session.send(
                        "Runtime.evaluate",
                        {
                            "expression": """(() => {
                        const text = document.body ? document.body.innerText : "";
                        const title = document.title;
                        const limit = MAX_BYTES;
                        if (text.length > limit || new TextEncoder().encode(text).length > limit)
                            return null;
                        if (title.length > 4096 || new TextEncoder().encode(title).length > 4096)
                            return null;
                        return {text, title};
                    })()""".replace("MAX_BYTES", str(MAX_TEXT_BYTES)),
                            "returnByValue": True,
                            "timeout": 5000,
                        },
                    )
                except Error as exc:
                    raise ValueError("rendered text extraction failed or timed out") from exc
                finally:
                    session.detach()
                if "exceptionDetails" in extracted:
                    raise ValueError("rendered text extraction failed")
                value = extracted["result"].get("value")
                if value is None:
                    raise ValueError("rendered text exceeds limit; no complete evidence saved")
                return value["text"], final_url, value["title"]
            finally:
                context.close()
        finally:
            browser.close()


def retrieve(url: str, *, render: bool = False) -> dict[str, object]:
    """HTTP first; render JS shells or explicitly requested dynamic evidence."""
    scratch = Path(os.environ["FLEET_RUN_SCRATCH"])
    if not scratch.is_absolute() or not scratch.is_dir():
        raise ValueError("FLEET_RUN_SCRATCH must be an existing absolute directory")
    print("browse: HTTP retrieval started", flush=True)
    text, final_url, title, needs_browser = http_text(url)
    method = "http"
    if render or needs_browser:
        print("browse: browser fallback selected", flush=True)
        text, final_url, title = browser_text(final_url)
        method = "browser"
    encoded = text.encode("utf-8")
    if not text.strip() or len(encoded) > MAX_TEXT_BYTES:
        raise ValueError("empty or oversized text; no complete evidence saved")
    if len(title.encode("utf-8")) > 4096:
        raise ValueError("page title exceeds limit")
    directory = Path(tempfile.mkdtemp(prefix="public-page-", dir=scratch))
    text_path = directory / "page.txt"
    provenance_path = directory / "provenance.json"
    provenance = {
        "requested_url": url,
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
    parser.add_argument("--render", action="store_true", help="Render after HTTP for known dynamic pages")
    args = parser.parse_args()
    result = retrieve(args.url, render=args.render)
    print(json.dumps({"text_path": result["text_path"], "provenance_path": result["provenance_path"]}), flush=True)
