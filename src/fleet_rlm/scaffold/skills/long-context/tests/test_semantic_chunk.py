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


semantic_chunk = load_script("semantic_chunk", "semantic_chunk.py")


def test_markdown_chunking_uses_headings() -> None:
    content = "# One\nalpha\n# Two\nbeta\n"

    chunks = semantic_chunk.chunk_content(content, "markdown", max_size=100, overlap=0)

    assert [chunk[2].splitlines()[0] for chunk in chunks] == ["# One", "# Two"]


def test_size_chunk_overlap_cannot_stall() -> None:
    chunks = semantic_chunk.size_chunks("abcdef", size=3, overlap=3)

    assert chunks[0] == (0, 3, "abc")
    assert chunks[-1] == (5, 6, "f")
