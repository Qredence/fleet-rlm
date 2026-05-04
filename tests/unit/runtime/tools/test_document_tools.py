from __future__ import annotations

import io
import os
import socket
from http.client import HTTPMessage
from pathlib import Path
from typing import Any

import pytest

from fleet_rlm.runtime.tools import document_tools


class _FakeResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self.headers = {"Content-Type": "text/plain"}
        self._chunks = list(chunks)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def read(self, size: int) -> bytes:
        _ = size
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class _FakeResponseWithUrl(_FakeResponse):
    def __init__(self, chunks: list[bytes], final_url: str) -> None:
        super().__init__(chunks)
        self._final_url = final_url

    def geturl(self) -> str:
        return self._final_url


class _FakeOpener:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def open(self, request: Any, timeout: int) -> _FakeResponse:
        _ = (request, timeout)
        return self._response


def test_download_url_removes_partial_temp_file_on_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Path] = []

    def fake_build_opener(*handlers: Any) -> _FakeOpener:
        assert any(
            isinstance(handler, document_tools._ValidatingRedirectHandler)
            for handler in handlers
        )
        return _FakeOpener(_FakeResponse([b"abcd", b"efgh"]))

    def fake_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        _ = (args, kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    def fake_mkstemp(suffix: str) -> tuple[int, str]:
        path = tmp_path / f"download{suffix}"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        created.append(path)
        return fd, str(path)

    monkeypatch.setattr(document_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(
        document_tools.urllib.request, "build_opener", fake_build_opener
    )
    monkeypatch.setattr(document_tools.tempfile, "mkstemp", fake_mkstemp)
    monkeypatch.setattr(document_tools, "_MAX_DOWNLOAD_BYTES", 4)

    with pytest.raises(ValueError, match="exceeds"):
        document_tools._download_url("https://example.test/doc.txt")

    assert created
    assert not created[0].exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/secrets",
        "http://localhost/secrets",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/internal",
    ],
)
def test_download_url_rejects_private_network_targets(
    url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_build_opener(*args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)
        raise AssertionError("build_opener should not be called for blocked URLs")

    monkeypatch.setattr(
        document_tools.urllib.request, "build_opener", fail_build_opener
    )

    with pytest.raises(ValueError, match="private network"):
        document_tools._download_url(url)


def test_download_url_rejects_hosts_resolving_to_private_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        _ = (args, kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    def fail_build_opener(*args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)
        raise AssertionError("build_opener should not be called for blocked URLs")

    monkeypatch.setattr(document_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(
        document_tools.urllib.request, "build_opener", fail_build_opener
    )

    with pytest.raises(ValueError, match="private network"):
        document_tools._download_url("https://docs.example.test/file.txt")


def test_download_url_rejects_private_redirect_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        _ = (args, kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    def fake_build_opener(*handlers: Any) -> Any:
        redirect_handler = next(
            handler
            for handler in handlers
            if isinstance(handler, document_tools._ValidatingRedirectHandler)
        )

        class _RedirectingOpener:
            def open(self, request: Any, timeout: int) -> _FakeResponse:
                _ = timeout
                redirect_handler.redirect_request(
                    request,
                    io.BytesIO(b""),
                    302,
                    "Found",
                    HTTPMessage(),
                    "http://169.254.169.254/latest/meta-data/",
                )
                raise AssertionError("private redirect should be blocked")

        return _RedirectingOpener()

    monkeypatch.setattr(document_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(
        document_tools.urllib.request, "build_opener", fake_build_opener
    )

    with pytest.raises(ValueError, match="private network"):
        document_tools._download_url("https://docs.example.test/file.txt")


def test_download_url_falls_back_to_original_url_without_geturl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Path] = []

    def fake_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        _ = (args, kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    def fake_build_opener(*handlers: Any) -> _FakeOpener:
        assert any(
            isinstance(handler, document_tools._ValidatingRedirectHandler)
            for handler in handlers
        )
        return _FakeOpener(_FakeResponse([b"hello world"]))

    def fake_mkstemp(suffix: str) -> tuple[int, str]:
        path = tmp_path / f"download{suffix}"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        created.append(path)
        return fd, str(path)

    monkeypatch.setattr(document_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(
        document_tools.urllib.request, "build_opener", fake_build_opener
    )
    monkeypatch.setattr(document_tools.tempfile, "mkstemp", fake_mkstemp)

    downloaded_path = document_tools._download_url("https://example.test/doc.md")

    assert downloaded_path == created[0]
    assert downloaded_path.suffix == ".md"
    assert downloaded_path.read_bytes() == b"hello world"


def test_download_url_uses_redirect_final_url_for_suffix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Path] = []

    def fake_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        _ = (args, kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    def fake_build_opener(*handlers: Any) -> _FakeOpener:
        assert any(
            isinstance(handler, document_tools._ValidatingRedirectHandler)
            for handler in handlers
        )
        return _FakeOpener(
            _FakeResponseWithUrl(
                [b"hello world"], "https://example.test/downloads/final.pdf"
            )
        )

    def fake_mkstemp(suffix: str) -> tuple[int, str]:
        path = tmp_path / f"download{suffix}"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        created.append(path)
        return fd, str(path)

    monkeypatch.setattr(document_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(
        document_tools.urllib.request, "build_opener", fake_build_opener
    )
    monkeypatch.setattr(document_tools.tempfile, "mkstemp", fake_mkstemp)

    downloaded_path = document_tools._download_url("https://example.test/redirect")

    assert downloaded_path == created[0]
    assert downloaded_path.suffix == ".pdf"


def test_fetch_document_text_rejects_local_paths() -> None:
    result = document_tools.fetch_document_text("/etc/passwd")

    assert result == {
        "status": "error",
        "error": "fetch_document_text only accepts HTTP(S) URLs.",
    }


def test_set_active_document_fails_without_document_cache() -> None:
    result = document_tools.set_active_document("docs")

    assert result["status"] == "error"
    assert result["active_alias"] == "docs"
    assert "without an agent document cache" in result["error"]
