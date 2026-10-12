"""Bundled page helper contracts; real Chromium is an explicit optional lane."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import threading
import time
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

RESOURCE = Path("src/fleet_rlm/skills/bundled/long-context/scripts/browse_page.py")


@pytest.fixture
def helper():
    spec = importlib.util.spec_from_file_location("browse_page", RESOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "url", ["file:///tmp/a", "ftp://example.org", "http://localhost", "http://user:pass@example.org"]
)
def test_public_url_rejects_non_public_shapes(helper, url):
    with pytest.raises(ValueError):
        helper.public_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1"])
def test_public_url_rejects_local_dns_and_redirects(helper, monkeypatch, address):
    monkeypatch.setattr(helper.socket, "getaddrinfo", lambda *_a, **_k: [(0, 0, 0, "", (address, 80))])
    with pytest.raises(ValueError):
        helper.public_url("https://example.org")
    with pytest.raises(ValueError):
        helper.PublicRedirects().redirect_request(None, None, 302, "", {}, "https://example.org")


def test_http_extraction_is_bounded_and_detects_js_shell(helper, monkeypatch):
    pytest.importorskip("bs4", reason="snapshot-only dependency; run with --with beautifulsoup4==4.15.0")
    monkeypatch.setattr(helper, "public_url", lambda url: url)

    def response(body):
        stream = io.BytesIO(body)
        stream.url = "https://example.org/final"
        stream.headers = Message()
        stream.headers["Content-Type"] = "text/html; charset=utf-8"
        return SimpleNamespace(open=lambda *_a, **_k: stream)

    monkeypatch.setattr(
        helper,
        "build_opener",
        lambda *_a: response(
            b"<title>Example</title><body>Loading<script>document.body.textContent='rendered'</script></body>"
        ),
    )
    text, final, title, needs_browser = helper.http_text("https://example.org")
    assert (text, final, title, needs_browser) == ("Example\nLoading", "https://example.org/final", "Example", True)
    monkeypatch.setattr(helper, "MAX_RESPONSE_BYTES", 10)
    monkeypatch.setattr(helper, "build_opener", lambda *_a: response(b"x" * 11))
    with pytest.raises(ValueError, match="exceeds limit"):
        helper.http_text("https://example.org")


def test_evidence_provenance_and_small_milestones(helper, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FLEET_RUN_SCRATCH", str(tmp_path))
    text = "verified evidence\n" * 100
    monkeypatch.setattr(helper, "http_text", lambda url: (text, url, "Example", False))
    receipt = helper.retrieve("https://example.org")
    stored = Path(receipt["text_path"]).read_bytes()
    assert stored == text.encode()
    assert receipt["sha256"] == hashlib.sha256(stored).hexdigest()
    assert receipt["bytes"] == len(stored)
    assert receipt["requested_url"] == receipt["final_url"] == "https://example.org"
    assert receipt["retrieved_at_utc"].endswith("+00:00")
    assert json.loads(Path(receipt["provenance_path"]).read_text()) == receipt
    assert receipt["completeness"]["truncated"] is False
    assert text not in capsys.readouterr().out
    monkeypatch.setattr(helper, "MAX_TEXT_BYTES", 10)
    before = list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="oversized"):
        helper.retrieve("https://example.org")
    assert list(tmp_path.iterdir()) == before


@pytest.mark.parametrize("failed_path", ["page.txt", "provenance.json"])
def test_failed_evidence_write_removes_only_its_new_directory(helper, monkeypatch, tmp_path, capsys, failed_path):
    monkeypatch.setenv("FLEET_RUN_SCRATCH", str(tmp_path))
    monkeypatch.setattr(helper, "http_text", lambda url: ("Verified evidence", url, "Example", False))
    existing = tmp_path / "existing.txt"
    existing.write_text("keep")
    write_bytes = Path.write_bytes
    write_text = Path.write_text

    def failing_bytes(path, content):
        result = write_bytes(path, content)
        if path.name == failed_path:
            raise OSError("write failed after partial output")
        return result

    def failing_text(path, content, **kwargs):
        result = write_text(path, content, **kwargs)
        if path.name == failed_path:
            raise OSError("write failed after partial output")
        return result

    monkeypatch.setattr(Path, "write_bytes", failing_bytes)
    monkeypatch.setattr(Path, "write_text", failing_text)
    with pytest.raises(OSError, match="write failed"):
        helper.retrieve("https://example.org")
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_text() == "keep"
    assert "evidence saved" not in capsys.readouterr().out


def test_http_body_deadline_stops_real_slow_trickle(helper, monkeypatch):
    pytest.importorskip("bs4", reason="snapshot-only dependency; run with --with beautifulsoup4==4.15.0")
    monkeypatch.setattr(helper, "public_url", lambda url: url)
    monkeypatch.setattr(helper, "TIMEOUT_S", 0.3)
    stopped = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    if stopped.wait(0.03):
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            helper.http_text(f"http://127.0.0.1:{server.server_port}/")
        assert time.monotonic() - started < 1.5
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_browser_fallback_retains_original_url(helper, monkeypatch, tmp_path):
    monkeypatch.setenv("FLEET_RUN_SCRATCH", str(tmp_path))
    monkeypatch.setattr(helper, "http_text", lambda url: ("Loading", url + "/final", "Before", True))

    def render(url):
        assert url == "https://example.org/final"
        return "Rendered evidence", url, "After"

    monkeypatch.setattr(helper, "browser_text", render)
    receipt = helper.retrieve("https://example.org")
    assert receipt["requested_url"] == "https://example.org"
    assert receipt["method"] == "browser"
    assert receipt["completeness"]["dynamic_content_complete"] is False


@pytest.mark.parametrize("resource_type", ["document", "script"])
@pytest.mark.parametrize("status", [200, 301, 302, 303, 307, 308])
def test_browser_never_hands_redirects_to_chromium(helper, monkeypatch, resource_type, status):
    from playwright import sync_api

    monkeypatch.setattr(helper.socket, "getaddrinfo", lambda *_a, **_k: [(0, 0, 0, "", ("93.184.216.34", 443))])
    manager = MagicMock()
    browser = manager.__enter__.return_value.chromium.launch.return_value
    context = browser.new_context.return_value
    page = context.new_page.return_value
    page.url = "https://example.org"
    context.new_cdp_session.return_value.send.return_value = {
        "result": {"value": {"text": "Public evidence", "title": "Example"}}
    }
    response = MagicMock(status=status)
    response.headers = {"location": "http://127.0.0.1/private"}
    route = MagicMock()
    route.request = SimpleNamespace(url="https://example.org/resource", method="GET", resource_type=resource_type)
    route.fetch.return_value = response

    def navigate(*_args, **_kwargs):
        context.route.call_args.args[1](route)
        return SimpleNamespace(status=200)

    page.goto.side_effect = navigate
    monkeypatch.setattr(sync_api, "sync_playwright", lambda: manager)
    assert helper.browser_text("https://example.org") == ("Public evidence", "https://example.org", "Example")
    route.fetch.assert_called_once_with(max_redirects=0, timeout=helper.TIMEOUT_S * 1000)
    route.continue_.assert_not_called()
    if status == 200:
        route.fulfill.assert_called_once_with(response=response)
        route.abort.assert_not_called()
    else:
        route.abort.assert_called_once_with()
        route.fulfill.assert_not_called()
    response.dispose.assert_called_once_with()
    context.close.assert_called_once_with()
    browser.close.assert_called_once_with()


@pytest.mark.skipif(os.environ.get("FLEET_TEST_BROWSER") != "1", reason="opt-in actual Chromium fixture")
@pytest.mark.parametrize("resource_type", ["document", "script"])
def test_actual_chromium_blocks_redirects_before_private_request(helper, monkeypatch, resource_type):
    from playwright.sync_api import Error

    private_requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/private")
                self.end_headers()
            else:
                if self.path == "/private":
                    private_requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b'<body>Public evidence<script src="/redirect"></script></body>')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"

    def fixture_url(target):
        if target not in {url + "/", url + "/redirect"}:
            raise ValueError("private fixture target")
        return target

    monkeypatch.setattr(helper, "public_url", fixture_url)
    try:
        if resource_type == "document":
            with pytest.raises(Error):
                helper.browser_text(url + "/redirect")
        else:
            assert helper.browser_text(url + "/")[0] == "Public evidence"
        assert private_requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.skipif(os.environ.get("FLEET_TEST_BROWSER") != "1", reason="opt-in actual Chromium fixture")
def test_actual_chromium_renders_js_and_closes_on_oversize(helper, monkeypatch, tmp_path):
    """Run with pinned Playwright and matching Chromium; no public network."""
    import importlib.metadata

    assert importlib.metadata.version("playwright") == "1.63.0"
    monkeypatch.setenv("FLEET_RUN_SCRATCH", str(tmp_path))
    # Local fixture exception exists only in this test, never in shipped code.
    monkeypatch.setattr(helper, "public_url", lambda url: url)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if self.path == "/stall":
                self.wfile.write(
                    b"<body>Loading<script>Object.defineProperty(document.body, 'innerText',"
                    b"{get(){while(true){}}})</script></body>"
                )
                return
            self.wfile.write(
                b"<title>JS fixture</title><body>Loading<script>"
                b"document.body.textContent='Real Chromium evidence'</script></body>"
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        receipt = helper.retrieve(url)
        assert receipt["method"] == "browser"
        assert Path(receipt["text_path"]).read_text() == "Real Chromium evidence"
        monkeypatch.setattr(helper, "MAX_TEXT_BYTES", 10)
        with pytest.raises(ValueError, match="exceeds limit"):
            helper.browser_text(url)
        # A subsequent launch succeeds after the failure path closed resources.
        monkeypatch.setattr(helper, "MAX_TEXT_BYTES", 1000)
        assert helper.browser_text(url)[0] == "Real Chromium evidence"
        with pytest.raises(ValueError, match="extraction failed"):
            helper.browser_text(url + "stall")
        assert helper.browser_text(url)[0] == "Real Chromium evidence"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
