"""Validate and persist one content-free Phase-0 runtime benchmark receipt.

The benchmark executor writes per-scenario aggregates to ``--results``. This
command adds reproducibility provenance from the selected Fleet policy and
current checkout, validates the stable receipt schema, and never reads prompts,
model responses, private reasoning, or credentials into the receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

from fleet_rlm.benchmarking import RuntimeBenchmarkReceipt, RuntimeBenchmarkResult
from fleet_rlm.config.loader import _CONFIG_PATH, load_runtime_settings


def _commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        text=True,
    )
    return completed.stdout.strip()


def _results(path: Path) -> tuple[RuntimeBenchmarkResult, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("results must be a JSON array")
    return tuple(RuntimeBenchmarkResult.model_validate(item) for item in payload)


def build_receipt(results: Sequence[RuntimeBenchmarkResult]) -> RuntimeBenchmarkReceipt:
    """Add selected-policy provenance to executor-provided aggregate results."""
    settings = load_runtime_settings()
    raw_config = _CONFIG_PATH.read_bytes()
    return RuntimeBenchmarkReceipt(
        fleet_version="0.7.6",
        commit=_commit(),
        config_digest=hashlib.sha256(raw_config).hexdigest(),
        snapshot_name=str(settings.daytona_snapshot),
        root_model=settings.root_model,
        sub_model=settings.sub_model,
        results=tuple(results),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path, help="per-scenario aggregate JSON array")
    parser.add_argument("--output", required=True, type=Path, help="receipt JSON path")
    args = parser.parse_args(argv)
    receipt = build_receipt(_results(args.results))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(receipt.model_dump_json(indent=2, by_alias=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())
