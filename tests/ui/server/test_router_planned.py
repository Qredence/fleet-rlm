from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    ("method", "path", "expected_detail_fragment"),
    [
        ("get", "/api/v1/analytics", "analytics endpoint not yet implemented"),
        (
            "get",
            "/api/v1/analytics/skills/test-skill",
            "analytics skill endpoint not yet implemented",
        ),
        ("get", "/api/v1/taxonomy", "taxonomy endpoint not yet implemented"),
        (
            "get",
            "/api/v1/taxonomy/a/b/c",
            "taxonomy path endpoint not yet implemented",
        ),
        ("get", "/api/v1/search", "search endpoint not yet implemented"),
        ("get", "/api/v1/memory", "memory list endpoint not yet implemented"),
        (
            "post",
            "/api/v1/memory",
            "memory create endpoint not yet implemented",
        ),
        ("get", "/api/v1/sandbox", "sandbox endpoint not yet implemented"),
        (
            "get",
            "/api/v1/sandbox/file",
            "sandbox file endpoint not yet implemented",
        ),
    ],
)
def test_planned_routes_return_501(
    local_client: TestClient,
    method: str,
    path: str,
    expected_detail_fragment: str,
) -> None:
    response = getattr(local_client, method)(path)

    assert response.status_code == 501
    payload = response.json()
    assert payload["detail"] == expected_detail_fragment
