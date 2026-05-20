"""Unit tests for the host-mediated evidence bridge.

Verifies:
- store_evidence / fetch_evidence / list_evidence skip gracefully without repository
- Correct delegation to MemoryRepository with proper tenant scoping
- Bridge tools are registered in bridge_callbacks.bridge_tools()
- Bridge tools are dispatched in bridge_callbacks.invoke_tool()
- _host_* attributes propagate to child interpreters
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fleet_rlm.integrations.daytona.isolation import (
    fetch_evidence,
    list_evidence,
    store_evidence,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _identity(
    *,
    tenant_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id=tenant_id or uuid.uuid4(),
        workspace_id=workspace_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
    )


def _interpreter(
    *,
    repository: Any = None,
    identity: Any = None,
    run_id: Any = None,
) -> MagicMock:
    interp = MagicMock()
    interp._host_repository = repository
    interp._host_identity = identity
    interp._host_run_id = run_id
    interp._tools = {}
    interp.llm_query = MagicMock(return_value="answer")
    interp.llm_query_batched = MagicMock(return_value=["a"])
    return interp


# ---------------------------------------------------------------------------
# Graceful skip without repository
# ---------------------------------------------------------------------------


class TestSkipWithoutRepository:
    def test_store_evidence_skips(self) -> None:
        interp = _interpreter()
        result = store_evidence(interp, key="k", content="v")
        assert result["status"] == "skipped"
        assert "no_repository" in result.get("reason", "")

    def test_fetch_evidence_skips(self) -> None:
        interp = _interpreter()
        result = fetch_evidence(interp)
        assert result["status"] == "skipped"
        assert result["items"] == []

    def test_list_evidence_skips(self) -> None:
        interp = _interpreter()
        result = list_evidence(interp)
        assert result["status"] == "skipped"
        assert result["items"] == []

    def test_store_evidence_skips_with_repo_but_no_identity(self) -> None:
        interp = _interpreter(repository=MagicMock(), identity=None)
        result = store_evidence(interp, key="k", content="v")
        assert result["status"] == "skipped"


# ---------------------------------------------------------------------------
# Delegation to MemoryRepository
# ---------------------------------------------------------------------------


class TestStoreEvidence:
    def test_calls_store_memory_item(self) -> None:
        identity = _identity()
        run_id = uuid.uuid4()
        mock_item = MagicMock()
        mock_item.id = uuid.uuid4()

        mock_repo = MagicMock()
        mock_repo.store_memory_item = AsyncMock(return_value=mock_item)

        interp = _interpreter(repository=mock_repo, identity=identity, run_id=run_id)

        with patch(
            "fleet_rlm.integrations.daytona.isolation._run_async_compat",
            return_value=mock_item,
        ):
            result = store_evidence(
                interp,
                key="child_a_result",
                content="analysis complete",
                kind="context",
                scope="run",
                tags=["pass:0"],
            )

        assert result["status"] == "ok"
        assert result["key"] == "child_a_result"
        assert "id" in result

    def test_handles_repository_error(self) -> None:
        identity = _identity()
        mock_repo = MagicMock()

        interp = _interpreter(repository=mock_repo, identity=identity)

        with patch(
            "fleet_rlm.integrations.daytona.isolation._run_async_compat",
            side_effect=RuntimeError("db connection failed"),
        ):
            result = store_evidence(interp, key="k", content="v")

        assert result["status"] == "error"
        assert "db connection failed" in result["error"]


class TestFetchEvidence:
    def test_returns_items(self) -> None:
        identity = _identity()
        mock_item = MagicMock()
        mock_item.id = uuid.uuid4()
        mock_item.scope_id = "child_result"
        mock_item.content_text = "the answer"
        mock_item.kind = MagicMock()
        mock_item.kind.value = "context"

        mock_repo = MagicMock()

        interp = _interpreter(repository=mock_repo, identity=identity)

        with patch(
            "fleet_rlm.integrations.daytona.isolation._run_async_compat",
            return_value=[mock_item],
        ):
            result = fetch_evidence(interp, scope="run", scope_id="child_result")

        assert result["status"] == "ok"
        assert len(result["items"]) == 1
        assert result["items"][0]["scope_id"] == "child_result"
        assert result["items"][0]["content"] == "the answer"


class TestListEvidence:
    def test_returns_metadata_only(self) -> None:
        identity = _identity()
        mock_item = MagicMock()
        mock_item.id = uuid.uuid4()
        mock_item.scope_id = "pass_0_output_0"
        mock_item.kind = MagicMock()
        mock_item.kind.value = "context"
        mock_item.importance = 5

        mock_repo = MagicMock()
        mock_repo.list_memory_items = MagicMock(return_value=object())
        interp = _interpreter(repository=mock_repo, identity=identity)

        def _fake_run_async_compat(fn, *args, **kwargs):
            fn(*args, **kwargs)
            return [mock_item]

        with patch(
            "fleet_rlm.integrations.daytona.isolation._run_async_compat",
            side_effect=_fake_run_async_compat,
        ):
            result = list_evidence(interp, scope="run")

        assert mock_repo.list_memory_items.call_args is not None
        assert mock_repo.list_memory_items.call_args.kwargs["user_id"] == identity.user_id

        assert result["status"] == "ok"
        assert len(result["items"]) == 1
        item = result["items"][0]
        assert "content" not in item
        assert item["scope_id"] == "pass_0_output_0"
        assert item["importance"] == 5


# ---------------------------------------------------------------------------
# Bridge registration
# ---------------------------------------------------------------------------


class TestBridgeRegistration:
    def test_evidence_tools_in_bridge_tools(self) -> None:
        from fleet_rlm.integrations.daytona.bridge_callbacks import bridge_tools

        interp = _interpreter()
        tools = bridge_tools(interp)

        assert "store_evidence" in tools
        assert "fetch_evidence" in tools
        assert "list_evidence" in tools

    def test_invoke_tool_dispatches_store_evidence(self) -> None:
        from fleet_rlm.integrations.daytona.bridge_callbacks import invoke_tool

        interp = _interpreter()

        with patch(
            "fleet_rlm.integrations.daytona.isolation.store_evidence",
            return_value={"status": "skipped", "reason": "no_repository"},
        ):
            result = invoke_tool(interp, "store_evidence", ["key", "content"], {})

        assert result["status"] == "skipped"

    def test_invoke_tool_dispatches_fetch_evidence(self) -> None:
        from fleet_rlm.integrations.daytona.bridge_callbacks import invoke_tool

        interp = _interpreter()

        with patch(
            "fleet_rlm.integrations.daytona.isolation.fetch_evidence",
            return_value={"status": "skipped", "items": []},
        ):
            result = invoke_tool(interp, "fetch_evidence", [], {"scope": "run"})

        assert result["status"] == "skipped"


# ---------------------------------------------------------------------------
# Host attribute propagation to children
# ---------------------------------------------------------------------------


class TestHostAttributePropagation:
    def test_propagate_parent_recursion_state_copies_host_refs(self) -> None:
        from fleet_rlm.integrations.daytona.isolation import (
            propagate_parent_recursion_state,
        )

        repo = MagicMock()
        identity = _identity()
        run_id = uuid.uuid4()

        parent = MagicMock()
        parent._sub_rlm_depth = 0
        parent._sub_rlm_max_depth = 2
        parent._check_and_increment_llm_calls = MagicMock()
        parent._remaining_llm_budget = MagicMock(return_value=50)
        parent._host_repository = repo
        parent._host_identity = identity
        parent._host_run_id = run_id

        child = MagicMock()
        child._sub_rlm_depth = 0
        child._sub_rlm_max_depth = 2

        propagate_parent_recursion_state(child, parent)

        assert child._host_repository is repo
        assert child._host_identity is identity
        assert child._host_run_id is run_id

    def test_propagation_skips_when_parent_has_no_host_refs(self) -> None:
        from fleet_rlm.integrations.daytona.isolation import (
            propagate_parent_recursion_state,
        )

        parent = SimpleNamespace(
            _sub_rlm_depth=0,
            _sub_rlm_max_depth=2,
            _check_and_increment_llm_calls=MagicMock(),
            _remaining_llm_budget=MagicMock(return_value=50),
        )
        child = SimpleNamespace(_sub_rlm_depth=0, _sub_rlm_max_depth=2)

        propagate_parent_recursion_state(child, parent)

        assert "_host_repository" not in child.__dict__
        assert "_host_identity" not in child.__dict__
        assert "_host_run_id" not in child.__dict__
