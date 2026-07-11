from __future__ import annotations

import uuid

import pytest

from fleet_rlm.db.enums import DatasetFormat, DatasetSource
from fleet_rlm.db.repos.optimization import DatasetCreateRequest, DatasetReviewUpdate


@pytest.mark.integration
@pytest.mark.db
@pytest.mark.asyncio
async def test_dataset_version_review_and_approval_are_workspace_scoped_and_atomic(repository) -> None:
    suffix = uuid.uuid4().hex
    identity = await repository.upsert_identity(
        entra_tenant_id=f"phase8-tenant-{suffix}",
        entra_user_id=f"phase8-user-{suffix}",
        email=f"phase8-{suffix}@example.com",
    )
    assert identity.workspace_id is not None
    rows = [
        {"id": "train", "partition": "training"},
        {"id": "selection", "partition": "selection"},
        {"id": "test", "partition": "promotion_test"},
    ]
    dataset = await repository.create_dataset(
        DatasetCreateRequest(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            created_by_user_id=identity.user_id,
            name="phase8-managed",
            row_count=len(rows),
            format=DatasetFormat.JSON,
            source=DatasetSource.UPLOAD,
        ),
        examples=rows,
    )

    reviewed = await repository.review_dataset_version(
        tenant_id=identity.tenant_id,
        workspace_id=identity.workspace_id,
        dataset_id=dataset.id,
        reviewed_by_user_id=identity.user_id,
        update_request=DatasetReviewUpdate(consent_status="approved", redaction_status="approved"),
    )
    assert reviewed is not None

    approved = await repository.approve_dataset_version(
        tenant_id=identity.tenant_id,
        workspace_id=identity.workspace_id,
        dataset_id=dataset.id,
        approved_by_user_id=identity.user_id,
    )
    assert approved is not None
    assert approved.eligibility == "approved"
    assert approved.approved_at is not None

    with pytest.raises(ValueError, match="immutable"):
        await repository.review_dataset_version(
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            dataset_id=dataset.id,
            reviewed_by_user_id=identity.user_id,
            update_request=DatasetReviewUpdate(consent_status="rejected"),
        )
