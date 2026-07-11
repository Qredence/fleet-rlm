from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from fleet_rlm.api.routers.optimization.managed_resolution import (
    effective_wall_clock_seconds,
    require_metric_profile,
    resolve_approved_dataset,
)
from fleet_rlm.quality.dataset_versions import canonical_dataset_sha256
from fleet_rlm.quality.module_registry import MetricProfile, ModuleOptimizationSpec


def _spec(profile: MetricProfile | None) -> ModuleOptimizationSpec:
    return ModuleOptimizationSpec(
        module_slug="managed",
        label="Managed",
        program_spec="package:Program",
        artifact_filename="artifact.json",
        input_keys=[],
        required_dataset_keys=[],
        module_factory=lambda: object(),
        row_converter=lambda rows: rows,
        metric_builder=lambda: object(),
        metric_profile=profile,
    )


def test_require_metric_profile_accepts_exact_qualified_id() -> None:
    profile = MetricProfile(profile_id="managed-quality", version="2")

    assert require_metric_profile(_spec(profile), "managed-quality@2") is profile


def test_require_metric_profile_rejects_missing_or_mismatched_profile() -> None:
    with pytest.raises(HTTPException, match="does not expose"):
        require_metric_profile(_spec(None), "managed-quality@1")

    with pytest.raises(HTTPException, match="does not match"):
        require_metric_profile(_spec(MetricProfile(profile_id="managed-quality")), "other@1")


def test_effective_wall_clock_uses_process_timeout_as_ceiling() -> None:
    assert effective_wall_clock_seconds(requested_seconds=120, process_ceiling_seconds=900) == 120
    assert effective_wall_clock_seconds(requested_seconds=3_600, process_ceiling_seconds=900) == 900


@pytest.mark.asyncio
async def test_resolve_approved_dataset_rejects_draft_version(tmp_path) -> None:
    path = tmp_path / "dataset.json"
    rows = [{"id": "train", "partition": "training"}]
    path.write_text(json.dumps(rows), encoding="utf-8")
    dataset_id = uuid.uuid4()
    persistence = SimpleNamespace(
        supports_managed_dataset_versions=True,
        get_dataset=AsyncMock(
            return_value=SimpleNamespace(
                id=dataset_id,
                eligibility="draft",
                uri=str(path),
                content_sha256=canonical_dataset_sha256(rows),
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await resolve_approved_dataset(
            SimpleNamespace(dataset_version_id=str(dataset_id)),
            persistence=persistence,
            persisted_identity=SimpleNamespace(tenant_id=uuid.uuid4(), workspace_id=uuid.uuid4(), user_id=uuid.uuid4()),
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_resolve_approved_dataset_rejects_digest_mismatch(tmp_path) -> None:
    path = tmp_path / "dataset.json"
    rows = [
        {"id": "train", "partition": "training"},
        {"id": "selection", "partition": "selection"},
        {"id": "test", "partition": "promotion_test"},
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")
    dataset_id = uuid.uuid4()
    persistence = SimpleNamespace(
        supports_managed_dataset_versions=True,
        get_dataset=AsyncMock(
            return_value=SimpleNamespace(
                id=dataset_id,
                eligibility="approved",
                uri=str(path),
                content_sha256="0" * 64,
                metadata_json={"module_slug": "managed"},
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await resolve_approved_dataset(
            SimpleNamespace(dataset_version_id=str(dataset_id), target=SimpleNamespace(kind="module", id="managed")),
            persistence=persistence,
            persisted_identity=SimpleNamespace(tenant_id=uuid.uuid4(), workspace_id=uuid.uuid4(), user_id=uuid.uuid4()),
        )

    assert exc_info.value.status_code == 409
