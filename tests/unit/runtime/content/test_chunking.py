"""Unit tests for fleet_rlm.chunking module.

These tests run locally (no Modal credentials required).
They validate the pure chunking functions used both
host-side and inside the sandbox.

Run with: uv run pytest tests/test_chunking.py -v
"""

from __future__ import annotations

import json

import pytest

from fleet_rlm.runtime.content.chunking import (
    chunk_by_headers,
    chunk_by_json_keys,
    chunk_by_size,
    chunk_by_timestamps,
)

# ---------------------------------------------------------------------------
# chunk_by_size
# ---------------------------------------------------------------------------


class TestChunkBySize:
    """Tests for chunk_by_size()."""

    @pytest.mark.parametrize(
        ("text", "size", "overlap", "expected"),
        [
            ("abcdefghij", 4, 0, ["abcd", "efgh", "ij"]),
            ("", 10, 0, []),
            ("abc", 10, 0, ["abc"]),
            ("abcdef", 3, 0, ["abc", "def"]),
            ("abc", 1, 0, ["a", "b", "c"]),
        ],
    )
    def test_happy_path(self, text, size, overlap, expected):
        result = chunk_by_size(text, size=size, overlap=overlap)
        assert result == expected

    def test_overlap(self):
        result = chunk_by_size("abcdefghij", size=4, overlap=1)
        assert result[0] == "abcd"
        assert result[1] == "defg"
        # Overlapping means 'd' appears in both chunk 0 and chunk 1

    @pytest.mark.parametrize(
        ("text", "size", "overlap", "match"),
        [
            ("abc", 0, 0, "size must be positive"),
            ("abc", 4, -1, "overlap must be non-negative"),
            ("abc", 4, 4, "overlap must be less than size"),
        ],
    )
    def test_invalid_params_raise(self, text, size, overlap, match):
        with pytest.raises(ValueError, match=match):
            chunk_by_size(text, size=size, overlap=overlap)

    def test_large_text(self):
        text = "x" * 10_000
        result = chunk_by_size(text, size=2000)
        assert len(result) == 5
        assert all(len(c) == 2000 for c in result)


# ---------------------------------------------------------------------------
# chunk_by_headers
# ---------------------------------------------------------------------------


class TestChunkByHeaders:
    """Tests for chunk_by_headers()."""

    def test_markdown_headers(self):
        text = "# Title\nContent\n## Section\nMore"
        result = chunk_by_headers(text)
        assert len(result) >= 2
        assert result[0]["header"] == "# Title"
        assert "Content" in result[0]["content"]

    def test_preamble_before_first_header(self):
        text = "Preamble text\n\n# First\nBody"
        result = chunk_by_headers(text)
        assert result[0]["header"] == ""
        assert "Preamble" in result[0]["content"]
        assert result[1]["header"] == "# First"

    def test_no_headers(self):
        text = "Just plain text\nno headers here"
        result = chunk_by_headers(text)
        assert len(result) == 1
        assert result[0]["header"] == ""

    def test_empty_text(self):
        assert chunk_by_headers("") == []

    def test_custom_pattern(self):
        text = "=== A ===\nContent A\n=== B ===\nContent B"
        result = chunk_by_headers(text, pattern=r"^===")
        assert len(result) == 2

    def test_start_pos_tracking(self):
        text = "# First\nBody\n# Second\nMore"
        result = chunk_by_headers(text)
        # First header should start at 0
        assert result[0]["start_pos"] == 0

    def test_header_only_sections(self):
        text = "# One\n# Two\n# Three"
        result = chunk_by_headers(text)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# chunk_by_timestamps
# ---------------------------------------------------------------------------


class TestChunkByTimestamps:
    """Tests for chunk_by_timestamps()."""

    def test_iso_timestamps(self):
        text = "2026-01-01 INFO Start\n2026-01-02 ERROR Fail"
        result = chunk_by_timestamps(text)
        assert len(result) == 2
        assert "2026-01-01" in result[0]["timestamp"]
        assert "2026-01-02" in result[1]["timestamp"]

    def test_content_includes_full_entry(self):
        text = "2026-01-01 INFO Start\n  details here\n2026-01-02 ERROR Fail"
        result = chunk_by_timestamps(text)
        assert "details here" in result[0]["content"]

    def test_no_timestamps(self):
        text = "no timestamps\njust text"
        result = chunk_by_timestamps(text)
        assert len(result) == 1
        assert result[0]["timestamp"] == ""

    def test_empty_text(self):
        assert chunk_by_timestamps("") == []

    def test_preamble_before_first_timestamp(self):
        text = "Log header\n2026-01-01 INFO Start"
        result = chunk_by_timestamps(text)
        assert result[0]["timestamp"] == ""
        assert "Log header" in result[0]["content"]

    def test_custom_pattern(self):
        text = "[10:00] Hello\n[10:05] World"
        result = chunk_by_timestamps(text, pattern=r"^\[\d{2}:\d{2}\]")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# chunk_by_json_keys
# ---------------------------------------------------------------------------


class TestChunkByJsonKeys:
    """Tests for chunk_by_json_keys()."""

    def test_basic_object(self):
        text = json.dumps({"users": [1, 2], "config": {"debug": True}})
        result = chunk_by_json_keys(text)
        assert len(result) == 2
        assert result[0]["key"] == "users"
        assert result[1]["key"] == "config"

    def test_value_type_tracking(self):
        text = json.dumps({"count": 42, "items": [1, 2], "name": "test"})
        result = chunk_by_json_keys(text)
        types = {r["key"]: r["value_type"] for r in result}
        assert types["count"] == "int"
        assert types["items"] == "list"
        assert types["name"] == "str"

    def test_content_is_valid_json(self):
        text = json.dumps({"items": [1, 2, 3]})
        result = chunk_by_json_keys(text)
        parsed = json.loads(result[0]["content"])
        assert parsed == [1, 2, 3]

    def test_empty_text(self):
        assert chunk_by_json_keys("") == []

    @pytest.mark.parametrize(
        ("text", "match"),
        [
            ("not json", "Invalid JSON"),
            ("[1, 2, 3]", "Expected JSON object"),
        ],
    )
    def test_invalid_input_raises(self, text, match):
        with pytest.raises(ValueError, match=match):
            chunk_by_json_keys(text)

    def test_empty_object(self):
        assert chunk_by_json_keys("{}") == []
