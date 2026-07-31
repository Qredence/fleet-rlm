from __future__ import annotations

import pytest

from scripts.benchmarks.run_native_long_context import _source

MARKERS = ("first-marker", "middle-marker", "last-marker")


def test_source_rejects_sizes_that_cannot_hold_planted_markers() -> None:
    with pytest.raises(ValueError, match="too small"):
        _source(1_024, MARKERS)

    with pytest.raises(ValueError, match="overlap"):
        _source(64 * 1024, MARKERS)


def test_source_preserves_marker_layout_for_valid_size() -> None:
    source = _source(128 * 1024, MARKERS)

    assert len(source.encode("utf-8")) == 128 * 1024
    assert source.index(MARKERS[0]) == 64 * 1024 - len(MARKERS[0])
    assert source.index(MARKERS[1]) == (128 * 1024) // 2
    assert source.index(MARKERS[2]) == 128 * 1024 - len(MARKERS[2]) - 1
