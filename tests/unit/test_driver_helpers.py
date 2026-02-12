"""Unit tests for sandbox-driver injected helpers.

Uses the same ``_run_driver`` pattern from ``test_driver_protocol.py``
to feed JSON commands through the driver and inspect results.

Run with: uv run pytest tests/test_driver_helpers.py -v
"""

from __future__ import annotations

import builtins
import io
import json
import sys

from fleet_rlm.core.driver import sandbox_driver


def _run_driver(monkeypatch, lines: list[str]) -> list[dict]:
    """Feed *lines* as stdin to ``sandbox_driver`` and return JSON responses."""
    iterator = iter(lines)

    def fake_input() -> str:
        try:
            return next(iterator)
        except StopIteration as exc:
            raise EOFError from exc

    proto_out = io.StringIO()
    monkeypatch.setattr(builtins, "input", fake_input)
    monkeypatch.setattr(sys, "__stdout__", proto_out)

    sandbox_driver()

    raw_lines = [line for line in proto_out.getvalue().splitlines() if line.strip()]
    return [json.loads(line) for line in raw_lines]


def _cmd(code: str) -> str:
    """Build a minimal JSON command string."""
    return json.dumps({"code": code})


# ---------------------------------------------------------------------------
# peek
# ---------------------------------------------------------------------------


class TestPeekHelper:
    """Test the peek() helper injected into sandbox."""

    def test_peek_basic(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [_cmd('text = "Hello World"\nresult = peek(text, 0, 5)\nSUBMIT(result)')],
        )
        assert msgs[0]["final"]["output"] == "Hello"

    def test_peek_offset(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [_cmd('text = "Hello World"\nresult = peek(text, 6, 5)\nSUBMIT(result)')],
        )
        assert msgs[0]["final"]["output"] == "World"

    def test_peek_beyond_end(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [_cmd('text = "abc"\nresult = peek(text, 0, 100)\nSUBMIT(result)')],
        )
        assert msgs[0]["final"]["output"] == "abc"


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


class TestGrepHelper:
    """Test the grep() helper injected into sandbox."""

    def test_grep_basic(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    'text = "line one\\nline two\\nno match\\nline three"\n'
                    'hits = grep(text, "line")\n'
                    "SUBMIT(hits)"
                )
            ],
        )
        assert len(msgs[0]["final"]["output"]) == 3

    def test_grep_case_insensitive(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    'text = "Hello\\nhello\\nHELLO"\nhits = grep(text, "hello")\nSUBMIT(hits)'
                )
            ],
        )
        assert len(msgs[0]["final"]["output"]) == 3

    def test_grep_with_context(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    'text = "a\\nb\\nTARGET\\nd\\ne"\n'
                    'hits = grep(text, "TARGET", context=1)\n'
                    "SUBMIT(hits)"
                )
            ],
        )
        hit = msgs[0]["final"]["output"][0]
        assert "b" in hit
        assert "d" in hit

    def test_grep_no_match(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [_cmd('text = "abc\\ndef"\nhits = grep(text, "xyz")\nSUBMIT(hits)')],
        )
        assert msgs[0]["final"]["output"] == []


# ---------------------------------------------------------------------------
# chunk_by_size
# ---------------------------------------------------------------------------


class TestChunkBySizeHelper:
    """Test the chunk_by_size() helper injected into sandbox."""

    def test_chunk_basic(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    'text = "abcdefghij"\n'
                    "chunks = chunk_by_size(text, 4, 0)\n"
                    "SUBMIT(chunks)"
                )
            ],
        )
        assert msgs[0]["final"]["output"] == ["abcd", "efgh", "ij"]

    def test_chunk_with_overlap(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    'text = "abcdefghij"\n'
                    "chunks = chunk_by_size(text, 4, 1)\n"
                    "SUBMIT(len(chunks))"
                )
            ],
        )
        assert msgs[0]["final"]["output"] >= 3


# ---------------------------------------------------------------------------
# chunk_by_headers
# ---------------------------------------------------------------------------


