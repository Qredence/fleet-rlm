from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.persistence.preflight import ManagedDatabasePreflightError
from scripts import database


def test_tracking_uri_defaults_to_the_selected_runtime_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fleet_rlm.config.loader.load_runtime_settings",
        lambda: SimpleNamespace(mlflow_tracking_uri="http://127.0.0.1:5001"),
    )

    assert database.preflight__resolve_mlflow_tracking_uri(None) == "http://127.0.0.1:5001"


def test_tracking_uri_rejects_blank_value() -> None:
    with pytest.raises(ManagedDatabasePreflightError, match="tracking URI"):
        database.preflight__resolve_mlflow_tracking_uri("  ")


def test_main_fails_closed_when_storage_separation_is_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fleet_app:p@lakebase.example/fleet?sslmode=require")
    monkeypatch.setattr(database, "preflight__resolve_mlflow_tracking_uri", lambda _value: "databricks")
    monkeypatch.setattr(database, "inspect_managed_postgres", _unknown_observation, raising=False)

    receipt = tmp_path / "preflight.json"
    assert (
        database.main(
            ["preflight", "--receipt", str(receipt), "--target", "lakebase-test", "--database-url-env", "DATABASE_URL"]
        )
        == 2
    )
    assert not receipt.exists()


async def _unknown_observation(*_args, **_kwargs):
    return SimpleNamespace(mlflow_storage_separate=None, as_dict=lambda: {"mlflow_storage_separate": None})
