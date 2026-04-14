"""Standardized artifact path resolution and manifest writing for GEPA optimization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DAYTONA_QUALITY_ROOT = Path("/home/daytona/memory/artifacts/quality")
LOCAL_QUALITY_ROOT = Path(".data/quality-artifacts")


def resolve_artifact_path(
    module_slug: str,
    filename: str,
    output_path: str | Path | None = None,
    default_root: str | Path | None = None,
) -> Path:
    """Resolve the artifact output path for a GEPA optimization run.

    When *output_path* is explicitly given it is returned as-is. When
    *default_root* is provided, artifacts are placed beneath that root first.
    Otherwise the helper checks for the Daytona-backed quality root and falls
    back to the local development path.

    This function is intended for **CLI/offline** use.  The API router layer
    constrains write paths via its own ``OPTIMIZATION_DATA_ROOT`` before
    calling the core optimizer.
    """
    if output_path is not None:
        return Path(output_path)

    if default_root is not None:
        return Path(default_root) / module_slug / filename

    root = (
        DAYTONA_QUALITY_ROOT / module_slug
        if DAYTONA_QUALITY_ROOT.exists()
        else LOCAL_QUALITY_ROOT / module_slug
    )
    return root / filename


def build_manifest(
    *,
    module_spec: str,
    dataset_path: str | Path,
    train_count: int,
    val_count: int,
    validation_score: float | None,
    optimizer: str = "GEPA",
    metric_name: str | None = None,
    auto: str | None = None,
) -> dict[str, Any]:
    """Build a canonical optimization manifest dict."""
    manifest: dict[str, Any] = {
        "dataset_path": str(dataset_path),
        "module": module_spec,
        "train_examples": train_count,
        "validation_examples": val_count,
        "validation_score": validation_score,
        "optimizer": optimizer,
    }
    if metric_name:
        manifest["metric"] = metric_name
    if auto:
        manifest["auto"] = auto
    return manifest


def write_manifest(manifest_path: str | Path, manifest_data: dict[str, Any]) -> Path:
    """Write *manifest_data* as pretty-printed JSON to *manifest_path*.

    Parent directories are created automatically.  Returns the resolved path.
    """
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest_data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
