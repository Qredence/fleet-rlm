from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from fleet_rlm.api.routers.optimization import datasets
from fleet_rlm.api.schemas.optimization import DatasetReviewRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [datasets.review_dataset_version, datasets.approve_dataset_version])
async def test_managed_dataset_lifecycle_rejects_local_persistence(endpoint) -> None:
    kwargs = {
        "config_deps": SimpleNamespace(),
        "identity": SimpleNamespace(),
        "persistence": SimpleNamespace(supports_managed_dataset_versions=False),
        "dataset_id": str(uuid.uuid4()),
    }
    if endpoint is datasets.review_dataset_version:
        kwargs["request"] = DatasetReviewRequest(consent_status="approved")

    with pytest.raises(HTTPException) as exc_info:
        await endpoint(**kwargs)

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_review_dataset_version_returns_updated_lifecycle_metadata(monkeypatch) -> None:
    dataset_id = uuid.uuid4()
    user_id = uuid.uuid4()
    identity = SimpleNamespace(tenant_id=uuid.uuid4(), workspace_id=uuid.uuid4(), user_id=user_id)
    row = SimpleNamespace(
        id=dataset_id,
        name="quality-set",
        row_count=3,
        format=SimpleNamespace(value="json"),
        metadata_json={"module_slug": "plan-code-change"},
        module_slug="plan-code-change",
        version=2,
        supersedes_dataset_id=uuid.uuid4(),
        eligibility="draft",
        consent_status="approved",
        redaction_status="unreviewed",
        content_sha256="a" * 64,
        approved_at=None,
        approved_by_user_id=None,
        created_at=datetime.now(UTC),
    )
    persistence = SimpleNamespace(
        supports_managed_dataset_versions=True,
        review_dataset_version=AsyncMock(return_value=row),
    )
    monkeypatch.setattr(datasets, "_resolve_persisted_identity", AsyncMock(return_value=identity))

    response = await datasets.review_dataset_version(
        config_deps=SimpleNamespace(),
        identity=SimpleNamespace(),
        persistence=persistence,
        dataset_id=str(dataset_id),
        request=DatasetReviewRequest(consent_status="approved"),
    )

    assert response.version == 2
    assert response.consent_status == "approved"
    assert response.content_sha256 == "a" * 64
