from __future__ import annotations


def head_tail_preview(text: str, *, max_chars: int = 500) -> tuple[str, int]:
    raw_len = len(text)
    if raw_len <= max_chars:
        return text, raw_len
    half = max(1, max_chars // 2)
    omitted = raw_len - (2 * half)
    preview = f"{text[:half]}\n\n... ({omitted:,} characters omitted) ...\n\n{text[-half:]}"
    return preview, raw_len


__all__ = ["head_tail_preview"]
