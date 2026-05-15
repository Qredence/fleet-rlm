"""Unit tests for SessionService behavior and edge cases."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.api.runtime_services.session_service import SessionService
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult

pytestmark = pytest.mark.unit


def _identity() -> IdentityUpsertResult:
    return IdentityUpsertResult(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        tenant_status="active",  # type: ignore[arg-type]
        membership_role="member",  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_list_sessions_uses_first_turn_title_for_placeholder_session_titles() -> None:
    """Verify list_sessions replaces placeholder titles with first turn user message."""
    identity = _identity()
    session_id = uuid.uuid4()
    created_at = SimpleNamespace(isoformat=lambda: "2026-05-15T00:00:00+00:00")
    updated_at = SimpleNamespace(isoformat=lambda: "2026-05-15T01:00:00+00:00")
    calls: list[tuple[str, object]] = []

    class Persistence:
        async def list_chat_sessions(self, **kwargs: Any) -> tuple[list[SimpleNamespace], int]:
            return (
                [
                    SimpleNamespace(
                        id=session_id,
                        title=str(session_id),
                        status=SimpleNamespace(value="active"),
                        model_name="gpt-test",
                        metadata_json={"external_session_id": str(session_id)},
                        created_at=created_at,
                        updated_at=updated_at,
                    )
                ],
                1,
            )

        async def list_chat_turns(self, session_id: Any, **kwargs: Any) -> tuple[list[SimpleNamespace], int]:
            calls.append(("list_chat_turns", kwargs.get("session_id", session_id)))
            return ([SimpleNamespace(user_message="Show me the prior auth debugging conversation")], 1)

        async def update_chat_session(self, **kwargs: Any) -> None:
            calls.append(("update_chat_session", kwargs["title"]))
            return None

    response = await SessionService(Persistence()).list_sessions(persisted_identity=identity)

    assert response.items[0].title == "Show me the prior auth debugging conversation"
    assert calls == [
        ("list_chat_turns", session_id),
        ("update_chat_session", "Show me the prior auth debugging conversation"),
    ]


@pytest.mark.asyncio
async def test_get_session_detail_preserves_explicit_human_title() -> None:
    """Verify get_session_detail preserves explicit human-assigned session titles."""
    identity = _identity()
    session_id = uuid.uuid4()
    created_at = SimpleNamespace(isoformat=lambda: "2026-05-15T00:00:00+00:00")
    updated_at = SimpleNamespace(isoformat=lambda: "2026-05-15T01:00:00+00:00")

    class Persistence:
        async def get_chat_session(self, **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                id=session_id,
                title="Investigate auth cache invalidation",
                status=SimpleNamespace(value="active"),
                model_name="gpt-test",
                metadata_json={"external_session_id": str(session_id)},
                workspace_id=identity.workspace_id,
                created_at=created_at,
                updated_at=updated_at,
            )

        async def list_chat_turns(self, **kwargs: Any) -> tuple[list[SimpleNamespace], int]:
            return ([SimpleNamespace(user_message="ignored")], 1)

    detail = await SessionService(Persistence()).get_session_detail(
        persisted_identity=identity,
        session_id=str(session_id),
    )

    assert detail.title == "Investigate auth cache invalidation"
