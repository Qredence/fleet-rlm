from __future__ import annotations

import pytest
from fastapi import HTTPException


def test_volume_service_accepts_all_daytona_canonical_roots() -> None:
    from fleet_rlm.api.runtime_services.volumes import CANONICAL_VOLUME_ROOTS, normalize_volume_tree_path
    from fleet_rlm.integrations.daytona.volumes import VFS_CANONICAL_ROOTS

    assert set(CANONICAL_VOLUME_ROOTS) == VFS_CANONICAL_ROOTS
    for root in VFS_CANONICAL_ROOTS:
        assert normalize_volume_tree_path(root) == root


def test_volume_service_rejects_unknown_root() -> None:
    from fleet_rlm.api.runtime_services.volumes import normalize_volume_tree_path

    with pytest.raises(HTTPException) as exc_info:
        normalize_volume_tree_path("/tmp")

    assert exc_info.value.status_code == 403
