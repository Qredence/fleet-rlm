from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_chunk_by_size_and_preview_cover_long_text() -> None:
    from fleet_rlm.runtime.content.chunking import chunk_by_size
    from fleet_rlm.runtime.content.preview import head_tail_preview

    chunks = chunk_by_size("abcdefghij", size=4, overlap=1)
    preview, length = head_tail_preview("X" * 10_000, max_chars=20)

    assert chunks == ["abcd", "defg", "ghij"]
    assert length == 10_000
    assert preview.startswith("X" * 10)
    assert preview.endswith("X" * 10)
    assert "characters omitted" in preview


def test_chunk_by_headers_and_timestamps_preserve_structure() -> None:
    from fleet_rlm.runtime.content.chunking import chunk_by_headers, chunk_by_timestamps

    headers = chunk_by_headers("Preamble\n# Title\nBody\n## Details\nMore")
    timestamps = chunk_by_timestamps("header\n2026-01-01 INFO Start\n2026-01-02 ERROR Fail")

    assert headers[0] == {"header": "", "content": "Preamble", "start_pos": 0}
    assert headers[1]["header"] == "# Title"
    assert "Body" in headers[1]["content"]
    assert headers[2]["header"] == "## Details"

    assert timestamps[0] == {"timestamp": "", "content": "header", "start_pos": 0}
    assert timestamps[1]["timestamp"] == "2026-01-01"
    assert timestamps[2]["timestamp"] == "2026-01-02"


def test_chunk_by_json_keys_splits_objects_and_rejects_non_objects() -> None:
    from fleet_rlm.runtime.content.chunking import chunk_by_json_keys

    chunks = chunk_by_json_keys(json.dumps({"users": [1, 2], "config": {"debug": True}}))

    assert chunks[0]["key"] == "users"
    assert json.loads(chunks[0]["content"]) == [1, 2]
    assert chunks[1]["value_type"] == "dict"

    with pytest.raises(ValueError, match="Expected JSON object"):
        chunk_by_json_keys("[1, 2, 3]")


def test_looks_like_binary_and_text_file_detection(tmp_path: Path) -> None:
    from fleet_rlm.runtime.content.ingestion import looks_like_binary, read_document_content

    text_path = tmp_path / "notes.txt"
    text_path.write_text("hello world\n", encoding="utf-8")
    binary_path = tmp_path / "payload.bin"
    binary_path.write_bytes(b"\x00\x01\x02garbage")

    text, metadata = read_document_content(text_path)

    assert text == "hello world\n"
    assert metadata == {"source_type": "text", "extraction_method": "read_text"}
    assert looks_like_binary(binary_path) is True


def test_read_document_content_falls_back_to_latin1_for_text_like_files(tmp_path: Path) -> None:
    from fleet_rlm.runtime.content.ingestion import read_document_content

    latin1_path = tmp_path / "latin1.txt"
    latin1_path.write_bytes("Cafe and croissant café".encode("latin-1"))

    text, metadata = read_document_content(latin1_path)

    assert text == "Cafe and croissant café"
    assert metadata == {"source_type": "text", "extraction_method": "read_text_latin1"}


def test_read_document_content_rejects_binary_non_text_files(tmp_path: Path) -> None:
    from fleet_rlm.runtime.content.ingestion import read_document_content

    binary_path = tmp_path / "image.dat"
    binary_path.write_bytes(b"\x00\xff\x01\x02")

    with pytest.raises(ValueError, match="Binary file detected"):
        read_document_content(binary_path)


def test_read_document_content_uses_pdf_fallback_when_markitdown_returns_no_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.runtime.content.ingestion as ingestion

    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.7")
    monkeypatch.setattr(ingestion, "extract_text_with_markitdown", lambda path: ("", {"source_type": "pdf"}))
    monkeypatch.setattr(
        ingestion,
        "extract_text_with_pypdf",
        lambda path: (
            "Recovered PDF text",
            {"source_type": "pdf", "extraction_method": "pypdf", "page_count": 1, "pages_with_text": 1},
        ),
    )

    text, metadata = ingestion.read_document_content(pdf_path)

    assert text == "Recovered PDF text"
    assert metadata["extraction_method"] == "pypdf"
    assert metadata["pages_with_text"] == 1
