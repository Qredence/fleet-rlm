"""Unit tests for document URL source helpers.

Covers fleet_rlm.runtime.execution.document_sources:
- is_http_url: URL detection
- _assert_public_http_url: security guard
- fetch_url_document_content: full fetch pipeline (mocked httpx)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fleet_rlm.runtime.execution.document_sources import (
    _assert_public_http_url,
    _normalize_content_type,
    _rewrite_gist_page_url,
    fetch_url_document_content,
    is_http_url,
)


# ---------------------------------------------------------------------------
# is_http_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://example.com/doc.txt", True),
        ("http://example.com/page", True),
        ("/local/path/file.txt", False),
        ("file:///some/path", False),
        ("", False),
        ("ftp://ftp.example.com/file", False),
    ],
)
def test_is_http_url(url: str, expected: bool):
    assert is_http_url(url) is expected


# ---------------------------------------------------------------------------
# _normalize_content_type
# ---------------------------------------------------------------------------


def test_normalize_content_type_strips_parameters():
    assert _normalize_content_type("text/html; charset=utf-8") == "text/html"


def test_normalize_content_type_none():
    assert _normalize_content_type(None) == ""


def test_normalize_content_type_plain():
    assert _normalize_content_type("application/json") == "application/json"


# ---------------------------------------------------------------------------
# _rewrite_gist_page_url
# ---------------------------------------------------------------------------


def test_rewrite_gist_url_rewrites_correctly():
    url = "https://gist.github.com/username/abc123def456"
    raw = _rewrite_gist_page_url(url)
    assert raw.startswith(
        "https://gist.githubusercontent.com/username/abc123def456/raw"
    )


def test_rewrite_non_gist_url_unchanged():
    url = "https://example.com/some-doc.txt"
    assert _rewrite_gist_page_url(url) == url


# ---------------------------------------------------------------------------
# _assert_public_http_url
# ---------------------------------------------------------------------------


def test_assert_public_http_url_rejects_non_http():
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        _assert_public_http_url("ftp://example.com/file", allow_private_net=False)


def test_assert_public_http_url_rejects_localhost():
    with pytest.raises(ValueError, match="Blocked local/private URL"):
        _assert_public_http_url("http://localhost:8080/api", allow_private_net=False)


def test_assert_public_http_url_allows_localhost_when_private_net_allowed():
    # Should not raise when allow_private_net=True
    _assert_public_http_url("http://localhost:8080/api", allow_private_net=True)


# ---------------------------------------------------------------------------
# fetch_url_document_content
# ---------------------------------------------------------------------------


def _fake_read_document_content(path: Path) -> tuple[str, dict]:
    return f"extracted from {path.name}", {
        "source_type": "html",
        "extraction_method": "fake",
    }


def _make_fake_streaming_response(
    *,
    status_code: int = 200,
    content_type: str = "text/plain",
    body: bytes = b"Hello world",
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"content-type": content_type}
    resp.url = MagicMock()
    resp.url.join = lambda loc: loc
    resp.__str__ = lambda self: "https://example.com/doc"
    resp.iter_bytes = MagicMock(return_value=iter([body]))
    resp.encoding = "utf-8"
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_fetch_url_document_content_plain_text(monkeypatch: pytest.MonkeyPatch):
    """fetch_url_document_content returns (text, meta) for text/plain content."""
    fake_resp = _make_fake_streaming_response(
        status_code=200,
        content_type="text/plain",
        body=b"Simple text doc",
    )

    client_mock = MagicMock()
    client_mock.__enter__ = MagicMock(return_value=client_mock)
    client_mock.__exit__ = MagicMock(return_value=False)
    client_mock.stream = MagicMock(return_value=fake_resp)

    with patch(
        "fleet_rlm.runtime.execution.document_sources.httpx.Client",
        return_value=client_mock,
    ):
        with patch(
            "fleet_rlm.runtime.execution.document_sources._assert_public_http_url"
        ):
            text, meta = fetch_url_document_content(
                "https://example.com/doc.txt",
                read_document_content=_fake_read_document_content,
            )

    assert "Simple text doc" in text
    assert meta["source_kind"] == "url"


def test_fetch_url_document_content_raises_on_disabled(monkeypatch: pytest.MonkeyPatch):
    """fetch_url_document_content raises ValueError when feature flag is disabled."""
    monkeypatch.setenv("FLEET_URL_DOCUMENT_FETCH_ENABLED", "false")

    with pytest.raises(ValueError, match="disabled"):
        fetch_url_document_content(
            "https://example.com/doc.txt",
            read_document_content=_fake_read_document_content,
        )


def test_fetch_url_document_content_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """fetch_url_document_content raises ValueError on 404 or other HTTP errors."""
    fake_resp = _make_fake_streaming_response(status_code=404)

    client_mock = MagicMock()
    client_mock.__enter__ = MagicMock(return_value=client_mock)
    client_mock.__exit__ = MagicMock(return_value=False)
    client_mock.stream = MagicMock(return_value=fake_resp)

    with patch(
        "fleet_rlm.runtime.execution.document_sources.httpx.Client",
        return_value=client_mock,
    ):
        with patch(
            "fleet_rlm.runtime.execution.document_sources._assert_public_http_url"
        ):
            with pytest.raises(ValueError, match="HTTP 404"):
                fetch_url_document_content(
                    "https://example.com/missing.txt",
                    read_document_content=_fake_read_document_content,
                )
