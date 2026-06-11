from __future__ import annotations

from unittest.mock import patch

from fleet_rlm.api import bootstrap_observability as module


def test_detect_local_mlflow_server_version_mismatch_returns_error() -> None:
    with (
        patch.object(module, "resolve_installed_mlflow_version", return_value="3.13.0"),
        patch.object(module, "fetch_mlflow_server_version", return_value="3.12.0"),
    ):
        error = module.detect_local_mlflow_server_version_mismatch(
            tracking_uri="http://127.0.0.1:5001",
            backend_store_uri="sqlite:///.data/mlruns.db",
        )

    assert error is not None
    assert "3.12.0" in error
    assert "3.13.0" in error
    assert "make mlflow-server" in error


def test_detect_local_mlflow_server_version_mismatch_ignores_matching_versions() -> None:
    with (
        patch.object(module, "resolve_installed_mlflow_version", return_value="3.13.0"),
        patch.object(module, "fetch_mlflow_server_version", return_value="3.13.0"),
    ):
        error = module.detect_local_mlflow_server_version_mismatch(
            tracking_uri="http://127.0.0.1:5001",
            backend_store_uri="sqlite:///.data/mlruns.db",
        )

    assert error is None
