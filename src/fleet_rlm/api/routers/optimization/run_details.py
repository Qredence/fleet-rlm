"""API helpers for GEPA optimization run details and promotion drafts."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from fleet_rlm.quality.optimization_report import build_optimization_run_detail

from ...schemas.optimization import (
    OptimizationPromotionDraftResponse,
    OptimizationRunResponse,
)
from ._deps import OPTIMIZATION_DATA_ROOT

__all__ = ["build_optimization_run_detail", "create_or_load_promotion_draft"]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "run"


def create_or_load_promotion_draft(
    run: OptimizationRunResponse,
    *,
    tenant_id: str,
    workspace_id: str,
) -> OptimizationPromotionDraftResponse:
    """Create or load a non-mutating draft promotion artifact for a run."""
    draft_id = f"promotion-draft-{_slugify(run.id)}"
    draft_root = (
        OPTIMIZATION_DATA_ROOT
        / ".data"
        / "quality-artifacts"
        / "promotion-drafts"
        / _slugify(tenant_id)
        / _slugify(workspace_id)
    )
    draft_path = draft_root / f"{draft_id}.json"
    if draft_path.is_file():
        try:
            payload = json.loads(draft_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return OptimizationPromotionDraftResponse.model_validate(payload)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            pass

    now = datetime.now(UTC).isoformat()
    target = run.program_spec or run.module_slug or run.id
    summary = (
        f"Draft promotion for {target}. This records the optimized artifact for review "
        "and does not mutate bundled skills or live runtime prompts."
    )
    response = OptimizationPromotionDraftResponse(
        draft_id=draft_id,
        run_id=run.id,
        target=target,
        summary=summary,
        optimized_artifact_path=run.output_path,
        manifest_path=run.manifest_path,
        draft_path=str(draft_path),
        created_at=now,
    )
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(json.dumps(response.model_dump(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return response
