"""Evaluate-job results: per-split summaries, the headline, and the installed program they measured."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from fleet_rlm.config import FleetSettings
from fleet_rlm.engine.compiled import CompiledProgramFile
from fleet_rlm.store import Store

__all__ = ["evaluation_result", "installed_digest", "installed_program", "rescore_scores", "split_summary"]


def installed_digest(settings: FleetSettings) -> str:
    """Digest of the installed global program file, or an empty string while the baseline serves."""
    path = Path(settings.compiled_program_dir) / "fleet.json"
    return hashlib.sha256(CompiledProgramFile(str(path)).read()).hexdigest() if path.exists() else ""


def installed_program(settings: FleetSettings, store: Store) -> dict[str, str]:
    """The program a result measured: its digest and the approved job that installed it."""
    digest = installed_digest(settings)
    version = "baseline"
    if digest:
        version = next(
            (
                job["run_id"]
                for job in store.list_optimization_runs()
                if job["kind"] == "optimize" and job["status"] == "approved" and job.get("manifest_digest") == digest
            ),
            "unrecorded",
        )
    return {"digest": digest, "version": version}


def split_summary(scores: Iterable[float], critical_scores: Iterable[float] = (), *, errors: int = 0) -> dict[str, Any]:
    """Mean score and critical pass count for one split; a critical example passes only at a perfect score."""
    values = [float(score) for score in scores]
    critical = [float(score) for score in critical_scores]
    return {
        "n": len(values),
        "mean": round(sum(values) / len(values), 4) if values else None,
        "errors": errors,
        "critical": {"passed": sum(score == 1.0 for score in critical), "n": len(critical)},
    }


def evaluation_result(
    kind: str, splits: Mapping[str, dict[str, Any]], program: dict[str, str], *, excluded: int = 0
) -> dict[str, Any]:
    """Persisted job result; the headline is the holdout mean and the critical pass rate across splits."""
    passed = sum(split["critical"]["passed"] for split in splits.values())
    critical = sum(split["critical"]["n"] for split in splits.values())
    return {
        "kind": kind,
        "program": program,
        # Rescore cannot re-judge an example whose Turn has no readable, approved-labelled trace.
        "excluded_without_trace": excluded,
        "splits": dict(splits),
        "headline": {
            "holdout": splits.get("holdout", {}).get("mean"),
            "critical_pass_rate": round(passed / critical, 4) if critical else None,
        },
    }


def rescore_scores(result: Any) -> tuple[list[float], list[float]]:
    """Per-trace Fleet metric values from an MLflow evaluation, and those of its critical examples."""
    frame = result.result_df
    if "fleet_metric/value" not in frame:
        return [], []
    expectations = frame["expectations"] if "expectations" in frame else [None] * len(frame)
    scores, critical = [], []
    for value, expected in zip(frame["fleet_metric/value"], expectations, strict=True):
        if value is None or value != value:
            continue
        scores.append(float(value))
        if isinstance(expected, dict) and expected.get("critical") is True:
            critical.append(float(value))
    return scores, critical
