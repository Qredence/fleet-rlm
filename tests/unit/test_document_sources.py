from __future__ import annotations

import ipaddress
from typing import Any

import httpx
import pytest

from fleet_rlm.core.execution.document_sources import (
    _rewrite_gist_page_url,
    fetch_url_document_content,
)


class _FakeStreamResponse:
    def __init__(
        self,
        *,
        url: str,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        encoding: str | None = "utf-8",
    ):
        self.url = httpx.URL(url)
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self.encoding = encoding

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_bytes(self):
        yield self._body


class _FakeClient:
    def __init__(self, responses: list[_FakeStreamResponse]):
        self._responses = list(responses)
        self.calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def stream(self, method: str, url: str, follow_redirects: bool = False):
        assert method == "GET"
        assert follow_redirects is False
        self.calls.append(url)
        if not self._responses:
            raise AssertionError("No fake responses left")
        return self._responses.pop(0)


def _public_ip_set() -> set[ipaddress.IPv4Address]:
    return {ipaddress.ip_address("93.184.216.34")}


def test_rewrite_gist_page_url_to_raw():
    rewritten = _rewrite_gist_page_url(
        "https://gist.github.com/darinkishore/48b705bb6581bd2c1c83f72dff872a81"
    )
    assert (
        rewritten
        == "https://gist.githubusercontent.com/darinkishore/48b705bb6581bd2c1c83f72dff872a81/raw"
    )


def test_fetch_url_document_content_loads_plain_text(monkeypatch):
    fake_client = _FakeClient(
        [
            _FakeStreamResponse(
                url="https://example.com/doc.txt",
                headers={"content-type": "text/plain; charset=utf-8"},
                body=b"hello world\nsecond line",
            )
        ]
    )
    monkeypatch.setattr(
        "fleet_rlm.react.document_sources._resolve_host_ips",
        lambda hostname, port: _public_ip_set(),
    )
    monkeypatch.setattr(
        "fleet_rlm.react.document_sources.httpx.Client",
        lambda **kwargs: fake_client,
    )

    text, meta = fetch_url_document_content(
        "https://example.com/doc.txt",
        read_document_content=lambda path: ("unused", {"source_type": "text"}),
    )

    assert text == "hello world\nsecond line"
    assert meta["source_kind"] == "url"
    assert meta["source_url"] == "https://example.com/doc.txt"
    assert meta["final_url"] == "https://example.com/doc.txt"
    assert meta["content_type"] == "text/plain"
    assert meta["extraction_method"] == "httpx_text"


def test_fetch_url_document_content_uses_extractor_for_html(monkeypatch):
    html_bytes = b"<html><body><h1>Title</h1></body></html>"
    fake_client = _FakeClient(
        [
            _FakeStreamResponse(
                url="https://example.com/page",
                headers={"content-type": "text/html; charset=utf-8"},
                body=html_bytes,
            )
        ]
    )
    monkeypatch.setattr(
        "fleet_rlm.react.document_sources._resolve_host_ips",
        lambda hostname, port: _public_ip_set(),
    )
    monkeypatch.setattr(
        "fleet_rlm.react.document_sources.httpx.Client",
        lambda **kwargs: fake_client,
    )

    seen: dict[str, Any] = {}

    def _fake_read_document_content(path):
        seen["suffix"] = path.suffix
        seen["exists"] = path.exists()
        seen["raw"] = path.read_bytes()
        return "Extracted HTML text", {
            "source_type": "html",
            "extraction_method": "markitdown",
        }

    text, meta = fetch_url_document_content(
        "https://example.com/page",
        read_document_content=_fake_read_document_content,
    )

    assert text == "Extracted HTML text"
    assert seen["suffix"] == ".html"
    assert seen["exists"] is True
    assert seen["raw"] == html_bytes
    assert meta["source_kind"] == "url"
    assert meta["source_type"] == "html"
    assert meta["extraction_method"] == "markitdown"


