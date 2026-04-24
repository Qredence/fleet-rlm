from __future__ import annotations

import os
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


def test_download_url_removes_partial_temp_file_on_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Path] = []

    def fake_urlopen(request: Any, timeout: int) -> _FakeResponse:
        _ = (request, timeout)
        return _FakeResponse([b"abcd", b"efgh"])

    def fake_mkstemp(suffix: str) -> tuple[int, str]:
        path = tmp_path / f"download{suffix}"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        created.append(path)
        return fd, str(path)

    monkeypatch.setattr(document_tools.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(document_tools.tempfile, "mkstemp", fake_mkstemp)
    monkeypatch.setattr(document_tools, "_MAX_DOWNLOAD_BYTES", 4)

    with pytest.raises(ValueError, match="exceeds"):
        document_tools._download_url("https://example.test/doc.txt")

    assert created
    assert not created[0].exists()
