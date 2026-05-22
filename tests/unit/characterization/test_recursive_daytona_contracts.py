"""Characterization tests for recursive RLM, Daytona VFS, and evidence contracts."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from fleet_rlm.api.runtime_services.volumes import normalize_volume_file_path, normalize_volume_tree_path
from fleet_rlm.integrations.daytona.isolation import fetch_evidence, list_evidence, store_evidence
from fleet_rlm.runtime.tools import rlm_delegate as rlm_delegate_mod


class _FakeSession:
    def __init__(self) -> None:
        self.repo_url: str | None = None
        self.write_calls: list[tuple[str, str]] = []

    def write_file(self, path: str, content: str) -> str:
        self.write_calls.append((path, content))
        return f"/workspace/repo/{path}"


class _FakeChild:
    def __init__(self) -> None:
        self._started = False
        self.start_calls = 0
        self.shutdown_calls = 0
        self.repo_url: str | None = None
        self.rlm_max_iterations = 20
        self.sub_lm = None
        self.verbose = False
        self.session = _FakeSession()
        self.child_isolation_metadata: dict[str, Any] = {
            "mode": "auto",
            "strategy": "clean",
            "child_sandbox_id": "child-sandbox",
        }

    def start(self) -> None:
        self.start_calls += 1
        self._started = True

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self._started = False

    def _ensure_session_sync(self) -> _FakeSession:
        return self.session


class _FakeParent:
    def __init__(self, *, remaining: int, children: list[_FakeChild] | None = None) -> None:
        self.remaining = remaining
        self.children = children or [_FakeChild()]
        self.build_calls: list[int] = []
        self.lease_calls: list[int] = []

    def _remaining_llm_budget(self) -> int:
        return self.remaining

    def build_delegate_child(self, *, remaining_llm_budget: int) -> _FakeChild:
        self.build_calls.append(remaining_llm_budget)
        return self.children[min(len(self.build_calls) - 1, len(self.children) - 1)]

    def _install_child_budget_lease(self, child: _FakeChild, lease: int) -> None:
        self.lease_calls.append(lease)
        child.max_llm_calls = lease
        child.child_isolation_metadata["llm_budget_lease"] = lease


def test_delegate_to_rlm_exhausted_budget_short_circuits_before_child_side_effects() -> None:
    """Current host delegation returns a structured budget error without constructing a child."""
    parent = _FakeParent(remaining=0)

    result = rlm_delegate_mod.delegate_to_rlm("summarize", interpreter=parent)

    assert result == {
        "status": "error",
        "reason": "budget_exhausted",
        "error": "LLM call budget exhausted - cannot spawn delegate_to_rlm child.",
    }
    assert parent.build_calls == []
    assert parent.lease_calls == []


def test_delegate_to_rlm_batched_preserves_input_order_and_leases_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Current host batched delegation returns successes in input order despite out-of-order completion."""
    parent = _FakeParent(remaining=7)
    calls: list[tuple[str, int]] = []

    def _fake_run_child(
        interpreter: Any,
        query: str,
        context: str,
        document_url: str | None,
        llm_budget: int,
    ) -> dict[str, str]:
        assert interpreter is parent
        assert context == "shared"
        assert document_url is None
        calls.append((query, llm_budget))
        if query == "slow":
            time.sleep(0.03)
        return {"status": "ok", "answer": f"answer:{query}"}

    monkeypatch.setattr(rlm_delegate_mod, "_run_delegate_child", _fake_run_child)

    result = rlm_delegate_mod.delegate_to_rlm_batched(
        ["slow", "fast-a", "fast-b"],
        context="shared",
        interpreter=parent,
    )

    assert result == {
        "status": "ok",
        "results": [
            {"query": "slow", "answer": "answer:slow"},
            {"query": "fast-a", "answer": "answer:fast-a"},
            {"query": "fast-b", "answer": "answer:fast-b"},
        ],
    }
    assert sorted(lease for _, lease in calls) == [2, 2, 3]


def test_delegate_to_rlm_batched_rejects_blank_legacy_inputs_without_spawning_children() -> None:
    """Blank batched child queries are a structured invalid-query result, not silent no-ops."""
    parent = _FakeParent(remaining=10)

    result = rlm_delegate_mod.delegate_to_rlm_batched(["ok", "  "], interpreter=parent)

    assert result["status"] == "error"
    assert result["reason"] == "invalid_query"
    assert result["results"] == []
    assert result["errors"][0]["index"] == 1
    assert parent.build_calls == []