def test_fetch_url_document_content_rewrites_gist_page_and_follows_redirect(
    monkeypatch,
):
    fake_client = _FakeClient(
        [
            _FakeStreamResponse(
                url="https://gist.githubusercontent.com/user/gistid/raw",
                status_code=302,
                headers={"location": "/user/gistid/raw/abcdef/file.txt"},
            ),
            _FakeStreamResponse(
                url="https://gist.githubusercontent.com/user/gistid/raw/abcdef/file.txt",
                headers={"content-type": "text/plain"},
                body=b"gist content",
            ),
        ]
    )
    monkeypatch.setattr(
        "fleet_rlm.react.document_sources._resolve_host_ips",
        lambda hostname, port: _public_ip_set(),
    )
    monkeypatch.setattr(
        "fleet_rlm.react.document_sources.httpx.Client",
        lambda **kwargs: fake_client,
    )

    text, meta = fetch_url_document_content(
        "https://gist.github.com/user/gistid",
        read_document_content=lambda path: ("unused", {}),
    )

    assert text == "gist content"
    assert fake_client.calls[0] == "https://gist.githubusercontent.com/user/gistid/raw"
    assert meta["final_url"].endswith("/raw/abcdef/file.txt")


def test_fetch_url_document_content_zero_redirect_limit_uses_default(monkeypatch):
    fake_client = _FakeClient(
        [
            _FakeStreamResponse(
                url="https://example.com/source",
                status_code=302,
                headers={"location": "/next"},
            ),
            _FakeStreamResponse(
                url="https://example.com/next",
                headers={"content-type": "text/plain"},
                body=b"redirected content",
            ),
        ]
    )
    monkeypatch.setenv("FLEET_URL_DOCUMENT_MAX_REDIRECTS", "0")
    monkeypatch.setattr(
        "fleet_rlm.react.document_sources._resolve_host_ips",
        lambda hostname, port: _public_ip_set(),
    )
    monkeypatch.setattr(
        "fleet_rlm.react.document_sources.httpx.Client",
        lambda **kwargs: fake_client,
    )

    text, meta = fetch_url_document_content(
        "https://example.com/source",
        read_document_content=lambda path: ("unused", {}),
    )

    assert text == "redirected content"
    assert fake_client.calls == [
        "https://example.com/source",
        "https://example.com/next",
    ]
    assert meta["final_url"] == "https://example.com/next"


def test_fetch_url_document_content_blocks_private_targets_by_default(monkeypatch):
    monkeypatch.setattr(
        "fleet_rlm.react.document_sources._resolve_host_ips",
        lambda hostname, port: {ipaddress.ip_address("127.0.0.1")},
    )

    with pytest.raises(ValueError, match="Blocked local/private URL target"):
        fetch_url_document_content(
            "http://127.0.0.1:8000/test",
            read_document_content=lambda path: ("unused", {}),
        )


def test_fetch_url_document_content_rejects_unsupported_scheme():
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        fetch_url_document_content(
            "ftp://example.com/file.txt",
            read_document_content=lambda path: ("unused", {}),
        )


def test_fetch_url_document_content_rejects_oversized_response(monkeypatch):
    fake_client = _FakeClient(
        [
            _FakeStreamResponse(
                url="https://example.com/big.txt",
                headers={"content-type": "text/plain", "content-length": "25"},
                body=b"x" * 25,
            )
        ]
    )
    monkeypatch.setenv("FLEET_URL_DOCUMENT_MAX_BYTES", "10")
    monkeypatch.setattr(
        "fleet_rlm.react.document_sources._resolve_host_ips",
        lambda hostname, port: _public_ip_set(),
    )
    monkeypatch.setattr(
        "fleet_rlm.react.document_sources.httpx.Client",
        lambda **kwargs: fake_client,
    )

    with pytest.raises(ValueError, match="size limit"):
        fetch_url_document_content(
            "https://example.com/big.txt",
            read_document_content=lambda path: ("unused", {}),
        )


def test_fetch_url_document_content_can_be_disabled(monkeypatch):
    monkeypatch.setenv("FLEET_URL_DOCUMENT_FETCH_ENABLED", "false")
    with pytest.raises(ValueError, match="disabled"):
        fetch_url_document_content(
            "https://example.com/file.txt",
            read_document_content=lambda path: ("unused", {}),
        )
