from __future__ import annotations


def test_runtime_status_includes_daytona_slot_diagnostics() -> None:
    import fleet_rlm.integrations.daytona.concurrency as concurrency
    from fleet_rlm.api.dependencies import ConfigDeps, DiagnosticsDeps, LmDeps
    from fleet_rlm.api.runtime_services.diagnostics import build_runtime_status_response

    concurrency._GLOBAL_SEMAPHORE = None
    concurrency._INITIALIZED_CONFIG = None

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