class TestChunkByHeadersHelper:
    """Test the chunk_by_headers() helper injected into sandbox."""

    def test_markdown_headers(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    'text = "# Title\\nContent\\n## Sub\\nMore"\n'
                    "chunks = chunk_by_headers(text)\n"
                    "SUBMIT(len(chunks))"
                )
            ],
        )
        assert msgs[0]["final"]["output"] >= 2

    def test_no_headers(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    'text = "Just plain text"\n'
                    "chunks = chunk_by_headers(text)\n"
                    "SUBMIT(len(chunks))"
                )
            ],
        )
        # Should still return one chunk (the whole text)
        assert msgs[0]["final"]["output"] == 1


# ---------------------------------------------------------------------------
# chunk_by_timestamps
# ---------------------------------------------------------------------------


class TestChunkByTimestampsHelper:
    """Test the chunk_by_timestamps() helper injected into sandbox."""

    def test_timestamp_chunks(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    'text = "2026-01-01 INFO Start\\n2026-01-02 ERROR Fail"\n'
                    "chunks = chunk_by_timestamps(text)\n"
                    "SUBMIT(len(chunks))"
                )
            ],
        )
        assert msgs[0]["final"]["output"] == 2

    def test_timestamp_no_matches(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    'chunks = chunk_by_timestamps("plain text")\nSUBMIT(chunks[0]["timestamp"])'
                )
            ],
        )
        assert msgs[0]["final"]["output"] == ""


# ---------------------------------------------------------------------------
# chunk_by_json_keys
# ---------------------------------------------------------------------------


class TestChunkByJsonKeysHelper:
    """Test the chunk_by_json_keys() helper injected into sandbox."""

    def test_json_key_chunks(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    'text = \'{"users": [1, 2], "config": {"debug": true}}\'\n'
                    "chunks = chunk_by_json_keys(text)\n"
                    "SUBMIT(len(chunks))"
                )
            ],
        )
        assert msgs[0]["final"]["output"] == 2

    def test_json_key_invalid_json(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    """
try:
    chunk_by_json_keys("not-json")
    SUBMIT(status="unexpected")
except Exception as exc:
    SUBMIT(status="error", message=str(exc))
"""
                )
            ],
        )
        assert msgs[0]["final"]["status"] == "error"
        assert "Invalid JSON" in msgs[0]["final"]["message"]


# ---------------------------------------------------------------------------
# Buffers
# ---------------------------------------------------------------------------


class TestBufferHelpers:
    """Test the add_buffer / get_buffer / clear_buffer helpers."""

    def test_buffer_lifecycle(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    'add_buffer("test", "a")\n'
                    'add_buffer("test", "b")\n'
                    'buf = get_buffer("test")\n'
                    "SUBMIT(buf)"
                )
            ],
        )
        assert msgs[0]["final"]["output"] == ["a", "b"]

    def test_get_missing_buffer(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch, [_cmd('buf = get_buffer("nonexistent")\nSUBMIT(buf)')]
        )
        assert msgs[0]["final"]["output"] == []

    def test_clear_specific_buffer(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    'add_buffer("x", 1)\n'
                    'add_buffer("y", 2)\n'
                    'clear_buffer("x")\n'
                    'bx = get_buffer("x")\n'
                    'by = get_buffer("y")\n'
                    "SUBMIT(x=bx, y=by)"
                )
            ],
        )
        assert msgs[0]["final"]["x"] == []
        assert msgs[0]["final"]["y"] == [2]

    def test_clear_all_buffers(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    'add_buffer("a", 1)\n'
                    'add_buffer("b", 2)\n'
                    "clear_buffer()\n"
                    'ba = get_buffer("a")\n'
                    "SUBMIT(ba)"
                )
            ],
        )
        assert msgs[0]["final"]["output"] == []

    def test_buffers_persist_across_commands(self, monkeypatch):
        """Buffers should persist across multiple commands."""
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd('add_buffer("acc", "first")'),
                _cmd(
                    'add_buffer("acc", "second")\nbuf = get_buffer("acc")\nSUBMIT(buf)'
                ),
            ],
        )
        # First command has no SUBMIT, second returns the buffer
        assert msgs[1]["final"]["output"] == ["first", "second"]


