from __future__ import annotations

import os
from pathlib import Path
import socket
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


def test_download_url_removes_partial_temp_file_on_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Path] = []

    def fake_urlopen(request: Any, timeout: int) -> _FakeResponse:
        _ = (request, timeout)
        return _FakeResponse([b"abcd", b"efgh"])

    def fake_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        _ = (args, kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    def fake_mkstemp(suffix: str) -> tuple[int, str]:
        path = tmp_path / f"download{suffix}"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        created.append(path)
        return fd, str(path)

    monkeypatch.setattr(document_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(document_tools.urllib.request, "urlopen", fake_urlopen)
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
    def fail_urlopen(*args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)
        raise AssertionError("urlopen should not be called for blocked URLs")

    monkeypatch.setattr(document_tools.urllib.request, "urlopen", fail_urlopen)

    with pytest.raises(ValueError, match="private network"):
        document_tools._download_url(url)


def test_download_url_rejects_hosts_resolving_to_private_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        _ = (args, kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    def fail_urlopen(*args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)
        raise AssertionError("urlopen should not be called for blocked URLs")

    monkeypatch.setattr(document_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(document_tools.urllib.request, "urlopen", fail_urlopen)

    with pytest.raises(ValueError, match="private network"):
        document_tools._download_url("https://docs.example.test/file.txt")


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
