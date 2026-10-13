"""Source boundaries and offsets for the bundled long-context splitter."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def helper():
    path = Path("src/fleet_rlm/skills/bundled/long-context/scripts/semantic_chunk.py")
    spec = importlib.util.spec_from_file_location("semantic_chunk", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_labelled_sections_keep_complete_blocks_and_original_offsets(helper):
    source = (
        "Preface mentions [CAL] without introducing it.\n[STATUS] Operative rules\nBody.\n[CAL-E] Calendar\nDetails.\n"
    )
    assert helper.detect_content_type(source) == "sectioned"
    chunks = helper.chunk_content(source, "auto", 1000, 0)
    assert [text for _, _, text in chunks] == [
        "Preface mentions [CAL] without introducing it.\n",
        "[STATUS] Operative rules\nBody.\n",
        "[CAL-E] Calendar\nDetails.\n",
    ]
    assert "".join(text for _, _, text in chunks) == source
    assert all(source[start:end] == text for start, end, text in chunks)


@pytest.mark.parametrize("source", ['["STATUS", "CAL"]', '{"title": "[STATUS] Rules"}', "[]"])
def test_valid_json_is_not_a_labelled_document(helper, source):
    assert helper.detect_content_type(source) == "json"


@pytest.mark.parametrize("source,kind", [("", "text"), ("Ordinary text.", "text"), ("# Heading\nBody", "markdown")])
def test_unlabelled_documents_retain_their_content_type(helper, source, kind):
    assert helper.detect_content_type(source) == kind


@pytest.mark.parametrize(
    "source,kind",
    [
        ("# Heading\n[NOTE] Body", "markdown"),
        ('def example():\n    """\n[NOTE] Description\n    """\n', "python"),
        ("2026-01-01 Started\n[NOTE] Diagnostic", "log"),
    ],
)
def test_labelled_notes_do_not_override_existing_document_structure(helper, source, kind):
    assert helper.detect_content_type(source) == kind


def test_oversized_labelled_sections_remain_bounded_and_source_aligned(helper):
    source = "[FIRST] Title\n" + "x" * 70 + "\n[SECOND] Another\nEnd."
    chunks = helper.chunk_content(source, "sectioned", 24, 3)
    second = source.index("[SECOND]")
    assert all(len(text) <= 24 and source[start:end] == text for start, end, text in chunks)
    assert all(end <= second or start >= second for start, end, _ in chunks)
    assert {index for start, end, _ in chunks for index in range(start, end)} == set(range(len(source)))
