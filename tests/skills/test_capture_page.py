"""Bundled HTTP capture and rendered-text evidence contracts without public network access."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import threading
import time
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

RESOURCE = Path("src/fleet_rlm/skills/bundled/web-research/scripts/capture_page.py")


@pytest.fixture
def helper():
    spec = importlib.util.spec_from_file_location("capture_page", RESOURCE)
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
    monkeypatch.setattr(helper, "public_url", lambda url: url)
    text = "verified evidence\n" * 100
    monkeypatch.setattr(helper, "http_text", lambda url: (text, url, "Example", False))
    receipt = helper.retrieve("https://example.org")
    stored = Path(receipt["text_path"]).read_bytes()
    assert stored == text.encode()
    assert receipt["sha256"] == hashlib.sha256(stored).hexdigest()
    assert receipt["bytes"] == len(stored)
    assert receipt["requested_url"] == receipt["final_url"] == "https://example.org"
    assert receipt["retrieved_at_utc"].endswith("+00:00")
    assert json.loads(Path(receipt["provenance_path"]).read_text()) == {
        key: value for key, value in receipt.items() if key != "needs_browser"
    }
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
    monkeypatch.setattr(helper, "public_url", lambda url: url)
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


def test_rendered_evidence_keeps_scope_and_is_never_a_full_site_claim(helper, monkeypatch, tmp_path):
    monkeypatch.setenv("FLEET_RUN_SCRATCH", str(tmp_path))
    monkeypatch.setattr(helper, "public_url", lambda url: url)
    receipt = helper.save_evidence(
        "Rendered evidence",
        requested_url="https://example.org",
        final_url="https://example.org/final",
        title="After",
        rendered=True,
    )
    assert receipt["requested_url"] == "https://example.org"
    assert receipt["final_url"] == "https://example.org/final"
    assert receipt["method"] == "browser"
    assert receipt["completeness"]["dynamic_content_complete"] is False
    assert Path(receipt["text_path"]).read_text() == "Rendered evidence"


def test_early_eof_never_saves_complete_evidence(helper, monkeypatch, tmp_path):
    monkeypatch.setenv("FLEET_RUN_SCRATCH", str(tmp_path))
    monkeypatch.setattr(helper, "public_url", lambda url: url)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"Only a partial response")
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ValueError, match="incomplete HTTP response"):
            helper.retrieve(f"http://127.0.0.1:{server.server_port}/")
        assert list(tmp_path.iterdir()) == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("content_type", ["application/json", "application/ld+json"])
def test_public_json_metadata_is_saved_without_html_interpretation(helper, monkeypatch, tmp_path, content_type):
    pytest.importorskip("bs4", reason="snapshot-only dependency")
    monkeypatch.setenv("FLEET_RUN_SCRATCH", str(tmp_path))
    monkeypatch.setattr(helper, "public_url", lambda url: url)
    body = b'{"name":"daytona","version":"0.220.0","description":"<script>source text</script>"}'
    stream = io.BytesIO(body)
    stream.url = "https://pypi.org/pypi/daytona/0.220.0/json"
    stream.headers = Message()
    stream.headers["Content-Type"] = content_type + "; charset=utf-8"
    stream.headers["Content-Length"] = str(len(body))
    monkeypatch.setattr(helper, "build_opener", lambda *_args: SimpleNamespace(open=lambda *_args, **_kwargs: stream))

    receipt = helper.retrieve(stream.url)

    assert Path(receipt["text_path"]).read_bytes() == body
    assert receipt["sha256"] == hashlib.sha256(body).hexdigest()
    assert receipt["needs_browser"] is False
    assert receipt["title"] == ""
    assert json.loads(Path(receipt["text_path"]).read_text())["version"] == "0.220.0"


def test_public_evidence_helper_refuses_binary_content(helper, monkeypatch):
    pytest.importorskip("bs4", reason="snapshot-only dependency")
    monkeypatch.setattr(helper, "public_url", lambda url: url)
    stream = io.BytesIO(b"binary archive")
    stream.url = "https://example.org/source.tar.gz"
    stream.headers = Message()
    stream.headers["Content-Type"] = "application/octet-stream"
    monkeypatch.setattr(helper, "build_opener", lambda *_args: SimpleNamespace(open=lambda *_args, **_kwargs: stream))
    with pytest.raises(ValueError, match="supported"):
        helper.http_text(stream.url)
