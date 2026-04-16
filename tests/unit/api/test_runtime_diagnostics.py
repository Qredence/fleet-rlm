from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from fleet_rlm.api.runtime_services import diagnostics


@pytest.mark.asyncio
async def test_run_connectivity_test_returns_preflight_failure_without_smoke() -> None:
    state = SimpleNamespace(runtime_test_results={})
    smoke_called = False

    async def _smoke() -> tuple[bool, str | None, str | None]:
        nonlocal smoke_called
        smoke_called = True
        return True, "ok", None

    result = await diagnostics.run_connectivity_test(
        state=state,
        kind="daytona",
        preflight_ok=False,
        checks={"configured": False},
        guidance=["DAYTONA_API_KEY is missing."],
        preflight_error="Daytona preflight checks failed.",
        default_error="Daytona connectivity test failed.",
        timeout_error=None,
        run_smoke=_smoke,
    )

    assert smoke_called is False
    assert result.kind == "daytona"
    assert result.ok is False
    assert result.preflight_ok is False
    assert result.error == "Daytona preflight checks failed."
    assert (
        state.runtime_test_results["daytona"]["error"]
        == "Daytona preflight checks failed."
    )


@pytest.mark.asyncio
async def test_run_daytona_connection_test_caches_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = SimpleNamespace(
        config=SimpleNamespace(sandbox_provider="daytona"),
        runtime_test_results={},
    )

    monkeypatch.setattr(
        diagnostics,
        "daytona_preflight",
        lambda sandbox_provider=None: ({"configured": True}, []),
    )

    class _FakeDaytonaConfig:
        def __init__(self, *, api_key: str, api_url: str, target: str) -> None:
            self.api_key = api_key
            self.api_url = api_url
            self.target = target

    class _FakeDaytona:
        def __init__(self, config: _FakeDaytonaConfig) -> None:
            self.config = config

        def list(self, limit: int = 1):
            _ = limit
            return SimpleNamespace(items=[object(), object()])

    fake_daytona_module = SimpleNamespace(
        Daytona=_FakeDaytona,
        DaytonaConfig=_FakeDaytonaConfig,
    )
    monkeypatch.setitem(sys.modules, "daytona", fake_daytona_module)
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.resolve_daytona_config",
        lambda: SimpleNamespace(
            api_key="daytona-key",
            api_url="https://daytona.example.com/",
            target="local",
        ),
    )

    async def _fake_run_blocking(fn, timeout):
        _ = timeout
        return fn()

    monkeypatch.setattr(diagnostics, "run_blocking", _fake_run_blocking)

    result = await diagnostics.run_daytona_connection_test(state=state)

    assert result.kind == "daytona"
    assert result.ok is True
    assert result.preflight_ok is True
    assert result.output_preview == (
        "Daytona connectivity verified. Found 2 sandboxes (limited)."
    )
    assert state.runtime_test_results["daytona"]["ok"] is True


@pytest.mark.asyncio
async def test_run_daytona_connection_test_reports_missing_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = SimpleNamespace(
        config=SimpleNamespace(sandbox_provider="daytona"),
        runtime_test_results={},
    )

    monkeypatch.setattr(
        diagnostics,
        "daytona_preflight",
        lambda sandbox_provider=None: ({"configured": True}, []),
    )
    monkeypatch.setitem(sys.modules, "daytona", None)

    result = await diagnostics.run_daytona_connection_test(state=state)

    assert result.ok is False
    assert result.error == "Daytona SDK is not installed."
    assert (
        state.runtime_test_results["daytona"]["error"]
        == "Daytona SDK is not installed."
    )


def test_build_runtime_status_response_includes_cached_test_failures_in_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        diagnostics,
        "resolve_mlflow_auto_start_enabled",
        lambda **_: False,
    )

    state = SimpleNamespace(
        is_ready=True,
        config=SimpleNamespace(
            app_env="local",
            sandbox_provider="daytona",
            agent_model="openai/gpt-4.1-mini",
            agent_delegate_model="openai/gpt-4.1-mini",
            agent_delegate_small_model="openai/gpt-4.1-mini",
        ),
        planner_lm=object(),
        optional_service_status={},
        optional_service_errors={},
        runtime_test_results={
            "lm": {
                "kind": "lm",
                "ok": False,
                "preflight_ok": True,
                "checked_at": "2026-04-16T10:00:00Z",
                "checks": {},
                "guidance": ["Check API connectivity and credentials."],
                "error": "LM test timed out after 20s.",
            },
            "daytona": {
                "kind": "daytona",
                "ok": True,
                "preflight_ok": True,
                "checked_at": "2026-04-16T10:00:10Z",
                "checks": {},
                "guidance": [],
                "output_preview": "OK",
            },
        },
    )

    monkeypatch.setattr(
        diagnostics,
        "lm_preflight",
        lambda: ({"model_set": True, "api_key_set": True}, []),
    )
    monkeypatch.setattr(
        diagnostics,
        "daytona_preflight",
        lambda sandbox_provider=None: ({"configured": True}, []),
    )

    status = diagnostics.build_runtime_status_response(state=state)

    assert "LM test timed out after 20s." in status.guidance
    assert "Check API connectivity and credentials." in status.guidance
