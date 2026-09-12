from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts.benchmarks import run_phase4_campaign
from scripts.benchmarks.phase4_api_server import (
    CampaignMiddleware,
    LifecycleObserver,
    _bounded_trial,
    _campaign_settings,
    _shape,
)


def test_campaign_trial_correlation_is_bounded_and_content_free() -> None:
    assert _bounded_trial("D-p4-suitable-01-1") == "D-p4-suitable-01-1"
    assert _bounded_trial("") is None
    assert _bounded_trial("bad value with spaces") is None
    assert _bounded_trial("x" * 129) is None


def test_shape_reads_the_selected_platform_resource_contract() -> None:
    resources = SimpleNamespace(
        platform=SimpleNamespace(
            spec_for_profile=lambda _profile: SimpleNamespace(cpu=4, memory_gib=8, disk_gib=8),
        )
    )

    assert _shape(resources, object()) == (4, 8, 8)


def test_shape_uses_default_spec_when_the_platform_call_omits_profile() -> None:
    resources = SimpleNamespace(
        platform=SimpleNamespace(
            spec_for_profile=lambda _profile: None,
        ),
        sandbox_spec=SimpleNamespace(cpu=4, memory_gib=8, disk_gib=8),
    )

    assert _shape(resources, None) == (4, 8, 8)


def test_shape_fails_closed_when_resource_shape_is_unavailable() -> None:
    assert _shape(SimpleNamespace(platform=SimpleNamespace()), object()) is None


def test_campaign_server_preserves_ordinary_policy_without_explicit_profile(tmp_path) -> None:
    settings = _campaign_settings(
        profile=None,
        recursive=True,
        data_root=tmp_path / "data",
        database_url="sqlite+aiosqlite:///tmp/fleet-partial.db",
        volume_name="fleet-partial-volume",
    )

    assert settings.root_llm_max_tokens == 16_384
    assert settings.sub_llm_max_tokens == 16_384
    assert settings.rlm_max_iters == 12
    assert settings.rlm_max_llm_calls == 32
    assert settings.database_url == "sqlite+aiosqlite:///tmp/fleet-partial.db"


def test_baseline_policy_overlay_selects_campaign_profile_without_mutating_candidate(
    monkeypatch,
    tmp_path,
) -> None:
    candidate = tmp_path / "candidate"
    candidate_config = candidate / "config"
    candidate_config.mkdir(parents=True)
    source = (
        "[config]\n"
        'default_profile = "daytona-recursive"\n\n'
        "[profiles.daytona-recursive]\n\n"
        "[profiles.phase4-campaign]\n"
    )
    (candidate_config / "fleet.toml").write_text(source, encoding="utf-8")

    baseline = tmp_path / "baseline"
    (baseline / "config").mkdir(parents=True)
    (baseline / "config" / "fleet.toml").write_text(source, encoding="utf-8")

    monkeypatch.setattr(run_phase4_campaign, "REPO_ROOT", candidate)
    run_phase4_campaign._install_baseline_policy_overlay(baseline)

    assert 'default_profile = "phase4-campaign"' in (baseline / "config" / "fleet.toml").read_text()
    assert (candidate / "config" / "fleet.toml").read_text() == source


def test_close_deadline_defaults_and_rejects_bad_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_P4_CLOSE_DEADLINE_S", raising=False)
    assert LifecycleObserver.close_deadline_seconds() == 120.0
    monkeypatch.setenv("FLEET_P4_CLOSE_DEADLINE_S", "60")
    assert LifecycleObserver.close_deadline_seconds() == 60.0
    for value in ("bogus", "", "0", "-5", "9999"):
        monkeypatch.setenv("FLEET_P4_CLOSE_DEADLINE_S", value)
        assert LifecycleObserver.close_deadline_seconds() == 120.0


@pytest.mark.asyncio
async def test_campaign_middleware_closes_admitted_turn_when_terminal_body_is_missing() -> None:
    session_id = uuid4()
    closed: list[object] = []

    class Observer:
        async def close_turn_root(self, value):
            closed.append(value)

    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("client disconnected")

    middleware = CampaignMiddleware(app, Observer())
    with pytest.raises(RuntimeError, match="disconnected"):
        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": f"/api/sessions/{session_id}/turns",
                "headers": [],
            },
            lambda: asyncio.sleep(0),
            lambda _message: asyncio.sleep(0),
        )
    assert closed == [session_id]


def test_campaign_server_reads_arm_budgets_from_selected_profile(tmp_path) -> None:
    recursive = _campaign_settings(
        profile="phase4-campaign",
        recursive=True,
        data_root=tmp_path / "data",
        database_url="sqlite+aiosqlite:///tmp/fleet-p4.db",
        volume_name="fleet-p4-volume",
    )

    assert (recursive.rlm_max_iters, recursive.rlm_max_llm_calls) == (6, 8)
    assert recursive.rlm_recursion_enabled is True
    assert recursive.root_llm_max_tokens == 1_024
    assert recursive.sub_llm_max_tokens == 512
    assert recursive.max_active_daytona_leases == 5
    assert recursive.turn_timeout_seconds == 90

    direct = _campaign_settings(
        profile="phase4-campaign-a",
        recursive=False,
        data_root=tmp_path / "data",
        database_url="sqlite+aiosqlite:///tmp/fleet-p4.db",
        volume_name="fleet-p4-volume",
    )

    assert (direct.rlm_max_iters, direct.rlm_max_llm_calls) == (2, 2)
    assert direct.rlm_recursion_enabled is False

    native = _campaign_settings(
        profile="phase4-campaign-b",
        recursive=False,
        data_root=tmp_path / "data",
        database_url="sqlite+aiosqlite:///tmp/fleet-p4.db",
        volume_name="fleet-p4-volume",
    )

    assert (native.rlm_max_iters, native.rlm_max_llm_calls) == (6, 8)
    assert native.rlm_recursion_enabled is False
