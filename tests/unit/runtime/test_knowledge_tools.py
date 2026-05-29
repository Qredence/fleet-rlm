from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fleet_rlm.runtime.tools.knowledge_tools import _search_knowledge_impl, persist_knowledge_document


@pytest.fixture()
def vol(tmp_path: Path) -> Path:
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "ingested").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# persist tests
# ---------------------------------------------------------------------------


def test_persist_creates_index(vol: Path) -> None:
    result = persist_knowledge_document(
        source="https://example.com/doc",
        text="Hello world",
        metadata=None,
        volume_mount_path=str(vol),
    )
    index_path = vol / "knowledge" / "index.json"
    assert index_path.exists()
    index = json.loads(index_path.read_text())
    assert index["schema_version"] == 1
    assert result.doc_id in index["documents"]
    entry = index["documents"][result.doc_id]
    assert entry["source"] == "https://example.com/doc"
    assert entry["alias"] == "active"
    assert entry["char_count"] == len("Hello world")


def test_persist_creates_ingested_file(vol: Path) -> None:
    result = persist_knowledge_document(
        source="https://example.com/doc",
        text="Some content here",
        metadata=None,
        volume_mount_path=str(vol),
    )
    ingested_file = vol / "knowledge" / "ingested" / f"{result.doc_id}.txt"
    assert ingested_file.exists()
    assert ingested_file.read_text() == "Some content here"


def test_persist_idempotent_same_content(vol: Path) -> None:
    r1 = persist_knowledge_document(
        source="https://example.com/same",
        text="Identical text",
        metadata=None,
        volume_mount_path=str(vol),
    )
    r2 = persist_knowledge_document(
        source="https://example.com/same",
        text="Identical text",
        metadata=None,
        volume_mount_path=str(vol),
    )
    assert r1.doc_id == r2.doc_id


def test_persist_includes_tags(vol: Path) -> None:
    result = persist_knowledge_document(
        source="https://example.com/tagged",
        text="Tagged content",
        metadata=None,
        volume_mount_path=str(vol),
        tags=["python", "api"],
    )
    index = json.loads((vol / "knowledge" / "index.json").read_text())
    assert index["documents"][result.doc_id]["tags"] == ["python", "api"]


def test_persist_deduplicates_by_source_text_hash(vol: Path) -> None:
    r1 = persist_knowledge_document(
        source="https://example.com/same",
        text="Same content",
        metadata={"a": 1},
        volume_mount_path=str(vol),
        alias="v1",
        tags=["tag1"],
    )
    r2 = persist_knowledge_document(
        source="https://example.com/same",
        text="Same content",
        metadata={"b": 2},
        volume_mount_path=str(vol),
        alias="v2",
        tags=["tag2"],
    )
    assert r1.doc_id == r2.doc_id


# ---------------------------------------------------------------------------
# search tests
# ---------------------------------------------------------------------------


def test_search_finds_by_source(vol: Path) -> None:
    persist_knowledge_document(
        source="https://mysite.io/article",
        text="Article body text",
        metadata=None,
        volume_mount_path=str(vol),
    )
    output = _search_knowledge_impl("mysite.io", volume_mount_path=str(vol))
    assert output.status == "ok"
    assert output.count >= 1
    assert any(urlparse(r.source).netloc == "mysite.io" for r in output.results)


def test_search_finds_by_text_content(vol: Path) -> None:
    persist_knowledge_document(
        source="https://example.com/content",
        text="unique_search_term_xyz content here",
        metadata=None,
        volume_mount_path=str(vol),
    )
    output = _search_knowledge_impl("unique_search_term_xyz", volume_mount_path=str(vol))
    assert output.status == "ok"
    assert output.count >= 1


def test_search_finds_by_tags(vol: Path) -> None:
    persist_knowledge_document(
        source="https://example.com/tagged",
        text="Some content",
        metadata=None,
        volume_mount_path=str(vol),
        tags=["python", "api"],
    )
    output = _search_knowledge_impl("python", volume_mount_path=str(vol))
    assert output.status == "ok"
    assert output.count >= 1
    assert any("python" in r.tags for r in output.results)


def test_search_empty_index_returns_ok(vol: Path) -> None:
    output = _search_knowledge_impl("anything", volume_mount_path=str(vol))
    assert output.status == "ok"
    assert output.count == 0


def test_search_max_results_respected(vol: Path) -> None:
    for i in range(5):
        persist_knowledge_document(
            source=f"https://example.com/doc{i}",
            text=f"shared_keyword content number {i}",
            metadata=None,
            volume_mount_path=str(vol),
        )
    output = _search_knowledge_impl("shared_keyword", volume_mount_path=str(vol), max_results=2)
    assert output.count <= 2
    assert len(output.results) <= 2


def test_search_path_traversal_blocked(vol: Path) -> None:
    index_path = vol / "knowledge" / "index.json"
    corrupt_index = {
        "doc_malicious": {
            "source": "https://example.com/evil",
            "alias": "active",
            "file": "../../../etc/passwd",
            "char_count": 100,
            "tags": [],
            "metadata": {},
            "ingested_at": "2024-01-01T00:00:00+00:00",
        }
    }
    index_path.write_text(json.dumps(corrupt_index), encoding="utf-8")
    output = _search_knowledge_impl("evil", volume_mount_path=str(vol))
    assert output.status == "ok"
    for r in output.results:
        assert "etc/passwd" not in r.path or str(vol / "knowledge").lower() in r.path.lower()


def test_search_reads_legacy_plain_index(vol: Path) -> None:
    doc_id = "doc_legacy"
    (vol / "knowledge" / "ingested" / f"{doc_id}.txt").write_text("legacy searchable text", encoding="utf-8")
    (vol / "knowledge" / "index.json").write_text(
        json.dumps(
            {
                doc_id: {
                    "source": "legacy://doc",
                    "alias": "legacy",
                    "file": f"ingested/{doc_id}.txt",
                    "char_count": 22,
                    "tags": ["legacy"],
                    "metadata": {},
                    "ingested_at": "2024-01-01T00:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )

    output = _search_knowledge_impl("legacy", volume_mount_path=str(vol))

    assert output.status == "ok"
    assert output.count == 1
    assert output.results[0].doc_id == doc_id


def test_legacy_index_is_rewritten_as_versioned_envelope(vol: Path) -> None:
    legacy_doc_id = "doc_legacy"
    (vol / "knowledge" / "ingested" / f"{legacy_doc_id}.txt").write_text("legacy text", encoding="utf-8")
    (vol / "knowledge" / "index.json").write_text(
        json.dumps(
            {
                legacy_doc_id: {
                    "source": "legacy://doc",
                    "alias": "legacy",
                    "file": f"ingested/{legacy_doc_id}.txt",
                    "char_count": 11,
                    "tags": ["legacy"],
                    "metadata": {},
                    "ingested_at": "2024-01-01T00:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )

    new_doc = persist_knowledge_document(
        source="new://doc",
        text="new searchable text",
        metadata=None,
        volume_mount_path=str(vol),
    )
    index = json.loads((vol / "knowledge" / "index.json").read_text())

    assert index["schema_version"] == 1
    assert legacy_doc_id in index["documents"]
    assert new_doc.doc_id in index["documents"]
