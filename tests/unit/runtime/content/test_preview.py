"""Unit tests for ``head_tail_preview``.

Covers the head+tail preview helper that mirrors DSPy 3.2.0's REPLEntry
output-formatting convention.
"""

from __future__ import annotations

from fleet_rlm.runtime.content.preview import head_tail_preview


def test_short_text_returned_unchanged() -> None:
    short = "hello world"

    preview, length = head_tail_preview(short, max_chars=500)

    assert preview == short
    assert length == len(short)


def test_boundary_text_is_not_truncated() -> None:
    exact = "x" * 500

    preview, length = head_tail_preview(exact, max_chars=500)

    assert preview == exact
    assert length == 500


def test_long_text_preserves_head_and_tail_with_omitted_count() -> None:
    head = "START" + ("a" * 245)
    tail = ("b" * 245) + "END"
    middle = "M" * 9_000
    text = head + middle + tail
    total = len(text)

    preview, length = head_tail_preview(text, max_chars=500)

    assert length == total
    # Head is the first 250 chars (max_chars // 2), tail is the last 250 chars.
    assert preview.startswith(text[:250])
    assert preview.endswith(text[-250:])
    assert "characters omitted" in preview
    # Omitted count is exactly what sits between the two halves.
    assert f"({total - 500:,} characters omitted)" in preview


def test_max_chars_smaller_than_default() -> None:
    text = "abcdefghij" * 10  # 100 chars

    preview, length = head_tail_preview(text, max_chars=20)

    assert length == 100
    assert preview.startswith(text[:10])
    assert preview.endswith(text[-10:])
    assert "(80 characters omitted)" in preview


def test_odd_max_chars_both_halves_present() -> None:
    # odd max_chars=5 → half=2, head=text[:2], tail=text[-2:], omitted=total-4
    text = "ABCDE" * 20  # 100 chars
    preview, length = head_tail_preview(text, max_chars=5)

    assert length == 100
    assert preview.startswith(text[:2])
    assert preview.endswith(text[-2:])
    assert "(96 characters omitted)" in preview


def test_max_chars_zero_or_one_clamped_to_one_char_each_side() -> None:
    # max_chars=1 → half=max(1, 0)=1, so head=text[:1], tail=text[-1:]
    text = "FIRST" + "X" * 100 + "LAST"
    for tiny in (0, 1):
        preview, length = head_tail_preview(text, max_chars=tiny)
        assert length == len(text)
        # preview must start with "F" and end with "T" (first/last char)
        assert preview[0] == "F"
        assert preview.split("...")[-1].strip().endswith("T")


def test_preview_slightly_exceeds_max_chars_due_to_marker() -> None:
    # Documents the soft-cap: total preview > max_chars because marker is extra.
    text = "X" * 10_000
    preview, _ = head_tail_preview(text, max_chars=4_000)
    assert len(preview) > 4_000  # marker overhead makes it slightly larger
    assert "characters omitted" in preview
