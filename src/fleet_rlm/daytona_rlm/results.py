"""Persistence helpers for the experimental Daytona RLM pilot."""

from __future__ import annotations

import json
from pathlib import Path

from .types import DaytonaRunResult


def persist_result(
    result: DaytonaRunResult, *, output_dir: Path | str = "results/daytona-rlm"
) -> Path:
    """Persist a run result to a local JSON artifact."""

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / f"{result.run_id}.json"
    path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    result.result_path = str(path)
    return path
