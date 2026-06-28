from __future__ import annotations

import pytest

import fleet_rlm.integrations.daytona.concurrency as concurrency


@pytest.fixture(autouse=True)
def reset_concurrency_globals(monkeypatch) -> None:
    """Reset global sandbox semaphore state and disable external services.

    This ensures that leaked semaphore state from preceding tests does not
    trigger real Daytona API network calls, and that MLflow / PostHog are
    disabled by default so they don't make outbound tracking connections.
    """
    monkeypatch.setenv("MLFLOW_ENABLED", "false")
    monkeypatch.setenv("POSTHOG_ENABLED", "false")
    concurrency._GLOBAL_SEMAPHORE = None
    concurrency._INITIALIZED_CONFIG = None
    yield
    concurrency._GLOBAL_SEMAPHORE = None
    concurrency._INITIALIZED_CONFIG = None


def test_runtime_status_includes_daytona_slot_diagnostics() -> None:
    from fleet_rlm.api.dependencies import ConfigDeps, DiagnosticsDeps, LmDeps
    from fleet_rlm.api.runtime_services.diagnostics import build_runtime_status_response

    response = build_runtime_status_response(
        config_deps=ConfigDeps(),
        lm_deps=LmDeps(),
        diagnostics_deps=DiagnosticsDeps(),
    )

    assert response.daytona["sandbox_slots"] == {
        "limit": 5,
        "available_slots": 5,
        "active_count": 0,
    }


def test_runtime_status_surfaces_persisted_mlflow_scorers(monkeypatch) -> None:
    from fleet_rlm.api.dependencies import ConfigDeps, DiagnosticsDeps, LmDeps
    from fleet_rlm.api.runtime_services import diagnostics
    from fleet_rlm.api.runtime_services.diagnostics import build_runtime_status_response

    monkeypatch.setattr(
        diagnostics,
        "MlflowConfig",
        type(
            "FakeMlflowConfigFactory",
            (),
            {
                "from_env": staticmethod(
                    type(
                        "FakeMlflowConfig",
                        (),
                        {
                            "enabled": True,
                            "enable_auto_assessment": False,
                            "tracking_uri": "http://127.0.0.1:5001",
                            "experiment": "fleet-rlm",
                        },
                    )
                )
            },
        ),
    )
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.auto_assessment.persisted_scorer_names",
        lambda config: ["Trace Judge"],
    )

    response = build_runtime_status_response(
        config_deps=ConfigDeps(),
        lm_deps=LmDeps(),
        diagnostics_deps=DiagnosticsDeps(),
    )

    assert response.mlflow.auto_assessment_enabled is False
    assert response.mlflow.persisted_scorer_count == 1
    assert response.mlflow.persisted_scorers == ["Trace Judge"]
    assert any("Trace Judge" in item for item in response.guidance)


def test_runtime_status_reconciles_stale_saturated_daytona_slots(monkeypatch) -> None:
    from fleet_rlm.api.dependencies import ConfigDeps, DiagnosticsDeps, LmDeps
    from fleet_rlm.api.runtime_services import diagnostics
    from fleet_rlm.integrations.daytona.concurrency import SandboxUsageStats

    class FakeRuntime:
        def _count_provider_fleet_sandboxes_sync(self) -> int:
            return 0

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        diagnostics,
        "get_current_sandbox_usage",
        lambda: SandboxUsageStats(limit=5, available_slots=0, active_count=5),
    )
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.runtime.DaytonaSandboxRuntime",
        lambda: FakeRuntime(),
    )
    monkeypatch.setattr(
        diagnostics,
        "reconcile_sandbox_slots",
        lambda provider_active_count: SandboxUsageStats(
            limit=5,
            available_slots=5,
            active_count=provider_active_count,
        ),
    )

    response = diagnostics.build_runtime_status_response(
        config_deps=ConfigDeps(),
        lm_deps=LmDeps(),
        diagnostics_deps=DiagnosticsDeps(),
    )

    assert response.daytona["sandbox_slots"] == {
        "limit": 5,
        "available_slots": 5,
        "active_count": 0,
    }


def test_runtime_status_reconciles_stale_partial_daytona_slots(monkeypatch) -> None:
    from fleet_rlm.api.dependencies import ConfigDeps, DiagnosticsDeps, LmDeps
    from fleet_rlm.api.runtime_services import diagnostics
    from fleet_rlm.integrations.daytona.concurrency import SandboxUsageStats

    class FakeRuntime:
        def _count_provider_fleet_sandboxes_sync(self) -> int:
            return 0

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        diagnostics,
        "get_current_sandbox_usage",
        lambda: SandboxUsageStats(limit=5, available_slots=3, active_count=2),
    )
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.runtime.DaytonaSandboxRuntime",
        lambda: FakeRuntime(),
    )
    monkeypatch.setattr(
        diagnostics,
        "reconcile_sandbox_slots",
        lambda provider_active_count: SandboxUsageStats(
            limit=5,
            available_slots=5,
            active_count=provider_active_count,
        ),
    )

    response = diagnostics.build_runtime_status_response(
        config_deps=ConfigDeps(),
        lm_deps=LmDeps(),
        diagnostics_deps=DiagnosticsDeps(),
    )

    assert response.daytona["sandbox_slots"] == {
        "limit": 5,
        "available_slots": 5,
        "active_count": 0,
    }
