"""Helpers for transcript-derived optimization datasets."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fleet_rlm.runtime.quality.transcript_exports import (
    build_transcript_dataset_rows as _build_transcript_dataset_rows,
)


def build_transcript_dataset_rows(
    *,
    module_slug: str,
    turns: list[tuple[str | None, str | None]],
) -> tuple[list[dict[str, object]], str]:
    """Re-export the shared transcript mapper at the API service boundary."""

    return _build_transcript_dataset_rows(module_slug=module_slug, turns=turns)


def persist_jsonl_rows(
    *,
    root: Path,
    rows: list[dict[str, object]],
    prefix: str,
) -> Path:
    """Write rows as JSONL under ``root`` and return the created file path."""
    resolved_root = root.resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=resolved_root,
        prefix=prefix,
        suffix=".jsonl",
        delete=False,
    ) as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return Path(fh.name).resolve()
