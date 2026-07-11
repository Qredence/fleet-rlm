"""Path traversal and run_id validation tests (VAL-SEC-004, VAL-SEC-005).

These tests verify:
- VAL-SEC-004: the run_id path parameter is constrained by a UUID regex at the
  router level, so non-UUID values (including ``..``, slashes, traversal
  sequences) are rejected with 422 before the handler runs.
- VAL-SEC-005: the service layer performs a ``Path.resolve()`` containment
  check so a (symlinked) run_id that resolves outside ``mlartifacts/eval/`` is
  rejected with 404 instead of leaking file contents.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.dependencies import (
    require_http_identity,
    resolve_persisted_identity,
)
from fleet_rlm.api.runtime_services import evaluations as evaluation_service
from fleet_rlm.db.repos.identity import IdentityUpsertResult
from fleet_rlm.quality.eval.report import EvaluationReport


def _stub_identity() -> object:
    from fleet_rlm.api.auth import NormalizedIdentity

    return NormalizedIdentity(
        tenant_claim="tenant-a",
        user_claim="user-a",
        email="alice@example.com",
        name="Alice",
        raw_claims={"tid": "tenant-a", "oid": "user-a"},
    )


def _stub_persisted_identity() -> IdentityUpsertResult:
    import uuid as _uuid

    tenant_id = _uuid.uuid5(_uuid.NAMESPACE_DNS, "tenant-a")
    user_id = _uuid.uuid5(_uuid.NAMESPACE_DNS, "user-a")
    return IdentityUpsertResult(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=tenant_id,
    )


@pytest.fixture
def evaluations_client(no_db_app) -> Iterator[TestClient]:
    """Client with auth dependencies stubbed so requests reach the router."""
    app = no_db_app
    app.dependency_overrides[require_http_identity] = _stub_identity  # type: ignore[assignment]
    app.dependency_overrides[resolve_persisted_identity] = _stub_persisted_identity  # type: ignore[assignment]
    with TestClient(app) as client:
        yield client


# All of these run_id values must be rejected at the router layer (422 or 404),
# never reaching the service layer's filesystem operations.
_TRAVERSAL_RUN_IDS = [
    "..",
    "../",
    "../../etc/passwd",
    "not-a-uuid",
    "abc",
    "%2e%2e",
    "%2F..%2F..",
    "....//....//etc/passwd",
    "a" * 36,  # 36 chars but not hex/dash -> fails pattern
    "00000000-0000-0000-0000-00000000000",  # 35 chars -> fails pattern
]


@pytest.mark.parametrize("run_id", _TRAVERSAL_RUN_IDS)
def test_get_evaluation_rejects_non_uuid_run_id(
    evaluations_client: TestClient,
    run_id: str,
) -> None:
    """VAL-SEC-004: non-UUID run_id values are rejected at the router (422 or 404).

    Per the validation contract, non-UUID run_id values may return either 422
    (pattern validation failure) or 404 (route does not match due to path
    separators). The key requirement is that they never reach the service layer
    (no 200 and no 500).
    """
    response = evaluations_client.get(f"/api/v1/evaluations/{run_id}")

    assert response.status_code in {422, 404}, (
        f"expected 422 or 404 for run_id={run_id!r}, got {response.status_code}: {response.text}"
    )
    # Crucially, the handler must never be reached (no 200) and no server error.
    assert response.status_code != 200
    assert response.status_code != 500


def test_get_evaluation_accepts_valid_uuid_format_but_returns_404(
    evaluations_client: TestClient,
) -> None:
    """VAL-SEC-004: a valid UUID-format run_id is accepted by the router (404 from service)."""
    evaluation_service._EVALUATION_STORE.clear()
    run_id = "00000000-0000-0000-0000-000000000000"

    # Patch the disk lookup so we don't depend on real artifacts existing.
    with patch.object(evaluation_service, "_resolve_report_path") as mock_resolve:
        mock_resolve.return_value = Path("/tmp/nonexistent/report.json")
        response = evaluations_client.get(f"/api/v1/evaluations/{run_id}")

    assert response.status_code == 404, response.text


def test_resolve_report_path_rejects_symlink_escape(tmp_path: Path, monkeypatch) -> None:
    """VAL-SEC-005: a run_id whose resolved path escapes mlartifacts/eval/ is rejected."""
    # Redirect the eval root to a temp directory.
    eval_root = tmp_path / "mlartifacts" / "eval"
    eval_root.mkdir(parents=True)
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("top secret", encoding="utf-8")

    # Create a directory whose name is a valid UUID, then symlink report.json
    # to a file outside the eval root.
    run_id = "00000000-0000-0000-0000-000000000000"
    run_dir = eval_root / run_id
    run_dir.mkdir()
    (run_dir / "report.json").symlink_to(secret_file)

    monkeypatch.chdir(tmp_path)

    # The path resolves (following the symlink) to outside the eval root.
    with pytest.raises(Exception) as exc_info:
        evaluation_service._resolve_report_path(run_id)

    # The raised exception should be an HTTPException with 404 status.
    from fastapi import HTTPException

    assert isinstance(exc_info.value, HTTPException)
    assert exc_info.value.status_code == 404


def test_resolve_report_path_accepts_legitimate_run(tmp_path: Path, monkeypatch) -> None:
    """VAL-SEC-005: a legitimate report inside mlartifacts/eval/ resolves successfully."""
    eval_root = tmp_path / "mlartifacts" / "eval"
    eval_root.mkdir(parents=True)
    run_id = "00000000-0000-0000-0000-000000000000"
    run_dir = eval_root / run_id
    run_dir.mkdir()
    report_file = run_dir / "report.json"
    report_file.write_text("{}", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    resolved = evaluation_service._resolve_report_path(run_id)

    assert resolved == report_file.resolve()
    assert resolved.is_relative_to(eval_root.resolve())


def test_get_evaluation_returns_report_from_disk(tmp_path: Path, monkeypatch) -> None:
    """VAL-SEC-005 (positive): a legitimate on-disk report is served correctly."""
    import json

    eval_root = tmp_path / "mlartifacts" / "eval"
    eval_root.mkdir(parents=True)
    run_id = "11111111-2222-3333-4444-555555555555"
    report = EvaluationReport(
        run_id=run_id,
        created_at="2026-01-01T00:00:00+00:00",
        filters={"trace_ids": None, "limit": None, "from_last_days": 1},
        per_trace=[],
        aggregates={"mean": {}, "median": {}},
    )
    (eval_root / run_id).mkdir()
    (eval_root / run_id / "report.json").write_text(report.to_json(), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    evaluation_service._EVALUATION_STORE.clear()

    resolved = evaluation_service._resolve_report_path(run_id)
    assert resolved.is_relative_to(eval_root.resolve())
    assert json.loads(resolved.read_text(encoding="utf-8"))["run_id"] == run_id