def test_delegate_local_workspace_context_is_staged_as_explicit_child_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local host context is represented by a curated snapshot file, not implicit filesystem sharing."""
    project_root = tmp_path / "project"
    source_dir = project_root / "src" / "fleet_rlm"
    source_dir.mkdir(parents=True)
    (project_root / "pyproject.toml").write_text("[project]\nname='example'\n")
    (source_dir / "runtime.py").write_text("def delegate_to_rlm():\n    return 'current behavior'\n")
    monkeypatch.chdir(project_root)
    child = _FakeChild()

    resolved = rlm_delegate_mod._resolve_delegate_context(
        child=child,
        query="Inspect delegate_to_rlm implementation in this codebase",
        base_context="base context",
        document_url=None,
    )

    assert "Local workspace snapshot" in resolved
    assert "artifacts/rlm-inputs/local_workspace_snapshot.txt" in resolved
    assert child.start_calls == 1
    assert child.session.write_calls[0][0] == "artifacts/rlm-inputs/local_workspace_snapshot.txt"
    assert "src/fleet_rlm/runtime.py" in child.session.write_calls[0][1]
    assert "current behavior" in child.session.write_calls[0][1]


@pytest.mark.parametrize("path", ["memory/note.txt", "/artifacts/out.txt"])
def test_volume_path_normalization_accepts_current_allowed_shapes(path: str) -> None:
    """Volume path helpers normalize relative Daytona paths while preserving absolute canonical paths."""
    assert normalize_volume_file_path(path).startswith("/")
    assert normalize_volume_tree_path(path).startswith("/")


def test_volume_file_path_normalization_rejects_root_without_file() -> None:
    """File reads require a canonical volume root descendant instead of implicit volume root aliases."""
    with pytest.raises(HTTPException) as exc_info:
        normalize_volume_file_path("/")

    assert exc_info.value.status_code == 403


@pytest.mark.parametrize("path", ["../secrets", "/memory/../meta", "artifacts/%2e%2e/secret"])
def test_volume_path_normalization_rejects_literal_traversal_segments(path: str) -> None:
    """Literal traversal is rejected before Daytona VFS operations are invoked."""
    decoded = path.replace("%2e", ".")
    with pytest.raises(HTTPException) as exc_info:
        normalize_volume_file_path(decoded)
    assert exc_info.value.status_code == 400

    with pytest.raises(HTTPException) as tree_exc_info:
        normalize_volume_tree_path(decoded)
    assert tree_exc_info.value.status_code == 400


def test_evidence_store_fetch_and_list_use_host_identity_without_exposing_repository_handles() -> None:
    """Evidence bridge persists through host repo context and returns sandbox-safe payload shapes."""
    identity = SimpleNamespace(tenant_id=uuid.uuid4(), workspace_id=uuid.uuid4(), user_id=uuid.uuid4())
    run_id = uuid.uuid4()
    item_id = uuid.uuid4()
    repo = MagicMock()
    repo.store_memory_item = AsyncMock(return_value=SimpleNamespace(id=item_id))
    stored_item = SimpleNamespace(
        id=item_id,
        scope_id="child-key",
        content_text="child evidence",
        kind=SimpleNamespace(value="context"),
        importance=5,
    )
    repo.list_memory_items = AsyncMock(return_value=[stored_item])
    interpreter = SimpleNamespace(_host_repository=repo, _host_identity=identity, _host_run_id=run_id)

    with patch("fleet_rlm.integrations.daytona.isolation._run_async_compat") as run_async:
        run_async.side_effect = [SimpleNamespace(id=item_id), [stored_item], [stored_item]]

        stored = store_evidence(interpreter, key="child-key", content="child evidence", tags=["characterization"])
        fetched = fetch_evidence(interpreter, scope="run", scope_id="child-key")
        listed = list_evidence(interpreter, scope="run")

    assert stored == {"status": "ok", "id": str(item_id), "key": "child-key"}
    assert fetched["items"] == [
        {
            "id": str(item_id),
            "scope_id": "child-key",
            "content": "child evidence",
            "kind": "context",
        }
    ]
    assert listed["items"] == [
        {
            "id": str(item_id),
            "scope_id": "child-key",
            "kind": "context",
            "importance": 5,
        }
    ]
    assert "content" not in listed["items"][0]
    create_request = run_async.call_args_list[0].args[1]
    assert create_request.tenant_id == identity.tenant_id
    assert create_request.workspace_id == identity.workspace_id
    assert create_request.user_id == identity.user_id
    assert create_request.run_id == run_id
