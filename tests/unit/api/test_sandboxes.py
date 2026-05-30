from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_list_sandboxes_falls_back_to_client_side_label_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.api.runtime_services.sandboxes import load_sandbox_list
    from fleet_rlm.utils.sandbox_ownership import SANDBOX_OWNER_LABEL

    calls: list[dict[str, Any]] = []

    class FakeClient:
        def list(self, *, page: int, limit: int) -> list[Any]:
            calls.append({"page": page, "limit": limit})
            return [
                SimpleNamespace(
                    id="owned",
                    name="owned",
                    state="started",
                    labels={SANDBOX_OWNER_LABEL: "tenant:user"},
                ),
                SimpleNamespace(
                    id="other",
                    name="other",
                    state="started",
                    labels={SANDBOX_OWNER_LABEL: "tenant:other"},
                ),
            ]

    async def fake_close(client: Any) -> None:
        _ = client

    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.sandboxes._build_daytona_client",
        lambda: FakeClient(),
    )
    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.sandboxes._close_daytona_client",
        fake_close,
    )

    response = await load_sandbox_list(
        page=1,
        limit=100,
        owner_labels={SANDBOX_OWNER_LABEL: "tenant:user"},
    )

    assert calls == [{"page": 1, "limit": 100}]
    assert [item.id for item in response.items] == ["owned"]


@pytest.mark.asyncio
async def test_list_sandboxes_supports_sdk_without_pagination_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.api.runtime_services.sandboxes import load_sandbox_list
    from fleet_rlm.utils.sandbox_ownership import SANDBOX_OWNER_LABEL

    calls = 0

    class FakeClient:
        def list(self) -> list[Any]:
            nonlocal calls
            calls += 1
            return [
                SimpleNamespace(
                    id="owned",
                    name="owned",
                    state="started",
                    labels={SANDBOX_OWNER_LABEL: "tenant:user"},
                )
            ]

    async def fake_close(client: Any) -> None:
        _ = client

    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.sandboxes._build_daytona_client",
        lambda: FakeClient(),
    )
    monkeypatch.setattr("fleet_rlm.api.runtime_services.sandboxes._close_daytona_client", fake_close)

    response = await load_sandbox_list(
        page=2,
        limit=1,
        owner_labels={SANDBOX_OWNER_LABEL: "tenant:user"},
    )

    assert calls == 1
    assert [item.id for item in response.items] == ["owned"]


@pytest.mark.asyncio
async def test_sandbox_service_maps_generic_daytona_error_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    from daytona import DaytonaError
    from fastapi import HTTPException

    from fleet_rlm.api.runtime_services.sandbox_service import SandboxService

    async def fail_list(**kwargs: Any) -> Any:
        _ = kwargs
        raise DaytonaError("list failed")

    monkeypatch.setattr("fleet_rlm.api.runtime_services.sandboxes.load_sandbox_list", fail_list)

    with pytest.raises(HTTPException) as exc_info:
        await SandboxService().list_sandboxes(
            page=1,
            limit=100,
            tenant_claim="tenant",
            user_claim="user",
        )

    assert exc_info.value.status_code == 503
    assert "Sandbox service unavailable" in str(exc_info.value.detail)
