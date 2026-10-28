"""Offline checks for the live verifier's failure reporting and cleanup."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("optimization", [0, 2])
def test_failed_health_check_stops_work_reports_failure_and_cleans_schema(tmp_path, monkeypatch, optimization):
    source = Path(__file__).parents[1] / "scripts/verify_runtime_live.py"
    verifier = ModuleType("runtime_verifier_test")
    exec(compile(source.read_text(), str(source), "exec", optimize=optimization), vars(verifier))
    settings = SimpleNamespace(
        fleet_database_url="postgresql://app@localhost/db",
        fleet_database_migration_url="postgresql://app@localhost/db",
        fleet_daytona_api_key="placeholder",
        fleet_daytona_org_id="placeholder",
        fleet_daytona_volume_id="placeholder",
    )
    settings.with_explicit_overrides = MagicMock(return_value=settings)
    monkeypatch.setattr(verifier, "FleetSettings", lambda: settings)
    provider = MagicMock()
    provider.list.return_value = []
    monkeypatch.setattr(verifier, "Daytona", MagicMock(return_value=provider))
    monkeypatch.setattr(verifier, "DaytonaConfig", MagicMock())
    connect = MagicMock()
    monkeypatch.setattr(verifier.psycopg, "connect", connect)
    monkeypatch.setattr(verifier, "create_app", MagicMock())
    client = MagicMock()
    client.get.return_value.status_code = 503
    test_client = MagicMock()
    test_client.return_value.__enter__.return_value = client
    monkeypatch.setattr(verifier, "TestClient", test_client)
    monkeypatch.setattr("sys.argv", [str(source), "--live", "--model", "test/offline"])
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit, match="Runtime acceptance failed"):
        verifier.main()

    client.get.assert_called_once_with("/health")
    client.post.assert_not_called()
    # Both schema creation and cleanup happen, even after the first check fails.
    assert connect.call_count == 2
    statements = connect.return_value.__enter__.return_value.execute.call_args_list
    assert "CREATE SCHEMA" in str(statements[0])
    assert "DROP SCHEMA" in str(statements[-1])
    (report_path,) = (tmp_path / ".scratch/runtime-validation").glob("*.json")
    report = json.loads(report_path.read_text())
    assert report["status"] == "failed"
    assert report["error_type"] == "RuntimeError"
    assert report["schema_cleanup"] == "dropped"
    assert report["remaining_sandboxes"] == []
