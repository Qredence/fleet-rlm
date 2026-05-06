from __future__ import annotations

from fleet_rlm.runtime.tools.chunking_tools import chunk_document


def test_chunk_document_warns_on_alias_like_input() -> None:
    result = chunk_document("codex_config", strategy="size", size=10000)

    assert result["status"] == "warning"
    assert result["reason"] == "alias_like_input"
    assert result["chunk_count"] == 0
    assert "document text" in result["warning"]


def test_chunk_document_warns_on_path_like_input() -> None:
    result = chunk_document("README.md", strategy="size", size=10000)

    assert result["status"] == "warning"
    assert result["reason"] == "alias_like_input"


def test_chunk_document_chunks_short_plain_text() -> None:
    result = chunk_document("README", strategy="size", size=10000)

    assert result["status"] == "ok"
    assert result["chunk_count"] == 1
    assert result["preview"] == "README"


def test_chunk_document_chunks_actual_text() -> None:
    result = chunk_document("first paragraph\n\nsecond paragraph", size=12)

    assert result["status"] == "ok"
    assert result["chunk_count"] > 0
    assert result["preview"]
