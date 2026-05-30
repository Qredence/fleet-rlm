from __future__ import annotations

import importlib.util
from pathlib import Path


def load_script(name: str, script: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / script
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rank_chunks = load_script("rank_chunks", "rank_chunks.py")


def test_rank_chunks_prefers_matching_query_terms() -> None:
    content = "alpha target\n" * 5 + "beta filler\n" * 5

    ranked = rank_chunks.rank_chunks_by_query(content, "alpha target", chunk_size=40, top_k=1)

    assert len(ranked) == 1
    assert ranked[0][0] == 0
    assert ranked[0][2] > 0


def test_rank_chunks_ignores_common_stopwords() -> None:
    content = ("and for are but not " * 20) + ("vector retrieval\n" * 3)

    ranked = rank_chunks.rank_chunks_by_query(content, "and for not vector", chunk_size=100, top_k=1)

    start, end, score = ranked[0]
    assert "vector" in content[start:end]
    assert score > 0


def test_rank_chunks_honors_top_k_without_keywords() -> None:
    content = "one two\n" * 10

    ranked = rank_chunks.rank_chunks_by_query(content, "the you", chunk_size=10, top_k=0)

    assert ranked == []