# ---------------------------------------------------------------------------
# Volume helpers (no real /data dir)
# ---------------------------------------------------------------------------


class TestVolumeHelpers:
    """Test save_to_volume / load_from_volume without a real volume."""

    def test_save_without_volume(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch, [_cmd('msg = save_to_volume("test.txt", "data")\nSUBMIT(msg)')]
        )
        assert "no volume" in msgs[0]["final"]["output"].lower()

    def test_load_without_volume(self, monkeypatch):
        msgs = _run_driver(
            monkeypatch, [_cmd('msg = load_from_volume("test.txt")\nSUBMIT(msg)')]
        )
        assert "not found" in msgs[0]["final"]["output"].lower()


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------


class TestWorkspaceHelpers:
    """Test workspace_write / workspace_read / workspace_list / workspace_append."""

    def test_workspace_read_without_volume(self, monkeypatch):
        """Test workspace_read returns error when no volume mounted."""
        msgs = _run_driver(
            monkeypatch, [_cmd('msg = workspace_read("test.txt")\nSUBMIT(msg)')]
        )
        assert "error" in msgs[0]["final"]["output"].lower()

    def test_workspace_list_without_volume(self, monkeypatch):
        """Test workspace_list returns empty list when no volume mounted."""
        msgs = _run_driver(
            monkeypatch, [_cmd("files = workspace_list()\nSUBMIT(files)")]
        )
        assert msgs[0]["final"]["output"] == []

    def test_workspace_helpers_exist(self, monkeypatch):
        """Test that all workspace helpers are available in sandbox."""
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    "funcs = ['workspace_write', 'workspace_read', 'workspace_list', 'workspace_append']\n"
                    "available = [f for f in funcs if f in globals()]\n"
                    "SUBMIT(available)"
                )
            ],
        )
        assert len(msgs[0]["final"]["output"]) == 4

    def test_workspace_read_rejects_parent_path_escape(self, monkeypatch):
        """Parent traversal should be rejected before filesystem access."""
        msgs = _run_driver(
            monkeypatch, [_cmd('msg = workspace_read("../outside.txt")\nSUBMIT(msg)')]
        )
        assert "invalid workspace path" in msgs[0]["final"]["output"].lower()

    def test_workspace_append_rejects_absolute_path(self, monkeypatch):
        """Absolute paths should not be writable via workspace helpers."""
        msgs = _run_driver(
            monkeypatch,
            [_cmd('msg = workspace_append("/tmp/escape.txt", "x")\nSUBMIT(msg)')],
        )
        assert "invalid workspace path" in msgs[0]["final"]["output"].lower()


# ---------------------------------------------------------------------------
# Session history helpers
# ---------------------------------------------------------------------------


class TestSessionHistoryHelpers:
    """Test log_execution / get_session_history / get_last_execution."""

    def test_session_history_helpers_exist(self, monkeypatch):
        """Test that session history helpers are available in sandbox."""
        msgs = _run_driver(
            monkeypatch,
            [
                _cmd(
                    "funcs = ['log_execution', 'get_session_history', 'get_last_execution']\n"
                    "available = [f for f in funcs if f in globals()]\n"
                    "SUBMIT(available)"
                )
            ],
        )
        assert len(msgs[0]["final"]["output"]) == 3

    def test_get_session_history_empty(self, monkeypatch):
        """Test get_session_history returns empty list initially."""
        msgs = _run_driver(
            monkeypatch, [_cmd("history = get_session_history()\nSUBMIT(history)")]
        )
        assert msgs[0]["final"]["output"] == []

    def test_get_last_execution_empty(self, monkeypatch):
        """Test get_last_execution returns None when no history."""
        msgs = _run_driver(
            monkeypatch, [_cmd("last = get_last_execution()\nSUBMIT(last)")]
        )
        assert msgs[0]["final"]["output"] is None
